from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "contracts.schema.json").read_text(encoding="utf-8"))


def validate(name, value):
    Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"},
                         format_checker=FormatChecker()).validate(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def stable_id(prefix, value):
    return prefix + "_" + digest(value)[:24]


def call(context, event_type, name, arguments, operation):
    """事件只存哈希和调用摘要；不记录原文或模型输出。日志失败直接传播。"""
    try:
        output = operation()
    except Exception:
        event(context, "error", name, arguments, None, "调用失败，详细原因由调用方处理")
        raise
    event(context, event_type, name, arguments, output, "调用完成")
    return output


def event(context, kind, name, arguments, output, message):
    context.events.append_event(context.run_id, {
        "event_id": "event_" + uuid4().hex, "run_id": context.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(), "event_type": kind,
        "tool_name": name, "input_hash": digest(arguments),
        "output_hash": digest(output) if output is not None else None, "message": message,
    })


def load_evidence(context, document_id):
    doc = call(context, "file_access", "get_document", document_id,
               lambda: context.documents.get_document(document_id))
    validate("Document", doc)
    if doc["document_id"] != document_id:
        raise ValueError("文档 ID 与请求不一致")
    manifest = call(context, "tool_call", "parse_document", document_id,
                    lambda: context.documents.parse_document(document_id))
    result = []
    for evidence_id in dict.fromkeys(manifest.get("evidence_ids", [])):
        ev = call(context, "file_access", "get_evidence", evidence_id,
                  lambda eid=evidence_id: context.evidence.get_evidence(eid))
        validate("Evidence", ev)
        x0, y0, x1, y1 = ev["bbox"]
        if ev["evidence_id"] != evidence_id or ev["document_id"] != document_id:
            raise ValueError("证据 ID 或文档版本不一致")
        if not (x0 < x1 and y0 < y1) or (doc["page_count"] and ev["page"] > doc["page_count"]):
            raise ValueError("证据页码或坐标无效")
        result.append(ev)
    return sorted(result, key=lambda ev: (ev["page"], ev["bbox"][1], ev["bbox"][0], ev["evidence_id"])), list(manifest.get("warnings", []))


def review(target_id, reason, evidence_ids, run_id):
    return {"review_id": stable_id("review", [run_id, target_id, reason]), "target_id": target_id,
            "reason": reason, "status": "pending", "evidence_ids": sorted(set(evidence_ids))}


def finish(data, evidence_ids, calculations, reviews, warnings, definition):
    validate(definition, data)
    for calc in calculations:
        validate("Calculation", calc)
    for item in reviews:
        validate("ReviewItem", item)
    return {"data": data, "evidence_ids": sorted(set(evidence_ids)),
            "calculations": calculations, "review_items": reviews,
            "warnings": list(dict.fromkeys(warnings))}
