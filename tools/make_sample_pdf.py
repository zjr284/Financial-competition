"""生成最小的确定性 PDF 样例（无需第三方库）。

用途：验证 PDF 文本层解析与证据坐标映射。
页面为 612x792（Letter），正文从 y=720 起每行下移 18 磅。
记录 (行号, x0, y0, 字体大小, 文本)，供 tests/test_parse.py 断言归一化坐标。

运行：python tools/make_sample_pdf.py
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "samples" / "announcement_pledge_demo.pdf"

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
FIRST_BASELINE = 720.0
LINE_STEP = 18.0
FONT_SIZE = 12

# (相对行号, x, 文本)
LINES: list[tuple[int, int, str]] = [
    (0, 72, "Sample Corp Share Pledge Announcement"),
    (1, 72, "Stock code: 999999   Announcement No.: 2026-045"),
    (3, 72, "Pledgor: Sample Holding Group Co., Ltd."),
    (4, 72, "Pledgee: Sample Bank Co., Ltd. City Branch"),
    (5, 72, "Pledged shares: 10,000,000 shares"),
    (6, 72, "Pledge ratio of total share capital: 5.20%"),
    (7, 72, "Pledge start date: 2026-08-20   End date: 2027-08-19"),
    (9, 72, "The pledged shares are not used as guarantee for restructuring."),
]


def line_position(line_index: int) -> tuple[float, float]:
    return 72.0, FIRST_BASELINE - line_index * LINE_STEP


def content_stream() -> bytes:
    chunks = ["BT", f"/F1 {FONT_SIZE} Tf"]
    for line_index, x, text in LINES:
        _, y = line_position(line_index)
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        chunks.append(f"1 0 0 1 {x} {y} Tm")
        chunks.append(f"({escaped}) Tj")
    chunks.append("ET")
    return ("\n".join(chunks) + "\n").encode("latin-1")


def build_pdf() -> bytes:
    stream = content_stream()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    payload = build_pdf()
    TARGET.write_bytes(payload)
    print(f"已生成 {TARGET} ({len(payload)} 字节)")
    for line_index, x, text in LINES:
        _, y = line_position(line_index)
        print(f"  行 {line_index}: x={x} y={y} size={FONT_SIZE} text={text!r}")


if __name__ == "__main__":
    main()
