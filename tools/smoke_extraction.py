"""开发期冒烟脚本：对 samples/ 下所有样例跑结构化提取并打印字段矩阵。

用法：python tools/smoke_extraction.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor" / "python"))
sys.path.insert(0, str(ROOT))

import app.bootstrap as bootstrap  # noqa: E402

bootstrap.bootstrap()

from app.config import get_settings  # noqa: E402
from app.container import build_app  # noqa: E402

SAMPLES = [
    ("samples/announcement_pledge_demo.txt", "2026-09-01"),
    ("samples/announcement_bid_demo.txt", "2026-09-10"),
    ("samples/announcement_equity_demo.txt", "2026-09-20"),
    ("samples/announcement_pledge_incomplete_demo.txt", "2026-09-25"),
    ("samples/pledge_table_demo.csv", "2026-09-30"),
    ("samples/announcement_pledge_demo.pdf", "2026-09-01"),
    ("samples/announcement_pledge_demo_cjk.pdf", "2026-09-01"),
]


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    app = build_app(settings)
    try:
        for relative, disclosure in SAMPLES:
            path = ROOT / relative
            if not path.exists():
                print(f"[skip] {relative} 不存在")
                continue
            document = app.upload_path(str(path), disclosure_date=disclosure)
            print("=" * 96)
            print(f"{relative}  ->  {document['document_id']}  {document['media_type']}")
            print(f"  sha256={document['sha256'][:16]}…  page_count={document['page_count']}")
            manifest = app.parse_document(document["document_id"])
            print(f"  parse: status={manifest['status']} backend={manifest['backend']} evidence={manifest['evidence_count']}")
            for warning in manifest["warnings"]:
                print(f"    warn: {warning}")
            run = app.run_task(
                {
                    "api_version": "1.0.0",
                    "task_type": "structured_extraction",
                    "document_ids": [document["document_id"]],
                    "task_input": {"announcement_types": ["pledge", "bid_award", "equity_change"]},
                }
            )
            if run["status"] == "failed":
                print("  RUN FAILED:", run["error"])
                continue
            result = app.get_result(run["run_id"])
            print(f"  run {run['run_id']} status={result['status']} records={len(result['data']['records'])} "
                  f"evidence={len(result['evidence_ids'])} reviews={len(result['review_items'])}")
            for record in result["data"]["records"]:
                print(f"  - [{record['announcement_type']}] {record['event_id']} date={record['event_date']} code={record['company_code']}")
                for field in record["fields"]:
                    print(
                        f"      {field['field_code']:<20} raw={str(field['raw_value'])[:22]:<24}"
                        f" value={str(field['value'])[:16]:<18} unit={field['unit']:<8}"
                        f" status={field['status']:<13} ev={len(field['evidence_ids'])}"
                    )
            replay = app.orchestrator.replay(run["run_id"])
            print(f"  replay: result_hash_match={replay['result_hash_match']} trace_match={replay['trace_match']} "
                  f"chain_ok={replay['chain']['ok']} events={replay['chain']['checked']}")
        return 0
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
