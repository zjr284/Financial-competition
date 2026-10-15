"""Validate historical provider records without declaring human financial review."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from app.common.ids import digest_of, sha256_bytes


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field: ' + key)
        result[key] = value
    return result


def _load(raw):
    def invalid(value):
        raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(raw.decode('utf-8'), parse_float=Decimal, parse_constant=invalid,
                      object_pairs_hook=_unique_object)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('Expected an exact decimal source value')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid market decimal') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('Market value must be positive and finite')
    return result


def validate_daily_market_snapshot(raw, cross_raw, company_code, as_of_date):
    """Eastmoney reported cap/shares + Tencent unadjusted close, all same day.

    Returns a source-check receipt, not a public Calculation or verified Fact.
    The market cap is read from the provider; arithmetic only checks consistency.
    """
    if len(company_code) != 6 or not company_code.isdigit():
        raise ValueError('Expected a six-digit company code')
    day = date.fromisoformat(as_of_date).isoformat()
    data, cross = _load(raw), _load(cross_raw)
    if not isinstance(data, dict) or not isinstance(cross, dict):
        raise ValueError('Expected provider JSON objects')
    if data.get('success') is not True or type(data.get('code')) is not int or data['code'] != 0:
        raise ValueError('Daily value provider returned an error')
    result = data.get('result')
    if not isinstance(result, dict):
        raise ValueError('Missing daily result object')
    rows = result.get('data', [])
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('Expected exactly one daily value record')
    row = rows[0]
    if not isinstance(row, dict):
        raise ValueError('Expected a daily record object')
    if row.get('SECURITY_CODE') != company_code or row.get('SECUCODE') != company_code + '.SZ':
        raise ValueError('Daily value company or exchange mismatch')
    if row.get('TRADE_DATE') != day + ' 00:00:00':
        raise ValueError('Daily value date mismatch')
    cross_data = cross.get('data')
    if not isinstance(cross_data, dict):
        raise ValueError('Missing cross-source data object')
    stock = cross_data.get('sz' + company_code, {})
    if not isinstance(stock, dict):
        raise ValueError('Expected a stock object')
    bars = stock.get('day', [])
    if (type(cross.get('code')) is not int or cross['code'] != 0 or not isinstance(bars, list)
            or len(bars) != 1 or not isinstance(bars[0], list) or not bars[0] or bars[0][0] != day):
        raise ValueError('Missing same-day unadjusted Tencent day bar')
    if len(bars[0]) < 6:
        raise ValueError('Incomplete daily bar')
    if not {'CLOSE_PRICE', 'TOTAL_SHARES', 'TOTAL_MARKET_CAP'} <= row.keys():
        raise ValueError('Missing market value fields')
    close = _number(row['CLOSE_PRICE'])
    shares = _number(row['TOTAL_SHARES'])
    cap = _number(row['TOTAL_MARKET_CAP'])
    if shares != shares.to_integral_value():
        raise ValueError('Total shares must be an integer count')
    if close != _number(bars[0][2]):
        raise ValueError('Close differs between independent providers')
    with localcontext() as context:
        context.prec = 50
        implied = (close * shares).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if cap != implied:
            raise ValueError('Reported cap differs from price times TOTAL shares')
    def plain(value):
        text = format(value, 'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
    numbers = {key: plain(value) for key, value in
               [('close_price', close), ('total_shares', shares), ('market_cap', cap)]}
    basis = {'company_code': company_code, 'as_of_date': day, **numbers,
             'close_price_unit': 'CNY/share', 'total_shares_unit': 'share', 'market_cap_unit': 'CNY',
             'source_sha256': sha256_bytes(raw), 'cross_source_sha256': sha256_bytes(cross_raw)}
    return {**basis, 'calculation_id': 'calc_market_source_' + digest_of(basis)[:20],
            'formula': 'close_price * total_shares (cross-check only)',
            'checked_value': format(implied, 'f'), 'checked_value_unit': 'CNY', 'source_check_status': 'consistent',
            'review_status': 'needs_review', 'source_field': '$.result.data[0].TOTAL_MARKET_CAP',
            'limitations': ['Historical provider snapshots retrieved later; not exchange-authenticated tape',
                            'Structured field confidence is not human approval or PDF coordinate accuracy']}


def is_historical_market_observation(fact, evidence, document, as_of_date):
    """Recognize only fixed native daily market records, not late financial reports.

    Observation date does not certify the first publication or historical vintage.
    Caller must retain a review warning even after the fact has been adopted.
    """
    from urllib.parse import urlparse, parse_qs
    if (fact.get('metric') != 'market_cap' or fact.get('unit') != 'CNY'
            or fact.get('period_type') != 'instant' or fact.get('statement_scope') != 'not_applicable'
            or fact.get('period_start') != as_of_date or fact.get('period_end') != as_of_date
            or document.get('media_type') != 'text/plain'
            or evidence.get('extraction_method') != 'table'
            or evidence.get('table_id') != 'json:$.result.data' or evidence.get('row') != 1
            or evidence.get('confidence') != 1.0):
        return False
    url = urlparse(document.get('source_url') or '')
    query = parse_qs(url.query)
    expected_filter = '(SECURITY_CODE="' + fact['company_code'] + '")(TRADE_DATE=\'' + as_of_date + '\')'
    if (url.scheme != 'https' or url.netloc != 'datacenter-web.eastmoney.com'
            or url.path != '/api/data/v1/get' or query.get('reportName') != ['RPT_VALUEANALYSIS_DET']
            or query.get('filter') != [expected_filter]):
        return False
    try:
        row = _load(('{' + ','.join(evidence['text'].splitlines()) + '}').encode('utf-8'))
        return (row['SECURITY_CODE'] == fact['company_code']
                and row['SECUCODE'] == fact['company_code'] + '.SZ'
                and row['TRADE_DATE'] == as_of_date + ' 00:00:00'
                and _number(row['TOTAL_MARKET_CAP']) == _number(fact['value']))
    except (ValueError, KeyError, TypeError):
        return False
