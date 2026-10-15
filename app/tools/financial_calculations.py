"""Evidence-linked financial calculations using decimal arithmetic."""

from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any, Iterable

from app.facts import FactError, validate_fact
from app.facts.catalog import decimal_value


def _canonical_number(value: Decimal, unit: str) -> str:
    quantum = Decimal("0.01") if unit == "CNY" else Decimal("0.0001")
    with localcontext() as ctx:
        ctx.prec = 40
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return "0"
    return format(rounded.normalize(), "f")


def _same_period(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(
        a[key] == b[key]
        for key in ("company_code", "period_start", "period_end", "period_type", "statement_scope")
    )


def _month_count(start: date, end: date) -> int:
    if start.day != 1 or end.day != monthrange(end.year, end.month)[1]:
        raise ValueError("TTM inputs must cover complete calendar months")
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _ttm_metadata(facts: list[dict[str, Any]]) -> dict[str, str]:
    if len(facts) != 3:
        raise ValueError("ttm_from_ytd expects 3 facts and 0 calculations")
    annual, current, prior = facts
    if annual["metric"] not in {"net_profit_parent", "net_profit_parent_excl_nonrecurring"}:
        raise ValueError("TTM requires disclosed parent or adjusted parent net profit")
    for key in ("company_code", "metric", "unit", "statement_scope", "restated"):
        if any(f[key] != annual[key] for f in facts):
            raise ValueError(f"TTM input {key} mismatch; reconcile the reporting basis")
    if annual["unit"] != "CNY" or annual["statement_scope"] == "not_applicable":
        raise ValueError("TTM requires CNY statement profits")
    if any(f["period_type"] != "cumulative" for f in facts):
        raise ValueError("TTM requires cumulative flow inputs")
    spans = [(date.fromisoformat(f["period_start"]), date.fromisoformat(f["period_end"])) for f in facts]
    (a_start, a_end), (c_start, c_end), (p_start, p_end) = spans
    lengths = [_month_count(start, end) for start, end in spans]
    if lengths[0] != 12 or not 1 <= lengths[1] <= 12 or lengths[1] != lengths[2]:
        raise ValueError("TTM requires one annual period and corresponding YTD periods")
    if c_start != a_end + timedelta(days=1) or p_start != a_start or not a_start <= p_end <= a_end:
        raise ValueError("TTM annual and YTD periods do not align")
    if c_start.year - p_start.year != 1 or c_start.month != p_start.month:
        raise ValueError("TTM YTD starts must correspond one year apart")
    if c_end.year - p_end.year != 1 or c_end.month != p_end.month:
        raise ValueError("TTM YTD ends must correspond one year apart")
    metadata = {"company_code": annual["company_code"],
                "metric": annual["metric"] + "_ttm", "statement_scope": annual["statement_scope"],
                "restated": str(annual["restated"]).lower(),
                "period_start": (p_end + timedelta(days=1)).isoformat(), "period_end": c_end.isoformat(),
                "calendar_rule": "complete_months;prior_end_plus_one_day;month_end_leap_year"}
    for role, fact in zip(("annual", "current_ytd", "prior_ytd"), facts):
        for edge in ("period_start", "period_end"):
            metadata[role + "_" + edge] = fact[edge]
    return metadata


class FinancialCalculator:
    """CalculationPort adapter; construct with the facts for one run."""

    def __init__(self, facts: Iterable[dict[str, Any]] = ()) -> None:
        self.facts: dict[str, dict[str, Any]] = {}
        self.calculations: dict[str, dict[str, Any]] = {}
        self.register_facts(facts)

    def register_facts(self, facts: Iterable[dict[str, Any]]) -> None:
        for fact in facts:
            validate_fact(fact)
            existing = self.facts.get(fact["fact_id"])
            if existing is not None and existing != fact:
                raise FactError(f"Duplicate fact ID with different content: {fact['fact_id']}")
            self.facts[fact["fact_id"]] = deepcopy(fact)

    def calculate(
        self,
        formula_code: str,
        input_fact_ids: list[str],
        input_calculation_ids: list[str],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a Calculation, including a review result for undefined math."""
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be a mapping")
        missing_facts = [fid for fid in input_fact_ids if fid not in self.facts]
        missing_calcs = [cid for cid in input_calculation_ids if cid not in self.calculations]
        if missing_facts or missing_calcs:
            raise ValueError(f"Unknown calculation inputs: {missing_facts + missing_calcs}")
        facts = [self.facts[fid] for fid in input_fact_ids]
        calculations = [self.calculations[cid] for cid in input_calculation_ids]
        parameters = dict(parameters)
        metadata_error = None
        if formula_code == "ttm_from_ytd":
            try:
                metadata = _ttm_metadata(facts)
                if any(key in parameters and parameters[key] != value for key, value in metadata.items()):
                    raise ValueError("TTM parameters conflict with source fact metadata")
                parameters.update(metadata)
            except ValueError as exc:
                metadata_error = str(exc)
        source_ids = set(input_fact_ids)
        pending = list(input_calculation_ids)
        visited = set()
        while pending:
            cid = pending.pop()
            if cid in visited:
                continue
            visited.add(cid)
            source = self.calculations.get(cid, {})
            source_ids.update(source.get("input_fact_ids", []))
            pending.extend(source.get("input_calculation_ids", []))
        payload = {
            "formula_revision": ("finance-tools-v2:qoe-ttm-v1" if formula_code == "nonrecurring_gap_share"
                                 and input_calculation_ids else "finance-tools-v2"),
            "formula_code": formula_code,
            "input_fact_ids": input_fact_ids,
            "input_calculation_ids": input_calculation_ids,
            "input_facts": facts,
            "input_calculations": calculations,
            "source_facts": {fid: self.facts.get(fid) for fid in sorted(source_ids)},
            "parameters": parameters,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        calc_id = f"calc_{digest}"
        if calc_id in self.calculations:
            return deepcopy(self.calculations[calc_id])
        unit = "CNY" if formula_code in {
            "ttm_from_ytd", "quarter_from_ytd", "nonrecurring_gap_amount", "forecast_fcf", "discounted_fcf", "terminal_value",
            "dcf_enterprise_value", "equity_value", "relative_equity_value",
        } else "percent" if formula_code in {"yoy", "gross_margin", "nonrecurring_gap_share"} else "times"
        value: Decimal | None = None
        reason: str | None = None
        try:
            if metadata_error:
                raise ValueError(metadata_error)
            value = self._evaluate(formula_code, facts, calculations, parameters)
        except (ValueError, FactError, ArithmeticError) as exc:
            reason = str(exc)
        result = {
            "calculation_id": calc_id,
            "formula_code": formula_code,
            "input_fact_ids": input_fact_ids,
            "input_calculation_ids": input_calculation_ids,
            "parameters": {key: str(item) for key, item in parameters.items()},
            "value": _canonical_number(value, unit) if value is not None else None,
            "unit": unit,
            "rounding_rule": "ROUND_HALF_UP:0.01" if unit == "CNY" else "ROUND_HALF_UP:0.0001",
            "status": "verified" if value is not None else "needs_review",
            "reason": reason,
        }
        self.calculations[calc_id] = deepcopy(result)
        return result

    def _evaluate(
        self,
        code: str,
        facts: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> Decimal:
        if any(fact["value"] is None or fact["review_status"] != "verified" for fact in facts):
            raise ValueError("Input fact is missing or unverified")
        if any(calc["value"] is None or calc["status"] != "verified" for calc in calculations):
            raise ValueError("Input calculation is missing or unverified")
        vals = [decimal_value(fact["value"]) for fact in facts]
        cvals = [decimal_value(calc["value"]) for calc in calculations]
        assert all(value is not None for value in vals + cvals)
        with localcontext() as ctx:
            ctx.prec = 40
            if code == "ttm_from_ytd":
                self._require_counts(code, vals, cvals, 3, 0)
                _ttm_metadata(facts)
                return vals[0] + vals[1] - vals[2]
            if code == "yoy":
                self._require_counts(code, vals, cvals, 2, 0)
                current, prior = facts
                if any(current[key] != prior[key] for key in ("company_code", "metric", "unit", "period_type", "statement_scope")):
                    raise ValueError("Year-on-year input metric or scope mismatch")
                cs, ps = date.fromisoformat(current["period_start"]), date.fromisoformat(prior["period_start"])
                ce, pe = date.fromisoformat(current["period_end"]), date.fromisoformat(prior["period_end"])
                if ce.year - pe.year != 1 or (ce.month, ce.day) != (pe.month, pe.day):
                    raise ValueError("Year-on-year periods must end one year apart on the same date")
                if current["period_type"] != "instant" and (cs.month, cs.day) != (ps.month, ps.day):
                    raise ValueError("Year-on-year periods must start on the same month and day")
                if vals[1] == 0:
                    raise ValueError("Year-on-year base is zero")
                return (vals[0] / vals[1] - 1) * 100
            if code == "quarter_from_ytd":
                self._require_counts(code, vals, cvals, 2, 0)
                current, previous = facts
                if any(current[key] != previous[key] for key in ("company_code", "metric", "unit", "statement_scope")):
                    raise ValueError("Quarter calculation input metric or scope mismatch")
                if current["period_type"] != "cumulative" or previous["period_type"] != "cumulative":
                    raise ValueError("Quarter calculation requires cumulative flow inputs")
                if current["period_start"] != previous["period_start"]:
                    raise ValueError("Quarter calculation requires the same cumulative start")
                ce, pe = date.fromisoformat(current["period_end"]), date.fromisoformat(previous["period_end"])
                start = date.fromisoformat(current["period_start"])
                quarter_ends = {(3, 31): 1, (6, 30): 2, (9, 30): 3, (12, 31): 4}
                if (
                    (start.month, start.day) != (1, 1)
                    or start.year != ce.year
                    or ce.year != pe.year
                    or quarter_ends.get((ce.month, ce.day), 0)
                    - quarter_ends.get((pe.month, pe.day), 0) != 1
                ):
                    raise ValueError("Quarter calculation requires adjacent quarters in one year")
                return vals[0] - vals[1]
            if code == "cash_conversion":
                self._require_counts(code, vals, cvals, 2, 0)
                if [f["metric"] for f in facts] != ["operating_cash_flow", "net_profit_parent"] or not _same_period(*facts):
                    raise ValueError("Cash conversion requires same-period operating cash flow and parent net profit")
                if vals[1] == 0:
                    raise ValueError("Net profit is zero")
                return vals[0] / vals[1]
            if code == "gross_margin":
                self._require_counts(code, vals, cvals, 2, 0)
                if [f["metric"] for f in facts] != ["revenue", "cost_of_revenue"] or not _same_period(*facts):
                    raise ValueError("Gross margin requires same-period revenue and cost")
                if vals[0] == 0:
                    raise ValueError("Revenue is zero")
                return (vals[0] - vals[1]) / vals[0] * 100
            if code in {"nonrecurring_gap_share", "nonrecurring_gap_amount"}:
                if calculations:
                    self._require_counts(code, vals, cvals, 0, 2)
                    parent = self._profit_calculation(calculations[0])
                    adjusted = self._profit_calculation(calculations[1], "net_profit_parent_excl_nonrecurring_ttm")
                    if any(parent[key] != adjusted[key] for key in
                           ("company_code", "period_start", "period_end", "statement_scope", "restated")):
                        raise ValueError("TTM quality comparison requires the same company, period and basis")
                    self._check_profit_context(parent, params)
                    if code == "nonrecurring_gap_amount":
                        return cvals[0] - cvals[1]
                    if cvals[0] == 0:
                        raise ValueError("Parent TTM profit is zero")
                    return abs(cvals[0] - cvals[1]) / abs(cvals[0]) * 100
                self._require_counts(code, vals, cvals, 2, 0)
                if ([f["metric"] for f in facts] != ["net_profit_parent", "net_profit_parent_excl_nonrecurring"]
                        or not _same_period(*facts) or facts[0]["restated"] != facts[1]["restated"]):
                    raise ValueError("Nonrecurring gap requires same-period parent and adjusted parent net profit")
                if code == "nonrecurring_gap_amount":
                    return vals[0] - vals[1]
                if vals[0] == 0:
                    raise ValueError("Parent net profit is zero")
                return abs(vals[0] - vals[1]) / abs(vals[0]) * 100
            if code == "pe":
                if calculations:
                    self._require_counts(code, vals, cvals, 1, 1)
                    profit = self._profit_calculation(calculations[0])
                    market_cap, profit_value = facts[0], cvals[0]
                else:
                    self._require_counts(code, vals, cvals, 2, 0)
                    market_cap, profit = facts
                    profit_value = vals[1]
                    if profit["metric"] != "net_profit_parent_ttm":
                        raise ValueError("PE requires TTM parent net profit")
                if market_cap["metric"] != "market_cap" or market_cap["unit"] != "CNY":
                    raise ValueError("PE requires a CNY market cap fact")
                if market_cap["company_code"] != profit["company_code"]:
                    raise ValueError("PE inputs refer to different companies")
                if profit_value <= 0 or vals[0] <= 0:
                    raise ValueError("PE is undefined for non-positive market cap or profit")
                if market_cap["period_end"] != params.get("as_of_date"):
                    raise ValueError("Market cap snapshot date differs from valuation date")
                if profit["period_end"] > market_cap["period_end"]:
                    raise ValueError("TTM earnings are newer than valuation date")
                self._check_profit_context(profit, params)
                return vals[0] / profit_value
            if code == "relative_equity_value":
                if calculations:
                    self._require_counts(code, vals, cvals, 0, 1)
                    profit = self._profit_calculation(calculations[0])
                    profit_value = cvals[0]
                else:
                    self._require_counts(code, vals, cvals, 1, 0)
                    profit = facts[0]
                    profit_value = vals[0]
                if profit["metric"] != "net_profit_parent_ttm" or profit_value <= 0:
                    raise ValueError("Relative valuation requires positive target TTM earnings")
                self._check_profit_context(profit, params)
                multiple = self._param(params, "multiple")
                shock = self._param(params, "earnings_shock_percent") / 100 if "earnings_shock_percent" in params else Decimal("0")
                if multiple <= 0:
                    raise ValueError("Selected PE multiple must be positive")
                if shock <= -1:
                    raise ValueError("Earnings shock cannot reduce earnings to zero or below")
                return profit_value * (1 + shock) * multiple
            if code == "forecast_fcf":
                self._require_counts(code, vals, cvals, 1, 0)
                if facts[0]["metric"] != "revenue" or vals[0] < 0:
                    raise ValueError("Forecast requires non-negative base revenue")
                year = int(params.get("year", 0))
                if year < 1 or year > 10:
                    raise ValueError("Forecast year must be between 1 and 10")
                growth = self._param(params, "revenue_growth") / 100
                margin = self._param(params, "operating_margin") / 100
                tax = self._param(params, "tax_rate") / 100
                da = self._param(params, "da_pct") / 100
                capex = self._param(params, "capex_pct") / 100
                nwc = self._param(params, "nwc_pct") / 100
                if (
                    growth <= -1
                    or not (Decimal("0") <= tax < 1)
                    or not (Decimal("0") <= margin <= 1)
                    or any(not (Decimal("0") <= rate <= 1) for rate in (da, capex, nwc))
                ):
                    raise ValueError("Forecast rate assumption is out of range")
                revenue = vals[0] * (1 + growth) ** year
                return revenue * (margin * (1 - tax) + da - capex - nwc)
            if code == "discounted_fcf":
                self._require_counts(code, vals, cvals, 0, 1)
                year = int(params.get("year", 0))
                wacc = self._param(params, "wacc") / 100
                if year < 1 or wacc <= 0:
                    raise ValueError("Discount year or WACC is invalid")
                return cvals[0] / (1 + wacc) ** year
            if code == "terminal_value":
                self._require_counts(code, vals, cvals, 0, 1)
                year = int(params.get("year", 0))
                wacc = self._param(params, "wacc") / 100
                growth = self._param(params, "terminal_growth") / 100
                if year < 1 or wacc <= growth or growth <= -1:
                    raise ValueError("Terminal growth must be above -100% and below WACC")
                terminal = cvals[0] * (1 + growth) / (wacc - growth)
                return terminal / (1 + wacc) ** year
            if code == "dcf_enterprise_value":
                if vals or len(cvals) < 2:
                    raise ValueError("DCF enterprise value requires discounted FCF and terminal value")
                return sum(cvals, Decimal("0"))
            if code == "equity_value":
                self._require_counts(code, vals, cvals, 0, 1)
                equity = cvals[0] - self._param(params, "net_debt")
                if equity < 0:
                    raise ValueError("Implied equity value is below zero")
                return equity
        raise ValueError(f"Unsupported formula_code: {code}")

    def _profit_calculation(self, calc: dict[str, Any], expected_metric: str = "net_profit_parent_ttm") -> dict[str, str]:
        if calc["formula_code"] != "ttm_from_ytd" or calc["unit"] != "CNY" or calc["input_calculation_ids"]:
            raise ValueError("PE requires a sourced TTM profit calculation")
        try:
            facts = [self.facts[fid] for fid in calc["input_fact_ids"]]
        except KeyError as exc:
            raise ValueError("TTM source fact is unavailable") from exc
        metadata = _ttm_metadata(facts)
        if metadata["metric"] != expected_metric:
            raise ValueError("PE cannot substitute adjusted profit for parent profit")
        if any(f["review_status"] != "verified" or f["value"] is None for f in facts):
            raise ValueError("TTM source facts are missing or unverified")
        if any(calc["parameters"].get(key) != value for key, value in metadata.items()):
            raise ValueError("TTM calculation metadata differs from source facts")
        with localcontext() as ctx:
            ctx.prec = 40
            expected = Decimal(facts[0]["value"]) + Decimal(facts[1]["value"]) - Decimal(facts[2]["value"])
            if calc["value"] != _canonical_number(expected, "CNY"):
                raise ValueError("TTM calculation differs from source arithmetic")
        return metadata

    @staticmethod
    def _check_profit_context(profit: dict[str, Any], params: dict[str, Any]) -> None:
        for key in ("company_code", "statement_scope", "period_start", "period_end"):
            if key in params and params[key] != profit[key]:
                raise ValueError(f"Valuation {key} differs from TTM earnings")
        if "as_of_date" in params and date.fromisoformat(profit["period_end"]) > date.fromisoformat(params["as_of_date"]):
            raise ValueError("TTM earnings are newer than valuation date")

    @staticmethod
    def _require_counts(
        code: str,
        facts: list[Decimal | None],
        calculations: list[Decimal | None],
        fact_count: int,
        calculation_count: int,
    ) -> None:
        if len(facts) != fact_count or len(calculations) != calculation_count:
            raise ValueError(
                f"{code} expects {fact_count} facts and {calculation_count} calculations"
            )

    @staticmethod
    def _param(params: dict[str, Any], name: str) -> Decimal:
        if name not in params:
            raise ValueError(f"Missing assumption: {name}")
        result = decimal_value(str(params[name]))
        if result is None:
            raise ValueError(f"Missing assumption: {name}")
        return result
