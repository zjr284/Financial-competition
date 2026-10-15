"""将冻结的语义输出拆成可人工审阅单元，不自动判断语义正确性。"""
from app.tasks.research_verification.support import stable_id


def records(result, company_code, split):
    data, output = result["data"], []
    def add(section, index, text, kind, refs, assumptions=(), limitations=(), context=None):
        output.append({"claim_id": stable_id("claim", [result["run_id"], company_code, result["task_type"], section, index, text]),
                       "company_code": company_code, "split": split, "sentence": text,
                       "model_path": f"/data/{section}/{index}", "kind": kind,
                       "source_evidence_ids": list(refs), "assumptions": list(assumptions),
                       "limitations": list(limitations), "context": context or {},
                       "annotation": {"annotator": "", "decision": "", "error_type": "", "reason": ""}})
    if result["task_type"] == "investment_memo":
        for section in ("thesis", "evidence", "risks", "disconfirming_conditions"):
            for index, item in enumerate(data[section]):
                add(section, index, item["text"], item["kind"], item["evidence_ids"], item["assumptions"], item["limitations"])
        for index, text in enumerate(data["monitoring_indicators"]):
            add("monitoring_indicators", index, text, "monitoring", [])
    elif result["task_type"] == "industry_impact":
        nodes = {node["node_id"]: node for node in data["nodes"]}
        for index, item in enumerate(data["edges"]):
            add("edges", index, item["mechanism"], "mechanism", item["evidence_ids"],
                context={"from": nodes.get(item["from_id"], item["from_id"]), "to": nodes.get(item["to_id"], item["to_id"]), "direction": item["direction"]})
        for index, item in enumerate(data["scenarios"]):
            add("scenarios", index, item["impact_text"], "scenario", item["evidence_ids"], item["assumptions"], context={"scenario_id": item["scenario_id"]})
        for index, item in enumerate(data["conclusions"]):
            add("conclusions", index, item["text"], item["kind"], item["evidence_ids"], item["assumptions"], item["limitations"])
    else:
        raise ValueError("不支持的语义任务")
    return output
