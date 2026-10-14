"""A/B production run, immutable sources, JSON export and offline replay."""
import json
import csv
import pytest
from app.errors import AppError
import hashlib
from pathlib import Path

from app.tasks.valuation.samples.ttm_pe.demo import HERE
from app.facts.review_workflow import add_fact, init_manifest
from app.tasks.a_bridge import install_role_b


def test_production_ttm_run_export_replay_and_pinned_review_snapshot(app, tmp_path):
    from tools import make_sample_pdf

    fixture=json.loads((HERE/'inputs.json').read_text())
    originals={e['evidence_id']:e for e in fixture['evidence']}
    documents={}
    source_evidence={}
    for entry in fixture['documents']:
        # TXT logical-layout evidence is deliberately below the verified threshold.
        # Build text-layer PDFs instead of weakening the real evidence-quality gate.
        original_lines=make_sample_pdf.LINES
        try:
            lines=['Synthetic financial source; all companies and values are fictional.'] + [
                e['text'] for e in fixture['evidence'] if e['document_id']==entry['document_id']]
            make_sample_pdf.LINES=[(i,72,line) for i,line in enumerate(lines)]
            raw=make_sample_pdf.build_pdf()
        finally:
            make_sample_pdf.LINES=original_lines
        doc=app.upload_document(entry['filename'].replace('.txt','.pdf'),raw,
                                disclosure_date=entry['disclosure_date'])
        assert doc['sha256']==hashlib.sha256(raw).hexdigest()
        documents[entry['document_id']]=doc['document_id']
        parsed=app.parse_document(doc['document_id'])
        source_evidence[entry['document_id']]=[app.get_evidence(e) for e in parsed['evidence_ids']]
    manifest=tmp_path/'synthetic_reviewed_facts.json'
    init_manifest(app,manifest,list(documents.values()))
    for fact in fixture['facts']:
        original=originals[fact['evidence_ids'][0]]
        evidence=next(e for e in source_evidence[original['document_id']] if original['text'] in e['text'])
        assert evidence['page']==1 and evidence['confidence']>=0.9
        assert evidence['bbox'][0]<evidence['bbox'][2] and evidence['bbox'][1]<evidence['bbox'][3]
        add_fact(app,manifest,document_id=documents[original['document_id']],evidence_id=evidence['evidence_id'],
                 company_code=fact['company_code'],metric=fact['metric'],value=fact['value'],
                 period_start=fact['period_start'],period_end=fact['period_end'],
                 period_type=fact['period_type'],statement_scope=fact['statement_scope'],verified=True)
    # These verified fixtures are explicitly synthetic; no real reviewer data is touched.
    install_role_b(app,manifest)
    request=dict(api_version='1.0.0',task_type='valuation',document_ids=list(documents.values()),
                 task_input=fixture['task_input'])
    run=app.run_task(request)
    assert run['status']=='needs_review'  # Business comparability remains a human decision.
    result=app.get_result(run['run_id'])
    assert [result['data']['valuation_range'][k] for k in ('low','base','high')]==['1200','1500','2400']
    ttm=[c for c in result['calculations'] if c['formula_code']=='ttm_from_ytd']
    assert len(ttm)==4 and all(len(c['input_fact_ids'])==3 for c in ttm)
    export=app.export_run(run['run_id'],formats=['json','csv','html'])
    json_file=next(Path(p) for p in export['files'] if p.endswith('.json'))
    exported=json.loads(json_file.read_text())
    assert exported['result']==result
    csv_file=next(Path(p) for p in export['files'] if p.endswith('.csv'))
    rows=list(csv.DictReader(csv_file.read_text(encoding='utf-8-sig').splitlines()))
    originals=[r for r in rows if r['kind']=='原始事实（运行快照）']
    assert len(originals)==15
    assert all('sha256=' in r['sources'] and 'bbox=' in r['sources'] for r in originals)
    ranges=[r for r in rows if r['kind']=='估值结果（计算得出）']
    assert [r['value'] for r in ranges]==['1200','1500','2400']
    assert all(r['input_calculation_ids'] for r in ranges)
    assert all(r['chain_status']=='references_preserved' for r in rows)
    html_file=next(Path(p) for p in export['files'] if p.endswith('.html'))
    assert '计算得出' in html_file.read_text() and '无表格视图' not in html_file.read_text()
    replay=app.orchestrator.replay(run['run_id'])
    assert replay['result_hash_match'] and replay['trace_match']
    original_export_hashes=export['hashes']
    manifest.write_text('{"version": 1, "documents": []}')
    replay=app.orchestrator.replay(run['run_id'])
    assert replay['result_hash_match'] and replay['trace_match']
    pinned_export=app.export_run(run['run_id'],formats=['csv','html'])
    for filename,sha in pinned_export['hashes'].items():
        assert sha==original_export_hashes[filename]
    assert str(app.settings.data_dir) not in json.dumps(app.get_events(run['run_id']))
    snapshot=app.settings.runs_dir/run['run_id']/'b-reviewed-facts.json'
    payload=json.loads(snapshot.read_text());payload['documents'][0]['facts'][0]['value']='9999'
    snapshot.write_text(json.dumps(payload))
    with pytest.raises(AppError,match='快照或来源版本不匹配'):
        app.export_run(run['run_id'],formats=['csv','html'])
    # Raw result JSON remains exportable; it does not read or rewrite source facts.
    assert app.export_run(run['run_id'],formats=['json'])['formats']==['json']
    fresh=app.run_task(request)
    assert app.get_result(fresh['run_id'])['data']['valuation_range']['base'] is None
