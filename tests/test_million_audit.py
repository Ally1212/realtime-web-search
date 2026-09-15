import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from realtime.experiment_store import ExperimentStore


class MillionAuditTests(unittest.TestCase):
    def test_late_and_duplicate_receipts_cannot_satisfy_daily_new_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            deadline = time.time()-20
            for key, value in {'id': 'audit-test', 'started_at': deadline-86400, 'deadline': deadline,
                               'finished_at': deadline+1, 'state': 'complete', 'stop_reason': 'deadline'}.items():
                store.set(key, value)
            with store.db:
                for i, (status, receipt_at) in enumerate([('accepted', deadline-5), ('accepted', deadline+5),
                                                         ('duplicate', deadline-5)], 1):
                    url = f'https://example.com/{i}'
                    store.db.execute("INSERT INTO documents(id,url,canonical,hash,finished,classification,quality) "
                                     "VALUES(?,?,?,?,?,'new','[]')", (i, url, url, str(i), deadline-10))
                    store.db.execute('INSERT INTO outbox(document_id,status,finished) VALUES(?,?,?)', (i, status, receipt_at))
            script = Path(__file__).resolve().parents[1]/'scripts/audit_million.py'
            def audit():
                return json.loads(subprocess.check_output([sys.executable, str(script), tmp], text=True))
            try:
                result = audit()
                self.assertEqual(result['strict_new_accepted_before_deadline'], 1)
                self.assertTrue(result['completed_full_window'])
                self.assertFalse(result['million_goal_proven'])
                self.assertFalse(any(result['violations'].values()))
                store.baseline(hashes=['1'])
                store.set('state', 'paused')
                result = audit()
                self.assertEqual(result['violations']['new_matches_baseline'], 1)
                self.assertFalse(result['completed_full_window'])
            finally:
                store.db.close()
