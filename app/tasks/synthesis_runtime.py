"""Run or replay B's qualitative tasks using explicit archived sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None):
    from app.tasks.ab_runtime import execute

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
    if args.command == 'replay':
        code, summary = execute(args.data_dir, args.manifest, run_id=args.run_id)
    else:
        task_input = ({'focus_company_code': args.company, 'event_description': args.event, 'scenario_ids': args.scenarios}
                      if args.task == 'industry_impact' else {'company_code': args.company, 'as_of_date': args.as_of})
        code, summary = execute(args.data_dir, args.manifest, request={
            'api_version': '1.0.0', 'task_type': args.task,
            'document_ids': args.documents, 'task_input': task_input})
    print(json.dumps(summary))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
