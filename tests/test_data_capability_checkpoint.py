"""Offline synthetic database resurrection, atomic recovery, and writer fencing."""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_fact_retention import FactRetention
from realtime.data_capability_jobs import digest
from realtime.data_capability_queries import CapabilityRuntime
from realtime.data_capability_retention import _encode
from realtime.product import ProductError
from tests import test_data_capability_responses as fixture
from tests import test_data_capability_store as facts


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class CheckpointTests(unittest.TestCase):
    tearDown=fixture.ResponseStoreTests.tearDown
    store=fixture.ResponseStoreTests.store
    start=facts.CapabilityStoreTests.start
    finish=facts.CapabilityStoreTests.finish

    def setUp(self):
        fixture.ResponseStoreTests.setUp(self)
        self.spool=self.store()
        self.recovery=DataErasureCheckpoint(self.data,self.spool);self.recovery.initialize()
        self.key=b'independently-stored-synthetic-recovery-key-0001'
        self.runtime=CapabilityRuntime(self.data,lambda t,c:self.grants[c],cursor_secret=b'isolated-checkpoint-cursor-secret'*2)

    def saved(self,cap='trends_history'):
        bundle=self.start(cap);job,claim,value,grant=bundle
        session=self.spool.begin(self.tenant,job['id'],claim['lease_token'],retain_raw=True)
        session.save(http_status=200,payload={'fixture':'independent source response'},requested_at=self.now,received_at=self.now,scope_verified=True)
        return self.finish(bundle)

    def backup(self):
        with self.data.connect() as db:
            return {table:db.execute('SELECT * FROM '+table).fetchall() for table in ['sf_capability_observations','sf_capability_fact_rows','sf_capability_responses']}

    def restore(self,backup):
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_fact_erasures');db.execute('DELETE FROM sf_capability_response_erasures')
            db.execute('DELETE FROM sf_capability_erasure_replays');db.execute('DELETE FROM sf_capability_fact_rows')
            for table in ['sf_capability_observations','sf_capability_responses']:
                for row in backup[table]:
                    fields=[k for k in row if k!='id']
                    db.execute(sql.SQL('UPDATE {} SET {} WHERE id=%s').format(sql.Identifier(table),sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in fields)),
                               [Jsonb(row[k]) if isinstance(row[k],dict) else row[k] for k in fields]+[row['id']])
            for row in backup['sf_capability_fact_rows']:
                db.execute('INSERT INTO sf_capability_fact_rows VALUES(%s,%s,%s,%s,%s,%s)',
                           (row['observation_id'],row['kind'],row['parent_id'],row['source_id'],row['position'],Jsonb(row['payload'])))

    def export(self): return self.recovery.export(self.key)
    def replay(self,value): return self.recovery.replay(value,self.key,expected_hash=digest(value))
    def sign(self,value):
        value['signature']=hmac.new(self.key,_encode({k:v for k,v in value.items() if k!='signature'}),hashlib.sha256).hexdigest()
        return value

    def error(self,code,call):
        with self.assertRaises(ProductError) as exc:call()
        self.assertEqual(exc.exception.code,code)

    def test_four_domain_old_database_resurrection_is_cleared_atomically_and_idempotently(self):
        jobs=[self.saved(c) for c in self.grants];backup=self.backup()
        for job in jobs:FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='rights_revoked')
        value=self.export();self.assertEqual(len(value['facts']),4);self.assertEqual(len(value['responses']['entries']),4)
        encoded=json.dumps(value);self.assertNotIn('independent source response',encoded);self.assertNotIn('parameters',encoded)
        self.restore(backup)
        for job in jobs:self.assertEqual(self.runtime.job_data(self.tenant,job['id'])['observation']['observation_id'],job['result_ref'])
        restored=self.replay(value);self.assertEqual((restored['facts_cleared'],restored['response_journal_rows_cleared']),(4,4))
        self.assertEqual(self.replay(value)['facts_cleared'],0)
        self.assertEqual(self.replay(value)['response_journal_rows_cleared'],0)
        for job in jobs:
            self.error('expired',lambda:self.runtime.job_data(self.tenant,job['id']))
            self.error('expired',lambda:self.spool.get(self.tenant,job['id']))
            self.assertEqual(self.data.get(self.tenant,job['id'])['actual_cost'],job['actual_cost'])
        self.assertEqual(self.runtime.observations(self.tenant,{})['records'],[])
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_fact_rows').fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_erasure_replays').fetchone()['n'],1)

    def test_first_response_erasure_time_survives_later_fact_cleanup_and_replay(self):
        job=self.saved();backup=self.backup()
        self.spool.erase(self.tenant,job['id'],reason='earlier_response_erasure')
        self.now+=timedelta(seconds=5)
        FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='later_fact_erasure')
        value=self.export();first=value['responses']['entries'][0]
        self.restore(backup);self.replay(value)
        after=self.export()['responses']['entries'][0]
        self.assertEqual(after,first)

    def test_identity_conflict_rolls_back_response_erasure_and_all_fact_markers(self):
        jobs=[self.saved(),self.saved()];backup=self.backup()
        for job in jobs:FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        value=self.export();self.restore(backup)
        bad=copy.deepcopy(value);bad['facts'][-1]['tenant_id']='wrong-owner';self.sign(bad)
        self.error('erasure_checkpoint_conflict',lambda:self.replay(bad))
        with self.data.connect() as db:
            for table in ['sf_capability_fact_erasures','sf_capability_response_erasures','sf_capability_erasure_replays']:
                self.assertEqual(db.execute('SELECT count(*) n FROM '+table).fetchone()['n'],0)
        for job in jobs:
            self.assertIn('payload',self.spool.get(self.tenant,job['id']))
            self.assertIn('payload',self.runtime.observation(self.tenant,job['result_ref']))

    def test_pre_row_markers_block_same_event_completion_before_an_observation_exists(self):
        job=self.saved();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased');value=self.export()
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_job_observations');db.execute('DELETE FROM sf_capability_observations')
            db.execute('DELETE FROM sf_capability_fact_erasures');db.execute('DELETE FROM sf_capability_responses')
            db.execute('DELETE FROM sf_capability_response_erasures')
        self.assertEqual(self.replay(value)['facts_cleared'],0)
        bundle=self.start()
        self.error('expired',lambda:self.finish(bundle,event_key=job['id']))
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)
        self.error('not_found',lambda:self.runtime.observation('foreign',job['result_ref']))

    def test_same_event_restored_under_a_different_id_cannot_bypass_marker(self):
        job=self.saved();backup=self.backup();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        value=self.export();self.restore(backup)
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_job_observations');db.execute('DELETE FROM sf_capability_fact_rows')
            db.execute('UPDATE sf_capability_observations SET id=%s',(str(uuid4()),))
        self.error('erasure_checkpoint_conflict',lambda:self.replay(value))
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_fact_erasures').fetchone()['n'],0)

    def test_signature_external_pin_duplicates_and_scope_are_validated_before_writes(self):
        job=self.saved();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased');value=self.export()
        self.error('invalid_erasure_checkpoint',lambda:self.recovery.replay(value,self.key,expected_hash='0'*64))
        for mutation in ['signature','duplicate','scope','response']:
            bad=copy.deepcopy(value)
            if mutation=='signature':bad['signature']='0'*64
            elif mutation=='duplicate':bad['facts'].append(copy.deepcopy(bad['facts'][0]));self.sign(bad)
            elif mutation=='scope':bad['scope']='all_customer_data';self.sign(bad)
            else:bad['responses']['entries'][0]['tenant_id']='forged';self.sign(bad)
            self.error('invalid_erasure_checkpoint',lambda:self.replay(bad))
        missing=DataErasureCheckpoint(self.data)
        self.error('erasure_checkpoint_scope',lambda:missing.export(self.key))
        self.error('invalid_erasure_checkpoint',lambda:missing.replay(value,self.key,expected_hash=digest(value)))
        with self.assertRaises(ValueError):self.recovery.export(b'short')

    def test_bounded_scan_or_export_fails_without_partial_replay_or_publication(self):
        job=self.saved();backup=self.backup();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased');value=self.export()
        with patch('realtime.data_capability_checkpoint.MAX_FACTS',0):
            self.error('erasure_checkpoint_limit',lambda:self.export())
        self.restore(backup)
        with patch('realtime.data_capability_checkpoint.MAX_SCAN',0):
            self.error('erasure_checkpoint_limit',lambda:self.replay(value))
        self.assertIn('payload',self.runtime.observation(self.tenant,job['result_ref']))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'checkpoint';result=self.recovery.checkpoint(path,self.key)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(digest(json.loads(path.read_bytes())),result['checkpoint_hash'])
            original=path.read_bytes()
            with patch('realtime.data_capability_checkpoint.MAX_BYTES',1):
                self.error('erasure_checkpoint_limit',lambda:self.recovery.checkpoint(path,self.key))
            self.assertEqual(path.read_bytes(),original)
            with self.assertRaises(OSError):self.recovery.checkpoint(Path(d)/'absent'/'file',self.key)
            self.assertEqual(len(list(Path(d).iterdir())),1)

    def test_completion_waits_for_recovery_fence_before_job_lock_then_rechecks_marker(self):
        bundle=self.start();event=bundle[0]['id'];oid=str(uuid4());started=threading.Event()
        def finish():
            started.set()
            try:self.finish(bundle)
            except ProductError as error:return error.code
            return 'unexpected_completed'
        with ThreadPoolExecutor(1) as pool:
            with self.data.connect() as db:
                db.execute('SELECT pg_advisory_xact_lock(20261110,30)')
                future=pool.submit(finish);self.assertTrue(started.wait(1))
                deadline=time.monotonic()+2;waiting=False
                while time.monotonic()<deadline:
                    waiting=db.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=20261110 AND objid=30 AND NOT granted) AS waiting").fetchone()['waiting']
                    if waiting:break
                    time.sleep(.01)
                self.assertTrue(waiting);self.assertFalse(future.done())
                db.execute('SELECT id FROM sf_capability_jobs WHERE id=%s FOR UPDATE NOWAIT',(event,))
                db.execute('INSERT INTO sf_capability_fact_erasures VALUES(%s,%s,%s,%s,%s)',(oid,self.tenant,digest(event),self.now,'restore_marker'))
            self.assertEqual(future.result(timeout=2),'expired')

    def test_fact_only_database_has_explicit_absent_spool_and_can_restore(self):
        with self.data.connect() as db:
            db.execute('DROP TABLE sf_capability_responses');db.execute('DROP TABLE sf_capability_response_policy')
            db.execute('DROP TABLE sf_capability_response_erasures')
        recovery=DataErasureCheckpoint(self.data);recovery.initialize()
        bundle=self.start();job=self.finish(bundle)
        with self.data.connect() as db:before=db.execute('SELECT * FROM sf_capability_observations').fetchone()
        FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        value=recovery.export(self.key);self.assertIsNone(value['responses'])
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_fact_erasures')
            db.execute('UPDATE sf_capability_observations SET event_key=%s,payload=%s,parameters=%s',(before['event_key'],Jsonb(before['payload']),Jsonb(before['parameters'])))
        result=recovery.replay(value,self.key,expected_hash=digest(value))
        self.assertEqual((result['facts_cleared'],result['response_entries']),(1,0))
        self.error('expired',lambda:self.runtime.observation(self.tenant,job['result_ref']))

    def test_replay_receipt_never_skips_later_resurrected_content_or_accepts_reused_id(self):
        job=self.saved();backup=self.backup();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        value=self.export();self.replay(value)
        with self.data.connect() as db:
            db.execute('UPDATE sf_capability_observations SET payload=%s',(Jsonb(backup['sf_capability_observations'][0]['payload']),))
        self.error('expired',lambda:self.runtime.observation(self.tenant,job['result_ref']))
        self.assertEqual(self.replay(value)['facts_cleared'],1)
        changed=copy.deepcopy(value);changed['created_at']=(self.now+timedelta(seconds=1)).isoformat();self.sign(changed)
        self.error('erasure_checkpoint_conflict',lambda:self.replay(changed))
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT checkpoint_hash FROM sf_capability_erasure_replays').fetchone()['checkpoint_hash'],digest(value))

    def test_bounded_file_replay_requires_exact_bytes_unique_keys_and_valid_json(self):
        job=self.saved();backup=self.backup();FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'checkpoint';proof=self.recovery.checkpoint(path,self.key);raw=path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),proof['checkpoint_hash'])
            self.restore(backup)
            path.write_bytes(raw+b' ')
            self.error('invalid_erasure_checkpoint',lambda:self.recovery.replay_file(path,self.key,expected_hash=proof['checkpoint_hash']))
            path.write_bytes(b'{"scope":"a","scope":"b"}')
            self.error('invalid_erasure_checkpoint',lambda:self.recovery.replay_file(path,self.key,expected_hash=hashlib.sha256(path.read_bytes()).hexdigest()))
            for bad in [b'{"x":NaN}',b'\xff',b'{']:
                path.write_bytes(bad)
                self.error('invalid_erasure_checkpoint',lambda:self.recovery.replay_file(path,self.key,expected_hash=hashlib.sha256(bad).hexdigest()))
            path.write_bytes(b'x'*2048)
            with patch('realtime.data_capability_checkpoint.MAX_BYTES',1):
                self.error('erasure_checkpoint_limit',lambda:self.recovery.replay_file(path,self.key,expected_hash=hashlib.sha256(path.read_bytes()).hexdigest()))
            self.assertIn('payload',self.runtime.observation(self.tenant,job['result_ref']))
            path.write_bytes(raw)
            result=self.recovery.replay_file(path,self.key,expected_hash=proof['checkpoint_hash'])
            self.assertEqual(result['facts_cleared'],1)
            self.error('expired',lambda:self.runtime.observation(self.tenant,job['result_ref']))
