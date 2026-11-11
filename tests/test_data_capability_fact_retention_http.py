"""Actual local customer HTTP: erase or revoke after file generation, before send."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from realtime.data_capability_fact_retention import FactRetention
from tests import test_data_capability_http as fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class ErasureHTTPTests(unittest.TestCase):
    setUp=fixtures.CapabilityHTTPTests.setUp
    tearDown=fixtures.CapabilityHTTPTests.tearDown
    work=fixtures.CapabilityHTTPTests.work
    request=fixtures.CapabilityHTTPTests.request
    json=fixtures.CapabilityHTTPTests.json
    submit=fixtures.CapabilityHTTPTests.submit
    wait=fixtures.CapabilityHTTPTests.wait

    def prepare(self,route):
        job=self.wait(self.submit(idempotency=str(uuid4())))
        oid=job['result_ref']
        if route=='job':
            from realtime import data_capability_export as module
            return job, '/v1/jobs/'+job['id']+'/export', module, 'build_bundle'
        if route=='observation':
            from realtime import data_capability_http as module
            return job, '/v1/data/export?capability=trends_history&observation_id='+oid,module,'build_bundle'
        from realtime import data_capability_subscriptions as module
        root='/v1/data/subscriptions'
        code,sub=self.json(root,method='POST',payload=dict(domain='data',name='erasure fixture',filters=dict(capability='trends_history'),start_mode='history'),idempotency=str(uuid4()))
        self.assertEqual(code,200,sub)
        url=root+'/'+sub['id'];self.assertEqual(self.json(url+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(url+'/pull',method='POST',payload={});self.assertEqual(code,200,batch)
        self.assertEqual(batch['payload']['observation_id'],oid)
        return job,url+'/batches/'+batch['batch_id']+'/export',module,'batch_files'

    def check_format(self,fmt):
        for route in ['job','observation','subscription']:
            with self.subTest(route=route,format=fmt):
                job,url,module,name=self.prepare(route);original=getattr(module,name)
                def build(*args,**kwargs):
                    result=original(*args,**kwargs)
                    FactRetention(self.data).erase(str(self.account['account_id']),job['result_ref'],reason='operator_erased')
                    return result
                with patch.object(module,name,side_effect=build):
                    code,error=self.json(url+('&' if '?' in url else '?')+'format='+fmt)
                self.assertEqual(code,410,error);self.assertEqual(error['code'],'expired')
                self.assertNotIn('files',error)
                self.assertEqual(self.json('/v1/data/observations/'+job['result_ref'],key=self.other['api_key'])[0],404)

    def test_json_erased_during_generation(self): self.check_format('json')
    def test_jsonl_erased_during_generation(self): self.check_format('jsonl')
    def test_csv_erased_during_generation(self): self.check_format('csv')
    def test_zip_erased_during_generation(self): self.check_format('zip')
    def test_manifest_erased_during_generation(self): self.check_format('manifest')

    def test_current_rights_are_resolved_again_after_generation(self):
        for route in ['job','observation','subscription']:
            with self.subTest(route=route):
                job,url,module,name=self.prepare(route);original=getattr(module,name)
                def build(*args,**kwargs):
                    result=original(*args,**kwargs)
                    self.grants['trends_history']['status']='revoked'
                    return result
                try:
                    with patch.object(module,name,side_effect=build):
                        code,error=self.json(url+('&' if '?' in url else '?')+'format=zip')
                    self.assertGreaterEqual(code,400);self.assertIn('code',error)
                finally:
                    self.grants['trends_history']['status']='approved'
                    FactRetention(self.data).erase(str(self.account['account_id']),job['result_ref'],reason='operator_erased')
