"""文件登记与解析测试：稳定 ID、哈希快照、页码坐标、扫描件失败处理。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.common.contracts import is_valid, validate_contract
from app.errors import AppError
from app.ingest.service import count_pdf_pages
from app.parse import pdfpos
from app.parse.index import DocumentIndex


def test_same_file_twice_returns_same_document_id(app, samples_dir: Path) -> None:
    path = samples_dir / "announcement_pledge_demo.txt"
    first = app.upload_path(str(path), disclosure_date="2026-09-01")
    second = app.upload_path(str(path), disclosure_date="2026-09-01")
    assert first["document_id"] == second["document_id"]
    assert first["sha256"] == second["sha256"]
    assert is_valid("Document", first)
    # 快照按内容寻址，只存一份
    assert app.store.blob_path(first["sha256"]).exists()


def test_corrected_announcement_is_new_document_sharing_blob(app, samples_dir: Path) -> None:
    path = samples_dir / "announcement_pledge_demo.txt"
    original = app.upload_path(str(path), disclosure_date="2026-09-01")
    corrected = app.upload_path(str(path), disclosure_date="2026-09-05")
    assert original["document_id"] != corrected["document_id"]
    assert original["sha256"] == corrected["sha256"]
    assert app.store.blob_path(original["sha256"]).exists()


def test_unsupported_extension_is_rejected(app) -> None:
    with pytest.raises(AppError) as error:
        app.upload_document("恶意脚本.exe", b"MZ\x90\x00")
    assert error.value.code == "DOCUMENT_UNSUPPORTED"
    assert error.value.http_status == 415


def test_oversized_file_is_rejected(app) -> None:
    app.settings  # noqa: B018 - 保持夹具引用
    with pytest.raises(AppError) as error:
        app.documents.ingest_file(
            "big.txt",
            {"filename": "big.txt"},
            content=b"x" * (app.settings.max_file_bytes + 1),
        )
    assert error.value.code == "FILE_TOO_LARGE"
    assert error.value.http_status == 413


def test_bad_disclosure_date_is_rejected(app) -> None:
    with pytest.raises(AppError) as error:
        app.upload_document("a.txt", b"hello", disclosure_date="2026/09/01")
    assert error.value.code == "VALIDATION_ERROR"


def test_pdf_page_count_fallback(samples_dir: Path) -> None:
    payload = (samples_dir / "announcement_pledge_demo.pdf").read_bytes()
    assert count_pdf_pages(payload) == 1


def test_parse_pdf_produces_page_and_bbox(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo_cjk.pdf"))
    manifest = app.parse_document(document["document_id"])
    assert manifest["status"] == "parsed"
    assert manifest["page_count"] == 1
    assert manifest["evidence_count"] > 0

    evidence = app.parse.list_evidence(document["document_id"])
    for item in evidence:
        validate_contract("Evidence", {**item, "extraction_method": item["extraction_method"]}, label="Evidence")
        assert item["page"] >= 1
        x0, y0, x1, y1 = item["bbox"]
        assert 0 <= x0 < x1 <= 1000
        assert 0 <= y0 < y1 <= 1000

    # 抽查“本次质押股数：1,000万股”所在行的归一化坐标：
    # 页面 595x842，基线 y=690、字号 11 -> 归一化 y 约 170—184
    target = next(item for item in evidence if "本次质押股数" in item["text"])
    assert 160 <= target["bbox"][1] <= 195
    assert target["extraction_method"] == "pdf_text"
    assert target["confidence"] == 1.0


def test_parse_pdf_without_text_layer_fails_with_contract_error(app, tmp_path: Path) -> None:
    """构造一个只有空内容流的 PDF：属于“扫描件无文本层”的等价情形。"""
    payload = _blank_pdf()
    document = app.upload_document("scan_only.pdf", payload)
    with pytest.raises(AppError) as error:
        app.parse_document(document["document_id"])
    assert error.value.code == "DOCUMENT_PARSE_FAILED"
    assert error.value.http_status == 422
    validate_contract("Error", error.value.payload(), label="Error")
    assert "OCR" in error.value.message or "文本层" in error.value.message


def test_parse_txt_marks_approximate_layout(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    manifest = app.parse_document(document["document_id"])
    assert manifest["status"] == "parsed"
    assert manifest["needs_review"] is True
    assert any("近似版面" in warning for warning in manifest["warnings"])


def test_parse_csv_exposes_row_column(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "pledge_table_demo.csv"))
    app.parse_document(document["document_id"])
    evidence = app.store.list_evidence(document["document_id"])
    assert evidence, "CSV 应产生表格证据"
    table = next(item for item in evidence if item["row"] is not None)
    assert table["extraction_method"] == "table"
    assert table["row"] >= 1 and table["column_index"] >= 1


def test_parse_is_cached_and_reusable(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.pdf"))
    first = app.parse_document(document["document_id"])
    second = app.parse_document(document["document_id"])
    assert first["evidence_ids"] == second["evidence_ids"]
    row = app.store.get_document_row(document["document_id"])
    manifest = json.loads(row["parse_manifest"])
    assert "index" in manifest
    index = app.parse.load_index(row)
    assert index is not None and index.page_count == 1


def test_available_backends_reported(app) -> None:
    health = app.health()
    assert health["details"]["parse_backends"]["stdlib"] is True
    assert "ocr" in health["details"]


def test_ocr_adapter_reports_unavailable(app) -> None:
    description = app.ocr.describe()
    if not description["available"]:
        assert description["reason"]


def _reversed_stream_pdf() -> bytes:
    """内容流按“从下到上”书写的最小 PDF。

    用于验证索引层强制按阅读顺序（bbox y 升序）排列，
    否则“文件前两行/标题行”判定会在部分真实 PDF 上失效。
    """
    lines = [
        ("Final Line Three", 560.0),
        ("Second Line Two", 640.0),
        ("Title Line One", 720.0),
    ]
    body = ["BT", "/F1 12 Tf"]
    for text, y in lines:
        body.append(f"1 0 0 1 72 {y} Tm")
        body.append(f"({text}) Tj")
    body.append("ET")
    stream = ("\n".join(body) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _blank_pdf() -> bytes:
    """最小可用 PDF：一页、无文本、含 xref（模拟扫描页无文本层）。"""
    content = b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_stdlib_backend_can_read_simple_pdf(samples_dir: Path) -> None:
    """即使没有任何可选库，纯标准库后端也要能定位到文本，且保留词间空格。"""
    payload = (samples_dir / "announcement_pledge_demo.pdf").read_bytes()
    extraction = pdfpos._extract_with_stdlib(payload)
    assert extraction.page_count == 1
    text = extraction.pages[0].text
    assert "Pledged shares" in text
    first = extraction.pages[0].chars[0]
    assert first.y0 > 0 and first.x0 > 0

    index = DocumentIndex.from_pdf("doc_stdlib", extraction)
    lines = [line.text for line in index.pages[0].lines]
    # 阅读顺序必须是从上到下，否则“文件前两行”这类标题判定会失效
    assert lines[0].startswith("Sample Corp Share Pledge Announcement")
    assert any(line.startswith("Pledged shares") for line in lines)


def test_stdlib_backend_reads_cjk_identity_h_pdf(samples_dir: Path) -> None:
    """无 pdfminer 时，纯标准库后端必须能通过 ToUnicode/Identity-H 还原中文。"""
    payload = (samples_dir / "announcement_pledge_demo_cjk.pdf").read_bytes()
    extraction = pdfpos._extract_with_stdlib(payload)
    assert extraction.page_count == 1
    text = extraction.pages[0].text
    assert "出质人：示例控股集团有限公司" in text
    assert "本次质押股数：1,000万股" in text

    index = DocumentIndex.from_pdf("doc_stdlib_cjk", extraction)
    line = next(line for line in index.pages[0].lines if "质权人" in line.text)
    # 页面 595x842、基线 y=708、字号 11 -> 归一化 y 约 149—162
    assert 140 <= line.bbox[1] <= 175
    assert line.bbox[0] < line.bbox[2]


def test_line_order_is_reading_order_not_stream_order(app, samples_dir: Path, tmp_path: Path) -> None:
    """内容流按从下到上书写时，索引仍必须给出从上到下的阅读顺序。"""
    payload = _reversed_stream_pdf()
    document = app.upload_document("reversed.pdf", payload)
    index = app.parse.build_index(document["document_id"])
    texts = [line.text for line in index.pages[0].lines if line.text.strip()]
    assert texts[0].startswith("Title Line One")
    assert texts[-1].startswith("Final Line Three")
    # 阅读顺序必须与 bbox 的 y 单调一致
    ys = [line.bbox[1] for line in index.pages[0].lines if line.text.strip()]
    assert ys == sorted(ys)
