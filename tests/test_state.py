import sqlite3
import tempfile
import unittest
from pathlib import Path

from realtime.state import StateStore


class StateTests(unittest.TestCase):
    def test_connection_context_closes_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db")
            with store._connect() as connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

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
