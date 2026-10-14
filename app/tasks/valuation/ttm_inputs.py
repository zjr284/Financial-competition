"""B-owned TTM selection; derived values stay internal, never become source Facts.

The production CalculationPort performs all arithmetic and validates the TTM
source periods independently. Only reviewed original inputs are accepted.
"""
from datetime import date, timedelta

from app.facts import FactConflict, FactIndex
from app.tasks._finance_common import record_calculation, unique


def prepare_ttm_inputs(context, facts, scope, as_of):
    index = FactIndex(facts)
    origins, calculations, issues = {}, [], []
    derived = []
    companies = sorted({f["company_code"] for f in facts if f["metric"] == "net_profit_parent"})
    for company in companies:
        candidates = [f for f in facts if f["company_code"] == company
                      and f["metric"] == "net_profit_parent" and f["statement_scope"] == scope
                      and f["period_type"] == "cumulative" and f["review_status"] != "rejected"
                      and f["period_end"] <= as_of
                      and f["period_end"][5:] in {"03-31", "06-30", "09-30", "12-31"}
                      and f["period_start"] == f["period_end"][:4] + "-01-01"]
        if not candidates:
            continue
        end = max(f["period_end"] for f in candidates)
        # Existing direct TTM inputs retain the legacy path; do not create duplicates.
        if any(f["company_code"] == company and f["metric"] == "net_profit_parent_ttm"
               and f["statement_scope"] == scope and f["period_end"] >= end
               and f["review_status"] != "rejected" for f in facts):
            continue
        year = int(end[:4]); prior_end = str(year - 1) + end[4:]
        refs = unique(e for f in candidates for e in f["evidence_ids"])
        try:
            inputs = [index.select(company, "net_profit_parent", d, scope, "cumulative")
                      for d in (f"{year-1}-12-31", end, prior_end)]
        except FactConflict:
            issues.append((f"TTM {company}: conflicting source profits", refs)); continue
        if any(f is None for f in inputs):
            issues.append((f"TTM {company}: missing annual/current YTD/prior YTD input", refs)); continue
        refs = unique(e for f in inputs for e in f["evidence_ids"])
        if any(f["period_start"] != f["period_end"][:4] + "-01-01" for f in inputs):
            issues.append((f"TTM {company}: incompatible cumulative period starts", refs)); continue
        if any(f["review_status"] != "verified" or f["value"] is None for f in inputs):
            issues.append((f"TTM {company}: source profits need review", refs)); continue
        if len({f["restated"] for f in inputs}) != 1:
            issues.append((f"TTM {company}: restatement basis requires independent reconciliation", refs)); continue
        start = (date.fromisoformat(prior_end) + timedelta(days=1)).isoformat()
        params = {"company_code": company, "metric": "net_profit_parent_ttm",
                  "statement_scope": scope, "period_start": start, "period_end": end}
        for role, f in zip(("annual", "current_ytd", "prior_ytd"), inputs):
            params[role + "_period_start"] = f["period_start"]
            params[role + "_period_end"] = f["period_end"]
        calc = context.calculations.calculate("ttm_from_ytd", [f["fact_id"] for f in inputs], [], params)
        calculations.append(calc); record_calculation(context, calc)
        if calc["status"] != "verified" or calc["value"] is None or calc["unit"] != "CNY":
            issues.append((f"TTM {company}: {calc['reason'] or 'invalid calculation result'}", refs)); continue
        # This internal selector record is never registered as an original fact,
        # stored in the fact database, or emitted in a public Fact response.
        selector_id = "fact_ttm_selector_" + calc["calculation_id"].removeprefix("calc_")
        derived.append({"fact_id": selector_id, "company_code": company,
                        "metric": "net_profit_parent_ttm", "value": calc["value"], "unit": "CNY",
                        "period_start": start, "period_end": end, "period_type": "cumulative",
                        "statement_scope": scope, "restated": inputs[0]["restated"],
                        "evidence_ids": refs, "review_status": calc["status"], "reason": None})
        origins[selector_id] = calc["calculation_id"]
    return facts + derived, origins, calculations, issues
