"""独立副本核查不得改源事实、污染真人模板或伪称留出成绩。"""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.config import Settings
from app.container import build_app
from demo.live_fixtures import seed
from demo.verify_draft import verify
from app.orchestrator.service import Orchestrator
from app.tasks.research_verification.support import digest


class VerifyDraftTests(unittest.TestCase):
    def test_copy_runs_both_tasks_preserving_sources_and_blank_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            app = build_app(Settings(data_dir=source))
            try:
                seeded = seed(app)
                raw, _ = app.get_document_content(seeded['documents']['draft'])
                # 模拟来源已缓存、草稿尚未解析的首次真实运行。
                with app.store.write() as conn:
                    conn.execute('UPDATE documents SET parse_manifest=NULL WHERE document_id=?',
                                 (seeded['documents']['draft'],))
            finally:
                app.close()
            draft = root / 'self-authored.pdf'
            draft.write_bytes(raw)
            before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*')
                      if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
            result = verify(source, draft, '999999', [seeded['documents']['source']],
                            root / 'review', dataset='synthetic-development', synthetic=True)
            self.assertEqual(result['model_calls'], 0)
            self.assertIsNone(result['holdout_metrics'])
            self.assertEqual(result['split'], 'development')
            self.assertFalse(result['human_gold_complete'])
            self.assertEqual(len(result['runs']), 2)
            self.assertTrue(all(run['replay_verified'] for run in result['runs']))
            for entry in result['runs']:
                saved_run = json.loads((root / 'review' / (entry['task_type']+'-run.json')).read_text())
                saved_result = json.loads((root / 'review' / (entry['task_type']+'-result.json')).read_text())
                self.assertEqual(entry['result_hash'], saved_run['result_hash'])
                self.assertEqual(entry['content_hash'], digest(saved_result))
            checks = json.loads((root / 'review/research_verification-result.json').read_text(encoding='utf-8'))
            self.assertEqual(checks['data']['summary'], {'correct': 2, 'incorrect': 2, 'needs_review': 5})
            for person in ('a', 'b'):
                blank = json.loads((root / f'review/annotation-{person}-blank.json').read_text(encoding='utf-8'))
                self.assertTrue(blank['synthetic'])
                self.assertEqual(len(blank['records']), 9)
                for row in blank['records']:
                    self.assertEqual(set(row['annotation'].values()), {''})
                    self.assertNotIn('decision', row)
                    self.assertNotIn('suggested_edit', row)
            for relative, content in before.items():
                self.assertEqual((source / relative).read_bytes(), content)
            with closing(sqlite3.connect(source / 'metadata.sqlite3')) as database:
                self.assertEqual(database.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError, '全新输出'):
                verify(source, draft, '999999', [seeded['documents']['source']],
                       root / 'review', dataset='retry', synthetic=True)

    def test_missing_sources_and_output_inside_source_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for ids, output in [([], root / 'new'), (['doc_a', 'doc_a'], root / 'new'),
                                (['doc_a'], root / 'source/nested')]:
                with self.assertRaises(ValueError):
                    verify(root / 'source', root / 'draft.pdf', '300207', ids,
                           output, dataset='development')
                self.assertFalse(output.exists())

    def test_invalid_event_chain_cannot_be_reported_as_verified_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = build_app(Settings(data_dir=root/'source'))
            try:
                seeded = seed(app)
                raw, _ = app.get_document_content(seeded['documents']['draft'])
            finally:
                app.close()
            draft = root/'draft.pdf'
            draft.write_bytes(raw)
            original = Orchestrator.replay
            def broken_chain(instance, run_id):
                proof = original(instance, run_id)
                proof['chain']['ok'] = False
                return proof
            with patch.object(Orchestrator, 'replay', broken_chain):
                with self.assertRaisesRegex(ValueError, '重放不一致'):
                    verify(root/'source', draft, '999999', [seeded['documents']['source']],
                           root/'review', dataset='synthetic-development', synthetic=True)
            self.assertFalse((root/'review'/'summary.json').exists())


if __name__ == '__main__':
    unittest.main()
