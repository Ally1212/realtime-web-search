"""Customer HTTP remains closed until fresh replay; old search stays available."""
import os
import unittest
from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_restore_gate import RestoreGate
from realtime.data_capability_jobs import digest
from tests import test_data_capability_http as fixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class RestoreGateHTTPTests(unittest.TestCase):
    tearDown=fixture.CapabilityHTTPTests.tearDown
    work=fixture.CapabilityHTTPTests.work
    request=fixture.CapabilityHTTPTests.request
    json=fixture.CapabilityHTTPTests.json
    submit=fixture.CapabilityHTTPTests.submit
    wait=fixture.CapabilityHTTPTests.wait

    def setUp(self):
        fixture.CapabilityHTTPTests.setUp(self)
        self.job=self.wait(self.submit());self.stop_worker.set();self.worker.join(3)
        self.assertFalse(self.worker.is_alive())
        self.recovery=DataErasureCheckpoint(self.data);self.recovery.initialize()
        self.key=b'isolated-http-restore-signing-key-0001';self.value=self.recovery.export(self.key)
        self.gate=RestoreGate(self.data,checkpoint_id=self.value['checkpoint_id'],checkpoint_hash=digest(self.value))

    def test_new_domain_reads_exports_subscriptions_and_writes_block_but_auth_and_legacy_work(self):
        jid=self.job['id'];oid=self.job['result_ref']
        paths=['/v1/jobs?domain=data','/v1/jobs/'+jid,'/v1/jobs/'+jid+'/progress','/v1/jobs/'+jid+'/data',
               '/v1/jobs/'+jid+'/export?format=zip','/v1/jobs/'+jid+'/response',
               '/v1/data/observations','/v1/data/observations/'+oid,
               '/v1/data/export?capability=trends_history&observation_id='+oid,'/v1/data/subscriptions?domain=data']
        for path in paths:
            with self.subTest(path=path):
                code,error=self.json(path);self.assertEqual(code,503,error);self.assertEqual(error['code'],'restore_required')
        for path in ['/v1/data/jobs','/v1/data/jobs/preview']:
            code,error=self.json(path,method='POST',payload={},idempotency='blocked')
            self.assertEqual(code,503,error);self.assertEqual(error['code'],'restore_required')
        self.assertEqual(self.json('/v1/jobs')[0],200)
        self.assertEqual(self.json('/v1/data/subscriptions')[0],200)
        self.assertEqual(self.json('/v1/jobs/'+jid,site=False)[0],401)
        self.assertEqual(self.json('/v1/jobs/'+jid,bearer=False)[0],401)
        self.recovery.replay(self.value,self.key,expected_hash=digest(self.value))
        self.assertTrue(self.gate.ready());self.assertEqual(self.json('/v1/jobs/'+jid)[0],200)
        self.assertEqual(self.json('/v1/data/observations/'+oid)[0],200)
        self.assertEqual(self.json('/v1/jobs/'+jid,key=self.other['api_key'])[0],404)

    def test_receipt_loss_closes_reads_and_putting_row_back_is_not_a_recovery(self):
        self.recovery.replay(self.value,self.key,expected_hash=digest(self.value));self.assertTrue(self.gate.ready())
        with self.data.connect() as db:db.execute('DELETE FROM sf_capability_erasure_replays')
        code,error=self.json('/v1/jobs/'+self.job['id']);self.assertEqual(code,503);self.assertEqual(error['code'],'restore_required')
        self.recovery.replay(self.value,self.key,expected_hash=digest(self.value))
        self.assertEqual(self.json('/v1/jobs/'+self.job['id'])[0],200)
