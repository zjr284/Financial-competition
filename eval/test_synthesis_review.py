import copy
import unittest

from demo.synthesis_fixtures import package
from eval.annotations import template, combine
from app.tasks.research_verification.support import validate


class SynthesisReviewTests(unittest.TestCase):
    def setUp(self):
        self.package, self.results = package()

    def test_candidates_stay_review_only_and_sources_are_preserved(self):
        for result in self.results:
            validate("TaskResult", result)
            self.assertEqual(result["status"], "needs_review")
            self.assertTrue(result["review_items"])
            self.assertIn("不是大模型效果", result["warnings"][0])
        self.assertEqual(self.results[-1]["evidence_ids"], [])

    def test_template_covers_sections_without_prefilling_judgments(self):
        for result in self.results[:2]:
            packet = template(result, "synthetic-protocol-review", "999999", "holdout", True)
            self.assertTrue(packet["synthetic"])
            for record in packet["records"]:
                self.assertEqual(record["annotation"]["decision"], "")
                self.assertIn("/data/", record["model_path"])
                self.assertTrue(set(record["source_evidence_ids"]) <= set(result["evidence_ids"]))
            if result["task_type"] == "industry_impact":
                edge = next(row for row in packet["records"] if "/edges/" in row["model_path"])
                self.assertEqual(edge["context"]["from"]["name"], "测试客户")
            else:
                self.assertTrue(any(row["kind"] == "monitoring" for row in packet["records"]))
                self.assertEqual(len(packet["records"]), 5)

    def test_empty_result_cannot_be_mistaken_for_a_reviewed_dataset(self):
        with self.assertRaises(ValueError):
            template(self.results[-1], "empty", "999999", "holdout", True)

    def test_identical_text_does_not_collide_between_companies_or_runs(self):
        first = template(self.results[0], "synthetic", "999999", "holdout", True)
        second = template(self.results[0], "synthetic", "888888", "development", True)
        self.assertTrue({row["claim_id"] for row in first["records"]}.isdisjoint({row["claim_id"] for row in second["records"]}))
        changed = copy.deepcopy(self.results[0]); changed["run_id"] = "run_other_synthetic"
        third = template(changed, "synthetic", "999999", "holdout", True)
        self.assertTrue({row["claim_id"] for row in first["records"]}.isdisjoint({row["claim_id"] for row in third["records"]}))

    def test_independent_annotations_adjudication_and_subject_context(self):
        first = template(self.results[0], "synthetic", "999999", "holdout", True)
        second = copy.deepcopy(first)
        for packet, name in [(first, "synthetic-reviewer-a"), (second, "synthetic-reviewer-b")]:
            for row in packet["records"]:
                row["annotation"] = {"annotator": name, "decision": "needs_review", "error_type": "insufficient_evidence", "reason": "自动测试资料，非真人签字"}
        gold = combine(first, second, {})
        self.assertEqual(len(gold["records"]), 4)
        second["records"][0]["context"]["from"]["name"] = "被替换的经济主体"
        with self.assertRaises(ValueError):
            combine(first, second, {})
        second = copy.deepcopy(first)
        with self.assertRaises(ValueError):
            combine(first, second, {})  # 同人不能构成双标。


if __name__ == "__main__": unittest.main()
