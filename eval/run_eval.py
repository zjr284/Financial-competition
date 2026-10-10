import argparse
import json
from pathlib import Path

from eval.metrics import check_company_split, evaluate


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    gold = read(args.gold)
    check_company_split(gold["records"])
    records = [r for r in gold["records"] if r["split"] == args.split]
    if not records:
        raise SystemExit("选定集合为空，不能报告评测结果")
    predictions = read(args.predictions)
    if isinstance(predictions, dict) and "data" in predictions:
        predictions = predictions["data"]["checks"]
    report = {"dataset_id": gold["dataset_id"], "synthetic": gold["synthetic"], "split": args.split,
              "notice": "合成冒烟测试，不代表竞赛实测" if gold["synthetic"] else "需核对人工标注与版本清单",
              **evaluate(records, predictions)}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
