import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.google_revision_gate import PREDECESSOR, COUNT_KEYS, full_hour, verify


class GoogleRevisionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.state = self.root/'state'/PREDECESSOR;self.state.mkdir(parents=True)
        self.start = datetime(2026,10,9,tzinfo=timezone.utc)
        self.independent = {};plans=[];completed=[]
        for index in range(9):
            left=self.start+timedelta(hours=index,minutes=index)
            run = PREDECESSOR + '-fixture-' + str(index)
            plans.append(dict(key='fixture-'+str(index),provider='openserp',sources=['google_web']))
            r=dict(run=run,index=index,provider='openserp',sources=['google_web'],
                   started_at=left.isoformat(),measured_until=(left+timedelta(hours=1)).isoformat(),
                   worker_verified_through=(left+timedelta(hours=1)).isoformat(),
                   elapsed_seconds=3600,full_hour_observed=True,valid_for_comparison=True,interruptions=[],
                   exclusive_provider=True,result_url_audit_complete=True,body_admission_audit_complete=True,
                   **{k:0 for k in COUNT_KEYS})
            p=self.root/'state'/run/'audited-final.json';p.parent.mkdir();p.write_text(json.dumps(r))
            self.independent[run]=dict(matches_frozen_report=True,database_read_only='on',
                frozen_report_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),checks={k:True for k in COUNT_KEYS},
                independent_counts={k:0 for k in COUNT_KEYS})
            completed.append(r)
        (self.state/'series.json').write_text(json.dumps(dict(plans=plans)))
        (self.state/'comparison.json').write_text(json.dumps(dict(completed=completed,pending=0)))
        self.worker=dict(Id='original-id',Running=False,Paused=False,Restarting=False,RestartCount=0,
                         StartedAt=self.start.isoformat(),FinishedAt=(self.start+timedelta(hours=1,seconds=10)).isoformat())
        self.supervisor=dict(ActiveState='inactive',MainPID='0')

    def gate(self):return verify(self.root,self.independent,self.worker,self.supervisor,
                                 now=self.start+timedelta(hours=12))

    def test_live_worker_or_supervisor_refuses_transition_without_mutating_evidence(self):
        self.assertTrue(self.gate()['ready'])
        self.worker['Running']=True
        self.assertFalse(self.gate()['ready'])
        self.worker['Running']=False;self.supervisor['ActiveState']='active';self.supervisor['MainPID']='123'
        self.assertFalse(self.gate()['ready'])

    def test_changed_report_or_missing_sql_proof_cannot_pass(self):
        run=next(iter(self.independent));path=self.root/'state'/run/'audited-final.json'
        report=json.loads(path.read_text());report['new_qualified_bodies']=1;path.write_text(json.dumps(report))
        self.assertFalse(self.gate()['checks']['all_nine_independent_sql'])
        del self.independent[run]
        self.assertFalse(self.gate()['ready'])

    def test_partial_or_missing_hour_cannot_pass_even_with_a_valid_label(self):
        run=next(iter(self.independent));path=self.root/'state'/run/'audited-final.json'
        report=json.loads(path.read_text());report['elapsed_seconds']=3599;path.write_text(json.dumps(report))
        self.assertFalse(self.gate()['checks']['all_nine_exact_hours'])
        path.unlink()
        self.assertFalse(self.gate()['ready'])

    def test_continuous_requires_an_archived_full_hour_and_normal_boundary_stop(self):
        start=self.start+timedelta(hours=10)
        self.worker.update(StartedAt=start.isoformat(),FinishedAt=(start+timedelta(hours=1,seconds=10)).isoformat())
        c=self.root/'state'/(PREDECESSOR+'-continuous-best');c.mkdir()
        manifest=dict(worker_started_at=start.isoformat(),docker_started_at=self.worker['StartedAt'],
                      worker_id='original-id',worker_restart_count=0,
                      last_verified_running_at=(start+timedelta(hours=1)).isoformat())
        (c/'manifest.json').write_text(json.dumps(manifest))
        self.assertFalse(self.gate()['ready'])

        hour=copy.deepcopy(json.loads((self.state/'comparison.json').read_text())['completed'][0])
        hour.update(started_at=start.isoformat(),measured_until=(start+timedelta(hours=1)).isoformat(),
                    worker_verified_through=(start+timedelta(hours=1)).isoformat())
        p=self.state/'hourly-archive'/'continuous-hour-000000.json';p.parent.mkdir();p.write_text(json.dumps(hour))
        self.assertTrue(self.gate()['ready'])
        self.worker['FinishedAt']=(start+timedelta(hours=1,minutes=30)).isoformat()
        self.assertFalse(self.gate()['ready'])
        self.worker['FinishedAt']=(start+timedelta(hours=1,seconds=10)).isoformat();self.worker['Id']='different'
        self.assertFalse(self.gate()['ready'])

    def test_legacy_protocol_exception_is_scoped_and_cannot_verify_continuous_hours(self):
        r=json.loads((self.state/'comparison.json').read_text())['completed'][0]
        r.pop('worker_verified_through');r['run']=PREDECESSOR+'-curl-p1-s3'
        self.assertFalse(full_hour(r))
        self.assertTrue(full_hour(r,legacy_protocol_verified=True))
        r['run']=PREDECESSOR+'-continuous-best'
        self.assertFalse(full_hour(r,legacy_protocol_verified=True))

    def test_overlapping_hours_and_future_reports_cannot_pass(self):
        rows=json.loads((self.state/'comparison.json').read_text())['completed']
        path=self.root/'state'/rows[1]['run']/'audited-final.json'
        second=json.loads(path.read_text())
        for key in ['started_at','measured_until','worker_verified_through']:second[key]=rows[0][key]
        path.write_text(json.dumps(second))
        self.assertFalse(self.gate()['checks']['recorded_windows_nonoverlapping'])
        self.assertFalse(verify(self.root,self.independent,self.worker,self.supervisor,
                               now=self.start)['checks']['all_nine_exact_hours'])
