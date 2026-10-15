"""Private-source audit must not approve facts or mutate the handoff."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

from eval.coordinate_audit import audit, inside, numeric_coverage


class CoordinateAuditTests(unittest.TestCase):
    def test_numeric_geometry_is_exact_and_distinguishes_missing_thin_and_full_boxes(self):
        words = [[10, 10, 110, 20, '1,250'], [10, 30, 110, 40, '-8.2']]
        self.assertEqual(numeric_coverage(words, [10, 10, 110, 20], '250'), 0)
        self.assertEqual(numeric_coverage(words, [10, 10, 110, 20], '1250'), 1)
        self.assertAlmostEqual(numeric_coverage(words, [10, 10, 110, 11], '1250'), .1)
        self.assertEqual(numeric_coverage(words, [10, 30, 110, 40], '8.2'), 0)
        self.assertEqual(numeric_coverage(words, [10, 30, 110, 40], '-8.2'), 1)
        self.assertIsNone(numeric_coverage(words, [10, 10, 110, 20], None))

    def test_archive_paths_cannot_escape_or_use_windows_drive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['../outside.pdf', '/outside.pdf', 'C:/outside.pdf', 'documents\\outside.pdf']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    inside(root, name)
            self.assertEqual(inside(root, 'documents/source.pdf'), root/'documents/source.pdf')

    @unittest.skipUnless(importlib.util.find_spec('fitz') and importlib.util.find_spec('PIL'), 'Optional PDF audit dependencies unavailable')
    def test_pdf_bundle_audit_preserves_sources_and_annotations_and_rejects_tampering(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'bundle'; root.mkdir()
            (root/'annotations').mkdir(); (root/'store').mkdir()
            pdf = fitz.open(); page = pdf.new_page()
            page.insert_text((50, 100), '12,500,000.00')
            word = page.get_text('words')[0]
            bbox = [math.floor(word[i]/(page.rect.width if i % 2 == 0 else page.rect.height)*1000) if i < 2
                    else math.ceil(word[i]/(page.rect.width if i % 2 == 0 else page.rect.height)*1000) for i in range(4)]
            pdf.save(root/'source.pdf'); pdf.close()
            document = {'document_id':'doc_coordinate', 'filename':'source.pdf', 'relative_file':'source.pdf',
                        'media_type':'application/pdf','sha256':hashlib.sha256((root/'source.pdf').read_bytes()).hexdigest(),
                        'source_url':None,'disclosure_date':'2026-08-01','ingested_at':'2026-10-15T00:00:00+08:00',
                        'page_count':1,'parse_status':'parsed'}
            evidence = {'evidence_id':'ev_coordinate','document_id':'doc_coordinate','page':1,'bbox':bbox,
                        'text':'12,500,000.00','extraction_method':'pdf_text','confidence':1}
            fact = {'fact_id':'fact_coordinate','company_code':'999999','metric':'net_profit_parent','value':'12500000',
                    'unit':'CNY','period_start':'2026-01-01','period_end':'2026-06-30','period_type':'cumulative',
                    'statement_scope':'consolidated','restated':False,'evidence_ids':['ev_coordinate'],'review_status':'needs_review','reason':'Synthetic test pending review'}
            annotation = {'reviewer':'','decision':'','note':'','checked_at':''}
            files = {'documents.json':[document], 'evidence.json':[evidence], 'runs.json':[], 'bundle.json':{'split':'development'},
                     'store/reviewed_facts.json':{'version':1,'documents':[{'document_id':'doc_coordinate','sha256':document['sha256'],'facts':[fact]}]},
                     'annotations/coordinate_review_blank.json':[{'fact':fact,'evidence':evidence,'annotation':annotation}]}
            for name, value in files.items(): (root/name).write_text(json.dumps(value), encoding='utf-8')
            checks = {path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in root.rglob('*') if path.is_file()}
            (root/'checksums.json').write_text(json.dumps(checks), encoding='utf-8')
            before = copy.deepcopy(checks)
            result = audit(root, Path(directory)/'report')
            self.assertEqual(result['geometry_attention'], 0)
            self.assertFalse(result['human_gold'])
            self.assertFalse(result['offline_replay_verified'])
            self.assertEqual(result['fact_statuses'], {'needs_review':1})
            for name, digest in before.items(): self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(), digest)
            self.assertEqual(json.loads((root/'annotations/coordinate_review_blank.json').read_text())[-1]['annotation'], annotation)
            with self.assertRaises(ValueError): audit(root, root/'report')
            with self.assertRaises(ValueError): audit(root, Path(directory)/'report')
            (root/'source.pdf').write_bytes(b'changed')
            with self.assertRaises(ValueError): audit(root, Path(directory)/'tampered-report')
            self.assertFalse((Path(directory)/'tampered-report').exists())


if __name__ == '__main__':
    unittest.main()
