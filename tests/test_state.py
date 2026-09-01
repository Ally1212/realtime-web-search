import tempfile
import unittest
from pathlib import Path

from realtime.state import StateStore


class StateTests(unittest.TestCase):
    def test_job_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db")
            job_id = store.create_job("test", 2, 4)
            store.update_job(job_id, status="running", discovered=10)
            store.add_event(job_id, "https://example.com", "Example", "success", 200)
            self.assertEqual(store.job(job_id)["discovered"], 10)
            self.assertEqual(store.stats()["events"][0]["http_status"], 200)


if __name__ == "__main__":
    unittest.main()
