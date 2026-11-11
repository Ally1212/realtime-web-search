import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from realtime.data_capability_store import CapabilityData
from realtime.data_capability_export import export_job
from realtime.product import ProductError
from tests.test_data_capability_facts import parameters, result


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CapabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        self.data = CapabilityData(os.environ['TEST_DATABASE_URL'], clock=lambda: self.now)
        self.data.initialize()
        self.tenant = 'facts-' + str(uuid4())
        self.data.configure_budget(self.tenant, 'USD', '100', '100')
        self.grants = {cap: dict(capability=cap, status='approved', provider=self.tenant,
                                license_id='fixture-only', allowed_use='isolated_test', retention_days=1,
                                billing_unit='fixture-call', currency='USD', maximum_cost='0.40', raw_response_retention_allowed=True,
                                expires_at='2027-01-01T00:00:00Z', validate_parameters=lambda p: None)
                       for cap in ('trends_history', 'keyword_volume', 'aio_answer', 'ai_mode_answer')}

    def tearDown(self):
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_fact_rows WHERE observation_id IN (SELECT id FROM sf_capability_observations WHERE tenant_id=%s)', (self.tenant,))
            db.execute('DELETE FROM sf_capability_job_observations WHERE job_id IN (SELECT id FROM sf_capability_jobs WHERE tenant_id=%s)', (self.tenant,))
            db.execute('DELETE FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,))
            db.execute('DELETE FROM sf_capability_job_events WHERE job_id IN (SELECT id FROM sf_capability_jobs WHERE tenant_id=%s)', (self.tenant,))
            db.execute('DELETE FROM sf_capability_jobs WHERE tenant_id=%s', (self.tenant,))
            db.execute('DELETE FROM sf_capability_budgets WHERE tenant_id=%s', (self.tenant,))

    def start(self, capability='trends_history', key=None):
        p = parameters(capability)
        grant = self.grants[capability]
        job = self.data.create(self.tenant, p, key or str(uuid4()), grant, '0.5', 'USD')
        claim = self.data.claim(capability, self.tenant, lambda t, c: self.grants[c])
        self.assertEqual(claim['job']['id'], job['id'])
        return job, claim, result(p, self.tenant), grant

    def finish(self, bundle, **kwargs):
        job, claim, value, grant = bundle
        return self.data.complete(self.tenant, job['id'], claim['lease_token'], value, '0.1', grant,
                                  event_key=kwargs.pop('event_key', job['id']), **kwargs)

    def counts(self):
        with self.data.connect() as db:
            n = db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s', (self.tenant,)).fetchone()['n']
            children = db.execute('SELECT count(*) n FROM sf_capability_fact_rows r JOIN sf_capability_observations o ON o.id=r.observation_id WHERE o.tenant_id=%s', (self.tenant,)).fetchone()['n']
        return n, children

    def test_raw_permission_is_current_and_required_for_save_and_read(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                bundle = self.start(cap)
                grant = bundle[3]
                grant.pop('raw_response_retention_allowed')
                with self.assertRaises(ProductError) as error:
                    self.finish(bundle)
                self.assertEqual(error.exception.code, 'raw_retention_not_admitted')
                grant['raw_response_retention_allowed'] = True
                done = self.finish(bundle)
                snapshot = self.data.job_data(self.tenant, done['id'], grant)['observation']
                grant['raw_response_retention_allowed'] = False
                alias = self.start(cap)
                for action in (
                    lambda: export_job(self.data, self.tenant, done['id'], grant),
                    lambda: self.data.job_data(self.tenant, done['id'], grant),
                    lambda: self.data.rebuild_projections(self.tenant, snapshot['observation_id'], grant),
                    lambda: self.data.reuse(self.tenant, alias[0]['id'], alias[1]['lease_token'], snapshot['observation_id'], '0', grant),
                ):
                    with self.assertRaises(ProductError) as denied:
                        action()
                    self.assertEqual(denied.exception.code, 'raw_retention_not_admitted')
                with self.assertRaises(ProductError) as error:
                    self.data.observation(self.tenant, snapshot['observation_id'], grant)
                self.assertEqual(error.exception.code, 'raw_retention_not_admitted')
                grant['raw_response_retention_allowed'] = True
                self.assertEqual(self.data.observation(self.tenant, snapshot['observation_id'], grant), snapshot)
                self.data.reuse(self.tenant, alias[0]['id'], alias[1]['lease_token'], snapshot['observation_id'], '0', grant)

    def test_normalized_only_fact_does_not_require_raw_permission(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                bundle = self.start(cap)
                bundle[3].pop('raw_response_retention_allowed')
                bundle[2]['raw'] = dict(status='not_retained', payload=None, redacted_paths=[], reason='license excludes raw')
                done = self.finish(bundle)
                saved = self.data.job_data(self.tenant, done['id'], bundle[3])
                self.assertEqual(saved['observation']['payload'], bundle[2])

    def test_all_domains_saved_exactly_with_paginated_children(self):
        expected = {'trends_history': (('series', '', 1), ('point', 'series-fixture', 2)),
                    'keyword_volume': (('volume', '', 1), ('month', 'group-fixture', 2)),
                    'aio_answer': (('answer', '', 1), ('block', 'answer-fixture', 1), ('citation', 'answer-fixture', 1), ('link', 'answer-fixture', 1)),
                    'ai_mode_answer': (('answer', '', 1), ('block', 'answer-fixture', 1), ('citation', 'answer-fixture', 1), ('link', 'answer-fixture', 1))}
        for cap, types in expected.items():
            with self.subTest(cap=cap):
                bundle = self.start(cap)
                done = self.finish(bundle)
                self.assertEqual(done['status'], 'succeeded')
                saved = self.data.job_data(self.tenant, done['id'], bundle[3])
                self.assertEqual(saved['observation']['payload'], bundle[2])
                self.assertIsNone(saved['cache_hit'])
                self.assertFalse(saved['local_cache_reuse'])
                for kind, parent, count in types:
                    page = self.data.records(self.tenant, done['result_ref'], kind, bundle[3], parent_id=parent, limit=1)
                    self.assertEqual(len(page['records']), 1)
                    self.assertEqual(page['has_more'], count > 1)
                    if count > 1:
                        last = self.data.records(self.tenant, done['result_ref'], kind, bundle[3], parent_id=parent, limit=1, offset=page['next_offset'])
                        self.assertFalse(last['has_more'])
                        self.assertNotEqual(last['records'], page['records'])

    def test_retry_same_event_new_equal_observation_and_revisions(self):
        bundle = self.start()
        with ThreadPoolExecutor(2) as pool:
            done = list(pool.map(lambda _: self.finish(bundle), range(2)))
        self.assertEqual(done[0], done[1])
        self.assertEqual(self.counts(), (1, 3))
        before = self.data.observation(self.tenant, done[0]['result_ref'], bundle[3])
        changed = copy.deepcopy(bundle)
        changed[2]['raw']['payload']['fixture'] = 'changed payload'
        with self.assertRaises(ProductError) as e:
            self.finish(changed)
        self.assertEqual(e.exception.code, 'event_conflict')
        fresh = self.start()
        same = self.finish(fresh)
        second = self.data.observation(self.tenant, same['result_ref'], fresh[3])
        self.assertNotEqual(before['observation_id'], second['observation_id'])
        self.assertEqual(before['payload_hash'], second['payload_hash'])
        self.assertEqual(before['entity_id'], second['entity_id'])
        revised = self.start()
        revised[2]['data']['series'][0]['points'][0]['metric'].update(value='5', status='available')
        revised[2]['data']['series'][0]['revision'] = 'r2'
        rev = self.finish(revised, revision='r2', supersedes=before['observation_id'])
        saved = self.data.observation(self.tenant, rev['result_ref'], revised[3])
        self.assertEqual(saved['supersedes'], before['observation_id'])
        self.assertEqual(self.data.observation(self.tenant, before['observation_id'], bundle[3]), before)
        self.assertEqual(self.counts()[0], 3)

    def test_cross_scale_revision_and_event_job_collision_rejected(self):
        original_bundle = self.start()
        original = self.finish(original_bundle)
        other = self.start()
        other[2]['data']['series'][0]['scale_id'] = 'unrelated-scale'
        with self.assertRaises(ProductError) as e:
            self.finish(other, revision='r2', supersedes=original['result_ref'])
        self.assertEqual(e.exception.code, 'invalid_revision')
        with self.assertRaises(ProductError) as e:
            self.finish(other, event_key=original['id'])
        self.assertEqual(e.exception.code, 'event_conflict')
        self.assertEqual(self.counts()[0], 1)
        self.assertEqual(self.data.get(self.tenant, other[0]['id'])['reservation_status'], 'held')

    def test_cache_reuse_keeps_original_observation_and_checks_scope(self):
        original = self.start()
        saved = self.finish(original)
        self.now += timedelta(seconds=10)
        cached = self.start()
        job, claim, _, grant = cached
        done = self.data.reuse(self.tenant, job['id'], claim['lease_token'], saved['result_ref'], '0', grant)
        same = self.data.reuse(self.tenant, job['id'], claim['lease_token'], saved['result_ref'], '0', grant)
        self.assertEqual(same, done)
        read = self.data.job_data(self.tenant, job['id'], grant)
        self.assertTrue(read['cache_hit'])
        self.assertEqual(read['cache_age_seconds'], '10')
        self.assertEqual(read['cache_age_basis'], 'received_at')
        self.assertEqual(read['observation']['payload'], original[2])
        self.assertEqual(self.counts()[0], 1)
        wrong = self.start('keyword_volume')
        with self.assertRaises(ProductError):
            self.data.reuse(self.tenant, wrong[0]['id'], wrong[1]['lease_token'], saved['result_ref'], '0', wrong[3])

    def test_current_rights_expiry_and_tenant_isolation(self):
        bundle = self.start()
        done = self.finish(bundle)
        oid = done['result_ref']
        for grant in (None, {**bundle[3], 'status': 'revoked'}, {**bundle[3], 'allowed_use': 'changed'}):
            with self.subTest(grant=grant), self.assertRaises(ProductError):
                self.data.observation(self.tenant, oid, grant)
        with self.assertRaises(ProductError) as e:
            self.data.observation('another-tenant', oid, bundle[3])
        self.assertEqual(e.exception.status, 404)
        self.now += timedelta(days=1)
        for read in (lambda: self.data.observation(self.tenant, oid, bundle[3]),
                     lambda: self.data.job_data(self.tenant, done['id'], bundle[3]),
                     lambda: self.data.records(self.tenant, oid, 'point', bundle[3], parent_id='series-fixture')):
            with self.assertRaises(ProductError) as e:
                read()
            self.assertEqual(e.exception.code, 'expired')

    def test_completion_rollback_and_cancel_discards_source_content(self):
        bundle = self.start()
        with patch.object(self.data, 'event', side_effect=RuntimeError('fixture commit failure')):
            with self.assertRaises(RuntimeError):
                self.finish(bundle)
        self.assertEqual(self.counts(), (0, 0))
        running = self.data.get(self.tenant, bundle[0]['id'])
        self.assertEqual((running['status'], running['actual_cost']), ('running', None))
        done = self.finish(bundle)
        self.assertEqual(self.counts(), (1, 3))
        canceled = self.start()
        self.data.cancel(self.tenant, canceled[0]['id'])
        canceled[2]['raw']['payload'] = {'token': 'discard-not-store'}
        result = self.finish(canceled)
        self.assertEqual((result['status'], result['actual_cost'], result['result_ref']), ('canceled', '0.1', None))
        self.assertEqual(self.counts(), (1, 3))
        self.assertEqual(self.finish(canceled), result)
        self.assertEqual(self.data.get(self.tenant, done['id'])['status'], 'succeeded')

    def test_partial_cannot_settle_as_complete_and_stale_lease_has_no_saved_facts(self):
        bundle = self.start('aio_answer')
        bundle[2]['is_partial'] = True
        bundle[2]['data']['answer']['state'] = 'partial'
        bundle[2]['data']['answer']['expansion']['state'] = 'expired'
        partial = self.finish(bundle)
        self.assertEqual(partial['status'], 'partial')
        fresh = self.start()
        self.now += timedelta(seconds=301)
        with self.assertRaises(ProductError) as e:
            self.finish(fresh)
        self.assertEqual(e.exception.code, 'stale_lease')
        self.assertEqual(self.counts()[0], 1)

    def test_complete_requires_claim_and_current_admission(self):
        p = parameters()
        grant = self.grants['trends_history']
        job = self.data.create(self.tenant, p, 'not-claimed', grant, '0.5', 'USD')
        with self.assertRaises(ProductError) as e:
            self.data.complete(self.tenant, job['id'], 'no-lease', result(p, self.tenant), '0', grant, event_key='not-claimed')
        self.assertEqual(e.exception.code, 'stale_lease')
        claim = self.data.claim('trends_history', self.tenant, lambda t, c: grant)
        with self.assertRaises(ProductError):
            self.data.complete(self.tenant, job['id'], claim['lease_token'], result(p, self.tenant), '0',
                               {**grant, 'status': 'revoked'}, event_key='not-claimed')
        self.assertEqual(self.counts(), (0, 0))

    def test_concurrent_cross_job_event_identity_is_a_structured_conflict(self):
        first, second = self.start(), self.start()
        def complete(bundle):
            try:
                return self.finish(bundle, event_key='one-event-across-two-jobs')
            except ProductError as exc:
                return exc.code
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(complete, (first, second)))
        self.assertEqual(sum(isinstance(v, dict) for v in outcomes), 1)
        self.assertEqual(outcomes.count('event_conflict'), 1)
        self.assertEqual(self.counts(), (1, 3))

    def test_cache_age_does_not_fabricate_zero_for_future_source_clock(self):
        original = self.start()
        original[2]['source'].update(source_observed_at='2026-11-10T12:00:30Z', unknown_time_reason=None)
        done = self.finish(original)
        cached = self.start()
        self.data.reuse(self.tenant, cached[0]['id'], cached[1]['lease_token'], done['result_ref'], '0', cached[3])
        read = self.data.job_data(self.tenant, cached[0]['id'], cached[3])
        self.assertIsNone(read['cache_age_seconds'])
        self.assertEqual(read['cache_age_basis'], 'unknown_source_clock_ahead')

    def test_corrupt_child_projection_is_detected_and_rebuilt_from_fixed_facts(self):
        from psycopg.types.json import Jsonb
        bundle = self.start()
        done = self.finish(bundle)
        before = self.data.observation(self.tenant, done['result_ref'], bundle[3])
        with self.data.connect() as db:
            db.execute("UPDATE sf_capability_fact_rows SET payload=%s WHERE observation_id=%s AND kind='point' AND position=0",
                       (Jsonb({'corrupt': True}), done['result_ref']))
        with self.assertRaises(ProductError) as e:
            self.data.records(self.tenant, done['result_ref'], 'point', bundle[3], parent_id='series-fixture')
        self.assertEqual(e.exception.code, 'corrupt_projection')
        self.data.rebuild_projections(self.tenant, done['result_ref'], bundle[3])
        restored = self.data.records(self.tenant, done['result_ref'], 'point', bundle[3], parent_id='series-fixture')
        self.assertEqual(restored['records'], bundle[2]['data']['series'][0]['points'])
        self.assertEqual(self.data.observation(self.tenant, done['result_ref'], bundle[3]), before)

    def test_corrupt_authoritative_snapshot_cannot_be_read_or_used_for_repair(self):
        from psycopg.types.json import Jsonb
        bundle = self.start()
        done = self.finish(bundle)
        corrupt = copy.deepcopy(bundle[2])
        corrupt['raw']['payload']['fixture'] = 'corrupted source response'
        with self.data.connect() as db:
            db.execute('UPDATE sf_capability_observations SET payload=%s WHERE id=%s', (Jsonb(corrupt), done['result_ref']))
        for operation in (lambda: self.data.job_data(self.tenant, done['id'], bundle[3]),
                          lambda: self.data.rebuild_projections(self.tenant, done['result_ref'], bundle[3])):
            with self.assertRaises(ProductError) as e:
                operation()
            self.assertEqual(e.exception.code, 'corrupt_snapshot')
