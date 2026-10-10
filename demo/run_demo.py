import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.tasks.report_quality import ReportQualityTask
from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.research_verification.support import digest, validate
from demo.adapters import FixturePorts, load_package


def run(output):
    package = load_package()
    ports = FixturePorts(package)
    results = []
    for name, plugin, document_ids, task_input in [
        ("verification", ResearchVerificationTask(), ["doc_c_source", "doc_c_draft"],
         {"draft_document_id": "doc_c_draft", "company_code": "999999"}),
        ("success", ResearchVerificationTask(), ["doc_c_source", "doc_c_success"],
         {"draft_document_id": "doc_c_success", "company_code": "999999"}),
        ("quality", ReportQualityTask(), ["doc_c_source", "doc_c_draft"],
         {"report_document_ids": ["doc_c_draft"]}),
    ]:
        run_id = "run_c_" + name
        fragment = plugin.run(ports.context(run_id, document_ids), task_input)
        result = {"api_version": "1.0.0", "run_id": run_id, "task_type": plugin.task_type,
                  "status": "needs_review" if fragment["review_items"] else "succeeded",
                  "generated_at": datetime.now(timezone.utc).isoformat(),
                  "input_document_ids": document_ids, **fragment}
        validate("TaskResult", result)
        results.append(result)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        revision = "unknown"
    manifest = {"synthetic": True, "code_version": revision, "rules_version": "c-rules-v1",
                "model_id": "none-rule-baseline", "model_revision": "not-applicable",
                "prompt_version": "not-applicable", "input_hash": digest(package),
                "result_data_hashes": [digest(r["data"]) for r in results],
                "notice": "合成冒烟测试，不是实际留出集或人工金融复核结论"}
    root = Path(__file__).resolve().parents[1]
    source_files = sorted(path for folder in [root / "app" / "tasks" / "research_verification",
                                             root / "app" / "tasks" / "report_quality", root / "web"]
                          for path in folder.glob("*.*")
                          if path.suffix in {".py", ".js", ".html", ".css"})
    manifest["implementation_hash"] = digest({str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in source_files})
    try:
        manifest["working_tree_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        manifest["working_tree_dirty"] = None
    output.mkdir(parents=True, exist_ok=True)
    for filename, value in [("results.json", results), ("events.json", ports.events), ("manifest.json", manifest)]:
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verification": results[0]["data"]["summary"], "success": results[1]["data"]["summary"],
                      "quality_score": results[2]["data"]["report_scores"][0]["score"]}, ensure_ascii=False))
    return results, ports.events, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("demo/output"))
    run(parser.parse_args().output)
