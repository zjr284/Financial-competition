"""Register explicit semantic excerpts without creating or confirming financial Facts."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.facts.review_workflow import _load_manifest, _write_validated
from app.facts.reviewed_store import ReviewedFactPort


def register_excerpt(app, path: Path, document_id: str, evidence_id: str, *, company_code=None, scope=None):
    payload = _load_manifest(path)
    matches = [b for b in payload['documents'] if b.get('document_id') == document_id]
    if len(matches) != 1:
        raise ValueError('Attach the document before selecting a semantic excerpt')
    bundle = matches[0]
    if company_code is not None and (not isinstance(company_code, str) or len(company_code) != 6 or not company_code.isascii() or not company_code.isdigit()):
        raise ValueError('Semantic company code must contain six ASCII digits')
    scope = scope or ('company' if company_code else 'unclassified')
    if scope not in {'company', 'industry', 'unclassified'} or (scope == 'company') != bool(company_code):
        raise ValueError('Company excerpts require a company code; industry excerpts must not bind a company')
    known_companies = {fact['company_code'] for fact in bundle.get('facts', []) if fact.get('company_code')}
    if company_code and known_companies and known_companies != {company_code}:
        raise ValueError('Semantic company differs from existing document facts')
    previous = (bundle.get('semantic_scope', 'unclassified'), bundle.get('semantic_company_code'))
    if bundle.get('semantic_evidence_ids') and previous != ('unclassified', None) and previous != (scope, company_code):
        raise ValueError('Cannot reassign existing semantic excerpts to another company or scope')
    bundle['semantic_scope'] = scope
    bundle['semantic_company_code'] = company_code
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
    parser.add_argument('--company', help='Six-digit owner of company-specific excerpts')
    parser.add_argument('--scope', choices=['company', 'industry', 'unclassified'])
    args = parser.parse_args()
    app = build_app(Settings(data_dir=args.data_dir))
    try:
        import json
        print(json.dumps(register_excerpt(app, args.manifest, args.document_id, args.evidence_id,
                                         company_code=args.company, scope=args.scope)))
    finally:
        app.close()


if __name__ == '__main__':
    main()
