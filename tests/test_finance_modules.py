"""Meaningful contract and financial-boundary checks for role B modules."""

from __future__ import annotations

import json
import copy
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from app.tasks.financial_analysis import FinancialAnalysisTask
from app.facts.reviewed_store import ReviewedFactPort
from app.tasks.a_bridge import ACompatibleTask
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
        documents=SimpleNamespace(get_document=lambda doc_id: {"document_id":doc_id, "disclosure_date":"2026-01-01"}),
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
    def test_unverified_values_are_visible_but_not_used_in_calculations(self):
        current = fact("fact_unverified_current", "999999", "revenue", "120", "2025-01-01", "2025-12-31")
        current["review_status"] = "needs_review"
        current["reason"] = "表格列名尚未人工确认"
        prior = fact("fact_verified_prior", "999999", "revenue", "100", "2024-01-01", "2024-12-31")
        ctx = context([current, prior], ("doc_annual_demo",))
        payload = FinancialAnalysisTask().run(ctx, {
            "company_code": "999999", "periods": ["2025-12-31", "2024-12-31"],
            "statement_scope": "consolidated",
        })
        self.assertEqual(len(payload["data"]["facts"]), 2)
        self.assertFalse(any(calc["formula_code"] == "yoy" for calc in payload["calculations"]))
        self.assertTrue(any("Unverified revenue" in item["reason"] for item in payload["review_items"]))

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

    def _pe_case(self):
        items = [fact("fact_target", "999999", "net_profit_parent_ttm", "10000000", "2025-07-01", "2026-06-30")]
        for code in ("888001", "888002", "888003"):
            items.extend([
                fact(f"fact_profit_{code}", code, "net_profit_parent_ttm", "10000000", "2025-07-01", "2026-06-30"),
                fact(f"fact_cap_{code}", code, "market_cap", "100000000", "2026-09-28", "2026-09-28", scope="not_applicable", period_type="instant", document_id="doc_market_snapshot_demo"),
            ])
        return items, {"company_code": "999999", "method": "relative_pe", "as_of_date": "2026-09-28", "statement_scope": "consolidated"}

    def test_pe_excludes_future_or_undated_market_sources_before_calculation(self):
        for disclosed in (None, "", "invalid", "2026-09-29"):
            with self.subTest(disclosed=disclosed):
                items, request = self._pe_case()
                ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo"))
                ctx.documents.get_document = lambda doc: {"disclosure_date": disclosed if doc == "doc_market_snapshot_demo" else "2026-08-01"}
                result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
                self.assertIsNone(result["data"]["valuation_range"]["base"])
                self.assertEqual(result["calculations"], [])
                self.assertEqual(ctx.events.events, [])
                self.assertTrue(any("Excluded fact_cap_" in message for message in result["warnings"]))

    def test_pe_accepts_sources_disclosed_on_cutoff(self):
        items, request = self._pe_case()
        ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo"))
        ctx.documents.get_document = lambda doc: {"disclosure_date": "2026-09-28"}
        result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
        self.assertEqual(result["data"]["valuation_range"]["base"], "100000000")

    def test_pe_checks_every_source_of_target_profit(self):
        items, request = self._pe_case()
        ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo", "doc_later_correction"), {"ev_later_correction": "doc_later_correction"})
        ctx.evidence.items[0]["evidence_ids"].append("ev_later_correction")
        ctx.documents.get_document = lambda doc: {"disclosure_date": "2026-10-01" if doc == "doc_later_correction" else "2026-08-01"}
        result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
        self.assertIsNone(result["data"]["valuation_range"]["base"])
        self.assertEqual(result["calculations"], [])
        self.assertIn("ev_later_correction", result["evidence_ids"])

    def test_pe_requires_peer_profit_period_to_match_target(self):
        items, request = self._pe_case()
        items[1]["period_start"], items[1]["period_end"] = "2025-04-01", "2026-03-31"
        ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo"))
        result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
        self.assertIsNone(result["data"]["valuation_range"]["base"])
        self.assertEqual(result["calculations"], [])

    def test_pe_rejects_almost_twelve_month_target_period(self):
        items, request = self._pe_case()
        items[0]["period_start"] = "2025-07-05"
        ctx = context(items, ("doc_annual_demo", "doc_market_snapshot_demo"))
        result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
        self.assertIsNone(result["data"]["valuation_range"]["base"])
        self.assertEqual(result["calculations"], [])

    def test_ttm_calendar_handles_leap_day(self):
        from app.tasks.valuation.plugin import _ttm
        for start, end in (("2023-03-01", "2024-02-29"), ("2024-03-01", "2025-02-28"), ("2023-07-01", "2024-06-30")):
            with self.subTest(start=start, end=end):
                self.assertTrue(_ttm(fact("fact_leap", "999999", "net_profit_parent_ttm", "1", start, end)))

    def test_dcf_excludes_revenue_disclosed_after_cutoff(self):
        items = [fact("fact_base", "999999", "revenue", "100000000", "2025-01-01", "2025-12-31")]
        ctx = context(items, ("doc_annual_demo",))
        ctx.documents.get_document = lambda doc: {"disclosure_date": "2026-10-01"}
        request = {
            "company_code": "999999", "method": "dcf", "as_of_date": "2026-09-28", "statement_scope": "consolidated",
            "assumption_overrides": {"revenue_growth": "5", "operating_margin": "15", "tax_rate": "25", "da_pct": "3", "capex_pct": "4", "nwc_pct": "2", "wacc": "10", "terminal_growth": "2", "net_debt": "5000000"},
        }
        result = wrap("valuation", ctx, ValuationTask().run(ctx, request))
        self.assertIsNone(result["data"]["valuation_range"]["base"])
        self.assertEqual(result["calculations"], [])
        self.assertEqual(ctx.events.events, [])
        self.assertTrue(any("disclosed after" in message for message in result["warnings"]))

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


class ReviewedFactBridgeTests(unittest.TestCase):
    def test_manifest_pins_document_hash_and_evidence_owner(self):
        item = public_fact(fact("fact_reviewed_rev", "999999", "revenue", "120000000", "2025-01-01", "2025-12-31"))
        class Documents:
            def get_document(self, document_id):
                return {"document_id": document_id, "sha256": "a" * 64}
        evidence = EvidenceFixture([item])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            bundle = {"version": 1, "documents": [{"document_id": "doc_annual_demo", "sha256": "a" * 64, "facts": [item]}]}
            path.write_text(json.dumps(bundle), encoding="utf-8")
            port = ReviewedFactPort(path, Documents(), evidence)
            self.assertEqual(port.extract_facts(["doc_annual_demo"], ["revenue"]), [item])
            self.assertEqual(port.extract_facts(["doc_other"], ["revenue"]), [])
            bundle["documents"][0]["sha256"] = "b" * 64
            path.write_text(json.dumps(bundle), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                port.extract_facts(["doc_annual_demo"], ["revenue"])
            bundle["documents"][0]["sha256"] = "a" * 64
            evidence.documents[item["evidence_ids"][0]] = "doc_other"
            path.write_text(json.dumps(bundle), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "is not from"):
                port.extract_facts(["doc_annual_demo"], ["revenue"])

    def test_a_context_bridge_runs_financial_task_with_reviewed_facts(self):
        item = public_fact(fact("fact_bridge_rev", "999999", "revenue", "120000000", "2025-01-01", "2025-12-31"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            path.write_text(json.dumps({"version": 1, "documents": [
                {"document_id": "doc_annual_demo", "sha256": "a" * 64, "facts": [item]}
            ]}), encoding="utf-8")
            bridge_context = SimpleNamespace(
                run_id="run_bridge_test", document_ids=("doc_annual_demo",),
                documents=SimpleNamespace(get_document=lambda document_id: {"document_id": document_id, "sha256": "a" * 64}),
                parse=EvidenceFixture([item]), model=ModelFixture(), events=EventFixture(),
            )
            payload = ACompatibleTask(FinancialAnalysisTask(), path).run(bridge_context, {
                "company_code": "999999", "periods": ["2025-12-31"], "statement_scope": "consolidated",
            })
            self.assertEqual(payload["data"]["facts"], [item])
            self.assertTrue(payload["review_items"])  # Other core metrics are genuinely missing.


class ReviewRegressionTests(unittest.TestCase):
    def synthesis_context(self):
        source = fact("fact_memo_source", "999999", "revenue", "100", "2025-01-01", "2025-12-31")
        source["evidence_ids"] = ["ev_annual_01"]
        industry = {"metric":"industry_event", "company_code":"999999", "evidence_ids":["ev_industry_01"], "_document_id":"doc_industry_demo"}
        return context([source, industry], ("doc_annual_demo", "doc_industry_demo"), {"ev_other":"doc_annual_demo"})

    def test_synthesis_rejects_uncomputed_model_calculations(self):
        from dataclasses import replace
        for plugin, task_input, field in [
            (InvestmentMemoTask(), {"company_code":"999999", "as_of_date":"2026-09-28"}, "thesis"),
            (IndustryImpactTask(), {"focus_company_code":"999999", "event_description":"test", "scenario_ids":["base"]}, "conclusions"),
        ]:
            data = copy.deepcopy(MODEL_EXAMPLES[plugin.task_type])
            data[field][0]["kind"] = "calculation"
            ctx = replace(self.synthesis_context(), model=SimpleNamespace(infer_json=lambda *args: data))
            with self.assertRaisesRegex(ValueError, "recomputation"):
                plugin.run(ctx, task_input)

    def test_model_cannot_cite_other_evidence_in_same_document(self):
        from dataclasses import replace
        data = copy.deepcopy(MODEL_EXAMPLES["industry_impact"])
        data["conclusions"][0]["evidence_ids"] = ["ev_other"]
        ctx = replace(self.synthesis_context(), model=SimpleNamespace(infer_json=lambda *args: data))
        with self.assertRaisesRegex(ValueError, "not supplied"):
            IndustryImpactTask().run(ctx, {"focus_company_code":"999999", "event_description":"test", "scenario_ids":["base"]})

    def test_memo_excludes_future_periods_and_disclosures(self):
        from dataclasses import replace
        for disclosure, end in [("2026-10-01", "2025-12-31"), (None, "2025-12-31"), ("2026-01-01", "2026-12-31")]:
            item = fact("fact_future", "999999", "revenue", "100", "2025-01-01", end)
            ctx = context([item], ("doc_annual_demo",))
            def forbidden_model(*args):
                self.fail("Future/undated source must not reach model")
            ctx = replace(ctx, documents=SimpleNamespace(get_document=lambda doc_id:{"disclosure_date":disclosure}),
                          model=SimpleNamespace(infer_json=forbidden_model))
            result = InvestmentMemoTask().run(ctx, {"company_code":"999999", "as_of_date":"2026-09-28"})
            self.assertEqual(result["data"]["thesis"], [])
            self.assertTrue(result["review_items"])

    def test_peer_calculation_order_is_stable(self):
        items = [fact("fact_target", "999999", "net_profit_parent_ttm", "100", "2025-07-01", "2026-06-30")]
        for company in ("888003", "888001", "888002"):
            items.extend([fact("fact_np_"+company, company, "net_profit_parent_ttm", "100", "2025-07-01", "2026-06-30"),
                          fact("fact_cap_"+company, company, "market_cap", "1000", "2026-09-28", "2026-09-28", scope="not_applicable", period_type="instant")])
        result = ValuationTask().run(context(items, ("doc_annual_demo",)), {"company_code":"999999", "method":"relative_pe", "as_of_date":"2026-09-28", "statement_scope":"consolidated"})
        self.assertEqual([c["input_fact_ids"][0] for c in result["calculations"] if c["formula_code"] == "pe"],
                         ["fact_cap_888001", "fact_cap_888002", "fact_cap_888003"])


if __name__ == "__main__":
    unittest.main()
