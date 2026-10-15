"""Create a new, explicitly local engineering draft from a pinned source candidate."""
from copy import deepcopy
from dataclasses import replace
import argparse
import json
from pathlib import Path

from app.common.ids import digest_of
from app.common.contracts import validate_contract
from app.config import Settings
from app.container import build_app
from app.models.adapter import OfflineRuleModel
from app.errors import AppError
from app.tasks.ab_runtime import current_code_version
from app.tasks.a_bridge import install_role_b


def apply_plan(data, edits, bindings):
    result = deepcopy(data)
    for edit in edits:
        target = result
        for key in edit['path'][:-1]:
            target = target[key]
        key = edit['path'][-1]
        expected = ([bindings[name] for name in edit['expected_sources']]
                    if 'expected_sources' in edit else edit['expected'])
        if target[key] != expected:
            raise ValueError('Source field differs from the reviewed revision plan')
        if edit['operation'] == 'replace':
            target[key] = deepcopy(edit['value'])
        elif edit['operation'] == 'append_source':
            eid = bindings[edit['source']]
            if not isinstance(target[key], list) or not isinstance(eid, str) or not eid.startswith('ev_'):
                raise ValueError('Invalid continuation binding')
            target[key] = list(dict.fromkeys(target[key] + [eid]))
        else:
            raise ValueError('Unknown revision operation')
    return result


def revise(data_dir, source_run, plan, bindings):
    settings = Settings(data_dir=Path(data_dir), code_version=current_code_version())
    reader = build_app(settings, model=OfflineRuleModel(settings))
    try:
        run = reader.get_run(source_run)
        if run['task_type'] != plan['task_type'] or run['task_type'] not in {'industry_impact', 'investment_memo'}:
            raise ValueError('Revision plan does not match the source task')
        snapshots = list((settings.runs_dir/source_run/'model-calls').glob('*.json'))
        if len(snapshots) != 1:
            raise ValueError('A unique original model snapshot is required')
        snapshot = json.loads(snapshots[0].read_text())
        events = reader.events.all_events(source_run)
        if not reader.events.verify_chain(source_run)['ok']:
            raise ValueError('Original event chain does not verify')
        if not any(e['tool_name'] == 'model.infer_json' and e['output_hash'] == digest_of(snapshot) for e in events):
            raise ValueError('Source model snapshot differs from its original event')
        definition = 'IndustryData' if run['task_type'] == 'industry_impact' else 'MemoData'
        data = apply_plan(snapshot['output'], plan['edits'], bindings)
        validate_contract(definition, data)
        with reader.store.write() as conn:
            task_input = json.loads(conn.execute('SELECT task_input FROM runs WHERE run_id=?', (source_run,)).fetchone()[0])
    finally:
        reader.close()
    manifest = settings.runs_dir/source_run/'b-reviewed-facts.json'
    if not manifest.is_file():
        raise ValueError('Original fixed facts and qualitative sources are required')
    fingerprint = digest_of(json.loads(manifest.read_text()))
    if not any(e['tool_name'] == 'b.reviewed_fact_snapshot' and e['output_hash'] == fingerprint for e in events):
        raise ValueError('Original source manifest differs from the fixed event')
    class EngineeringDraft:
        calls = []
        def infer_json(self, task_code, evidence_ids, schema_name, context):
            if task_code != plan['task_type'] or schema_name != definition:
                raise ValueError('Unexpected revision task')
            if not set(bindings.values()) <= set(evidence_ids):
                raise ValueError('Continuation was not supplied in the original qualitative sources')
            return deepcopy(data)
    settings = replace(settings, model_id='local-ai-engineering-draft', model_revision=source_run,
                       prompt_version='review-plan-'+digest_of(plan)[:16])
    app = build_app(settings, model=EngineeringDraft())
    try:
        install_role_b(app, manifest)
        new = app.run_task({'api_version':'1.0.0', 'task_type':run['task_type'],
                           'document_ids':run['document_ids'], 'task_input':task_input})
        if new['status'] == 'failed':
            raise ValueError('Revised task failed source or contract validation')
        result = app.get_result(new['run_id'])
        app.export_run(new['run_id'], formats=['json','html'])
        proof = app.orchestrator.replay(new['run_id'])
        good = all(proof[k] for k in ['input_hash_match','result_hash_match','trace_match']) and proof['chain']['ok']
        if not good:
            raise ValueError('Revised draft replay differs')
        from eval.annotations import template
        company = task_input.get('company_code',task_input.get('focus_company_code'))
        for person in ['a','b']:
            packet = template(result,'c-second-review',company,'development',False)
            path = settings.runs_dir/new['run_id']/('annotation_'+person+'_blank.json')
            with path.open('x') as stream:
                json.dump(packet,stream,ensure_ascii=False,indent=2)
        return {'run_id':new['run_id'],'result_hash':new['result_hash'],'source_run':source_run,
                'status':new['status'],'external_model_calls':0,'replay_ok':good,'human_gold':False}
    finally:
        app.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--source-run',required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--bind',action='append',default=[],help='source_alias=existing_evidence_id')
    args=parser.parse_args()
    try:
        summary=revise(args.data_dir,args.source_run,json.loads(args.plan.read_text()),dict(x.split('=',1) for x in args.bind))
    except (ValueError,KeyError,OSError,AppError):
        print(json.dumps({'status':'failed','error_code':'VALIDATION_ERROR'}))
        return 1
    print(json.dumps(summary))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
