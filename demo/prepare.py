"""生成离线 Web 数据，不启动服务器。"""
import json
from pathlib import Path

from demo.adapters import load_package
from demo.run_demo import run
from demo.ttm_display import package as ttm_package
from app.tasks.research_verification.support import digest


def prepare():
    root = Path(__file__).resolve().parents[1]
    results, events, manifest = run(root / "demo" / "output")
    package = load_package()
    ttm, ttm_results = ttm_package()
    for key in ("documents", "evidence", "facts"):
        package[key].extend(ttm[key])
    package["ttm_pending_facts"] = ttm["pending_facts"]
    results.extend(ttm_results)
    manifest["result_data_hashes"] = [digest(result["data"]) for result in results]
    manifest["ttm_notice"] = "全部虚构；计算器版本见各结果 warnings；待复核样例的一项原始利润未确认，不允许用于估值"
    (root / "web" / "demo.json").write_text(json.dumps({"package": package, "results": results,
                                                      "events": events, "manifest": manifest}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    prepare()
