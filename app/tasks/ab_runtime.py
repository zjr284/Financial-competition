"""Run five A/B tasks or replay their pinned inputs without a model service."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess

from app.common.contracts import validate_contract
from app.common.ids import digest_of
from app.config import Settings
from app.container import build_app
from app.models.adapter import OfflineRuleModel
from app.errors import AppError
from app.tasks.a_bridge import install_role_b

TASKS = {'structured_extraction', 'financial_analysis', 'valuation', 'industry_impact', 'investment_memo'}


class PreflightError(ValueError):
    """Fixed, non-sensitive instructions safe to show in a CLI summary."""


def current_code_version():
    """Identify this checkout and runtime source bytes, including uncommitted new code."""
    root = Path(__file__).resolve().parents[2]
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    digest = hashlib.sha256()
    for folder in ('app', 'contracts'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.json', '.yaml', '.yml'}:
                digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes() + b'\0')
    return head + '+src-' + digest.hexdigest()[:16]


def execute(data_dir, manifest, *, request=None, run_id=None):
    version = current_code_version()
    settings = Settings(data_dir=Path(data_dir), code_version=version)
    if run_id is not None:
        if not re.fullmatch(r'run_[A-Za-z0-9_-]+', run_id):
            raise PreflightError('Invalid Run ID')
        run_dir = settings.runs_dir / run_id
        frozen = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
        recorded, bundle = frozen['run'], frozen['input_bundle']
        validate_contract('Run', recorded)
        if recorded['run_id'] != run_id or recorded['task_type'] not in TASKS:
            raise PreflightError('Run is outside the A/B runtime')
        if digest_of(bundle) != recorded['input_hash']:
            raise PreflightError('Pinned input bundle does not match the original Run')
        if recorded['code_version'] != version or bundle['code_version'] != version:
            raise PreflightError('Code version differs; use the original checkout and unchanged runtime sources')
        if recorded['task_type'] != 'structured_extraction' and not (run_dir / 'b-reviewed-facts.json').is_file():
            raise PreflightError('Missing pinned B fact snapshot; replay cannot read a mutable manifest')
        settings = replace(settings, model_id=recorded['model_id'], model_revision=recorded['model_revision'],
                           prompt_version=recorded['prompt_version'],
                           extraction_backend=bundle.get('extraction_backend', 'rules'),
                           extraction_grid=bundle.get('extraction_grid', False))
        model = OfflineRuleModel(settings)  # No endpoint/key validation and no network fallback.
    else:
        validate_contract('RunRequest', request)
        if request['task_type'] not in TASKS:
            raise PreflightError('Task is outside the A/B runtime')
        pure_tool = (request['task_type'] in {'financial_analysis', 'valuation'}
                     or (request['task_type'] == 'structured_extraction' and settings.extraction_backend == 'rules'))
        model = OfflineRuleModel(settings) if pure_tool else None
    app = build_app(settings, model=model)
    try:
        if run_id is not None:
            stored = app.get_run(run_id)
            for key in ('task_type', 'document_ids', 'input_hash', 'code_version', 'model_id',
                        'model_revision', 'prompt_version'):
                if stored[key] != recorded[key]:
                    raise PreflightError('Manifest metadata differs from the stored Run')
        install_role_b(app, manifest or settings.data_dir / 'reviewed_facts.json')
        if run_id is not None:
            proof = app.orchestrator.replay(run_id)
            good = all(proof[k] for k in ('input_hash_match', 'result_hash_match', 'trace_match')) and proof['chain']['ok']
            return (0 if good else 1), {'run_id': run_id, 'replay_ok': good,
                **{k: proof[k] for k in ('input_hash_match', 'result_hash_match', 'trace_match')},
                'event_chain_ok': proof['chain']['ok']}
        outcome = app.run_task(request)
        if outcome['status'] == 'failed':
            return 1, {'run_id': outcome['run_id'], 'status': 'failed', 'error_code': outcome['error']['code']}
        result = app.get_result(outcome['run_id'])
        app.export_run(outcome['run_id'], formats=['json', 'html'])
        return 0, {'run_id': outcome['run_id'], 'status': outcome['status'],
            'evidence_count': len(result['evidence_ids']), 'calculation_count': len(result['calculations']),
            'review_count': len(result['review_items']),
            'model_call_count': sum(e['event_type'] == 'model_call' for e in app.events.all_events(outcome['run_id'])),
            'result_hash': outcome['result_hash']}
    finally:
        app.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--request', type=Path, required=True, help='Private file conforming to RunRequest')
    replay = commands.add_parser('replay')
    replay.add_argument('run_id')
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding='utf-8')) if args.command == 'run' else None
        code, summary = execute(args.data_dir, args.manifest, request=request,
                                run_id=args.run_id if args.command == 'replay' else None)
    except (ValueError, KeyError, OSError, AppError) as exc:
        # No request payload, endpoint, values or local file paths on stdout.
        summary = {'status': 'failed', 'error_code': exc.code if isinstance(exc, AppError) else 'VALIDATION_ERROR',
                   'error_type': type(exc).__name__}
        if isinstance(exc, PreflightError):
            summary['hint'] = str(exc)
        print(json.dumps(summary))
        return 1
    print(json.dumps(summary))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
