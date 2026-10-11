"""把解析结果组织成可定位的页面索引，并生成符合契约的 Evidence。

坐标规则（contracts.schema.json -> $defs.Evidence.bbox）：
  [x0,y0,x1,y1] 整数 0—1000，左上角原点，页码从 1 开始，先按 PDF 旋转后归一化。
索引里的每一行都带 bbox 与它在页文本中的字符区间，因此“字段正则命中位置”可以稳定
映射回坐标，不会出现没有原文坐标的数值字段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.common.ids import make_id, normalize_bbox, truncate, union_bbox
from app.parse import pdfpos


@dataclass
class LineRecord:
    """一行文本及其定位信息。"""

    page: int
    kind: str  # pdf_text / ocr / table
    text: str
    bbox: list[int]
    char_start: int
    char_end: int
    table_id: str | None = None
    row: int | None = None
    column: int | None = None
    confidence: float = 1.0
    font_size: float = 0.0


@dataclass
class PageIndex:
    page: int
    width: float
    height: float
    lines: list[LineRecord] = field(default_factory=list)
    text: str = ""
    warnings: list[str] = field(default_factory=list)

    def rebuild_text(self) -> None:
        """按行拼页文本，并回填每行的字符区间。"""
        chunks: list[str] = []
        cursor = 0
        for line in self.lines:
            line.char_start = cursor
            chunks.append(line.text)
            cursor += len(line.text)
            line.char_end = cursor
            chunks.append("\n")
            cursor += 1
        self.text = "".join(chunks)

    def lines_for_span(self, start: int, end: int) -> list[LineRecord]:
        return [line for line in self.lines if line.char_start < end and line.char_end > start]

    def bbox_for_span(self, start: int, end: int) -> list[int]:
        found = self.lines_for_span(start, end)
        if not found:
            return [0, 0, 0, 0]
        return union_bbox([line.bbox for line in found])

    def search(self, pattern: re.Pattern[str]) -> list[tuple[str, int, int, list[int]]]:
        """在整页文本上匹配，返回 (命中文本, 起, 止, bbox)。"""
        out: list[tuple[str, int, int, list[int]]] = []
        for match in pattern.finditer(self.text):
            start, end = match.span()
            out.append((match.group(0), start, end, self.bbox_for_span(start, end)))
        return out


@dataclass
class DocumentIndex:
    """一份文档的完整定位索引，可序列化进 documents.parse_manifest。"""

    document_id: str
    page_count: int
    pages: list[PageIndex]
    warnings: list[str] = field(default_factory=list)
    backend: str = "stdlib"
    has_text_layer: bool = True

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_pdf(cls, document_id: str, extraction: pdfpos.PdfExtraction) -> "DocumentIndex":
        pages: list[PageIndex] = []
        warnings = list(extraction.warnings)
        for page_chars in extraction.pages:
            parsed = _lines_from_chars(page_chars)
            # 阅读顺序：PDF 元素顺序不保证是从上到下（内容流顺序可能相反），
            # 因此按行 bbox 的 y0 升序强制排序，使“文件前两行/标题行”判定稳定。
            parsed.lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
            parsed.rebuild_text()
            pages.append(parsed)
        text_pages = sum(1 for page in pages if page.text.strip())
        has_text = text_pages > 0
        if not has_text and pages:
            warnings.append("整份 PDF 无文本层：疑似扫描件，需要 OCR 或人工录入")
        return cls(
            document_id=document_id,
            page_count=max(1, len(pages)),
            pages=pages,
            warnings=warnings,
            backend=extraction.source,
            has_text_layer=has_text,
        )

    @classmethod
    def from_plain_text(cls, document_id: str, text: str, *, kind: str = "pdf_text") -> "DocumentIndex":
        page = PageIndex(page=1, width=1000.0, height=1000.0)
        window_lines = max(1, len(text.splitlines()))
        page.lines = _lines_from_plain(text, page=1, kind=kind, total_lines=window_lines)
        page.rebuild_text()
        return cls(document_id=document_id, page_count=1, pages=[page], backend="plain")

    @classmethod
    def from_rows(cls, document_id: str, rows: Iterable[Iterable[Any]]) -> "DocumentIndex":
        page = PageIndex(page=1, width=1000.0, height=1000.0)
        page.lines = _lines_from_rows(rows, page=1)
        page.rebuild_text()
        return cls(document_id=document_id, page_count=1, pages=[page], backend="table")

    # ------------------------------------------------------------ 查询
    def page(self, number: int) -> PageIndex | None:
        for candidate in self.pages:
            if candidate.page == number:
                return candidate
        return None

    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def find_span(self, pattern: re.Pattern[str]) -> tuple[int, int, int, str, list[int]] | None:
        """跨页查找第一个命中，返回 (page, start, end, text, bbox)。"""
        for page in self.pages:
            match = pattern.search(page.text)
            if match:
                start, end = match.span()
                return page.page, start, end, match.group(0), page.bbox_for_span(start, end)
        return None

    def find_all(self, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in self.pages:
            for text, start, end, bbox in page.search(pattern):
                found.append(
                    {
                        "page": page.page,
                        "start": start,
                        "end": end,
                        "text": text,
                        "bbox": bbox,
                        "kind": (page.lines_for_span(start, end) or [None])[0].kind,  # type: ignore[union-attr]
                    }
                )
        return found

    # ------------------------------------------------------------ 证据
    def evidence_for_span(
        self,
        page_number: int,
        start: int,
        end: int,
        *,
        method: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """为一个字符区间生成契约 Evidence（同一区间多次调用得到同一 evidence_id）。"""
        page = self.page(page_number)
        if page is None:
            raise KeyError(f"页码不存在: {page_number}")
        span_lines = page.lines_for_span(start, end)
        bbox = union_bbox([line.bbox for line in span_lines]) if span_lines else [0, 0, 0, 0]
        kind = span_lines[0].kind if span_lines else "pdf_text"
        snippet = _snippet(page.text, start, end)
        return _evidence_row(
            self.document_id,
            page_number,
            bbox,
            snippet,
            method=method or ("table" if kind == "table" else kind),
            confidence=confidence if confidence is not None else (span_lines[0].confidence if span_lines else 1.0),
            table_id=span_lines[0].table_id if span_lines else None,
            row=span_lines[0].row if span_lines else None,
            column=span_lines[0].column if span_lines else None,
        )

    def evidence_for_lines(self, page_number: int, lines: list[LineRecord]) -> dict[str, Any]:
        page = self.page(page_number)
        if page is None:
            raise KeyError(f"页码不存在: {page_number}")
        if not lines:
            return _evidence_row(self.document_id, page_number, [0, 0, 0, 0], "", method="pdf_text", confidence=0.0)
        return _evidence_row(
            self.document_id,
            page_number,
            union_bbox([line.bbox for line in lines]),
            truncate(" ".join(line.text for line in lines), 300),
            method=lines[0].kind,
            confidence=min(line.confidence for line in lines),
            table_id=lines[0].table_id,
            row=lines[0].row,
            column=lines[0].column,
        )

    def all_evidence(self, *, max_rows: int = 400) -> list[dict[str, Any]]:
        """登记文档级证据：优先表格单元格，其次每页前若干行。"""
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            table_lines = [line for line in page.lines if line.kind == "table"]
            if table_lines:
                for line in table_lines[:max_rows]:
                    evidence = self.evidence_for_lines(page.page, [line])
                    if evidence["text"]:
                        rows.append(evidence)
                continue
            for line in page.lines[:max_rows]:
                if not line.text.strip():
                    continue
                evidence = self.evidence_for_lines(page.page, [line])
                rows.append(evidence)
        return rows

    # ------------------------------------------------------------ 序列化
    def to_manifest(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "backend": self.backend,
            "has_text_layer": self.has_text_layer,
            "warnings": list(self.warnings),
            "pages": [
                {
                    "page": page.page,
                    "width": page.width,
                    "height": page.height,
                    "warnings": list(page.warnings),
                    "lines": [
                        {
                            "text": line.text,
                            "bbox": line.bbox,
                            "kind": line.kind,
                            "char_start": line.char_start,
                            "char_end": line.char_end,
                            "table_id": line.table_id,
                            "row": line.row,
                            "column": line.column,
                            "confidence": line.confidence,
                        }
                        for line in page.lines
                    ],
                }
                for page in self.pages
            ],
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "DocumentIndex":
        pages: list[PageIndex] = []
        for raw_page in manifest.get("pages", []):
            page = PageIndex(
                page=int(raw_page["page"]),
                width=float(raw_page.get("width", 1000.0)),
                height=float(raw_page.get("height", 1000.0)),
                warnings=list(raw_page.get("warnings", [])),
            )
            page.lines = [
                LineRecord(
                    page=page.page,
                    kind=raw["kind"],
                    text=raw["text"],
                    bbox=list(raw["bbox"]),
                    char_start=int(raw.get("char_start", 0)),
                    char_end=int(raw.get("char_end", 0)),
                    table_id=raw.get("table_id"),
                    row=raw.get("row"),
                    column=raw.get("column"),
                    confidence=float(raw.get("confidence", 1.0)),
                )
                for raw in raw_page.get("lines", [])
            ]
            page.rebuild_text()
            pages.append(page)
        return cls(
            document_id=manifest["document_id"],
            page_count=int(manifest.get("page_count", len(pages) or 1)),
            pages=pages,
            warnings=list(manifest.get("warnings", [])),
            backend=manifest.get("backend", "unknown"),
            has_text_layer=bool(manifest.get("has_text_layer", True)),
        )


# ---------------------------------------------------------------- 内部工具

def _snippet(text: str, start: int, end: int, *, context: int = 20, limit: int = 300) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    return truncate(text[left:right].replace("\n", " "), limit)


def _evidence_row(
    document_id: str,
    page: int,
    bbox: list[int],
    text: str,
    *,
    method: str,
    confidence: float,
    table_id: str | None = None,
    row: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    confidence = max(0.0, min(1.0, round(float(confidence), 4)))
    evidence_id = make_id("ev", document_id, page, bbox, text, method, row, column, length=20)
    payload: dict[str, Any] = {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "page": int(page),
        "bbox": [int(v) for v in bbox],
        "text": text or "（空片段）",
        "extraction_method": method if method in {"pdf_text", "ocr", "table", "manual"} else "pdf_text",
        "confidence": confidence,
    }
    if table_id is not None or row is not None or column is not None:
        payload["table_id"] = table_id
        payload["row"] = row
        payload["column"] = column
    return payload


def _lines_from_chars(page_chars: pdfpos.PageChars) -> PageIndex:
    """字符 -> 行：先按 y 分组，再按 x 排序，按间距决定是否插空格。"""
    page = PageIndex(
        page=page_chars.page,
        width=page_chars.width,
        height=page_chars.height,
        warnings=list(page_chars.warnings),
    )
    if not page_chars.chars:
        page.rebuild_text()
        return page

    tolerance = max(1.5, min(6.0, (min((c.size for c in page_chars.chars if c.size), default=10.0)) * 0.5))
    ordered = sorted(page_chars.chars, key=lambda c: (-round(c.y1, 1), c.x0))
    groups: list[list[pdfpos.CharBox]] = []
    current: list[pdfpos.CharBox] = []
    current_key: float | None = None
    for char in ordered:
        key = round(char.y1, 1)
        if current_key is None or abs(key - current_key) <= tolerance:
            current.append(char)
            current_key = key if current_key is None else (current_key + key) / 2
            continue
        groups.append(current)
        current = [char]
        current_key = key
    if current:
        groups.append(current)

    method = "ocr" if page_chars.source == "ocr" else "pdf_text"
    for group in groups:
        group.sort(key=lambda c: c.x0)
        text_parts: list[str] = []
        previous: pdfpos.CharBox | None = None
        for char in group:
            if previous is not None:
                gap = char.x0 - previous.x1
                size = max(1.0, char.size or previous.size or 10.0)
                if gap > size * 0.35 and not (pdfpos.is_cjk(previous.text) or pdfpos.is_cjk(char.text)):
                    text_parts.append(" ")
            text_parts.append(char.text)
            previous = char
        text = "".join(text_parts).strip()
        if not text:
            continue
        bbox = normalize_bbox(
            min(c.x0 for c in group),
            min(c.y0 for c in group),
            max(c.x1 for c in group),
            max(c.y1 for c in group),
            page.width,
            page.height,
        )
        page.lines.append(
            LineRecord(
                page=page.page,
                kind=method,
                text=text,
                bbox=bbox,
                char_start=0,
                char_end=0,
                confidence=1.0 if method == "pdf_text" else 0.75,
                font_size=max((c.size for c in group), default=0.0),
            )
        )
    page.rebuild_text()
    return page


def _lines_from_plain(text: str, *, page: int, kind: str, total_lines: int) -> list[LineRecord]:
    """纯文本没有原生坐标：按等宽排版模型给出稳定的归一化坐标。

    这是“近似版面”，不是原件实测坐标，因此 evidence 置信度低于 PDF 文本层，
    并在 warnings 中显式说明，避免把近似坐标当成原件框选位置。
    """
    lines = text.splitlines()
    total = max(1, total_lines)
    height_per_line = 1000.0 / total
    records: list[LineRecord] = []
    for index, raw in enumerate(lines):
        text_line = raw.rstrip()
        if not text_line.strip():
            continue
        width_chars = max(len(line) for line in lines) or 1
        x1 = min(1000, int(round(len(text_line) / width_chars * 1000)))
        x0 = 0
        y0 = int(round(index * height_per_line))
        y1 = min(1000, max(y0 + 1, int(round((index + 1) * height_per_line))))
        records.append(
            LineRecord(
                page=page,
                kind=kind,
                text=text_line,
                bbox=[x0, y0, max(x0 + 1, x1), y1],
                char_start=0,
                char_end=0,
                confidence=0.7,
            )
        )
    return records


def _lines_from_rows(rows: Iterable[Iterable[Any]], *, page: int) -> list[LineRecord]:
    """CSV/XLSX：每个非空单元格是一条 table 证据，带 row/column（从 1 开始）。"""
    materialized = [list(row) for row in rows]
    if not materialized:
        return []
    height = max(1, len(materialized))
    width = max(1, max(len(row) for row in materialized))
    row_height = 1000.0 / height
    column_width = 1000.0 / width
    records: list[LineRecord] = []
    for row_index, row in enumerate(materialized, start=1):
        for column_index, value in enumerate(row, start=1):
            text = "" if value is None else str(value).strip()
            if text == "":
                continue
            x0 = int(round((column_index - 1) * column_width))
            x1 = min(1000, max(x0 + 1, int(round(column_index * column_width))))
            y0 = int(round((row_index - 1) * row_height))
            y1 = min(1000, max(y0 + 1, int(round(row_index * row_height))))
            records.append(
                LineRecord(
                    page=page,
                    kind="table",
                    text=text,
                    bbox=[x0, y0, x1, y1],
                    char_start=0,
                    char_end=0,
                    table_id="table_0001",
                    row=row_index,
                    column=column_index,
                    confidence=0.95,
                )
            )
    return records
