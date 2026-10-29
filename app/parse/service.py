"""解析服务：PDF / TXT / CSV / XLSX -> DocumentIndex + Evidence。

对应 contracts/ports.py 的 parse_document(document_id) -> ParseManifest：
返回 evidence_ids 与 warnings；解析产物落库，下游任务只读索引与证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.contracts import validate_contract
from app.common.ids import digest_of, truncate
from app.config import Settings
from app.errors import AppError
from app.ingest.service import DocumentService
from app.parse import pdfpos, sources
from app.parse.index import DocumentIndex, LineRecord
from app.models.ocr import OcrAdapter
from app.storage.db import Store


@dataclass
class ParseManifest:
    """契约 ports.parse_document 的返回结构。"""

    document_id: str
    page_count: int
    status: str
    backend: str
    evidence_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False
    first_evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "status": self.status,
            "backend": self.backend,
            "evidence_ids": self.evidence_ids,
            "evidence_count": len(self.evidence_ids),
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


class ParseService:
    def __init__(self, store: Store, documents: DocumentService, settings: Settings) -> None:
        self.store = store
        self.documents = documents
        self.settings = settings
        self.ocr = OcrAdapter(settings)

    # ------------------------------------------------------------ 主入口
    def parse_document(self, document_id: str, *, force: bool = False) -> ParseManifest:
        row = self.store.get_document_row(document_id)
        media_type = row["media_type"]

        if not force:
            cached = self._cached_manifest(row)
            if cached is not None:
                return cached

        payload = self.store.read_blob(row["sha256"])
        index, page_count, warnings, status, needs_review = self._build_index(document_id, media_type, payload)

        evidence_rows = index.all_evidence()
        for evidence in evidence_rows:
            validate_contract("Evidence", evidence, label="Evidence")
        self.store.insert_evidence(evidence_rows)

        manifest = ParseManifest(
            document_id=document_id,
            page_count=page_count,
            status=status,
            backend=index.backend,
            evidence_ids=[evidence["evidence_id"] for evidence in evidence_rows],
            warnings=warnings,
            needs_review=needs_review,
            first_evidence_id=evidence_rows[0]["evidence_id"] if evidence_rows else None,
        )
        stored = manifest.to_dict()
        stored["index"] = index.to_manifest()
        stored["source_sha256"] = row["sha256"]
        self.documents.mark_parsed(document_id, page_count, status, stored)
        return manifest

    # ------------------------------------------------------------ 索引
    def build_index(self, document_id: str) -> DocumentIndex:
        row = self.store.get_document_row(document_id)
        cached = self._cached_manifest(row)
        if cached is None:
            self.parse_document(document_id)
            row = self.store.get_document_row(document_id)
            cached = self._cached_manifest(row)
        assert cached is not None
        raw = self.store.get_document_row(document_id)["parse_manifest"]
        import json

        manifest = json.loads(raw)
        return DocumentIndex.from_manifest(manifest["index"])

    def load_index(self, row: dict[str, Any]) -> DocumentIndex | None:
        """从已缓存的 parse_manifest 恢复索引，不重新解析原文件。"""
        if not row.get("parse_manifest"):
            return None
        import json

        try:
            manifest = json.loads(row["parse_manifest"])
        except (ValueError, TypeError):
            return None
        if "index" not in manifest:
            return None
        return DocumentIndex.from_manifest(manifest["index"])

    # ------------------------------------------------------------ 内部
    def _cached_manifest(self, row: dict[str, Any]) -> ParseManifest | None:
        if not row.get("parse_manifest"):
            return None
        import json

        try:
            manifest = json.loads(row["parse_manifest"])
        except (ValueError, TypeError):
            return None
        if "index" not in manifest:
            return None
        return ParseManifest(
            document_id=row["document_id"],
            page_count=int(manifest.get("page_count", row["page_count"])),
            status=manifest.get("status", row["parse_status"]),
            backend=manifest.get("backend", "cache"),
            evidence_ids=list(manifest.get("evidence_ids", [])),
            warnings=list(manifest.get("warnings", [])),
            needs_review=bool(manifest.get("needs_review", False)),
            first_evidence_id=(manifest.get("evidence_ids") or [None])[0],
        )

    def _build_index(
        self, document_id: str, media_type: str, payload: bytes
    ) -> tuple[DocumentIndex, int, list[str], str, bool]:
        warnings: list[str] = []
        needs_review = False

        if media_type == "application/pdf":
            extraction = pdfpos.extract_pdf(payload)
            index = DocumentIndex.from_pdf(document_id, extraction)
            warnings.extend(index.warnings)
            page_count = max(1, index.page_count)
            empty_pages = [page.page for page in index.pages if not page.text.strip()]
            if empty_pages:
                warnings.append(f"以下页面无文本层，数字不清时需人工复核：{empty_pages[:20]}")
                needs_review = True
                if self.ocr.available:
                    try:
                        provenance = self.ocr.provenance()
                        recognized, ocr_warnings = self.ocr.recognize_pdf(payload, empty_pages)
                        warnings.extend(ocr_warnings)
                        for page in index.pages:
                            for result in recognized.get(page.page, []):
                                page.lines.append(LineRecord(
                                    page=page.page, kind="ocr", text=result["text"], bbox=result["bbox"],
                                    char_start=0, char_end=0, confidence=result["confidence"]))
                            page.rebuild_text()
                        if any(recognized.values()):
                            index.backend += "+rapidocr"
                            warnings.extend([f"OCR 来源：{provenance}",
                                             "OCR 识别结果均须人工复核；检测框不等于财务表格单元格，未恢复表格拓扑"])
                    except Exception as exc:
                        warnings.append(f"OCR 初始化或渲染失败（{type(exc).__name__}），仍需人工复核")
                else:
                    warnings.append(self.ocr.availability.reason)
                if not self.settings.ocr_enabled:
                    warnings.append("FINSCOPE_OCR_ENABLED=0，本次未尝试 OCR")
            if not any(page.text.strip() for page in index.pages):
                raise AppError(
                    "DOCUMENT_PARSE_FAILED",
                    "该 PDF 无文本层且 OCR 未产生文字，无法生成带坐标的证据",
                    details={"document_id": document_id, "page_count": page_count, "warnings": warnings},
                )
            remaining = [p.page for p in index.pages if not p.text.strip()]
            if remaining:
                warnings.append(f"仍有未识别页面：{remaining[:20]}，当前仅为部分解析结果")
            status = "parsed"
            low_confidence = [
                line.bbox
                for parsed_page in index.pages
                for line in parsed_page.lines
                if line.confidence < self.settings.ocr_confidence_floor
            ]
            if low_confidence:
                needs_review = True
                warnings.append(f"存在 {len(low_confidence)} 处低置信度文本，相关字段必须复核")
        elif media_type == "text/plain":
            index = sources.parse_text(document_id, payload)
            page_count = 1
            status = "parsed"
            warnings.extend(index.warnings)
            needs_review = True
        elif media_type == "text/csv":
            index = sources.parse_csv(document_id, payload)
            page_count = 1
            status = "parsed"
            warnings.extend(index.warnings)
        else:
            index, _sheets = sources.parse_xlsx(document_id, payload)
            page_count = max(1, index.page_count)
            status = "parsed"
            warnings.extend(index.warnings)

        if not index.all_evidence(max_rows=1):
            raise AppError(
                "DOCUMENT_PARSE_FAILED",
                "解析未产生任何证据，无法支撑可溯源输出",
                details={"document_id": document_id, "media_type": media_type},
            )
        index.warnings = list(dict.fromkeys(warnings))
        return index, page_count, list(dict.fromkeys(warnings)), status, needs_review

    # ------------------------------------------------------------ 证据
    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        row = self.store.get_evidence_row(evidence_id)
        import json

        payload: dict[str, Any] = {
            "evidence_id": row["evidence_id"],
            "document_id": row["document_id"],
            "page": int(row["page"]),
            "bbox": json.loads(row["bbox"]),
            "text": row["text"],
            "extraction_method": row["extraction_method"],
            "confidence": float(row["confidence"]),
        }
        if row["table_id"] is not None or row["row"] is not None or row["column_index"] is not None:
            payload["table_id"] = row["table_id"]
            payload["row"] = None if row["row"] is None else int(row["row"])
            payload["column"] = None if row["column_index"] is None else int(row["column_index"])
        validate_contract("Evidence", payload, label="Evidence")
        return payload

    def list_evidence(self, document_id: str, *, page: int | None = None) -> list[dict[str, Any]]:
        import json

        rows = self.store.list_evidence(document_id, page=page)
        return [
            {
                "evidence_id": row["evidence_id"],
                "document_id": row["document_id"],
                "page": int(row["page"]),
                "bbox": json.loads(row["bbox"]),
                "text": row["text"],
                "extraction_method": row["extraction_method"],
                "confidence": float(row["confidence"]),
            }
            for row in rows
        ]

    def index_digest(self, document_id: str) -> str:
        """索引摘要，写入 Run.input_hash 便于离线重放比对。"""
        row = self.store.get_document_row(document_id)
        manifest = row.get("parse_manifest")
        return digest_of({"document_id": document_id, "parse_manifest": manifest})

    @staticmethod
    def describe_warnings(manifest: ParseManifest) -> str:
        return truncate("; ".join(manifest.warnings), 200)
