"""Private query erasure with real isolated queue, retry graph and recovery."""
import copy
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from realtime.data_capability_job_retention import JobRetention
from realtime.data_capability_jobs import digest
from realtime.data_capability_retry import preview,submit,lineage
from realtime.data_capability_executor import SourceResult,SourceFailure
from realtime.product import ProductError
from tests import test_data_capability_checkpoint as fixture
from tests import test_data_capability_executor as execution
from tests.test_data_capability_retry import failed_fact
from tests.test_data_capability_facts import parameters


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class JobRetentionTests(unittest.TestCase):
    setUp=fixture.CheckpointTests.setUp
    store=fixture.CheckpointTests.store
    start=fixture.CheckpointTests.start
    finish=fixture.CheckpointTests.finish
    saved=fixture.CheckpointTests.saved
    backup=fixture.CheckpointTests.backup
    restore=fixture.CheckpointTests.restore
    export=fixture.CheckpointTests.export
    replay=fixture.CheckpointTests.replay
    sign=fixture.CheckpointTests.sign
    error=fixture.CheckpointTests.error
    executor=execution.ExecutorTests.executor
    facts=execution.ExecutorTests.facts

    def tearDown(self):
        for e in getattr(self,'executors',[]):self.assertTrue(e.stop())
        fixture.CheckpointTests.tearDown(self)

    def test_four_domains_remove_query_content_and_restore_v2_without_rewriting_bills(self):
        jobs=[self.saved(c) for c in self.grants]
        backup=self.backup()
        with self.data.connect() as db:oldjobs=db.execute('SELECT * FROM sf_capability_jobs').fetchall()
        for job in jobs:
            result=JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased')
            self.assertEqual((result['jobs'],result['observations']),(1,1))
            for call in [lambda:self.runtime.job(self.tenant,job['id']),lambda:self.runtime.progress(self.tenant,job['id']),
                         lambda:self.runtime.job_data(self.tenant,job['id']),lambda:self.spool.get(self.tenant,job['id']),
                         lambda:preview(self.runtime,self.tenant,job['id']),lambda:lineage(self.runtime,self.tenant,job['id'])]:
                self.error('expired',call)
            raw=self.data.get(self.tenant,job['id']);self.assertEqual(raw['parameters'],{})
            self.assertEqual((raw['status'],raw['actual_cost']),(job['status'],job['actual_cost']))
        self.assertEqual(self.runtime.jobs(self.tenant,{})['jobs'],[]);self.assertEqual(self.data.list(self.tenant)['jobs'],[])
        value=self.export();self.assertEqual(value['schema_version'],'searchflow-data-erasure-3');self.assertEqual(len(value['jobs']),4)
        self.restore(backup)
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_job_erasures')
            for row in oldjobs:
                db.execute('UPDATE sf_capability_jobs SET parameters=%s,idempotency_key=%s WHERE id=%s',(Jsonb(row['parameters']),row['idempotency_key'],row['id']))
        self.assertTrue(self.runtime.job(self.tenant,jobs[0]['id'])['parameters'])
        result=self.replay(value);self.assertEqual(result['jobs_cleared'],4)
        self.assertEqual(self.replay(value)['jobs_cleared'],0)
        for row in oldjobs:
            self.error('expired',lambda:self.data.create(self.tenant,row['parameters'],row['idempotency_key'],self.grants[row['capability']],'0.5','USD'))
            self.error('expired',lambda:self.data.create(self.tenant,row['parameters'],'erased:'+str(row['id']),self.grants[row['capability']],'0.5','USD'))
            self.assertEqual(self.data.get(self.tenant,str(row['id']))['actual_cost'],str(row['actual_cost']))
        self.error('not_found',lambda:JobRetention(self.data).erase('foreign',jobs[0]['id'],reason='operator_erased'))

    def test_aliases_and_queued_retry_descendants_are_cleared_atomically(self):
        bundle=self.start('keyword_volume');bundle=list(bundle);bundle[2]=failed_fact(bundle[0]['parameters'],self.tenant)
        parent=self.finish(bundle)
        alias=self.start('keyword_volume');self.data.reuse(self.tenant,alias[0]['id'],alias[1]['lease_token'],parent['result_ref'],'0',alias[3])
        self.runtime.worker_ready=lambda *args:True
        plan=preview(self.runtime,self.tenant,parent['id'])
        child=submit(self.runtime,self.tenant,parent['id'],dict(plan_hash=plan['plan_hash'],budget=dict(maximum_cost='0.5',currency='USD')),'private-retry-key')
        result=JobRetention(self.data).erase(self.tenant,alias[0]['id'],reason='operator_erased')
        self.assertEqual((result['jobs'],result['observations']),(3,1))
        raw=self.data.get(self.tenant,child['id']);self.assertEqual((raw['status'],raw['actual_cost'],raw['parameters']),('canceled','0',{}))
        self.assertIsNone(self.data.claim('keyword_volume',self.tenant,lambda *args:self.grants['keyword_volume']))
        with self.data.connect() as db:
            row=db.execute('SELECT plan,plan_hash FROM sf_capability_retries').fetchone()
            self.assertEqual(row['plan'],{});self.assertEqual(row['plan_hash'],plan['plan_hash'])
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_job_erasures').fetchone()['n'],3)
        self.error('expired',lambda:lineage(self.runtime,self.tenant,child['id']))
        self.assertEqual(JobRetention(self.data).erase(self.tenant,parent['id'],reason='repeat')['new_job_markers'],0)

    def test_queued_job_releases_reservation_and_does_not_keep_free_text_keys_or_events(self):
        p=parameters();key='private query text '+str(uuid4())
        job=self.data.create(self.tenant,p,key,self.grants['trends_history'],'0.5','USD')
        with self.data.connect() as db:
            row=self.data._get(db,self.tenant,job['id']);self.data.event(db,row,self.now,dict(note=key))
        JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased')
        row=self.data.get(self.tenant,job['id']);self.assertEqual((row['actual_cost'],row['status']),('0','canceled'))
        with self.data.connect() as db:
            raw=db.execute('SELECT idempotency_key,parameters FROM sf_capability_jobs').fetchone()
            self.assertEqual(raw['idempotency_key'],'erased:'+job['id']);self.assertEqual(raw['parameters'],{})
            self.data.event(db,self.data._get(db,self.tenant,job['id']),self.now,dict(note=key))
            self.assertTrue(all(r['detail']=={} for r in db.execute('SELECT detail FROM sf_capability_job_events').fetchall()))
        self.assertNotIn(key,json.dumps(self.export()))
        self.error('expired',lambda:self.data.create(self.tenant,p,key,self.grants['trends_history'],'0.5','USD'))

    def test_inflight_known_bill_settles_after_erasure_without_retaining_source_facts(self):
        entered,release=threading.Event(),threading.Event()
        def execute(ctx):
            entered.set();self.assertTrue(release.wait(3))
            self.assertTrue(ctx.canceled())
            return SourceResult(self.facts(ctx),'0.2')
        e=self.executor(execute,response_store=self.spool)
        job=self.data.create(self.tenant,parameters(),str(uuid4()),self.grants['trends_history'],'0.5','USD')
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(e.run_once);self.assertTrue(entered.wait(2))
            try:
                JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased')
                row=self.data.get(self.tenant,job['id']);self.assertIsNone(row['actual_cost']);self.assertEqual(row['reservation_status'],'held')
            finally:release.set()
            self.assertTrue(future.result(timeout=3))
        row=self.data.get(self.tenant,job['id']);self.assertEqual((row['status'],row['actual_cost'],row['parameters']),('canceled','0.2',{}))
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)

    def test_unknown_inflight_bill_remains_held_and_reconciliation_cannot_reintroduce_notes(self):
        entered,release=threading.Event(),threading.Event()
        def execute(ctx):
            entered.set();self.assertTrue(release.wait(3));raise SourceFailure('source_unknown',actual_cost=None)
        e=self.executor(execute,response_store=self.spool)
        job=self.data.create(self.tenant,parameters(),str(uuid4()),self.grants['trends_history'],'0.5','USD')
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(e.run_once);self.assertTrue(entered.wait(2))
            try:JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased')
            finally:release.set()
            future.result(timeout=3)
        row=self.data.get(self.tenant,job['id']);self.assertEqual(row['status'],'reconciliation_required');self.assertIsNone(row['actual_cost'])
        self.data.reconcile(self.tenant,job['id'],'failed','0.3',reason='private query in external manual note')
        with self.data.connect() as db:
            row=db.execute('SELECT reconciliation_reason,actual_cost,status FROM sf_capability_jobs').fetchone()
            self.assertEqual((row['reconciliation_reason'],str(row['actual_cost']),row['status']),('erased_job_reconciliation','0.3','canceled'))

    def test_failure_or_graph_limit_rolls_back_all_job_fact_and_response_content(self):
        job=self.saved();before=self.runtime.job_data(self.tenant,job['id'])
        with patch('realtime.data_capability_job_retention.MAX_JOBS',0):
            self.error('erasure_checkpoint_limit',lambda:JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased'))
        from realtime.data_capability_fact_retention import FactRetention
        original=FactRetention._clear
        def fail(*args):original(*args);raise RuntimeError('fixture transaction failure')
        with patch.object(FactRetention,'_clear',side_effect=fail):
            with self.assertRaises(RuntimeError):JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased')
        self.assertEqual(self.runtime.job_data(self.tenant,job['id']),before)
        self.assertTrue(self.spool.get(self.tenant,job['id'])['payload'])
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_job_erasures').fetchone()['n'],0)

    def test_legacy_v1_checkpoint_remains_fact_response_only(self):
        job=self.saved();from realtime.data_capability_fact_retention import FactRetention
        FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
        value=self.export();value.pop('jobs');value.pop('subscriptions');value['schema_version']='searchflow-data-erasure-1';value['scope']='observation_content_and_source_responses';self.sign(value)
        result=self.replay(value);self.assertEqual(result['job_entries'],0)
        self.assertTrue(self.runtime.job(self.tenant,job['id'])['parameters'])

    def test_job_marker_identity_or_duplicate_keys_reject_entire_recovery(self):
        job=self.saved();JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased');value=self.export()
        bad=copy.deepcopy(value);bad['jobs'][0]['request_hash']='0'*64;self.sign(bad)
        self.error('erasure_checkpoint_conflict',lambda:self.replay(bad))
        bad=copy.deepcopy(value);bad['jobs'].append(copy.deepcopy(bad['jobs'][0]));self.sign(bad)
        self.error('invalid_erasure_checkpoint',lambda:self.replay(bad))

    def test_pre_job_checkpoint_marker_prevents_original_key_from_recreating_a_missing_job(self):
        job=self.saved()
        with self.data.connect() as db:old=db.execute('SELECT parameters,idempotency_key FROM sf_capability_jobs WHERE id=%s',(job['id'],)).fetchone()
        JobRetention(self.data).erase(self.tenant,job['id'],reason='operator_erased');value=self.export()
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_responses');db.execute('DELETE FROM sf_capability_job_events')
            db.execute('DELETE FROM sf_capability_job_observations');db.execute('DELETE FROM sf_capability_jobs')
            db.execute('DELETE FROM sf_capability_job_erasures');db.execute('DELETE FROM sf_capability_response_erasures')
        result=self.replay(value);self.assertEqual((result['job_entries'],result['jobs_cleared']),(1,0))
        self.error('expired',lambda:self.data.create(self.tenant,old['parameters'],old['idempotency_key'],self.grants['trends_history'],'0.5','USD'))
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_jobs').fetchone()['n'],0)
