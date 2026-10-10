"""Meaningful contract and financial-boundary checks for role B modules."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

import jsonschema

from app.tasks.financial_analysis import FinancialAnalysisTask
from app.tasks.industry_impact import IndustryImpactTask
from app.tasks.investment_memo import InvestmentMemoTask
from app.tasks.valuation import ValuationTask
from app.tools import FinancialCalculator
from contracts.ports import TaskContext


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/contracts.schema.json").read_text(encoding="utf-8"))
MODEL_EXAMPLES = {
    item["task_type"]: item["data"]
    for item in json.loads((ROOT / "contracts/examples/results.json").read_text(encoding="utf-8"))
}


def fact(
    fact_id: str,
    company: str,
    metric: str,
    value: str | None,
    start: str,
    end: str,
    *,
    scope: str = "consolidated",
    period_type: str = "cumulative",
    document_id: str = "doc_annual_demo",
) -> dict:
    if period_type == "instant":
        start = end
    return {
        "fact_id": fact_id,
        "company_code": company,
        "metric": metric,
        "value": value,
        "unit": "CNY",
        "period_start": start,
        "period_end": end,
        "period_type": period_type,
        "statement_scope": scope,
        "restated": False,
        "evidence_ids": ["ev_" + fact_id.removeprefix("fact_")],
        "review_status": "verified" if value is not None else "needs_review",
        "reason": None if value is not None else "Unreadable source",
        "_document_id": document_id,
    }


def public_fact(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


class EvidenceFixture:
    def __init__(self, items: list[dict], extra_documents: dict[str, str] | None = None):
        self.items = [public_fact(item) for item in items]
        self.documents = {
            item["evidence_ids"][0]: item.get("_document_id", "doc_annual_demo")
            for item in items
        }
        self.documents.update(extra_documents or {})

    def extract_facts(self, document_ids: list[str], field_codes: list[str]) -> list[dict]:
        return [
            item for item in self.items
            if item.get("metric") in field_codes
            and self.documents[item["evidence_ids"][0]] in document_ids
        ]

    def get_evidence(self, evidence_id: str) -> dict:
        return {
            "evidence_id": evidence_id,
            "document_id": self.documents[evidence_id],
            "page": 42,
            "bbox": [100, 200, 700, 240],
            "text": "模拟财务表格中的对应字段（非真实披露）",
            "extraction_method": "manual",
            "confidence": 1.0,
        }


class EventFixture:
    def __init__(self):
        self.events: list[dict] = []

    def append_event(self, run_id: str, event: dict) -> None:
        assert event["run_id"] == run_id
        self.events.append(event)


class ModelFixture:
    def infer_json(self, task_code: str, evidence_ids: list[str], schema_name: str, context: dict) -> dict:
        return MODEL_EXAMPLES[task_code]


def context(items: list[dict], docs: tuple[str, ...], extra_documents: dict[str, str] | None = None) -> TaskContext:
    return TaskContext(
        run_id="run_role_b_test",
        document_ids=docs,
        documents=object(),
        evidence=EvidenceFixture(items, extra_documents),
        calculations=FinancialCalculator(public_fact(item) for item in items if item.get("metric") != "industry_event"),
        model=ModelFixture(),
        events=EventFixture(),
    )


def wrap(task_type: str, context_value: TaskContext, payload: dict) -> dict:
    result = {
        "api_version": "1.0.0",
        "run_id": context_value.run_id,
        "task_type": task_type,
        "status": "needs_review" if payload["review_items"] else "succeeded",
        "generated_at": "2026-10-10T12:00:00+08:00",
        "input_document_ids": list(context_value.document_ids),
        **payload,
    }
    validator = jsonschema.Draft202012Validator(
        {"$defs": SCHEMA["$defs"], "$ref": "#/$defs/TaskResult"},
        format_checker=jsonschema.FormatChecker(),
    )
    validator.validate(result)
    return result


class FinancialCalculationTests(unittest.TestCase):
    def test_financial_analysis_recomputes_yoy_and_flags_profit_cash_divergence(self):
        items = [
            fact("fact_rev25", "999999", "revenue", "120000000", "2025-01-01", "2025-12-31"),
            fact("fact_rev24", "999999", "revenue", "100000000", "2024-01-01", "2024-12-31"),
            fact("fact_cost25", "999999", "cost_of_revenue", "90000000", "2025-01-01", "2025-12-31"),
            fact("fact_np25", "999999", "net_profit_parent", "10000000", "2025-01-01", "2025-12-31"),
            fact("fact_np24", "999999", "net_profit_parent", "8000000", "2024-01-01", "2024-12-31"),
            fact("fact_dnp25", "999999", "net_profit_parent_excl_nonrecurring", "4000000", "2025-01-01", "2025-12-31"),
            fact("fact_dnp24", "999999", "net_profit_parent_excl_nonrecurring", "7000000", "2024-01-01", "2024-12-31"),
            fact("fact_ocf25", "999999", "operating_cash_flow", "-2000000", "2025-01-01", "2025-12-31"),
            fact("fact_ocf24", "999999", "operating_cash_flow", "5000000", "2024-01-01", "2024-12-31"),
            fact("fact_ar25", "999999", "accounts_receivable", "30000000", "2025-12-31", "2025-12-31", period_type="instant"),
            fact("fact_ar24", "999999", "accounts_receivable", "10000000", "2024-12-31", "2024-12-31", period_type="instant"),
        ]
        ctx = context(items, ("doc_annual_demo",))
        payload = FinancialAnalysisTask().run(ctx, {
            "company_code": "999999", "periods": ["2025-12-31", "2024-12-31"],
            "statement_scope": "consolidated",
        })
        result = wrap("financial_analysis", ctx, payload)
        yoy = [calc for calc in result["calculations"] if calc["formula_code"] == "yoy"]
        revenue_yoy = next(calc for calc in yoy if calc["input_fact_ids"][0] == "fact_rev25")
        self.assertEqual(revenue_yoy["value"], "20")
        self.assertEqual(
            {alert["alert_code"] for alert in result["data"]["alerts"]},
            {"profit_cash_divergence", "large_parent_adjusted_profit_gap", "accounts_receivable_outpaces_revenue"},
        )
        source = ctx.evidence.get_evidence(result["evidence_ids"][0])
        self.assertEqual((source["page"], source["bbox"]), (42, [100, 200, 700, 240]))
        self.assertEqual(len(ctx.events.events), len(result["calculations"]))

    def test_zero_base_and_scope_mismatch_require_review(self):
        current = public_fact(fact("fact_current", "999999", "revenue", "100", "2025-01-01", "2025-12-31"))
        prior = public_fact(fact("fact_prior", "999999", "revenue", "0", "2024-01-01", "2024-12-31"))
        calculator = FinancialCalculator([current, prior])
        result = calculator.calculate("yoy", ["fact_current", "fact_prior"], [], {})
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["value"])
        self.assertIn("zero", result["reason"])
        prior["statement_scope"] = "parent"
        calculator = FinancialCalculator([current, prior])
        result = calculator.calculate("yoy", ["fact_current", "fact_prior"], [], {})
        self.assertIn("scope mismatch", result["reason"])

    def test_quarter_difference_requires_adjacent_cumulative_periods(self):
        half = public_fact(fact("fact_half", "999999", "revenue", "70", "2025-01-01", "2025-06-30"))
        quarter_one = public_fact(fact("fact_q1", "999999", "revenue", "30", "2025-01-01", "2025-03-31"))
        calculator = FinancialCalculator([half, quarter_one])
        result = calculator.calculate("quarter_from_ytd", ["fact_half", "fact_q1"], [], {})
        self.assertEqual(result["value"], "40")
        quarter_one["period_end"] = "2024-03-31"
        quarter_one["period_start"] = "2024-01-01"
        calculator = FinancialCalculator([half, quarter_one])
        result = calculator.calculate("quarter_from_ytd", ["fact_half", "fact_q1"], [], {})
        self.assertEqual(result["status"], "needs_review")

    def test_pe_rejects_non_positive_earnings(self):
        market = public_fact(fact("fact_cap", "888001", "market_cap", "100000000", "2026-09-28", "2026-09-28", scope="not_applicable", period_type="instant"))
        earnings = public_fact(fact("fact_ttm", "888001", "net_profit_parent_ttm", "-1000000", "2025-07-01", "2026-06-30"))
        calculator = FinancialCalculator([market, earnings])
        result = calculator.calculate("pe", ["fact_cap", "fact_ttm"], [], {"as_of_date": "2026-09-28"})
        self.assertEqual(result["status"], "needs_review")


class ValuationTests(unittest.TestCase):
    def test_relative_pe_uses_three_peers_and_sensitivity(self):
        items = [fact("fact_target", "999999", "net_profit_parent_ttm", "10000000", "2025-07-01", "2026-06-30")]
        for code, multiple in (("888001", 8), ("888002", 10), ("888003", 12)):
            items.extend([
                fact(f"fact_profit_{code}", code, "net_profit_parent_ttm", "10000000", "2025-07-01", "2026-06-30"),
                fact(f"fact_cap_{code}", code, "market_cap", str(multiple * 10000000), "2026-09-28", "2026-09-28", scope="not_applicable", period_type="instant", document_id="doc_market_snapshot_demo"),
            ])
        ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo"))
        payload = ValuationTask().run(ctx, {
            "company_code": "999999", "method": "relative_pe", "as_of_date": "2026-09-28",
            "statement_scope": "consolidated", "assumption_overrides": {},
        })
        result = wrap("valuation", ctx, payload)
        self.assertEqual(result["data"]["valuation_range"]["base"], "100000000")
        self.assertEqual(result["data"]["valuation_range"]["low"], "80000000")
        self.assertEqual(len(result["data"]["sensitivity"]), 9)
        self.assertTrue(result["review_items"])

    def test_dcf_missing_assumptions_returns_no_invented_value(self):
        ctx = context([], ("doc_annual_demo",))
        result = wrap("valuation", ctx, ValuationTask().run(ctx, {
            "company_code": "999999", "method": "dcf", "as_of_date": "2026-09-28",
            "statement_scope": "consolidated", "assumption_overrides": {},
        }))
        self.assertIsNone(result["data"]["valuation_range"]["base"])
        self.assertEqual(result["status"], "needs_review")

    def test_dcf_non_finite_assumption_is_rejected(self):
        ctx = context([], ("doc_annual_demo",))
        result = wrap("valuation", ctx, ValuationTask().run(ctx, {
            "company_code": "999999", "method": "dcf", "as_of_date": "2026-09-28",
            "statement_scope": "consolidated",
            "assumption_overrides": {
                "revenue_growth": "NaN", "operating_margin": "15", "tax_rate": "25",
                "da_pct": "3", "capex_pct": "4", "nwc_pct": "2", "wacc": "10",
                "terminal_growth": "2", "net_debt": "5000000",
            },
        }))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("finite", result["warnings"][0])

    def test_dcf_explicit_assumptions_generate_nine_scenarios(self):
        items = [fact("fact_base_revenue", "999999", "revenue", "100000000", "2025-01-01", "2025-12-31")]
        ctx = context(items, ("doc_annual_demo",))
        payload = ValuationTask().run(ctx, {
            "company_code": "999999", "method": "dcf", "as_of_date": "2026-09-28",
            "statement_scope": "consolidated",
            "assumption_overrides": {
                "revenue_growth": "5", "operating_margin": "15", "tax_rate": "25",
                "da_pct": "3", "capex_pct": "4", "nwc_pct": "2", "wacc": "10",
                "terminal_growth": "2", "net_debt": "5000000", "forecast_years": "5",
            },
        })
        result = wrap("valuation", ctx, payload)
        value = result["data"]["valuation_range"]
        self.assertEqual(len(result["data"]["sensitivity"]), 9)
        self.assertLessEqual(Decimal(value["low"]), Decimal(value["base"]))
        self.assertLessEqual(Decimal(value["base"]), Decimal(value["high"]))
        self.assertTrue(result["calculations"])


class SynthesisTests(unittest.TestCase):
    def test_synthesis_without_evidence_stops_before_model_call(self):
        ctx = context([], ("doc_industry_demo",))
        industry = IndustryImpactTask().run(ctx, {
            "focus_company_code": "999999", "event_description": "原料价格上涨",
            "scenario_ids": ["base"],
        })
        memo = InvestmentMemoTask().run(ctx, {
            "company_code": "999999", "as_of_date": "2026-09-28",
        })
        self.assertEqual(wrap("industry_impact", ctx, industry)["status"], "needs_review")
        self.assertEqual(wrap("investment_memo", ctx, memo)["status"], "needs_review")
        self.assertEqual(industry["data"]["conclusions"], [])
        self.assertEqual(memo["data"]["thesis"], [])

    def test_industry_and_memo_return_grounded_reviewable_data(self):
        industry_item = {
            "metric": "industry_event", "company_code": "999999",
            "evidence_ids": ["ev_industry_01"], "_document_id": "doc_industry_demo",
        }
        revenue_item = fact("fact_revenue_memo", "999999", "revenue", "100000000", "2025-01-01", "2025-12-31")
        revenue_item["evidence_ids"] = ["ev_annual_01"]
        ctx = context([industry_item, revenue_item], ("doc_industry_demo", "doc_annual_demo"))
        industry = IndustryImpactTask().run(ctx, {
            "focus_company_code": "999999", "event_description": "原料价格上涨",
            "scenario_ids": ["base"],
        })
        memo = InvestmentMemoTask().run(ctx, {
            "company_code": "999999", "as_of_date": "2026-09-28",
        })
        self.assertEqual(wrap("industry_impact", ctx, industry)["status"], "needs_review")
        self.assertEqual(wrap("investment_memo", ctx, memo)["status"], "needs_review")
        self.assertIn("ev_industry_01", industry["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
