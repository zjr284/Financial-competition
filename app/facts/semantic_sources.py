"""Register explicit semantic excerpts without creating or confirming financial Facts."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.facts.review_workflow import _load_manifest, _write_validated
from app.facts.reviewed_store import ReviewedFactPort


def register_excerpt(app, path: Path, document_id: str, evidence_id: str):
    payload = _load_manifest(path)
    matches = [b for b in payload['documents'] if b.get('document_id') == document_id]
    if len(matches) != 1:
        raise ValueError('Attach the document before selecting a semantic excerpt')
    bundle = matches[0]
    ids = bundle.setdefault('semantic_evidence_ids', [])
    if evidence_id not in ids:
        ids.append(evidence_id)
    candidate = ReviewedFactPort(path, app.documents, app.parse, snapshot=payload)
    candidate.archived_excerpts([b['document_id'] for b in payload['documents']])
    _write_validated(app, path, payload)
    return {'selected': True, 'kind': 'archived_excerpt_needs_review'}


def main():
    from app.config import Settings
    from app.container import build_app
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--evidence-id', required=True)
    args = parser.parse_args()
    app = build_app(Settings(data_dir=args.data_dir))
    try:
        import json
        print(json.dumps(register_excerpt(app, args.manifest, args.document_id, args.evidence_id)))
    finally:
        app.close()


if __name__ == '__main__':
    main()
