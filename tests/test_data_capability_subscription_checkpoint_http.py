import os
import unittest
from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_restore_gate import RestoreGate
from realtime.data_capability_subscription_retention import SubscriptionRetention
from realtime.data_capability_jobs import digest
from tests import test_data_capability_http as fixture
from tests import test_data_capability_subscription_checkpoint as recovery_fixture

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SubscriptionCheckpointHTTPTests(unittest.TestCase):
    setUp=fixture.CapabilityHTTPTests.setUp
    tearDown=fixture.CapabilityHTTPTests.tearDown
    work=fixture.CapabilityHTTPTests.work
    request=fixture.CapabilityHTTPTests.request
    json=fixture.CapabilityHTTPTests.json
    submit=fixture.CapabilityHTTPTests.submit
    wait=fixture.CapabilityHTTPTests.wait
    backup_subs=recovery_fixture.SubscriptionCheckpointTests.backup_subs
    restore_subs=recovery_fixture.SubscriptionCheckpointTests.restore_subs

    def test_restored_old_subscription_blocked_before_replay_and_erased_after_pinned_recovery(self):
        job=self.wait(self.submit());self.stop_worker.set();self.worker.join(3);self.assertFalse(self.worker.is_alive())
        root='/v1/data/subscriptions';body=dict(domain='data',name='private restore fixture',filters=dict(capability='trends_history'))
        code,sub=self.json(root,method='POST',payload=body,idempotency='restore-sub-key');self.assertEqual(code,200)
        url=root+'/'+sub['id'];self.assertEqual(self.json(url+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(url+'/pull',method='POST',payload={});self.assertEqual(code,200)
        batch_url=url+'/batches/'+batch['batch_id'];self.assertEqual(self.request(batch_url+'/export?format=zip')[0],200)
        backup=self.backup_subs();recovery=DataErasureCheckpoint(self.data);recovery.initialize()
        key=b'isolated-subscription-recovery-signing-key-0001'
        SubscriptionRetention(self.data).erase(str(self.account['account_id']),sub['id'],reason='operator_erased')
        value=recovery.export(key);self.restore_subs(backup)
        gate=RestoreGate(self.data,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        code,error=self.json(url+'/status');self.assertEqual(code,503);self.assertEqual(error['code'],'restore_required')
        recovery.replay(value,key,expected_hash=digest(value));self.assertTrue(gate.ready())
        for path in [url+'/status',url+'/batches',batch_url]+[batch_url+'/export?format='+fmt for fmt in ['json','jsonl','csv','zip','manifest']]:
            self.assertEqual(self.request(path)[0],410)
            self.assertEqual(self.request(path,key=self.other['api_key'])[0],404)
            self.assertEqual(self.request(path,site=False)[0],401)
        self.assertEqual(self.json(root+'?domain=data')[1]['subscriptions'],[])
        self.assertEqual(self.json(root,method='POST',payload=body,idempotency='restore-sub-key')[0],410)
        self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],200)
        with self.data.connect() as db:db.execute('DELETE FROM sf_capability_subscription_erasure_replays')
        self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],503)
        recovery.replay(value,key,expected_hash=digest(value));self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],200)
