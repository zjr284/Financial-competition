"""A private review pack travels without credentials, personal history or the source store."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from app.config import Settings
from app.container import build_app
from app.export.review_bundle import build_bundle
from app.export.review_bundle_verify import verify_bundle
from app.facts.review_workflow import init_manifest, add_fact
from app.tasks.a_bridge import install_role_b


@pytest.fixture
def source(app, tmp_path):
    document = app.upload_document('synthetic-source.txt', '虚构测试：营业收入120元。'.encode(), disclosure_date='2026-04-01')
    app.parse_document(document['document_id'])
    eid = 'ev_synthetic_manual'
    app.store.insert_evidence([{'evidence_id': eid, 'document_id': document['document_id'], 'page': 1,
        'bbox': [100, 100, 800, 200], 'text': '虚构测试：营业收入120元。', 'extraction_method': 'manual', 'confidence': 1}])
    manifest = app.settings.data_dir/'reviewed_facts.json'
    init_manifest(app, manifest, [document['document_id']])
    add_fact(app, manifest, document_id=document['document_id'], evidence_id=eid, company_code='999999',
             metric='revenue', value='120', period_start='2025-01-01', period_end='2025-12-31',
             period_type='cumulative', statement_scope='consolidated', verified=True)
    payload = json.loads(manifest.read_text())
    payload['review_history'] = [{'reviewer': 'private-person-marker'}]
    manifest.write_text(json.dumps(payload))
    (app.settings.data_dir/'model-service.key').write_text('private-key-marker')
    install_role_b(app, manifest)
    run = app.run_task({'api_version': '1.0.0', 'task_type': 'financial_analysis',
        'document_ids': [document['document_id']], 'task_input': {'company_code': '999999',
        'periods': ['2025-12-31'], 'statement_scope': 'consolidated'}})
    result = app.get_result(run['run_id'])
    review = result['review_items'][0]
    app.store.upsert_review({**review, 'run_id': run['run_id'], 'evidence_ids': json.dumps(review['evidence_ids']),
        'reviewer': 'private-person-marker', 'note': 'private-person-marker', 'corrected_value': None, 'updated_at': None})
    spec = {'company_code': '999999', 'fact_manifest': str(manifest),
            'stores': [{'data_dir': str(app.settings.data_dir), 'run_ids': [run['run_id']]}]}
    return app, document, eid, manifest, run, spec


def test_zip_is_self_contained_and_original_fact_values_survive_without_identities(source, tmp_path):
    app, document, eid, manifest, run, spec = source
    before = manifest.read_bytes()
    output = tmp_path/'private-pack'
    summary = build_bundle(spec, output)
    assert summary['facts'] == 1 and summary['runs'] == 1 and not summary['offline_replay_included']
    assert manifest.read_bytes() == before
    archive = output.with_suffix('.zip')
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == summary['archive_sha256']
    extracted = tmp_path/'another-computer'
    with zipfile.ZipFile(archive) as z:
        assert not any('key' in name or 'model-calls' in name for name in z.namelist())
        assert all(b'private-person-marker' not in z.read(name) and b'private-key-marker' not in z.read(name) for name in z.namelist())
        z.extractall(extracted)
    root = extracted/output.name
    proc = subprocess.run([sys.executable, str(root/'verify_bundle.py'), '--restore-blobs'],
                          cwd=extracted, env={'PYTHONPATH': ''}, text=True, capture_output=True)
    assert proc.returncode == 0 and json.loads(proc.stdout)['verified']
    clone = build_app(Settings(data_dir=root/'store'))
    try:
        assert clone.get_document_content(document['document_id'])[0] == '虚构测试：营业收入120元。'.encode()
        assert clone.get_evidence(eid)['document_id'] == document['document_id']
        assert clone.get_result(run['run_id']) == app.get_result(run['run_id'])
        assert clone.store.list_reviews(run['run_id'])[0]['reviewer'] is None
        assert clone.events.all_events(run['run_id']) == []
    finally:
        clone.close()
    copied = json.loads((root/'store/reviewed_facts.json').read_text())
    assert copied['documents'][0]['facts'] == json.loads(before)['documents'][0]['facts']
    assert 'review_history' not in copied
    coordinate = json.loads((root/'annotations/coordinate_review_blank.json').read_text())[0]
    assert all(value == '' for value in coordinate['annotation'].values())


def test_same_document_in_two_stores_keeps_additional_cell_evidence(source, tmp_path):
    app, document, eid, manifest, run, spec = source
    second = build_app(Settings(data_dir=tmp_path/'second'))
    try:
        clone = second.upload_document('synthetic-source.txt', '虚构测试：营业收入120元。'.encode(), disclosure_date='2026-04-01')
        assert clone['document_id'] == document['document_id']
        second.parse_document(clone['document_id'])
        second.store.insert_evidence([{'evidence_id': 'ev_synthetic_cell', 'document_id': clone['document_id'],
            'page': 1, 'bbox': [100, 100, 300, 200], 'text': '120元', 'extraction_method': 'table', 'confidence': 1}])
        spec['stores'].append({'data_dir': str(second.settings.data_dir), 'run_ids': []})
    finally:
        second.close()
    output = tmp_path/'joined'
    build_bundle(spec, output)
    conn = sqlite3.connect(output/'store/metadata.sqlite3')
    try:
        assert conn.execute('SELECT text FROM evidence WHERE evidence_id=?', ('ev_synthetic_cell',)).fetchone()[0] == '120元'
    finally:
        conn.close()


def test_corrupt_source_or_missing_evidence_is_rejected_before_destination_creation(source, tmp_path):
    app, document, eid, manifest, run, spec = source
    app.store.blob_path(document['sha256']).write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='Original document hash'):
        build_bundle(spec, tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_completed_pack_is_never_overwritten(source, tmp_path):
    spec = source[-1]
    output = tmp_path/'existing'
    build_bundle(spec, output)
    (output/'human-work.txt').write_text('keep my work')
    with pytest.raises(ValueError, match='must not be overwritten'):
        build_bundle(spec, output)
    assert (output/'human-work.txt').read_text() == 'keep my work'


def test_verifier_rejects_tampering_and_path_escape(source, tmp_path):
    output = tmp_path/'verify'
    build_bundle(source[-1], output)
    checks = json.loads((output/'checksums.json').read_text())
    checks['../outside'] = '0'*64
    (output/'checksums.json').write_text(json.dumps(checks))
    with pytest.raises(ValueError, match='Unsafe bundle path'):
        verify_bundle(output)
    del checks['../outside']
    (output/'checksums.json').write_text(json.dumps(checks))
    (output/'runs.json').write_text('[]')
    with pytest.raises(ValueError, match='checksum mismatch'):
        verify_bundle(output)


def test_annotation_owner_comes_from_original_run_not_a_bundle_label(source, tmp_path):
    app, document, eid, manifest, original, spec = source
    run = app.create_run({'api_version': '1.0.0', 'task_type': 'investment_memo',
        'document_ids': [document['document_id']], 'task_input': {'company_code': '888888', 'as_of_date': '2026-09-30'}})
    run['status'] = 'needs_review'
    result = {'api_version': '1.0.0', 'run_id': run['run_id'], 'task_type': 'investment_memo',
        'status': 'needs_review', 'generated_at': '2026-10-15T00:00:00+08:00',
        'input_document_ids': [document['document_id']], 'evidence_ids': [eid], 'calculations': [], 'review_items': [], 'warnings': [],
        'data': {'thesis': [{'text': '合成审阅测试', 'kind': 'inference', 'evidence_ids': [eid], 'assumptions': ['合成条件'], 'limitations': []}],
                 'evidence': [], 'risks': [], 'disconfirming_conditions': [], 'monitoring_indicators': []}}
    app.store.update_run(run['run_id'], status='needs_review', payload=json.dumps(run), result=result, touch_result=True)
    spec['company_code'] = '999999'  # A stale external label must not change the Run's owner.
    spec['stores'][0]['run_ids'] = [run['run_id']]
    output = tmp_path/'semantic'
    build_bundle(spec, output)
    packet = json.loads((output/'annotations'/(run['run_id']+'_a_blank.json')).read_text())
    assert packet['records'][0]['company_code'] == '888888'
    assert packet['records'][0]['split'] == 'development' and not packet['synthetic']
    assert all(value == '' for value in packet['records'][0]['annotation'].values())
