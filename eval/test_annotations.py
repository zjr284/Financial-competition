import copy
import unittest

from demo.adapters import FixturePorts
from demo.build_fixtures import build
from app.tasks.research_verification import ResearchVerificationTask
from eval.annotations import combine, template


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        ports = FixturePorts(build())
        context = ports.context(document_ids=["doc_c_source", "doc_c_success"])
        fragment = ResearchVerificationTask().run(context, {"draft_document_id": "doc_c_success", "company_code": "999999"})
        result = {"api_version": "1.0.0", "run_id": context.run_id, "task_type": "research_verification",
                  "status": "succeeded", "generated_at": "2026-10-12T00:00:00+08:00",
                  "input_document_ids": list(context.document_ids), **fragment}
        self.blank = template(result, "synthetic-test", "999999", "holdout", True)

    def pair(self):
        first, second = copy.deepcopy(self.blank), copy.deepcopy(self.blank)
        for package, name in [(first, "member-one"), (second, "member-two")]:
            for row in package["records"]:
                row["annotation"] = {"annotator": name, "decision": "correct", "error_type": "none", "reason": "合成测试依据"}
        return first, second

    def test_template_does_not_prefill_predictions(self):
        for row in self.blank["records"]:
            self.assertNotIn("decision", row)
            self.assertEqual(row["annotation"]["decision"], "")

    def test_same_person_blank_labels_and_changed_snapshot_are_rejected(self):
        first, second = self.pair()
        second["records"][0]["annotation"]["annotator"] = "member-one "
        with self.assertRaises(ValueError):
            combine(first, second, {})
        with self.assertRaises(ValueError):
            combine(self.blank, self.blank, {})
        first, second = self.pair()
        second["records"][0]["sentence"] = "被修改的原句"
        with self.assertRaises(ValueError):
            combine(first, second, {})

    def test_disagreement_requires_independent_adjudicator(self):
        first, second = self.pair()
        second["records"][0]["annotation"].update(decision="needs_review", error_type="insufficient_evidence")
        with self.assertRaises(ValueError):
            combine(first, second, {})
        cid = first["records"][0]["claim_id"]
        decision = {cid: {"adjudicator": "member-three", "decision": "correct", "error_type": "none", "reason": "独立核对合成来源"}}
        gold = combine(first, second, decision)
        self.assertTrue(gold["synthetic"])
        self.assertEqual(len(gold["provenance"]["annotation_hashes"]), 2)
        decision[cid]["adjudicator"] = "member-one"
        with self.assertRaises(ValueError):
            combine(first, second, decision)


if __name__ == "__main__":
    unittest.main()
