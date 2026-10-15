"""导出核查/语义结果的空白标注模板，合并两人标注及第三人裁决。"""
import argparse
import json
from pathlib import Path

from app.tasks.research_verification.support import SCHEMA, digest, validate
from eval.metrics import adjudicate, check_company_split

ERROR_TYPES = set(SCHEMA["$defs"]["VerificationData"]["properties"]["checks"]["items"]["properties"]["error_type"]["enum"])


def template(result, dataset_id, company_code, split, synthetic):
    validate("TaskResult", result)
    if result["task_type"] in {"industry_impact", "investment_memo"}:
        from eval.synthesis_records import records as semantic_records
        records = semantic_records(result, company_code, split)
    elif result["task_type"] == "research_verification":
        records = [{"claim_id": check["claim_id"], "company_code": company_code, "split": split,
                "sentence": check["sentence"], "draft_evidence_id": check["draft_evidence_id"],
                "source_evidence_ids": check["source_evidence_ids"], "calculation_id": check["calculation_id"],
                "annotation": {"annotator": "", "decision": "", "error_type": "", "reason": ""}}
                   for check in result["data"]["checks"]]
    else:
        raise ValueError("仅接受核查、产业链或备忘录 TaskResult")
    check_company_split(records)
    if not records or len({row["claim_id"] for row in records}) != len(records):
        raise ValueError("声明为空或重复")
    # 固定任务原件引用，不向标注人展示系统判定、建议改写或评分理由。
    return {"dataset_id": dataset_id, "synthetic": synthetic, "run_id": result["run_id"],
            "result_hash": digest(result), "records": records}


def _label(label, person):
    if not isinstance(label, dict) or not isinstance(label.get(person), str) or not label[person].strip():
        raise ValueError("标注/裁决人不能为空")
    if not isinstance(label.get("reason"), str) or not label["reason"].strip():
        raise ValueError("标注/裁决须填写依据")
    decision, error = label.get("decision"), label.get("error_type")
    if decision not in {"correct", "incorrect", "needs_review"} or error not in ERROR_TYPES:
        raise ValueError("非法决策或错误类别")
    if (decision == "correct") != (error == "none"):
        raise ValueError("correct 必须使用 none，其他决策不能使用 none")
    return {**label, person: label[person].strip()}


def combine(first, second, decisions):
    for key in ("dataset_id", "synthetic", "run_id", "result_hash"):
        if first[key] != second[key]:
            raise ValueError("两份标注的资料版本不一致")
    if type(first["synthetic"]) is not bool:
        raise ValueError("synthetic 必须是布尔值")
    def index(package):
        rows = package["records"]
        mapped = {row["claim_id"]: row for row in rows}
        if not rows or len(mapped) != len(rows):
            raise ValueError("声明为空或重复")
        return mapped
    left, right = index(first), index(second)
    if set(left) != set(right) or set(decisions) - set(left):
        raise ValueError("标注或裁决含多余/缺少的声明")
    combined = []
    for cid, row in left.items():
        fixed = {key: value for key, value in row.items() if key != "annotation"}
        if fixed != {key: value for key, value in right[cid].items() if key != "annotation"}:
            raise ValueError("原句、公司划分或来源引用被修改")
        combined.append({**fixed, "annotations": [_label(row["annotation"], "annotator"),
                                                  _label(right[cid]["annotation"], "annotator")]})
    decisions = {cid: _label(label, "adjudicator") for cid, label in decisions.items()}
    records = adjudicate(combined, decisions)
    return {"dataset_id": first["dataset_id"], "synthetic": first["synthetic"], "records": records,
            "provenance": {"run_id": first["run_id"], "result_hash": first["result_hash"],
                           "annotation_hashes": [digest(first), digest(second)], "adjudications_hash": digest(decisions)}}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("template")
    prepare.add_argument("--result", required=True)
    prepare.add_argument("--dataset", required=True)
    prepare.add_argument("--company", required=True)
    prepare.add_argument("--split", choices=["development", "holdout"], required=True)
    prepare.add_argument("--synthetic", action="store_true")
    prepare.add_argument("--output", required=True)
    merge = commands.add_parser("combine")
    merge.add_argument("--first", required=True)
    merge.add_argument("--second", required=True)
    merge.add_argument("--adjudications")
    merge.add_argument("--output", required=True)
    args = parser.parse_args()
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if args.command == "template":
        if not args.dataset.strip() or len(args.company) != 6 or not args.company.isascii() or not args.company.isdigit():
            parser.error("dataset 不可为空，company 必须为六位公司代码")
        payload = template(read(args.result), args.dataset, args.company, args.split, args.synthetic)
    else:
        payload = combine(read(args.first), read(args.second), read(args.adjudications) if args.adjudications else {})
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不覆盖已经填好的人工标注或冻结金标。
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    print(f"已写入 {path}；标注身份与金融结论仍需团队确认")


if __name__ == "__main__":
    main()
