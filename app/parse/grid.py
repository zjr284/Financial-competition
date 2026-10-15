"""Recover cells only from closed, ruled PDF grids and native character positions."""
from __future__ import annotations

from io import BytesIO

from app.common.ids import digest_of
from app.parse.index import _evidence_row

EPS = 1.2


def _unique(values):
    groups = []
    for value in sorted(values):
        if groups and abs(value - groups[-1][-1]) <= EPS:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _text(chars):
    lines = []
    for char in sorted(chars, key=lambda c: (-(c.y0 + c.y1) / 2, c.x0)):
        cy = (char.y0 + char.y1) / 2
        # Superscript footnotes overlap their text line despite a different
        # centre/baseline. Keep them in x order after the amount, so 注3 cannot
        # move before 200万股 and produce the false literal "3200万股".
        found = next((line for y, line in lines if abs(y - cy) <= 2.5 or any(
            min(char.y1, c.y1) - max(char.y0, c.y0) >= 0.5 * min(char.y1 - char.y0, c.y1 - c.y0)
            for c in line)), None)
        if found is None:
            lines.append((cy, [char]))
        else:
            found.append(char)
    return ''.join(''.join(c.text for c in sorted(line, key=lambda c: c.x0)) for _, line in lines).strip()


def cells_from_geometry(document_id, page, width, height, chars, segments):
    """No layout guesses: matching vertical extents and full-width horizontal rules."""
    vertical, horizontal = [], []
    for x0, y0, x1, y1 in segments:
        if abs(x1 - x0) <= EPS and y1 - y0 >= 12:
            vertical.append(((x0 + x1) / 2, y0, y1))
        if abs(y1 - y0) <= EPS and x1 - x0 >= 30:
            horizontal.append((x0, x1, (y0 + y1) / 2))
    groups = []
    for x, lo, hi in vertical:
        group = next((g for g in groups if abs(g[0] - lo) <= EPS and abs(g[1] - hi) <= EPS), None)
        if group is None:
            groups.append([lo, hi, [x]])
        else:
            group[2].append(x)
    cells, seen = [], set()
    for lo, hi, columns in groups:
        xs = _unique(columns)
        if len(xs) < 3:
            continue
        ys = sorted(_unique([y for left, right, y in horizontal
                            if left <= xs[0] + EPS and right >= xs[-1] - EPS and lo - EPS <= y <= hi + EPS]), reverse=True)
        if len(ys) < 3 or abs(ys[0] - hi) > EPS or abs(ys[-1] - lo) > EPS:
            continue
        if (len(xs) - 1) * (len(ys) - 1) > 200:
            continue
        table_id = 'grid_' + digest_of([document_id, page, xs, ys])[:20]
        for row, (top, bottom) in enumerate(zip(ys, ys[1:]), 1):
            for column, (left, right) in enumerate(zip(xs, xs[1:]), 1):
                selected = [c for c in chars if left <= (c.x0 + c.x1) / 2 < right
                            and bottom < (c.y0 + c.y1) / 2 <= top]
                text = _text(selected)
                if not text or len(text) > 300:
                    continue
                bbox = [round(left / width * 1000), round((height - top) / height * 1000),
                        round(right / width * 1000), round((height - bottom) / height * 1000)]
                bbox = [max(0, min(1000, x)) for x in bbox]
                key = (tuple(bbox), text)
                if key in seen:
                    continue
                seen.add(key)
                cells.append(_evidence_row(document_id, page, bbox, text, method='table', confidence=0.95,
                                           table_id=table_id, row=row, column=column))
    return cells


def extract_grid_evidence(document_id, payload, *, max_pages=2):
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LAParams, LTChar, LTLine, LTRect
        from app.parse.pdfpos import CharBox
    except ImportError:
        return [], ['原生网格解析需要 pdfminer；本次不恢复跨行单元格']
    result = []
    try:
        for number, layout in enumerate(extract_pages(BytesIO(payload), maxpages=max_pages, laparams=LAParams()), 1):
            chars, segments = [], []
            def visit(obj):
                if isinstance(obj, LTChar):
                    chars.append(CharBox(obj.get_text(), obj.x0, obj.y0, obj.x1, obj.y1, obj.size))
                    return
                if isinstance(obj, LTLine):
                    segments.append(obj.bbox)
                elif isinstance(obj, LTRect):
                    x0, y0, x1, y1 = obj.bbox
                    segments.extend([(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)])
                if hasattr(obj, '__iter__'):
                    for child in obj:
                        visit(child)
            visit(layout)
            result.extend(cells_from_geometry(document_id, number, layout.width, layout.height, chars, segments))
    except Exception:
        return [], ['原生网格未能可靠解析；保留文本候选并需人工复核']
    return result, [] if result else ['未识别到可闭合的原生网格；本次不恢复跨行单元格']
