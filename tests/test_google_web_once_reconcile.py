from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from realtime.google_data import digest
from realtime.google_web_once import WebOnce,web_plan
from realtime.google_web_once_reconcile import inspect_journal
from tests.test_google_web_once import FakeAdapter


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'journal'
        self.plan=web_plan(dataset_id='test',source_name='test',collector_id='test')
        self.adapter=FakeAdapter();self.adapter.failure='bulk'
        self.runner=WebOnce(self.root,self.plan,self.adapter)
        self.runner.advance(authorized_plan_hash=digest(self.plan))
        execution=dict(journal=str(self.root),plan=self.plan)
        (self.root/'execution.json').write_text(json.dumps(execution))
        self.hash=digest(execution)
        self.adapter.calls.clear()
        def forbidden(*args):raise AssertionError('mutation during read-only diagnosis')
        self.adapter.acquire_once=self.adapter.validate=self.adapter.bulk_once=forbidden

    def snapshot(self):
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob('*') if p.is_file()}

    def inspect(self):
        before=self.snapshot()
        try:return inspect_journal(self.root,expected_manifest_hash=self.hash,reader=self.adapter)
        finally:self.assertEqual(before,self.snapshot())

    def state(self,**updates):
        data=json.loads(self.runner.path.read_text());data.update(updates)
        self.runner.path.write_text(json.dumps(data))

    def test_unknown_bulk_can_be_proved_terminal_without_mutating_intent(self):
        report=self.inspect()
        self.assertEqual(report['status'],'writer_terminal_and_pinned_verified')
        self.assertFalse(report['replay_authorized'])
        self.assertEqual(self.adapter.calls,['receipt','readback'])
        self.assertEqual(json.loads(self.runner.path.read_text())['phase'],'bulk_intent')

    def test_unknown_and_queued_do_not_prove_failure_or_completion(self):
        for status in ('unknown','queued','retrying'):
            self.adapter.receipt_status=status
            report=self.inspect()
            self.assertEqual(report['status'],'writer_not_terminal')
            self.assertFalse(report['replay_authorized'])
        self.assertNotIn('readback',self.adapter.calls)

    def test_missing_source_evidence_is_unknown_not_failed(self):
        self.state(phase='source_intent',request_id=None)
        report=self.inspect()
        self.assertEqual(report['status'],'outcome_unknown')
        self.assertEqual(report['source_reads'],0)
        self.assertEqual(self.adapter.calls,[])

    def test_source_proof_recovers_read_only_identity_without_search(self):
        self.state(phase='source_intent',request_id=None)
        (self.root/'source-proof.json').write_text(json.dumps(dict(saved_request_ids=[self.adapter.row['request_id']])))
        self.assertEqual(self.inspect()['status'],'saved_source_verified')
        self.assertEqual(self.adapter.calls,[])

    def test_changed_source_or_wrong_event_is_rejected(self):
        original=self.adapter.receipt
        self.adapter.receipt=lambda message,ack:{**original(message,ack),'source_record_key':'other'}
        with self.assertRaisesRegex(ValueError,'event'):self.inspect()
        self.adapter.row['query']='changed'
        with self.assertRaisesRegex(ValueError,'identity'):self.inspect()

    def test_wrong_pinned_payload_is_rejected(self):
        self.adapter.failure='readback'
        with self.assertRaisesRegex(ValueError,'fixed payload'):self.inspect()

    def test_existing_cooldown_is_honored(self):
        self.state(receipt_not_before=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
        self.assertEqual(self.inspect()['status'],'receipt_cooldown')
        self.assertEqual(self.adapter.calls,[])

    def test_manifest_change_and_symlinks_refused(self):
        with self.assertRaisesRegex(ValueError,'binding'):inspect_journal(self.root,expected_manifest_hash='wrong',reader=self.adapter)
        self.runner.path.rename(self.root/'original-state.json')
        self.runner.path.symlink_to(self.root/'original-state.json')
        with self.assertRaisesRegex(ValueError,'private journal'):self.inspect()

    def test_rejection_does_not_authorize_replay(self):
        self.adapter.receipt_status='failed'
        report=self.inspect()
        self.assertEqual(report['status'],'writer_rejected')
        self.assertFalse(report['replay_authorized'])


if __name__=='__main__':unittest.main()
