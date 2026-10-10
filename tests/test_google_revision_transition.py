import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from scripts import google_revision_gate as gate
from scripts.transition_google_revision import boundary, can_stop, hour_verified, audit_verified, Transition


class TransitionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026,10,9,11,2,8,tzinfo=timezone.utc)
        self.right = self.start + timedelta(hours=1)

    def test_before_first_complete_hour_only_targets_the_future_boundary(self):
        index, right = boundary(self.start, self.start + timedelta(minutes=30))
        self.assertEqual((index,right),(0,self.right))
        self.assertFalse(can_stop(self.start + timedelta(minutes=30),right))

    def test_requires_commit_grace_and_shutdown_headroom(self):
        self.assertFalse(can_stop(self.right + timedelta(seconds=59),self.right))
        self.assertTrue(can_stop(self.right + timedelta(seconds=60),self.right))
        self.assertTrue(can_stop(self.right + timedelta(seconds=100),self.right))
        self.assertFalse(can_stop(self.right + timedelta(seconds=101),self.right))

    def test_expired_first_hour_does_not_allow_mid_second_hour_stop(self):
        index,right = boundary(self.start,self.right + timedelta(minutes=30))
        self.assertEqual(index,1)
        self.assertEqual(right,self.right+timedelta(hours=1))
        self.assertFalse(can_stop(self.right + timedelta(minutes=30),right))

    def test_long_running_series_selects_latest_completed_boundary(self):
        now = self.start + timedelta(hours=5,seconds=70)
        self.assertEqual(boundary(self.start,now),(4,self.start+timedelta(hours=5)))
        now = self.start + timedelta(hours=5,seconds=96)
        self.assertEqual(boundary(self.start,now),(5,self.start+timedelta(hours=6)))

    def test_wall_clock_and_valid_label_cannot_replace_post_boundary_verification(self):
        report = dict(run='run',window_index=0,started_at=self.start.isoformat(),
            measured_until=self.right.isoformat(),worker_verified_through=self.right.isoformat(),
            full_hour_observed=True,valid_for_comparison=True,elapsed_seconds=3600,interruptions=[],
            exclusive_provider=True,result_url_audit_complete=True,body_admission_audit_complete=True)
        manifest=dict(run='run',last_verified_running_at=self.right.isoformat())
        self.assertTrue(hour_verified(gate,report,manifest,0,self.right))
        manifest['last_verified_running_at']=(self.right-timedelta(seconds=1)).isoformat()
        self.assertFalse(hour_verified(gate,report,manifest,0,self.right))
        manifest['last_verified_running_at']=self.right.isoformat()
        report['interruptions']=[dict(at=self.right.isoformat())]
        self.assertFalse(hour_verified(gate,report,manifest,0,self.right))

    def test_all_nine_sql_counts_and_same_frozen_bytes_are_required(self):
        report={k:1 for k in gate.COUNT_KEYS}
        audit=dict(matches_frozen_report=True,database_read_only='on',frozen_report_sha256='digest',
                   checks={k:True for k in gate.COUNT_KEYS},independent_counts=report.copy())
        self.assertTrue(audit_verified(gate,audit,report,'digest'))
        for key,value in [('frozen_report_sha256','different'),('database_read_only','off'),
                          ('checks',{}),('independent_counts',{})]:
            bad=copy.deepcopy(audit);bad[key]=value
            self.assertFalse(audit_verified(gate,bad,report,'digest'))
        bad=copy.deepcopy(audit);bad['independent_counts']['google_searches']=2
        self.assertFalse(audit_verified(gate,bad,report,'digest'))

    def test_an_unready_boundary_performs_no_stop_backup_or_migration(self):
        t=object.__new__(Transition)
        with tempfile.TemporaryDirectory() as tmp:
            t.state=Path(tmp)
            with patch.object(t,'check'),patch.object(t,'wait_boundary',side_effect=RuntimeError('not ready')),\
                    patch.object(t,'command') as commands:
                with self.assertRaises(RuntimeError):t.apply(False)
                commands.assert_not_called()
                self.assertFalse((t.state/'mutation-started.json').exists())

    def test_previous_mutation_is_never_automatically_retried(self):
        t=object.__new__(Transition)
        with tempfile.TemporaryDirectory() as tmp:
            t.state=Path(tmp);(t.state/'mutation-started.json').write_text('{}')
            with patch.object(t,'check'),patch.object(t,'wait_boundary') as wait,patch.object(t,'command') as commands:
                with self.assertRaisesRegex(RuntimeError,'prior mutation'):t.apply(True)
                wait.assert_not_called();commands.assert_not_called()

    def test_failed_backup_never_runs_migration_or_new_series(self):
        t=object.__new__(Transition)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);t.root=root;t.state=root/'transition';t.state.mkdir()
            hour=root/'hour.json';hour.write_text('{}')
            now=datetime.now(timezone.utc);right=now-timedelta(seconds=70)
            commands=[]
            with patch.object(t,'check'),patch.object(t,'wait_boundary',return_value=(right,hour)),\
                    patch.object(t,'gate_snapshot',return_value={'ready':True}),\
                    patch.object(t,'inventory',return_value=[]),patch.object(t,'event'),\
                    patch.object(t,'command',side_effect=lambda args,**kw:commands.append(args)),\
                    patch('scripts.transition_google_revision.shutil.disk_usage',return_value=SimpleNamespace(free=20*1024**3)),\
                    patch('scripts.transition_google_revision.subprocess.run',side_effect=RuntimeError('backup failed')):
                with self.assertRaisesRegex(RuntimeError,'backup failed'):t.apply(False)
            self.assertTrue((t.state/'mutation-started.json').exists())
            self.assertFalse(any('run' in c and 'web' in c for c in commands))
            self.assertFalse(any('enable' in c for c in commands))
