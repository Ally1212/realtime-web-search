"""Real PostgreSQL transaction tests; fixture grants authorize no real provider."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from realtime.data_capability_jobs import CapabilityJobs, amount
from realtime.product import ProductError


class AmountTests(unittest.TestCase):
    def test_no_float_or_nonfinite_budget(self):
        for value in (True, 0.1, 'NaN', 'Infinity', '-1', '0.0000000001', '1000000000001', None):
            with self.subTest(value=value), self.assertRaises(ProductError):
                amount(value)
        self.assertEqual(str(amount('0.000001')), '0.000001')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CapabilityJobTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        self.jobs = CapabilityJobs(os.environ['TEST_DATABASE_URL'], clock=lambda: self.now)
        self.jobs.initialize()
        self.tenant = 'fixture-' + str(uuid4())
        self.jobs.configure_budget(self.tenant, 'USD', '1', '2')
        self.payload = dict(capability='keyword_volume', country='US', language='en', keywords=['apple', '苹果'],
                            start_month='2025-01', end_month='2026-01', network='google')
        self.grant = dict(capability='keyword_volume', status='approved', provider=self.tenant,
                          license_id='fixture-only', allowed_use='isolated_test', retention_days=1,
                          billing_unit='keyword', currency='USD', maximum_cost='0.40',
                          expires_at='2027-01-01T00:00:00Z', validate_parameters=lambda p: None)

    def tearDown(self):
        with self.jobs.connect() as db:
            db.execute('DELETE FROM sf_capability_job_events WHERE job_id IN (SELECT id FROM sf_capability_jobs WHERE tenant_id=%s)', (self.tenant,))
            db.execute('DELETE FROM sf_capability_jobs WHERE tenant_id=%s', (self.tenant,))
            db.execute('DELETE FROM sf_capability_budgets WHERE tenant_id=%s', (self.tenant,))

    def create(self, key='one', **kwargs):
        return self.jobs.create(self.tenant, kwargs.get('payload', self.payload), key,
                                kwargs.get('grant', self.grant), kwargs.get('maximum_cost', '0.50'),
                                kwargs.get('currency', 'USD'))

    def claim(self, **kwargs):
        return self.jobs.claim('keyword_volume', self.tenant, lambda tenant, cap: self.grant, **kwargs)

    def test_concurrent_idempotency_and_isolation(self):
        with ThreadPoolExecutor(4) as pool:
            rows = list(pool.map(lambda _: self.create(), range(4)))
        self.assertEqual(len({r['id'] for r in rows}), 1)
        self.assertEqual(len(self.jobs.list(self.tenant)['jobs']), 1)
        for kwargs in ({'maximum_cost': '0.60'}, {'payload': {**self.payload, 'keywords': ['changed']}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProductError) as e:
                self.create(**kwargs)
            self.assertEqual(e.exception.code, 'idempotency_conflict')
        self.assertEqual(self.jobs.list('different')['jobs'], [])
        for operation in (self.jobs.get, self.jobs.cancel):
            with self.assertRaises(ProductError) as e:
                operation('different', rows[0]['id'])
            self.assertEqual(e.exception.status, 404)
        self.assertNotIn('lease_token', rows[0])
        self.assertNotIn('admission', rows[0])

    def test_concurrent_budget_reservation_and_refund(self):
        def create(index):
            try:
                return self.create(str(index))
            except ProductError as exc:
                return exc.code
        with ThreadPoolExecutor(6) as pool:
            rows = list(pool.map(create, range(6)))
        accepted = [r for r in rows if isinstance(r, dict)]
        self.assertEqual(len(accepted), 2)
        self.assertEqual(rows.count('budget_exceeded'), 4)
        canceled = self.jobs.cancel(self.tenant, accepted[0]['id'])
        self.assertEqual(canceled['actual_cost'], '0')
        self.assertEqual(canceled['reservation_status'], 'settled')
        self.assertEqual(self.jobs.cancel(self.tenant, accepted[0]['id']), canceled)
        self.assertEqual(self.create('after-refund')['status'], 'queued')

    def test_admission_fail_closed_and_recheck_before_execution(self):
        with self.assertRaises(ProductError):
            self.create(grant=None)
        self.assertEqual(self.jobs.list(self.tenant)['jobs'], [])
        job = self.create()
        self.grant['status'] = 'revoked'
        self.assertIsNone(self.claim())
        row = self.jobs.get(self.tenant, job['id'])
        self.assertEqual((row['status'], row['actual_cost']), ('failed', '0'))
        self.assertEqual(row['error_code'], 'source_not_admitted')
        self.grant['status'] = 'approved'
        # Accepted idempotent retry reports the original terminal job; no new call.
        self.assertEqual(self.create()['id'], job['id'])
        job2 = self.create('rights-change')
        self.grant['allowed_use'] = 'different-purpose'
        self.assertIsNone(self.claim())
        self.assertEqual(self.jobs.get(self.tenant, job2['id'])['error_code'], 'admission_changed')

    def test_claim_once_settlement_and_duplicate_conflict(self):
        job = self.create()
        with ThreadPoolExecutor(2) as pool:
            claims = list(pool.map(lambda _: self.claim(), range(2)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        claim = next(c for c in claims if c)
        for token, cost in (('invalid', '0.2'), (claim['lease_token'], '0.41')):
            with self.assertRaises(ProductError):
                self.jobs.settle(self.tenant, job['id'], token, 'succeeded', cost, result_ref='observation-fixture')
        with self.assertRaises(ProductError) as e:
            self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'succeeded', '0.20', result_ref='observation-fixture')
        self.assertEqual(e.exception.code, 'result_not_saved')
        result = self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'failed', '0.20')
        self.assertEqual(result['platform_delivery'], 'not_submitted')
        self.assertEqual(result['actual_cost'], '0.20')
        self.assertEqual(self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'failed', '0.2'), result)
        with self.assertRaises(ProductError) as e:
            self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'failed', '0')
        self.assertEqual(e.exception.code, 'settlement_conflict')

    def test_cancel_inflight_keeps_bill_and_discards_result(self):
        job = self.create()
        claim = self.claim()
        canceled = self.jobs.cancel(self.tenant, job['id'])
        self.assertEqual(canceled['status'], 'running')
        self.assertEqual(canceled['reservation_status'], 'held')
        result = self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'succeeded', '0.1', result_ref='not-published')
        self.assertEqual((result['status'], result['actual_cost'], result['result_ref']), ('canceled', '0.1', None))
        self.assertEqual(self.jobs.settle(self.tenant, job['id'], claim['lease_token'], 'succeeded', '0.1', result_ref='not-published'), result)

    def test_process_loss_does_not_retry_or_assume_free(self):
        job = self.create()
        claim = self.claim(lease_seconds=30)
        # A fresh service instance observes the durable lease and quarantine.
        restarted = CapabilityJobs(self.jobs.dsn, clock=lambda: self.now)
        self.now += timedelta(seconds=30)
        self.assertIsNone(self.claim())
        with self.assertRaises(ProductError) as e:
            restarted.settle(self.tenant, job['id'], claim['lease_token'], 'failed', '0')
        self.assertEqual(e.exception.code, 'stale_lease')
        self.assertEqual(restarted.quarantine_expired(), 1)
        self.assertEqual(restarted.quarantine_expired(), 0)
        row = restarted.get(self.tenant, job['id'])
        self.assertEqual((row['status'], row['actual_cost'], row['reservation_status']), ('reconciliation_required', None, 'held'))
        result = restarted.reconcile(self.tenant, job['id'], 'failed', '0.1', reason='fixture provider billing checked', error_code='source_timeout')
        self.assertEqual((result['status'], result['actual_cost']), ('failed', '0.1'))
        self.assertIsNone(self.claim())

    def test_utc_budget_windows_and_monthly_ceiling(self):
        self.jobs.configure_budget(self.tenant, 'USD', '0.4', '0.8')
        self.create()
        with self.assertRaises(ProductError):
            self.create('same-day')
        self.now += timedelta(days=1)
        self.create('next-day')
        self.now += timedelta(days=1)
        with self.assertRaises(ProductError):
            self.create('monthly-limit')
        self.now = self.now.replace(month=12, day=1)
        self.assertEqual(self.create('new-month')['status'], 'queued')

    def test_atomic_failure_leaves_no_reservation_or_event(self):
        self.grant['validate_parameters'] = lambda p: (_ for _ in ()).throw(ProductError('unsupported_parameter', 'fixture rejection'))
        with self.assertRaises(ProductError):
            self.create()
        self.assertEqual(self.jobs.list(self.tenant)['jobs'], [])
        with self.jobs.connect() as db:
            n = db.execute('SELECT count(*) n FROM sf_capability_job_events e JOIN sf_capability_jobs j ON j.id=e.job_id WHERE j.tenant_id=%s', (self.tenant,)).fetchone()['n']
        self.assertEqual(n, 0)

    def test_transaction_rollback_after_insert_and_operator_overrun_accounting(self):
        with patch.object(self.jobs, 'event', side_effect=RuntimeError('fixture disk failure')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(self.jobs.list(self.tenant)['jobs'], [])
        job = self.create()
        self.claim(lease_seconds=1)
        self.now += timedelta(seconds=2)
        self.jobs.quarantine_expired()
        # Explicit operator reconciliation records the true bill even when the
        # provider violated its approved upper bound; future budgets see it.
        result = self.jobs.reconcile(self.tenant, job['id'], 'failed', '1.20', reason='fixture provider overrun independently confirmed')
        self.assertEqual(result['actual_cost'], '1.20')
        with self.assertRaises(ProductError) as e:
            self.create('blocked-by-real-bill')
        self.assertEqual(e.exception.code, 'budget_exceeded')

    def test_same_key_different_currency_is_conflict_not_new_charge(self):
        self.jobs.configure_budget(self.tenant, 'EUR', '1', '2')
        self.create()
        with self.assertRaises(ProductError) as e:
            self.create(currency='EUR', grant={**self.grant, 'currency': 'EUR'})
        self.assertEqual(e.exception.code, 'idempotency_conflict')
        self.assertEqual(len(self.jobs.list(self.tenant)['jobs']), 1)
