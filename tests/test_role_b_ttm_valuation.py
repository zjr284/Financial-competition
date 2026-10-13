"""B integration checks: arithmetic remains in the injected calculation tool."""
import copy
import json
import unittest
from pathlib import Path

import jsonschema

from app.tasks.valuation.samples.ttm_pe.demo import HERE, UnsupportedFormulaCalculator, run_demo
from app.tools import FinancialCalculator

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/'contracts/contracts.schema.json').read_text())


class TTMValuationTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((HERE/'inputs.json').read_text())

    def validate(self, result):
        jsonschema.Draft202012Validator({'$defs':SCHEMA['$defs'],'$ref':'#/$defs/TaskResult'},
                                        format_checker=jsonschema.FormatChecker()).validate(result)

    def test_synthetic_pe_has_complete_calculation_chain(self):
        result=run_demo(self.data);self.validate(result)
        self.assertEqual(result['data']['valuation_range'],dict(low='1200',base='1500',high='2400',unit='CNY',reason=None))
        records={c['calculation_id']:c for c in result['calculations']}
        originals={f['fact_id'] for f in self.data['facts']}
        ttm=[c for c in records.values() if c['formula_code']=='ttm_from_ytd']
        self.assertEqual(len(ttm),4)
        for c in records.values():
            self.assertTrue(set(c['input_fact_ids']) <= originals)
            self.assertTrue(set(c['input_calculation_ids']) <= records.keys())
        for c in ttm:
            self.assertEqual(len(c['input_fact_ids']),3)
            self.assertEqual(c['parameters']['period_start'],'2025-07-01')
            self.assertEqual(c['parameters']['period_end'],'2026-06-30')
        for c in records.values():
            if c['formula_code'] in {'pe','relative_equity_value'}:
                self.assertEqual(len(c['input_calculation_ids']),1)
        self.assertEqual(result,run_demo(copy.deepcopy(self.data)))

    def test_unsupported_formula_adapter_fails_closed(self):
        result=run_demo(self.data,UnsupportedFormulaCalculator);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertTrue(all(c['status']=='needs_review' for c in result['calculations']))
        self.assertIn('Unsupported formula_code', ' '.join(result['warnings']))

    def test_production_outputs_match_saved_success_and_pending_examples(self):
        self.assertEqual(run_demo(self.data),json.loads((HERE/'success.json').read_text()))
        source=next(f for f in self.data['facts'] if f['fact_id']=='fact_demo_999999_current')
        source['review_status']='needs_review';source['reason']='Synthetic pending review example'
        self.assertEqual(run_demo(self.data),json.loads((HERE/'needs_review.json').read_text()))

    def test_missing_target_source_produces_review_not_value(self):
        self.data['facts']=[f for f in self.data['facts'] if f['fact_id']!='fact_demo_999999_prior']
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertIn('missing annual/current YTD/prior YTD', ' '.join(result['warnings']))

    def test_unverified_source_is_never_promoted(self):
        f=next(f for f in self.data['facts'] if f['fact_id']=='fact_demo_999999_current')
        f['review_status']='needs_review';f['reason']='Human review pending'
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertEqual(f['review_status'],'needs_review')
        self.assertFalse(any(c['formula_code']=='ttm_from_ytd' and
                             c['parameters']['company_code']=='999999' for c in result['calculations']))

    def test_restated_mismatch_requires_reconciliation(self):
        self.data['facts'][0]['restated']=True
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertIn('restatement basis', ' '.join(result['warnings']))

    def test_parent_statement_does_not_mix_with_consolidated(self):
        self.data['facts'][0]['statement_scope']='parent'
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])

    def test_after_cutoff_source_cannot_supply_ttm(self):
        self.data['documents'][0]['disclosure_date']='2026-10-01'
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertIn('disclosed after', ' '.join(result['warnings']))

    def test_nonpositive_peer_profit_retains_calculation_and_requires_review(self):
        next(f for f in self.data['facts'] if f['fact_id']=='fact_demo_999991_current')['value']='-100'
        result=run_demo(self.data);self.validate(result)
        self.assertIsNone(result['data']['valuation_range']['base'])
        self.assertTrue(any(c['formula_code']=='pe' and c['status']=='needs_review'
                            for c in result['calculations']))

    def test_source_files_are_fixed_and_contract_valid(self):
        import hashlib
        for kind,key in [('Fact','facts'),('Evidence','evidence'),('Document','documents')]:
            validator=jsonschema.Draft202012Validator({'$defs':SCHEMA['$defs'],'$ref':f'#/$defs/{kind}'})
            for entity in self.data[key]:validator.validate(entity)
        for doc in self.data['documents']:
            self.assertEqual(hashlib.sha256((HERE/doc['filename']).read_bytes()).hexdigest(),doc['sha256'])


if __name__=='__main__':unittest.main()
