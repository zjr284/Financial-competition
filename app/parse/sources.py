"""非 PDF 来源解析：TXT / CSV / XLSX。

均输出统一的 DocumentIndex，使结构化提取任务对格式无感。
XLSX 用 zipfile + 标准 XML 解析实现，避免额外依赖；openpyxl 仅作为可选加速。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Any, Iterable

from app.parse.index import DocumentIndex

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1")


def decode_text(payload: bytes) -> tuple[str, str]:
    """返回 (文本, 使用的编码)。中文材料常见 GB18030，必须显式尝试。"""
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        return payload.decode("utf-16"), "utf-16"
    for encoding in ENCODINGS:
        try:
            return payload.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_text(document_id: str, payload: bytes) -> DocumentIndex:
    text, encoding = decode_text(payload)
    index = DocumentIndex.from_plain_text(document_id, text)
    index.backend = f"text:{encoding}"
    if encoding not in {"utf-8", "utf-8-sig", "utf-16"}:
        index.warnings.append(f"文本以 {encoding} 解码，字符可能有损，证据坐标仅为近似版面")
    else:
        index.warnings.append("TXT 无原生坐标：bbox 为按行等宽推导的近似版面，不能当作原件框选位置")
    return index


def sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def parse_csv(document_id: str, payload: bytes) -> DocumentIndex:
    text, encoding = decode_text(payload)
    dialect = sniff_dialect(text[:4096])
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [[cell.strip() for cell in row] for row in reader]
    rows = [row for row in rows if any(cell for cell in row)]
    index = DocumentIndex.from_rows(document_id, rows)
    index.backend = f"csv:{encoding}"
    index.warnings.append("CSV 坐标按行列网格推导：row/column 可靠，bbox 为版面近似值")
    return index


_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def column_index(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    import xml.etree.ElementTree as ET

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings: list[str] = []
    for item in root.findall(f"{namespace}si"):
        parts = [node.text or "" for node in item.iter(f"{namespace}t")]
        strings.append("".join(parts))
    return strings


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    import xml.etree.ElementTree as ET

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    relationships = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rels[rel.get("Id", "")] = rel.get("Target", "")
    sheets: list[tuple[str, str]] = []
    for sheet in root.iter(f"{namespace}sheet"):
        name = sheet.get("name", "sheet")
        rel_id = sheet.get(f"{relationships}id", "")
        target = rels.get(rel_id, "")
        if target and not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        if target in archive.namelist():
            sheets.append((name, target))
    return sheets


def _sheet_rows(archive: zipfile.ZipFile, target: str, shared: list[str]) -> list[list[str]]:
    import xml.etree.ElementTree as ET

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(archive.read(target))
    rows: list[list[str]] = []
    for row in root.iter(f"{namespace}row"):
        cells: dict[int, str] = {}
        for cell in row.iter(f"{namespace}c"):
            reference = cell.get("r", "")
            match = _CELL_REF.match(reference)
            index = column_index(match.group(1)) if match else len(cells) + 1
            kind = cell.get("t")
            value_node = cell.find(f"{namespace}v")
            inline = cell.find(f"{namespace}is")
            if kind == "s" and value_node is not None and value_node.text is not None:
                try:
                    text = shared[int(value_node.text)]
                except (ValueError, IndexError):
                    text = value_node.text or ""
            elif kind == "inlineStr" and inline is not None:
                text = "".join(node.text or "" for node in inline.iter(f"{namespace}t"))
            elif value_node is not None:
                text = value_node.text or ""
            else:
                text = ""
            cells[index] = text.strip()
        if not cells:
            rows.append([])
            continue
        width = max(cells)
        rows.append([cells.get(i, "") for i in range(1, width + 1)])
    return rows


def parse_xlsx(document_id: str, payload: bytes) -> tuple[DocumentIndex, list[tuple[str, list[list[str]]]]]:
    """返回 (索引, [(工作表名, 行)]）。所有工作表拼进同一索引，便于按表名定位。"""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared = _read_shared_strings(archive)
        sheets = _sheet_targets(archive)
        collected: list[tuple[str, list[list[str]]]] = []
        for name, target in sheets:
            collected.append((name, _sheet_rows(archive, target, shared)))
    combined: list[list[str]] = []
    for name, rows in collected:
        combined.append([f"#sheet:{name}"])
        combined.extend(rows)
    index = DocumentIndex.from_rows(document_id, combined)
    index.backend = "xlsx"
    index.warnings.append("XLSX 坐标按行列网格推导：row/column 可靠，bbox 为版面近似值")
    return index, collected


def rows_to_dicts(
    rows: Iterable[Iterable[str]],
    *,
    expected_headers: Iterable[str] = (),
    header_scan: int = 5,
) -> tuple[list[dict[str, str]], list[str]]:
    """把表格行转成 dict：定位表头行，其余按表头映射。

    返回 (记录列表, 表头列表)。找不到可识别表头时，用第 1 行作为表头。
    """
    materialized = [list(row) for row in rows]
    expected = {name for name in expected_headers}
    header_index = 0
    best_score = -1
    for index, row in enumerate(materialized[:header_scan]):
        cells = {cell.strip() for cell in row if cell.strip()}
        score = len(cells & expected)
        if score > best_score:
            best_score = score
            header_index = index
    header = [cell.strip() for cell in materialized[header_index]] if materialized else []
    records: list[dict[str, str]] = []
    for row in materialized[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        record: dict[str, str] = {}
        for position, cell in enumerate(row):
            key = header[position] if position < len(header) and header[position] else f"col{position + 1}"
            record[key] = cell.strip()
        records.append(record)
    return records, header
