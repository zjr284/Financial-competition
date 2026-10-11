"""End-to-end B checks against A's app once both branches are present."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("app.container"), "A's app is not merged")
class RoleBAIntegrationTests(unittest.TestCase):
    def test_pdf_evidence_financial_run_and_replay(self):
        from app.config import Settings
        from app.container import build_app
        from app.tasks.a_bridge import install_role_b
        from tools import make_sample_pdf

        original_lines = make_sample_pdf.LINES
        try:
            make_sample_pdf.LINES = [
                (0, 72, "Sample financial statements 999999"),
                (1, 72, "2025 consolidated revenue: CNY 120000000"),
                (2, 72, "2024 consolidated revenue: CNY 100000000"),
            ]
            payload = make_sample_pdf.build_pdf()
        finally:
            make_sample_pdf.LINES = original_lines

        with tempfile.TemporaryDirectory() as directory:
            app = build_app(Settings(data_dir=Path(directory)))
            try:
                doc = app.upload_document("finance_demo.pdf", payload, disclosure_date="2026-03-01")
                parsed = app.parse_document(doc["document_id"])
                evidence = [app.get_evidence(eid) for eid in parsed["evidence_ids"]]
                facts = []
                for year, value in ((2025, "120000000"), (2024, "100000000")):
                    source = next(item for item in evidence if str(year) in item["text"])
                    self.assertEqual(source["page"], 1)
                    self.assertEqual(source["confidence"], 1.0)
                    self.assertTrue(source["bbox"][0] < source["bbox"][2])
                    facts.append({
                        "fact_id": f"fact_revenue_{year}", "company_code": "999999",
                        "metric": "revenue", "value": value, "unit": "CNY",
                        "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
                        "period_type": "cumulative", "statement_scope": "consolidated",
                        "restated": False, "evidence_ids": [source["evidence_id"]],
                        "review_status": "verified", "reason": None,
                    })
                manifest_path = Path(directory) / "reviewed_facts.json"
                manifest = {"version": 1, "documents": [{
                    "document_id": doc["document_id"], "sha256": doc["sha256"], "facts": facts,
                }]}
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                install_role_b(app, manifest_path)
                request = {
                    "api_version": "1.0.0", "task_type": "financial_analysis",
                    "document_ids": [doc["document_id"]],
                    "task_input": {"company_code": "999999", "periods": ["2025-12-31", "2024-12-31"],
                                   "statement_scope": "consolidated"},
                }
                run = app.run_task(request)
                self.assertEqual(run["status"], "needs_review")  # Other core facts are absent.
                result = app.get_result(run["run_id"])
                yoy = next(calc for calc in result["calculations"] if calc["formula_code"] == "yoy")
                self.assertEqual(yoy["value"], "20")
                self.assertTrue(app.orchestrator.replay(run["run_id"])["result_hash_match"])

                manifest["documents"][0]["sha256"] = "0" * 64
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                invalid = app.run_task(request)
                self.assertEqual(invalid["status"], "failed")
                self.assertEqual(invalid["error"]["code"], "VALIDATION_ERROR")
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
