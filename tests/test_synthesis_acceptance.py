"""Specified review fields must fail independently, without changing source runs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.common.ids import digest_of
from app.tasks.synthesis_acceptance import check_fields, check_run, main
from app.orchestrator.service import Orchestrator


@pytest.fixture
def candidate():
    examples = json.loads((Path(__file__).parents[1]/'contracts/examples/results.json').read_text())
    return deepcopy(next(x for x in examples if x['task_type'] == 'industry_impact'))


def plan_for(result):
    return {'task_type': result['task_type'], 'edits': [
        {'operation': 'replace', 'path': ['conclusions', 0, 'text'],
         'value': result['data']['conclusions'][0]['text']},
        {'operation': 'replace', 'path': ['conclusions', 0, 'assumptions'],
         'value': result['data']['conclusions'][0]['assumptions']},
        {'operation': 'replace', 'path': ['edges', 0, 'mechanism'],
         'value': result['data']['edges'][0]['mechanism']},
    ]}


def test_all_requested_fields_pass_without_promoting_candidate(candidate):
    before = deepcopy(candidate)
    report = check_fields(candidate, plan_for(candidate), {}, [])
    assert report['status'] == 'passed'
    assert report['human_gold'] is False and report['review_status'] == 'needs_review'
    assert candidate == before


@pytest.mark.parametrize('section,field', [('conclusions', 'text'), ('conclusions', 'assumptions'),
                                         ('edges', 'mechanism')])
def test_fixing_conclusion_does_not_hide_stale_assumption_or_edge(candidate, section, field):
    plan = plan_for(candidate)
    candidate['data'][section][0][field] = ['unreviewed scope'] if field == 'assumptions' else 'unreviewed scope'
    report = check_fields(candidate, plan, {}, [])
    assert report['status'] == 'failed'
    assert sum(not x['passed'] for x in report['checks']) == 1
    assert 'unreviewed scope' not in json.dumps(report)


def test_continuation_on_top_level_or_another_statement_does_not_pass(candidate):
    plan = {'task_type': 'industry_impact', 'edits': [
        {'operation': 'append_source', 'path': ['conclusions', 0, 'evidence_ids'],
         'expected_sources': ['first'], 'source': 'continuation'}]}
    bindings = {'first': 'ev_industry_01', 'continuation': 'ev_continuation'}
    candidate['evidence_ids'].append('ev_continuation')
    candidate['data']['edges'][0]['evidence_ids'].append('ev_continuation')
    assert check_fields(candidate, plan, bindings, bindings.values())['status'] == 'failed'
    candidate['data']['conclusions'][0]['evidence_ids'].append('ev_continuation')
    assert check_fields(candidate, plan, bindings, bindings.values())['status'] == 'passed'
    with pytest.raises(ValueError, match='distinct'):
        check_fields(candidate, plan, bindings, ['ev_industry_01'])
    with pytest.raises(ValueError, match='binding'):
        check_fields(candidate, plan, {}, bindings.values())


def test_missing_requested_section_is_failure_and_empty_plan_is_invalid(candidate):
    plan = plan_for(candidate)
    candidate['data']['conclusions'] = []
    assert check_fields(candidate, plan, {}, [])['status'] == 'failed'
    with pytest.raises(ValueError, match='nonempty'):
        check_fields(candidate, {'task_type': 'industry_impact', 'edits': []}, {}, [])
    plan['edits'] = [plan['edits'][0], plan['edits'][0]]
    with pytest.raises(ValueError, match='Duplicate'):
        check_fields(candidate, plan, {}, [])


def pinned_run(app, candidate, tmp_path):
    source = tmp_path/'source.txt'
    source.write_text('synthetic qualitative source')
    doc = app.documents.ingest_file(str(source), {})
    run = app.orchestrator.create_run({'api_version': '1.0.0', 'task_type': 'industry_impact',
        'document_ids': [doc['document_id']], 'task_input': {'focus_company_code': '999999',
        'event_description': 'synthetic', 'scenario_ids': ['base']}})
    candidate['run_id'] = run['run_id']
    candidate['input_document_ids'] = run['document_ids']
    candidate['status'] = 'needs_review'
    directory = app.settings.runs_dir/run['run_id']
    snapshot = {'version': 1, 'documents': [{'document_id': doc['document_id'],
        'sha256': doc['sha256'], 'facts': [], 'semantic_evidence_ids': ['ev_industry_01']}]}
    (directory/'b-reviewed-facts.json').write_text(json.dumps(snapshot))
    app.events.append(run['run_id'], event_type='tool_call', tool_name='b.reviewed_fact_snapshot',
                      message='snapshot', output_hash=digest_of(snapshot))
    run.update(status='needs_review', result_hash=Orchestrator._result_hash(candidate))
    with app.store.write() as conn:
        conn.execute('UPDATE runs SET payload=?, result=?, status=? WHERE run_id=?',
                     (json.dumps(run), json.dumps(candidate), 'needs_review', run['run_id']))
    app.events.write_result(run['run_id'], candidate, needs_review=True)
    return directory


def test_read_only_registered_run_and_cli_privacy(app, candidate, tmp_path, capsys):
    directory = pinned_run(app, candidate, tmp_path)
    plan = plan_for(candidate)
    before = {p: p.read_bytes() for p in app.settings.data_dir.rglob('*') if p.is_file() and not p.name.endswith('-shm')}
    report = check_run(directory, plan, {})
    assert report['status'] == 'passed'
    assert {p: p.read_bytes() for p in before} == before
    path = tmp_path/'plan.json'
    path.write_text(json.dumps(plan))
    assert main(['--run-dir', str(directory), '--plan', str(path)]) == 0
    output = capsys.readouterr().out
    assert candidate['run_id'] not in output
    assert candidate['data']['conclusions'][0]['text'] not in output
    path.write_text('private broken json')
    assert main(['--run-dir', str(directory), '--plan', str(path)]) == 1
    assert 'private broken json' not in capsys.readouterr().out


@pytest.mark.parametrize('artifact', ['result', 'sources', 'bundle', 'chain'])
def test_changed_pinned_artifact_refuses_acceptance(app, candidate, tmp_path, artifact):
    directory = pinned_run(app, candidate, tmp_path)
    if artifact == 'result':
        path = directory/'result.needs_review.json'
        data = json.loads(path.read_text())
        data['data']['conclusions'][0]['text'] = 'tampered'
        path.write_text(json.dumps(data))
    elif artifact == 'sources':
        (directory/'b-reviewed-facts.json').write_text('{}')
    elif artifact == 'bundle':
        path = directory/'manifest.json'
        data = json.loads(path.read_text())
        data['input_bundle']['task_input']['event_description'] = 'tampered'
        path.write_text(json.dumps(data))
    else:
        with app.store.write() as conn:
            conn.execute("UPDATE events SET message='tampered' WHERE run_id=?", (candidate['run_id'],))
    with pytest.raises(ValueError):
        check_run(directory, plan_for(candidate), {})


@pytest.mark.parametrize('attributed', [True, False])
def test_management_attribution_is_an_explicit_expectation(candidate, attributed):
    expected = '公司报告自述可靠性改善；未获第三方验证。'
    candidate['data']['conclusions'][0]['text'] = expected if attributed else '可靠性已经改善。'
    plan = {'task_type': 'industry_impact', 'edits': [
        {'operation': 'replace', 'path': ['conclusions', 0, 'text'], 'value': expected}]}
    assert (check_fields(candidate, plan, {}, [])['status'] == 'passed') is attributed


def test_cli_returns_failure_for_wrong_reviewed_field(app, candidate, tmp_path, capsys):
    directory = pinned_run(app, candidate, tmp_path)
    plan = plan_for(candidate)
    plan['edits'][0]['value'] = 'explicit reviewed replacement'
    path = tmp_path/'plan.json'
    path.write_text(json.dumps(plan))
    assert main(['--run-dir', str(directory), '--plan', str(path)]) == 1
    assert json.loads(capsys.readouterr().out)['checks'][0]['passed'] is False
    path.write_text(json.dumps({'task_type': 'industry_impact', 'edits': [None]}))
    assert main(['--run-dir', str(directory), '--plan', str(path)]) == 1
    assert json.loads(capsys.readouterr().out)['error_code'] == 'VALIDATION_ERROR'
