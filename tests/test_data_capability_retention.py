"""Synthetic backup resurrection, erasure races, and durable retention checks."""
import copy
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
from datetime import timedelta

from realtime.data_capability_retention import ResponseRetention, _encode
from realtime.product import ProductError
import unittest
from tests import test_data_capability_responses as fixture


@unittest.skipUnless(__import__('os').getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class RetentionTests(unittest.TestCase):
    setUp = fixture.ResponseStoreTests.setUp
    tearDown = fixture.ResponseStoreTests.tearDown
    store = fixture.ResponseStoreTests.store
    start = fixture.ResponseStoreTests.start
    session = fixture.ResponseStoreTests.session
    save = fixture.ResponseStoreTests.save
    key = b'independent-local-test-signing-key-0001'

    def test_restore_replay_erases_body_metadata_and_is_idempotent(self):
        store=self.store(); job,claim,session=self.session(store); self.save(session,{'private_fixture':'body'})
        with self.data.connect() as db:
            backup=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        retention=ResponseRetention(store)
        store.erase(self.tenant,job['id'],reason='rights_revoked')
        checkpoint=retention.export(self.key)
        self.assertNotIn('private_fixture',json.dumps(checkpoint));self.assertNotIn('parameters',json.dumps(checkpoint))
        with self.data.connect() as db:
            from psycopg.types.json import Jsonb
            db.execute('DELETE FROM sf_capability_response_erasures')
            db.execute("UPDATE sf_capability_responses SET state='captured',payload=%s,metadata=%s,payload_bytes=%s,snapshot_hash=%s WHERE id=%s",
                       (Jsonb(backup['payload']),Jsonb(backup['metadata']),backup['payload_bytes'],backup['snapshot_hash'],backup['id']))
        self.assertEqual(store.get(self.tenant,job['id'])['payload'],{'private_fixture':'body'})
        self.assertEqual(retention.replay(checkpoint,self.key)['cleared'],1)
        self.assertEqual(retention.replay(checkpoint,self.key)['cleared'],0)
        with self.data.connect() as db:
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertIsNone(row['payload']);self.assertIsNone(row['metadata']);self.assertIsNone(row['snapshot_hash'])
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.status,410)
        with self.assertRaises(ProductError):self.save(session)

    def test_pre_row_tombstone_blocks_reservation_and_read_without_cross_tenant_leak(self):
        store=self.store();job,claim,session=self.session(store);self.save(session)
        retention=ResponseRetention(store);store.erase(self.tenant,job['id'],reason='operator_erased')
        checkpoint=retention.export(self.key)
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_responses');db.execute('DELETE FROM sf_capability_response_erasures')
        self.assertEqual(retention.replay(checkpoint,self.key)['cleared'],0)
        with self.assertRaises(ProductError) as exc:store.begin(self.tenant,job['id'],claim['lease_token'],retain_raw=True)
        self.assertEqual(exc.exception.status,410)
        with self.assertRaises(ProductError) as exc:store.get('other-tenant',job['id'])
        self.assertEqual(exc.exception.status,404)

    def test_invalid_signature_or_identity_conflict_rolls_back_entire_batch(self):
        store=self.store();jobs=[]
        for _ in range(2):
            job,_,session=self.session(store);self.save(session);store.erase(self.tenant,job['id'],reason='operator_erased');jobs.append(job)
        retention=ResponseRetention(store);checkpoint=retention.export(self.key)
        for bad in [dict(checkpoint,signature='0'*64),dict(checkpoint,scope='all_data')]:
            with self.assertRaises(ProductError) as exc:retention.replay(bad,self.key)
            self.assertEqual(exc.exception.code,'invalid_erasure_checkpoint')
        bad=copy.deepcopy(checkpoint);bad['entries'][-1]['tenant_id']='foreign'
        body={k:v for k,v in bad.items() if k!='signature'}
        bad['signature']=hmac.new(self.key,_encode(body),hashlib.sha256).hexdigest()
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_response_erasures')
            db.execute("UPDATE sf_capability_responses SET state='captured',payload='{}',metadata='{}'")
        with self.assertRaises(ProductError) as exc:retention.replay(bad,self.key)
        self.assertEqual(exc.exception.code,'erasure_checkpoint_conflict')
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_response_erasures').fetchone()['n'],0)
            self.assertEqual(db.execute("SELECT count(*) n FROM sf_capability_responses WHERE state='captured'").fetchone()['n'],2)

    def test_tick_durable_checkpoint_expiry_and_failed_write_rolls_back(self):
        store=self.store(ttl_seconds=10);job,_,session=self.session(store);self.save(session)
        self.now+=timedelta(seconds=11);retention=ResponseRetention(store)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):retention.tick(Path(d)/'missing'/'checkpoint',self.key)
            with self.data.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_response_erasures').fetchone()['n'],0)
                self.assertIsNotNone(db.execute('SELECT payload FROM sf_capability_responses').fetchone()['payload'])
            path=Path(d)/'checkpoint';result=retention.tick(path,self.key)
            self.assertEqual((result['expired'],result['entries']),(1,1))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            checkpoint=json.loads(path.read_bytes());self.assertEqual(checkpoint['entries'][0]['state'],'expired')
            self.assertEqual(retention.replay(checkpoint,self.key)['cleared'],0)
            self.assertEqual(retention.tick(path,self.key)['expired'],0)
            self.assertEqual(len(list(Path(d).iterdir())),1)

    def test_repeated_erase_preserves_first_marker_and_complete_export_rejects_overflow(self):
        from unittest.mock import patch
        store=self.store();job,_,session=self.session(store);self.save(session)
        retention=ResponseRetention(store);store.erase(self.tenant,job['id'],reason='first_reason')
        original=retention.export(self.key)
        self.now+=timedelta(seconds=1);store.erase(self.tenant,job['id'],reason='second_reason')
        self.assertEqual(retention.export(self.key),original)
        with patch('realtime.data_capability_retention.MAX_ENTRIES',0):
            with self.assertRaises(ProductError) as exc:retention.export(self.key)
            self.assertEqual(exc.exception.code,'erasure_checkpoint_limit')
        with self.assertRaises(ValueError):retention.export(b'too-short')
