"""The unified entry preserves provenance and refuses incompatible replay."""
import json

import pytest

from app.tasks import ab_runtime


def request_for(task, doc):
    inputs = {
        'structured_extraction': {'announcement_types': ['pledge']},
        'financial_analysis': {'company_code': '999999', 'periods': ['2025-12-31'], 'statement_scope': 'consolidated'},
        'valuation': {'company_code': '999999', 'method': 'relative_pe', 'as_of_date': '2026-09-30',
                      'statement_scope': 'consolidated', 'assumption_overrides': {}},
    }
    return {'api_version': '1.0.0', 'task_type': task, 'document_ids': [doc], 'task_input': inputs[task]}


@pytest.mark.parametrize('task', ['structured_extraction', 'financial_analysis', 'valuation'])
def test_pure_tools_ignore_broken_cloud_configuration_and_replay_pinned_settings(app, monkeypatch, task, tmp_path):
    doc = app.upload_document('synthetic.txt', '证券代码：999999\n质押公告\n公告日期：2026年9月22日\n股东名称：张某\n本次质押股数：12万股'.encode(),
                              disclosure_date='2026-09-22')
    monkeypatch.setenv('FINSCOPE_MODEL_BACKEND', 'remote-json')
    monkeypatch.setenv('FINSCOPE_MODEL_ENDPOINT', 'invalid secret endpoint')
    monkeypatch.setenv('FINSCOPE_MODEL_TIMEOUT_SECONDS', 'invalid')
    monkeypatch.setenv('FINSCOPE_MODEL_ID', 'original-model-alias')
    monkeypatch.setenv('FINSCOPE_PROMPT_VERSION', 'original-prompt')
    code, summary = ab_runtime.execute(app.settings.data_dir, None, request=request_for(task, doc['document_id']))
    assert code == 0 and summary['model_call_count'] == 0
    run_id = summary['run_id']
    run_dir = app.settings.runs_dir / run_id
    before = {p.name: p.read_bytes() for p in run_dir.glob('*') if p.is_file()}
    monkeypatch.setenv('FINSCOPE_MODEL_ID', 'different-model')
    monkeypatch.setenv('FINSCOPE_PROMPT_VERSION', 'different-prompt')
    monkeypatch.setenv('FINSCOPE_EXTRACTION_BACKEND', 'model')
    monkeypatch.setenv('FINSCOPE_EXTRACTION_GRID', '1')
    code, proof = ab_runtime.execute(app.settings.data_dir, tmp_path/'absent-global-manifest', run_id=run_id)
    assert code == 0 and proof['replay_ok'] and proof['input_hash_match']
    assert before == {p.name: p.read_bytes() for p in run_dir.glob('*') if p.is_file()}


def test_code_mismatch_refuses_before_opening_app_or_rewriting_record(app, monkeypatch):
    doc = app.upload_document('synthetic.txt', b'Synthetic source')
    _, summary = ab_runtime.execute(app.settings.data_dir, None, request=request_for('financial_analysis', doc['document_id']))
    monkeypatch.setattr(ab_runtime, 'current_code_version', lambda: 'other-checkout')
    monkeypatch.setattr(ab_runtime, 'build_app', lambda *a, **kw: pytest.fail('must reject before opening DB'))
    with pytest.raises(ValueError, match='original checkout'):
        ab_runtime.execute(app.settings.data_dir, None, run_id=summary['run_id'])


def test_missing_b_snapshot_cannot_recreate_it_from_global_facts(app):
    doc = app.upload_document('synthetic.txt', b'Synthetic source')
    _, summary = ab_runtime.execute(app.settings.data_dir, None, request=request_for('financial_analysis', doc['document_id']))
    path = app.settings.runs_dir / summary['run_id'] / 'b-reviewed-facts.json'
    path.unlink()
    with pytest.raises(ValueError, match='Missing pinned B'):
        ab_runtime.execute(app.settings.data_dir, None, run_id=summary['run_id'])
    assert not path.exists()


def test_cli_failure_is_redacted_and_nonzero(app, tmp_path, capsys):
    path = tmp_path/'private-request.json'
    path.write_text(json.dumps({'private': 'must-not-print-financial-data'}))
    assert ab_runtime.main(['--data-dir', str(app.settings.data_dir), 'run', '--request', str(path)]) == 1
    output = capsys.readouterr().out
    assert 'must-not-print' not in output and str(path) not in output
    assert json.loads(output)['status'] == 'failed'
