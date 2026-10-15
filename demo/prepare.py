"""生成离线 Web 数据，不启动服务器。"""
import json
from pathlib import Path

from demo.adapters import load_package
from demo.run_demo import run
from demo.ttm_display import package as ttm_package
from demo.synthesis_fixtures import package as synthesis_package
from demo.disclosure_fixtures import package as disclosure_package
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
    synthesis, synthesis_results = synthesis_package()
    for key in ("documents", "evidence", "facts"):
        package[key].extend(synthesis[key])
    results.extend(synthesis_results)
    disclosures, disclosure_results = disclosure_package()
    for key in ("documents", "evidence", "facts"):
        package[key].extend(disclosures[key])
    results.extend(disclosure_results)
    manifest["disclosure_notice"] = "全部虚构；A 规则提取与 B 实际计算器生成，不代表模型效果或真实财报验收"
    manifest["synthesis_notice"] = "全部虚构的固定协议响应，不代表模型效果或人工金标"
    manifest["result_data_hashes"] = [digest(result["data"]) for result in results]
    manifest["ttm_notice"] = "全部虚构；计算器版本见各结果 warnings；待复核样例的一项原始利润未确认，不允许用于估值"
    (root / "web" / "demo.json").write_text(json.dumps({"package": package, "results": results,
                                                      "events": events, "manifest": manifest}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    prepare()
