"""Read-only, local regression checks for a specified semantic revision plan.

This checks exact reviewed fields, not general semantic accuracy or human gold.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.common.contracts import validate_contract
from app.common.ids import digest_of
from app.errors import AppError
from app.logging.events import EventLog
from app.orchestrator.service import Orchestrator


SECTIONS = {
    'industry_impact': {'conclusions', 'edges', 'scenarios'},
    'investment_memo': {'thesis', 'evidence', 'risks', 'disconfirming_conditions'},
}


def check_fields(result, plan, bindings, allowed_sources):
    """Return only check indexes/statuses; never echo text, IDs or private values."""
    validate_contract('TaskResult', result)
    task = result['task_type']
    if not isinstance(plan, dict):
        raise ValueError('Invalid plan')
    if task not in SECTIONS or task != plan.get('task_type'):
        raise ValueError('Task mismatch')
    edits = plan.get('edits')
    if not isinstance(edits, list) or not edits:
        raise ValueError('A nonempty explicit revision plan is required')
    checks, seen = [], set()
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError('Invalid review check')
        path = edit.get('path')
        if (not isinstance(path, list) or len(path) != 3 or path[0] not in SECTIONS[task]
                or type(path[1]) is not int or path[1] < 0
                or path[2] not in {'text', 'assumptions', 'limitations', 'mechanism',
                                   'impact_text', 'evidence_ids'}):
            raise ValueError('Invalid review field path')
        key = tuple(path)
        if key in seen:
            raise ValueError('Duplicate review field')
        seen.add(key)
        operation = edit.get('operation')
        if operation == 'replace' and path[2] != 'evidence_ids':
            expected = edit['value']
            if not isinstance(expected, (str, list)):
                raise ValueError('Invalid reviewed value')
        elif operation == 'append_source' and path[2] == 'evidence_ids':
            names = edit['expected_sources'] + [edit['source']]
            if not names or any(name not in bindings for name in names):
                raise ValueError('Missing source binding')
            expected = list(dict.fromkeys(bindings[name] for name in names))
            if len(expected) != len(names) or not set(expected) <= set(allowed_sources):
                raise ValueError('Sources must be distinct and in the pinned qualitative source manifest')
        else:
            raise ValueError('Unsupported review operation')
        try:
            actual = result['data'][path[0]][path[1]][path[2]]
            passed = actual == expected
        except (KeyError, IndexError, TypeError):
            passed = False
        checks.append({'check': index, 'passed': passed})
    return {
        'status': 'passed' if all(c['passed'] for c in checks) else 'failed',
        'scope': 'specified_revision_fields_only', 'checks': checks,
        'result_hash': Orchestrator._result_hash(result), 'plan_hash': digest_of(plan),
        'bindings_hash': digest_of(bindings),
        'human_gold': False, 'review_status': 'needs_review',
        'acceptance_model_calls': 0, 'replay_verified': False,
    }


def check_run(run_dir, plan, bindings):
    """Read pinned artifacts and registry; never initialize or migrate the database."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    recorded, bundle = manifest['run'], manifest['input_bundle']
    # The manifest records the initial queued Run, not its final registry state.
    uri = (run_dir.parent.parent/'metadata.sqlite3').resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute('BEGIN')
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT payload, result FROM runs WHERE run_id=?',
                           (recorded['run_id'],)).fetchone()
        if row is None:
            raise ValueError('Missing registered Run')
        run = json.loads(row['payload'])
        registered_result = json.loads(row['result'] or '{}')
        class ReadOnlyStore:
            @contextmanager
            def write(self):
                yield conn
        log = EventLog(ReadOnlyStore(), None)
        if not log.verify_chain(run['run_id'])['ok']:
            raise ValueError('Event chain mismatch')
        events = log.all_events(run['run_id'])
    for key in ('run_id', 'task_type', 'document_ids', 'input_hash',
                'code_version', 'model_id', 'model_revision', 'prompt_version'):
        if run[key] != recorded[key]:
            raise ValueError('Run metadata mismatch')
    validate_contract('Run', run)
    if run['task_type'] not in SECTIONS or run['status'] not in {'succeeded', 'needs_review'}:
        raise ValueError('A finished semantic candidate is required')
    if digest_of(bundle) != run['input_hash']:
        raise ValueError('Pinned input mismatch')
    files = [p for p in (run_dir/'result.json', run_dir/'result.needs_review.json') if p.is_file()]
    if len(files) != 1:
        raise ValueError('A unique result is required')
    result = json.loads(files[0].read_text(encoding='utf-8'))
    if (result['run_id'] != run['run_id'] or result['task_type'] != run['task_type']
            or result['input_document_ids'] != run['document_ids']
            or result != registered_result
            or Orchestrator._result_hash(result) != run['result_hash']):
        raise ValueError('Pinned result mismatch')
    sources = json.loads((run_dir/'b-reviewed-facts.json').read_text(encoding='utf-8'))
    source_hash = digest_of(sources)
    if not any(e['run_id'] == run['run_id'] and e['tool_name'] == 'b.reviewed_fact_snapshot'
               and e['output_hash'] == source_hash for e in events):
        raise ValueError('Pinned source manifest mismatch')
    allowed = {eid for doc in sources['documents'] for eid in doc.get('semantic_evidence_ids', [])}
    report = check_fields(result, plan, bindings, allowed)
    report.update(input_hash=run['input_hash'], source_manifest_hash=source_hash,
                  model_id_hash=digest_of(run['model_id']),
                  local_engineering_draft=run['model_id'] == 'local-ai-engineering-draft',
                  model_revision_hash=digest_of(run['model_revision']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--bind', action='append', default=[], help='alias=existing_evidence_id')
    args = parser.parse_args(argv)
    try:
        pairs = [value.split('=', 1) for value in args.bind]
        bindings = dict(pairs)
        if len(bindings) != len(pairs):
            raise ValueError('Duplicate binding')
        report = check_run(args.run_dir, json.loads(args.plan.read_text(encoding='utf-8')), bindings)
    except (ValueError, KeyError, IndexError, TypeError, OSError, AppError, sqlite3.Error):
        print(json.dumps({'status': 'failed', 'error_code': 'VALIDATION_ERROR',
                          'human_gold': False, 'review_status': 'needs_review'}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
