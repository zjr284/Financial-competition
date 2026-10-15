"""Real local HTTP protocol tests with a fixture responder, never a real LLM."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.common.ids import digest_of
from app.config import Settings
from app.container import build_app
from app.errors import AppError
from app.facts.review_workflow import init_manifest, attach_document
from app.facts.reviewed_store import ReviewedFactPort
from app.facts.semantic_sources import register_excerpt
from app.models.adapter import RemoteJsonModel
from app.tasks.a_bridge import install_role_b


@pytest.fixture
def model_server():
    state = SimpleNamespace(requests=[], replies=[])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.requests.append((body, self.headers.get('Authorization')))
            user = json.loads(body['messages'][-1]['content'])
            eid = user['evidence_ids'][0]
            statement = {'text': '需求变化可能影响交付节奏', 'kind': 'inference', 'evidence_ids': [eid],
                         'assumptions': ['其他条件保持不变'], 'limitations': ['需分析员复核适用性']}
            if user['schema_name'] == 'ExtractionData':
                output = state.extraction_output(user)
            elif user['schema_name'] == 'IndustryData':
                output = {'nodes': [{'node_id': 'upstream', 'name': '材料', 'stage': 'upstream'},
                                    {'node_id': 'company', 'name': '目标', 'stage': 'company'}],
                          'edges': [{'from_id': 'upstream', 'to_id': 'company', 'mechanism': '供需传导',
                                     'direction': 'uncertain', 'evidence_ids': [eid]}],
                          'scenarios': [{'scenario_id': sid, 'assumptions': ['仅作条件分析'], 'impact_text': '需核对原文',
                                         'evidence_ids': [eid]} for sid in user['context']['scenario_ids']],
                          'conclusions': [statement]}
            else:
                output = {'thesis': [statement], 'evidence': [], 'risks': [statement],
                          'disconfirming_conditions': [statement], 'monitoring_indicators': ['跟踪公开订单变化']}
            response = {'model': 'protocol-fixture', 'choices': [{'finish_reason': 'stop',
                         'message': {'content': json.dumps(output, ensure_ascii=False)}}]}
            status, raw = state.replies.pop(0) if state.replies else (200, json.dumps(response).encode())
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def runtime(tmp_path, monkeypatch, model_server):
    monkeypatch.setenv('FINSCOPE_MODEL_BACKEND', 'remote-json')
    monkeypatch.setenv('FINSCOPE_MODEL_ENDPOINT', model_server.endpoint)
    monkeypatch.setenv('FINSCOPE_MODEL_KEY', 'protocol-test-only-secret')
    settings = Settings(data_dir=tmp_path, model_id='protocol-fixture', model_revision='fixture-only')
    app = build_app(settings)
    manifest = tmp_path / 'reviewed_facts.json'
    init_manifest(app, manifest, [])
    doc = app.upload_document('synthetic-source.txt', '明确标为合成资料：材料供需变化影响交付节奏。'.encode(),
                              disclosure_date='2026-08-01')
    evidence_id = app.parse_document(doc['document_id'])['evidence_ids'][0]
    attach_document(app, manifest, doc['document_id'])
    register_excerpt(app, manifest, doc['document_id'], evidence_id, company_code='999999')
    install_role_b(app, manifest)
    try:
        yield SimpleNamespace(app=app, manifest=manifest, doc=doc, eid=evidence_id, settings=settings)
    finally:
        app.close()


def run(runtime, task='industry_impact'):
    task_input = ({'focus_company_code': '999999', 'event_description': '材料供需变化', 'scenario_ids': ['base']}
                  if task == 'industry_impact' else {'company_code': '999999', 'as_of_date': '2026-09-30'})
    return runtime.app.run_task({'api_version': '1.0.0', 'task_type': task,
                                'document_ids': [runtime.doc['document_id']], 'task_input': task_input})


@pytest.mark.parametrize('task', ['industry_impact', 'investment_memo'])
def test_production_source_port_and_local_http_replay_without_network(runtime, model_server, task):
    before = runtime.manifest.read_bytes()
    assert runtime.app.health()['model_available'] is False  # Configuration is not a connectivity test.
    outcome = run(runtime, task)
    assert outcome['status'] == 'needs_review'
    result = runtime.app.get_result(outcome['run_id'])
    assert runtime.eid in result['evidence_ids']
    assert runtime.app.health()['model_available'] is True
    assert runtime.manifest.read_bytes() == before
    assert len(model_server.requests) == 1
    body, auth = model_server.requests[0]
    assert auth == 'Bearer protocol-test-only-secret'
    assert runtime.eid not in json.dumps(body) and runtime.doc['document_id'] not in json.dumps(body)
    assert 'ev_model_0001' in json.dumps(body)
    events = runtime.app.events.all_events(outcome['run_id'])
    assert 'protocol-test-only-secret' not in json.dumps(events)
    assert '明确标为合成资料' not in json.dumps(events, ensure_ascii=False)
    snapshots = list((runtime.settings.runs_dir / outcome['run_id'] / 'model-calls').glob('*.json'))
    assert len(snapshots) == 1
    record = json.loads(snapshots[0].read_text())
    assert record['call_record']['attempts'] == 1
    def forbidden(*args, **kwargs):
        pytest.fail('Replay attempted a new model call')
    runtime.app.model.infer_json = forbidden
    replay = runtime.app.orchestrator.replay(outcome['run_id'])
    assert replay['input_hash_match'] and replay['result_hash_match'] and replay['trace_match']
    assert replay['chain']['ok'] and len(model_server.requests) == 1
    assert events == runtime.app.events.all_events(outcome['run_id'])


def test_cold_offline_replay_uses_original_versions(runtime, monkeypatch, model_server):
    outcome = run(runtime)
    monkeypatch.setenv('FINSCOPE_MODEL_BACKEND', 'offline-rule')
    monkeypatch.delenv('FINSCOPE_MODEL_ENDPOINT')
    app = build_app(runtime.settings)
    try:
        install_role_b(app, runtime.manifest)
        replay = app.orchestrator.replay(outcome['run_id'])
        assert replay['input_hash_match'] and replay['result_hash_match'] and replay['trace_match']
        assert len(model_server.requests) == 1
    finally:
        app.close()


@pytest.mark.parametrize('damage', ['delete', 'change_and_rehash'])
def test_missing_or_tampered_response_never_falls_back_to_network(runtime, model_server, damage):
    outcome = run(runtime)
    path = next((runtime.settings.runs_dir / outcome['run_id'] / 'model-calls').glob('*.json'))
    if damage == 'delete':
        path.unlink()
    else:
        snapshot = json.loads(path.read_text())
        snapshot['output']['conclusions'][0]['text'] = 'corrupted'
        snapshot['output_hash'] = digest_of(snapshot['output'])
        path.write_text(json.dumps(snapshot))
    with pytest.raises(AppError, match='快照'):
        runtime.app.orchestrator.replay(outcome['run_id'])
    assert len(model_server.requests) == 1


def test_retry_transient_failure_and_pin_attempt_count(runtime, model_server):
    model_server.replies = [(503, b'provider-secret-must-not-enter-log')]
    outcome = run(runtime)
    assert outcome['status'] == 'needs_review' and len(model_server.requests) == 2
    assert runtime.app.model.calls[-1].attempts == 2


@pytest.mark.parametrize('model_id', ['qwen3.7-plus', 'qwen3.7-plus-2026-05-26', 'protocol-fixture'])
def test_qwen_plus_disables_thinking_without_changing_other_providers(runtime, model_server, model_id):
    model = RemoteJsonModel(Settings(data_dir=runtime.settings.data_dir, model_id=model_id))
    model.infer_json('industry_impact', [runtime.eid], 'IndustryData', {'scenario_ids': ['base']})
    body, _ = model_server.requests[-1]
    if model_id.startswith('qwen3.7-plus'):
        assert body['enable_thinking'] is False
    else:
        assert 'enable_thinking' not in body
    assert body['response_format'] == {'type': 'json_object'}


def test_uncited_memo_inference_has_an_explicit_review_item(runtime, model_server):
    statement = {'text': '合成情景，来源不足', 'kind': 'inference', 'evidence_ids': [],
                 'assumptions': ['未经证实的条件'], 'limitations': ['需要补充来源']}
    output = {'thesis': [statement], 'evidence': [], 'risks': [statement],
              'disconfirming_conditions': [statement], 'monitoring_indicators': ['待补充']}
    envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}
    model_server.replies = [(200, json.dumps(envelope).encode())]
    outcome = run(runtime, 'investment_memo')
    result = runtime.app.get_result(outcome['run_id'])
    assert outcome['status'] == 'needs_review'
    assert any(item['reason'] == 'Memo statement lacks source evidence' for item in result['review_items'])


@pytest.mark.parametrize('status,raw', [
    (401, b'provider-secret-must-not-enter-log'),
    (200, b'{bad json'),
    (200, b'{"choices": [], "choices": []}'),
    (200, b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}'),
    (200, b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"extra\\":true}"}}]}'),
    (200, b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"evidence_ids\\":[\\"ev_not_supplied\\"]}"}}]}'),
])
def test_bad_protocol_or_untrusted_citation_fails_redacted(runtime, model_server, status, raw):
    model_server.replies = [(status, raw)]
    outcome = run(runtime)
    assert outcome['status'] == 'failed'
    assert outcome['error']['code'] == 'MODEL_UNAVAILABLE'
    assert len(model_server.requests) == 1
    assert 'secret' not in json.dumps(outcome['error'])
    assert not list((runtime.settings.runs_dir / outcome['run_id']).glob('model-calls/*.json'))


@pytest.mark.parametrize('disclosure', [None, '2026-10-01'])
def test_memo_does_not_send_late_or_undated_excerpt(tmp_path, monkeypatch, model_server, disclosure):
    monkeypatch.setenv('FINSCOPE_MODEL_BACKEND', 'remote-json')
    monkeypatch.setenv('FINSCOPE_MODEL_ENDPOINT', model_server.endpoint)
    app = build_app(Settings(data_dir=tmp_path, model_id='protocol-fixture'))
    try:
        manifest = tmp_path / 'reviewed_facts.json'; init_manifest(app, manifest, [])
        doc = app.upload_document('source.txt', b'Synthetic only.', disclosure_date=disclosure)
        eid = app.parse_document(doc['document_id'])['evidence_ids'][0]
        attach_document(app, manifest, doc['document_id']); register_excerpt(app, manifest, doc['document_id'], eid, company_code='999999')
        install_role_b(app, manifest)
        runtime = SimpleNamespace(app=app, doc=doc)
        assert run(runtime, 'investment_memo')['status'] == 'needs_review'
        assert model_server.requests == []
    finally:
        app.close()


def test_semantic_registration_does_not_promote_or_invent_facts(runtime):
    data = json.loads(runtime.manifest.read_text())
    assert data['documents'][0]['facts'] == [] and not data.get('review_history')
    assert register_excerpt(runtime.app, runtime.manifest, runtime.doc['document_id'], runtime.eid, company_code='999999')['selected']
    port = ReviewedFactPort(runtime.manifest, runtime.app.documents, runtime.app.parse)
    assert port.extract_facts([runtime.doc['document_id']], ['industry_event']) == []
    assert len(port.archived_excerpts([runtime.doc['document_id']])) == 1
    assert port.archived_excerpts([]) == []


def test_wrong_document_or_changed_hash_rejects_semantic_source(runtime):
    other = runtime.app.upload_document('other.txt', b'Other synthetic source.', disclosure_date='2026-08-01')
    eid = runtime.app.parse_document(other['document_id'])['evidence_ids'][0]
    before = runtime.manifest.read_bytes()
    with pytest.raises(ValueError):
        register_excerpt(runtime.app, runtime.manifest, runtime.doc['document_id'], eid)
    assert runtime.manifest.read_bytes() == before
    payload = json.loads(before); payload['documents'][0]['sha256'] = '0' * 64
    port = ReviewedFactPort(runtime.manifest, runtime.app.documents, runtime.app.parse, snapshot=payload)
    with pytest.raises(ValueError, match='hash'):
        port.archived_excerpts([runtime.doc['document_id']])


def test_endpoint_validation_and_missing_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv('FINSCOPE_MODEL_ENDPOINT', raising=False)
    model = RemoteJsonModel(Settings(data_dir=tmp_path))
    with pytest.raises(AppError):
        model.infer_json('industry_impact', [], 'IndustryData', {})
    monkeypatch.setenv('FINSCOPE_MODEL_ENDPOINT', 'https://user:password@example.test/v1/chat/completions')
    with pytest.raises(ValueError, match='credential-free'):
        RemoteJsonModel(Settings(data_dir=tmp_path))


def test_request_uses_public_fields_and_aliases_not_private_ledger(runtime, model_server):
    eid = runtime.eid
    output = runtime.app.model.infer_json('investment_memo', [eid], 'MemoData', {
        'company_code': '999999', 'source_items': [{
            'fact_id': 'fact_private_ledger', 'document_id': 'doc_private_ledger',
            'metric': 'revenue', 'value': '100', 'unit': 'CNY', 'evidence_ids': [eid],
            'reviewer': 'private reviewer', 'sha256': 'a' * 64, 'bbox': [0, 0, 100, 100],
            '_path': '/private/not-for-model', 'review_history': ['private decision'],
        }]})
    request = json.loads(model_server.requests[0][0]['messages'][-1]['content'])
    item = request['context']['source_items'][0]
    assert set(item) == {'metric', 'value', 'unit', 'evidence_ids'}
    assert item['value'] == '100' and item['evidence_ids'] == ['ev_model_0001']
    assert output['thesis'][0]['evidence_ids'] == [eid]


def test_model_revision_changes_input_fingerprint(tmp_path):
    app1 = build_app(Settings(data_dir=tmp_path/'one', model_revision='one'))
    app2 = build_app(Settings(data_dir=tmp_path/'two', model_revision='two'))
    try:
        task_input = {'focus_company_code': '999999', 'event_description': 'x', 'scenario_ids': ['base']}
        assert app1.orchestrator._input_bundle([], 'industry_impact', task_input) != app2.orchestrator._input_bundle([], 'industry_impact', task_input)
    finally:
        app1.close(); app2.close()


def test_command_runs_and_replays_the_same_pinned_response(runtime, monkeypatch, model_server, capsys):
    from app.tasks.synthesis_runtime import main
    monkeypatch.setenv('FINSCOPE_MODEL_ID', 'protocol-fixture')
    monkeypatch.setenv('FINSCOPE_MODEL_REVISION', 'fixture-only')
    common = ['--data-dir', str(runtime.settings.data_dir), '--manifest', str(runtime.manifest)]
    assert main(common + ['run', '--task', 'industry_impact', '--company', '999999',
                          '--documents', runtime.doc['document_id'], '--event', '供需变化']) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['model_call_count'] == 1 and summary['status'] == 'needs_review'
    assert main(common + ['replay', summary['run_id']]) == 0
    assert json.loads(capsys.readouterr().out)['replay_ok']
    assert len(model_server.requests) == 1


@pytest.mark.parametrize('task', ['industry_impact', 'investment_memo'])
@pytest.mark.parametrize('scope,owner,included', [('company', '888888', False), ('industry', None, True), ('unclassified', None, False)])
def test_semantic_scope_filters_peers_and_unclassified_sources(runtime, model_server, task, scope, owner, included):
    extra = runtime.app.upload_document('other.txt', b'EXTRA_SCOPE_SENTINEL', disclosure_date='2026-08-01')
    eid = runtime.app.parse_document(extra['document_id'])['evidence_ids'][0]
    attach_document(runtime.app, runtime.manifest, extra['document_id'])
    register_excerpt(runtime.app, runtime.manifest, extra['document_id'], eid, company_code=owner, scope=scope)
    task_input = ({'focus_company_code': '999999', 'event_description': 'test', 'scenario_ids': ['base']}
                  if task == 'industry_impact' else {'company_code': '999999', 'as_of_date': '2026-09-30'})
    outcome = runtime.app.run_task({'api_version': '1.0.0', 'task_type': task,
        'document_ids': [runtime.doc['document_id'], extra['document_id']], 'task_input': task_input})
    assert outcome['status'] == 'needs_review'
    body = model_server.requests[0][0]
    assert ('EXTRA_SCOPE_SENTINEL' in json.dumps(body)) is included


def test_rebinding_semantic_company_is_rejected_without_changing_manifest(runtime):
    before = runtime.manifest.read_bytes()
    with pytest.raises(ValueError, match='reassign'):
        register_excerpt(runtime.app, runtime.manifest, runtime.doc['document_id'], runtime.eid, company_code='888888')
    assert runtime.manifest.read_bytes() == before


def test_model_extraction_runs_through_http_and_cold_offline_replay(runtime, model_server, monkeypatch):
    from dataclasses import replace
    settings = replace(runtime.settings, extraction_backend='model')
    app = build_app(settings)
    try:
        doc = app.upload_document('synthetic-pledge.txt',
            '证券代码：999999\n质押公告\n公告日期：2026年9月22日\n股东名称：张某\n本次质押股数：12万股'.encode())
        def output(user):
            refs = user['evidence_ids']
            fields = [{'field_code': code, 'raw_value': value, 'value': '999999', 'unit': 'none',
                       'evidence_ids': refs, 'status': 'verified', 'reason': None}
                      for code, value in [('company_code', '999999'), ('event_date', '2026年9月22日'),
                                           ('shareholder', '张某'), ('pledged_shares', '12万股')]]
            return {'records': [{'event_id': 'model_event', 'document_id': 'doc_model_0001',
                                'company_code': '999999', 'event_date': None,
                                'announcement_type': 'pledge', 'fields': fields}]}
        model_server.extraction_output = output
        outcome = app.run_task({'api_version': '1.0.0', 'task_type': 'structured_extraction',
            'document_ids': [doc['document_id']], 'task_input': {'announcement_types': ['pledge']}})
        assert outcome['status'] == 'needs_review'
        result = app.get_result(outcome['run_id'])
        field = next(f for f in result['data']['records'][0]['fields'] if f['field_code'] == 'pledged_shares')
        assert field['value'] == '120000' and field['status'] == 'needs_review'
        assert doc['document_id'] not in json.dumps(model_server.requests[0][0])
        app.close()
        monkeypatch.setenv('FINSCOPE_MODEL_BACKEND', 'offline-rule')
        monkeypatch.delenv('FINSCOPE_MODEL_ENDPOINT')
        app = build_app(settings)
        proof = app.orchestrator.replay(outcome['run_id'])
        assert proof['input_hash_match'] and proof['result_hash_match'] and proof['trace_match'] and proof['chain']['ok']
        assert len(model_server.requests) == 1
    finally:
        app.close()
