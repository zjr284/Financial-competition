"""通过实际 A HTTP 服务与 B 计算器验证 C，不拦截 API 请求。"""
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from api.server import ApiServer
from app.config import Settings
from app.container import build_app
from app.tasks.research_verification.runtime import install_role_c
from app.tasks.research_verification.support import validate
from demo.live_fixtures import seed


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = build_app(Settings(data_dir=Path(self.temp.name)))
        self.addCleanup(self.app.close)
        self.seeded = seed(self.app)
        self.ids = self.seeded["documents"]
        self.manifest = Path(self.seeded["fact_manifest"])
        install_role_c(self.app)
        self.server = ApiServer(self.app, port=0).start(background=True)
        self.addCleanup(self.server.stop)

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server.base_url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertTrue(response.headers["X-Request-Id"])
            return json.load(response)

    def run_task(self, kind="research_verification", report="draft"):
        task_input = ({"draft_document_id": self.ids[report], "company_code": "999999"}
                      if kind == "research_verification" else {"report_document_ids": [self.ids[report]]})
        run = self.request("/runs", {"api_version": "1.0.0", "task_type": kind,
                          "document_ids": [self.ids["source"], self.ids[report]], "task_input": task_input})
        for _ in range(100):
            run = self.request("/runs/" + run["run_id"])
            if run["status"] not in {"queued", "running"}:
                validate("Run", run)
                return run
            time.sleep(0.02)
        self.fail("实际 HTTP 任务未结束")

    def test_http_success_mixed_quality_evidence_review_and_pagination(self):
        for kind, report in [("research_verification", "success"), ("research_verification", "draft"), ("report_quality", "draft")]:
            run = self.run_task(kind, report)
            self.assertEqual(run["status"], "succeeded" if report == "success" else "needs_review")
            result = self.request(f"/runs/{run['run_id']}/result")
            validate("TaskResult", result)
            self.assertEqual(result["calculations"][0]["value"], "20")
            if kind == "research_verification":
                self.assertEqual(result["data"]["summary"], {"correct": 2, "incorrect": 0 if report == "success" else 2, "needs_review": 0 if report == "success" else 5})
            else:
                self.assertEqual(result["data"]["report_scores"][0]["score"], 45)
            for eid in result["evidence_ids"]:
                evidence = self.request("/evidence/" + eid)
                validate("Evidence", evidence)
                with urllib.request.urlopen(self.server.base_url + "/documents/" + evidence["document_id"] + "/content") as response:
                    import hashlib
                    self.assertEqual(hashlib.sha256(response.read()).hexdigest(), self.app.get_document(evidence["document_id"])["sha256"])
            events, cursor = [], None
            while True:
                page = self.request(f"/runs/{run['run_id']}/events?limit=3" + ("&cursor=" + cursor if cursor else ""))
                validate("EventPage", page)
                events.extend(page["events"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            self.assertEqual(len(events), len(self.app.events.all_events(run["run_id"])))
            self.assertEqual(len(events), len({event["event_id"] for event in events}))
            self.assertNotIn("营业收入", json.dumps(events, ensure_ascii=False))
            if result["review_items"]:
                item = self.request("/reviews/" + result["review_items"][0]["review_id"] + "/decision",
                                    {"decision": "accept", "reviewer": "automated-synthetic-test", "note": "测试操作，不代表人工验收", "corrected_value": None})
                self.assertEqual(item["status"], "accepted")

    def test_replay_pins_facts_and_preserves_trace_without_writing_events(self):
        run = self.run_task(report="success")
        self.assertEqual(run["status"], "succeeded")
        self.manifest.write_text('{"version": 1, "documents": []}', encoding="utf-8")
        before = len(self.app.events.all_events(run["run_id"]))
        report = self.app.orchestrator.replay(run["run_id"])
        for key in ("input_hash_match", "result_hash_match", "trace_match", "result_original_matches"):
            self.assertTrue(report[key], (key, report))
        self.assertEqual(before, len(self.app.events.all_events(run["run_id"])))
        self.assertTrue(report["chain"]["ok"])
        fresh = self.run_task(report="success")
        self.assertEqual(fresh["status"], "needs_review")

    def test_missing_facts_abstain_and_stale_hash_fails_safely(self):
        original = self.manifest.read_text(encoding="utf-8")
        self.manifest.unlink()
        run = self.run_task(report="success")
        self.assertEqual(run["status"], "needs_review")
        result = self.request(f"/runs/{run['run_id']}/result")
        self.assertEqual(result["data"]["summary"], {"correct": 0, "incorrect": 0, "needs_review": 2})
        payload = json.loads(original)
        payload["documents"][0]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        failed = self.run_task()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(failed["error"]["details"]["task_type"], "research_verification")
        self.assertNotIn("营业收入", json.dumps(failed["error"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
