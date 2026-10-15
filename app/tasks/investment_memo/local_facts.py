"""As-of, disclosed financial facts for a local-only memo appendix."""
from __future__ import annotations

from app.facts.catalog import FactConflict, FactIndex, METRICS
from app.tasks._finance_common import review_item, source_ids_for_facts


APPENDIX_METRICS = (
    'revenue', 'net_profit_parent', 'net_profit_parent_excl_nonrecurring',
    'operating_cash_flow',
)


def financial_appendix(context, company, as_of_date):
    eligible = []
    for fact in context.evidence.extract_facts(list(context.document_ids), list(APPENDIX_METRICS)):
        if (fact['company_code'] != company or fact['metric'] not in APPENDIX_METRICS
                or fact['statement_scope'] != 'consolidated'
                or fact['period_type'] != 'cumulative' or fact['period_end'] > as_of_date):
            continue
        source_ids_for_facts(context, [fact])
        days = [context.documents.get_document(context.evidence.get_evidence(eid)['document_id'])
                .get('disclosure_date') for eid in fact['evidence_ids']]
        if all(day and day <= as_of_date for day in days):
            eligible.append(fact)
    index = FactIndex(eligible)
    statements, reviews = [], []
    for metric in APPENDIX_METRICS:
        candidates = [f for f in eligible if f['metric'] == metric and f['review_status'] != 'rejected']
        if not candidates:
            reviews.append(review_item(context.run_id, f'Memo financial appendix missing eligible {metric}'))
            continue
        end = max(f['period_end'] for f in candidates)
        try:
            chosen = index.select(company, metric, end, 'consolidated', 'cumulative')
        except FactConflict:
            reviews.append(review_item(context.run_id, f'Memo financial appendix has conflicting {metric} at {end}',
                                       [eid for f in candidates if f['period_end'] == end for eid in f['evidence_ids']]))
            continue
        if chosen['review_status'] != 'verified' or chosen['value'] is None:
            reviews.append(review_item(context.run_id, f'Memo financial appendix latest {metric} requires review',
                                       chosen['evidence_ids']))
            continue
        statements.append({
            'text': (f"本地已复核原始事实：公司 {company}，{chosen['period_start']} 至 {end}，"
                     f"合并报表累计{METRICS[metric].label} {chosen['value']} 元人民币；"
                     f"重述口径：{'是' if chosen['restated'] else '否'}。"),
            'kind': 'fact', 'evidence_ids': list(chosen['evidence_ids']), 'assumptions': [],
            'limitations': [f"本地事实 {chosen['fact_id']}；保留原期间及口径，各指标期间可能不同。",
                            '该条由本地复核事实直接组装，未发送给模型；不代表 TTM、增长率或估值结论。'],
        })
    return statements, reviews
