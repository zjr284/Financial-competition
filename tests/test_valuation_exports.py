"""Valuation report provenance, review states and escaping; no financial arithmetic."""
import copy
import csv
import json
from pathlib import Path

from app.config import Settings
from app.export.service import run_export
from app.export.valuation import html_for_valuation, rows_for_valuation
from app.tasks.valuation.samples.ttm_pe.demo import HERE, run_demo


def test_csv_html_keep_originals_and_calculation_lineage(tmp_path):
    sources=json.loads((HERE/'inputs.json').read_text())
    result=run_demo(sources);before=copy.deepcopy(result)
    report=run_export(run_id=result['run_id'],result=result,settings=Settings(data_dir=tmp_path),
                      formats=['json','csv','html'],valuation_sources=sources)
    assert result==before and not report.warnings
    path=next(Path(p) for p in report.files if p.endswith('.csv'))
    rows=list(csv.DictReader(path.read_text(encoding='utf-8-sig').splitlines()))
    assert len(rows)==37  # 3 endpoints + 19 calculations + 15 original facts.
    assert sum(r['kind']=='计算得出' for r in rows)==19
    assert sum('sha256=' in r['sources'] for r in rows)==15
    assert all(r['chain_status']=='references_preserved' for r in rows)
    assert '未读取' not in next(Path(p) for p in report.files if p.endswith('.html')).read_text()


def test_unread_facts_stay_blank_and_preserve_ids(tmp_path):
    result=run_demo();rows,problems=rows_for_valuation(result)
    assert problems
    missing=[r for r in rows if r['kind']=='原始事实引用（未读取）']
    assert len(missing)==15 and all(r['value']=='' for r in missing)
    assert all(r['chain_status']=='needs_review' for r in rows)


def test_pending_interval_is_empty_and_reason_preserved():
    result=json.loads((HERE/'needs_review.json').read_text())
    rows,_=rows_for_valuation(result)
    assert all(r['value']=='' and r['reason'] for r in rows[:3])
    assert all(r['status']=='needs_review' for r in rows[:3])


def test_missing_upstream_duplicate_and_cycle_flagged_without_result_mutation():
    for kind in ('missing','duplicate','cycle'):
        result=run_demo()
        if kind=='missing':result['calculations'].pop(0)
        if kind=='duplicate':result['calculations'].append(copy.deepcopy(result['calculations'][0]))
        if kind=='cycle':
            first=result['calculations'][0];first['input_calculation_ids']=[first['calculation_id']]
        before=copy.deepcopy(result)
        _,problems=rows_for_valuation(result)
        assert any(term in ' '.join(problems) for term in ('缺少上游','重复','循环'))
        assert result==before


def test_export_does_not_guess_scenario_references_by_equal_values():
    result=run_demo()
    for c in result['calculations']:c['parameters'].pop('valuation_scenario',None)
    rows,problems=rows_for_valuation(result)
    assert all(r['input_calculation_ids']=='' for r in rows[:3])
    assert any('场景计算引用' in p for p in problems)


def test_html_and_csv_escape_source_text_and_formula_like_cells(tmp_path):
    sources=json.loads((HERE/'inputs.json').read_text())
    sources['facts'][0]['reason']='<script>unsafe()</script>'
    result=run_demo()
    result['calculations'][0]['reason']='=HYPERLINK("bad")'
    report=run_export(run_id=result['run_id'],result=result,settings=Settings(data_dir=tmp_path),
                      formats=['csv','html'],valuation_sources=sources)
    text=next(Path(p) for p in report.files if p.endswith('.html')).read_text()
    assert '<script>unsafe()</script>' not in text and '&lt;script&gt;' in text
    rows=list(csv.DictReader(next(Path(p) for p in report.files if p.endswith('.csv')).read_text(encoding='utf-8-sig').splitlines()))
    assert any(r['reason'].startswith("'=HYPERLINK") for r in rows)


def test_signed_decimal_is_preserved_and_unresolved_location_is_flagged(tmp_path):
    sources=json.loads((HERE/'inputs.json').read_text())
    sources['facts'][0]['value']='-100.12';sources['documents']=[]
    result=run_demo()
    report=run_export(run_id=result['run_id'],result=result,settings=Settings(data_dir=tmp_path),
                      formats=['csv'],valuation_sources=sources)
    rows=list(csv.DictReader(Path(report.files[0]).read_text(encoding='utf-8-sig').splitlines()))
    assert any(r['value']=='-100.12' for r in rows)
    assert any('文档版本未读取' in p for p in report.warnings)


def test_source_reader_uses_the_checked_snapshot_not_a_second_file_read(tmp_path):
    from types import SimpleNamespace
    from app.facts.reviewed_store import ReviewedFactPort
    sources=json.loads((HERE/'inputs.json').read_text())
    evidence={e['evidence_id']:e for e in sources['evidence']}
    documents={d['document_id']:d for d in sources['documents']}
    snapshot={'version':1,'documents':[
        {'document_id':did,'sha256':doc['sha256'],'facts':[
            f for f in sources['facts'] if evidence[f['evidence_ids'][0]]['document_id']==did]}
        for did,doc in documents.items()]}
    path=tmp_path/'snapshot.json'
    port=ReviewedFactPort(path,SimpleNamespace(get_document=lambda did:documents[did]),
                          SimpleNamespace(get_evidence=lambda eid:evidence[eid]),snapshot=snapshot)
    path.write_text('invalid replacement after the snapshot was checked')
    snapshot['documents'].clear()
    assert len(port.extract_facts(list(documents),['net_profit_parent','market_cap']))==15
