"""Synthetic native-provider records; exact validation is not human approval."""
import copy
import json
from pathlib import Path

import pytest
from app.common.ids import sha256_bytes
from app.facts.review_workflow import init_manifest,verify_fact
from app.ingest.market_snapshot import register_market_candidate
from app.tools.market_sources import validate_daily_market_snapshot
from app.tasks.valuation.samples.ttm_pe.demo import run_demo,HERE


def provider_payloads(code='999991',day='2026-09-30'):
    source={'success':True,'code':0,'result':{'data':[{'SECURITY_CODE':code,'SECUCODE':code+'.SZ',
        'TRADE_DATE':day+' 00:00:00','TOTAL_MARKET_CAP':250,'TOTAL_SHARES':100,'CLOSE_PRICE':2.5}]}}
    cross={'code':0,'data':{'sz'+code:{'day':[[day,'2.4','2.5','2.6','2.3','100']]}}}
    return source,cross


def encode(data):return json.dumps(data,separators=(',',':')).encode()


def test_exact_receipt_does_not_promote_to_verified_and_is_stable():
    data,cross=provider_payloads()
    receipt=validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')
    assert receipt['market_cap']=='250' and receipt['checked_value']=='250.00'
    assert receipt['review_status']=='needs_review' and receipt['source_check_status']=='consistent'
    assert receipt==validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')


def test_native_decimal_precision_without_float_multiplication():
    data,cross=provider_payloads();data['result']['data'][0].update(TOTAL_MARKET_CAP='100000000000.07',TOTAL_SHARES=10000000000007,CLOSE_PRICE='0.01')
    cross['data']['sz999991']['day'][0][2]='0.01'
    result=validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')
    assert result['market_cap']=='100000000000.07'


@pytest.mark.parametrize('field,value',[('SECURITY_CODE','999992'),('SECUCODE','999991.SH'),
 ('TRADE_DATE','2026-10-01 00:00:00'),('TOTAL_MARKET_CAP',251),('TOTAL_SHARES',100.5),
 ('CLOSE_PRICE',True),('TOTAL_MARKET_CAP',0),('CLOSE_PRICE','not-a-number')])
def test_bad_primary_fields_are_rejected(field,value):
    data,cross=provider_payloads();data['result']['data'][0][field]=value
    with pytest.raises(ValueError):validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')


@pytest.mark.parametrize('case',['adjusted_only','wrong_day','wrong_close','extra_row','error'])
def test_bad_cross_source_is_rejected(case):
    data,cross=provider_payloads();stock=cross['data']['sz999991']
    if case=='adjusted_only':stock['qfqday']=stock.pop('day')
    if case=='wrong_day':stock['day'][0][0]='2026-09-29'
    if case=='wrong_close':stock['day'][0][2]='2.6'
    if case=='extra_row':stock['day'].append(copy.deepcopy(stock['day'][0]))
    if case=='error':cross['code']=False
    with pytest.raises(ValueError):validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')


def test_duplicate_json_fields_and_nonfinite_values_are_rejected():
    data,cross=provider_payloads()
    for raw in (encode(data).replace(b'"TOTAL_SHARES":100',b'"TOTAL_SHARES":100,"TOTAL_SHARES":101'),
                encode(data).replace(b'"TOTAL_MARKET_CAP":250',b'"TOTAL_MARKET_CAP":NaN')):
        with pytest.raises(ValueError):validate_daily_market_snapshot(raw,encode(cross),'999991','2026-09-30')


def test_registration_preserves_raw_bytes_needs_review_and_logical_locator(app,tmp_path):
    manifest=tmp_path/'facts.json';init_manifest(app,manifest,[])
    data,cross=provider_payloads();raw,cross_raw=encode(data),encode(cross)
    result=register_market_candidate(app,manifest,raw,cross_raw,'999991','2026-09-30')
    doc=app.get_document(result['document_id']);payload,_=app.get_document_content(doc['document_id'])
    assert payload==raw and doc['sha256']==sha256_bytes(raw)
    fact=json.loads(manifest.read_text())['documents'][0]['facts'][0]
    assert fact['review_status']=='needs_review' and fact['metric']=='market_cap' and fact['value']=='250'
    e=app.get_evidence(result['evidence_id']);assert e['extraction_method']=='table' and e['confidence']==1
    assert 'TOTAL_MARKET_CAP' in e['text'] and '250' in e['text']
    parsed=app.parse_document(doc['document_id'])
    assert e['evidence_id'] in parsed['evidence_ids'] and any('逻辑区域' in w for w in parsed['warnings'])
    assert not json.loads(manifest.read_text()).get('review_history')
    again=register_market_candidate(app,manifest,raw,cross_raw,'999991','2026-09-30')
    assert again['fact_id']==result['fact_id']
    assert sum(len(b['facts']) for b in json.loads(manifest.read_text())['documents'])==1
    # Explicit human decision in this isolated synthetic test only.
    verify_fact(app,manifest,result['fact_id'],'synthetic test reviewer')
    assert register_market_candidate(app,manifest,raw,cross_raw,'999991','2026-09-30')['review_status']=='verified'
    assert json.loads(manifest.read_text())['documents'][0]['facts'][0]['review_status']=='verified'


def test_invalid_registration_does_not_mutate_manifest(app,tmp_path):
    manifest=tmp_path/'facts.json';init_manifest(app,manifest,[]);before=manifest.read_bytes()
    data,cross=provider_payloads();data['result']['data'][0]['TOTAL_MARKET_CAP']=999
    with pytest.raises(ValueError):register_market_candidate(app,manifest,encode(data),encode(cross),'999991','2026-09-30')
    assert manifest.read_bytes()==before


def test_pending_peer_market_source_is_returned_in_valuation_review_items():
    sources=json.loads((HERE/'inputs.json').read_text())
    for fact in sources['facts']:
        if fact['metric']=='market_cap':fact.update(review_status='needs_review',reason='Pending market adoption')
    result=run_demo(sources)
    assert result['data']['valuation_range']['base'] is None
    assert sum('raw market cap needs review' in w for w in result['warnings'])==3
    market_ids={e for f in sources['facts'] if f['metric']=='market_cap' for e in f['evidence_ids']}
    assert market_ids <= set(result['evidence_ids'])


def test_locator_uses_the_record_path_not_an_unrelated_root_field(app,tmp_path):
    path=tmp_path/'facts.json';init_manifest(app,path,[])
    data,cross=provider_payloads()
    data={'TOTAL_MARKET_CAP':999,**data}
    record=register_market_candidate(app,path,encode(data),encode(cross),'999991','2026-09-30')
    text=app.get_evidence(record['evidence_id'])['text']
    assert '"TOTAL_MARKET_CAP":250' in text and '"TOTAL_MARKET_CAP":999' not in text


@pytest.mark.parametrize('data,cross',[(None,{}),({'success':True,'code':0,'result':[]},{}),
    ({'success':True,'code':0,'result':{'data':['bad']}},{}),
    (provider_payloads()[0],{'code':0,'data':None})])
def test_malformed_provider_shape_is_a_validation_failure(data,cross):
    with pytest.raises(ValueError):validate_daily_market_snapshot(encode(data),encode(cross),'999991','2026-09-30')


def test_unknown_publication_is_not_backfilled_with_the_trading_day(app,tmp_path):
    from app.tools.market_sources import is_historical_market_observation
    path=tmp_path/'facts.json';init_manifest(app,path,[])
    data,cross=provider_payloads()
    candidate=register_market_candidate(app,path,encode(data),encode(cross),'999991','2026-09-30')
    doc=app.get_document(candidate['document_id'])
    assert doc['disclosure_date'] is None
    assert app.get_document(candidate['cross_document_id'])['disclosure_date'] is None
    fact=json.loads(path.read_text())['documents'][0]['facts'][0]
    evidence=app.get_evidence(candidate['evidence_id'])
    assert is_historical_market_observation(fact,evidence,doc,'2026-09-30')
    for key,value in [('metric','net_profit_parent'),('period_end','2026-10-01')]:
        altered={**fact,key:value}
        assert not is_historical_market_observation(altered,evidence,doc,'2026-09-30')
    assert not is_historical_market_observation(fact,evidence,{**doc,'source_url':'https://untrusted.example/data'},'2026-09-30')
