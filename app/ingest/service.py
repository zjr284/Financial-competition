"""文件登记：登记 + 哈希 + 版本 + 快照。

契约要点（docs/接口约定.md 第 2 节 Document）：
  * 同一文件内容哈希可重复上传，但须返回相同或明确关联的文档版本
  * 更正公告是新文档，不能覆盖旧文件
因此文档身份 = (sha256, source_url, disclosure_date)：
  同内容同来源 -> 稳定 document_id；
  同内容不同来源/披露日 -> 新 document_id，但共享同一个内容寻址快照。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from app.common.contracts import validate_contract
from app.common.ids import make_id, now_iso, sha256_bytes
from app.config import ALLOWED_MEDIA_TYPES, Settings
from app.errors import AppError, unsupported, validation_error
from app.storage.db import Store


_PDF_PAGE_OBJ = re.compile(rb"/Type\s*/Page[^s]")
_CSV_NEWLINE = re.compile(rb"\r\n|\r|\n")


def count_pdf_pages(payload: bytes) -> int:
    """先试 pypdf（含压缩对象流），失败退回正则统计 /Type /Page。"""
    try:  # pragma: no cover - 依赖可选库
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(_BytesReader(payload))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return max(1, len(reader.pages))
        return max(1, len(reader.pages))
    except Exception:
        pass
    direct = len(_PDF_PAGE_OBJ.findall(payload))
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", payload, re.DOTALL)]
    if counts:
        # /Pages 根节点的 Count 是权威页数
        return max(1, max(counts))
    return max(1, direct)


class _BytesReader:
    """pypdf 需要 file-like；直接包一层避免临时文件。"""

    def __init__(self, payload: bytes) -> None:
        import io

        self._io = io.BytesIO(payload)

    def read(self, *args: Any) -> bytes:
        return self._io.read(*args)

    def seek(self, *args: Any) -> int:
        return self._io.seek(*args)

    def tell(self) -> int:
        return self._io.tell()

    def close(self) -> None:
        self._io.close()


def _row_count(payload: bytes) -> int:
    if not payload:
        return 1
    return max(1, len([line for line in _CSV_NEWLINE.split(payload.rstrip(b"\r\n"))]))


def _xlsx_row_count(path: Path) -> int:
    try:  # pragma: no cover - 依赖可选库
        import openpyxl  # type: ignore

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return max(1, max((sheet.max_row or 1) for sheet in book.worksheets))
        finally:
            book.close()
    except Exception:
        return 1


def document_key(sha256: str, source_url: str | None, disclosure_date: str | None) -> str:
    return f"{sha256}|{source_url or ''}|{disclosure_date or ''}"


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MEDIA_TYPES:
        raise unsupported(
            "文件类型不受支持，仅允许 PDF、TXT、CSV、XLSX",
            filename=filename,
            allowed=sorted(ALLOWED_MEDIA_TYPES),
        )
    guessed = mimetypes.guess_type(filename)[0]
    return ALLOWED_MEDIA_TYPES[suffix] if guessed is None else ALLOWED_MEDIA_TYPES[suffix]


class DocumentService:
    """实现 contracts/ports.py 的 DocumentPort（ingest/get 部分）。"""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------ 登记
    def ingest_file(
        self,
        path: str,
        metadata: dict[str, Any] | None = None,
        *,
        content: bytes | None = None,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        source = Path(path)
        filename = str(metadata.get("filename") or source.name)
        if not filename:
            raise validation_error("缺少 filename")
        media_type = media_type_for(filename)

        payload = content if content is not None else source.read_bytes()
        if not payload:
            raise validation_error("空文件不可登记", filename=filename)
        if len(payload) > self.settings.max_file_bytes:
            raise AppError(
                "FILE_TOO_LARGE",
                "文件超过部署上限",
                details={
                    "filename": filename,
                    "size_bytes": len(payload),
                    "max_file_bytes": self.settings.max_file_bytes,
                },
            )

        sha256 = sha256_bytes(payload)
        source_url = metadata.get("source_url")
        disclosure_date = metadata.get("disclosure_date")
        if disclosure_date is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(disclosure_date)):
            raise validation_error("disclosure_date 必须是 YYYY-MM-DD", disclosure_date=disclosure_date)

        key = document_key(sha256, source_url, disclosure_date)
        existing = self.store.find_document_by_key(key)
        if existing is not None:
            # 稳定 ID：重复登记返回同一条记录，不新增版本
            self.store.put_blob(sha256, payload)
            return self.to_document(existing)

        if media_type == "application/pdf":
            page_count = count_pdf_pages(payload)
            if page_count > self.settings.max_pdf_pages:
                raise AppError(
                    "FILE_TOO_LARGE",
                    "PDF 页数超过部署上限",
                    details={"filename": filename, "page_count": page_count, "max_pdf_pages": self.settings.max_pdf_pages},
                )
        elif media_type == "text/csv":
            page_count = _row_count(payload)
        elif media_type.startswith("application/vnd.openxmlformats"):
            self.store.put_blob(sha256, payload)
            page_count = _xlsx_row_count(self.store.blob_path(sha256))
        else:
            page_count = 1

        document_id = make_id("doc", sha256, source_url, disclosure_date, length=20)
        row = {
            "document_id": document_id,
            "doc_key": key,
            "filename": filename,
            "media_type": media_type,
            "sha256": sha256,
            "source_url": source_url,
            "disclosure_date": disclosure_date,
            "ingested_at": ingested_at or now_iso(),
            "page_count": page_count,
            "parse_status": "pending",
            "size_bytes": len(payload),
            "parse_manifest": None,
        }
        self.store.put_blob(sha256, payload)
        self.store.insert_document(row)
        return self.to_document(row)

    # ------------------------------------------------------------ 查询
    def to_document(self, row: dict[str, Any]) -> dict[str, Any]:
        """DB 行 -> 契约 Document（只输出契约字段）。"""
        document = {
            "document_id": row["document_id"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "sha256": row["sha256"],
            "source_url": row["source_url"],
            "disclosure_date": row["disclosure_date"],
            "ingested_at": row["ingested_at"],
            "page_count": int(row["page_count"]),
            "parse_status": row["parse_status"],
        }
        validate_contract("Document", document, label="Document")
        return document

    def get_document(self, document_id: str) -> dict[str, Any]:
        return self.to_document(self.store.get_document_row(document_id))

    def list_documents(self) -> list[dict[str, Any]]:
        return [self.to_document(row) for row in self.store.list_documents()]

    def get_document_row(self, document_id: str) -> dict[str, Any]:
        return self.store.get_document_row(document_id)

    def get_content(self, document_id: str) -> tuple[bytes, dict[str, Any]]:
        row = self.store.get_document_row(document_id)
        return self.store.read_blob(row["sha256"]), self.to_document(row)

    def mark_parsed(self, document_id: str, page_count: int, status: str, manifest: dict[str, Any]) -> None:
        self.store.update_document_parse(document_id, page_count=page_count, parse_status=status, manifest=manifest)


def upload_metadata(
    filename: str,
    *,
    source_url: str | None = None,
    disclosure_date: str | None = None,
) -> dict[str, Any]:
    return {"filename": filename, "source_url": source_url, "disclosure_date": disclosure_date}
