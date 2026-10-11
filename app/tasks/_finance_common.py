"""Utilities shared by the finance-owned task plugins."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from contracts.ports import TaskContext


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def review_item(run_id: str, reason: str, evidence_ids: Iterable[str] = ()) -> dict[str, Any]:
    refs = unique(evidence_ids)
    return {
        "review_id": f"review_{digest([run_id, reason, refs])[:20]}",
        "target_id": run_id,
        "reason": reason,
        "status": "pending",
        "evidence_ids": refs,
    }


def record_calculation(context: TaskContext, calc: dict[str, Any]) -> None:
    """Record a tool call without copying source document text into logs."""
    event = {
        "event_id": f"event_{digest([context.run_id, calc['calculation_id']])[:20]}",
        "run_id": context.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "calculation",
        "tool_name": calc["formula_code"],
        "input_hash": digest([calc["input_fact_ids"], calc["input_calculation_ids"], calc["parameters"]]),
        "output_hash": digest({"value": calc["value"], "status": calc["status"]}),
        "message": calc["status"],
    }
    context.events.append_event(context.run_id, event)


def prime_calculator(context: TaskContext, facts: Iterable[dict[str, Any]]) -> None:
    """Register facts when the injected calculator supports per-run registration."""
    register = getattr(context.calculations, "register_facts", None)
    if callable(register):
        register(facts)


def source_ids_for_facts(context: TaskContext, facts: Iterable[dict[str, Any]]) -> list[str]:
    """Reject facts whose evidence points outside the run's fixed documents."""
    ids: list[str] = []
    for fact in facts:
        for evidence_id in fact["evidence_ids"]:
            evidence = context.evidence.get_evidence(evidence_id)
            if evidence["document_id"] not in context.document_ids:
                raise ValueError(
                    f"Evidence {evidence_id} is outside this run's document snapshot"
                )
            ids.append(evidence_id)
    return unique(ids)


def source_ids_for_statements(
    context: TaskContext, statements: Iterable[dict[str, Any]], allowed_ids: Iterable[str] | None = None
) -> list[str]:
    ids: list[str] = []
    allowed = set(allowed_ids) if allowed_ids is not None else None
    for statement in statements:
        if statement.get("kind") == "calculation":
            raise ValueError("Model calculation claims require deterministic tool recomputation; no calculation output is supported by this synthesis task")
        for evidence_id in statement.get("evidence_ids", []):
            if allowed is not None and evidence_id not in allowed:
                raise ValueError(f"Model cites evidence not supplied for this task: {evidence_id}")
            evidence = context.evidence.get_evidence(evidence_id)
            if evidence["document_id"] not in context.document_ids:
                raise ValueError(
                    f"Evidence {evidence_id} is outside this run's document snapshot"
                )
            ids.append(evidence_id)
    return unique(ids)


def validate_task_data(definition: str, data: dict[str, Any]) -> None:
    """Validate model-produced task data against the frozen public contract."""
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "contracts" / "contracts.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({
        "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"
    })
    error = next(iter(validator.iter_errors(data)), None)
    if error is not None:
        raise ValueError(f"Invalid {definition} model output: {error.message}")
