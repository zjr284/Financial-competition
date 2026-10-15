"""C 的完整链路检查；样例行为与生产工具缺失分开记录。"""
import copy
import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.tasks.valuation.samples.ttm_pe.demo import HERE, run_demo
from app.tasks.research_verification.support import validate
from app.tools import FinancialCalculator
from demo.ttm_display import package, fixture_paths


class TTMDisplayTests(unittest.TestCase):
    def setUp(self):
        self.inputs = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))

    def test_chain_recomputes_from_originals_without_derived_fact(self):
        display, results = package()
        facts = {fact["fact_id"]: fact for fact in display["facts"]}
        self.assertFalse(any(fact["metric"] == "net_profit_parent_ttm" for fact in facts.values()))
        records = {calc["calculation_id"]: calc for calc in results[0]["calculations"]}
        self.assertEqual(results[0]["status"], "needs_review")  # 可比性仍待人工判断。
        for calc in records.values():
            for ref in calc["input_calculation_ids"]:
                self.assertIn(ref, records)
            if calc["formula_code"] == "ttm_from_ytd":
                a, b, c = [facts[fid] for fid in calc["input_fact_ids"]]
                self.assertEqual(Decimal(a["value"]) + Decimal(b["value"]) - Decimal(c["value"]), Decimal(calc["value"]))
                self.assertEqual(len({fact["company_code"] for fact in (a, b, c)}), 1)
        for document in display["documents"]:
            self.assertEqual(hashlib.sha256(fixture_paths()[document["filename"]].read_bytes()).hexdigest(), document["sha256"])

    def test_business_failures_do_not_produce_valuation(self):
        def target(data, key, value):
            next(f for f in data["facts"] if f["fact_id"] == "fact_demo_999999_current")[key] = value
        scenarios = {
            "wrong_company": lambda data: target(data, "company_code", "888888"),
            "wrong_scope": lambda data: target(data, "statement_scope", "parent"),
            "wrong_metric": lambda data: target(data, "metric", "net_profit_parent_excl_nonrecurring"),
            "wrong_period": lambda data: target(data, "period_start", "2026-01-02"),
            "unverified": lambda data: target(data, "review_status", "needs_review"),
            "restated": lambda data: target(data, "restated", True),
            "nonpositive_target": lambda data: target(data, "value", "-200"),
            "missing_source": lambda data: data["facts"].remove(next(f for f in data["facts"] if f["fact_id"] == "fact_demo_999999_prior")),
            "too_few_peers": lambda data: data["facts"].remove(next(f for f in data["facts"] if f["fact_id"] == "fact_demo_999991_market")),
            "future_disclosure": lambda data: data["documents"][0].update(disclosure_date="2026-10-01"),
            "market_date": lambda data: data["task_input"].update(as_of_date="2026-09-29"),
        }
        for name, mutate in scenarios.items():
            with self.subTest(name=name):
                data = copy.deepcopy(self.inputs)
                mutate(data)
                result = run_demo(data)
                validate("TaskResult", result)
                self.assertTrue(all(result["data"]["valuation_range"][key] is None for key in ("low", "base", "high")))
                self.assertTrue(result["review_items"])
                self.assertFalse(any(fact["metric"] == "net_profit_parent_ttm" for fact in data["facts"]))

    def test_invalid_financial_unit_is_a_validation_error(self):
        from app.facts.catalog import FactError
        self.inputs["facts"][0]["unit"] = "percent"
        with self.assertRaises(FactError):
            run_demo(self.inputs)

    def test_twelve_month_recognition_is_separate_from_supported_formula_inputs(self):
        from app.tasks.valuation.plugin import _ttm
        for start, end in [("2023-03-01", "2024-02-29"), ("2024-03-01", "2025-02-28")]:
            fact = {"period_type": "cumulative", "period_start": start, "period_end": end}
            self.assertTrue(_ttm(fact))
            fact["period_start"] = start[:-2] + "02"
            self.assertFalse(_ttm(fact))

    def test_production_http_run_exports_and_replays_full_chain(self):
        from api.server import ApiServer
        from app.config import Settings
        from app.container import build_app
        from app.tasks.a_bridge import install_role_b
        from tools import make_sample_pdf
        import time
        import urllib.request
        with tempfile.TemporaryDirectory() as directory:
            app = build_app(Settings(data_dir=Path(directory)))
            server = None
            try:
                source_map, evidence_map, bundles = {}, {}, []
                old_lines = make_sample_pdf.LINES
                try:
                    for original in self.inputs["documents"]:
                        source_evidence = [item for item in self.inputs["evidence"] if item["document_id"] == original["document_id"]]
                        make_sample_pdf.LINES = [(i, 72, item["text"]) for i, item in enumerate(source_evidence)]
                        doc = app.upload_document(original["filename"].replace(".txt", ".pdf"), make_sample_pdf.build_pdf(), disclosure_date=original["disclosure_date"])
                        source_map[original["document_id"]] = doc["document_id"]
                        parsed = [app.get_evidence(eid) for eid in app.parse_document(doc["document_id"])["evidence_ids"]]
                        for old in source_evidence:
                            matched = [ev for ev in parsed if ev["text"] == old["text"]]
                            self.assertEqual(len(matched), 1)
                            evidence_map[old["evidence_id"]] = matched[0]["evidence_id"]
                        facts = [{**fact, "evidence_ids": [evidence_map[eid] for eid in fact["evidence_ids"]]}
                                 for fact in self.inputs["facts"] if all(eid in {ev["evidence_id"] for ev in source_evidence} for eid in fact["evidence_ids"])]
                        bundles.append({"document_id": doc["document_id"], "sha256": doc["sha256"], "facts": facts})
                finally:
                    make_sample_pdf.LINES = old_lines
                (Path(directory) / "reviewed_facts.json").write_text(json.dumps({"version": 1, "documents": bundles}), encoding="utf-8")
                install_role_b(app)
                server = ApiServer(app, port=0).start(background=True)
                def request(path, payload=None):
                    req = urllib.request.Request(server.base_url + path, data=json.dumps(payload).encode() if payload is not None else None, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return json.load(response)
                run = request("/runs", {"api_version": "1.0.0", "task_type": "valuation", "document_ids": list(source_map.values()), "task_input": self.inputs["task_input"]})
                for _ in range(100):
                    run = request("/runs/" + run["run_id"])
                    if run["status"] not in {"queued", "running"}: break
                    time.sleep(0.02)
                self.assertEqual(run["status"], "needs_review")
                result = request(f"/runs/{run['run_id']}/result")
                validate("TaskResult", result)
                expected = run_demo(self.inputs, FinancialCalculator)
                self.assertEqual(result["data"]["valuation_range"], expected["data"]["valuation_range"])
                self.assertEqual(len(result["calculations"]), len(expected["calculations"]))
                self.assertEqual(sum(c["formula_code"] == "ttm_from_ytd" for c in result["calculations"]), 4)
                replay = app.orchestrator.replay(run["run_id"])
                for key in ("input_hash_match", "result_hash_match", "trace_match", "result_original_matches"):
                    self.assertTrue(replay[key], key)
                self.assertTrue(replay["chain"]["ok"])
                self.assertNotIn("market_cap 2026", json.dumps(app.get_events(run["run_id"])))
                bundles[0]["facts"][0]["unit"] = "percent"
                (Path(directory) / "reviewed_facts.json").write_text(json.dumps({"version": 1, "documents": bundles}), encoding="utf-8")
                broken = request("/runs", {"api_version": "1.0.0", "task_type": "valuation", "document_ids": list(source_map.values()), "task_input": self.inputs["task_input"]})
                for _ in range(100):
                    broken = request("/runs/" + broken["run_id"])
                    if broken["status"] not in {"queued", "running"}: break
                    time.sleep(0.02)
                self.assertEqual(broken["status"], "failed")
                self.assertEqual(broken["error"]["code"], "VALIDATION_ERROR")
            finally:
                if server is not None: server.stop()
                app.close()


if __name__ == "__main__": unittest.main()
