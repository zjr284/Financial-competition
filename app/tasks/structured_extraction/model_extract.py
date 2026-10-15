"""Ground model raw-value candidates in bounded source windows, then normalize locally."""
from __future__ import annotations

from datetime import date
import re

from app.common.ids import make_id
from app.common.text import field_payload, parse_quantity, to_halfwidth
from app.errors import validation_error
from app.tasks.structured_extraction.dictionary import FIELD_SPECS


def _compact(text):
    return re.sub(r'\s+', '', to_halfwidth(text))


def source_windows(index, *, exclude=()):
    """At most 16 short, spatially contiguous excerpts from the first two pages."""
    windows = []
    for page in index.pages[:2]:
        chunk, size = [], 0
        stop = False
        for line in page.lines[:70]:
            if not line.text.strip():
                continue
            if re.match(r'^\s*2[、.．]', line.text) and '累计' in line.text and '质押' in line.text:
                stop = True  # Keep cumulative holdings out of the new-pledge candidate window.
                break
            if any(box['page'] == page.page and box['bbox'][1] <= (line.bbox[1] + line.bbox[3]) / 2 <= box['bbox'][3]
                   for box in exclude):
                continue
            if chunk and size + len(line.text) > 240:
                windows.append(index.evidence_for_lines(page.page, chunk))
                chunk, size = [], 0
            if len(windows) >= 16:
                return windows
            chunk.append(line)
            size += len(line.text) + 1
        if chunk:
            windows.append(index.evidence_for_lines(page.page, chunk))
        if stop:
            return windows[:16]
        if len(windows) >= 16:
            break
    return windows[:16]


def _field(code, candidate, expect, evidence):
    raw = candidate.get('raw_value') if candidate else None
    refs = candidate.get('evidence_ids', []) if candidate else []
    cited = [eid for eid in evidence if eid in refs]
    text = ''.join(evidence[eid]['text'] for eid in cited)
    if raw is None or not raw.strip():
        return field_payload(code, None, expect=expect, reason='模型未定位到原文，需人工复核')
    if not cited or not _compact(raw) or not any(_compact(raw) in _compact(evidence[eid]['text']) for eid in cited):
        return field_payload(code, None, expect=expect, reason='候选原值不在所引用原文中，已拒绝')
    reason = '模型原文候选；仅完成定位及确定性归一化，仍需人工复核'
    value = field_payload(code, raw, expect=expect, evidence_ids=cited, status='needs_review', reason=reason)
    if expect == 'date' and value['value']:
        try:
            date.fromisoformat(value['value'])
        except ValueError:
            value.update(value=None, reason='原文日期不是有效的公历日期')
    if expect == 'percent' and not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)%', _compact(raw).replace(',', '')):
        value.update(value=None, reason='原文不是单个明确的百分数，拒绝猜测比例')
    if expect == 'share' and parse_quantity(raw)[2] != '股':
        value.update(value=None, reason='原文缺少股数单位，拒绝猜测表头量纲')
    foreign = ['美元', '港元', '港币', '欧元', '日元', '英镑', '澳元', '加元', 'USD', 'HKD', 'EUR', 'JPY', 'GBP', 'AUD', 'CAD']
    if expect == 'amount' and (value['unit'] != 'CNY' or parse_quantity(raw)[2] != '元' or any(x in text.upper() for x in foreign)):
        value.update(value=None, reason='人民币金额或币种不明确，拒绝直接换算')
    return value


def grid_sources(index, cells):
    """Select complete native grids before the cumulative-pledge section.

    Row/column identifiers stay local. Captions contain only public header text;
    raw-value checks still use the actual cell text, never the generated caption.
    """
    cutoff = next(((page.page, line.bbox[1]) for page in index.pages[:2] for line in page.lines
                   if re.match(r'^\s*2[、.．]', line.text) and '累计' in line.text and '质押' in line.text), (3, 0))
    tables = {}
    for cell in cells or []:
        tables.setdefault((cell['page'], cell['table_id']), []).append(cell)
    selected, captions, boxes = [], [], []
    for (page, _), group in sorted(tables.items()):
        # Reject a whole table when it crosses the section boundary or cap.
        if page > cutoff[0] or (page == cutoff[0] and max(c['bbox'][3] for c in group) > cutoff[1]):
            continue
        headers = {c['column']: c['text'] for c in group if c['row'] == 1}
        body = sorted((c for c in group if c['row'] > 1), key=lambda c: (c['row'], c['column']))
        if not body or len(selected) + len(body) > 64:
            continue
        selected.extend(body)
        captions.extend({'text': headers.get(c['column'], '')[:80] + '：' + c['text'],
                         'evidence_ids': [c['evidence_id']]} for c in body)
        boxes.append({'page': page, 'bbox': [min(c['bbox'][0] for c in group), min(c['bbox'][1] for c in group),
                                           max(c['bbox'][2] for c in group), max(c['bbox'][3] for c in group)]})
    return selected, captions, boxes


def extract_candidates(model, document_id, index, announcement_types, *, cells=None):
    selected, captions, boxes = grid_sources(index, cells)
    windows = source_windows(index, exclude=boxes)
    evidence_rows = windows + selected
    warning = '模型只处理前两页的有限短片段；候选不等于完整抽取或已复核事实'
    if not evidence_rows:
        return {'records': [], 'evidence': [], 'warnings': [warning]}
    evidence = {item['evidence_id']: item for item in evidence_rows}
    specs = {kind: [{'field_code': 'company_code', 'label': '证券代码', 'expect': 'text'},
                    {'field_code': 'event_date', 'label': '公告或签署日期', 'expect': 'date'}]
                   + [{'field_code': spec.field_code, 'label': spec.label, 'expect': spec.expect}
                      for spec in FIELD_SPECS[kind]] for kind in announcement_types}
    output = model.infer_json('structured_extraction', list(evidence), 'ExtractionData', {
        'document_alias': 'doc_model_0001', 'announcement_types': announcement_types, 'field_specs': specs,
        'source_items': [{'text': item['text'], 'evidence_ids': [item['evidence_id']]} for item in windows] + captions,
        'instructions_version': 'announcement-candidates-grid-v2' if selected else 'announcement-candidates-v1',
        'instructions': (
            'Extract only events actually disclosed in supplied excerpts, one record per transaction/row. '
            'Use document_alias as document_id. Every record must include company_code and event_date '
            'as FieldValue fields with literal raw_value and source evidence IDs, in addition to the listed fields. '
            'Copy raw_value verbatim including units. Set every field value=null, status=needs_review, '
            'unit=none and reason="model candidate"; local tools will normalize. Do not calculate, add units, '
            'invent dates, merge different rows, or treat cumulative positions as new transactions. '
            'For pledge, pledged_ratio means percentage of total company share capital, not of the holder holdings. '
            'A pledge ending upon release is not a known calendar date; retain its raw phrase. '
            + ('Native table cells appear in row order with header captions; a caption is not part of raw_value. '
               if selected else '') +
            'Missing fields have raw_value=null and no evidence. Return records=[] when no relevant event is supported.'
        ),
    })
    records = []
    if len(output['records']) > 20:
        raise validation_error('模型候选记录超过单文档上限')
    for ordinal, record in enumerate(output['records']):
        kind = record['announcement_type']
        if record['document_id'] != 'doc_model_0001' or kind not in announcement_types:
            raise validation_error('模型候选文档或公告类型超出请求范围')
        fields = {}
        for field in record['fields']:
            code = field['field_code']
            if code in fields:
                raise validation_error('模型候选包含重复字段')
            if any(eid not in evidence for eid in field['evidence_ids']):
                raise validation_error('模型候选包含未知来源')
            fields[code] = field
        company = _field('company_code', fields.get('company_code'), 'text', evidence)
        # Record metadata may be omitted by the model. Resolve only an explicit
        # securities-code header, never a free six-digit number in the body.
        header = next(((re.search(r'(?:证券代码|股票代码)[:：]?([0-9]{6})', _compact(e['text'])), eid)
                       for eid, e in evidence.items()
                       if re.search(r'(?:证券代码|股票代码)[:：]?([0-9]{6})', _compact(e['text']))), None)
        if header and record['company_code'] == header[0].group(1):
            company = field_payload('company_code', header[0].group(1), expect='text',
                evidence_ids=[header[1]], status='needs_review', reason='证券代码来自本地原文表头，记录仍需人工复核')
        else:
            continue
        if not company['value'] or not re.fullmatch(r'[0-9]{6}', company['value']):
            continue  # No invented placeholder security code.
        event = _field('event_date', fields.get('event_date'), 'date', evidence)
        normalized = [_field(spec.field_code, fields.get(spec.field_code), spec.expect, evidence)
                      for spec in FIELD_SPECS[kind]]
        if selected:
            for field in normalized:
                if field['field_code'].endswith('_date') and field['raw_value'] and field['value'] is None:
                    field['reason'] = '保留原文期限表述；不能确定公历日期，需人工复核'
        currency = next((f['value'] for f in normalized if f['field_code'] == 'currency'), None)
        if currency and currency.upper() not in {'人民币', 'CNY', 'RMB', '人民币元', '元'}:
            for field in normalized:
                if field['field_code'] == 'contract_amount':
                    field.update(value=None, reason='公告币种并非人民币，尚无带来源的汇率换算')
        metadata = field_payload('document_id', document_id, expect='text', status='verified')
        all_fields = [metadata, company, event] + normalized
        records.append({'event_id': make_id('event', document_id, kind, ordinal, all_fields, length=20),
                        'document_id': document_id, 'company_code': company['value'], 'event_date': event['value'],
                        'announcement_type': kind, 'fields': all_fields})
    return {'records': records, 'evidence': evidence_rows, 'warnings': [warning]}
