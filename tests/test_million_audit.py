import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from realtime.experiment_store import ExperimentStore
from realtime.experiment_audit import write_audit_snapshot
from scripts.audit_million import parse_container_log


class MillionAuditTests(unittest.TestCase):
    def test_container_log_maximum_is_observed_not_strict_audit(self):
        output = '\n'.join([
            '2026-09-15T07:00:00.000000000Z '
            '{"experiment":"google-experiment-run","state":"running",'
            '"new":10,"requests":4,"whale_accepted":8}',
            'unstructured diagnostic',
            '2026-09-15T08:00:00.000000000Z '
            '{"experiment":"google-experiment-run","state":"paused",'
            '"new":25,"requests":9,"whale_accepted":20}',
        ])

        rows = parse_container_log('collector-test', output)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['new'], 25)
        self.assertEqual(rows[0]['whale_accepted'], 20)
        self.assertEqual(rows[0]['state'], 'paused')
        self.assertEqual(rows[0]['observed_seconds'], 3600)
        self.assertEqual(rows[0]['containers'], ['collector-test'])

    def test_cli_accepts_relative_experiment_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ExperimentStore(root / 'run', create=True)
            try:
                now = time.time()
                for key, value in {
                    'id': 'relative-run', 'started_at': now - 10,
                    'deadline': now + 10, 'state': 'paused',
                }.items():
                    store.set(key, value)
                script = Path(__file__).resolve().parents[1] / 'scripts/audit_million.py'
                result = json.loads(subprocess.check_output(
                    [sys.executable, str(script), '.', '--all-experiments'],
                    cwd=root,
                    text=True,
                ))
                self.assertEqual(result['verified_maximum']['experiment'], 'relative-run')
            finally:
                store.db.close()

    def test_archived_strict_snapshot_survives_ledger_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'experiments' / 'archived-run'
            store = ExperimentStore(run, create=True)
            now = time.time()
            try:
                for key, value in {
                    'id': 'archived-run', 'started_at': now - 10,
                    'deadline': now + 10, 'state': 'paused',
                }.items():
                    store.set(key, value)
                snapshot = write_audit_snapshot(store.path)
                self.assertTrue(snapshot.is_file())
            finally:
                store.db.close()
            store.path.unlink()

            script = Path(__file__).resolve().parents[1] / 'scripts/audit_million.py'
            result = json.loads(subprocess.check_output(
                [sys.executable, str(script), str(root / 'experiments'), '--all-experiments'],
                text=True,
            ))

            self.assertEqual(result['verified_maximum']['experiment'], 'archived-run')
            self.assertEqual(result['verified_maximum']['evidence'], 'archived_strict_snapshot')

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

    def test_all_experiments_reports_verified_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stores = []
            now = time.time()
            try:
                for name, accepted in [('small', 1), ('largest', 3)]:
                    store = ExperimentStore(root/name, create=True)
                    stores.append(store)
                    for key, value in {
                        'id': name, 'started_at': now-100, 'deadline': now+100,
                        'state': 'paused',
                    }.items():
                        store.set(key, value)
                    with store.db:
                        for identifier in range(1, accepted+1):
                            url = f'https://{name}.example/{identifier}'
                            store.db.execute(
                                "INSERT INTO documents(id,url,canonical,hash,finished,classification,quality) "
                                "VALUES(?,?,?,?,?,'new','[]')",
                                (identifier, url, url, f'{name}-{identifier}', now),
                            )
                            store.db.execute(
                                "INSERT INTO outbox(document_id,status,finished) VALUES(?,'accepted',?)",
                                (identifier, now),
                            )
                script = Path(__file__).resolve().parents[1]/'scripts/audit_million.py'
                result = json.loads(subprocess.check_output(
                    [sys.executable, str(script), '--all-experiments', str(root)], text=True,
                ))
                self.assertTrue(result['maximum_proven_across_ledgers'])
                self.assertEqual(result['verified_maximum']['experiment'], 'largest')
                self.assertEqual(result['verified_maximum']['strict_new_accepted_before_deadline'], 3)
                self.assertEqual(
                    result['verified_valid_content_maximum']['experiment'], 'largest'
                )
                self.assertEqual(
                    result['verified_valid_content_maximum']['valid_new_content_before_deadline'],
                    3,
                )
                self.assertEqual([row['directory'] for row in result['experiments']], ['largest', 'small'])
            finally:
                for store in stores:
                    store.db.close()
