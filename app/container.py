"""依赖装配：把 store / ingest / parse / events / models / orchestrator 组装成 App。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.common.contracts import schema
from app.config import Settings, get_settings
from app.errors import not_found, validation_error
from app.ingest.service import DocumentService
from app.logging.events import EventLog
from app.models.adapter import OfflineRuleModel
from app.models.ocr import OcrAdapter
from app.orchestrator.service import Orchestrator
from app.parse import pdfpos
from app.parse.service import ParseService
from app.storage.db import Store


@dataclass
class App:
    """一次进程装配出的服务集合；HTTP 层与 CLI 共用。"""

    settings: Settings
    store: Store
    documents: DocumentService
    parse: ParseService
    events: EventLog
    model: OfflineRuleModel
    ocr: OcrAdapter
    orchestrator: Orchestrator

    def close(self) -> None:
        self.store.close()

    # ---- 统一入口，便于 HTTP/CLI/测试复用 ----
    def upload_document(
        self,
        filename: str,
        payload: bytes,
        *,
        source_url: str | None = None,
        disclosure_date: str | None = None,
    ) -> dict[str, Any]:
        from app.common.ids import sha256_bytes
        from pathlib import Path

        document = self.documents.ingest_file(
            Path(filename).name,
            {"filename": Path(filename).name, "source_url": source_url, "disclosure_date": disclosure_date},
            content=payload,
        )
        # 文件访问必须留痕：run_id 复用 document_id（契约 Id 要求 run_ 前缀，故用等长稳定别名）
        self.events.append(
            f"run_ingest_{document['document_id'].split('_', 1)[1]}",
            event_type="file_access",
            message=f"登记文件 {document['filename']}（{len(payload)} 字节）",
            tool_name="ingest_file",
            input_hash=sha256_bytes(payload),
            output_hash=document["sha256"],
        )
        return document

    def upload_path(self, path: str, *, source_url: str | None = None, disclosure_date: str | None = None) -> dict[str, Any]:
        from pathlib import Path

        file = Path(path)
        payload = file.read_bytes()
        return self.upload_document(
            file.name, payload, source_url=source_url, disclosure_date=disclosure_date
        )

    def get_document(self, document_id: str) -> dict[str, Any]:
        return self.documents.get_document(document_id)

    def get_document_content(self, document_id: str) -> tuple[bytes, dict[str, Any]]:
        return self.documents.get_content(document_id)

    def parse_document(self, document_id: str, *, force: bool = False) -> dict[str, Any]:
        return self.parse.parse_document(document_id, force=force).to_dict()

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        return self.parse.get_evidence(evidence_id)

    def create_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.orchestrator.create_run(request)

    def execute_run(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        return self.orchestrator.execute_run(run_id, force=force)

    def run_task(self, request: dict[str, Any], *, sync: bool = True) -> dict[str, Any]:
        """RunRequest -> Run（同步执行，便于 CLI 与测试；API 层异步触发）。"""
        run = self.orchestrator.create_run(request)
        if sync:
            self.orchestrator.execute_run(run["run_id"])
            return self.orchestrator.get_run(run["run_id"])
        return run

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.orchestrator.get_run(run_id)

    def get_result(self, run_id: str) -> dict[str, Any]:
        return self.orchestrator.get_result(run_id)

    def get_events(self, run_id: str, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        return self.orchestrator.get_events(run_id, cursor=cursor, limit=limit)

    def health(self) -> dict[str, Any]:
        backends = pdfpos.available_backends()
        model_health = self.model.health()
        return {
            "status": "ok" if model_health["available"] and (backends["pdfminer"] or backends["pypdf"]) else "degraded",
            "api_version": "1.0.0",
            "model_available": bool(model_health["available"]),
            "details": {
                "parse_backends": backends,
                "model": model_health,
                "ocr": self.ocr.describe(),
                "limits": self.settings.describe(),
                "schema_defs": len(schema()["$defs"]),
            },
        }


_CACHED: App | None = None


def build_app(settings: Settings | None = None) -> App:
    active = settings or get_settings()
    store = Store(active)
    documents = DocumentService(store, active)
    parse = ParseService(store, documents, active)
    events = EventLog(store, active)
    model = OfflineRuleModel(active)
    ocr = OcrAdapter(active)
    orchestrator = Orchestrator(store, documents, parse, events, active, model=model)
    return App(active, store, documents, parse, events, model, ocr, orchestrator)


def get_app(settings: Settings | None = None, *, refresh: bool = False) -> App:
    global _CACHED
    if _CACHED is None or refresh or settings is not None:
        _CACHED = build_app(settings)
    return _CACHED
