"""Synthetic old-backup resurrection, identity fencing and v3 startup admission."""
import copy
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from psycopg import sql
from psycopg.types.json import Jsonb

from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_fact_retention import FactRetention
from realtime.data_capability_subscription_retention import SubscriptionRetention
from realtime.data_capability_restore_gate import RestoreGate
from realtime.data_capability_jobs import digest
from realtime.data_capability_subscriptions import CapabilitySubscriptions
from tests import test_data_capability_checkpoint as fixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SubscriptionCheckpointTests(unittest.TestCase):
    setUp=fixture.CheckpointTests.setUp
    tearDown=fixture.CheckpointTests.tearDown
    store=fixture.CheckpointTests.store
    start=fixture.CheckpointTests.start
    finish=fixture.CheckpointTests.finish
    saved=fixture.CheckpointTests.saved
    error=fixture.CheckpointTests.error
    sign=fixture.CheckpointTests.sign
    export=fixture.CheckpointTests.export
    replay=fixture.CheckpointTests.replay

    def subscription(self,cap='trends_history'):
        subs=CapabilitySubscriptions(self.runtime,self.tenant);key=str(uuid4())
        body=dict(domain='data',name='secret subscription name',filters=dict(capability=cap,country='US'))
        sub=subs.create(body,key);subs.action(sub['id'],'resume')
        batch=subs.batch(sub['id'])
        return subs,sub,body,key,batch

    def backup_subs(self):
        with self.data.connect() as db:
            return {t:db.execute('SELECT * FROM '+t).fetchall() for t in ['sf_capability_subscriptions','sf_capability_batches','sf_capability_subscription_seen']}

    def restore_subs(self,backup):
        with self.data.connect() as db:
            for t in ['sf_capability_subscription_erasures','sf_capability_subscription_erasure_replays','sf_capability_job_erasure_replays','sf_capability_erasure_replays','sf_capability_batches','sf_capability_subscription_seen']:
                db.execute('DELETE FROM '+t)
            for r in backup['sf_capability_subscriptions']:
                fields=[k for k in r if k!='id']
                db.execute(sql.SQL('UPDATE sf_capability_subscriptions SET {} WHERE id=%s').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in fields)),
                           [Jsonb(r[k]) if isinstance(r[k],(dict,list)) else r[k] for k in fields]+[r['id']])
            for t in ['sf_capability_batches','sf_capability_subscription_seen']:
                for r in backup[t]:
                    db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(t),sql.SQL(',').join(map(sql.Identifier,r)),sql.SQL(',').join(sql.Placeholder() for _ in r)),
                               [Jsonb(v) if isinstance(v,(dict,list)) else v for v in r.values()])

    def test_four_domain_old_backup_replay_clears_subscriptions_facts_and_responses_before_gate_admits(self):
        jobs=[self.saved(c) for c in self.grants]
        subscriptions=[self.subscription(c) for c in self.grants]
        backup=fixture.CheckpointTests.backup(self);subs_backup=self.backup_subs()
        for job in jobs:FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        for _,sub,_,_,_ in subscriptions:SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased')
        value=self.export();self.assertEqual(value['schema_version'],'searchflow-data-erasure-3')
        self.assertEqual(len(value['subscriptions']),4)
        encoded=str(value);self.assertNotIn('secret subscription name',encoded)
        for _,_,_,key,_ in subscriptions:self.assertNotIn(key,encoded)
        fixture.CheckpointTests.restore(self,backup);self.restore_subs(subs_backup)
        gate=RestoreGate(self.data,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        self.error('restore_required',lambda:subscriptions[0][0].list())
        proof=self.replay(value);self.assertTrue(gate.ready())
        self.assertEqual((proof['subscription_entries'],proof['subscriptions_cleared'],proof['facts_cleared'],proof['response_journal_rows_cleared']),(4,4,4,4))
        for subs,sub,body,key,batch in subscriptions:
            self.error('expired',lambda:subs.status(sub['id']))
            self.error('expired',lambda:subs.create(body,key))
            self.error('expired',lambda:subs.batch(sub['id'],batch['batch_id']))
        self.assertEqual(subscriptions[0][0].list()['subscriptions'],[])
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_batches').fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_subscription_seen').fetchone()['n'],0)
        again=self.replay(value);self.assertEqual(again['subscriptions_cleared'],0)
        # Receipt never shortcuts a later resurrected body.
        self.restore_subs(subs_backup);self.replay(value)
        self.assertTrue(gate.ready());self.assertEqual(subscriptions[0][0].list()['subscriptions'],[])

    def test_missing_original_subscription_is_premarked_and_original_key_cannot_recreate(self):
        self.saved();subs,sub,body,key,_=self.subscription()
        SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased');value=self.export()
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_subscription_erasures');db.execute('DELETE FROM sf_capability_subscriptions WHERE id=%s',(sub['id'],))
        proof=self.replay(value);self.assertEqual((proof['subscription_entries'],proof['subscriptions_cleared']),(1,0))
        self.error('expired',lambda:subs.create(body,key))
        self.error('not_found',lambda:subs.status(sub['id']))

    def test_same_original_key_restored_under_new_id_rolls_back_all_components(self):
        job=self.saved();subs,sub,body,key,_=self.subscription();backup=fixture.CheckpointTests.backup(self);sub_backup=self.backup_subs()
        FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased');value=self.export()
        fixture.CheckpointTests.restore(self,backup);self.restore_subs(sub_backup)
        with self.data.connect() as db:
            db.execute('UPDATE sf_capability_subscriptions SET idempotency_key=%s WHERE id=%s',('different-key',sub['id']))
        replacement=subs.create(body,key);self.assertNotEqual(replacement['id'],sub['id'])
        self.error('erasure_checkpoint_conflict',lambda:self.replay(value))
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_subscription_erasures').fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_response_erasures').fetchone()['n'],0)
        self.assertEqual(self.runtime.job_data(self.tenant,job['id'])['observation']['observation_id'],job['result_ref'])

    def test_identity_conflicts_and_scan_limit_roll_back_without_publishing_receipt(self):
        self.saved();_,sub,_,_,_=self.subscription();backup=self.backup_subs()
        SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased');value=self.export()
        for field in ['tenant_id','request_hash','idempotency_digest']:
            with self.subTest(field=field):
                self.restore_subs(backup);bad=copy.deepcopy(value)
                bad['subscriptions'][0][field]=str(uuid4()) if field=='tenant_id' else '0'*64;self.sign(bad)
                self.error('erasure_checkpoint_conflict',lambda:self.replay(bad))
        self.restore_subs(backup)
        with patch('realtime.data_capability_checkpoint.MAX_SCAN',0):
            self.error('erasure_checkpoint_limit',lambda:self.replay(value))
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_erasure_replays').fetchone()['n'],0)

    def test_signature_duplicate_identity_and_size_limits_reject_before_mutation(self):
        self.saved();_,sub,_,_,_=self.subscription()
        SubscriptionRetention(self.data).erase(self.tenant,sub['id'],reason='operator_erased');value=self.export()
        for mutation in ['duplicate_id','duplicate_key','extra','signature']:
            with self.subTest(mutation=mutation):
                bad=copy.deepcopy(value)
                if mutation=='duplicate_id':bad['subscriptions'].append(copy.deepcopy(bad['subscriptions'][0]))
                elif mutation=='duplicate_key':
                    entry=copy.deepcopy(bad['subscriptions'][0]);entry['subscription_id']=str(uuid4());bad['subscriptions'].append(entry)
                elif mutation=='extra':bad['subscriptions'][0]['name']='must not be present'
                if mutation!='signature':self.sign(bad)
                else:bad['signature']='0'*64
                self.error('invalid_erasure_checkpoint',lambda:self.replay(bad))
        with patch('realtime.data_capability_checkpoint.MAX_SUBSCRIPTIONS',0):
            self.error('erasure_checkpoint_limit',self.export)
            self.error('invalid_erasure_checkpoint',lambda:self.replay(value))

    def test_v1_v2_compatibility_never_admits_v3_gate_and_subscription_receipt_loss_latches_closed(self):
        value=self.export();old=copy.deepcopy(value);old.pop('subscriptions');old['checkpoint_id']=str(uuid4());old['schema_version']='searchflow-data-erasure-2';old['scope']='observations_jobs_and_source_responses';self.sign(old)
        self.assertEqual(self.replay(old)['subscription_entries'],0)
        legacy=copy.deepcopy(old);legacy.pop('jobs');legacy['schema_version']='searchflow-data-erasure-1';legacy['scope']='observation_content_and_source_responses';legacy['checkpoint_id']=str(uuid4());self.sign(legacy)
        self.assertEqual(self.replay(legacy)['subscription_entries'],0)
        # Even a valid, pinned v2 checkpoint is insufficient for the v3 gate.
        gate=RestoreGate(self.data,checkpoint_id=old['checkpoint_id'],checkpoint_hash=digest(old))
        self.error('restore_required',lambda:self.replay(old));self.assertFalse(gate.ready())
        # A second independent service owns its own immutable v3 requirement.
        from realtime.data_capability_store import CapabilityData
        clone=CapabilityData(self.data.dsn,clock=lambda:self.now)
        gate3=RestoreGate(clone,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        recovery=DataErasureCheckpoint(clone,self.spool);recovery.replay(value,self.key,expected_hash=digest(value));self.assertTrue(gate3.ready())
        with clone.connect() as db:db.execute('DELETE FROM sf_capability_subscription_erasure_replays WHERE checkpoint_id=%s',(value['checkpoint_id'],))
        self.assertFalse(gate3.ready());recovery.replay(value,self.key,expected_hash=digest(value));self.assertTrue(gate3.ready())
