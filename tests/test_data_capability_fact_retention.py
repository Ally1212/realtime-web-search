import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from realtime.data_capability_fact_retention import FactRetention
from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_subscriptions import CapabilitySubscriptions
from realtime.product import ProductError
from tests import test_data_capability_subscriptions as fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class FactRetentionTests(unittest.TestCase):
    setUp=fixtures.CapabilitySubscriptionTests.setUp
    def tearDown(self):
        with self.data.connect() as db:
            if db.execute("SELECT to_regclass('sf_capability_responses') AS t").fetchone()['t']:
                db.execute('DELETE FROM sf_capability_responses WHERE tenant_id=%s',(self.tenant,))
                db.execute('DELETE FROM sf_capability_response_erasures WHERE tenant_id=%s',(self.tenant,))
            db.execute('DELETE FROM sf_capability_fact_erasures WHERE tenant_id=%s',(self.tenant,))
        fixtures.CapabilitySubscriptionTests.tearDown(self)
    start=fixtures.CapabilitySubscriptionTests.start
    finish=fixtures.CapabilitySubscriptionTests.finish
    create=fixtures.CapabilitySubscriptionTests.create
    error=fixtures.CapabilitySubscriptionTests.error

    def test_four_domains_remove_facts_projections_and_block_every_delivery_entry(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                bundle=self.start(cap);done=self.finish(bundle);oid=done['result_ref'];retention=FactRetention(self.data)
                sub=self.create(cap);self.subs.action(sub['id'],'resume');batch=self.subs.batch(sub['id'])
                before=self.data.get(self.tenant,done['id'])
                self.assertTrue(retention.erase(self.tenant,oid,reason='rights_revoked'))
                for call in [lambda:self.runtime.observation(self.tenant,oid),
                             lambda:self.runtime.job_data(self.tenant,done['id']),
                             lambda:self.data.records(self.tenant,oid,'series',bundle[3]),
                             lambda:self.data.rebuild_projections(self.tenant,oid,bundle[3]),
                             lambda:self.subs.batch(sub['id'],batch['batch_id']),
                             lambda:self.subs.acknowledge(sub['id'],batch['batch_id'],batch['sha256']),
                             lambda:self.finish(bundle)]:
                    self.error('expired',call)
                self.assertEqual(self.runtime.observations(self.tenant,{'capability':cap})['records'],[])
                for kind in {'trends_history':['series'],'keyword_volume':['volume'],'aio_answer':['answer'],'ai_mode_answer':['answer']}[cap]:
                    self.assertEqual(self.runtime.observations(self.tenant,{'capability':cap},kind=kind)['records'],[])
                self.assertEqual(self.subs.status(sub['id'])['pending_observations'],0)
                self.assertEqual(self.subs.history(sub['id'],{})['batches'],[])
                self.assertFalse(retention.erase(self.tenant,oid,reason='second_reason'))
                after=self.data.get(self.tenant,done['id'])
                for name in ['actual_cost','reserved','status','reservation_status']:
                    self.assertEqual(before[name],after[name])
                with self.data.connect() as db:
                    row=db.execute('SELECT payload,parameters,revision,event_key FROM sf_capability_observations WHERE id=%s',(oid,)).fetchone()
                    self.assertEqual((row['payload'],row['parameters'],row['revision']),({},{},None))
                    self.assertEqual(row['event_key'],'erased:'+oid)
                    self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_fact_rows WHERE observation_id=%s',(oid,)).fetchone()['n'],0)
                    self.assertEqual(db.execute('SELECT reason FROM sf_capability_fact_erasures WHERE observation_id=%s',(oid,)).fetchone()['reason'],'rights_revoked')
                self.error('not_found',lambda:retention.erase('foreign',oid,reason='operator_erased'))

    def test_cache_alias_and_associated_response_are_erased_without_rebilling(self):
        bundle=self.start();job,claim,value,grant=bundle
        grant['raw_response_retention_allowed']=True
        store=ResponseStore(self.data,lambda t,c:self.grants[c]);store.initialize()
        session=store.begin(self.tenant,job['id'],claim['lease_token'],retain_raw=True)
        session.save(http_status=200,payload={'fixture':'private body'},requested_at=self.now,received_at=self.now,scope_verified=True)
        done=self.finish(bundle);alias=self.start();self.data.reuse(self.tenant,alias[0]['id'],alias[1]['lease_token'],done['result_ref'],'0',grant)
        FactRetention(self.data).erase(self.tenant,done['result_ref'],reason='operator_erased')
        self.error('expired',lambda:self.runtime.job_data(self.tenant,alias[0]['id']))
        self.error('expired',lambda:store.get(self.tenant,job['id']))
        self.assertEqual(self.data.get(self.tenant,alias[0]['id'])['actual_cost'],'0')
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_response_erasures WHERE job_id=%s',(job['id'],)).fetchone()['n'],1)
        fresh=self.start()
        self.error('expired',lambda:self.data.reuse(self.tenant,fresh[0]['id'],fresh[1]['lease_token'],done['result_ref'],'0',grant))

    def test_bounded_expiry_and_concurrent_erasure_preserve_one_marker(self):
        jobs=[self.finish(self.start()) for _ in range(3)];retention=FactRetention(self.data)
        with ThreadPoolExecutor(2) as pool:
            output=list(pool.map(lambda _:retention.erase(self.tenant,jobs[0]['result_ref'],reason='operator_erased'),range(2)))
        self.assertEqual(sorted(output),[False,True])
        self.now+=timedelta(days=2)
        self.assertEqual(retention.purge_expired(self.tenant,limit=1),{'erased':1,'has_more':True})
        self.assertEqual(retention.purge_expired(self.tenant,limit=1),{'erased':1,'has_more':False})
        self.assertEqual(retention.purge_expired(self.tenant,limit=1),{'erased':0,'has_more':False})
        self.error('invalid_parameter',lambda:retention.purge_expired(self.tenant,limit=True))

    def test_partial_cleanup_failure_rolls_back_facts_and_marker(self):
        from unittest.mock import patch
        job=self.finish(self.start());retention=FactRetention(self.data)
        before=self.runtime.observation(self.tenant,job['result_ref'])
        original=retention._clear
        def fail(*args):
            original(*args)
            raise RuntimeError('fixture transaction failure')
        with patch.object(retention,'_clear',side_effect=fail):
            with self.assertRaises(RuntimeError):retention.erase(self.tenant,job['result_ref'],reason='operator_erased')
        self.assertEqual(self.runtime.observation(self.tenant,job['result_ref']),before)
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_fact_erasures WHERE tenant_id=%s',(self.tenant,)).fetchone()['n'],0)
            self.assertGreater(db.execute('SELECT count(*) n FROM sf_capability_fact_rows WHERE observation_id=%s',(job['result_ref'],)).fetchone()['n'],0)
