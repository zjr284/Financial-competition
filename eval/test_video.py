from decimal import Decimal
import json
from pathlib import Path
import unittest

from demo.build_video import timing, silent_timing


class VideoTests(unittest.TestCase):
    def test_narration_fits_exact_scene_budget_without_losing_audio(self):
        for lengths in ([Decimal('3.75'),Decimal('4.25')], [Decimal('11'),Decimal('10')]):
            speed, durations = timing({'duration':20}, lengths)
            self.assertLessEqual(speed, Decimal('1.35'))
            self.assertAlmostEqual(sum(durations), Decimal(20))
            self.assertTrue(all(d>=n/speed for d,n in zip(durations,lengths)))

    def test_voice_and_scene_budgets_refuse_impossible_inputs(self):
        for scene, lengths in [({'duration':0},[Decimal(1)]),
                               ({'duration':10},[Decimal(30)]),
                               ({'duration':20},[Decimal(0)])]:
            with self.assertRaises(ValueError):
                timing(scene,lengths)

    def test_storyboard_keeps_real_archives_and_development_boundaries(self):
        plan=json.loads((Path(__file__).parents[1]/'demo/video_storyboard.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(s['duration'] for s in plan['scenes']),285)
        self.assertIn('真实',plan['scenes'][3]['label'])
        self.assertIn('归档',plan['scenes'][3]['label'])
        self.assertIn('自编',plan['scenes'][5]['label'])
        self.assertIn('未完成',plan['scenes'][7]['label'])

    def test_silent_windows_keep_every_frame_and_all_27_recording_slots(self):
        plan=json.loads((Path(__file__).parents[1]/'demo/video_storyboard.json').read_text(encoding='utf-8'))
        windows=[silent_timing(scene) for scene in plan['scenes']]
        self.assertEqual(sum(map(len,windows)),27)
        self.assertEqual(sum(sum(w) for w in windows),Decimal(285))
        for scene,slots in zip(plan['scenes'],windows):
            self.assertEqual(sum(slots),Decimal(scene['duration']))
            self.assertTrue(all(s*25==int(s*25) and s>0 for s in slots))


if __name__=='__main__':
    unittest.main()
