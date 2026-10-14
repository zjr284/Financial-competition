"""Valuation exports reorganize pinned facts and tool records without arithmetic."""
from __future__ import annotations

import html
import json
import jsonschema
from pathlib import Path
from typing import Any

from app.common.ids import digest_of
from app.errors import AppError
from app.facts.reviewed_store import ReviewedFactPort


COLUMNS = ('kind', 'id', 'company_code', 'metric', 'value', 'unit', 'period_start',
           'period_end', 'status', 'reason', 'input_fact_ids', 'input_calculation_ids',
           'evidence_ids', 'sources', 'chain_status')


def load_pinned_sources(path: Path, result: dict, documents: Any, evidence: Any,
                        expected_hash: str | None) -> dict | None:
    """Never use the current global reviewer manifest for an old Run's export."""
    if not path.is_file():
        if expected_hash:
            raise AppError('VALIDATION_ERROR', '本次运行的事实快照缺失，无法导出完整来源')
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not expected_hash or digest_of(payload) != expected_hash:
            raise ValueError('Snapshot hash differs from recorded Run input')
        metrics = sorted({f['metric'] for bundle in payload['documents'] for f in bundle['facts']})
        port = ReviewedFactPort(path, documents, evidence, snapshot=payload)
        facts = port.extract_facts(result['input_document_ids'], metrics)
        source_ids = {eid for f in facts for eid in f['evidence_ids']}
        sources = {eid: evidence.get_evidence(eid) for eid in source_ids}
        docs = {e['document_id']: documents.get_document(e['document_id']) for e in sources.values()}
        return {'facts': facts, 'evidence': list(sources.values()), 'documents': list(docs.values())}
    except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
        raise AppError('VALIDATION_ERROR', '运行快照或来源版本不匹配，拒绝导出来源明细') from exc


def rows_for_valuation(result: dict, sources: dict | None = None) -> tuple[list[dict], list[str]]:
    facts = {f['fact_id']: f for f in (sources or {}).get('facts', [])}
    evidence = {e['evidence_id']: e for e in (sources or {}).get('evidence', [])}
    documents = {d['document_id']: d for d in (sources or {}).get('documents', [])}
    calculations = result.get('calculations', [])
    by_id = {c['calculation_id']: c for c in calculations}
    problems = []
    if len(by_id) != len(calculations):
        problems.append('计算 ID 重复')
    dependencies = {cid: set(c['input_calculation_ids']) for cid, c in by_id.items()}
    for refs in dependencies.values():
        if refs - by_id.keys():
            problems.append('缺少上游计算记录')
    remaining = {cid: refs & by_id.keys() for cid, refs in dependencies.items()}
    ready = [cid for cid, refs in remaining.items() if not refs]
    visited = set()
    while ready:
        cid = ready.pop()
        if cid in visited:
            continue
        visited.add(cid)
        for parent, refs in remaining.items():
            if cid in refs:
                refs.remove(cid)
                if not refs:
                    ready.append(parent)
    if len(visited) != len(by_id):
        problems.append('计算引用存在循环')
    if any(fid not in facts for c in calculations for fid in c['input_fact_ids']):
        problems.append('原始事实未读取；保留引用，不补写原始金额')
    rows = []

    def row(**values):
        item = dict.fromkeys(COLUMNS, '')
        item.update(values)
        rows.append(item)

    def source_text(fact):
        locations = []
        for eid in fact['evidence_ids']:
            item = evidence.get(eid)
            doc = documents.get(item['document_id']) if item else None
            if not item or not doc:
                problems.append('证据定位或文档版本未读取')
                locations.append(eid + '; unavailable')
            else:
                locations.append(f"{eid}; document={item['document_id']}; page={item['page']}; "
                                 f"bbox={'/'.join(map(str, item['bbox']))}; sha256={doc['sha256']}")
        return ' | '.join(locations)

    data = result['data']
    interval = data['valuation_range']
    for name in ('low', 'base', 'high'):
        # Explicit scenario tags originate in B, not from matching equal numbers.
        ids = [c['calculation_id'] for c in calculations
               if c['parameters'].get('valuation_scenario') == name
               and c['formula_code'] == 'relative_equity_value'
               and c['value'] == interval[name] and c['status'] == 'verified']
        if data['method']=='relative_pe' and interval[name] is not None and len(ids)!=1:
            problems.append('估值汇总缺少唯一场景计算引用；保留已有结果与证据待复核')
        row(kind='估值结果（计算得出）', id=name, metric=data['method'],
            value=interval[name] if interval[name] is not None else '', unit=interval['unit'],
            period_end=data['as_of_date'], status=result['status'], reason=interval.get('reason') or '',
            input_calculation_ids=' | '.join(ids), evidence_ids=' | '.join(result['evidence_ids']))
    seen_facts = set()
    for calc in calculations:
        params = calc['parameters']
        row(kind='计算得出', id=calc['calculation_id'], metric=calc['formula_code'],
            company_code=params.get('company_code', ''), value=calc['value'] if calc['value'] is not None else '',
            unit=calc['unit'], period_start=params.get('period_start', ''), period_end=params.get('period_end', ''),
            status=calc['status'], reason=calc.get('reason') or '',
            input_fact_ids=' | '.join(calc['input_fact_ids']), input_calculation_ids=' | '.join(calc['input_calculation_ids']))
        for fid in calc['input_fact_ids']:
            if fid in seen_facts:
                continue
            seen_facts.add(fid)
            fact = facts.get(fid)
            if fact is None:
                row(kind='原始事实引用（未读取）', id=fid, status='unavailable', reason='未读取本次运行原始事实')
                continue
            row(kind='原始事实（运行快照）', id=fid, company_code=fact['company_code'], metric=fact['metric'],
                value=fact['value'] if fact['value'] is not None else '', unit=fact['unit'],
                period_start=fact['period_start'], period_end=fact['period_end'], status=fact['review_status'],
                reason=fact.get('reason') or '', evidence_ids=' | '.join(fact['evidence_ids']), sources=source_text(fact))
    problems = list(dict.fromkeys(problems))
    state = 'needs_review' if problems else 'references_preserved'
    for item in rows:
        item['chain_status'] = state
    return rows, problems


def csv_cell(value: Any) -> str:
    """Escape spreadsheet formula text while preserving decimal financial values."""
    import re
    text = str(value)
    trimmed = text.lstrip()
    if trimmed.startswith(('=', '+', '@')) or text.startswith(('\t', '\r', '\n')) or (
        trimmed.startswith('-') and not re.fullmatch(r'-\d+(\.\d+)?', trimmed)
    ):
        return "'" + text
    return text


def html_for_valuation(result: dict, sources: dict | None = None) -> str:
    rows, problems = rows_for_valuation(result, sources)
    escape = lambda value: html.escape(str(value), quote=True)
    parts = ['<h2>估值与计算来源</h2>',
             '<p>派生利润为“计算得出”；工具核算状态不代表可比性人工复核已完成。原始资料仍需独立复核。</p>']
    if problems:
        parts.append('<p class="needs_review">' + escape('；'.join(problems)) + '</p>')
    parts.append('<table><thead><tr>' + ''.join('<th>' + escape(c) + '</th>' for c in COLUMNS) + '</tr></thead><tbody>')
    for row in rows:
        parts.append('<tr>' + ''.join('<td>' + escape(row[c]) + '</td>' for c in COLUMNS) + '</tr>')
    parts.extend(['</tbody></table>', '<h2>完整运行记录</h2><pre>',
                  escape(json.dumps(result, ensure_ascii=False, indent=2)), '</pre>'])
    return '\n'.join(parts)
