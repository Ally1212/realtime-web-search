import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('autotune', Path(__file__).resolve().parents[1] / 'scripts/autotune_openserp_capacity.py')
autotune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autotune)


class AutotuneTests(unittest.TestCase):
    def test_high_load_skips_expansion_and_restores_higher_yield_baseline(self):
        state = dict(current_slots=4, candidates=[3, 6], mode='experiment')
        self.assertEqual(autotune.choose(state, 1700, False), 3)
        state['current_slots'] = 3
        self.assertEqual(autotune.choose(state, 1300, False), 4)
        self.assertEqual(state['mode'], 'hold')
        self.assertEqual(state['skipped'][0]['slots'], 6)

    def test_measured_target_holds_without_further_changes(self):
        state = dict(current_slots=4, candidates=[3, 6], mode='experiment')
        self.assertIsNone(autotune.choose(state, 2050, True))
        self.assertEqual(state['mode'], 'hold')

    def test_lower_yield_expansion_rolls_back(self):
        state = dict(current_slots=4, candidates=[3, 6], mode='experiment')
        self.assertEqual(autotune.choose(state, 1500, True), 3)
        state['current_slots'] = 3
        self.assertEqual(autotune.choose(state, 1600, True), 6)
        state['current_slots'] = 6
        self.assertEqual(autotune.choose(state, 1400, True), 3)

    def test_expired_run_never_recreates_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'manifest.json').write_text(json.dumps({'deadline': '2000-01-01T00:00:00+00:00'}))
            with patch.object(autotune.subprocess, 'run') as run:
                autotune.tick(directory, directory)
                autotune.apply_slots(directory, directory, {'pending': {'slots': 6}}, datetime(2000, 1, 1, tzinfo=timezone.utc))
                run.assert_not_called()

    def test_incomplete_hour_never_changes_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'manifest.json').write_text(json.dumps({'deadline': '2099-01-01T00:00:00+00:00'}))
            (directory / 'phases.jsonl').write_text(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'phase': 'baseline'}) + '\n')
            with patch.object(autotune, 'worker_slots', return_value=4), patch.object(autotune.subprocess, 'run') as run:
                autotune.tick(directory, directory)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
