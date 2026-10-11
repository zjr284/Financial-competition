"""生成带中文文本层的最小 PDF 样例（嵌入系统 TTF，供坐标定位测试）。

为什么需要它：结构化提取要求“证据页码、坐标能在原件上框选”。TXT/CSV 的坐标是
按行推导的近似版面，只有真实 PDF 才能提供原件实测坐标。本脚本把每个汉字以
Identity-H 双字节编码写入内容流，并附 ToUnicode CMap，使评审工具的“复制文本”
与我们的解析结果一致。

依赖：Windows 自带黑体（C:/Windows/Fonts/simhei.ttf）。找不到字体时脚本退出码为 0
并打印说明，不阻塞其它样例。

运行：python tools/make_sample_pdf_cjk.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "samples" / "announcement_pledge_demo_cjk.pdf"
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/simkai.ttf"),
    Path("C:/Windows/Fonts/simsunb.ttf"),
]

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
FONT_SIZE = 11.0
LEFT = 56.0
TOP_BASELINE = 780.0
LINE_STEP = 18.0

LINES: list[tuple[int, str]] = [
    (0, "示例公司股权质押公告"),
    (1, "证券代码：999999    证券简称：示例公司    公告编号：2026-045"),
    (3, "出质人：示例控股集团有限公司"),
    (4, "质权人：示例银行股份有限公司城中支行"),
    (5, "本次质押股数：1,000万股"),
    (6, "占公司总股本比例：5.20%"),
    (7, "质押起始日：2026年8月20日"),
    (8, "质押到期日：2027年8月19日"),
    (10, "本次质押后，出质人累计质押股份占其所持公司股份的32.50%。"),
]


class TrueType:
    """只解析生成 PDF 所需的最小表集合，不依赖第三方字体库。"""

    def __init__(self, payload: bytes) -> None:
        self.data = payload
        tag = payload[:4]
        if tag == b"ttcf":
            raise ValueError("不支持 TTC 集合字体，请改用 .ttf")
        self.sfnt_version = tag
        (num_tables,) = struct.unpack(">H", payload[4:6])
        self.tables: dict[bytes, tuple[int, int]] = {}
        offset = 12
        for _ in range(num_tables):
            name, _checksum, table_offset, length = struct.unpack(">4sIII", payload[offset : offset + 16])
            self.tables[name] = (table_offset, length)
            offset += 16
        self.num_glyphs = self._num_glyphs()
        self.units_per_em = self._units_per_em()
        self.cmap = self._cmap()
        self.widths = self._hmtx()

    def table(self, name: bytes) -> bytes:
        offset, length = self.tables[name]
        return self.data[offset : offset + length]

    def _num_glyphs(self) -> int:
        return struct.unpack(">H", self.table(b"maxp")[4:6])[0]

    def _units_per_em(self) -> int:
        return struct.unpack(">H", self.table(b"head")[18:20])[0]

    def _hmtx(self) -> dict[int, int]:
        header = self.table(b"hhea")
        num_hmetrics = struct.unpack(">H", header[34:36])[0]
        table = self.table(b"hmtx")
        widths: dict[int, int] = {}
        last = 0
        for index in range(self.num_glyphs):
            if index < num_hmetrics:
                advance = struct.unpack(">H", table[index * 4 : index * 4 + 2])[0]
                last = advance
            widths[index] = last
        return widths

    def _cmap(self) -> dict[int, int]:
        """优先取 (3,1) 或 (3,10) 子表，覆盖 BMP 与扩展区。"""
        table = self.table(b"cmap")
        (num_tables,) = struct.unpack(">H", table[2:4])
        best: dict[int, int] = {}
        best_score = -1
        for index in range(num_tables):
            platform, encoding, offset = struct.unpack(">HHI", table[4 + index * 8 : 12 + index * 8])
            score = {(3, 1): 3, (3, 10): 4, (0, 3): 2}.get((platform, encoding), 1)
            mapping = self._read_subtable(table, offset)
            if score > best_score and mapping:
                best, best_score = mapping, score
        return best

    @staticmethod
    def _read_subtable(table: bytes, offset: int) -> dict[int, int]:
        (fmt,) = struct.unpack(">H", table[offset : offset + 2])
        mapping: dict[int, int] = {}
        if fmt == 4:
            seg_x2 = struct.unpack(">H", table[offset + 6 : offset + 8])[0]
            segments = seg_x2 // 2
            ends = struct.unpack(">" + "H" * segments, table[offset + 14 : offset + 14 + seg_x2])
            starts = struct.unpack(">" + "H" * segments, table[offset + 16 + seg_x2 : offset + 16 + seg_x2 * 2])
            deltas = struct.unpack(">" + "h" * segments, table[offset + 16 + seg_x2 * 2 : offset + 16 + seg_x2 * 3])
            range_offset_base = offset + 16 + seg_x2 * 3
            range_offsets = struct.unpack(">" + "H" * segments, table[range_offset_base : range_offset_base + seg_x2])
            for index in range(segments):
                start, end = starts[index], ends[index]
                if start == 0xFFFF:
                    continue
                for code in range(start, min(end, 0xFFFF) + 1):
                    if range_offsets[index] == 0:
                        glyph = (code + deltas[index]) & 0xFFFF
                    else:
                        glyph_offset = range_offset_base + index * 2 + range_offsets[index] + (code - start) * 2
                        if glyph_offset + 2 > len(table):
                            continue
                        glyph = struct.unpack(">H", table[glyph_offset : glyph_offset + 2])[0]
                        if glyph:
                            glyph += 0
                    if glyph:
                        mapping[code] = glyph
        elif fmt == 12:
            (num_groups,) = struct.unpack(">I", table[offset + 12 : offset + 16])
            for index in range(num_groups):
                base = offset + 16 + index * 12
                start, end, glyph_start = struct.unpack(">III", table[base : base + 12])
                for code in range(start, min(end, 0x10FFFF) + 1):
                    mapping[code] = glyph_start + (code - start)
        return mapping


def build_font_objects(font: TrueType) -> tuple[bytes, bytes, dict[int, int]]:
    """返回 (FontFile 流, W 数组文本, 字符->glyph 映射)。"""
    raw = font.data
    compressed = zlib.compress(raw, 9)
    units = font.units_per_em or 1000
    codes = sorted({code for _line, text in LINES for code in [ord(ch) for ch in text]})
    glyphs = {code: font.cmap.get(code, 0) for code in codes}
    widths = sorted({glyphs[code] for code in codes})

    def scaled(glyph: int) -> int:
        return int(round(font.widths.get(glyph, units // 2) / units * 1000))

    w_entries = " ".join(f"{glyph} [{scaled(glyph)}]" for glyph in widths)
    return compressed, w_entries, glyphs


def to_unicode_cmap(glyphs: dict[int, int]) -> bytes:
    """Identity-H：字符代码就是 CID，这里把 CID 映射回 Unicode。"""
    reverse: dict[int, int] = {}
    for code, glyph in glyphs.items():
        reverse.setdefault(glyph, code)
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    entries = sorted(reverse.items())
    for start in range(0, len(entries), 100):
        chunk = entries[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for glyph, code in chunk:
            lines.append(f"<{glyph:04X}> <{code:04X}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def content_stream(glyphs: dict[int, int]) -> bytes:
    chunks = ["BT", f"/F1 {FONT_SIZE} Tf", "1 0 0 1 0 0 Tm"]
    for line_index, text in LINES:
        y = TOP_BASELINE - line_index * LINE_STEP
        hex_text = "".join(f"{glyphs.get(ord(ch), 0):04X}" for ch in text)
        chunks.append(f"1 0 0 1 {LEFT} {y} Tm")
        chunks.append(f"<{hex_text}> Tj")
    chunks.append("ET")
    return ("\n".join(chunks) + "\n").encode("ascii")


def build_pdf(font_path: Path) -> bytes:
    font = TrueType(font_path.read_bytes())
    compressed, w_entries, glyphs = build_font_objects(font)
    cmap_stream = to_unicode_cmap(glyphs)
    content = content_stream(glyphs)

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # 先占位，稍后回填引用编号
    catalog_num = add(b"")
    pages_num = add(b"")
    page_num = add(b"")
    content_num = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
    fontfile_num = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed) + compressed + b"\nendstream")
    tounicode_num = add(b"<< /Length %d >>\nstream\n" % len(cmap_stream) + cmap_stream + b"endstream")
    cid_num = add(b"")
    font_num = add(b"")

    sub_type = b"/CIDFontType2" if font.sfnt_version == b"\x00\x01\x00\x00" else b"/CIDFontType0"
    descriptor = (
        b"<< /Type /FontDescriptor /FontName /SampleCJK /Flags 4 "
        b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 "
        b"/CapHeight 700 /StemV 80 /FontFile2 " + str(fontfile_num).encode() + b" 0 R >>"
    )
    descriptor_num = add(descriptor)

    objects[cid_num - 1] = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /SampleCJK "
        b"/Encoding /Identity-H /DescendantFonts [" + str(font_num).encode() + b" 0 R] "
        b"/ToUnicode " + str(tounicode_num).encode() + b" 0 R >>"
    )
    objects[font_num - 1] = (
        b"<< /Type /Font /Subtype " + sub_type + b" /BaseFont /SampleCJK "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor " + str(descriptor_num).encode() + b" 0 R "
        b"/DW 1000 /W [" + w_entries.encode("ascii") + b"] "
        b"/CIDToGIDMap /Identity >>"
    )
    objects[page_num - 1] = (
        b"<< /Type /Page /Parent " + str(pages_num).encode() + b" 0 R "
        b"/MediaBox [0 0 " + f"{PAGE_WIDTH:g} {PAGE_HEIGHT:g}".encode() + b"] "
        b"/Resources << /Font << /F1 " + str(cid_num).encode() + b" 0 R >> >> "
        b"/Contents " + str(content_num).encode() + b" 0 R >>"
    )
    objects[pages_num - 1] = (
        b"<< /Type /Pages /Kids [" + str(page_num).encode() + b" 0 R] /Count 1 >>"
    )
    objects[catalog_num - 1] = b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"

    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def main() -> int:
    font_path = next((path for path in FONT_CANDIDATES if path.exists()), None)
    if font_path is None:
        print("未找到可嵌入的中文字体，跳过中文 PDF 样例（不影响其它样例）。")
        return 0
    payload = build_pdf(font_path)
    TARGET.write_bytes(payload)
    print(f"已生成 {TARGET}（{len(payload)} 字节，嵌入字体 {font_path.name}）")
    for line_index, text in LINES:
        y = TOP_BASELINE - line_index * LINE_STEP
        print(f"  行 {line_index}: x={LEFT:g} baseline_y={y:g} size={FONT_SIZE:g} text={text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
