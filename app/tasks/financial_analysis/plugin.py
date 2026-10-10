"""Evidence-backed financial report analysis plugin."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.facts import FactConflict, FactIndex, METRICS
from app.facts.catalog import decimal_value
from app.tasks._finance_common import (
    prime_calculator,
    record_calculation,
    review_item,
    source_ids_for_facts,
    unique,
)
from contracts.ports import TaskContext


CORE_METRICS = (
    "revenue",
    "net_profit_parent",
    "net_profit_parent_excl_nonrecurring",
    "operating_cash_flow",
)


class FinancialAnalysisTask:
    task_type = "financial_analysis"

    def run(self, context: TaskContext, task_input: dict[str, Any]) -> dict[str, Any]:
        company = task_input["company_code"]
        scope = task_input["statement_scope"]
        periods = sorted(set(task_input["periods"]), reverse=True)
        extracted = context.evidence.extract_facts(list(context.document_ids), list(METRICS))
        prime_calculator(context, extracted)
        index = FactIndex(extracted)
        chosen: list[dict[str, Any]] = []
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        reviews: list[dict[str, Any]] = []
        warnings: list[str] = []

        for period in periods:
            for metric in METRICS:
                if METRICS[metric].kind == "market":
                    continue
                try:
                    fact = index.select(company, metric, period, scope)
                except FactConflict as exc:
                    reviews.append(review_item(context.run_id, str(exc)))
                    continue
                if fact is not None:
                    chosen.append(fact)
                    selected[(period, metric)] = fact
            for metric in CORE_METRICS:
                if (period, metric) not in selected:
                    reviews.append(
                        review_item(context.run_id, f"Missing {metric} for {period} ({scope})")
                    )

        evidence_ids = source_ids_for_facts(context, chosen)
        calculations: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        yoy_by_period_metric: dict[tuple[str, str], dict[str, Any]] = {}

        def add_calc(code: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
            calc = context.calculations.calculate(
                code, [fact["fact_id"] for fact in facts], [], {}
            )
            calculations.append(calc)
            record_calculation(context, calc)
            if calc["status"] != "verified":
                reviews.append(
                    review_item(context.run_id, f"{code}: {calc['reason']}",
                                [ev for fact in facts for ev in fact["evidence_ids"]])
                )
            return calc

        for period in periods:
            revenue = selected.get((period, "revenue"))
            cost = selected.get((period, "cost_of_revenue"))
            ocf = selected.get((period, "operating_cash_flow"))
            profit = selected.get((period, "net_profit_parent"))
            if revenue and cost:
                add_calc("gross_margin", [revenue, cost])
            if ocf and profit:
                add_calc("cash_conversion", [ocf, profit])
                op_value = decimal_value(ocf["value"])
                profit_value = decimal_value(profit["value"])
                if op_value is not None and profit_value is not None and profit_value > 0 and op_value < 0:
                    alerts.append({
                        "alert_code": "profit_cash_divergence",
                        "severity": "high",
                        "description": f"{period} 归母净利润为正而经营现金流净额为负；需结合附注核查原因。",
                        "fact_ids": [profit["fact_id"], ocf["fact_id"]],
                        "evidence_ids": unique(profit["evidence_ids"] + ocf["evidence_ids"]),
                        "interpretation_status": "unresolved",
                    })
            adjusted_profit = selected.get((period, "net_profit_parent_excl_nonrecurring"))
            if profit and adjusted_profit:
                gap = add_calc("nonrecurring_gap_share", [profit, adjusted_profit])
                if gap["status"] == "verified" and decimal_value(gap["value"]) >= 30:
                    alerts.append({
                        "alert_code": "large_parent_adjusted_profit_gap",
                        "severity": "medium",
                        "description": f"{period} 归母净利润与扣非归母净利润差额占归母净利润绝对值较高；需结合非经常性损益明细核查。",
                        "fact_ids": [profit["fact_id"], adjusted_profit["fact_id"]],
                        "evidence_ids": unique(profit["evidence_ids"] + adjusted_profit["evidence_ids"]),
                        "interpretation_status": "unresolved",
                    })

        for current_period, prior_period in zip(periods, periods[1:]):
            current_date = date.fromisoformat(current_period)
            prior_date = date.fromisoformat(prior_period)
            if current_date.year - prior_date.year != 1 or (
                current_date.month, current_date.day
            ) != (prior_date.month, prior_date.day):
                warnings.append(
                    f"Skipped year-on-year calculation for nonmatching periods {current_period}/{prior_period}"
                )
                continue
            for metric in METRICS:
                current = selected.get((current_period, metric))
                prior = selected.get((prior_period, metric))
                if current and prior:
                    yoy_by_period_metric[(current_period, metric)] = add_calc("yoy", [current, prior])
            revenue_yoy = yoy_by_period_metric.get((current_period, "revenue"))
            for metric in ("accounts_receivable", "inventory"):
                working_capital_yoy = yoy_by_period_metric.get((current_period, metric))
                if (
                    revenue_yoy and revenue_yoy["status"] == "verified"
                    and working_capital_yoy and working_capital_yoy["status"] == "verified"
                    and decimal_value(working_capital_yoy["value"]) - decimal_value(revenue_yoy["value"]) >= 20
                ):
                    current = selected[(current_period, metric)]
                    prior = selected[(prior_period, metric)]
                    revenue_current = selected[(current_period, "revenue")]
                    revenue_prior = selected[(prior_period, "revenue")]
                    sources = unique(
                        current["evidence_ids"] + prior["evidence_ids"]
                        + revenue_current["evidence_ids"] + revenue_prior["evidence_ids"]
                    )
                    alerts.append({
                        "alert_code": f"{metric}_outpaces_revenue",
                        "severity": "medium",
                        "description": f"{current_period} {METRICS[metric].label}同比增速比营业收入高至少 20 个百分点；需核查附注及业务变化。",
                        "fact_ids": [current["fact_id"], prior["fact_id"], revenue_current["fact_id"], revenue_prior["fact_id"]],
                        "evidence_ids": sources,
                        "interpretation_status": "unresolved",
                    })

        if not chosen:
            warnings.append("No verified facts matched the requested company, scope and periods")
        return {
            "data": {"facts": chosen, "alerts": alerts},
            "evidence_ids": evidence_ids,
            "calculations": calculations,
            "review_items": reviews,
            "warnings": warnings,
        }
