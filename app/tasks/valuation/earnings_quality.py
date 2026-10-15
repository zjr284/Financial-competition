"""Compare reported and adjusted TTM via tools; never substitute the PE basis."""
from app.facts import FactConflict
from app.tasks._finance_common import record_calculation, unique


def target_earnings_quality(context, index, facts, target_profit, origins, upstream):
    refs = target_profit['evidence_ids']
    parent_id = origins.get(target_profit['fact_id'])
    parent = next((c for c in upstream if c['calculation_id'] == parent_id), None)
    if parent is None:
        return [], [('Target earnings quality: no reported TTM derivation available for adjusted comparison', refs)]
    company, scope = target_profit['company_code'], target_profit['statement_scope']
    periods = [parent['parameters'][role + '_period_end'] for role in ('annual', 'current_ytd', 'prior_ytd')]
    try:
        inputs = [index.select(company, 'net_profit_parent_excl_nonrecurring', end, scope, 'cumulative')
                  for end in periods]
    except FactConflict:
        refs = unique(e for f in facts if f['company_code'] == company
                      and f['metric'] == 'net_profit_parent_excl_nonrecurring' for e in f['evidence_ids'])
        return [], [('Target earnings quality: conflicting adjusted profits', refs)]
    if any(f is None for f in inputs):
        return [], [('Target earnings quality: missing adjusted profit inputs; PE earnings sustainability needs review', refs)]
    refs = unique(refs + [e for f in inputs for e in f['evidence_ids']])
    if any(f['review_status'] != 'verified' or f['value'] is None for f in inputs):
        return [], [('Target earnings quality: adjusted profit inputs need review', refs)]
    adjusted = context.calculations.calculate('ttm_from_ytd', [f['fact_id'] for f in inputs], [], {})
    record_calculation(context, adjusted)
    if adjusted['status'] != 'verified':
        return [adjusted], [('Target earnings quality: ' + adjusted['reason'], refs)]
    gap = context.calculations.calculate('nonrecurring_gap_share', [], [parent_id, adjusted['calculation_id']],
        {key: target_profit[key] for key in ('company_code', 'period_start', 'period_end', 'statement_scope')})
    record_calculation(context, gap)
    calculations = [adjusted, gap]
    if gap['status'] != 'verified':
        return calculations, [('Target earnings quality: ' + gap['reason'], refs)]
    if gap['value'] != '0':
        return calculations, [(f"Target earnings quality: reported versus adjusted TTM gap {gap['value']}%; "
                               'PE uses reported parent profit; review nonrecurring earnings sustainability', refs)]
    return calculations, []
