"""两家虚构公司的公司隔离冒烟集；预期类别独立于被测模块。"""
import copy
import json
import re
from pathlib import Path

from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.research_verification.support import stable_id
from demo.adapters import FixturePorts, load_package
from eval.metrics import check_company_split, evaluate

EXPECTED = [
    ("correct", "none"), ("incorrect", "number"), ("correct", "none"),
    ("incorrect", "unit"), ("needs_review", "period"), ("needs_review", "scope"),
    ("needs_review", "insufficient_evidence"), ("needs_review", "insufficient_evidence"),
    ("needs_review", "insufficient_evidence"),
]


def run(output=Path("eval/output")):
    gold, all_predictions, reports = [], [], []
    for company, split in [("999999", "development"), ("888888", "holdout")]:
        package = copy.deepcopy(load_package())
        if company == "888888":
            # 只用于合成代码测试：不同公司/文档/事实/证据 ID，真实集不能这样复制。
            def convert(value):
                if isinstance(value, dict):
                    return {k: convert(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [convert(v) for v in value]
                if isinstance(value, str):
                    return value.replace("999999", company).replace("doc_c_", "doc_h_").replace("ev_c_", "ev_h_").replace("fact_", "fact_h_")
                return value
            package = convert(package)
        prefix = "c" if split == "development" else "h"
        draft_id, source_id = f"doc_{prefix}_draft", f"doc_{prefix}_source"
        expected = []
        for ev in package["evidence"]:
            if ev["document_id"] != draft_id:
                continue
            for index, sentence in enumerate(filter(None, (s.strip() for s in re.split(r"[。！？；\n]+", ev["text"])))):
                decision, error = EXPECTED[len(expected)]
                expected.append({"claim_id": stable_id("claim", [draft_id, ev["evidence_id"], index, sentence]),
                                 "company_code": company, "split": split, "decision": decision, "error_type": error})
        ports = FixturePorts(package)
        predictions = ResearchVerificationTask().run(ports.context(document_ids=[source_id, draft_id]),
                         {"draft_document_id": draft_id, "company_code": company})["data"]["checks"]
        report = {"synthetic": True, "notice": "虚构测试，不是实际公司留出集", "split": split, **evaluate(expected, predictions)}
        gold.extend(expected)
        all_predictions.extend(predictions)
        reports.append(report)
    check_company_split(gold)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [("gold.synthetic.json", {"dataset_id":"c-synthetic-v1", "synthetic":True, "records":gold}),
                        ("predictions.json", all_predictions), ("smoke-metrics.json", reports)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=True))
    return reports


if __name__ == "__main__":
    run()
