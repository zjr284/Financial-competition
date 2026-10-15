"""A 的规则抽取与 B 的财报分析/计算器生成的虚构展示资料；不调用模型。"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from app.parse.index import DocumentIndex
from app.tasks.financial_analysis.plugin import FinancialAnalysisTask
from app.tasks.structured_extraction.extract import ExtractionEngine
from app.tasks.research_verification.support import review, validate
from app.tools.financial_calculations import FinancialCalculator
from demo.adapters import FixturePorts, load_package

SOURCE = Path(__file__).parent / "fixtures" / "announcement.txt"
NOTICE = "全部虚构的规则/计算器展示样例；不是上市公司披露、大模型效果或真人金融核验"


def wrap(task, fragment, documents):
    result = {"api_version": "1.0.0", "run_id": "run_c_disclosure_" + task, "task_type": task,
              "status": "needs_review" if fragment["review_items"] else "succeeded",
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "input_document_ids": documents, **fragment}
    result["warnings"] = [NOTICE, *result["warnings"]]
    validate("TaskResult", result)
    return result


def package():
    raw = SOURCE.read_bytes()
    doc_id = "doc_c_announcement"
    document = {"document_id": doc_id, "filename": SOURCE.name, "media_type": "text/plain",
                "sha256": hashlib.sha256(raw).hexdigest(), "source_url": None,
                "disclosure_date": "2026-09-22", "ingested_at": "2026-10-15T00:00:00+08:00",
                "page_count": 1, "parse_status": "parsed"}
    extracted = ExtractionEngine().extract(doc_id, DocumentIndex.from_plain_text(doc_id, raw.decode("utf-8")), ["pledge"])
    reviews = [review(record["event_id"], "合成公告规则结果；空字段与原文须人工核对",
                      [eid for field in record["fields"] for eid in field["evidence_ids"]],
                      "run_c_disclosure_structured_extraction") for record in extracted["records"]]
    extraction = wrap("structured_extraction", {"data": {"records": extracted["records"]},
        "evidence_ids": [item["evidence_id"] for item in extracted["evidence"]],
        "calculations": [], "review_items": reviews, "warnings": extracted["warnings"]}, [doc_id])
    source = load_package()
    ports = FixturePorts(source)
    context = replace(ports.context("run_c_disclosure_financial_analysis", ["doc_c_source"]),
                      calculations=FinancialCalculator())
    fragment = FinancialAnalysisTask().run(context, {"company_code": "999999",
        "periods": ["2025-12-31", "2024-12-31"], "statement_scope": "consolidated"})
    financial = wrap("financial_analysis", fragment, ["doc_c_source"])
    validate("Document", document)
    for evidence in extracted["evidence"]:
        validate("Evidence", evidence)
    return {"synthetic": True, "documents": [document], "evidence": extracted["evidence"], "facts": []}, [extraction, financial]
