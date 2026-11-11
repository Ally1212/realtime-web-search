import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch
from contextlib import contextmanager

from realtime.data_capability_subscription_retention import SubscriptionRetention
from realtime.data_capability_subscriptions import CapabilitySubscriptions
from realtime.data_capability_jobs import digest
from tests import test_data_capability_checkpoint as fixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SubscriptionRetentionTests(unittest.TestCase):
    setUp=fixture.CheckpointTests.setUp
    tearDown=fixture.CheckpointTests.tearDown
    saved=fixture.CheckpointTests.saved
    start=fixture.CheckpointTests.start
    finish=fixture.CheckpointTests.finish
    store=fixture.CheckpointTests.store
    error=fixture.CheckpointTests.error

    def subscription(self,cap='trends_history'):
        subs=CapabilitySubscriptions(self.runtime,self.tenant)
        key=str(uuid4());body=dict(domain='data',name='private-query-name',filters=dict(capability=cap,country='US'))
        sub=subs.create(body,key);subs.action(sub['id'],'resume')
        return subs,sub,body,key

    def test_four_domains_metadata_batches_and_original_key_erased_but_facts_preserved(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                job=self.saved(cap);subs,sub,body,key=self.subscription(cap)
                batch=subs.batch(sub['id']);self.assertIn('batch_id',batch)
                subs.acknowledge(sub['id'],batch['batch_id'],batch['sha256'])
                retention=SubscriptionRetention(self.data)
                proof=retention.erase(self.tenant,sub['id'],reason='operator_erased')
                self.assertEqual((proof['created'],proof['batches_removed'],proof['seen_removed']),(True,1,1))
                self.assertFalse(retention.erase(self.tenant,sub['id'],reason='rights_revoked')['created'])
                with self.data.connect() as db:
                    row=db.execute('SELECT * FROM sf_capability_subscriptions WHERE id=%s',(sub['id'],)).fetchone()
                    marker=db.execute('SELECT * FROM sf_capability_subscription_erasures WHERE subscription_id=%s',(sub['id'],)).fetchone()
                    self.assertEqual((row['name'],row['filters'],row['active'],row['next_poll_at']),('',{},False,None))
                    self.assertEqual(row['idempotency_key'],'erased:'+sub['id'])
                    self.assertEqual(marker['idempotency_digest'],digest(key));self.assertEqual(marker['reason'],'operator_erased')
                self.assertEqual(subs.list()['subscriptions'],[])
                for call in [lambda:subs.status(sub['id']),lambda:subs.batch(sub['id']),lambda:subs.batch(sub['id'],batch['batch_id']),
                             lambda:subs.action(sub['id'],'resume'),lambda:subs.history(sub['id'],{}),
                             lambda:subs.acknowledge(sub['id'],batch['batch_id'],batch['sha256']),lambda:subs.discard(sub['id'],batch['batch_id']),
                             lambda:subs.create(body,key),lambda:subs.create(body,'erased:'+sub['id'])]:
                    self.error('expired',call)
                self.assertEqual(self.runtime.job_data(self.tenant,job['id'])['observation']['observation_id'],job['result_ref'])
                fresh=subs.create(body,str(uuid4()));self.assertNotEqual(fresh['id'],sub['id'])
                retention.erase(self.tenant,fresh['id'],reason='operator_erased')

    def test_foreign_tenant_missing_and_invalid_reason_do_not_change_subscription(self):
        subs,sub,_,_=self.subscription();retention=SubscriptionRetention(self.data)
        self.error('not_found',lambda:retention.erase(str(uuid4()),sub['id'],reason='operator_erased'))
        self.error('not_found',lambda:retention.erase(self.tenant,str(uuid4()),reason='operator_erased'))
        with self.assertRaises(Exception):retention.erase(self.tenant,sub['id'],reason='private free text!')
        self.assertEqual(subs.status(sub['id'])['subscription']['name'],'private-query-name')
        retention.erase(self.tenant,sub['id'],reason='operator_erased')
        self.error('not_found',lambda:CapabilitySubscriptions(self.runtime,str(uuid4())).status(sub['id']))

    def test_transaction_failure_rolls_back_marker_and_contents(self):
        self.saved();subs,sub,body,key=self.subscription();batch=subs.batch(sub['id'])
        original=self.data.connect
        @contextmanager
        def fail():
            with original() as db:
                yield db
                raise RuntimeError('synthetic commit failure')
        with patch.object(self.data,'connect',fail):
            with self.assertRaises(RuntimeError):SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased')
        self.assertEqual(subs.batch(sub['id'])['batch_id'],batch['batch_id'])
        self.assertEqual(subs.create(body,key)['id'],sub['id'])
        with original() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_subscription_erasures').fetchone()['n'],0)

    def test_parallel_erasure_and_recreation_never_revives_original_key(self):
        subs,sub,body,key=self.subscription();retention=SubscriptionRetention(self.data)
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:retention.erase(self.tenant,sub['id'],reason='operator_erased'),range(4)))
        self.assertEqual(sum(r['created'] for r in results),1)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _:self.error('expired',lambda:subs.create(body,key)),range(4)))

    def test_legacy_joint_checkpoint_cannot_silently_omit_subscription_markers(self):
        value=self.recovery.export(self.key)
        value.pop('subscriptions');value['schema_version']='searchflow-data-erasure-2';value['scope']='observations_jobs_and_source_responses'
        fixture.CheckpointTests.sign(self,value)
        subs,sub,_,_=self.subscription()
        SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased')
        self.assertEqual(len(self.recovery.export(self.key)['subscriptions']),1)
        self.error('erasure_checkpoint_scope',lambda:self.recovery.replay(value,self.key,expected_hash=digest(value)))
