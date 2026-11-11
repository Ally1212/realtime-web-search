"""Synthetic adapters only; real PostgreSQL, no upstream calls or credentials."""
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from realtime.data_capability_executor import CapabilityExecutor, SourceResult, SourceFailure
from realtime.data_capability_sample import run_sample_step, run_sample_file
from realtime.product import ProductError
from tests import test_data_capability_store as store_fixture
from tests.test_data_capability_facts import parameters, result


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class ExecutorTests(unittest.TestCase):
    setUp = store_fixture.CapabilityStoreTests.setUp

    def tearDown(self):
        for executor in getattr(self, 'executors', []):
            self.assertTrue(executor.stop())
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_dispatches WHERE scope=%s', (self.tenant,))
            db.execute('DELETE FROM sf_capability_executor_scopes WHERE scope=%s', (self.tenant,))
        store_fixture.CapabilityStoreTests.tearDown(self)

    def executor(self, execute=None, cap='trends_history', **kwargs):
        def default(context):
            self.assertLess(self.now, context.deadline)
            self.assertFalse(context.canceled())
            facts = result(context.parameters, self.tenant)
            facts['requested_at'] = facts['received_at'] = self.now.isoformat()
            return SourceResult(facts, '0.1')
        e = CapabilityExecutor(self.data, lambda t, c: self.grants[c], capability=cap, provider=self.tenant,
                               scope=self.tenant, execute=execute or default, interval_seconds=kwargs.pop('interval_seconds', 0),
                               poll_seconds=.01, **kwargs)
        e.initialize()
        self.executors = getattr(self, 'executors', []) + [e]
        return e

    def facts(self, ctx):
        value = result(ctx.parameters, self.tenant)
        value['requested_at'] = value['received_at'] = self.now.isoformat()
        return value

    def create(self, cap='trends_history'):
        self.now += timedelta(microseconds=1)
        return self.data.create(self.tenant, parameters(cap), str(uuid4()), self.grants[cap], '0.5', 'USD')

    def row(self, job):
        return self.data.get(self.tenant, job['id'])

    def test_four_domains_and_single_dispatch_identity(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                e = self.executor(cap=cap)
                self.assertFalse(e.ready(self.tenant, cap, self.tenant))
                job = self.create(cap)
                self.assertTrue(e.run_once())
                self.assertEqual(self.row(job)['status'], 'succeeded')
                self.assertEqual(self.row(job)['actual_cost'], '0.1')
                self.assertFalse(e.run_once())
                saved = self.data.job_data(self.tenant, job['id'], self.grants[cap])
                expected = result(parameters(cap), self.tenant)
                expected['requested_at'] = expected['received_at'] = self.now.isoformat()
                self.assertEqual(saved['observation']['payload'], expected)
        with self.data.connect() as db:
            rows = db.execute('SELECT * FROM sf_capability_dispatches WHERE scope=%s', (self.tenant,)).fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['state'] == 'completed' for r in rows))

    def test_shared_account_concurrency_spacing_and_policy_conflict(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocking(ctx):
            calls.append(ctx.job_id)
            entered.set()
            self.assertTrue(release.wait(3))
            return SourceResult(self.facts(ctx), '0.1')
        e1 = self.executor(blocking, interval_seconds=10)
        e2 = self.executor(interval_seconds=10)
        jobs = [self.create(), self.create()]
        with ThreadPoolExecutor(2) as pool:
            work = pool.submit(e1.run_once)
            self.assertTrue(entered.wait(3))
            self.assertFalse(e2.run_once())
            release.set()
            self.assertTrue(work.result())
        self.assertFalse(e2.run_once())
        self.now += timedelta(seconds=10)
        self.assertTrue(e2.run_once())
        self.assertEqual([self.row(j)['status'] for j in jobs], ['succeeded', 'succeeded'])
        with self.assertRaises(ProductError) as exc:
            self.executor(interval_seconds=5)
        self.assertEqual(exc.exception.code, 'executor_policy_conflict')

    def test_confirmed_bill_failure_and_retry_after(self):
        def fail(ctx):
            raise SourceFailure('rate_limited', actual_cost='0.02', retry_after=60)
        e = self.executor(fail)
        first, second = self.create(), self.create()
        original_settle = self.data.settle
        def settle(*args, **kwargs):
            with self.data.connect() as db:
                scope = db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s', (self.tenant,)).fetchone()
            self.assertGreaterEqual(scope['next_at'], self.now + timedelta(seconds=60))
            return original_settle(*args, **kwargs)
        with patch.object(self.data, 'settle', side_effect=settle):
            self.assertTrue(e.run_once())
        self.assertEqual((self.row(first)['status'], self.row(first)['actual_cost']), ('failed', '0.02'))
        self.assertFalse(e.run_once())
        self.now += timedelta(seconds=59)
        self.assertFalse(e.run_once())
        self.now += timedelta(seconds=1)
        self.assertTrue(e.run_once())
        self.assertEqual(self.row(second)['error_code'], 'rate_limited')
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_dispatches WHERE scope=%s', (self.tenant,)).fetchone()['n'], 2)

    def test_unknown_transport_outcome_quarantines_and_blocks_account(self):
        calls = []
        def fail(ctx):
            calls.append(ctx.job_id)
            raise RuntimeError('SECRET token and unapproved source content')
        e = self.executor(fail)
        first, second = self.create(), self.create()
        e.run_once()
        self.assertEqual(self.row(first)['status'], 'reconciliation_required')
        self.assertEqual(self.row(first)['reservation_status'], 'held')
        self.assertFalse(e.run_once())
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(len(calls), 1)
        with self.data.connect() as db:
            row = db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s', (first['id'],)).fetchone()
            events = db.execute('SELECT detail FROM sf_capability_job_events WHERE job_id=%s', (first['id'],)).fetchall()
        self.assertNotIn('SECRET', str(row) + str(events))
        self.data.reconcile(self.tenant, first['id'], 'failed', '0.1', reason='fixture independent billing check')
        self.assertTrue(e.run_once())
        self.assertEqual(len(calls), 2)

    def test_invalid_facts_overbill_and_revoked_response_keep_bill_evidence(self):
        for mode in ['facts', 'cost', 'revoke', 'unknown_failure']:
            with self.subTest(mode=mode):
                job = self.create()
                def invalid(ctx):
                    facts = self.facts(ctx)
                    if mode == 'facts':
                        facts['data']['series'][0]['points'][0]['metric']['value'] = '999'
                    if mode == 'revoke':
                        self.grants['trends_history']['status'] = 'revoked'
                    if mode == 'unknown_failure':
                        raise SourceFailure('upstream_timeout')
                    return SourceResult(facts, '0.8' if mode == 'cost' else '0.1')
                e = self.executor(invalid)
                e.run_once()
                self.assertEqual(self.row(job)['status'], 'reconciliation_required')
                with self.data.connect() as db:
                    d = db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s', (job['id'],)).fetchone()
                    n = db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,)).fetchone()['n']
                self.assertEqual(n, 0)
                self.assertEqual(str(d['actual_cost']) if d['actual_cost'] is not None else None,
                                 None if mode == 'unknown_failure' else '0.8' if mode == 'cost' else '0.1')
                self.data.reconcile(self.tenant, job['id'], 'failed', d['actual_cost'] or '0', reason='fixture confirmed bill')
                self.grants['trends_history']['status'] = 'approved'

    def test_cancel_inflight_discards_response_but_settles_charge(self):
        def cancel(ctx):
            self.data.cancel(self.tenant, ctx.job_id)
            self.assertTrue(ctx.canceled())
            return SourceResult(self.facts(ctx), '0.15')
        e = self.executor(cancel)
        job = self.create()
        e.run_once()
        self.assertEqual((self.row(job)['status'], self.row(job)['actual_cost']), ('canceled', '0.15'))
        self.assertIsNone(self.row(job)['result_ref'])

    def test_raw_revoked_after_billing_keeps_reason_and_cost_without_replay(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                calls = []
                def source(ctx):
                    calls.append(ctx.job_id)
                    self.grants[cap]['raw_response_retention_allowed'] = False
                    return SourceResult(self.facts(ctx), '0.1')
                executor = self.executor(source, cap=cap)
                job = self.create(cap)
                self.assertTrue(executor.run_once())
                row = self.row(job)
                self.assertEqual(row['status'], 'reconciliation_required')
                self.assertEqual(row['error_code'], 'raw_retention_not_admitted')
                self.assertIsNone(row['result_ref'])
                with self.data.connect() as db:
                    dispatch = db.execute('SELECT actual_cost,error_code FROM sf_capability_dispatches WHERE job_id=%s', (job['id'],)).fetchone()
                    count = db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,)).fetchone()['n']
                self.assertEqual(count, 0)
                self.assertEqual(str(dispatch['actual_cost']), '0.1')
                self.assertEqual(dispatch['error_code'], row['error_code'])
                self.grants[cap]['raw_response_retention_allowed'] = True
                self.assertFalse(executor.run_once())
                self.assertEqual(calls, [job['id']])
                self.data.reconcile(self.tenant, job['id'], 'failed', '0.1', reason='synthetic bill confirmed')

    def test_pre_dispatch_revocation_and_cancellation_make_no_call(self):
        for mode in ['revoke', 'cancel']:
            with self.subTest(mode=mode):
                calls = []
                e = self.executor(lambda ctx: calls.append(ctx))
                job = self.create()
                original = self.data.claim
                def claim(*a, **kw):
                    c = original(*a, **kw)
                    if mode == 'revoke':
                        self.grants['trends_history']['status'] = 'revoked'
                    else:
                        self.data.cancel(self.tenant, job['id'])
                    return c
                with patch.object(self.data, 'claim', side_effect=claim):
                    self.assertTrue(e.run_once())
                self.assertEqual(calls, [])
                self.assertEqual(self.row(job)['actual_cost'], '0')
                self.grants['trends_history']['status'] = 'approved'

    def test_pre_dispatch_raw_permission_missing_or_revoked_makes_no_call(self):
        for cap in self.grants:
            for missing in (False, True):
                with self.subTest(cap=cap, missing=missing):
                    calls = []
                    def source(ctx):
                        calls.append(ctx.job_id)
                        raise AssertionError('must not enter source')
                    source.retain_raw = True
                    executor = self.executor(source, cap=cap)
                    job = self.create(cap)
                    original = self.data.claim
                    def claim(*args, **kwargs):
                        value = original(*args, **kwargs)
                        if missing:
                            self.grants[cap].pop('raw_response_retention_allowed')
                        else:
                            self.grants[cap]['raw_response_retention_allowed'] = False
                        return value
                    with patch.object(self.data, 'claim', side_effect=claim):
                        self.assertTrue(executor.run_once())
                    row = self.row(job)
                    self.assertEqual(calls, [])
                    self.assertEqual((row['status'], row['actual_cost'], row['error_code']),
                                     ('failed', '0', 'response_retention_not_admitted'))
                    with self.data.connect() as db:
                        self.assertIsNone(db.execute('SELECT job_id FROM sf_capability_dispatches WHERE job_id=%s', (job['id'],)).fetchone())
                    self.grants[cap]['raw_response_retention_allowed'] = True

    def test_targeted_single_step_does_not_consume_earlier_jobs(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                calls = []
                def source(ctx):
                    calls.append(ctx.job_id)
                    return SourceResult(self.facts(ctx), '0.1')
                executor = self.executor(source, cap=cap)
                earlier, target = self.create(cap), self.create(cap)
                for owner, jid in [('another-tenant', target['id']), (self.tenant, str(uuid4()))]:
                    self.assertFalse(executor.run_once(target_tenant_id=owner, target_job_id=jid))
                self.assertEqual(calls, [])
                self.assertTrue(executor.run_once(target_tenant_id=self.tenant, target_job_id=target['id']))
                self.assertEqual(calls, [target['id']])
                self.assertEqual(self.row(earlier)['status'], 'queued')
                self.assertEqual(self.row(target)['status'], 'succeeded')
                self.assertFalse(executor.run_once(target_tenant_id=self.tenant, target_job_id=target['id']))
                self.assertEqual(calls, [target['id']])
                self.data.cancel(self.tenant, earlier['id'])

    def test_sample_step_checks_budget_and_returns_complete_bundle_only(self):
        from realtime.data_capability_export import verify_bundle
        for cap in self.grants:
            with self.subTest(cap=cap):
                executor = self.executor(cap=cap)
                earlier, target = self.create(cap), self.create(cap)
                for ceiling, currency in [('0.01', 'USD'), ('1', 'CNY')]:
                    with self.assertRaises(ProductError) as denied:
                        run_sample_step(executor, self.tenant, target['id'], maximum_cost=ceiling, currency=currency)
                    self.assertEqual(denied.exception.code, 'sample_budget_mismatch')
                saved = run_sample_step(executor, self.tenant, target['id'], maximum_cost='0.5', currency='USD')
                self.assertEqual(saved['report']['status'], 'succeeded')
                self.assertEqual(saved['report']['actual_cost'], '0.1')
                proof = saved['proof']
                verify_bundle(saved['bundle'], expected_observation_id=proof['observation_id'],
                              expected_payload_hash=proof['payload_hash'], expected_snapshot_hash=proof['snapshot_hash'])
                self.assertEqual(self.row(earlier)['status'], 'queued')
                with self.assertRaises(ProductError) as denied:
                    run_sample_step(executor, self.tenant, target['id'], maximum_cost='0.5', currency='USD')
                self.assertEqual(denied.exception.code, 'sample_not_queued')
                self.data.cancel(self.tenant, earlier['id'])

    def test_sample_file_with_real_ledger_and_all_domain_bundles(self):
        import tempfile
        from pathlib import Path
        from realtime.data_capability_export import verify_bundle
        for cap in self.grants:
            with self.subTest(cap=cap), tempfile.TemporaryDirectory(prefix='sf-sample-ledger-') as directory:
                executor = self.executor(cap=cap)
                job = self.create(cap)
                target = Path(directory) / 'sample.zip'
                saved = run_sample_file(executor, self.tenant, job['id'], maximum_cost='0.5', currency='USD', output=target)
                self.assertTrue(saved['published'])
                proof = saved['proof']
                verify_bundle(target.read_bytes(), expected_observation_id=proof['observation_id'],
                              expected_payload_hash=proof['payload_hash'], expected_snapshot_hash=proof['snapshot_hash'])
                self.assertEqual(saved['report']['actual_cost'], '0.1')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_crash_after_dispatch_entry_never_repeats_source(self):
        e = self.executor(lease_seconds=10)
        job, second = self.create(), self.create()
        permit = e._permit()
        claim = self.data.claim('trends_history', self.tenant, lambda t, c: self.grants[c], lease_seconds=10)
        e._dispatch(claim)
        self.now += timedelta(seconds=11)
        self.assertFalse(e.run_once())
        self.assertEqual(self.row(job)['status'], 'reconciliation_required')
        self.assertEqual(self.row(second)['status'], 'queued')
        # An old owner cannot release the next owner's permit.
        self.data.reconcile(self.tenant, job['id'], 'failed', '0.03', reason='fixture provider receipt')
        newer = e._permit()
        self.assertIsNotNone(newer)
        e._release(permit, retry_after=60)
        with self.data.connect() as db:
            self.assertEqual(str(db.execute('SELECT token FROM sf_capability_executor_scopes WHERE scope=%s', (self.tenant,)).fetchone()['token']), newer)
        e._release(newer)
        self.assertFalse(e.run_once())
        self.now += timedelta(seconds=60)
        self.assertTrue(e.run_once())

    def test_commit_then_crash_repairs_journal_without_source_replay(self):
        e = self.executor(lease_seconds=10)
        job = self.create()
        e._permit()
        claim = self.data.claim('trends_history', self.tenant, lambda t, c: self.grants[c], lease_seconds=10)
        e._dispatch(claim)
        facts = result(parameters(), self.tenant)
        facts['requested_at'] = facts['received_at'] = self.now.isoformat()
        self.data.complete(self.tenant, job['id'], claim['lease_token'], facts, '0.11', self.grants['trends_history'],
                           event_key='capability:' + job['id'])
        self.now += timedelta(seconds=11)
        # A recovery pass repairs even when no other job is queued.
        self.assertFalse(e.run_once())
        with self.data.connect() as db:
            row = db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s', (job['id'],)).fetchone()
            self.assertEqual(row['state'], 'completed')
            self.assertEqual(str(row['actual_cost']), '0.11')
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,)).fetchone()['n'], 1)
        self.assertEqual(self.row(job)['status'], 'succeeded')

    def test_persistence_failure_rolls_back_fact_and_preserves_known_bill(self):
        e = self.executor()
        job = self.create()
        with patch.object(self.data, '_project', side_effect=RuntimeError('secret database payload')):
            e.run_once()
        self.assertEqual(self.row(job)['status'], 'reconciliation_required')
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,)).fetchone()['n'], 0)
            self.assertEqual(str(db.execute('SELECT actual_cost FROM sf_capability_dispatches WHERE job_id=%s', (job['id'],)).fetchone()['actual_cost']), '0.1')

    def test_idle_capability_does_not_consume_shared_quota(self):
        idle = self.executor(interval_seconds=60)
        active = self.executor(cap='aio_answer', interval_seconds=60)
        self.assertFalse(idle.run_once())
        job = self.create('aio_answer')
        self.assertTrue(active.run_once())
        self.assertEqual(self.row(job)['status'], 'succeeded')

    def test_partial_and_late_response_are_distinct(self):
        def partial(ctx):
            facts = self.facts(ctx)
            facts['is_partial'] = True
            facts['data']['series'][0]['points'][0]['partial'] = True
            return SourceResult(facts, '0.1')
        e = self.executor(partial)
        first = self.create()
        e.run_once()
        self.assertEqual(self.row(first)['status'], 'partial')
        def late(ctx):
            self.now += timedelta(seconds=301)
            return SourceResult(self.facts(ctx), '0.12')
        e.execute = late
        second = self.create()
        e.run_once()
        self.assertEqual(self.row(second)['status'], 'reconciliation_required')
        self.assertIsNone(self.row(second)['result_ref'])
        self.assertFalse(e.run_once())

    def test_explicit_lifecycle_and_stopped_readiness(self):
        e = self.executor()
        self.assertFalse(e.ready(self.tenant, 'trends_history', self.tenant))
        job = self.create()
        e.start()
        deadline = time.monotonic() + 3
        while self.row(job)['status'] == 'queued' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(e.ready(self.tenant, 'trends_history', self.tenant))
        self.assertFalse(e.ready(self.tenant, 'aio_answer', self.tenant))
        self.assertTrue(e.stop())
        self.assertFalse(e.ready(self.tenant, 'trends_history', self.tenant))
        self.assertFalse(e.run_once())
        self.assertEqual(self.row(job)['status'], 'succeeded')


    def bill(self,phase,cost='0.1',**kwargs):
        return dict(reference='fixture-invoice-'+phase,actual_cost=cost,currency='USD',billing_unit='fixture-call',**kwargs)

    def progress(self,job):
        from realtime.data_capability_queries import CapabilityRuntime
        runtime=CapabilityRuntime(self.data,lambda t,c:self.grants[c],cursor_secret=b'synthetic-cursor-secret-for-call-tests')
        return runtime.progress(self.tenant,job['id'])

    def test_multi_call_bills_persist_before_second_call_and_total_settlement(self):
        def source(ctx):
            self.assertEqual(ctx.before_call('search','0.2'),'0')
            self.assertEqual(ctx.confirm_bill('search',self.bill('search')),'0.1')
            self.assertEqual(ctx.confirm_bill('search',self.bill('search')),'0.1')
            with self.data.connect() as db:
                row=db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s',(ctx.job_id,)).fetchone()
            self.assertEqual(str(row['actual_cost']),'0.1');self.assertEqual(len(row['billing_parts']),1)
            self.assertEqual(ctx.before_call('expand','0.2'),'0.1')
            self.assertEqual(ctx.confirm_bill('expand',self.bill('expand','0.2')),'0.3')
            return SourceResult(self.facts(ctx),'0.3',billing_evidence=self.bill('whole','0.3'))
        e=self.executor(source,cap='aio_answer');job=self.create('aio_answer');e.run_once()
        self.assertEqual(self.row(job)['status'],'succeeded');self.assertEqual(self.row(job)['actual_cost'],'0.3')
        progress=self.progress(job);self.assertEqual(progress['confirmed_call_cost'],'0.3')
        self.assertEqual([p['phase'] for p in progress['confirmed_call_charges']],['search','expand'])
        self.assertEqual(progress['call_charge_basis'],'confirmed_call_receipts_not_final_settlement')

    def test_unknown_second_call_keeps_confirmed_first_bill_and_quarantines(self):
        calls=[]
        def source(ctx):
            calls.append('search');ctx.before_call('search','0.2');ctx.confirm_bill('search',self.bill('search'))
            ctx.before_call('expand','0.2');calls.append('expand');raise SourceFailure('source_transport_uncertain')
        e=self.executor(source);job=self.create();other=self.create();e.run_once()
        p=self.progress(job);self.assertEqual(p['status'],'reconciliation_required');self.assertIsNone(p['actual_cost'])
        self.assertEqual(p['confirmed_call_cost'],'0.1');self.assertEqual(p['reservation_status'],'held')
        self.assertFalse(e.run_once());self.assertEqual(self.row(other)['status'],'queued');self.assertEqual(calls,['search','expand'])
        with self.data.connect() as db:
            row=db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()
        self.assertEqual(str(row['actual_cost']),'0.1');self.assertEqual(row['state'],'quarantined')

    def test_per_call_gate_checks_remaining_budget_current_rights_and_cancel(self):
        for mode in ['budget','grant','raw','cancel','lease','stop']:
            with self.subTest(mode=mode):
                def source(ctx):
                    ctx.before_call('search','0.3');ctx.confirm_bill('search',self.bill('search','0.3'))
                    if mode=='grant':self.grants['trends_history']['status']='revoked'
                    if mode=='raw':self.grants['trends_history']['raw_response_retention_allowed']=False
                    if mode=='cancel':self.data.cancel(self.tenant,ctx.job_id)
                    if mode=='lease':self.now+=timedelta(seconds=301)
                    if mode=='stop':e._stop.set()
                    with self.assertRaises(ProductError):ctx.before_call('expand','0.3' if mode=='budget' else '0.01')
                    return SourceResult(self.facts(ctx),'0.3',billing_evidence=self.bill('whole','0.3'))
                source.retain_raw=mode=='raw'
                self.grants['trends_history']['raw_response_retention_allowed']=True
                e=self.executor(source);job=self.create();e.run_once()
                with self.data.connect() as db:
                    row=db.execute('SELECT billing_parts FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()
                self.assertEqual(row['billing_parts'][0]['actual_cost'],'0.3')
                self.grants['trends_history']['status']='approved'
                if self.row(job)['status']=='reconciliation_required':
                    self.data.reconcile(self.tenant,job['id'],'failed','0.3',reason='fixture receipt checked')

    def test_call_bill_cannot_change_units_phase_or_final_sum(self):
        def source(ctx):
            ctx.before_call('search','0.1');ctx.confirm_bill('search',self.bill('search'))
            for phase,evidence in [('search',self.bill('search','0.2')),('second',dict(self.bill('second'),currency='CNY')),
                ('unsafe',dict(self.bill('unsafe'),reference='https://secret.invalid/?token=x')),('../bad',self.bill('bad'))]:
                with self.subTest(phase=phase),self.assertRaises((ValueError,ProductError)):ctx.confirm_bill(phase,evidence)
            return SourceResult(self.facts(ctx),'0',billing_evidence=self.bill('whole','0'))
        e=self.executor(source);job=self.create();e.run_once()
        self.assertEqual(self.row(job)['status'],'reconciliation_required');self.assertIsNone(self.row(job)['actual_cost'])
        self.assertEqual(self.progress(job)['confirmed_call_cost'],'0.1')

    def test_no_receipts_is_unknown_not_zero_and_progress_respects_tenant_grant(self):
        e=self.executor();job=self.create();e.run_once();p=self.progress(job)
        self.assertIsNone(p['confirmed_call_cost']);self.assertEqual(p['confirmed_call_charges'],[])
        self.assertEqual(p['call_charge_basis'],'not_reported')
        from realtime.data_capability_queries import CapabilityRuntime
        rt=CapabilityRuntime(self.data,lambda t,c:self.grants[c],cursor_secret=b'synthetic-cursor-secret-for-call-tests')
        with self.assertRaises(ProductError) as exc:rt.progress('other',job['id'])
        self.assertEqual(exc.exception.status,404)
        self.grants['trends_history']['status']='revoked'
        with self.assertRaises(ProductError):self.progress(job)


    def test_unconfirmed_call_cannot_continue_reenter_or_claim_free_final_bill(self):
        def source(ctx):
            ctx.before_call('search','0.1')
            for phase in ['expand','search']:
                with self.subTest(phase=phase),self.assertRaises(ProductError):ctx.before_call(phase,'0.1')
            return SourceResult(self.facts(ctx),'0')
        e=self.executor(source);job=self.create();e.run_once()
        p=self.progress(job);self.assertEqual(p['status'],'reconciliation_required')
        self.assertIsNone(p['actual_cost']);self.assertIsNone(p['confirmed_call_cost'])
        self.assertEqual(p['source_call_attempts'][0]['bill_status'],'unconfirmed')
        self.assertFalse(e.run_once())

    def test_duplicate_phase_and_per_call_overbill_stop_further_spending(self):
        def source(ctx):
            ctx.before_call('search','0.1');ctx.confirm_bill('search',self.bill('search','0.2'))
            for phase in ['search','expand']:
                with self.subTest(phase=phase),self.assertRaises(ProductError):ctx.before_call(phase,'0.1')
            raise SourceFailure('source_call_budget_exceeded',actual_cost='0.2',billing_evidence=self.bill('whole','0.2'))
        e=self.executor(source);job=self.create();e.run_once()
        self.assertEqual(self.row(job)['status'],'failed');self.assertEqual(self.row(job)['actual_cost'],'0.2')
        p=self.progress(job);self.assertEqual(len(p['source_call_attempts']),1);self.assertEqual(p['confirmed_call_cost'],'0.2')

    def test_each_paid_hop_obeys_shared_spacing_and_next_job_waits_from_last_hop(self):
        from realtime.data_capability_executor import SourceCallDeferred
        def source(ctx):
            ctx.before_call('search','0.1');ctx.confirm_bill('search',self.bill('search'))
            with self.assertRaises(SourceCallDeferred) as deferred:ctx.before_call('expand','0.1')
            self.assertEqual(deferred.exception.retry_after,10)
            self.now+=timedelta(seconds=9)
            with self.assertRaises(SourceCallDeferred) as deferred:ctx.before_call('expand','0.1')
            self.assertEqual(deferred.exception.retry_after,1)
            self.now+=timedelta(seconds=1)
            ctx.before_call('expand','0.1');ctx.confirm_bill('expand',self.bill('expand'))
            return SourceResult(self.facts(ctx),'0.2',billing_evidence=self.bill('whole','0.2'))
        e=self.executor(source,interval_seconds=10);job=self.create();other=self.create();e.run_once()
        self.assertEqual(self.row(job)['status'],'succeeded')
        self.assertEqual(len(self.progress(job)['source_call_attempts']),2)
        self.assertFalse(e.run_once());self.now+=timedelta(seconds=10)
        e.execute=lambda ctx:SourceResult(self.facts(ctx),'0.1')
        self.assertTrue(e.run_once());self.assertEqual(self.row(other)['status'],'succeeded')

    def test_lost_account_permit_blocks_next_hop_and_cannot_clear_new_owner(self):
        owner=str(uuid4())
        def source(ctx):
            ctx.before_call('search','0.1');ctx.confirm_bill('search',self.bill('search'))
            with self.data.connect() as db:db.execute('UPDATE sf_capability_executor_scopes SET token=%s WHERE scope=%s',(owner,self.tenant))
            with self.assertRaises(ProductError):ctx.before_call('expand','0.1')
            raise SourceFailure('source_call_not_admitted',actual_cost='0.1',billing_evidence=self.bill('whole'))
        e=self.executor(source);job=self.create();e.run_once()
        self.assertEqual(self.row(job)['status'],'failed')
        with self.data.connect() as db:row=db.execute('SELECT token FROM sf_capability_executor_scopes WHERE scope=%s',(self.tenant,)).fetchone()
        self.assertEqual(str(row['token']),owner);self.assertEqual(len(self.progress(job)['source_call_attempts']),1)

    def test_late_confirmed_bill_after_expiry_is_saved_but_never_enables_another_call(self):
        def source(ctx):
            ctx.before_call('search','0.1');self.now+=timedelta(seconds=301)
            observer=self.executor()
            self.assertFalse(observer.run_once())
            with self.data.connect() as db:row=db.execute('SELECT state FROM sf_capability_dispatches WHERE job_id=%s',(ctx.job_id,)).fetchone()
            self.assertEqual(row['state'],'quarantined')
            self.assertEqual(ctx.confirm_bill('search',self.bill('late_search')),'0.1')
            with self.assertRaises(ProductError):ctx.before_call('expand','0.1')
            raise SourceFailure('source_deadline_exceeded',actual_cost='0.1',billing_evidence=self.bill('whole'))
        e=self.executor(source);job=self.create();e.run_once()
        p=self.progress(job);self.assertEqual(p['status'],'reconciliation_required');self.assertIsNone(p['actual_cost'])
        self.assertEqual(p['confirmed_call_cost'],'0.1');self.assertEqual(p['reservation_status'],'held')
        self.assertEqual(p['source_call_attempts'][0]['bill_status'],'confirmed');self.assertFalse(e.run_once())

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class ExecutorHTTPTests(unittest.TestCase):
    # Reuse only the isolated HTTP/site-gate setup, with its hand-written
    # consumer disabled. The candidate executor is the only claimant here.
    from tests import test_data_capability_http as _http
    request = _http.CapabilityHTTPTests.request
    json = _http.CapabilityHTTPTests.json
    submit = _http.CapabilityHTTPTests.submit
    wait = _http.CapabilityHTTPTests.wait

    def work(self):
        self.stop_worker.wait()

    def setUp(self):
        self._http.CapabilityHTTPTests.setUp(self)
        self.executors = []
        for cap in self.grants:
            def execute(ctx):
                facts = result(ctx.parameters, self.provider)
                facts['requested_at'] = facts['received_at'] = self.now.isoformat()
                return SourceResult(facts, '0.1')
            e = CapabilityExecutor(self.data, lambda t, c: self.grants[c], capability=cap,
                                   provider=self.provider, scope=self.schema, execute=execute,
                                   interval_seconds=0, poll_seconds=.01)
            e.initialize()
            self.executors.append(e)
        self.runtime.worker_ready = lambda t, c, p: any(e.ready(t, c, p) for e in self.executors)

    def tearDown(self):
        for e in self.executors:
            self.assertTrue(e.stop())
        self._http.CapabilityHTTPTests.tearDown(self)

    def test_real_customer_http_uses_only_explicit_live_executor(self):
        payload = dict(parameters(), budget=dict(maximum_cost='0.5', currency='USD'))
        code, failure = self.json('/v1/data/jobs', method='POST', payload=payload, idempotency='not-started')
        self.assertEqual(code, 503, failure)
        for e in self.executors:
            e.start()
        deadline = time.monotonic() + 3
        while not all(e.ready('', e.capability, self.provider) for e in self.executors) and time.monotonic() < deadline:
            time.sleep(.01)
        for cap in self.grants:
            with self.subTest(cap=cap):
                job = self.submit(cap, idempotency=cap)
                done = self.wait(job)
                self.assertEqual(done['status'], 'succeeded')
                code, facts = self.json(job['data_url'])
                self.assertEqual(code, 200, facts)
                self.assertEqual(facts['observation']['capability'], cap)
                self.assertEqual(facts['observation']['payload']['data'], result(parameters(cap), self.provider)['data'])
                code, hidden = self.json(job['data_url'], key=self.other['api_key'])
                self.assertEqual(code, 404, hidden)
        for e in self.executors:
            self.assertTrue(e.stop())
        code, failure = self.json('/v1/data/jobs', method='POST', payload=payload, idempotency='after-stop')
        self.assertEqual(code, 503, failure)
        with self.data.connect() as db:
            rows = db.execute('SELECT * FROM sf_capability_dispatches').fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['state'] == 'completed' for r in rows))

    def test_unknown_account_bill_disables_runtime_admission(self):
        def unknown(ctx):
            raise SourceFailure('source_bill_unknown')
        self.executors[0].execute = unknown
        for e in self.executors:
            e.start()
        deadline = time.monotonic() + 3
        while not self.executors[0].ready('', 'trends_history', self.provider) and time.monotonic() < deadline:
            time.sleep(.01)
        job = self.submit()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = self.data.get(str(self.account['account_id']), job['id'])
            if row['status'] == 'reconciliation_required':
                break
            time.sleep(.01)
        self.assertEqual(row['status'], 'reconciliation_required')
        self.assertTrue(all(not e.ready('', e.capability, self.provider) for e in self.executors))
        payload = dict(parameters('aio_answer'), budget=dict(maximum_cost='0.5', currency='USD'))
        code, failure = self.json('/v1/data/jobs', method='POST', payload=payload, idempotency='account-paused')
        self.assertEqual(code, 503, failure)
