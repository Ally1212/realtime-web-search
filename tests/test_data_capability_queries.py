import copy
import os
import unittest
from datetime import timedelta

from realtime.data_capability_queries import CapabilityRuntime, Cursors, decode_object
from realtime.product import ProductError
from tests import test_data_capability_store as store_tests
from tests.test_data_capability_facts import parameters


class CursorTests(unittest.TestCase):
    def test_tenant_filter_route_expiry_and_tamper_binding(self):
        from datetime import datetime, timezone
        now = [datetime(2026, 11, 10, tzinfo=timezone.utc)]
        cursors = Cursors(b'fixture-cursor-secret-not-production' * 2, lambda: now[0])
        token = cursors.encode('one', 'route', {'country': 'US'}, ['key'])
        self.assertEqual(cursors.decode(token, 'one', 'route', {'country': 'US'}), ['key'])
        for value, tenant, route, filters in ((token, 'two', 'route', {'country': 'US'}),
                                              (token, 'one', 'other', {'country': 'US'}),
                                              (token, 'one', 'route', {'country': 'GB'}),
                                              (token[:-1] + ('1' if token[-1] != '1' else '2'), 'one', 'route', {'country': 'US'})):
            with self.subTest(value=value), self.assertRaises(ProductError) as e:
                cursors.decode(value, tenant, route, filters)
            self.assertEqual(e.exception.code, 'invalid_cursor')
        now[0] += timedelta(hours=1)
        with self.assertRaises(ProductError):
            cursors.decode(token, 'one', 'route', {'country': 'US'})


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CapabilityQueryTests(unittest.TestCase):
    tearDown = store_tests.CapabilityStoreTests.tearDown
    start = store_tests.CapabilityStoreTests.start
    finish = store_tests.CapabilityStoreTests.finish
    counts = store_tests.CapabilityStoreTests.counts

    def setUp(self):
        store_tests.CapabilityStoreTests.setUp(self)
        self.ready = True
        self.runtime = CapabilityRuntime(self.data, self.grant_for,
                                         cursor_secret=b'fixture-cursor-secret-not-production' * 2,
                                         worker_ready=lambda t, c, p: self.ready)

    def grant_for(self, tenant, cap):
        return self.grants.get(cap) if tenant == self.tenant else None

    def test_submission_gate_and_original_idempotent_task_without_executor(self):
        p = dict(parameters(), budget=dict(maximum_cost='0.5', currency='USD'))
        self.ready = False
        with self.assertRaises(ProductError) as e:
            self.runtime.submit(self.tenant, p, 'key')
        self.assertEqual(e.exception.code, 'executor_unavailable')
        self.assertEqual(self.data.list(self.tenant)['jobs'], [])
        self.ready = True
        accepted = self.runtime.submit(self.tenant, p, 'key')
        self.ready = False
        self.grants['trends_history']['maximum_cost'] = '1.0'
        self.assertEqual(self.runtime.submit(self.tenant, p, 'key')['id'], accepted['id'])
        with self.assertRaises(ProductError) as e:
            self.runtime.submit(self.tenant, {**p, 'terms': ['different']}, 'key')
        self.assertEqual(e.exception.code, 'idempotency_conflict')
        self.assertIsNone(self.data.get(self.tenant, accepted['id'])['actual_cost'])
        self.grants['trends_history']['status'] = 'revoked'
        with self.assertRaises(ProductError):
            self.runtime.submit(self.tenant, p, 'key')

    def test_owned_job_listing_cursor_recovery_and_no_fake_progress(self):
        jobs = [self.finish(self.start()) for _ in range(3)]
        seen = []
        p = {'limit': '1', 'country': 'us'}
        while True:
            page = self.runtime.jobs(self.tenant, p)
            seen.extend(r['id'] for r in page['jobs'])
            if not page['has_more']:
                break
            p['cursor'] = page['next_cursor']
        self.assertEqual(set(seen), {j['id'] for j in jobs})
        self.assertEqual(len(seen), 3)
        self.assertEqual(self.runtime.jobs('other-tenant', {})['jobs'], [])
        progress = self.runtime.progress(self.tenant, jobs[0]['id'])
        self.assertTrue(progress['completed'])
        self.assertNotIn('percent', progress)
        self.assertEqual(progress['platform_delivery'], 'not_submitted')
        with self.assertRaises(ProductError):
            self.runtime.jobs(self.tenant, {**p, 'country': 'GB'})
        p = dict(parameters(), budget=dict(maximum_cost='0.5', currency='USD'))
        queued = self.runtime.submit(self.tenant, p, 'timeout')
        self.now += timedelta(seconds=300)
        expired = self.runtime.job(self.tenant, queued['id'])
        self.assertEqual((expired['status'], expired['error_code'], expired['actual_cost']), ('failed', 'queue_timeout', '0'))

    def test_observation_and_typed_parent_pagination_preserves_every_record(self):
        for _ in range(3):
            self.finish(self.start())
        page = self.runtime.observations(self.tenant, {'limit': '1'})
        self.assertEqual(len(page['records']), 1)
        self.assertNotIn('payload', page['records'][0])
        self.assertTrue(page['has_more'])
        next_page = self.runtime.observations(self.tenant, {'limit': '1', 'cursor': page['next_cursor']})
        self.assertNotEqual(page['records'][0]['observation_id'], next_page['records'][0]['observation_id'])
        typed = self.runtime.observations(self.tenant, {'limit': '1'}, kind='series')
        oid = typed['records'][0]['object_id']
        parent = self.runtime.typed(self.tenant, oid, 'series')
        first = self.runtime.children(self.tenant, oid, 'point', {'limit': '1'})
        last = self.runtime.children(self.tenant, oid, 'point', {'limit': '1', 'cursor': first['next_cursor']})
        self.assertEqual(first['records'] + last['records'], parent['record']['points'])
        self.assertFalse(last['has_more'])
        self.assertFalse(typed['records'][0]['includes_children'])
        self.assertNotIn('points', typed['records'][0]['record'])
        with self.assertRaises(ProductError):
            self.runtime.observations(self.tenant, {'limit': '1', 'cursor': typed['next_cursor']})
        with self.assertRaises(ProductError):
            self.runtime.children('other-tenant', oid, 'point', {})
        with self.assertRaises(ProductError):
            decode_object(oid, 'answer')

    def test_multiple_roots_in_same_observation_do_not_disappear_at_page_boundary(self):
        bundle = self.start('keyword_volume')
        group = copy.deepcopy(bundle[2]['data']['groups'][0])
        group['id'] = 'second-group'
        group['returned_text'] = '苹果'
        bundle[2]['data']['groups'].append(group)
        bundle[2]['data']['input_mapping'][0].update(record_id=group['id'], status='available', reason=None)
        self.finish(bundle)
        first = self.runtime.observations(self.tenant, {'limit': '1'}, kind='volume')
        last = self.runtime.observations(self.tenant, {'limit': '1', 'cursor': first['next_cursor']}, kind='volume')
        self.assertEqual(first['records'][0]['record']['id'], 'group-fixture')
        self.assertEqual(last['records'][0]['record']['id'], 'second-group')
        self.assertFalse(last['has_more'])
        with self.assertRaises(ProductError):
            self.runtime.observations(self.tenant, {'limit': '1', 'cursor': first['next_cursor'], 'country': 'GB'}, kind='volume')

    def test_revoked_license_is_filtered_without_leaking_count_or_cursor(self):
        bundle = self.start()
        done = self.finish(bundle)
        self.grants['trends_history']['license_id'] = 'different-license'
        for page in (self.runtime.observations(self.tenant, {}), self.runtime.observations(self.tenant, {}, kind='series'), self.runtime.jobs(self.tenant, {})):
            records = page.get('records', page.get('jobs'))
            self.assertEqual(records, [])
            self.assertFalse(page['has_more'])
            self.assertIsNone(page['next_cursor'])
        with self.assertRaises(ProductError):
            self.runtime.observation(self.tenant, done['result_ref'])

    def test_root_listing_uses_authoritative_facts_when_projection_is_missing(self):
        bundle = self.start()
        done = self.finish(bundle)
        with self.data.connect() as db:
            db.execute("DELETE FROM sf_capability_fact_rows WHERE observation_id=%s AND kind='series'", (done['result_ref'],))
        roots = self.runtime.observations(self.tenant, {}, kind='series')
        self.assertEqual(len(roots['records']), 1)
        self.assertEqual(roots['records'][0]['record']['id'], 'series-fixture')
