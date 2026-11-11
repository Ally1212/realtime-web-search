import os
import unittest
from realtime.data_capability_subscription_retention import SubscriptionRetention
from tests import test_data_capability_http as fixture

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SubscriptionRetentionHTTPTests(unittest.TestCase):
    setUp=fixture.CapabilityHTTPTests.setUp
    tearDown=fixture.CapabilityHTTPTests.tearDown
    work=fixture.CapabilityHTTPTests.work
    request=fixture.CapabilityHTTPTests.request
    json=fixture.CapabilityHTTPTests.json
    submit=fixture.CapabilityHTTPTests.submit
    wait=fixture.CapabilityHTTPTests.wait

    def test_erased_subscription_reads_actions_and_files_410_foreign_404_auth_401(self):
        job=self.wait(self.submit());root='/v1/data/subscriptions'
        body=dict(domain='data',name='private subscription',filters=dict(capability='trends_history'))
        code,sub=self.json(root,method='POST',payload=body,idempotency='sensitive-sub-key');self.assertEqual(code,200)
        url=root+'/'+sub['id']
        self.assertEqual(self.json(url+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(url+'/pull',method='POST',payload={});self.assertEqual(code,200)
        b_url=url+'/batches/'+batch['batch_id']
        SubscriptionRetention(self.data).erase(self.account['account_id'],sub['id'],reason='operator_erased')
        for path in [url+'/status',url+'/batches',b_url]+[b_url+'/export?format='+fmt for fmt in ['json','jsonl','csv','zip','manifest']]:
            with self.subTest(path=path):
                self.assertEqual(self.json(path)[0],410)
                self.assertEqual(self.json(path,key=self.other['api_key'])[0],404)
                self.assertEqual(self.json(path,site=False)[0],401)
        for path,payload in [(url+'/pause',{}),(url+'/resume',{}),(url+'/pull',{}),(b_url+'/ack',dict(sha256=batch['sha256'])),(b_url+'/discard',{})]:
            self.assertEqual(self.json(path,method='POST',payload=payload)[0],410)
        self.assertEqual(self.json(root,method='POST',payload=body,idempotency='sensitive-sub-key')[0],410)
        self.assertEqual(self.json(root+'?domain=data')[1]['subscriptions'],[])
        self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],200)
