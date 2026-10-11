"""PDF 定位文本抽取：把每个字符映射回页面坐标。

优先级：
  1. pdfminer.six（若是可用）——精确到字符的 bbox、字号与字体；
  2. pypdf  ——仅文本，用于校验/兜底，坐标不可得时标 needs_review；
  3. 纯标准库 ——解析 FlateDecode 内容流里的 Tj/TJ/Td/TD/Tm，支持 WinAnsi 与 ToUnicode CMap。
任何一层失败都不抛异常穿透到 HTTP，而是返回 warning 由 parse 层写成 evidence 置信度与解析状态。
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field
from typing import Any


CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in CJK_RANGES)


@dataclass
class CharBox:
    """字符及其在 PDF 默认用户空间（底原点）的包围盒。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 0.0


@dataclass
class PageChars:
    page: int
    width: float
    height: float
    chars: list[CharBox] = field(default_factory=list)
    source: str = "none"
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(ch.text for ch in self.chars)


@dataclass
class PdfExtraction:
    pages: list[PageChars] = field(default_factory=list)
    source: str = "none"
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_chars(self) -> int:
        return sum(len(page.chars) for page in self.pages)


# ---------------------------------------------------------------- pdfminer.six

def _extract_with_pdfminer(payload: bytes) -> PdfExtraction:
    from pdfminer.converter import PDFPageAggregator  # type: ignore
    from pdfminer.layout import LAParams, LTChar, LTTextContainer  # type: ignore
    from pdfminer.pdfdocument import PDFDocument  # type: ignore
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager  # type: ignore
    from pdfminer.pdfpage import PDFPage  # type: ignore
    from pdfminer.pdfparser import PDFParser  # type: ignore

    result = PdfExtraction(source="pdfminer")
    parser = PDFParser(io.BytesIO(payload))
    document = PDFDocument(parser)
    manager = PDFResourceManager()
    # detect_vertical=True 在部分嵌入字体上会把多行横排文字并成一个竖排容器，
    # 导致 bbox 跨行（实测示例 PDF 的 bbox 高度与行位置都错）。关掉它，按行分组交给索引层。
    device = PDFPageAggregator(manager, laparams=LAParams(detect_vertical=False, all_texts=True))
    interpreter = PDFPageInterpreter(manager, device)

    for index, page in enumerate(PDFPage.create_pages(document), start=1):
        interpreter.process_page(page)
        layout = device.get_result()
        width = float(getattr(layout, "width", 0.0)) or 612.0
        height = float(getattr(layout, "height", 0.0)) or 792.0
        page_chars = PageChars(page=index, width=width, height=height, source="pdfminer")
        for element in layout:
            if not isinstance(element, LTTextContainer):
                continue
            for line in element:
                for char in line:
                    if isinstance(char, LTChar) and char.get_text():
                        page_chars.chars.append(
                            CharBox(
                                text=char.get_text(),
                                x0=float(char.x0),
                                y0=float(char.y0),
                                x1=float(char.x1),
                                y1=float(char.y1),
                                size=float(getattr(char, "size", 0.0) or 0.0),
                            )
                        )
        result.pages.append(page_chars)
    if not result.pages:
        result.warnings.append("pdfminer 未解析出任何页面")
    return result


# ---------------------------------------------------------------- pypdf 兜底

def _extract_with_pypdf(payload: bytes) -> PdfExtraction:
    import pypdf  # type: ignore

    result = PdfExtraction(source="pypdf")
    reader = pypdf.PdfReader(io.BytesIO(payload))
    for index, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width or 612)
        height = float(page.mediabox.height or 792)
        page_chars = PageChars(page=index, width=width, height=height, source="pypdf")
        # pypdf 能给出访问顺序文本与每字符变换矩阵（v6+），尽量取坐标
        try:
            chars: list[Any] = []
            page.extract_text(visitor_text=lambda *args: chars.append(args))
        except TypeError:
            chars = []
        if chars:
            for entry in chars:
                # visitor_text(text, cm, tm, font_dict, font_size)
                text, _cm, tm, font_dict, font_size = (list(entry) + [None] * 5)[:5]
                if not text:
                    continue
                size = float(font_size or 0.0)
                x = float(tm[4]) if tm else 0.0
                y = float(tm[5]) if tm else 0.0
                for offset, ch in enumerate(text):
                    if ch in "\r\n":
                        continue
                    advance = size * (1.0 if is_cjk(ch) else 0.5)
                    page_chars.chars.append(
                        CharBox(text=ch, x0=x + offset * advance, y0=y, x1=x + (offset + 1) * advance, y1=y + size, size=size)
                    )
        else:
            text = page.extract_text() or ""
            if not text.strip():
                page_chars.warnings.append("该页无文本层（可能是扫描件），需要 OCR")
            else:
                page_chars.warnings.append("pypdf 未提供字符坐标，证据坐标不可定位")
                # 保留纯文本以便判类型，但没有坐标就不生成 bbox 证据
                page_chars.chars = []
        result.pages.append(page_chars)
    return result


# ---------------------------------------------------------------- 标准库兜底

_STD_TOKEN = re.compile(
    rb"""\[(?:[^\[\]\\]|\\.)*\]   # TJ array
        |\((?:[^()\\]|\\.)*\)    # literal string
        |<[0-9A-Fa-f\s]*>        # hex string
        |<<|>>
        |/[^\s/\[\]<>()]+
        |[-+]?[0-9]*\.?[0-9]+
        |[A-Za-z'*"]+""",
    re.VERBOSE | re.DOTALL,
)


def _decode_literal(raw: bytes) -> str:
    out = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte == 0x5C and index + 1 < len(raw):
            nxt = raw[index + 1]
            mapping = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C}
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if 0x30 <= nxt <= 0x37:
                octal = bytes(raw[index + 1 : index + 4])
                digits = b""
                for byte2 in octal:
                    if 0x30 <= byte2 <= 0x37:
                        digits += bytes([byte2])
                    else:
                        break
                out.append(int(digits, 8) & 0xFF)
                index += 1 + len(digits)
                continue
            out.append(nxt)
            index += 2
            continue
        out.append(byte)
        index += 1
    return out.decode("latin-1")


def _std_winansi() -> dict[int, str]:
    table: dict[int, str] = {}
    for code in range(32, 127):
        table[code] = chr(code)
    for code in range(160, 256):
        try:
            table[code] = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            table[code] = chr(code)
    return table


def _simple_encoding() -> dict[int, str]:
    table: dict[int, str] = {}
    for code in range(32, 127):
        table[code] = chr(code)
    return table


def _parse_tounicode(data: bytes) -> dict[int, str]:
    """解析 CMap 的 bfchar/bfrange，供 CID 字体使用。"""
    mapping: dict[int, str] = {}

    def utf16(hex_text: str) -> str:
        cleaned = re.sub(r"\s+", "", hex_text)
        try:
            return bytes.fromhex(cleaned).decode("utf-16-be", errors="ignore")
        except ValueError:
            return ""

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.DOTALL):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[int(src, 16)] = utf16(dst.decode("ascii"))
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.DOTALL):
        for src, dst, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            start, end, base = int(src, 16), int(dst, 16), int(target, 16)
            for offset in range(end - start + 1):
                mapping[start + offset] = utf16(f"{base + offset:04X}")
    return mapping


class _Font:
    def __init__(self, mapping: dict[int, str] | None, *, bytes_per_code: int, name: str = "") -> None:
        self.mapping = mapping
        self.bytes_per_code = bytes_per_code
        self.name = name

    def decode(self, raw: bytes) -> str:
        if self.mapping is None:
            if self.bytes_per_code == 2:
                return raw.decode("utf-16-be", errors="ignore")
            return "".join(_std_winansi().get(byte, "") for byte in raw)
        out = []
        if self.bytes_per_code == 2:
            for index in range(0, len(raw) - 1, 2):
                code = int.from_bytes(raw[index : index + 2], "big")
                out.append(self.mapping.get(code, ""))
        else:
            for byte in raw:
                out.append(self.mapping.get(byte, ""))
        return "".join(out)


def _extract_objects(payload: bytes) -> dict[int, bytes]:
    """粗提取 obj/endobj 片段（不含对象流内对象）。"""
    objects: dict[int, bytes] = {}
    for match in re.finditer(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", payload, re.DOTALL):
        objects[int(match.group(1))] = match.group(3)
    return objects


def _extract_with_stdlib(payload: bytes) -> PdfExtraction:
    objects = _extract_objects(payload)
    # 页面顺序：按对象号无法保证；先取页面树顺序，退化为对象号顺序
    page_numbers = [num for num, body in objects.items() if re.search(rb"/Type\s*/Page[^s]", body)]
    order = _page_order(objects, page_numbers)

    width, height = _media_box(objects)
    fonts = _document_fonts(objects)
    result = PdfExtraction(source="stdlib")
    if not order:
        result.warnings.append("未在 PDF 中找到页面对象")
        return result

    for page_number in order:
        body = objects.get(page_number, b"")
        page_chars = PageChars(page=len(result.pages) + 1, width=width, height=height, source="stdlib")
        contents = _page_content(objects, body)
        if contents is None:
            page_chars.warnings.append("页面内容流无法解析")
        else:
            _run_content(page_chars, contents, fonts)
        if not page_chars.chars:
            page_chars.warnings.append("该页无可用文本层（可能是扫描件或字体未嵌入），需要 OCR")
        result.pages.append(page_chars)
    return result


def _page_order(objects: dict[int, bytes], page_numbers: list[int]) -> list[int]:
    for num, body in objects.items():
        if re.search(rb"/Type\s*/Pages", body) and b"/Parent" not in body:
            kids = re.search(rb"/Kids\s*\[(.*?)\]", body, re.DOTALL)
            if kids:
                found = [int(k) for k in re.findall(rb"(\d+)\s+\d+\s+R", kids.group(1))]
                if found:
                    return found
    return sorted(page_numbers)


def _media_box(objects: dict[int, bytes]) -> tuple[float, float]:
    for body in objects.values():
        match = re.search(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]", body)
        if match:
            x0, y0, x1, y1 = (float(match.group(i)) for i in range(1, 5))
            return max(1.0, abs(x1 - x0)), max(1.0, abs(y1 - y0))
    return 595.0, 842.0


def _document_fonts(objects: dict[int, bytes]) -> dict[str, _Font]:
    fonts: dict[str, _Font] = {}
    for body in objects.values():
        if b"/Font" not in body:
            continue
        for name, ref in re.findall(rb"/([A-Za-z0-9#_+.\-]+)\s+(\d+)\s+\d+\s+R", body):
            font_body = objects.get(int(ref))
            if font_body is None:
                continue
            fonts[name.decode("latin-1")] = _build_font(objects, font_body)
    return fonts


def _build_font(objects: dict[int, bytes], font_body: bytes) -> _Font:
    subtype = re.search(rb"/Subtype\s*/(\w+)", font_body)
    subtype_name = subtype.group(1).decode() if subtype else ""
    name_match = re.search(rb"/BaseFont\s*/([A-Za-z0-9#_+.\-]+)", font_body)
    base_name = name_match.group(1).decode("latin-1") if name_match else ""
    two_byte = subtype_name == "Type0" or b"/Identity-H" in font_body
    mapping: dict[int, str] | None = None
    tou = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", font_body)
    if tou:
        stream = objects.get(int(tou.group(1)))
        if stream is not None:
            data = _stream_bytes(stream)
            if data:
                mapping = _parse_tounicode(data)
    if mapping is None and not two_byte:
        if b"/Differences" in font_body or subtype_name in {"TrueType", "Type1"}:
            mapping = None  # 用 WinAnsi 近似
        else:
            mapping = _simple_encoding()
    return _Font(mapping, bytes_per_code=2 if two_byte else 1, name=base_name)


def _stream_bytes(body: bytes) -> bytes | None:
    marker = re.search(rb"stream\r?\n", body)
    if not marker:
        return None
    raw = body[marker.end() :]
    end = raw.rfind(b"endstream")
    if end != -1:
        raw = raw[:end]
    if b"/FlateDecode" in body[: marker.start()]:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompressobj().decompress(raw)
            except zlib.error:
                return None
    return raw


def _page_content(objects: dict[int, bytes], page_body: bytes) -> bytes | None:
    contents = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", page_body)
    if contents:
        stream = objects.get(int(contents.group(1)))
        return _stream_bytes(stream) if stream is not None else None
    array = re.search(rb"/Contents\s*\[(.*?)\]", page_body, re.DOTALL)
    if array:
        chunks: list[bytes] = []
        for ref in re.findall(rb"(\d+)\s+\d+\s+R", array.group(1)):
            stream = objects.get(int(ref))
            if stream is not None:
                data = _stream_bytes(stream)
                if data:
                    chunks.append(data)
        return b"\n".join(chunks) if chunks else None
    return None


def _run_content(page: PageChars, content: bytes, fonts: dict[str, _Font]) -> None:
    """极简内容流解释器：跟踪 CTM/Tm/Td/TD/T*，只取文本操作符。"""
    tokens = _STD_TOKEN.findall(content)
    stack: list[float] = []
    operands: list[bytes] = []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    ctm_stack: list[list[float]] = []
    tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    tlm = list(tm)
    leading = 0.0
    size = 12.0
    font = _Font(None, bytes_per_code=1)

    def multiply(a: list[float], b: list[float]) -> list[float]:
        return [
            a[0] * b[0] + a[1] * b[2],
            a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4],
            a[4] * b[1] + a[5] * b[3] + b[5],
        ]

    def floats() -> list[float]:
        out = []
        for token in operands:
            try:
                out.append(float(token))
            except ValueError:
                out.append(0.0)
        return out

    def emit(text: str) -> None:
        """输出一段文本。

        必须保留编码后的空格字符：否则 "Pledged shares" 会变成 "Pledgedshares"，
        标签匹配（如“本次质押股数”）和多词英文标签都会失效。
        制表/换行不产出字符，改用水平位移近似；相邻字符间距过大时额外补一个空格。
        """
        if not text:
            return
        matrix = multiply(tm, ctm)
        x, y = matrix[4], matrix[5]
        scale_x = (matrix[0] ** 2 + matrix[1] ** 2) ** 0.5 or 1.0
        scale_y = (matrix[2] ** 2 + matrix[3] ** 2) ** 0.5 or size
        advance = 0.0
        previous: CharBox | None = None
        for ch in text:
            if ch in "\r\n":
                advance += size * scale_x * 0.5
                continue
            if ch in "\t":
                advance += size * scale_x * 2.0
                continue
            if ch == " ":
                # 字面空格相对前一字符的间隙为 0（它就是零间隙字符），
                # 只用 0.5 磅的容差过滤浮点噪声，否则 "Sample Corp" 会被粘成 "SampleCorp"。
                if previous is not None:
                    gap = (x + advance) - previous.x1
                    if gap > 0.5:
                        page.chars.append(
                            CharBox(
                                text=" ",
                                x0=previous.x1,
                                y0=y,
                                x1=x + advance,
                                y1=y + size * scale_y,
                                size=size,
                            )
                        )
                        previous = page.chars[-1]
                advance += size * scale_x * 0.25
                continue
            width = size * scale_x * (1.0 if is_cjk(ch) else 0.5)
            height = size * scale_y
            char = CharBox(text=ch, x0=x + advance, y0=y, x1=x + advance + width, y1=y + height, size=size)
            if previous is not None:
                gap = char.x0 - previous.x1
                # 断裂间距说明原内容里有空格，补一个空格字符以保持可读与可匹配。
                # 阈值取字号的 10%（约 1.2 磅 @12pt）：若用 25% 会把 word 之间
                # 常见的 3 磅间距判成"无空格"，"Sample Corp" 就被粘成 "SampleCorp"。
                if gap > max(0.5, size * scale_x * 0.10):
                    page.chars.append(
                        CharBox(text=" ", x0=previous.x1, y0=y, x1=char.x0, y1=y + height, size=size)
                    )
            page.chars.append(char)
            previous = char
            advance += width

    for token in tokens:
        if token in {b"BT"}:
            tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            tlm = list(tm)
            operands.clear()
            continue
        if token in {b"ET"}:
            operands.clear()
            continue
        if token in {b"q"}:
            ctm_stack.append(list(ctm))
            operands.clear()
            continue
        if token in {b"Q"}:
            if ctm_stack:
                ctm = ctm_stack.pop()
            operands.clear()
            continue
        if token in {b"cm"}:
            values = floats()
            if len(values) == 6:
                ctm = multiply(values, ctm)
            operands.clear()
            continue
        if token in {b"Tf"}:
            name = next((op for op in operands if op.startswith(b"/")), None)
            if name is not None:
                font = fonts.get(name[1:].decode("latin-1"), font)
            values = floats()
            if values:
                size = abs(values[-1]) or size
            operands.clear()
            continue
        if token in {b"TL"}:
            values = floats()
            if values:
                leading = values[-1]
            operands.clear()
            continue
        if token in {b"Td", b"TD"}:
            values = floats()
            if len(values) >= 2:
                tlm = multiply([1, 0, 0, 1, values[-2], values[-1]], tlm)
                tm = list(tlm)
            if token == b"TD" and values:
                leading = -values[-1]
            operands.clear()
            continue
        if token in {b"Tm"}:
            values = floats()
            if len(values) == 6:
                tlm = list(values)
                tm = list(tlm)
            operands.clear()
            continue
        if token in {b"T*"}:
            tlm = multiply([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
            operands.clear()
            continue
        if token in {b"Tj", b"'", b'"'}:
            if token in {b"'", b'"'}:
                tlm = multiply([1, 0, 0, 1, 0, -leading], tlm)
                tm = list(tlm)
            raw = next((op for op in operands if op.startswith(b"(") or op.startswith(b"<")), None)
            if raw is not None:
                emit(_decode_operand(raw, font))
            operands.clear()
            continue
        if token == b"TJ":
            array = next((op for op in operands if op.startswith(b"[")), None)
            if array is not None:
                emit(_decode_array(array, font))
            operands.clear()
            continue
        if token.startswith(b"(") or token.startswith(b"<") or token.startswith(b"/"):
            operands.append(token)
            continue
        try:
            float(token)
        except ValueError:
            operands.clear()  # 其它操作符（路径、颜色等）直接忽略
            continue
        operands.append(token)
        if len(operands) > 32:
            operands.pop(0)
    _ = stack


def _decode_operand(raw: bytes, font: _Font) -> str:
    if raw.startswith(b"<"):
        hex_text = re.sub(rb"[^0-9A-Fa-f]", b"", raw[1:-1])
        if len(hex_text) % 2:
            hex_text += b"0"
        try:
            return font.decode(bytes.fromhex(hex_text.decode("ascii")))
        except ValueError:
            return ""
    return font.decode(raw[1:-1])


def _decode_array(array: bytes, font: _Font) -> str:
    out: list[str] = []
    position = 0
    while position < len(array):
        char = array[position : position + 1]
        if char == b"(":
            depth = 1
            end = position + 1
            while end < len(array) and depth:
                if array[end : end + 1] == b"\\":
                    end += 2
                    continue
                if array[end : end + 1] == b"(":
                    depth += 1
                elif array[end : end + 1] == b")":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            out.append(font.decode(array[position + 1 : end]))
            position = end + 1
            continue
        if char == b"<" and array[position + 1 : position + 2] != b"<":
            end = array.find(b">", position)
            if end == -1:
                break
            out.append(_decode_operand(array[position : end + 1], font))
            position = end + 1
            continue
        position += 1
    return "".join(out)


# ---------------------------------------------------------------- 统一入口

def extract_pdf(payload: bytes) -> PdfExtraction:
    """按 pdfminer -> pypdf -> 标准库 顺序尝试，合并警告。"""
    warnings: list[str] = []
    for name, extractor in (
        ("pdfminer", _extract_with_pdfminer),
        ("pypdf", _extract_with_pypdf),
        ("stdlib", _extract_with_stdlib),
    ):
        try:
            result = extractor(payload)
        except Exception as exc:  # 单一后端失败不影响其它后端
            warnings.append(f"{name} 解析失败：{type(exc).__name__}: {exc}")
            continue
        result.warnings = warnings + list(result.warnings)
        if result.pages and result.text_chars > 0:
            return result
        if result.pages and name == "pdfminer":
            warnings.extend(result.warnings)
            continue
        if result.pages and not any(p.chars for p in result.pages):
            warnings.extend(result.warnings)
            continue
        warnings.extend(result.warnings)
    fallback = _extract_with_stdlib(payload)
    fallback.warnings = warnings + list(fallback.warnings)
    return fallback


def available_backends() -> dict[str, bool]:
    """报告可用的解析后端，写入 README 与 /health。"""
    status = {"pdfminer": False, "pypdf": False, "stdlib": True}
    try:  # pragma: no cover
        import pdfminer  # type: ignore  # noqa: F401

        status["pdfminer"] = True
    except Exception:
        pass
    try:  # pragma: no cover
        import pypdf  # type: ignore  # noqa: F401

        status["pypdf"] = True
    except Exception:
        pass
    return status


def page_text_without_coords(payload: bytes) -> list[str]:
    """仅取每页文本，用于面页判型；不用于生成证据坐标。"""
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(payload))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return []
