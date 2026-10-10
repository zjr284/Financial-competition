import copy
import hashlib
import json
import unittest
from pathlib import Path

from app.tasks.report_quality import ReportQualityTask
from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.research_verification.task import parse_claim
from app.tasks.research_verification.support import validate
from demo.adapters import FixturePorts, load_package
from eval.metrics import adjudicate, check_company_split, evaluate


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.package = load_package()

    def run_task(self, package=None, draft="doc_c_draft"):
        ports = FixturePorts(package or self.package)
        result = ResearchVerificationTask().run(ports.context(document_ids=["doc_c_source", draft]),
                                               {"draft_document_id": draft, "company_code": "999999"})
        return result, ports

    def test_mixed_output_and_traceability(self):
        result, ports = self.run_task()
        self.assertEqual(result["data"]["summary"], {"correct": 2, "incorrect": 2, "needs_review": 5})
        checks = result["data"]["checks"]
        self.assertEqual(checks[1]["error_type"], "number")
        self.assertEqual(checks[3]["error_type"], "unit")
        self.assertEqual(checks[4]["error_type"], "period")
        self.assertEqual(checks[5]["error_type"], "scope")
        self.assertEqual(checks[2]["calculation_id"], result["calculations"][0]["calculation_id"])
        for c in checks:
            self.assertIn(c["draft_evidence_id"], ports.evidence)
            for eid in c["source_evidence_ids"]:
                self.assertEqual(ports.evidence[eid]["document_id"], "doc_c_source")
        for event in ports.events:
            validate("Event", event)
            self.assertNotIn("营业收入", json.dumps(event, ensure_ascii=False))
        self.assertEqual(self.run_task()[0], result)

    def test_all_pass_success(self):
        result, _ = self.run_task(draft="doc_c_success")
        self.assertEqual(result["data"]["summary"], {"correct": 2, "incorrect": 0, "needs_review": 0})
        self.assertEqual(result["review_items"], [])

    def test_review_ids_are_isolated_between_runs(self):
        ports = FixturePorts(self.package)
        plugin = ResearchVerificationTask()
        task_input = {"draft_document_id": "doc_c_draft", "company_code": "999999"}
        first = plugin.run(ports.context("run_first", ["doc_c_source", "doc_c_draft"]), task_input)
        second = plugin.run(ports.context("run_second", ["doc_c_source", "doc_c_draft"]), task_input)
        self.assertEqual(first["data"], second["data"])
        self.assertTrue({r["review_id"] for r in first["review_items"]}.isdisjoint(
                        {r["review_id"] for r in second["review_items"]}))

    def test_zero_base_and_negative_base_abstain(self):
        for value in ["0", "-100000000"]:
            package = copy.deepcopy(self.package)
            package["facts"][1]["value"] = value
            result, _ = self.run_task(package)
            self.assertEqual(result["data"]["checks"][2]["decision"], "needs_review")
            self.assertEqual(result["calculations"], [])

    def test_versions_and_missing_sources_abstain(self):
        package = copy.deepcopy(self.package)
        duplicate = dict(package["facts"][0], fact_id="fact_other_version", restated=True)
        package["facts"].append(duplicate)
        self.assertEqual(self.run_task(package)[0]["data"]["checks"][0]["decision"], "needs_review")
        package = copy.deepcopy(self.package)
        package["evidence"][1]["confidence"] = 0.7
        self.assertEqual(self.run_task(package)[0]["data"]["checks"][0]["decision"], "needs_review")

    def test_draft_cannot_support_itself(self):
        package = copy.deepcopy(self.package)
        package["facts"][0]["evidence_ids"] = ["ev_c_draft_0"]
        self.assertEqual(self.run_task(package)[0]["data"]["checks"][0]["decision"], "needs_review")

    def test_invalid_bbox_and_calculation_rejected(self):
        package = copy.deepcopy(self.package)
        package["evidence"][0]["bbox"] = [950, 60, 50, 120]
        with self.assertRaises(ValueError):
            self.run_task(package)
        ports = FixturePorts(self.package)
        calculate = ports.calculate
        ports.calculate = lambda *args: dict(calculate(*args), input_fact_ids=["fact_wrong"])
        with self.assertRaises(ValueError):
            ResearchVerificationTask().run(ports.context(document_ids=["doc_c_source", "doc_c_draft"]),
                                            {"draft_document_id": "doc_c_draft", "company_code": "999999"})

    def test_no_evidence_produces_review(self):
        package = copy.deepcopy(self.package)
        package["evidence"] = [e for e in package["evidence"] if e["document_id"] != "doc_c_draft"]
        result, _ = self.run_task(package)
        self.assertEqual(result["data"]["checks"], [])
        self.assertTrue(result["review_items"])

    def test_qualifiers_negation_compounds_and_decline(self):
        for sentence in ["2025年全年合并营业收入预计为1.20亿元", "2025年全年合并营业收入不是1.20亿元",
                         "2025年全年合并营业收入与1.20亿元无关", "2025年全年合并营业收入为1.20亿元，归母净利润为1亿元"]:
            self.assertIsNone(parse_claim(sentence))
        claim = parse_claim("2025年全年合并营业收入同比下降20.0%")
        self.assertEqual(str(claim["value"]), "-20.0")

    def test_quality_deterministic_and_bounded(self):
        ports = FixturePorts(self.package)
        plugin = ReportQualityTask()
        context = ports.context(document_ids=["doc_c_source", "doc_c_draft"])
        result = plugin.run(context, {"report_document_ids": ["doc_c_draft"]})
        self.assertEqual(plugin.run(context, {"report_document_ids": ["doc_c_draft"]}), result)
        score = result["data"]["report_scores"][0]
        self.assertEqual(score["score"], 45)
        self.assertEqual(sum(r["weight"] for r in result["data"]["rubric"]), 100)
        for row in result["data"]["rubric"]:
            self.assertLessEqual(sum(d["points"] for d in score["deductions"] if d["dimension"] == row["dimension"]), row["weight"])

    def test_quality_missing_company_not_invented(self):
        package = copy.deepcopy(self.package)
        package["facts"] = []
        ports = FixturePorts(package)
        result = ReportQualityTask().run(ports.context(document_ids=["doc_c_source", "doc_c_draft"]),
                                        {"report_document_ids": ["doc_c_draft"]})
        self.assertEqual(result["data"]["report_scores"][0]["score"], 35)
        self.assertTrue(result["review_items"])

    def test_fixed_file_hashes(self):
        for doc in self.package["documents"]:
            raw = (Path("demo/fixtures") / doc["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), doc["sha256"])


class EvaluationTests(unittest.TestCase):
    def test_metrics_missing_and_unknown(self):
        gold = [{"claim_id":"a","decision":"incorrect"}, {"claim_id":"b","decision":"incorrect"},
                {"claim_id":"c","decision":"correct"}, {"claim_id":"d","decision":"needs_review"}]
        prediction = [{"claim_id":"a","decision":"incorrect"}, {"claim_id":"c","decision":"incorrect"},
                      {"claim_id":"d","decision":"incorrect"}]
        result = evaluate(gold, prediction)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 1))
        self.assertEqual(result["precision"], "0.3333")
        self.assertEqual(result["recall"], "0.5000")
        self.assertEqual(result["missing_predictions"], 1)
        self.assertIsNone(evaluate([], [])["precision"])
        with self.assertRaises(ValueError):
            evaluate(gold, prediction + [{"claim_id":"extra","decision":"correct"}])

    def test_company_leakage_and_independent_annotation(self):
        with self.assertRaises(ValueError):
            check_company_split([{"company_code":"999999","split":"development"},
                                 {"company_code":"999999","split":"holdout"}])
        records = [{"claim_id":"a", "company_code":"999999", "split":"holdout", "annotations":[
            {"annotator":"C", "decision":"correct", "error_type":"none"},
            {"annotator":"B", "decision":"incorrect", "error_type":"number"}]}]
        with self.assertRaises(ValueError):
            adjudicate(records, {})
        with self.assertRaises(ValueError):
            adjudicate(records, {"a":{"adjudicator":"C", "decision":"correct", "error_type":"none", "reason":"test"}})
        self.assertEqual(adjudicate(records, {"a":{"adjudicator":"A", "decision":"incorrect", "error_type":"number", "reason":"查原文"}})[0]["decision"], "incorrect")


if __name__ == "__main__":
    unittest.main()
