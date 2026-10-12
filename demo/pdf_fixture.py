"""带中文文本层的合成 PDF；嵌入中文 TTF 与 ToUnicode，缺字体时明确报错。"""

import os
import zlib
from pathlib import Path
from tools.make_sample_pdf_cjk import TrueType, to_unicode_cmap


def make_pdf(lines):
    candidates = [Path(os.environ["C_DEMO_FONT"])] if os.environ.get("C_DEMO_FONT") else [Path("C:/Windows/Fonts/simhei.ttf"), Path("C:/Windows/Fonts/simkai.ttf")]
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise ValueError("合成 PDF 需要中文 TTF；设置 C_DEMO_FONT 指向本机字体")
    font = TrueType(font_path.read_bytes())
    codes = sorted({ord(char) for line in lines for char in line})
    glyphs = {code: font.cmap.get(code, 0) for code in codes}
    if not all(glyphs.values()):
        raise ValueError("字体缺少合成资料使用的字符")
    cmap = to_unicode_cmap(glyphs)
    widths = " ".join(f"{gid} [{round(font.widths[gid] / font.units_per_em * 1000)}]" for gid in sorted(set(glyphs.values())))
    content = "".join(f"BT /F1 12 Tf 50 {780 - i * 28} Td <{''.join(f'{glyphs[ord(char)]:04X}' for char in line)}> Tj ET\n" for i, line in enumerate(lines)).encode()
    compressed = zlib.compress(font.data, 9)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /SyntheticCJK /Encoding /Identity-H /DescendantFonts [5 0 R] /ToUnicode 7 0 R >>",
        f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SyntheticCJK /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 8 0 R /CIDToGIDMap /Identity /DW 1000 /W [{widths}] >>".encode(),
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
        f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"endstream",
        b"<< /Type /FontDescriptor /FontName /SyntheticCJK /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 /FontFile2 9 0 R >>",
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode() + compressed + b"\nendstream",
    ]
    result, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    result.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode())
    result.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)
