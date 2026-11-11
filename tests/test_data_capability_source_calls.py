"""Synthetic multi-call foundation through real owned customer HTTP routes.

These are NOT SerpApi AIO responses, real supplier calls, or answer gold data.
"""
import json
import os
import time
import unittest

from realtime.data_capability_executor import SourceFailure,SourceResult
from realtime.data_capability_response_export import verify_bundle
from tests import test_data_capability_serpapi_ai as ai


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SourceCallHTTPTests(unittest.TestCase):
    setUp=ai.AIModeHTTPTests.setUp
    tearDown=ai.AIModeHTTPTests.tearDown
    work=ai.AIModeHTTPTests.work
    submit=ai.AIModeHTTPTests.submit
    request=ai.AIModeHTTPTests.request
    json=ai.AIModeHTTPTests.json
    wait=ai.AIModeHTTPTests.wait

    def mount(self,unknown=False):
        calls=[]
        def source(ctx):
            for phase in ['search','expand']:
                ctx.before_call(phase,'0.15');calls.append(phase)
                if phase=='expand' and unknown:
                    ctx.response.checkpoint(phase,http_status=None,payload=None,requested_at=self.now,received_at=self.now,
                        scope_verified=False,response_complete=False,reason='source_transport_uncertain')
                    raise SourceFailure('source_transport_uncertain')
                ctx.response.checkpoint(phase,http_status=200,payload=dict(synthetic_phase=phase),requested_at=self.now,received_at=self.now,scope_verified=True)
                ctx.confirm_bill(phase,dict(reference='fixture-invoice-'+phase,actual_cost='0.1',currency='USD',billing_unit='fixture-search'))
            facts=ai.normalize(ai.parameters('ai_mode_answer'),ai.response(),requested_at=self.now,received_at=self.now)
            return SourceResult(facts,'0.2',billing_evidence=dict(reference='fixture-aggregate-invoice',actual_cost='0.2',currency='USD',billing_unit='fixture-search'))
        source.retain_raw=True;source.requires_response_store=True
        self.executor.execute=source
        return calls

    def test_two_receipts_progress_sealed_journal_five_formats_and_private_rights(self):
        calls=self.mount();job=self.submit();state=self.wait(job)
        self.assertEqual((state['status'],state['actual_cost']),('succeeded','0.2'));self.assertEqual(calls,['search','expand'])
        code,p=self.json(job['status_url']+'/progress');self.assertEqual(code,200,p)
        self.assertEqual(p['confirmed_call_cost'],'0.2');self.assertEqual(len(p['confirmed_call_charges']),2)
        code,snapshot=self.json(job['status_url']+'/response');self.assertEqual(code,200,snapshot)
        self.assertEqual(snapshot['capture_kind'],'source_call_journal');self.assertEqual(snapshot['call_count'],2)
        self.assertEqual([c['phase'] for c in snapshot['payload']['calls']],['search','expand'])
        for format in ['json','jsonl','csv','manifest','zip']:
            with self.subTest(format=format):
                code,body,_=self.request(job['status_url']+'/response/export?format='+format+'&snapshot_hash='+snapshot['snapshot_hash'])
                self.assertEqual(code,200,body[:100])
                if format=='zip':verify_bundle(body,expected_response_id=snapshot['response_id'],expected_snapshot_hash=snapshot['snapshot_hash'])
        for path in [job['status_url']+'/progress',job['status_url']+'/response']:
            self.assertEqual(self.json(path,key=self.other['api_key'])[0],404)
        self.grants['ai_mode_answer']['status']='revoked'
        self.assertEqual(self.json(job['status_url']+'/progress')[0],409)
        self.assertEqual(self.json(job['status_url']+'/response')[0],409)

    def test_unknown_second_call_exposes_only_confirmed_sum_and_preserves_first_body(self):
        calls=self.mount(unknown=True);job=self.submit();end=time.monotonic()+4
        while time.monotonic()<end:
            state=self.json(job['status_url'])[1]
            if state['status']=='reconciliation_required':break
            time.sleep(.01)
        self.assertEqual(state['status'],'reconciliation_required');self.assertIsNone(state['actual_cost'])
        p=self.json(job['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.1')
        self.assertEqual(p['reservation_status'],'held');self.assertEqual(calls,['search','expand'])
        code,snapshot=self.json(job['status_url']+'/response');self.assertEqual(code,200,snapshot)
        self.assertEqual(snapshot['payload']['calls'][0]['payload'],dict(synthetic_phase='search'))
        self.assertIsNone(snapshot['payload']['calls'][1]['payload']);self.assertFalse(snapshot['response_complete'])
        self.assertEqual(self.json(job['data_url'])[0],409)
        payload=dict(ai.parameters('ai_mode_answer'),budget=dict(maximum_cost='0.5',currency='USD'))
        self.assertEqual(self.json('/v1/data/jobs',method='POST',payload=payload,idempotency='no-paid-retry')[0],503)
        self.assertEqual(calls,['search','expand'])

    def test_erased_spool_prevents_next_paid_call_without_discarding_known_bill(self):
        calls=[]
        def source(ctx):
            ctx.before_call('search','0.15');calls.append('search')
            ctx.response.checkpoint('search',http_status=200,payload=dict(synthetic_phase='search'),requested_at=self.now,received_at=self.now,scope_verified=True)
            ctx.confirm_bill('search',dict(reference='fixture-search-invoice',actual_cost='0.1',currency='USD',billing_unit='fixture-search'))
            self.spool.erase(str(self.account['account_id']),ctx.job_id,reason='operator_erasure')
            with self.assertRaises(ai.ProductError):ctx.before_call('expand','0.15')
            raise SourceFailure('response_capture_unavailable',actual_cost='0.1',billing_evidence=dict(reference='fixture-search-invoice',actual_cost='0.1',currency='USD',billing_unit='fixture-search'))
        source.retain_raw=True;source.requires_response_store=True;self.executor.execute=source
        job=self.submit();state=self.wait(job);self.assertEqual(state['status'],'failed')
        self.assertEqual(state['actual_cost'],'0.1');self.assertEqual(calls,['search'])
        self.assertEqual(self.json(job['status_url']+'/response')[0],410)
        p=self.json(job['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.1')
        self.assertEqual(len(p['source_call_attempts']),1)
