"""生成有固定文件哈希/人工示意坐标的合成资料包，不代表真实人工标注。"""
import hashlib
import json
from pathlib import Path

from app.tasks.research_verification.support import stable_id, validate

ROOT = Path(__file__).parent / "fixtures"


def build():
    ROOT.mkdir(exist_ok=True)
    source_lines = ["接口演示样例：虚构公司999999，以下数字为测试数据。",
                    "2025年全年合并营业收入为120000000元。",
                    "2024年全年合并营业收入为100000000元。",
                    "2025年全年合并归母净利润为10000000元。"]
    draft_lines = ["2025年全年合并营业收入为1.20亿元。",
                   "2025年全年合并归母净利润为9000000元。",
                   "2025年全年合并营业收入同比增长20.0%。",
                   "2025年全年合并营业收入为120000000万元。",
                   "2025年上半年合并营业收入为1.20亿元。",
                   "2025年全年母公司营业收入为1.20亿元。",
                   "公司未来一定成为行业第一。",
                   "风险：订单变化；反证：新增订单持续减少时投资逻辑不成立。"]
    success_lines = [draft_lines[0], draft_lines[2]]
    documents, evidence = [], []
    for label, lines in [("source", source_lines), ("draft", draft_lines), ("success", success_lines)]:
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        filename = label + ".txt"
        (ROOT / filename).write_bytes(raw)
        doc_id = "doc_c_" + label
        doc = {"document_id": doc_id, "filename": filename, "media_type": "text/plain",
               "sha256": hashlib.sha256(raw).hexdigest(), "source_url": None,
               "disclosure_date": None, "ingested_at": "2026-10-10T00:00:00+08:00",
               "page_count": 1, "parse_status": "parsed"}
        validate("Document", doc)
        documents.append(doc)
        for i, line in enumerate(lines):
            evidence.append({"evidence_id": f"ev_c_{label}_{i}", "document_id": doc_id,
                             "page": 1, "bbox": [50, 60 + i * 80, 950, 120 + i * 80],
                             "text": line, "extraction_method": "manual", "confidence": 1})
    facts = []
    for year, metric, value, eid in [(2025, "revenue", "120000000", "ev_c_source_1"),
                                    (2024, "revenue", "100000000", "ev_c_source_2"),
                                    (2025, "net_profit_parent", "10000000", "ev_c_source_3")]:
        facts.append({"fact_id": stable_id("fact", [year, metric]), "company_code": "999999",
                      "metric": metric, "value": value, "unit": "CNY",
                      "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
                      "period_type": "cumulative", "statement_scope": "consolidated", "restated": False,
                      "evidence_ids": [eid], "review_status": "verified", "reason": None})
    package = {"synthetic": True, "label": "接口演示样例：非竞赛实测；坐标为合成页面示意",
               "documents": documents, "evidence": evidence, "facts": facts}
    (ROOT / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


if __name__ == "__main__":
    build()
