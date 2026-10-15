"""Internal source selection for qualitative tasks, separate from financial Facts."""
from __future__ import annotations


def sources(context, *, company=None, as_of_date=None, financial=False):
    from app.facts import METRICS
    codes = list(METRICS) + ['industry_event'] if financial else ['industry_event']
    items = context.evidence.extract_facts(list(context.document_ids), codes)
    excerpts = getattr(context.evidence, 'archived_excerpts', None)
    if callable(excerpts):
        items += [{**item, 'source_kind': 'archived_excerpt_needs_review'}
                  for item in excerpts(list(context.document_ids))]
    selected = []
    for item in items:
        if item.get('source_kind') == 'archived_excerpt_needs_review':
            scope = item.get('source_scope')
            if scope not in {'company', 'industry'}:
                continue  # Old unbound excerpts are retained but need an explicit owner.
            if company and scope == 'company' and item.get('company_code') != company:
                continue
        if company is not None and item.get('company_code') not in {company, None}:
            continue
        if item.get('review_status') not in {None, 'verified'}:
            continue
        if as_of_date and item.get('period_end', as_of_date) > as_of_date:
            continue
        ids = item.get('evidence_ids', [])
        if not ids:
            continue
        eligible = True
        for eid in ids:
            evidence = context.evidence.get_evidence(eid)
            if evidence['document_id'] not in context.document_ids:
                raise ValueError('Synthesis source is outside the Run snapshot')
            document = context.documents.get_document(evidence['document_id'])
            day = document.get('disclosure_date')
            if as_of_date and (not day or day > as_of_date):
                eligible = False
        if eligible:
            selected.append(item)
    return selected
