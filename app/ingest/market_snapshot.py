"""Import deterministic market JSON locators as private needs-review candidates."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from app.common.contracts import validate_contract
from app.common.ids import make_id
from app.facts.review_workflow import add_fact, attach_document
from app.tools.market_sources import validate_daily_market_snapshot


def provider_urls(company_code, as_of_date):
    eastmoney = ('https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET'
                 '&columns=ALL&filter=(SECURITY_CODE%3D%22' + company_code + '%22)'
                 '(TRADE_DATE%3D%27' + as_of_date + '%27)&pageSize=10&pageNumber=1')
    tencent = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz' + company_code
               + ',day,' + as_of_date + ',' + as_of_date + ',10,')
    return eastmoney, tencent



def _record_fragments(text):
    """Locate literal fields in result.data[0], never similarly named root fields."""
    decoder = json.JSONDecoder(parse_float=Decimal)

    def skip(position):
        while position < len(text) and text[position].isspace():
            position += 1
        return position

    def member(start, name):
        cursor = skip(start)
        if text[cursor] != '{':
            raise ValueError('Expected an object at the source path')
        cursor = skip(cursor + 1)
        while text[cursor] != '}':
            key_start = cursor
            key, cursor = decoder.raw_decode(text, cursor)
            cursor = skip(cursor)
            if text[cursor] != ':':
                raise ValueError('Invalid JSON member')
            value_start = skip(cursor + 1)
            _, value_end = decoder.raw_decode(text, value_start)
            if key == name:
                return value_start, value_end, text[key_start:value_end]
            cursor = skip(value_end)
            if text[cursor] == ',':
                cursor = skip(cursor + 1)
        raise ValueError('Missing source field: ' + name)

    result_start, _, _ = member(0, 'result')
    data_start, _, _ = member(result_start, 'data')
    if text[data_start] != '[':
        raise ValueError('Expected source records array')
    record_start = skip(data_start + 1)
    return [member(record_start, field)[2] for field in
            ('SECURITY_CODE', 'SECUCODE', 'TRADE_DATE', 'TOTAL_MARKET_CAP', 'TOTAL_SHARES', 'CLOSE_PRICE')]

def register_market_candidate(app, manifest_path, raw, cross_raw, company_code, as_of_date):
    receipt = validate_daily_market_snapshot(raw, cross_raw, company_code, as_of_date)
    source_url, cross_url = provider_urls(company_code, as_of_date)
    # Archive the exact response as TXT; do not synthesize a fictional source PDF.
    # Preserve unknown first-publication dates. TRADE_DATE is an observation,
    # not a document disclosure date; ingested_at records later collection.
    # The Tencent response may also include live quotes beyond its historical bar.
    doc = app.upload_document(company_code + '_' + as_of_date + '_daily_market_raw.txt', raw,
                              source_url=source_url, disclosure_date=None)
    cross = app.upload_document(company_code + '_' + as_of_date + '_unadjusted_day_raw.txt', cross_raw,
                                source_url=cross_url, disclosure_date=None)
    app.parse_document(doc['document_id']);app.parse_document(cross['document_id'])
    text = raw.decode('utf-8')
    snippets = _record_fragments(text)
    evidence_id = make_id('ev', doc['document_id'], 'market-json-v2', receipt['source_field'])
    evidence = {'evidence_id': evidence_id, 'document_id': doc['document_id'], 'page': 1,
                'bbox': [0, 0, 1000, 1000], 'text': '\n'.join(snippets),
                'extraction_method': 'table', 'confidence': 1.0,
                'table_id': 'json:$.result.data', 'row': 1, 'column': None}
    validate_contract('Evidence', evidence, label='Evidence')
    # Confidence describes exact JSON field matching, not original-layout accuracy.
    app.store.insert_evidence([evidence])
    stored = app.store.get_document_row(doc['document_id'])
    parsed = json.loads(stored['parse_manifest'])
    if evidence_id not in parsed['evidence_ids']:
        parsed['evidence_ids'].append(evidence_id)
    warning = '行情 JSON 为精确字段定位；bbox 仅逻辑区域，非原始 PDF/OCR 框选；来源一致不等于人工复核。'
    if warning not in parsed['warnings']:
        parsed['warnings'].append(warning)
    app.documents.mark_parsed(doc['document_id'], doc['page_count'], stored['parse_status'], parsed)
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for document in (doc, cross):
        if not any(b['document_id'] == document['document_id'] for b in manifest['documents']):
            attach_document(app, path, document['document_id'])
            manifest = json.loads(path.read_text(encoding='utf-8'))
    existing = [f for b in manifest['documents'] for f in b['facts']
                if f['company_code'] == company_code and f['metric'] == 'market_cap'
                and f['period_end'] == as_of_date and f['evidence_ids'] == [evidence_id]]
    if existing:
        if len(existing) != 1 or existing[0]['value'] != receipt['market_cap']:
            raise ValueError('Existing candidate conflicts with the fixed provider response')
        fact = existing[0]
    else:
        fact = add_fact(app, path, document_id=doc['document_id'], evidence_id=evidence_id,
                        company_code=company_code, metric='market_cap', value=receipt['market_cap'],
                        period_start=as_of_date, period_end=as_of_date, period_type='instant',
                        statement_scope='not_applicable', verified=False,
                        reason='提供商直接报告的历史总市值；日期、代码和未复权收盘价交叉校验通过，待人工确认市场快照采用。')
    return {**receipt, 'review_status': fact['review_status'], 'archived_at': doc['ingested_at'], 'document_id': doc['document_id'], 'cross_document_id': cross['document_id'],
            'fact_id': fact['fact_id'], 'evidence_id': evidence_id,
            'source_url': source_url, 'cross_source_url': cross_url}


def main():
    import argparse
    from app.config import Settings
    from app.container import build_app
    from app.common.ids import digest_of
    from app.facts.review_workflow import init_manifest
    parser = argparse.ArgumentParser(description='固定历史原始行情并登记待复核市值；不会代签人工复核')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--primary', type=Path, required=True)
    parser.add_argument('--cross', type=Path, required=True)
    parser.add_argument('--company', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('采用凭据文件已存在；不覆盖已有记录，请指定新输出文件')
    raw, cross_raw = args.primary.read_bytes(), args.cross.read_bytes()
    validate_daily_market_snapshot(raw, cross_raw, args.company, args.date)
    app = build_app(Settings(data_dir=args.data_dir))
    try:
        if not args.manifest.exists():
            init_manifest(app, args.manifest, [])
        receipt = register_market_candidate(app, args.manifest, raw, cross_raw, args.company, args.date)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps({'source_check_status': receipt['source_check_status'],
                          'review_status': receipt['review_status'], 'output_hash': digest_of(receipt)},
                         ensure_ascii=False))
    finally:
        app.close()


if __name__ == '__main__':
    main()
