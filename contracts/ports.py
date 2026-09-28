"""Stable Python ports for separately developed modules.

These are interfaces, not implementations. JSON payloads must still be validated
against contracts.schema.json at HTTP and task boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


JsonObject = dict[str, Any]


class DocumentPort(Protocol):
    def ingest_file(self, path: str, metadata: JsonObject) -> JsonObject:
        """Return a Document."""

    def parse_document(self, document_id: str) -> JsonObject:
        """Return a parse manifest with evidence_ids and warnings."""

    def get_document(self, document_id: str) -> JsonObject:
        """Return a Document."""


class EvidencePort(Protocol):
    def get_evidence(self, evidence_id: str) -> JsonObject:
        """Return an Evidence."""

    def extract_facts(
        self, document_ids: list[str], field_codes: list[str]
    ) -> list[JsonObject]:
        """Return Facts or FieldValues with source evidence IDs."""


class CalculationPort(Protocol):
    def calculate(
        self,
        formula_code: str,
        input_fact_ids: list[str],
        input_calculation_ids: list[str],
        parameters: JsonObject,
    ) -> JsonObject:
        """Return a Calculation; use Decimal for numerical work."""


class ModelPort(Protocol):
    def infer_json(
        self,
        task_code: str,
        evidence_ids: list[str],
        schema_name: str,
        context: JsonObject,
    ) -> JsonObject:
        """Return validated JSON and log model/prompt versions."""


class EventPort(Protocol):
    def append_event(self, run_id: str, event: JsonObject) -> None:
        """Append a state, file, tool, model, calculation, export, or error event."""


@dataclass(frozen=True)
class TaskContext:
    run_id: str
    document_ids: tuple[str, ...]
    documents: DocumentPort
    evidence: EvidencePort
    calculations: CalculationPort
    model: ModelPort
    events: EventPort


class TaskPlugin(Protocol):
    task_type: str

    def run(self, context: TaskContext, task_input: JsonObject) -> JsonObject:
        """Return data, evidence_ids, calculations, review_items, warnings.

        The orchestrator wraps these fields in TaskResult and sets common metadata.
        """
