"""Role B plugin adapter for member A's current orchestrator context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.facts.reviewed_store import ReviewedFactPort
from app.tools import FinancialCalculator
from contracts.ports import TaskContext


class _DocumentBridge:
    def __init__(self, documents: Any, parse: Any):
        self.documents = documents
        self.parse = parse

    def get_document(self, document_id: str) -> dict[str, Any]:
        return self.documents.get_document(document_id)

    def parse_document(self, document_id: str) -> dict[str, Any]:
        result = self.parse.parse_document(document_id)
        return result.to_dict() if hasattr(result, "to_dict") else result


class ACompatibleTask:
    """Supply ports absent from A's _TaskContext without mutating A's code."""

    def __init__(self, plugin: Any, manifest_path: str | Path):
        self.plugin = plugin
        self.task_type = plugin.task_type
        self.manifest_path = Path(manifest_path)

    def run(self, context: Any, task_input: dict[str, Any]) -> dict[str, Any]:
        documents = _DocumentBridge(context.documents, context.parse)
        evidence = ReviewedFactPort(self.manifest_path, documents, context.parse)
        bridged = TaskContext(
            run_id=context.run_id,
            document_ids=tuple(context.document_ids),
            documents=documents,
            evidence=evidence,
            calculations=FinancialCalculator(),
            model=context.model,
            events=context.events,
        )
        try:
            return self.plugin.run(bridged, task_input)
        except Exception as exc:
            # A's current Orchestrator catches AppError only. Convert failures
            # at this boundary so the Run becomes failed instead of hanging in
            # running. Do not put source text or model output in the public log.
            from app.errors import AppError
            import jsonschema

            if isinstance(exc, AppError):
                raise
            code = "VALIDATION_ERROR" if isinstance(
                exc, (ValueError, KeyError, TypeError, jsonschema.ValidationError)
            ) else "INTERNAL_ERROR"
            raise AppError(
                code,
                "角色 B 任务输入、事实来源或模型结果未通过处理",
                details={"task_type": self.task_type, "error_type": type(exc).__name__},
            ) from exc


def install_role_b(app: Any, manifest_path: str | Path | None = None) -> None:
    """Register B's four tasks on an A App instance before creating a run.

    The manifest defaults to A's private data directory. The offline model in
    A's current branch cannot produce IndustryData/MemoData; those tasks require
    a suitable model port and should remain review-only until it is available.
    """
    from app.tasks.role_b import get_role_b_tasks

    path = Path(manifest_path) if manifest_path is not None else app.settings.data_dir / "reviewed_facts.json"
    app.orchestrator.task_registry.update(
        {code: ACompatibleTask(plugin, path) for code, plugin in get_role_b_tasks().items()}
    )
