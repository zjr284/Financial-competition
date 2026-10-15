"""Source grounding and financial normalization of untrusted model candidates."""
import pytest

from app.parse.index import DocumentIndex
from app.tasks.structured_extraction.model_extract import _field, extract_candidates, source_windows


TEXT = '''证券代码：999999
关于部分股份质押的公告
公告日期：2026年9月22日
股东名称：张某
本次质押股数：1,250万股
占公司总股本比例：0.12%
质押起始日：2026年9月21日
质押到期日：解除质押日
质权人：合成证券有限公司'''


class CandidateModel:
    def __init__(self, overrides=None, *, company='999999', document='doc_model_0001'):
        self.overrides = overrides or {}
        self.company, self.document = company, document

    def infer_json(self, task, evidence_ids, schema_name, context):
        self.context = context
        self.refs = evidence_ids
        raw = {'company_code': self.company, 'event_date': '2026年9月22日', 'shareholder': '张某',
               'pledged_shares': '1,250万股', 'pledged_ratio': '0.12%',
               'pledge_start_date': '2026年9月21日', 'pledge_end_date': '解除质押日',
               'pledgee': '合成证券有限公司'}
        fields = [{'field_code': code, 'raw_value': value, 'value': '999999999', 'unit': 'CNY',
                   'status': 'verified', 'reason': None, 'evidence_ids': list(evidence_ids)}
                  for code, value in raw.items()]
        for field in fields:
            field.update(self.overrides.get(field['field_code'], {}))
        return {'records': [{'event_id': 'untrusted-model-event', 'document_id': self.document,
                             'company_code': self.company, 'event_date': None,
                             'announcement_type': 'pledge', 'fields': fields}]}


def extract(model, text=TEXT):
    index = DocumentIndex.from_plain_text('doc_local_private', text)
    return extract_candidates(model, 'doc_local_private', index, ['pledge'])


def fields(result):
    return {f['field_code']: f for f in result['records'][0]['fields']}


def test_model_numbers_status_and_ids_are_replaced_by_grounded_local_values():
    model = CandidateModel()
    result = extract(model)
    values = fields(result)
    assert values['pledged_shares']['value'] == '12500000'
    assert values['pledged_ratio']['value'] == '0.12'
    assert values['pledge_start_date']['value'] == '2026-09-21'
    assert values['pledge_end_date']['raw_value'] == '解除质押日'
    assert values['pledge_end_date']['value'] is None
    assert all(f['status'] == 'needs_review' for code, f in values.items() if code != 'document_id')
    assert result['records'][0]['document_id'] == 'doc_local_private'
    assert 'doc_local_private' not in str(model.context)


@pytest.mark.parametrize('change', [
    {'raw_value': '1,251万股'}, {'evidence_ids': []}, {'raw_value': None},
])
def test_unquoted_or_missing_numeric_candidate_is_null_with_reason(change):
    field = fields(extract(CandidateModel({'pledged_shares': change})))['pledged_shares']
    assert field['value'] is None and field['reason'] and field['status'] == 'needs_review'


def test_unknown_citation_or_document_is_rejected():
    with pytest.raises(Exception, match='未知来源'):
        extract(CandidateModel({'pledged_shares': {'evidence_ids': ['ev_invented']}}))
    with pytest.raises(Exception, match='文档或公告类型'):
        extract(CandidateModel(document='doc_somewhere_else'))


def test_ungrounded_company_does_not_create_a_placeholder_company_record():
    assert extract(CandidateModel(company='888888'))['records'] == []


def test_invalid_calendar_date_remains_null():
    model = CandidateModel({'pledge_start_date': {'raw_value': '2026年2月31日'}})
    field = fields(extract(model, TEXT.replace('2026年9月21日', '2026年2月31日')))['pledge_start_date']
    assert field['value'] is None and '公历日期' in field['reason']


def test_missing_share_unit_is_not_guessed_from_a_model_unit():
    field = fields(extract(CandidateModel({'pledged_shares': {'raw_value': '1,250'}})))['pledged_shares']
    assert field['value'] is None and '单位' in field['reason']


def test_ambiguous_percent_and_foreign_currency_are_not_silently_normalized():
    evidence = {'ev_one': {'text': '比例0.12%或1.68%；币种日元；金额100元'}}
    ratio = _field('pledged_ratio', {'raw_value': '0.12%或1.68%', 'evidence_ids': ['ev_one']}, 'percent', evidence)
    amount = _field('contract_amount', {'raw_value': '100元', 'evidence_ids': ['ev_one']}, 'amount', evidence)
    assert ratio['value'] is None and amount['value'] is None


def test_metadata_omission_is_recovered_only_from_the_security_code_header():
    class MissingMetadata(CandidateModel):
        def infer_json(self, *args):
            output = super().infer_json(*args)
            for record in output['records']:
                record['fields'] = [f for f in record['fields'] if f['field_code'] not in {'company_code', 'event_date'}]
            return output
    result = extract(MissingMetadata())
    assert result['records'][0]['company_code'] == '999999'
    assert result['records'][0]['event_date'] is None
    assert extract(MissingMetadata(), TEXT.replace('证券代码：', '无关数字：'))['records'] == []


def test_new_pledge_windows_exclude_the_cumulative_position_section():
    text = TEXT+'\n2、股东股份累计被质押的情况\n累计持股12345万股'
    windows = source_windows(DocumentIndex.from_plain_text('doc_synthetic', text))
    assert all('12345' not in item['text'] for item in windows)
