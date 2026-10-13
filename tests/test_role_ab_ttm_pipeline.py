"""A/B production run, immutable sources, JSON export and offline replay."""
import json
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
    replay=app.orchestrator.replay(run['run_id'])
    assert replay['result_hash_match'] and replay['trace_match']
    manifest.write_text('{"version": 1, "documents": []}')
    replay=app.orchestrator.replay(run['run_id'])
    assert replay['result_hash_match'] and replay['trace_match']
    fresh=app.run_task(request)
    assert app.get_result(fresh['run_id'])['data']['valuation_range']['base'] is None
