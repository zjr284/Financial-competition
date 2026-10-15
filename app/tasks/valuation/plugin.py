"""Relative PE and DCF valuation with explicit assumptions and provenance."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.facts import FactConflict, FactIndex
from app.facts.catalog import decimal_value
from app.tasks._finance_common import (
    prime_calculator,
    record_calculation,
    review_item,
    source_ids_for_facts,
    unique,
)
from contracts.ports import TaskContext
from .ttm_inputs import prepare_ttm_inputs
from .earnings_quality import target_earnings_quality
from app.tools.market_sources import is_historical_market_observation


DCF_REQUIRED = (
    "revenue_growth", "operating_margin", "tax_rate", "da_pct",
    "capex_pct", "nwc_pct", "wacc", "terminal_growth", "net_debt",
)


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def _full_year(fact: dict[str, Any]) -> bool:
    start, end = date.fromisoformat(fact["period_start"]), date.fromisoformat(fact["period_end"])
    return (
        fact["period_type"] == "cumulative"
        and start.year == end.year
        and (start.month, start.day) == (1, 1)
        and (end.month, end.day) == (12, 31)
    )


def _ttm(fact: dict[str, Any]) -> bool:
    start, end = date.fromisoformat(fact["period_start"]), date.fromisoformat(fact["period_end"])
    prior_month_days = monthrange(end.year - 1, end.month)[1]
    prior_day = (prior_month_days if end.day == monthrange(end.year, end.month)[1]
                 else min(end.day, prior_month_days))
    prior_year_end = end.replace(year=end.year - 1, day=prior_day)
    return fact["period_type"] == "cumulative" and start == prior_year_end + timedelta(days=1)


class ValuationTask:
    task_type = "valuation"

    def run(self, context: TaskContext, task_input: dict[str, Any]) -> dict[str, Any]:
        method = task_input["method"]
        if method not in {"relative_pe", "dcf"}:
            raise ValueError(f"Unsupported valuation method: {method}")
        metrics = ["market_cap", "net_profit_parent_ttm", "net_profit_parent", "net_profit_parent_excl_nonrecurring"] if method == "relative_pe" else ["revenue"]
        facts = context.evidence.extract_facts(list(context.document_ids), metrics)
        available, excluded = self._available_facts(context, facts, task_input["as_of_date"])
        payload = (self._relative_pe(context, task_input, available) if method == "relative_pe"
                   else self._dcf(context, task_input, available))
        observed_docs = set()
        for fact in available:
            for eid in fact["evidence_ids"]:
                ev = context.evidence.get_evidence(eid)
                doc = context.documents.get_document(ev["document_id"])
                if ev["document_id"] not in observed_docs and is_historical_market_observation(fact, ev, doc, task_input["as_of_date"]):
                    observed_docs.add(ev["document_id"])
                    note = "Historical market observation: trading date matches valuation date; first publication and original point-in-time version are not certified"
                    payload["warnings"].append(note)
                    payload["review_items"].append(review_item(context.run_id, note, [eid]))
        for fact, reason in excluded:
            message = f"Excluded {fact['fact_id']}: {reason}"
            payload["warnings"].append(message)
            payload["review_items"].append(review_item(context.run_id, message, fact["evidence_ids"]))
            payload["evidence_ids"] = unique(payload["evidence_ids"] + fact["evidence_ids"])
        return payload

    @staticmethod
    def _available_facts(
        context: TaskContext, facts: list[dict[str, Any]], as_of_date: str,
    ) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
        """Use only facts whose complete source chain was disclosed by the cutoff."""
        available, excluded = [], []
        cutoff = date.fromisoformat(as_of_date)
        documents: dict[str, dict[str, Any]] = {}
        for fact in facts:
            if fact["review_status"] == "rejected":
                continue
            reason = None
            if not fact["evidence_ids"]:
                reason = "Missing source evidence"
            for evidence_id in fact["evidence_ids"]:
                evidence = context.evidence.get_evidence(evidence_id)
                document_id = evidence["document_id"]
                if document_id not in context.document_ids:
                    raise ValueError(f"Evidence {evidence_id} is outside this run's document snapshot")
                if document_id not in documents:
                    documents[document_id] = context.documents.get_document(document_id)
                if is_historical_market_observation(fact, evidence, documents[document_id], as_of_date):
                    continue
                disclosed = documents[document_id].get("disclosure_date")
                try:
                    disclosed_date = date.fromisoformat(disclosed) if disclosed else None
                except (ValueError, TypeError):
                    disclosed_date = None
                if disclosed_date is None:
                    reason = "Missing or invalid source disclosure date"
                elif disclosed_date > cutoff:
                    reason = "Source was disclosed after valuation as_of_date"
            if reason:
                excluded.append((fact, reason))
            else:
                available.append(fact)
        return available, excluded

    @staticmethod
    def _empty(
        context: TaskContext,
        task_input: dict[str, Any],
        reason: str,
        evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        refs = evidence_ids or []
        return {
            "data": {
                "method": task_input["method"],
                "as_of_date": task_input["as_of_date"],
                "assumptions": [],
                "valuation_range": {
                    "low": None, "base": None, "high": None, "unit": "CNY", "reason": reason,
                },
                "sensitivity": [],
            },
            "evidence_ids": refs,
            "calculations": [],
            "review_items": [review_item(context.run_id, reason, refs)],
            "warnings": [reason],
        }

    def _relative_pe(
        self, context: TaskContext, task_input: dict[str, Any], facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        as_of = task_input["as_of_date"]
        scope = task_input["statement_scope"]
        target = task_input["company_code"]
        prime_calculator(context, facts)
        facts, origins, upstream, issues = prepare_ttm_inputs(context, facts, scope, as_of)
        index = FactIndex(facts)

        computed: list[dict[str, Any]] = []
        source_facts = {f["fact_id"]: f for f in facts}

        def profit_inputs(profit: dict[str, Any]) -> tuple[list[str], list[str]]:
            origin = origins.get(profit["fact_id"])
            return ([], [origin]) if origin else ([profit["fact_id"]], [])

        def finish(payload: dict[str, Any]) -> dict[str, Any]:
            payload["calculations"] = list({c["calculation_id"]: c for c in upstream + computed + payload["calculations"]}.values())
            payload["evidence_ids"] = unique(payload["evidence_ids"] + [
                evidence_id for calc in upstream for fid in calc["input_fact_ids"]
                for evidence_id in source_facts[fid]["evidence_ids"]
            ])
            for reason, refs in issues:
                payload["warnings"].append(reason)
                payload["review_items"].append(review_item(context.run_id, reason, refs))
                payload["evidence_ids"] = unique(payload["evidence_ids"] + refs)
            return payload

        def empty(reason: str, refs: list[str] | None = None) -> dict[str, Any]:
            return finish(self._empty(context, task_input, reason, refs))

        relevant = [
            fact for fact in index.all()
            if fact["metric"] == "net_profit_parent_ttm"
            and fact["statement_scope"] == scope
            and fact["period_end"] <= as_of
            and (date.fromisoformat(as_of) - date.fromisoformat(fact["period_end"])).days <= 200
            and _ttm(fact)
        ]
        by_company: dict[str, dict[str, Any]] = {}
        for company in sorted({fact["company_code"] for fact in relevant}):
            latest_period = max(
                fact["period_end"] for fact in relevant if fact["company_code"] == company
            )
            try:
                selected = index.select(
                    company, "net_profit_parent_ttm", latest_period, scope, "cumulative"
                )
            except FactConflict:
                if company == target:
                    return empty("Conflicting target TTM earnings")
                continue
            if selected is not None:
                by_company[company] = selected
        target_profit = by_company.get(target)
        if target_profit is None or target_profit["review_status"] != "verified" or decimal_value(target_profit["value"]) is None:
            return empty("Missing recent target TTM parent net profit")
        if decimal_value(target_profit["value"]) <= 0:
            return empty("Target TTM earnings are non-positive", target_profit["evidence_ids"])

        quality_calculations, quality_issues = target_earnings_quality(
            context, index, facts, target_profit, origins, upstream,
        )
        upstream.extend(quality_calculations)
        issues.extend(quality_issues)
        peer_inputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for company, profit in by_company.items():
            if company == target or profit["review_status"] != "verified":
                continue
            if (profit["period_start"], profit["period_end"]) != (target_profit["period_start"], target_profit["period_end"]):
                continue
            try:
                market = index.select(company, "market_cap", as_of, "not_applicable", "instant")
            except FactConflict:
                refs = unique(e for f in facts if f["company_code"] == company and f["metric"] == "market_cap"
                              for e in f["evidence_ids"])
                issues.append((f"Market snapshot {company}: conflicting same-date market caps", refs))
                continue
            if market is None:
                issues.append((f"Market snapshot {company}: missing valuation-date market cap", []))
            elif market["review_status"] != "verified":
                issues.append((f"Market snapshot {company}: raw market cap needs review", market["evidence_ids"]))
            else:
                peer_inputs.append((market, profit))
        if len(peer_inputs) < 3:
            return empty(
                "At least three peers with same-date market caps and TTM profits matching the target period are required",
                target_profit["evidence_ids"],
            )
        chosen = [target_profit] + [item for pair in peer_inputs for item in pair]
        evidence_ids = source_ids_for_facts(context, chosen)
        calculations: list[dict[str, Any]] = computed
        multiples: list[Decimal] = []
        for market, profit in peer_inputs:
            profit_fact_ids, profit_calc_ids = profit_inputs(profit)
            calc = context.calculations.calculate(
                "pe", [market["fact_id"]] + profit_fact_ids, profit_calc_ids,
                {"as_of_date": as_of, "company_code": market["company_code"], "statement_scope": scope}
            )
            calculations.append(calc)
            record_calculation(context, calc)
            if calc["status"] == "verified":
                multiples.append(Decimal(calc["value"]))
        if len(multiples) < 3:
            return empty("Fewer than three valid positive peer PE values", evidence_ids)
        multiples.sort()
        low_multiple = multiples[0]
        high_multiple = multiples[-1]
        middle = len(multiples) // 2
        base_multiple = (
            multiples[middle] if len(multiples) % 2
            else (multiples[middle - 1] + multiples[middle]) / 2
        )
        values: dict[str, str] = {}
        target_fact_ids, target_calc_ids = profit_inputs(target_profit)
        target_context = {"company_code": target, "statement_scope": scope,
                          "period_start": target_profit["period_start"],
                          "period_end": target_profit["period_end"], "as_of_date": as_of}
        for name, multiple in (
            ("low", low_multiple), ("base", base_multiple), ("high", high_multiple)
        ):
            calc = context.calculations.calculate(
                "relative_equity_value", target_fact_ids, target_calc_ids,
                {**target_context, "multiple": _number(multiple), "valuation_scenario": name},
            )
            calculations.append(calc)
            record_calculation(context, calc)
            if calc["status"] != "verified":
                return empty(calc["reason"], evidence_ids)
            values[name] = calc["value"]
        sensitivity: list[dict[str, Any]] = []
        for multiple in (low_multiple, base_multiple, high_multiple):
            for shock in (Decimal("-10"), Decimal("0"), Decimal("10")):
                calc = context.calculations.calculate(
                    "relative_equity_value", target_fact_ids, target_calc_ids,
                    {**target_context, "multiple": _number(multiple), "earnings_shock_percent": _number(shock)},
                )
                calculations.append(calc)
                record_calculation(context, calc)
                sensitivity.append({
                    "parameter_a": "peer_pe_multiple",
                    "value_a": _number(multiple),
                    "parameter_b": "earnings_shock_percent",
                    "value_b": _number(shock),
                    "valuation": calc["value"],
                    "unit": "CNY",
                })
        return finish({
            "data": {
                "method": "relative_pe",
                "as_of_date": as_of,
                "assumptions": [{
                    "text": f"以 {len(multiples)} 家可比公司同日市值和近期滚动净利润计算 PE；取最小值、中位数和最大值形成情景。",
                    "kind": "calculation",
                    "evidence_ids": evidence_ids,
                    "assumptions": ["可比公司由输入资料包确定", "盈利口径保持一致"],
                    "limitations": ["极端样本可能扩大区间；需人工审查可比性"],
                }],
                "valuation_range": {**values, "unit": "CNY", "reason": None},
                "sensitivity": sensitivity,
            },
            "evidence_ids": evidence_ids,
            "calculations": list({item["calculation_id"]: item for item in calculations}.values()),
            "review_items": [review_item(context.run_id, "Review peer comparability and market snapshot date", evidence_ids)],
            "warnings": ["区间为企业股权总价值（人民币元），不是每股价格或投资建议"],
        })

    def _dcf(self, context: TaskContext, task_input: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
        overrides = task_input.get("assumption_overrides", {})
        missing = [key for key in DCF_REQUIRED if key not in overrides]
        if missing:
            return self._empty(
                context, task_input,
                "Missing explicit DCF assumptions: " + ", ".join(missing),
            )
        try:
            assumptions = {key: Decimal(str(overrides[key])) for key in DCF_REQUIRED}
            forecast_years = int(overrides.get("forecast_years", "5"))
        except (ValueError, ArithmeticError):
            return self._empty(context, task_input, "Invalid DCF assumption value")
        if not all(value.is_finite() for value in assumptions.values()):
            return self._empty(context, task_input, "DCF assumptions must be finite numbers")
        if forecast_years < 1 or forecast_years > 10:
            return self._empty(context, task_input, "forecast_years must be between 1 and 10")
        if (
            assumptions["wacc"] <= 0
            or assumptions["terminal_growth"] <= -100
            or assumptions["revenue_growth"] <= -100
            or any(not (0 <= assumptions[key] <= 100) for key in (
                "operating_margin", "da_pct", "capex_pct", "nwc_pct"
            ))
            or not (0 <= assumptions["tax_rate"] < 100)
        ):
            return self._empty(context, task_input, "DCF rate assumption is out of range")
        if assumptions["wacc"] - 1 <= assumptions["terminal_growth"] + Decimal("0.5"):
            return self._empty(context, task_input, "Sensitivity grid requires WACC - 1% above terminal growth + 0.5%")

        prime_calculator(context, facts)
        index = FactIndex(facts)
        candidates = [
            fact for fact in index.all()
            if fact["company_code"] == task_input["company_code"]
            and fact["statement_scope"] == task_input["statement_scope"]
            and fact["period_end"] <= task_input["as_of_date"]
            and fact["review_status"] == "verified"
            and _full_year(fact)
        ]
        if not candidates:
            return self._empty(context, task_input, "Missing verified full-year base revenue")
        latest_period = max(item["period_end"] for item in candidates)
        try:
            base_revenue = index.select(
                task_input["company_code"], "revenue", latest_period,
                task_input["statement_scope"], "cumulative",
            )
        except FactConflict:
            return self._empty(context, task_input, "Conflicting full-year base revenue")
        assert base_revenue is not None
        if base_revenue["review_status"] != "verified":
            return self._empty(context, task_input, "Base annual revenue is unverified", base_revenue["evidence_ids"])
        if (date.fromisoformat(task_input["as_of_date"]) - date.fromisoformat(latest_period)).days > 540:
            return self._empty(context, task_input, "Base annual revenue is more than 540 days old", base_revenue["evidence_ids"])
        evidence_ids = source_ids_for_facts(context, [base_revenue])
        calculations: dict[str, dict[str, Any]] = {}

        def calculate(code: str, fact_ids: list[str], calc_ids: list[str], params: dict[str, str]) -> dict[str, Any]:
            calc = context.calculations.calculate(code, fact_ids, calc_ids, params)
            if calc["calculation_id"] not in calculations:
                calculations[calc["calculation_id"]] = calc
                record_calculation(context, calc)
            return calc

        def scenario(wacc: Decimal, growth: Decimal) -> dict[str, Any] | None:
            discounted: list[str] = []
            last_fcf: dict[str, Any] | None = None
            for year in range(1, forecast_years + 1):
                params = {key: _number(assumptions[key]) for key in (
                    "revenue_growth", "operating_margin", "tax_rate", "da_pct", "capex_pct", "nwc_pct"
                )}
                params["year"] = str(year)
                fcf = calculate("forecast_fcf", [base_revenue["fact_id"]], [], params)
                if fcf["status"] != "verified":
                    return None
                last_fcf = fcf
                pv = calculate("discounted_fcf", [], [fcf["calculation_id"]],
                               {"year": str(year), "wacc": _number(wacc)})
                if pv["status"] != "verified":
                    return None
                discounted.append(pv["calculation_id"])
            assert last_fcf is not None
            terminal = calculate("terminal_value", [], [last_fcf["calculation_id"]], {
                "year": str(forecast_years), "wacc": _number(wacc),
                "terminal_growth": _number(growth),
            })
            if terminal["status"] != "verified":
                return None
            enterprise = calculate("dcf_enterprise_value", [], discounted + [terminal["calculation_id"]], {})
            if enterprise["status"] != "verified":
                return None
            equity = calculate("equity_value", [], [enterprise["calculation_id"]],
                               {"net_debt": _number(assumptions["net_debt"])})
            return equity if equity["status"] == "verified" else None

        sensitivity: list[dict[str, Any]] = []
        base_value: str | None = None
        for wacc in (assumptions["wacc"] - 1, assumptions["wacc"], assumptions["wacc"] + 1):
            for growth in (
                assumptions["terminal_growth"] - Decimal("0.5"),
                assumptions["terminal_growth"],
                assumptions["terminal_growth"] + Decimal("0.5"),
            ):
                equity = scenario(wacc, growth)
                if equity is None:
                    return self._empty(context, task_input, "DCF scenario could not be calculated", evidence_ids)
                if wacc == assumptions["wacc"] and growth == assumptions["terminal_growth"]:
                    base_value = equity["value"]
                sensitivity.append({
                    "parameter_a": "wacc_percent", "value_a": _number(wacc),
                    "parameter_b": "terminal_growth_percent", "value_b": _number(growth),
                    "valuation": equity["value"], "unit": "CNY",
                })
        values = [Decimal(row["valuation"]) for row in sensitivity]
        return {
            "data": {
                "method": "dcf", "as_of_date": task_input["as_of_date"],
                "assumptions": [
                    {
                        "text": f"基期营业收入来自截至 {base_revenue['period_end']} 的全年{'合并' if base_revenue['statement_scope'] == 'consolidated' else '母公司'}报表。",
                        "kind": "fact", "evidence_ids": evidence_ids,
                        "assumptions": [], "limitations": [],
                    },
                    {
                        "text": "收入增长、利润率、税率、折现率、永续增长率和净债务由使用者显式输入。",
                        "kind": "opinion", "evidence_ids": [],
                        "assumptions": [f"{key}={_number(value)}" for key, value in assumptions.items()],
                        "limitations": ["输入假设未经原始披露自动证实", "采用简化自由现金流模型"],
                    },
                ],
                "valuation_range": {
                    "low": _number(min(values)), "base": base_value,
                    "high": _number(max(values)), "unit": "CNY", "reason": None,
                },
                "sensitivity": sensitivity,
            },
            "evidence_ids": evidence_ids,
            "calculations": list(calculations.values()),
            "review_items": [review_item(context.run_id, "Review DCF assumptions and simplified cash-flow model", evidence_ids)],
            "warnings": ["股权价值为人民币元；敏感性区间依赖假设，不构成投资建议"],
        }
