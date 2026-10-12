"""Local, evidence-first workflow for preparing B's reviewed fact manifest.

The commands never extract or invent financial values. A reviewer reads the
original filing and enters each value after checking its scope and period.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.facts.catalog import METRICS, validate_fact
from app.facts.reviewed_store import ReviewedFactPort


def _open_app(data_dir: Path | None) -> Any:
    from app.config import Settings
    from app.container import build_app

    return build_app(Settings(data_dir=data_dir.resolve())) if data_dir else build_app()


def _manifest_path(app: Any, supplied: Path | None) -> Path:
    return supplied if supplied is not None else app.settings.data_dir / "reviewed_facts.json"


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("documents"), list):
        raise ValueError("事实清单必须包含 version=1 和 documents 数组")
    return payload


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def init_manifest(app: Any, path: Path, document_ids: list[str]) -> dict[str, Any]:
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("文档 ID 不得重复")
    bundles = []
    for document_id in document_ids:
        document = app.get_document(document_id)
        bundles.append({"document_id": document_id, "sha256": document["sha256"], "facts": []})
    payload = {"version": 1, "documents": bundles}
    _write_new(path, payload)
    return payload


def inspect_evidence(app: Any, document_id: str, *, contains: str = "", page: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    app.get_document(document_id)
    app.parse_document(document_id)
    items = app.parse.list_evidence(document_id, page=page)
    selected = [item for item in items if contains in item["text"]]
    return [{
        "evidence_id": item["evidence_id"], "page": item["page"],
        "bbox": item["bbox"], "confidence": item["confidence"],
        "text": item["text"][:160],
    } for item in selected[:limit]]


def add_fact(app: Any, path: Path, *, document_id: str, evidence_id: str,
             company_code: str, metric: str, value: str | None,
             period_start: str, period_end: str, period_type: str,
             statement_scope: str, restated: bool = False,
             verified: bool = False, reason: str | None = None) -> dict[str, Any]:
    payload = _load_manifest(path)
    bundles = [bundle for bundle in payload["documents"] if bundle.get("document_id") == document_id]
    if len(bundles) != 1:
        raise ValueError("文档必须在清单中恰好出现一次")
    bundle = bundles[0]
    document = app.get_document(document_id)
    if bundle.get("sha256") != document["sha256"]:
        raise ValueError("文档哈希不匹配，请重新核对登记版本")
    source = app.get_evidence(evidence_id)
    if source["document_id"] != document_id:
        raise ValueError("证据不属于指定文档")
    if metric not in METRICS:
        raise ValueError(f"未知财务指标代码：{metric}")
    if value is None and verified:
        raise ValueError("缺失数值不能标为 verified")
    if not verified and not reason:
        raise ValueError("待复核事实必须说明原因；核对完成后可使用 --verified")
    basis = [document_id, evidence_id, company_code, metric, period_start, period_end,
             period_type, statement_scope, restated]
    fact_id = "fact_" + hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    fact = {
        "fact_id": fact_id, "company_code": company_code, "metric": metric,
        "value": value, "unit": METRICS[metric].unit,
        "period_start": period_start, "period_end": period_end,
        "period_type": period_type, "statement_scope": statement_scope,
        "restated": restated, "evidence_ids": [evidence_id],
        "review_status": "verified" if verified else "needs_review",
        "reason": None if verified else reason,
    }
    validate_fact(fact)
    if any(fact_id == item.get("fact_id") for document_bundle in payload["documents"]
           for item in document_bundle.get("facts", [])):
        raise ValueError("该来源、期间和口径的事实已登记；请检查旧记录，不自动覆盖")
    bundle["facts"].append(fact)
    # Validate the complete candidate before replacing the private manifest.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".facts-", suffix=".json", delete=False) as stream:
        candidate_path = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        ReviewedFactPort(candidate_path, app.documents, app.parse).extract_facts(
            [entry["document_id"] for entry in payload["documents"]], list(METRICS)
        )
        os.replace(candidate_path, path)
    finally:
        candidate_path.unlink(missing_ok=True)
    return fact


def check_manifest(app: Any, path: Path) -> dict[str, Any]:
    payload = _load_manifest(path)
    document_ids = [bundle["document_id"] for bundle in payload["documents"]]
    facts = ReviewedFactPort(path, app.documents, app.parse).extract_facts(document_ids, list(METRICS))
    return {
        "documents": len(document_ids), "facts": len(facts),
        "verified": sum(item["review_status"] == "verified" for item in facts),
        "needs_review": sum(item["review_status"] == "needs_review" for item in facts),
        "sources": [{"fact_id": fact["fact_id"], "metric": fact["metric"],
                     "evidence_id": evidence_id,
                     "page": app.get_evidence(evidence_id)["page"],
                     "bbox": app.get_evidence(evidence_id)["bbox"]}
                    for fact in facts for evidence_id in fact["evidence_ids"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="人工复核财务事实：定位、登记、校验")
    parser.add_argument("--data-dir", type=Path, help="A 的本地运行数据目录")
    parser.add_argument("--manifest", type=Path, help="私有 reviewed_facts.json 路径")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="为已登记文档创建空白事实清单")
    init.add_argument("document_ids", nargs="+")
    inspect = sub.add_parser("inspect", help="查找已解析文档的证据与位置")
    inspect.add_argument("document_id")
    inspect.add_argument("--contains", default="")
    inspect.add_argument("--page", type=int)
    inspect.add_argument("--limit", type=int, default=30)
    add = sub.add_parser("add", help="人工核对后登记一条 Fact")
    for name in ("document-id", "evidence-id", "company-code", "metric", "period-start", "period-end", "period-type", "statement-scope"):
        add.add_argument("--" + name, required=True)
    add.add_argument("--value", help="人民币元的十进制字符串；缺失时不传")
    add.add_argument("--restated", action="store_true")
    add.add_argument("--verified", action="store_true", help="仅人工核对原件与口径后使用")
    add.add_argument("--reason", help="待复核原因")
    sub.add_parser("check", help="校验事实、文件哈希与证据位置")
    args = parser.parse_args()
    app = _open_app(args.data_dir)
    try:
        path = _manifest_path(app, args.manifest)
        if args.command == "init":
            result = init_manifest(app, path, args.document_ids)
        elif args.command == "inspect":
            if args.limit < 1:
                parser.error("--limit 必须大于 0")
            result = inspect_evidence(app, args.document_id, contains=args.contains, page=args.page, limit=args.limit)
        elif args.command == "add":
            values = {name.replace("-", "_"): getattr(args, name.replace("-", "_"))
                      for name in ("document-id", "evidence-id", "company-code", "metric", "period-start", "period-end", "period-type", "statement-scope")}
            result = add_fact(app, path, **values, value=args.value, restated=args.restated,
                              verified=args.verified, reason=args.reason)
        else:
            result = check_manifest(app, path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        app.close()


if __name__ == "__main__":
    main()
