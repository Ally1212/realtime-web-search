"""Isolated response spool transactions and synthetic customer delivery only."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_response_export import build_bundle,verify_bundle
from realtime.product import ProductError
from tests import test_data_capability_store as fixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class ResponseStoreTests(unittest.TestCase):
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='response_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.env=patch.dict(os.environ,{'TEST_DATABASE_URL':make_conninfo(self.root,options='-c search_path='+self.schema)})
        self.env.start();fixture.CapabilityStoreTests.setUp(self)
        for grant in self.grants.values():grant['raw_response_retention_allowed']=True
    def tearDown(self):
        self.env.stop()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
    def store(self,**kw):
        value=ResponseStore(self.data,lambda t,c:self.grants[c],**kw);value.initialize();return value
    def start(self):return fixture.CapabilityStoreTests.start(self)
    def session(self,store):
        job,claim,_,_=self.start()
        return job,claim,store.begin(self.tenant,job['id'],claim['lease_token'],retain_raw=True)
    def save(self,session,payload=None,**kw):
        return session.save(http_status=kw.pop('http_status',200),payload=payload if payload is not None else {'fixture':'licensed response'},
                            requested_at=kw.pop('requested_at',self.now),received_at=kw.pop('received_at',self.now),
                            scope_verified=kw.pop('scope_verified',True),**kw)

    def test_immutable_capture_restart_and_separate_from_successful_facts(self):
        store=self.store();job,claim,session=self.session(store)
        saved=self.save(session)
        self.assertEqual(store.get(self.tenant,job['id']),saved)
        self.now+=timedelta(seconds=2)
        again=self.save(session,requested_at=self.now-timedelta(seconds=2),received_at=self.now-timedelta(seconds=2))
        self.assertEqual(again,saved)
        with self.assertRaises(ProductError):self.save(session,{'different':'body'})
        replica=ResponseStore(self.data,lambda t,c:self.grants[c])
        self.assertEqual(replica.get(self.tenant,job['id']),saved)
        self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'running')
        self.assertEqual(saved['normalized_fact_status'],'not_asserted')
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)

    def test_concurrent_quota_reservation_and_unused_release(self):
        store=self.store(max_payload_bytes=1024,max_total_bytes=1024,max_active_records=2)
        a=self.start();b=self.start()
        def begin(bundle):
            try:return store.begin(self.tenant,bundle[0]['id'],bundle[1]['lease_token'],retain_raw=True)
            except ProductError as exc:return exc.code
        with ThreadPoolExecutor(2) as pool:out=list(pool.map(begin,[a,b]))
        self.assertEqual(out.count('response_spool_full'),1)
        winner=next(x for x in out if x!='response_spool_full')
        winner.finish()
        self.assertTrue(store.ready(self.tenant,'trends_history',self.tenant))
        loser=b if winner.job_id==a[0]['id'] else a
        self.assertNotEqual(begin(loser),'response_spool_full')

    def test_policy_conflict_and_expiry_reclaims_payload_and_slots(self):
        store=self.store(max_payload_bytes=1024,max_total_bytes=1024,max_active_records=1,ttl_seconds=10)
        job,_,session=self.session(store);self.save(session)
        with self.assertRaises(ProductError) as exc:self.store()
        self.assertEqual(exc.exception.code,'response_policy_conflict')
        self.assertFalse(store.ready(self.tenant,'trends_history',self.tenant))
        self.now+=timedelta(seconds=11)
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.status,410)
        self.assertEqual(store.purge_expired(),1)
        with self.data.connect() as db:
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual((row['state'],row['payload'],row['reserved_bytes'],row['payload_bytes']),('expired',None,0,0))
        self.assertTrue(store.ready(self.tenant,'trends_history',self.tenant))

    def test_current_raw_permission_scope_and_tenant_are_checked(self):
        store=self.store();job,claim,session=self.session(store);self.save(session)
        with self.assertRaises(ProductError) as exc:store.get('other',job['id'])
        self.assertEqual(exc.exception.status,404)
        self.grants['trends_history']['raw_response_retention_allowed']=False
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.code,'response_retention_not_admitted')
        with self.assertRaises(ProductError):store.begin(self.tenant,job['id'],claim['lease_token'],retain_raw=True)
        self.grants['trends_history']['raw_response_retention_allowed']=True
        self.grants['trends_history']['allowed_use']='different'
        with self.assertRaises(ProductError):store.get(self.tenant,job['id'])

    def test_cancel_and_erasure_prevent_republication(self):
        store=self.store();job,claim,session=self.session(store);self.save(session)
        self.data.cancel(self.tenant,job['id'])
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.status,410)
        session.finish()
        with self.data.connect() as db:
            row=db.execute('SELECT state,payload FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual((row['state'],row['payload']),('erased',None))
        with self.assertRaises(ProductError):self.save(session)
        other,_,s2=self.session(store);self.save(s2);store.erase(self.tenant,other['id'],reason='operator_revocation')
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,other['id'])
        self.assertEqual(exc.exception.status,410)
        with self.assertRaises(ProductError):self.save(s2)

    def test_scope_mismatch_and_incomplete_body_never_publish_payload(self):
        store=self.store();job,_,session=self.session(store)
        saved=self.save(session,{'unexpected':'outside requested scope'},scope_verified=False,reason='source_scope_unverified')
        self.assertIsNone(saved['payload']);self.assertEqual(saved['raw_status'],'not_retained')
        other,_,s2=self.session(store)
        saved=self.save(s2,{'partial':'truncated'},response_complete=False,reason='source_incomplete')
        self.assertIsNone(saved['payload']);self.assertFalse(saved['response_complete'])
        self.assertEqual(store.get(self.tenant,job['id'])['payload_bytes'],0)
        self.assertEqual(store.get(self.tenant,other['id'])['payload_hash'],None)

    def test_unseen_network_response_is_explicit_not_retained(self):
        store=self.store();job,_,session=self.session(store)
        session.finish();snapshot=store.get(self.tenant,job['id'])
        self.assertEqual(snapshot['reason'],'source_response_unavailable')
        self.assertFalse(snapshot['response_complete']);self.assertIsNone(snapshot['http_status'])
        session.finish();self.assertEqual(store.get(self.tenant,job['id']),snapshot)

    def test_secret_or_oversize_rejected_and_database_fault_keeps_reservation(self):
        store=self.store(max_payload_bytes=1024,max_total_bytes=2048);job,_,session=self.session(store)
        for payload in [{'api_key':'do-not-store'},{'data':'x'*2048},{'data':float('nan')}]:
            with self.subTest(payload_type=type(payload)),self.assertRaises(ProductError):self.save(session,payload)
        with self.data.connect() as db:
            db.execute("CREATE FUNCTION fail_capture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.payload IS NOT NULL THEN RAISE EXCEPTION 'fixture write failure'; END IF; RETURN NEW; END $$")
            db.execute('CREATE TRIGGER fixture_capture_fault BEFORE UPDATE ON sf_capability_responses FOR EACH ROW EXECUTE FUNCTION fail_capture()')
        with self.assertRaises(psycopg.Error):self.save(session)
        with self.data.connect() as db:
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual(row['state'],'reserved');self.assertEqual(row['reserved_bytes'],1024);self.assertIsNone(row['payload'])
        session.finish();self.assertEqual(store.get(self.tenant,job['id'])['raw_status'],'not_retained')

    def test_large_response_five_formats_verify_and_corruption_rejects(self):
        store=self.store();job,_,session=self.session(store)
        saved=self.save(session,{'text':'=untrusted spreadsheet input '+('fixture长文本'*30000)})
        bundle=build_bundle(saved)
        proof=verify_bundle(bundle['files']['bundle.zip'],expected_response_id=saved['response_id'],expected_snapshot_hash=saved['snapshot_hash'])
        self.assertEqual(proof['normalized_fact_count'],0);self.assertEqual(proof['record_count'],1)
        self.assertEqual(set(bundle['files']),{'response.json','response.jsonl','response.csv','manifest.json','bundle.zip'})
        self.assertEqual(build_bundle(saved)['files'],bundle['files'])
        with self.assertRaises(ProductError):verify_bundle(bundle['files']['bundle.zip'],expected_response_id=saved['response_id'],expected_snapshot_hash='wrong')
        with self.data.connect() as db:db.execute("UPDATE sf_capability_responses SET payload='{}' WHERE job_id=%s",(job['id'],))
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.code,'corrupt_response')

    def checkpoint(self,session,phase='search',payload=None,**kwargs):
        return session.checkpoint(phase,http_status=kwargs.pop('http_status',200),
            payload=payload if payload is not None else dict(text='synthetic phase'),
            requested_at=kwargs.pop('requested_at',self.now),received_at=kwargs.pop('received_at',self.now),
            scope_verified=kwargs.pop('scope_verified',True),**kwargs)

    def test_journal_checkpoints_are_durable_immutable_and_seal_to_five_formats(self):
        store=self.store();job,claim,session=self.session(store)
        first=self.checkpoint(session,payload={'text':'first scoped response'})
        with store.service.connect() as db:
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual(row['state'],'reserved');self.assertEqual(row['payload']['calls'],[first])
        self.assertEqual(row['reserved_bytes']+row['payload_bytes'],store.payload_limit)
        self.assertEqual(self.checkpoint(session,payload={'text':'first scoped response'}),first)
        with self.assertRaises(ProductError):self.checkpoint(session,payload={'text':'replacement'})
        with self.assertRaises(ProductError):self.save(session)
        self.now+=timedelta(seconds=1)
        second=self.checkpoint(session,'expand',{'text':'all expanded blocks'})
        with self.assertRaises(ProductError) as exc:store.get(self.tenant,job['id'])
        self.assertEqual(exc.exception.code,'response_pending')
        session.finish();saved=store.get(self.tenant,job['id'])
        self.assertEqual(saved['capture_kind'],'source_call_journal');self.assertEqual(saved['call_count'],2)
        self.assertEqual(saved['payload']['calls'],[first,second])
        self.assertEqual(saved['normalized_fact_status'],'not_asserted')
        bundle=build_bundle(saved);verify_bundle(bundle['files']['bundle.zip'],expected_response_id=saved['response_id'],expected_snapshot_hash=saved['snapshot_hash'])
        self.assertEqual(len(bundle['files']),5)
        session.finish();self.assertEqual(store.get(self.tenant,job['id']),saved)
        with self.assertRaises(ProductError):self.checkpoint(session,'third')

    def test_abandoned_journal_seals_after_restart_without_repeating_source_call(self):
        store=self.store();job,claim,session=self.session(store);self.checkpoint(session)
        replica=ResponseStore(self.data,lambda t,c:self.grants[c]);self.assertEqual(replica.seal_abandoned(),0)
        self.now+=timedelta(seconds=301)
        self.assertEqual(replica.seal_abandoned(),1)
        before=replica.get(self.tenant,job['id']);self.assertEqual(before['call_count'],1)
        self.assertEqual(replica.seal_abandoned(),0);self.assertEqual(replica.get(self.tenant,job['id']),before)
        self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'running') # Sealing does not claim outcome/settlement.
        with self.assertRaises(ProductError):self.checkpoint(session,'would_repeat')

    def test_journal_preserves_good_phase_when_later_body_is_unretainable(self):
        store=self.store();job,_,session=self.session(store);self.checkpoint(session)
        self.checkpoint(session,'expand',{'api_key':'must never retain'},scope_verified=False,response_complete=False,reason='source_scope_unverified')
        session.finish();saved=store.get(self.tenant,job['id'])
        self.assertEqual(saved['payload']['calls'][0]['payload'],{'text':'synthetic phase'})
        self.assertIsNone(saved['payload']['calls'][1]['payload']);self.assertFalse(saved['response_complete'])
        self.assertFalse(saved['scope_verified']);self.assertNotIn('must never retain',json.dumps(saved))
        bundle=build_bundle(saved);verify_bundle(bundle['files']['bundle.zip'],expected_response_id=saved['response_id'],expected_snapshot_hash=saved['snapshot_hash'])

    def test_journal_total_quota_rejects_later_oversize_without_losing_first(self):
        store=self.store(max_payload_bytes=1500,max_total_bytes=1500);job,_,session=self.session(store)
        self.checkpoint(session,payload={'text':'first'})
        with self.assertRaises(ProductError) as exc:self.checkpoint(session,'expand',{'text':'x'*1500})
        self.assertEqual(exc.exception.code,'response_too_large')
        with self.data.connect() as db:
            row=db.execute('SELECT payload_bytes,reserved_bytes,payload FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual(row['payload_bytes']+row['reserved_bytes'],1500);self.assertEqual(len(row['payload']['calls']),1)
        session.finish();self.assertEqual(store.get(self.tenant,job['id'])['call_count'],1)

    def test_journal_cancel_expire_revoke_and_corruption_never_republish(self):
        store=self.store(ttl_seconds=10);job,_,session=self.session(store);self.checkpoint(session)
        self.data.cancel(self.tenant,job['id']);session.finish()
        with self.assertRaises(ProductError):store.get(self.tenant,job['id'])
        other,_,s2=self.session(store);self.checkpoint(s2)
        self.grants['trends_history']['raw_response_retention_allowed']=False
        with self.assertRaises(ProductError):self.checkpoint(s2,'expand')
        self.grants['trends_history']['raw_response_retention_allowed']=True
        with self.data.connect() as db:db.execute("UPDATE sf_capability_responses SET payload='{}' WHERE job_id=%s",(other['id'],))
        with self.assertRaises(ProductError) as exc:s2.finish()
        self.assertEqual(exc.exception.code,'corrupt_response')
        self.now+=timedelta(seconds=11);store.purge_expired()
        with self.assertRaises(ProductError):store.get(self.tenant,other['id'])

    def test_journal_bounds_chronology_and_sensitive_payload_validation(self):
        store=self.store();job,_,session=self.session(store)
        for kwargs in [dict(payload={'api_key':'secret'}),dict(http_status=True),dict(response_complete=1),dict(redacted_paths=['x'*4097])]:
            with self.subTest(kwargs=list(kwargs)),self.assertRaises(ProductError):self.checkpoint(session,**kwargs)
        self.checkpoint(session)
        self.now+=timedelta(seconds=2);self.checkpoint(session,'phase_two')
        with self.assertRaises(ProductError):self.checkpoint(session,'older',requested_at=self.now-timedelta(seconds=1))
        for n in range(6):self.checkpoint(session,'phase_'+str(n))
        with self.assertRaises(ProductError):self.checkpoint(session,'ninth')
        session.finish();self.assertEqual(store.get(self.tenant,job['id'])['call_count'],8)

    def test_runtime_poll_seals_only_this_tenants_expired_execution(self):
        from realtime.data_capability_queries import CapabilityRuntime
        store=self.store();job,_,session=self.session(store);self.checkpoint(session)
        self.now+=timedelta(seconds=301)
        self.assertEqual(store.seal_abandoned(tenant='other'),0)
        runtime=CapabilityRuntime(self.data,lambda t,c:self.grants[c],cursor_secret=b'synthetic-cursor-secret-for-call-tests',response_store=store)
        saved=runtime.response(self.tenant,job['id'])
        self.assertEqual(saved['call_count'],1);self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'reconciliation_required')
        self.assertEqual(runtime.response(self.tenant,job['id']),saved)
