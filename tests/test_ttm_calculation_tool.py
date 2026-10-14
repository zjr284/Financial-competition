"""Production formula checks; all facts here are isolated arithmetic fixtures."""
import copy
import json
from decimal import Decimal
import unittest
from pathlib import Path

from app.tasks.valuation.samples.ttm_pe.demo import HERE
from app.tools import FinancialCalculator


class TTMToolTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((HERE/'inputs.json').read_text())
        self.facts=[copy.deepcopy(next(f for f in self.data['facts'] if f['fact_id']==f'fact_demo_999999_{role}'))
                    for role in ('annual','current','prior')]
        self.market=copy.deepcopy(next(f for f in self.data['facts'] if f['metric']=='market_cap'))
        self.market['company_code']='999999'

    def calculate(self, facts=None, parameters=None):
        facts=self.facts if facts is None else facts
        calculator=FinancialCalculator(facts)
        return calculator, calculator.calculate('ttm_from_ytd',[f['fact_id'] for f in facts],[],parameters or {})

    def test_normal_metadata_cents_and_stable_ids(self):
        self.facts[0]['value']='100.12';self.facts[1]['value']='60.34';self.facts[2]['value']='40.56'
        calc, result=self.calculate()
        self.assertEqual((result['value'],result['unit'],result['status']),('119.9','CNY','verified'))
        self.assertEqual(result['parameters']['period_start'],'2025-07-01')
        self.assertEqual(result['parameters']['period_end'],'2026-06-30')
        self.assertEqual(len(result['input_fact_ids']),3)
        self.assertEqual(result,self.calculate()[1])
        result['value']='0'
        self.assertEqual(calc.calculate('ttm_from_ytd',[f['fact_id'] for f in self.facts],[],{})['value'],'119.9')

    def test_first_quarter_and_year_end(self):
        for end in ('03-31','12-31'):
            with self.subTest(end=end):
                self.facts[1]['period_end']='2026-'+end
                self.facts[2]['period_end']='2025-'+end
                result=self.calculate()[1]
                self.assertEqual(result['status'],'verified')
                self.assertEqual(result['parameters']['period_end'],'2026-'+end)

    def test_february_leap_windows_and_fiscal_year(self):
        for periods, start, end in [
            ([('2023-01-01','2023-12-31'),('2024-01-01','2024-02-29'),('2023-01-01','2023-02-28')],'2023-03-01','2024-02-29'),
            ([('2023-03-01','2024-02-29'),('2024-03-01','2025-02-28'),('2023-03-01','2024-02-29')],'2024-03-01','2025-02-28')]:
            with self.subTest(end=end):
                for f,(a,b) in zip(self.facts,periods):f.update(period_start=a,period_end=b)
                result=self.calculate()[1]
                self.assertEqual(result['status'],'verified')
                self.assertEqual((result['parameters']['period_start'],result['parameters']['period_end']),(start,end))

    def test_wrong_company_metric_scope_restated_and_flow_rejected(self):
        for key, value in [('company_code','999991'),('metric','net_profit_parent_excl_nonrecurring'),
                           ('statement_scope','parent'),('restated',True),('period_type','single_quarter')]:
            with self.subTest(key=key):
                facts=copy.deepcopy(self.facts);facts[1][key]=value
                result=self.calculate(facts)[1]
                self.assertEqual((result['value'],result['status']),(None,'needs_review'))
                self.assertTrue(result['reason'])

    def test_bad_dates_and_metadata_are_rejected(self):
        for index,key,value in [(0,'period_end','2025-11-30'),(1,'period_start','2026-02-01'),
                                (2,'period_end','2025-03-31'),(1,'period_end','2026-06-29')]:
            with self.subTest(value=value):
                facts=copy.deepcopy(self.facts);facts[index][key]=value
                self.assertEqual(self.calculate(facts)[1]['status'],'needs_review')
        self.assertEqual(self.calculate(parameters={'period_end':'2025-12-31'})[1]['status'],'needs_review')

    def test_missing_and_unreviewed_inputs(self):
        self.assertEqual(self.calculate(self.facts[:2])[1]['status'],'needs_review')
        self.facts[1]['review_status']='needs_review';self.facts[1]['reason']='Test only'
        self.assertEqual(self.calculate()[1]['status'],'needs_review')
        calc=FinancialCalculator()
        with self.assertRaisesRegex(ValueError,'Unknown calculation inputs'):
            calc.calculate('ttm_from_ytd',['fact_unknown'],[],{})

    def test_cache_includes_source_status_evidence_and_period(self):
        calc,original=self.calculate()
        first=self.facts[0]['fact_id']
        calc.facts[first]['review_status']='needs_review';calc.facts[first]['reason']='Test correction'
        reviewed=calc.calculate('ttm_from_ytd',[f['fact_id'] for f in self.facts],[],{})
        self.assertNotEqual(original['calculation_id'],reviewed['calculation_id'])
        self.assertEqual(reviewed['status'],'needs_review')
        facts=copy.deepcopy(self.facts);facts[0]['evidence_ids']=['ev_demo_alternate']
        self.assertNotEqual(original['calculation_id'],self.calculate(facts)[1]['calculation_id'])

    def test_parent_pe_and_equity_use_calculation_inputs(self):
        calc,ttm=self.calculate();calc.register_facts([self.market])
        pe=calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})
        self.assertEqual(pe['value'],'8.3333')
        value=calc.calculate('relative_equity_value',[],[ttm['calculation_id']],{'multiple':'10','company_code':'999999','as_of_date':'2026-09-30'})
        self.assertEqual(value['value'],'1200')

    def test_wrong_market_context_negative_profit_and_non_ttm_calculation(self):
        calc,ttm=self.calculate();calc.register_facts([self.market])
        for params in ({'as_of_date':'2026-09-29'},{'as_of_date':'2026-09-30','company_code':'999992'},
                       {'as_of_date':'2026-09-30','statement_scope':'parent'}):
            self.assertEqual(calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],params)['status'],'needs_review')
        self.assertEqual(calc.calculate('relative_equity_value',[],[ttm['calculation_id']],{'multiple':'10','as_of_date':'2026-05-31'})['status'],'needs_review')
        calc.calculations[ttm['calculation_id']]['formula_code']='forecast_fcf'
        self.assertEqual(calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})['status'],'needs_review')
        self.facts[1]['value']='-100';calc,ttm=self.calculate();calc.register_facts([self.market])
        self.assertEqual(ttm['status'],'verified')
        self.assertEqual(calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})['status'],'needs_review')

    def test_adjusted_profit_supported_but_not_substituted_in_parent_pe(self):
        for f in self.facts:f['metric']='net_profit_parent_excl_nonrecurring'
        calc,ttm=self.calculate();calc.register_facts([self.market])
        self.assertEqual(ttm['parameters']['metric'],'net_profit_parent_excl_nonrecurring_ttm')
        self.assertEqual(calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})['status'],'needs_review')

    def test_downstream_cache_cannot_reuse_success_after_source_change(self):
        calc,ttm=self.calculate();calc.register_facts([self.market])
        args=('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})
        before=calc.calculate(*args)
        calc.facts[self.facts[0]['fact_id']]['review_status']='needs_review'
        after=calc.calculate(*args)
        self.assertNotEqual(before['calculation_id'],after['calculation_id'])
        self.assertEqual(after['status'],'needs_review')

    def test_tampered_profit_value_and_metadata_rejected(self):
        for key,value in [('value','99999'),('parameters',{'metric':'net_profit_parent_ttm'})]:
            calc,ttm=self.calculate();calc.register_facts([self.market])
            calc.calculations[ttm['calculation_id']][key]=value
            self.assertEqual(calc.calculate('pe',[self.market['fact_id']],[ttm['calculation_id']],{'as_of_date':'2026-09-30'})['status'],'needs_review')

    def test_public_draft_arithmetic_without_promoting_real_facts(self):
        root=Path(__file__).resolve().parents[1]
        records=json.loads((root/'app/tasks/valuation/PEER_SCREEN_DRAFT_CALCULATIONS.json').read_text())['calculations']
        for record in records:
            if record['formula']!='annual + current_h1 - prior_h1':continue
            with self.subTest(company=record['company_code'],metric=record['metric']):
                # Isolated arithmetic fixtures only; no real review manifest is read/written.
                facts=copy.deepcopy(self.facts)
                for f,value,period in zip(facts,record['input_values'],record['input_periods']):
                    f.update(metric=record['metric'],value=value,period_start=period.split('/')[0],period_end=period.split('/')[1])
                self.assertEqual(self.calculate(facts)[1]['value'],format(Decimal(record['value']).normalize(),'f'))
                self.assertEqual(record['review_status'],'needs_review')


if __name__=='__main__':unittest.main()
