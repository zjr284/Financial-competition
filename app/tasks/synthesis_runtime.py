"""Run or replay B's qualitative tasks using explicit archived sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def main(argv=None):
    from app.config import Settings
    from app.container import build_app
    from app.tasks.a_bridge import install_role_b

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--task', choices=['industry_impact', 'investment_memo'], required=True)
    run.add_argument('--company', required=True)
    run.add_argument('--documents', nargs='+', required=True)
    run.add_argument('--as-of')
    run.add_argument('--event')
    run.add_argument('--scenarios', nargs='+', default=['base'])
    replay = commands.add_parser('replay')
    replay.add_argument('run_id')
    args = parser.parse_args(argv)
    if args.command == 'run' and ((args.task == 'industry_impact' and not args.event)
                                  or (args.task == 'investment_memo' and not args.as_of)):
        parser.error('industry_impact requires --event; investment_memo requires --as-of')
    root = Path(__file__).resolve().parents[2]
    version = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if subprocess.run(['git', 'diff', '--quiet'], cwd=root).returncode:
        version += '+worktree'
    app = build_app(Settings(data_dir=args.data_dir, code_version=version))
    try:
        install_role_b(app, args.manifest or args.data_dir / 'reviewed_facts.json')
        if args.command == 'replay':
            proof = app.orchestrator.replay(args.run_id)
            good = all(proof[k] for k in ['input_hash_match', 'result_hash_match', 'trace_match']) and proof['chain']['ok']
            print(json.dumps({'run_id': args.run_id, 'replay_ok': good}))
            return 0 if good else 1
        task_input = ({'focus_company_code': args.company, 'event_description': args.event, 'scenario_ids': args.scenarios}
                      if args.task == 'industry_impact' else {'company_code': args.company, 'as_of_date': args.as_of})
        outcome = app.run_task({'api_version': '1.0.0', 'task_type': args.task,
                                'document_ids': args.documents, 'task_input': task_input})
        if outcome['status'] == 'failed':
            print(json.dumps({'run_id': outcome['run_id'], 'status': 'failed', 'error_code': outcome['error']['code']}))
            return 1
        result = app.get_result(outcome['run_id'])
        app.export_run(outcome['run_id'], formats=['json', 'html'])
        print(json.dumps({'run_id': outcome['run_id'], 'status': outcome['status'],
                          'evidence_count': len(result['evidence_ids']), 'review_count': len(result['review_items']),
                          'model_call_count': sum(e['event_type'] == 'model_call' for e in app.events.all_events(outcome['run_id'])),
                          'result_hash': outcome['result_hash']}))
        return 0
    finally:
        app.close()


if __name__ == '__main__':
    raise SystemExit(main())
