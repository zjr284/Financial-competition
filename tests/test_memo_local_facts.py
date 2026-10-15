"""Local memo facts stay auditable without becoming cloud model inputs."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_finance_modules import context, fact, wrap
from app.tasks.investment_memo import InvestmentMemoTask


INPUT = {'company_code': '999999', 'as_of_date': '2026-09-30'}


def memo(items, **changes):
    ctx = context(items, ('doc_annual_demo',))
    def forbidden(*args):
        pytest.fail('A financial-only memo must not call the model')
    ctx = replace(ctx, model=SimpleNamespace(infer_json=forbidden), **changes)
    return wrap('investment_memo', ctx, InvestmentMemoTask().run(ctx, INPUT))


def revenue(identifier='current', value='9007199254740993.17', end='2026-06-30', **changes):
    return {**fact('fact_'+identifier, '999999', 'revenue', value, end[:4]+'-01-01', end), **changes}


def test_financial_only_memo_preserves_decimal_period_evidence_and_review():
    result = memo([revenue()])
    statement, = result['data']['evidence']
    assert statement['kind'] == 'fact'
    assert '9007199254740993.17' in statement['text'] and '2026-01-01 至 2026-06-30' in statement['text']
    assert statement['evidence_ids'] == result['evidence_ids'] == ['ev_current']
    assert result['data']['thesis'] == [] and result['calculations'] == []
    assert result['status'] == 'needs_review'


@pytest.mark.parametrize('change', [
    {'company_code': '888888'}, {'statement_scope': 'parent'}, {'period_type': 'single_quarter'},
    {'period_end': '2026-12-31'}, {'review_status': 'needs_review', 'reason': 'Unconfirmed'},
    {'review_status': 'rejected', 'reason': 'Rejected'}, {'metric': 'net_profit_parent_ttm'},
])
def test_ineligible_financial_values_never_become_memo_facts(change):
    assert memo([revenue(**change)])['data']['evidence'] == []


@pytest.mark.parametrize('day', [None, '2026-10-01'])
def test_financial_appendix_respects_actual_publication_date(day):
    docs = SimpleNamespace(get_document=lambda _: {'disclosure_date': day})
    assert memo([revenue()], documents=docs)['data']['evidence'] == []


@pytest.mark.parametrize('variant', ['new_unverified', 'restatement_pending', 'conflict'])
def test_unresolved_latest_fact_does_not_fall_back_to_a_convenient_verified_value(variant):
    old = revenue('old', '100', '2025-12-31')
    current = revenue('current', '200')
    if variant == 'new_unverified':
        current.update(review_status='needs_review', reason='Unconfirmed')
        items = [old, current]
    else:
        other = revenue('other', '201', restated=variant == 'restatement_pending')
        if variant == 'restatement_pending':
            other.update(review_status='needs_review', reason='Restatement pending')
        items = [old, current, other]
    result = memo(items)
    assert result['data']['evidence'] == []
    assert any('requires review' in r['reason'] or 'conflicting revenue' in r['reason'] for r in result['review_items'])


def test_verified_restatement_supersedes_old_value_and_zero_is_kept():
    result = memo([revenue('old', '100'), revenue('restated', '0', restated=True)])
    statement, = result['data']['evidence']
    assert statement['evidence_ids'] == ['ev_restated']
    assert '营业收入 0 元人民币' in statement['text'] and '重述口径：是' in statement['text']
