"""全部虚构的语义协议样例；不是模型推理或真人审核结果。"""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.tasks.industry_impact import IndustryImpactTask
from app.tasks.investment_memo import InvestmentMemoTask
from app.tasks.research_verification.support import validate
from app.tools import FinancialCalculator
from contracts.ports import TaskContext

SOURCE = Path(__file__).parent / "fixtures" / "synthesis.txt"


def protocol_output(schema_name, evidence_ids, context):
    """本地测试响应器仅复现 JSON 协议，文本是人工编写的虚构案例。"""
    ids = evidence_ids[1:] if len(evidence_ids) > 1 else evidence_ids[:1]
    statement = {"text": "若测试客户减少订单，测试封装公司的交付安排可能变化。", "kind": "inference",
                 "evidence_ids": ids, "assumptions": ["客户订单确有变化，其他条件不变"],
                 "limitations": ["不能将客户元器件成本直接视为封装公司的自身成本"]}
    if schema_name == "IndustryData":
        return {"nodes": [{"node_id": "customer", "name": "测试客户", "stage": "downstream"},
                          {"node_id": "company", "name": "测试封装公司", "stage": "company"}],
                "edges": [{"from_id": "customer", "to_id": "company", "mechanism": "订单变化可能影响排程", "direction": "uncertain", "evidence_ids": ids}],
                "scenarios": [{"scenario_id": sid, "assumptions": ["订单未减少" if sid == "reverse" else "测试客户减少订单"], "impact_text": "负向订单假设不成立" if sid == "reverse" else "需检查订单与实际产能安排", "evidence_ids": ids}
                              for sid in context["scenario_ids"]], "conclusions": [statement]}
    if schema_name != "MemoData":
        raise ValueError("Unexpected protocol fixture schema")
    return {"thesis": [statement], "evidence": [{"text": "合成资料描述客户与封装公司的不同经济主体。", "kind": "fact", "evidence_ids": ids, "assumptions": [], "limitations": ["仅虚构接口资料，事实性陈述仍须核对原文"]}],
            "risks": [{**statement, "text": "订单来源集中可能放大排程变化。"}],
            "disconfirming_conditions": [{**statement, "text": "若订单没有下降，上述负向假设不成立。"}],
            "monitoring_indicators": ["检查公开订单与排程变化，不自动生成数值阈值"]}


def package():
    raw = SOURCE.read_bytes()
    document = {"document_id": "doc_demo_synthesis", "filename": SOURCE.name, "media_type": "text/plain",
                "sha256": hashlib.sha256(raw).hexdigest(), "source_url": None, "disclosure_date": "2026-08-01",
                "ingested_at": "2026-10-15T00:00:00+08:00", "page_count": 1, "parse_status": "parsed"}
    evidence = {"evidence_id": "ev_demo_synthesis", "document_id": document["document_id"], "page": 1,
                "bbox": [0, 0, 1000, 1000], "text": raw.decode("utf-8").strip(), "extraction_method": "manual", "confidence": 1}
    validate("Document", document); validate("Evidence", evidence)
    class FixtureModel:
        def infer_json(self, task_code, ids, schema_name, context):
            return protocol_output(schema_name, ids, context)
    results = []
    for plugin, task_input, available in [
        (IndustryImpactTask(), {"focus_company_code": "999999", "event_description": "测试订单变化", "scenario_ids": ["base", "reverse"]}, True),
        (InvestmentMemoTask(), {"company_code": "999999", "as_of_date": "2026-09-30"}, True),
        (InvestmentMemoTask(), {"company_code": "999999", "as_of_date": "2026-09-30"}, False),
    ]:
        port = SimpleNamespace(get_evidence=lambda eid: evidence, extract_facts=lambda ids, fields: [],
                               archived_excerpts=lambda ids: [{"source_kind": "archived_excerpt", "source_scope": "company", "company_code": "999999", "text": evidence["text"], "evidence_ids": [evidence["evidence_id"]]}] if available else [])
        context = TaskContext("run_demo_" + plugin.task_type + ("_empty" if not available else ""), (document["document_id"],),
                              SimpleNamespace(get_document=lambda did: document), port, FinancialCalculator(), FixtureModel(),
                              SimpleNamespace(append_event=lambda rid, event: None))
        fragment = plugin.run(context, task_input)
        fragment["warnings"].insert(0, "全部虚构的接口协议样例；固定文本由测试响应器提供，不是大模型效果或真人金标")
        result = {"api_version": "1.0.0", "run_id": context.run_id, "task_type": plugin.task_type, "status": "needs_review",
                  "generated_at": "2026-10-15T00:00:00+08:00", "input_document_ids": list(context.document_ids), **fragment}
        validate("TaskResult", result)
        results.append(result)
    return {"documents": [document], "evidence": [evidence], "facts": []}, results
