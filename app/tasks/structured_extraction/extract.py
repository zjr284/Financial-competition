"""结构化提取实现：文本条款路径 + 表格路径。

约束（AGENTS.md / docs/接口约定.md）：
  * 每个字段必须有 evidence_ids；找不到值时保留字段并标 needs_review，不编造来源；
  * 数值只由确定性归一化产生（app/common/text.py），模型只给候选片段；
  * 每条记录固定包含 document_id, company_code, event_date。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.common.ids import make_id, truncate
from app.common.text import field_payload, percent, scalar, squeeze
from app.parse.index import DocumentIndex, LineRecord
from app.tasks.structured_extraction.dictionary import (
    ANNOUNCEMENT_TYPES,
    COMPANY_ANCHORS,
    COMPANY_PATTERN,
    DATE_TOKEN,
    EQUITY_FIELDS,
    EVENT_DATE_ANCHORS,
    FIELD_SPECS,
    PLEDGE_FIELDS,
    RECORD_FIELDS,
    TYPE_KEYWORDS,
    TYPE_LABELS,
    ExtractConfig,
    FieldSpec,
    compile_anchor,
)

# 记录级字段在契约 FieldValue 中同样出现，便于前端逐字段展示来源
RECORD_FIELD_LABELS = {
    "document_id": "文档 ID",
    "company_code": "证券代码",
    "event_date": "事件日期",
}


@dataclass
class Segment:
    """文档中一段疑似属于某一类公告的文本。"""

    announcement_type: str
    page: int
    start: int  # 页内字符偏移
    end: int
    text: str
    title: str = ""
    source: str = "text"  # text | table
    row: int | None = None
    column: int | None = None

    def slice_offset(self, page_offset: int) -> tuple[int, int]:
        return self.start + page_offset, self.end + page_offset


@dataclass
class SegmentExtraction:
    segment: Segment
    fields: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)


class ExtractionEngine:
    """把 DocumentIndex 转成契约 ExtractionData.records。"""

    def __init__(self, config: ExtractConfig | None = None) -> None:
        self.config = config or ExtractConfig()
        self.warnings: list[str] = []

    # ------------------------------------------------------------ 主流程
    def extract(self, document_id: str, index: DocumentIndex, announcement_types: Iterable[str]) -> dict[str, Any]:
        wanted = [t for t in announcement_types if t in ANNOUNCEMENT_TYPES]
        unknown = [t for t in announcement_types if t not in ANNOUNCEMENT_TYPES]
        if unknown:
            self.warnings.append(f"忽略未在契约中的公告类型：{unknown}")
        if not wanted:
            wanted = list(ANNOUNCEMENT_TYPES)

        text = index.full_text()
        company_code = self._company_code(index)
        if company_code is None:
            self.warnings.append("未在原文中找到 6 位证券代码，记录级 company_code 标为待复核")

        segments = self._segments(index, wanted)
        if not segments:
            self.warnings.append("未识别到任何目标类型公告段落")

        records: list[dict[str, Any]] = []
        evidence_all: list[dict[str, Any]] = []
        for segment in segments[: self.config.max_events]:
            extraction = self._extract_segment(document_id, index, segment, company_code)
            records.append(extraction["record"])
            evidence_all.extend(extraction["evidence"])
        if len(segments) > self.config.max_events:
            self.warnings.append(f"段落数超过上限 {self.config.max_events}，仅处理前 {self.config.max_events} 段")

        return {
            "records": records,
            "evidence": evidence_all,
            "warnings": list(dict.fromkeys(self.warnings)),
            "company_code": company_code,
            "text_length": len(text),
        }

    # ------------------------------------------------------------ 段落切分
    def _segments(self, index: DocumentIndex, wanted: list[str]) -> list[Segment]:
        """按公告标题切段。

        标题判定从严，避免正文提到“减持”“质押”就拆出假事件：
          1. 关键词所在行同时含另一个类型关键词（表格型表头）；或
          2. 该行是文件前两行（“XX公司股权质押公告”式标题）；或
          3. 该行形如“（一）”、“1、”等分节标题且在前 8 行内；
        正文中“一、本次质押基本情况”这类小节不会单独成段。
        """
        segments: list[Segment] = []
        for page in index.pages:
            bounds: list[tuple[int, str, str]] = []
            for line_index, line in enumerate(page.lines):
                title = line.text.strip()
                if not title:
                    continue
                kind = self._dominant_type(line.text, wanted)
                if kind is None:
                    continue
                document_start = line_index < 2
                section_heading = bool(re.match(r"^\s*(\([一二三四五六七八九十]+\)|（[一二三四五六七八九十]+）|\d+[、.])", title))
                if not (document_start or (section_heading and line_index < 8)):
                    continue
                bounds.append((line.char_start, kind, title))
            bounds.sort()
            for position, (offset, kind, title) in enumerate(bounds):
                end = bounds[position + 1][0] if position + 1 < len(bounds) else len(page.text)
                span = end - offset
                if span < self.config.segment_min_chars:
                    end = min(len(page.text), offset + self.config.segment_max_chars)
                segments.append(
                    Segment(
                        announcement_type=kind,
                        page=page.page,
                        start=offset,
                        end=end,
                        text=page.text[offset:end],
                        title=truncate(title, 80),
                    )
                )
        return segments

    # ------------------------------------------------------------ 段内抽取
    def _extract_segment(
        self,
        document_id: str,
        index: DocumentIndex,
        segment: Segment,
        company_code: str | None,
    ) -> dict[str, Any]:
        page = index.page(segment.page)
        assert page is not None
        specs = FIELD_SPECS[segment.announcement_type]

        table_fields = self._table_fields(index, segment)
        fields: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        seen_evidence: dict[str, dict[str, Any]] = {}

        def collect(evidence: dict[str, Any]) -> str:
            """同一 evidence_id 只保留一条，避免 records/evidence 重复。"""
            seen_evidence.setdefault(evidence["evidence_id"], evidence)
            return evidence["evidence_id"]

        for spec in specs:
            located = self._locate(index, segment, spec, specs)
            if located is not None:
                anchor_page, anchor_start, anchor_end, value_start, value_end, raw_value = located
                span_start = min(anchor_start, value_start)
                span_end = max(anchor_end, value_end)
                evidence = index.evidence_for_span(anchor_page, span_start, span_end)
                fields.append(
                    field_payload(
                        spec.field_code,
                        raw_value,
                        expect=spec.expect,
                        evidence_ids=[collect(evidence)],
                    )
                )
                continue
            if table_fields is not None and spec.field_code in table_fields:
                cell = table_fields[spec.field_code]
                matched = [
                    line
                    for line in page.lines
                    if line.row == cell["row"] and line.column == cell["column"] and line.kind == "table"
                ]
                evidence = index.evidence_for_lines(page.page, matched or [])
                fields.append(
                    field_payload(
                        spec.field_code,
                        cell["value"],
                        expect=spec.expect,
                        evidence_ids=[collect(evidence)],
                    )
                )
                continue
            # 缺失字段：保留字段并给出原因，绝不填 0 或编造
            fields.append(
                field_payload(
                    spec.field_code,
                    None,
                    expect=spec.expect,
                    evidence_ids=[],
                    reason=f"未在原文中定位到「{spec.label}」，需人工复核原文" if spec.required else "非必填字段未出现",
                )
            )

        event = self._event_date(index, segment)
        event_date = event["date"] if event else None
        if event:
            collect(event["evidence"])

        company_evidence = self._company_evidence(index, segment, company_code)
        if company_evidence:
            collect(company_evidence)
        evidence_rows = list(seen_evidence.values())

        record_fields = [
            {
                "field_code": "document_id",
                "raw_value": document_id,
                "value": document_id,
                "unit": "none",
                "evidence_ids": [],
                "status": "verified",
                "reason": None,
            },
            {
                "field_code": "company_code",
                "raw_value": company_code,
                "value": company_code,
                "unit": "none",
                "evidence_ids": [company_evidence["evidence_id"]] if company_evidence else [],
                "status": "verified" if (company_code and company_evidence) else "needs_review",
                "reason": None if (company_code and company_evidence) else "未定位到带证据的 6 位证券代码",
            },
            {
                "field_code": "event_date",
                "raw_value": event["raw"] if event else None,
                "value": event_date,
                "unit": "none",
                "evidence_ids": [event["evidence"]["evidence_id"]] if event else [],
                "status": "verified" if event else "needs_review",
                "reason": None if event else "未定位到公告/签署日期",
            },
        ]

        event_id = make_id(
            "event",
            document_id,
            segment.announcement_type,
            segment.page,
            segment.start,
            event_date,
            length=20,
        )
        record = {
            "event_id": event_id,
            "document_id": document_id,
            "company_code": company_code or "000000",
            "event_date": event_date,
            "announcement_type": segment.announcement_type,
            "fields": record_fields + fields,
        }
        return {"record": record, "evidence": evidence_rows}

    # ------------------------------------------------------------ 字段定位
    def _locate(
        self,
        index: DocumentIndex,
        segment: Segment,
        spec: FieldSpec,
        specs: tuple[FieldSpec, ...] = (),
    ) -> tuple[int, int, int, int, int, str] | None:
        """在段内定位字段，返回 (page, anchor_start, anchor_end, value_start, value_end, raw)。"""
        page = index.page(segment.page)
        assert page is not None
        page_text = page.text
        anchor_span = self._anchor_span(page_text, segment, spec)
        if anchor_span is None:
            return None
        anchor_start, anchor_end = anchor_span
        window_end = min(segment.end, anchor_end + self.config.value_window)
        window = page_text[anchor_end:window_end]

        best = self._scan_value(window, window_end, anchor_end, spec, specs)
        if best is None:
            return None
        value_start, value_end, raw = best
        return page.page, anchor_start, anchor_end, value_start, value_end, raw

    def _scan_value(
        self,
        window: str,
        window_end: int,
        anchor_end: int,
        spec: FieldSpec,
        specs: tuple[FieldSpec, ...] = (),
    ) -> tuple[int, int, str] | None:
        """在标签之后找可归一化的取值。

        三条例外规则，缺一就会出现“整块串行”：
          1. 标签同一行的剩余部分优先（“出质人：示例控股集团有限公司”）；
          2. 标签行没有取值时才向下看后续行；
          3. 向下看时遇到同一公告其它字段的标签行就停止，否则 pledgee 会取到
             下一行“本次质押股数：1,000万股”的取值。
        另外，当标签本身落在“出质人：值”这种同一行结构里时，标签行从标签处开始取，
        避免标题尾部（“…权益变动公告”）被当成取值。
        `[^\\s...]` 这类模式遇到换行只匹配单个汉字，因此按连续区间整体取出。
        """
        value_pattern = re.compile(rf"(?P<value>{spec.value_chars})")
        lines = window.split("\n")
        cursor = 0
        for line_index, raw_line in enumerate(lines):
            if not raw_line.strip():
                cursor += len(raw_line) + 1
                continue
            if line_index > 0 and self._is_other_field_line(raw_line, spec, specs):
                break
            # 标签行：只从标签之后取，标签之前通常是标题或其它字段
            run_from = 0
            if line_index == 0:
                run_from = 0
            for start, end in self._contiguous_runs(value_pattern, raw_line):
                if start < run_from:
                    continue
                raw = raw_line[start:end].strip()
                if raw.startswith("："):
                    raw = raw[1:].strip()
                    start += 1
                    end = start + len(raw)
                if len(raw) < 2 or raw in {"详见", "见附", "无此", "不适用"}:
                    continue
                if raw.startswith("说明"):
                    continue
                if spec.expect == "percent" and percent(raw) is None:
                    continue
                if spec.expect in {"amount", "share", "count"} and scalar(raw) is None:
                    continue
                if spec.expect == "date" and not re.search(r"\d{4}", raw):
                    continue
                if len(raw) > spec.max_value_len:
                    raw = raw[: spec.max_value_len]
                    end = start + len(raw)
                absolute_start = anchor_end + cursor + start
                return absolute_start, min(window_end, anchor_end + cursor + end), raw
            cursor += len(raw_line) + 1
        return None

    @staticmethod
    def _is_other_field_line(line: str, spec: FieldSpec, specs: tuple[FieldSpec, ...]) -> bool:
        """该行是否是同一公告另一个字段的标签行。"""
        text = squeeze(line)
        for other in specs:
            if other.field_code == spec.field_code:
                continue
            for label in other.anchors:
                if text.startswith(label):
                    return True
        return False

    @staticmethod
    def _contiguous_runs(pattern: re.Pattern[str], line: str) -> list[tuple[int, int]]:
        """把同一行里相邻的匹配合并成一个连续区间 [(start, end), ...]。

        括号等被 value_chars 排除的字符会把“示例投资合伙企业（有限合伙）”切成两段；
        这里把中间只隔一个被排除字符的片段重新粘回去，避免截断公司全称。
        """
        spans = [match.span() for match in pattern.finditer(line)]
        if not spans:
            return []
        runs: list[list[int]] = [list(spans[0])]
        for start, end in spans[1:]:
            if start - runs[-1][1] <= 1:
                runs[-1][1] = end
            else:
                runs.append([start, end])
        return [(item[0], item[1]) for item in runs]

    def _anchor_span(self, page_text: str, segment: Segment, spec: FieldSpec) -> tuple[int, int] | None:
        """定位字段标签，优先级从高到低：

          1. 位于行首或紧跟分隔符的标签（“出质人：…”“质押股数 1,000万股”）；
          2. 声明字段区（段内第一个分节标题之前）内的任意出现；
          3. 段内任意出现（兜底）。

        否则正文里“出质人累计质押股份”会抢在“出质人：示例控股集团有限公司”之前命中；
        “本次质押后…质权人：…”同现时也会先命中错误位置。
        """
        primary_end = self._declared_zone_end(page_text, segment)
        for group in (spec.anchors, spec.weak_anchors):
            for label in group:
                pattern = compile_anchor(label)
                line_anchored: list[tuple[int, int]] = []
                declared: list[tuple[int, int]] = []
                anywhere: list[tuple[int, int]] = []
                for match in pattern.finditer(page_text, segment.start, segment.end):
                    span = (match.start(), match.end())
                    anywhere.append(span)
                    if match.start() < primary_end:
                        declared.append(span)
                    prefix = page_text[match.start() - 1 : match.start()] if match.start() > 0 else "\n"
                    following = page_text[match.end() : match.end() + 1]
                    line_start = page_text.rfind("\n", 0, match.start()) + 1
                    line_prefix = page_text[line_start : match.start()]
                    if self._is_explanatory_line(line_prefix):
                        # “说明：…本次缺少质权人…”：解释性文字，不是字段标签
                        continue
                    if prefix in {"", "\n", " ", "\t", "|", "\u3000"} or following in {"：", ":", "＝", "=", " "}:
                        line_anchored.append(span)
                for candidate in (line_anchored, declared, anywhere):
                    kept = [span for span in candidate if not self._span_on_explanatory_line(page_text, span)]
                    if kept:
                        return min(kept)
        return None

    @staticmethod
    def _span_on_explanatory_line(page_text: str, span: tuple[int, int]) -> bool:
        line_start = page_text.rfind("\n", 0, span[0]) + 1
        line_end = page_text.find("\n", span[1])
        if line_end == -1:
            line_end = len(page_text)
        return ExtractionEngine._is_explanatory_line(page_text[line_start:line_end])

    @staticmethod
    def _title_type_score(text: str, kind: str) -> int:
        """标题类型得分：命中的类型关键词字符总长（越长越具体）。

        例：「示例公司股东权益变动公告」同时含「质押」（2）与「权益变动」（4），
        应判为 equity_change 而不是 pledge。
        """
        return max((len(keyword) for keyword in TYPE_KEYWORDS[kind] if keyword in text), default=0)

    def _dominant_type(self, text: str, wanted: list[str]) -> str | None:
        scored = [(self._title_type_score(text, kind), kind) for kind in wanted]
        best_score, best_kind = max(scored, key=lambda item: item[0]) if scored else (0, None)
        return best_kind if best_score > 0 else None

    @staticmethod
    def _is_explanatory_line(line: str) -> bool:
        """该行是否为声明性/解释性文字，而不是字段行。

        例：“说明：本样例故意缺失质权人、质押起止日期，…”里的“质权人”不是标签。
        行首出现说明类标记一律排除；带编号的说明行（“3. 用于验证…”）需同时含
        “故意/验证/伪造”等测试性措辞，避免误伤正常条款。
        """
        text = squeeze(line)
        if not text:
            return False
        if any(text.startswith(marker) for marker in ("说明", "注：", "注:", "备注", "提示：", "提示:", "注意：")):
            return True
        return any(marker in text for marker in ("故意", "本样例", "用于验证", "不得编造"))

    @staticmethod
    def _declared_zone_end(page_text: str, segment: Segment) -> int:
        """段内声明字段区的结束位置：第一个分节标题（如「一、」「（一）」）之前。"""
        section = re.compile(r"(?m)^\s*(?:[一二三四五六七八九十]+[、.]|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\))")
        match = section.search(page_text, segment.start, segment.end)
        return match.start() if match else segment.end

    # ------------------------------------------------------------ 记录级字段
    def _event_date(self, index: DocumentIndex, segment: Segment) -> dict[str, Any] | None:
        page = index.page(segment.page)
        assert page is not None
        date_pattern = re.compile(DATE_TOKEN)
        for anchor in EVENT_DATE_ANCHORS:
            label = compile_anchor(anchor)
            for match in label.finditer(page.text, segment.start, segment.end):
                window_end = min(segment.end, match.end() + 40)
                window = page.text[match.end() : window_end]
                found = date_pattern.search(window)
                if found:
                    raw = found.group(0)
                    from app.common.text import date as parse_date

                    evidence = index.evidence_for_span(page.page, match.start(), min(page_end(page.text), match.end() + found.end()))
                    return {"date": parse_date(raw), "raw": raw, "evidence": evidence}
        # 退一步：段内第一个日期
        found = date_pattern.search(page.text, segment.start, segment.end)
        if found:
            from app.common.text import date as parse_date

            evidence = index.evidence_for_span(page.page, found.start(), found.end())
            return {"date": parse_date(found.group(0)), "raw": found.group(0), "evidence": evidence}
        return None

    def _company_code(self, index: DocumentIndex) -> str | None:
        text = index.full_text()
        for anchor in COMPANY_ANCHORS:
            label = compile_anchor(anchor)
            for match in label.finditer(text):
                window = text[match.end() : match.end() + 40]
                found = COMPANY_PATTERN.search(window)
                if found:
                    return found.group(1)
        found = COMPANY_PATTERN.search(text)
        return found.group(1) if found else None

    def _company_evidence(self, index: DocumentIndex, segment: Segment, company_code: str | None) -> dict[str, Any] | None:
        if not company_code:
            return None
        page = index.page(segment.page)
        assert page is not None
        for anchor in COMPANY_ANCHORS:
            label = compile_anchor(anchor)
            for match in label.finditer(page.text, segment.start, segment.end):
                window_end = min(len(page.text), match.end() + 40)
                found = COMPANY_PATTERN.search(page.text[match.end() : window_end])
                if found and found.group(1) == company_code:
                    start = match.start()
                    end = match.end() + found.end()
                    return index.evidence_for_span(page.page, start, end)
        # 段内直接出现代码也可作为弱证据
        found = COMPANY_PATTERN.search(page.text, segment.start, segment.end)
        if found and found.group(1) == company_code:
            return index.evidence_for_span(page.page, found.start(), found.end())
        return None

    # ------------------------------------------------------------ 表格路径
    def _table_fields(self, index: DocumentIndex, segment: Segment) -> dict[str, dict[str, Any]] | None:
        """按表头-数据行结构定位字段，比文本正则更可靠。"""
        page = index.page(segment.page)
        if page is None:
            return None
        table_lines = [line for line in page.lines if line.kind == "table" and line.row is not None]
        if not table_lines:
            return None
        rows: dict[int, dict[int, LineRecord]] = {}
        for line in table_lines:
            rows.setdefault(line.row or 0, {})[line.column or 0] = line
        specs = FIELD_SPECS[segment.announcement_type]
        labels = [spec.label for spec in specs]
        header_row: int | None = None
        header_map: dict[int, str] = {}
        for row_index in sorted(rows):
            cells = rows[row_index]
            hit = 0
            mapping: dict[int, str] = {}
            for column, line in cells.items():
                spec = self._header_spec(line.text, specs)
                if spec is not None:
                    mapping[column] = spec.field_code
                    hit += 1
            if hit >= 2 and hit > len(header_map):
                header_row, header_map = row_index, mapping
        _ = labels
        if header_row is None or not header_map:
            return None
        data_rows = [row for row in sorted(rows) if row > header_row]
        if not data_rows:
            return None
        data_row = rows[data_rows[0]]
        found: dict[str, dict[str, Any]] = {}
        for column, field_code in header_map.items():
            line = data_row.get(column)
            if line is None or not line.text.strip():
                continue
            found[field_code] = {"value": line.text.strip(), "page": page.page, "row": line.row, "column": line.column}
        return found or None

    @staticmethod
    def _header_spec(cell_text: str, specs: tuple[FieldSpec, ...]) -> FieldSpec | None:
        """把表头单元格映射到字段。

        支持两种表头：契约字段代码（pledged_shares）与中文标签（质押股数）。
        表格型资料用字段代码更常见，必须同时支持，否则整份表会被判成“无记录”。
        """
        normalized = squeeze(cell_text).strip().lower().replace(" ", "")
        if not normalized:
            return None
        for spec in specs:
            if normalized == spec.field_code.lower():
                return spec
        for spec in specs:
            for label in spec.anchors + spec.weak_anchors:
                candidate = squeeze(label)
                if normalized == candidate.lower() or normalized.startswith(candidate.lower()):
                    return spec
        # 记录级列名（英文/中文）
        return None

    # ------------------------------------------------------------ 表格文档整体路径
    def extract_table_document(
        self, document_id: str, index: DocumentIndex, announcement_types: Iterable[str]
    ) -> dict[str, Any] | None:
        """整个文档就是一张表的场景（CSV/XLSX）：每个数据行产出记录，字段来自表头。"""
        page = index.page(1)
        if page is None:
            return None
        table_lines = [line for line in page.lines if line.kind == "table" and line.row is not None]
        if not table_lines:
            return None
        rows: dict[int, dict[int, LineRecord]] = {}
        for line in table_lines:
            rows.setdefault(line.row or 0, {})[line.column or 0] = line

        # 识别工作表分隔行（#sheet:xxx）
        sheet_rows = {row_index: cells for row_index, cells in rows.items() if any(
            line.text.startswith("#sheet:") for line in cells.values()
        )}

        best: tuple[int, str, dict[int, FieldSpec]] | None = None
        for row_index in sorted(rows):
            cells = rows[row_index]
            for kind in announcement_types:
                specs = FIELD_SPECS[kind]
                mapping: dict[int, FieldSpec] = {}
                for column, line in cells.items():
                    spec = self._header_spec(line.text, specs)
                    if spec is not None:
                        mapping[column] = spec
                score = len(mapping)
                if score >= 2 and (best is None or score > len(best[2])):
                    best = (row_index, kind, mapping)
        if best is None:
            return None
        header_row, kind, mapping = best
        data_rows = [row for row in sorted(rows) if row > header_row]
        if header_row in sheet_rows:
            data_rows = [row for row in data_rows if row not in sheet_rows]

        records: list[dict[str, Any]] = []
        evidence_all: list[dict[str, Any]] = []
        for row_index in data_rows[: self.config.max_events]:
            cells = rows[row_index]
            if not any(line.text.strip() for line in cells.values()):
                continue
            specs = FIELD_SPECS[kind]
            fields: list[dict[str, Any]] = []
            row_evidence: list[dict[str, Any]] = []
            company_code = None
            event_date = None
            for column, line in cells.items():
                if line.text.strip() and re.fullmatch(r"\d{6}", line.text.strip()):
                    company_code = company_code or line.text.strip()
            for column, line in cells.items():
                from app.common.text import date as parse_date

                if parse_date(line.text) and event_date is None:
                    event_date = parse_date(line.text)
            for spec in specs:
                line = next((cells[column] for column, mapped in mapping.items() if mapped is spec and column in cells), None)
                if line is None or not line.text.strip():
                    fields.append(
                        field_payload(
                            spec.field_code,
                            None,
                            expect=spec.expect,
                            evidence_ids=[],
                            reason=f"表格第 {row_index} 行未填「{spec.label}」",
                        )
                    )
                    continue
                evidence = index.evidence_for_lines(page.page, [line])
                row_evidence.append(evidence)
                fields.append(
                    field_payload(spec.field_code, line.text.strip(), expect=spec.expect, evidence_ids=[evidence["evidence_id"]])
                )
            # 记录级字段的证据
            code_line = next((line for line in cells.values() if line.text.strip() == company_code), None)
            code_evidence = index.evidence_for_lines(page.page, [code_line]) if code_line is not None else None
            date_line = next((line for line in cells.values() if event_date and parse_date(line.text) == event_date), None)
            date_evidence = index.evidence_for_lines(page.page, [date_line]) if date_line is not None else None
            for extra in (code_evidence, date_evidence):
                if extra:
                    row_evidence.append(extra)
            record_fields = [
                {
                    "field_code": "document_id",
                    "raw_value": document_id,
                    "value": document_id,
                    "unit": "none",
                    "evidence_ids": [],
                    "status": "verified",
                    "reason": None,
                },
                {
                    "field_code": "company_code",
                    "raw_value": company_code,
                    "value": company_code,
                    "unit": "none",
                    "evidence_ids": [code_evidence["evidence_id"]] if code_evidence else [],
                    "status": "verified" if code_evidence else "needs_review",
                    "reason": None if code_evidence else "表格行内未定位到 6 位证券代码",
                },
                {
                    "field_code": "event_date",
                    "raw_value": event_date,
                    "value": event_date,
                    "unit": "none",
                    "evidence_ids": [date_evidence["evidence_id"]] if date_evidence else [],
                    "status": "verified" if date_evidence else "needs_review",
                    "reason": None if date_evidence else "表格行内未定位到日期",
                },
            ]
            event_id = make_id("event", document_id, "table", kind, row_index, length=20)
            records.append(
                {
                    "event_id": event_id,
                    "document_id": document_id,
                    "company_code": company_code or "000000",
                    "event_date": event_date,
                    "announcement_type": kind,
                    "fields": record_fields + fields,
                }
            )
            evidence_all.extend(row_evidence)
        if not records:
            return None
        return {
            "records": records,
            "evidence": evidence_all,
            "warnings": list(dict.fromkeys(self.warnings + [f"表格文档：识别为 {TYPE_LABELS[kind]}，表头行 {header_row}"])),
            "company_code": None,
            "text_length": len(index.full_text()),
        }


def page_end(text: str) -> int:
    return len(text)
