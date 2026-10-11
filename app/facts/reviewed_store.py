"""Read human-reviewed facts pinned to A's document and evidence snapshots.

This is a conservative bridge until automatic financial statement extraction is
evaluated on real filings. The file is private run data, never a public sample.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from app.facts.catalog import validate_fact


_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "contracts.schema.json").read_text(
        encoding="utf-8"
    )
)
_FACT_VALIDATOR = jsonschema.Draft202012Validator(
    {"$defs": _SCHEMA["$defs"], "$ref": "#/$defs/Fact"},
    format_checker=jsonschema.FormatChecker(),
)


class ReviewedFactPort:
    """EvidencePort backed by an explicit, versioned reviewer manifest."""

    def __init__(self, manifest_path: str | Path, documents: Any, evidence: Any):
        self.manifest_path = Path(manifest_path)
        self.documents = documents
        self.evidence = evidence

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        return self.evidence.get_evidence(evidence_id)

    def extract_facts(self, document_ids: list[str], field_codes: list[str]) -> list[dict[str, Any]]:
        if not self.manifest_path.is_file():
            return []
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Reviewed fact manifest must declare version 1")
        bundles = payload.get("documents")
        if not isinstance(bundles, list):
            raise ValueError("Reviewed fact manifest requires a documents list")
        requested = set(document_ids)
        allowed_metrics = set(field_codes)
        seen_documents: set[str] = set()
        seen_facts: set[str] = set()
        results: list[dict[str, Any]] = []
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise ValueError("Each reviewed fact bundle must be an object")
            document_id = bundle.get("document_id")
            if document_id not in requested:
                continue
            if document_id in seen_documents:
                raise ValueError(f"Duplicate reviewed fact bundle: {document_id}")
            seen_documents.add(document_id)
            document = self.documents.get_document(document_id)
            if bundle.get("sha256") != document["sha256"]:
                raise ValueError(f"Document hash mismatch for {document_id}")
            facts = bundle.get("facts")
            if not isinstance(facts, list):
                raise ValueError(f"Reviewed facts must be a list for {document_id}")
            for fact in facts:
                _FACT_VALIDATOR.validate(fact)
                validate_fact(fact)
                if fact["fact_id"] in seen_facts:
                    raise ValueError(f"Duplicate fact ID: {fact['fact_id']}")
                seen_facts.add(fact["fact_id"])
                for evidence_id in fact["evidence_ids"]:
                    source = self.get_evidence(evidence_id)
                    if source["document_id"] != document_id:
                        raise ValueError(f"Evidence {evidence_id} is not from {document_id}")
                    if fact["review_status"] == "verified" and (
                        not source["text"].strip() or source["confidence"] < 0.9
                    ):
                        raise ValueError(f"Verified fact has low-quality evidence: {evidence_id}")
                if fact["metric"] in allowed_metrics:
                    results.append(fact)
        return results
