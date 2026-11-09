import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from realtime.google_web_once import WebOnce, web_plan


class FakeAdapter:
    def __init__(self):
        self.calls=[]
        self.failure=None
        self.receipt_status='accepted'
        self.idle=True
        self.row=dict(request_id=str(uuid4()),source='google_web',query='solar energy research 2026',
            page=1,classification='results',completed_at=None,response={
                'search_parameters':{'gl':'us','hl':'en'},
                'organic_results':[{'link':'https://example.org/a','title':'Saved title','position':1}]})

    def assert_idle(self, plan):
        if not self.idle: raise ValueError('not idle')

    def acquire_once(self, plan):
        self.calls.append('source')
        self.row['completed_at']=datetime.now(timezone.utc)
        if self.failure=='source':raise TimeoutError('credential must not be recorded')
        return self.row['request_id']

    def saved_public_request(self, request_id):return copy.deepcopy(self.row)

    def validate(self, message):
        self.calls.append('validate')
        if self.failure=='validate':raise TimeoutError('secret')
        return self.failure!='invalid'

    def bulk_once(self, message):
        self.calls.append('bulk')
        if self.failure=='bulk':raise TimeoutError('secret')
        return dict(document_id='doc_test',version_id='ver_test')

    def receipt(self, message, acknowledgment):
        self.calls.append('receipt')
        return dict(status=self.receipt_status,document_id='doc_test',version_id='ver_test',
            dataset_id=message['dataset_id'],**{k:message['source'][k]
            for k in ('source_platform','source_name','source_record_key')})

    def readback(self, target, expected_payload):
        self.calls.append('readback')
        payload=copy.deepcopy(expected_payload)
        if self.failure=='readback':payload['observation']['occurrences'][0]['title']='Changed'
        return dict(payload=payload,proof=dict(payload_binding='pinned_version',payload_roundtrip_verified=True))


class WebOnceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.adapter=FakeAdapter()
        self.plan=web_plan(dataset_id='dataset-test',source_name='test',collector_id='collector-test')
        self.runner=WebOnce(Path(self.temp.name).resolve()/'journal',self.plan,self.adapter)

    def advance(self):return self.runner.advance(authorized_plan_hash=self.runner.plan_hash)

    def test_complete_and_replay_never_repeats_calls(self):
        state=self.advance()
        self.assertEqual(state['phase'],'verified')
        self.assertEqual(state['occurrence_count'],1)
        self.assertFalse(state['formal_admission'])
        self.advance()
        self.assertEqual(self.adapter.calls,['source','validate','bulk','receipt','readback'])

    def test_missing_authorization_and_nonidle_do_nothing(self):
        with self.assertRaises(ValueError):self.runner.advance(authorized_plan_hash='wrong')
        self.adapter.idle=False
        with self.assertRaises(ValueError):self.advance()
        self.assertEqual(self.adapter.calls,[])

    def test_uncertain_source_or_bulk_never_replays(self):
        for stage in ('source','bulk'):
            with self.subTest(stage=stage):
                runner=WebOnce(Path(self.temp.name).resolve()/stage,self.plan,FakeAdapter())
                runner.adapter.failure=stage
                state=runner.advance(authorized_plan_hash=runner.plan_hash)
                self.assertEqual(state['phase'],stage+'_intent')
                for _ in range(3):runner.advance(authorized_plan_hash=runner.plan_hash)
                self.assertEqual(runner.adapter.calls.count(stage),1)
                self.assertNotIn('credential',runner.path.read_text())
                self.assertNotIn('secret',runner.path.read_text())

    def test_queued_is_not_complete_and_poll_cap_is_durable(self):
        self.adapter.receipt_status='queued'
        for _ in range(15):state=self.advance()
        self.assertEqual(state['phase'],'awaiting_terminal')
        self.assertEqual(self.adapter.calls.count('receipt'),10)
        self.assertEqual(self.adapter.calls.count('bulk'),1)
        self.assertNotIn('readback',self.adapter.calls)

    def test_failed_receipt_stops_without_reupload(self):
        self.adapter.receipt_status='failed'
        self.assertEqual(self.advance()['phase'],'rejected')
        self.advance()
        self.assertEqual(self.adapter.calls.count('bulk'),1)

    def test_changed_source_before_poll_is_rejected(self):
        self.adapter.receipt_status='queued';self.advance()
        self.adapter.row['response']['organic_results'][0]['title']='Changed'
        with self.assertRaisesRegex(ValueError,'source changed'):self.advance()
        self.assertEqual(self.adapter.calls.count('receipt'),1)

    def test_wrong_pinned_payload_does_not_pass(self):
        self.adapter.failure='readback'
        with self.assertRaisesRegex(ValueError,'pinned payload'):self.advance()
        self.assertEqual(self.adapter.calls.count('bulk'),1)

    def test_interrupted_validation_does_not_replay(self):
        self.adapter.failure='validate'
        with self.assertRaises(TimeoutError):self.advance()
        self.assertEqual(self.advance()['phase'],'validation_intent')
        self.assertEqual(self.adapter.calls.count('validate'),1)
        self.assertNotIn('bulk',self.adapter.calls)

    def test_validation_failure_stops_before_bulk(self):
        self.adapter.failure='invalid'
        self.assertEqual(self.advance()['phase'],'rejected')
        self.assertNotIn('bulk',self.adapter.calls)

    def test_old_request_cannot_substitute(self):
        original=self.adapter.acquire_once
        def acquire(plan):
            result=original(plan)
            self.adapter.row['completed_at']-=timedelta(days=1)
            return result
        self.adapter.acquire_once=acquire
        with self.assertRaisesRegex(ValueError,'historical request'):self.advance()
        self.assertEqual(self.adapter.calls,['source'])

    def test_concurrent_controller_cannot_enter(self):
        with self.runner.locked():
            with self.assertRaises(BlockingIOError):self.advance()
        self.assertEqual(self.adapter.calls,[])

    def test_scope_or_journal_rebinding_is_rejected(self):
        self.adapter.receipt_status='queued';self.advance()
        plan={**self.plan,'dataset_id':'different'}
        other=WebOnce(self.runner.root,plan,self.adapter)
        with self.assertRaisesRegex(ValueError,'another plan'):other.advance(authorized_plan_hash=other.plan_hash)
        with self.assertRaisesRegex(ValueError,'scope'):WebOnce(self.runner.root,{**self.plan,'max_source_calls':2},self.adapter)

    def test_receipt_for_other_event_is_rejected(self):
        original=self.adapter.receipt
        def receipt(message, ack):
            return {**original(message,ack),'source_record_key':'different-event'}
        self.adapter.receipt=receipt
        with self.assertRaisesRegex(ValueError,'event scope'):self.advance()
        self.assertNotIn('readback',self.adapter.calls)

    def test_stop_before_source_and_between_source_and_upload(self):
        stop=self.runner.root/'STOP';stop.touch()
        with self.assertRaisesRegex(ValueError,'stop'):self.advance()
        self.assertEqual(self.adapter.calls,[])
        stop.unlink()
        original=self.adapter.acquire_once
        def acquire(plan):
            result=original(plan);stop.touch();return result
        self.adapter.acquire_once=acquire
        with self.assertRaisesRegex(ValueError,'stop'):self.advance()
        self.assertEqual(self.adapter.calls,['source'])

    def test_callers_plan_mutation_does_not_change_scope(self):
        self.plan['query']='different'
        self.assertEqual(self.advance()['phase'],'verified')

    def test_receipt_rate_limit_cooldown_survives_controller_restart(self):
        from realtime.google_web_once_platform import PlatformCallError
        def limited(message,ack):
            self.adapter.calls.append('receipt')
            raise PlatformCallError(429,'60')
        self.adapter.receipt=limited
        state=self.advance()
        self.assertEqual(state['phase'],'awaiting_terminal')
        self.assertIn('receipt_not_before',state)
        restarted=WebOnce(self.runner.root,self.plan,self.adapter)
        restarted.advance(authorized_plan_hash=restarted.plan_hash)
        self.assertEqual(self.adapter.calls.count('receipt'),1)
        self.assertEqual(self.adapter.calls.count('bulk'),1)


if __name__=='__main__':unittest.main()
