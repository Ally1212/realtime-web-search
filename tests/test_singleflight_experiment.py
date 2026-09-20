import tempfile
import unittest
from pathlib import Path

from realtime.experiment_store import ExperimentStore
from realtime.singleflight_experiment import run_case


class SingleflightExperimentTests(unittest.TestCase):
    def test_waiting_reuses_cache_instead_of_failing_inflight_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ExperimentStore(Path(directory) / 'state', create=True)
            baseline = run_case(store, 'wait_0', 0, 20)
            waiting = run_case(store, 'wait_2', 2, 20)
            self.assertEqual(baseline['network_requests'], 1)
            self.assertEqual(baseline['errors'], 19)
            self.assertEqual(waiting['network_requests'], 1)
            self.assertEqual(waiting['cache_reuses'], 19)
            self.assertEqual(waiting['errors'], 0)
            rows = store.inflight_experiments()
            self.assertEqual([row['name'] for row in rows], ['wait_0', 'wait_2'])
            self.assertEqual(rows[1]['cache_reuses'], 19)


if __name__ == '__main__':
    unittest.main()
