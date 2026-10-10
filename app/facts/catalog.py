"""Canonical metric definitions and evidence-backed fact indexing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


@dataclass(frozen=True)
class MetricSpec:
    code: str
    label: str
    kind: str  # flow, stock, or market
    unit: str


METRICS: dict[str, MetricSpec] = {
    "revenue": MetricSpec("revenue", "营业收入", "flow", "CNY"),
    "cost_of_revenue": MetricSpec("cost_of_revenue", "营业成本", "flow", "CNY"),
    "net_profit_parent": MetricSpec("net_profit_parent", "归母净利润", "flow", "CNY"),
    "net_profit_parent_excl_nonrecurring": MetricSpec(
        "net_profit_parent_excl_nonrecurring", "扣非归母净利润", "flow", "CNY"
    ),
    "operating_cash_flow": MetricSpec(
        "operating_cash_flow", "经营活动现金流净额", "flow", "CNY"
    ),
    "total_assets": MetricSpec("total_assets", "总资产", "stock", "CNY"),
    "total_liabilities": MetricSpec("total_liabilities", "总负债", "stock", "CNY"),
    "accounts_receivable": MetricSpec(
        "accounts_receivable", "应收账款", "stock", "CNY"
    ),
    "inventory": MetricSpec("inventory", "存货", "stock", "CNY"),
    "market_cap": MetricSpec("market_cap", "总市值", "market", "CNY"),
    "net_profit_parent_ttm": MetricSpec(
        "net_profit_parent_ttm", "滚动十二个月归母净利润", "flow", "CNY"
    ),
}


class FactError(ValueError):
    """A fact is malformed or lacks required provenance."""


class FactConflict(FactError):
    """Two plausible facts claim the same metric and period with different values."""


def decimal_value(value: str | None) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FactError("Fact.value must be a decimal string or null")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise FactError(f"Invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise FactError("Fact.value must be finite")
    return result


def validate_fact(fact: dict[str, Any]) -> None:
    """Check semantic invariants not expressible in the shared JSON Schema."""
    required = {
        "fact_id", "company_code", "metric", "value", "unit", "period_start",
        "period_end", "period_type", "statement_scope", "restated",
        "evidence_ids", "review_status", "reason",
    }
    missing = required - fact.keys()
    if missing:
        raise FactError(f"Missing Fact fields: {sorted(missing)}")
    if not str(fact["fact_id"]).startswith("fact_"):
        raise FactError("fact_id must start with fact_")
    if len(str(fact["company_code"])) != 6 or not str(fact["company_code"]).isdigit():
        raise FactError("company_code must have six digits")
    metric = fact["metric"]
    if metric not in METRICS:
        raise FactError(f"Unknown metric code: {metric}")
    spec = METRICS[metric]
    if fact["unit"] != spec.unit:
        raise FactError(f"Metric {metric} must use {spec.unit}")
    if fact["statement_scope"] not in {"consolidated", "parent", "not_applicable"}:
        raise FactError("Invalid statement_scope")
    if spec.kind != "market" and fact["statement_scope"] == "not_applicable":
        raise FactError("Financial statement facts require consolidated or parent scope")
    if spec.kind == "market" and fact["statement_scope"] != "not_applicable":
        raise FactError("Market facts require not_applicable scope")
    if fact["period_type"] not in {"instant", "cumulative", "single_quarter"}:
        raise FactError("Invalid period_type")
    if spec.kind in {"stock", "market"} and fact["period_type"] != "instant":
        raise FactError(f"{metric} is a point-in-time metric")
    if spec.kind == "flow" and fact["period_type"] == "instant":
        raise FactError(f"{metric} is a period flow")
    try:
        start = date.fromisoformat(fact["period_start"])
        end = date.fromisoformat(fact["period_end"])
    except (TypeError, ValueError) as exc:
        raise FactError("Invalid period date") from exc
    if start > end:
        raise FactError("period_start is after period_end")
    if fact["period_type"] == "instant" and start != end:
        raise FactError("Instant facts require identical period dates")
    if not isinstance(fact["evidence_ids"], list) or not fact["evidence_ids"]:
        raise FactError("Facts require at least one evidence ID")
    if any(not str(item).startswith("ev_") for item in fact["evidence_ids"]):
        raise FactError("Invalid evidence ID")
    value = decimal_value(fact["value"])
    if value is None and not fact["reason"]:
        raise FactError("Null fact value requires a reason")
    if value is None and fact["review_status"] == "verified":
        raise FactError("Null fact value cannot be verified")
    if value is not None and fact["review_status"] == "verified" and fact["reason"]:
        raise FactError("Verified fact should not carry an unresolved reason")


class FactIndex:
    """Select canonical facts without silently mixing versions or scopes."""

    def __init__(self, facts: Iterable[dict[str, Any]]):
        self._facts: list[dict[str, Any]] = []
        for fact in facts:
            validate_fact(fact)
            self._facts.append(fact)

    def select(
        self,
        company_code: str,
        metric: str,
        period_end: str,
        statement_scope: str,
        period_type: str | None = None,
    ) -> dict[str, Any] | None:
        matches = [
            fact for fact in self._facts
            if fact["company_code"] == company_code
            and fact["metric"] == metric
            and fact["period_end"] == period_end
            and fact["statement_scope"] == statement_scope
            and (period_type is None or fact["period_type"] == period_type)
            and fact["review_status"] != "rejected"
        ]
        if not matches:
            return None
        # A corrected/restated value supersedes an unadjusted version for the
        # same company, metric, period, scope and period type.
        if any(fact["restated"] for fact in matches):
            matches = [fact for fact in matches if fact["restated"]]
        first = matches[0]
        for other in matches[1:]:
            if (
                other["value"] != first["value"]
                or other["period_start"] != first["period_start"]
                or other["period_type"] != first["period_type"]
            ):
                raise FactConflict(
                    f"Conflicting {metric} facts for {company_code} at {period_end}"
                )
        return first

    def all(self) -> list[dict[str, Any]]:
        return list(self._facts)
