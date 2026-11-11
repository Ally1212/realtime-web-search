"""Actual customer HTTP: erased tasks cannot leak query content via metadata."""
import os
import unittest
from realtime.data_capability_job_retention import JobRetention
from tests import test_data_capability_http as fixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class JobErasureHTTPTests(unittest.TestCase):
    setUp=fixture.CapabilityHTTPTests.setUp
    tearDown=fixture.CapabilityHTTPTests.tearDown
    work=fixture.CapabilityHTTPTests.work
    request=fixture.CapabilityHTTPTests.request
    json=fixture.CapabilityHTTPTests.json
    submit=fixture.CapabilityHTTPTests.submit
    wait=fixture.CapabilityHTTPTests.wait

    def test_owned_metadata_progress_lineage_retry_and_all_export_formats_are_blocked(self):
        job=self.wait(self.submit());base='/v1/jobs/'+job['id']
        JobRetention(self.data).erase(str(self.account['account_id']),job['id'],reason='operator_erased')
        for suffix in ['', '/progress','/data','/lineage','/retry','/response']:
            code,error=self.json(base+suffix);self.assertEqual(code,410,error);self.assertEqual(error['code'],'expired')
        for fmt in ['json','jsonl','csv','zip','manifest']:
            code,error=self.json(base+'/export?format='+fmt);self.assertEqual(code,410,error);self.assertEqual(error['code'],'expired')
        code,result=self.json('/v1/jobs?domain=data');self.assertEqual(code,200);self.assertEqual(result['jobs'],[])
        self.assertEqual(self.json(base,key=self.other['api_key'])[0],404)
        self.assertEqual(self.json(base,site=False)[0],401)
        self.assertEqual(self.json(base,bearer=False)[0],401)
        code,error=self.json('/v1/data/jobs',method='POST',payload=dict(job['parameters'],budget=dict(maximum_cost='0.5',currency='USD')),idempotency='fixture-one')
        self.assertEqual(code,410,error)

    def test_erased_queued_task_cannot_be_polled_or_started_by_fixture_worker(self):
        self.consume=False;job=self.submit();base='/v1/jobs/'+job['id']
        JobRetention(self.data).erase(str(self.account['account_id']),job['id'],reason='operator_erased')
        self.assertEqual(self.json(base)[0],410)
        raw=self.data.get(str(self.account['account_id']),job['id'])
        self.assertEqual((raw['status'],raw['actual_cost'],raw['parameters']),('canceled','0',{}))
        self.assertEqual(self.json(base+'/cancel',method='POST',payload={})[0],410)
        self.consume=True
        self.assertIsNone(self.data.claim('trends_history',self.provider,lambda *args:self.grants['trends_history']))
