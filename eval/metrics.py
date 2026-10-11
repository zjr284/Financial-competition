"""声明级分类指标。缺预测计为弃权，不悄悄忽略漏报。"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

DECISIONS = {"correct", "incorrect", "needs_review"}


def ratio(numerator, denominator):
    return format((Decimal(numerator) / denominator).quantize(Decimal("0.0001")), "f") if denominator else None


def evaluate(gold, predictions):
    expected, actual = {}, {}
    for row in gold:
        if row["decision"] not in DECISIONS or row["claim_id"] in expected:
            raise ValueError("金标包含非法决策或重复 claim_id")
        expected[row["claim_id"]] = row
    for row in predictions:
        if row["decision"] not in DECISIONS or row["claim_id"] in actual:
            raise ValueError("预测包含非法决策或重复 claim_id")
        if row["claim_id"] not in expected:
            raise ValueError("预测包含金标范围之外的声明，不能静默忽略")
        actual[row["claim_id"]] = row
    tp = fp = fn = tn = correct = covered = 0
    categories, cases = Counter(), []
    for cid, label in expected.items():
        prediction = actual.get(cid, {"decision": "needs_review", "error_type": "insufficient_evidence"})
        truth, decision = label["decision"], prediction["decision"]
        correct += truth == decision
        covered += decision != "needs_review"
        if truth == "incorrect":
            tp += decision == "incorrect"
            fn += decision != "incorrect"
        elif truth == "correct":
            fp += decision == "incorrect"
            tn += decision != "incorrect"
        categories[f"{truth}->{decision}"] += 1
        if truth != decision or cid not in actual:
            cases.append({"claim_id": cid, "gold": truth, "prediction": decision,
                          "kind": "missing_prediction" if cid not in actual else "classification_mismatch"})
    unknown_false_errors = sum(expected[cid]["decision"] == "needs_review" and p["decision"] == "incorrect"
                               for cid, p in actual.items())
    typed = [cid for cid, g in expected.items() if g["decision"] == "incorrect" and "error_type" in g]
    return {"sample_count": len(expected), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp + unknown_false_errors), "recall": ratio(tp, tp + fn),
            "false_positive_rate": ratio(fp, fp + tn), "accuracy": ratio(correct, len(expected)),
            "coverage": ratio(covered, len(expected)), "missing_predictions": len(expected.keys() - actual.keys()),
            "unknown_claims_marked_incorrect": unknown_false_errors,
            "error_type_accuracy": ratio(sum(cid in actual and actual[cid]["decision"] == "incorrect"
                                             and actual[cid].get("error_type") == expected[cid]["error_type"]
                                             for cid in typed), len(typed)),
            "confusion": dict(sorted(categories.items())), "failure_cases": cases}


def check_company_split(records):
    companies = {}
    for row in records:
        if row["split"] not in {"development", "holdout"}:
            raise ValueError("split 只允许 development 或 holdout")
        previous = companies.setdefault(row["company_code"], row["split"])
        if previous != row["split"]:
            raise ValueError("同一公司跨开发集与留出集：" + row["company_code"])


def adjudicate(annotations, decisions):
    """两名不同标注人一致才自动合并；分歧须第三名裁决人明确给出结果。"""
    result = []
    for record in annotations:
        first, second = record["annotations"]
        if first["annotator"] == second["annotator"]:
            raise ValueError("双人标注必须由两名不同成员完成")
        for label in (first, second):
            if label["decision"] not in DECISIONS:
                raise ValueError("非法标注决策")
        fields = ("decision", "error_type")
        if all(first.get(k) == second.get(k) for k in fields):
            final = first
        else:
            final = decisions.get(record["claim_id"])
            if (not final or not final.get("reason") or final.get("adjudicator") in
                    {first["annotator"], second["annotator"]} or not final.get("adjudicator")):
                raise ValueError("分歧缺少独立裁决人与裁决理由")
            if final["decision"] not in DECISIONS:
                raise ValueError("非法裁决决策")
        result.append({"claim_id": record["claim_id"], "company_code": record["company_code"],
                       "split": record["split"], **{k: final[k] for k in fields}})
    check_company_split(result)
    return result
