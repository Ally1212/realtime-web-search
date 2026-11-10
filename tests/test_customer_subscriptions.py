import csv
import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from realtime.customer_subscriptions import DataSubscriptions, checksum, export_batch
from realtime.product import ProductError
from tests import test_customer_data_api as data_tests


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class SubscriptionTests(unittest.TestCase):
    tearDown = data_tests.CustomerDataDatabaseTests.tearDown
    allow_more = data_tests.CustomerDataDatabaseTests.allow_more
    record = data_tests.CustomerDataDatabaseTests.record
    def setUp(self):
        data_tests.CustomerDataDatabaseTests.setUp(self)
        self.allow_more()
        self.subs = DataSubscriptions(self.store, self.owner['id'])

    def subscription(self, mode='occurrences', **filters):
        return self.subs.create(dict(name='Saved data', mode=mode, filters=filters), 'sub-' + mode)

    def test_paused_idempotent_create_and_account_isolation(self):
        sub = self.subscription()
        self.assertFalse(sub['active'])
        self.assertEqual(sub['id'], self.subscription()['id'])
        with self.assertRaises(ProductError):
            self.subs.create(dict(name='Changed'), 'sub-occurrences')
        with self.assertRaises(ProductError):
            self.subs.batch(sub['id'])
        other = DataSubscriptions(self.store, self.other['account_id'])
        self.assertEqual(other.list()['subscriptions'], [])
        with self.assertRaises(ProductError):
            other.action(sub['id'], 'resume')
        self.assertEqual(len(self.subs.list()['subscriptions']), 1)

    def test_retry_ack_late_commit_pause_and_exports(self):
        self.record(query='first')
        self.record(query='second')
        self.record(query='public', private=False)
        sub = self.subscription()
        sid = sub['id']
        self.subs.action(sid, 'resume')
        with ThreadPoolExecutor(2) as pool:
            batches = list(pool.map(lambda _: self.subs.batch(sid, limit=1), range(2)))
        first = batches[0]
        self.assertEqual(first, batches[1])
        self.assertEqual(checksum(first['payload']), first['sha256'])
        self.assertEqual(first['payload']['request_count'], 1)
        self.subs.action(sid, 'pause')
        self.assertEqual(first, self.subs.batch(sid))
        with self.assertRaises(ProductError):
            self.subs.acknowledge(sid, first['batch_id'], 'bad')
        for fmt in ('csv', 'jsonl'):
            handler = Mock()
            export_batch(handler, first, fmt)
            raw = handler.send_body.call_args.args[0].decode('utf-8-sig')
            exported = json.loads(next(csv.DictReader(io.StringIO(raw)))['record_json']) if fmt == 'csv' else json.loads(raw)['request']
            self.assertEqual(exported, first['payload']['requests'][0])
        handler = Mock()
        export_batch(handler, first, 'manifest')
        manifest = json.loads(handler.send_body.call_args.args[0])
        from realtime.customer_subscriptions import batch_file
        import hashlib
        for kind in ('jsonl', 'csv'):
            content, _ = batch_file(first, kind)
            self.assertEqual(manifest['files'][kind], {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
        ack = self.subs.acknowledge(sid, first['batch_id'], first['sha256'])
        self.assertEqual(ack, self.subs.acknowledge(sid, first['batch_id'], first['sha256']))
        self.subs.action(sid, 'resume')
        second = self.subs.batch(sid)
        self.assertNotEqual(first['batch_id'], second['batch_id'])
        self.subs.acknowledge(sid, second['batch_id'], second['sha256'])
        self.assertEqual(self.subs.batch(sid)['reason'], 'interval_not_elapsed')
        # A late commit with an old timestamp must remain discoverable.
        _, _, late = self.record(query='late')
        with self.base.connect() as db:
            db.execute("UPDATE serp_requests SET completed_at='2020-01-01' WHERE request_id=%s", (late,))
            db.execute('UPDATE sf_data_subscriptions SET next_poll_at=NULL WHERE id=%s', (sid,))
        third = self.subs.batch(sid)
        self.assertEqual([r['request_id'] for r in third['payload']['requests']], [late])
        self.assertEqual(self.subs.batch(sid, bid=first['batch_id'])['payload'], first['payload'])

    def test_start_now_excludes_existing_but_keeps_late_commits(self):
        self.record(query='old')
        sub = self.subs.create(dict(name='Only future', start_mode='now'), 'future')
        self.assertEqual(sub['start_mode'], 'now')
        self.assertEqual(self.subs.status(sub['id'])['remaining_requests'], 0)
        _, _, late = self.record(query='late-new')
        with self.base.connect() as db:
            db.execute("UPDATE serp_requests SET completed_at='2020-01-01' WHERE request_id=%s", (late,))
        self.subs.action(sub['id'], 'resume')
        batch = self.subs.batch(sub['id'])
        self.assertEqual([r['request_id'] for r in batch['payload']['requests']], [late])
        self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256'])
        status = self.subs.status(sub['id'])
        self.assertEqual(status['remaining_requests'], 0)
        self.assertEqual(status['delivery']['acknowledged_requests'], 1)
        self.assertEqual(status['delivery']['acknowledged_batches'], 1)
        self.assertIsNotNone(status['delivery']['last_acknowledged_at'])
        self.assertIsNone(status['pending_batch'])
        self.assertEqual(self.subs.create(dict(name='Only future', start_mode='now'), 'future')['id'], sub['id'])

    def test_delivery_progress_history_paging_and_foreign_account(self):
        for i in range(3):
            self.record(query='history-' + str(i))
        sub = self.subscription()
        sid = sub['id']
        self.assertEqual(self.subs.status(sid)['remaining_requests'], 3)
        self.subs.action(sid, 'resume')
        batches = []
        for _ in range(3):
            batch = self.subs.batch(sid, limit=1)
            self.assertEqual(self.subs.status(sid)['pending_batch']['request_count'], 1)
            batches.append(batch['batch_id'])
            self.subs.acknowledge(sid, batch['batch_id'], batch['sha256'])
        first = self.subs.history(sid, {'limit': 2})
        second = self.subs.history(sid, {'limit': 2, 'cursor': first['next_cursor']})
        self.assertEqual([str(x['id']) for x in first['batches'] + second['batches']], list(reversed(batches)))
        self.assertFalse(second['has_more'])
        self.assertTrue(all(x['state'] == 'acknowledged' and x['payload_bytes'] > 0 for x in first['batches']))
        self.assertEqual(self.subs.list()['subscriptions'][0]['last_acknowledged_at'], self.subs.status(sid)['delivery']['last_acknowledged_at'])
        other = DataSubscriptions(self.store, self.other['account_id'])
        for operation in (lambda: other.status(sid), lambda: other.history(sid, {})):
            with self.assertRaises(ProductError): operation()
        with self.assertRaises(Exception) as error: self.subs.history(sid, {'cursor': 'broken'})
        self.assertEqual(error.exception.status, 400)
        with self.assertRaises(ProductError): self.subs.create(dict(name='x', start_mode='wrong'), 'bad-start')
        with self.assertRaises(ProductError): self.subs.create(dict(name='x', interval_seconds=60.5), 'bad-interval')

    def test_bulk_duplicate_urls_and_rollback_on_failed_ack(self):
        from unittest.mock import patch
        response = {'organic_results': [dict(title='Title', link='https://example.com/bulk/' + str(i)) for i in range(250)]}
        for i in range(3): self.record(query='bulk-' + str(i), response=response)
        sub = self.subscription('new_urls')
        self.subs.action(sub['id'], 'resume')
        batch = self.subs.batch(sub['id'])
        self.assertEqual(batch['payload']['request_count'], 3)
        self.assertEqual(batch['payload']['event_count'], 250)
        self.assertEqual(checksum(batch['payload']), batch['sha256'])
        with self.assertRaises(ProductError): self.subs.acknowledge(sub['id'], batch['batch_id'], 'bad')
        self.assertEqual(self.subs.status(sub['id'])['remaining_requests'], 3)
        self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256'])
        self.assertEqual(self.subs.status(sub['id'])['remaining_requests'], 0)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_data_seen_urls WHERE subscription_id=%s', (sub['id'],)).fetchone()['n'], 250)

    def test_large_delivery_splits_batches_and_keeps_all_occurrence_events(self):
        import time
        from pathlib import Path
        response = {'organic_results': [dict(title='Synthetic fixture', link='https://example.com/scale/' + str(i)) for i in range(500)]}
        for i in range(20):
            self.record(query='synthetic-scale-' + str(i), response=response, private=False)
        sub = self.subs.create(dict(name='Synthetic scale', filters={'scope': 'public'}, mode='occurrences'), 'scale')
        sid = sub['id']
        self.subs.action(sid, 'resume')
        started = time.perf_counter()
        requests, events, seen, batches, maximum_bytes = 0, 0, set(), 0, 0
        while True:
            batch = self.subs.batch(sid, limit=20)
            self.assertEqual(checksum(batch['payload']), batch['sha256'])
            self.assertEqual(self.subs.batch(sid)['batch_id'], batch['batch_id'])
            current = {e['event_id'] for e in batch['payload']['events']}
            self.assertFalse(seen & current)
            seen |= current
            requests += batch['payload']['request_count']
            events += batch['payload']['event_count']
            batches += 1
            maximum_bytes = max(maximum_bytes, len(json.dumps(batch['payload'], ensure_ascii=False).encode()))
            self.subs.acknowledge(sid, batch['batch_id'], batch['sha256'])
            if not batch['payload']['has_more']: break
        elapsed = time.perf_counter() - started
        self.assertEqual((requests, events, len(seen)), (20, 10000, 10000))
        self.assertGreater(batches, 1)
        self.assertEqual(self.subs.status(sid)['remaining_requests'], 0)
        evidence = os.getenv('SEARCHFLOW_SCALE_EVIDENCE')
        if evidence:
            Path(evidence).write_text(json.dumps(dict(fixture='isolated synthetic; not production throughput', requests=requests, events=events,
                                                   unique_event_ids=len(seen), batches=batches, seconds=elapsed,
                                                   maximum_serialized_bytes=maximum_bytes), indent=2))

    def test_http_subscription_lifecycle_and_method_guards(self):
        from realtime.customer_subscriptions import handle
        self.record(query='http')
        handler = Mock()
        handler.headers = {'Content-Length': '27', 'Content-Type': 'application/json', 'Idempotency-Key': 'http-sub'}
        raw = json.dumps({'name': 'HTTP test'}).encode()
        handler.headers['Content-Length'] = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        self.assertTrue(handle(handler, 'POST', '/v1/data/subscriptions', {}, self.store, self.owner))
        sid = handler.send_json.call_args.args[0]['id']
        def post(path, payload):
            value = json.dumps(payload).encode()
            handler.headers['Content-Length'] = str(len(value))
            handler.rfile = io.BytesIO(value)
            handle(handler, 'POST', '/v1/data/subscriptions/' + str(sid) + path, {}, self.store, self.owner)
            return handler.send_json.call_args.args[0]
        post('/resume', {})
        batch = post('/pull', {})
        self.assertEqual(batch['payload']['request_count'], 1)
        self.assertTrue(post('/batches/' + batch['batch_id'] + '/ack', {'sha256': batch['sha256']})['acknowledged'])
        self.assertFalse(post('/pause', {})['active'])
        with self.assertRaises(ProductError):
            handle(handler, 'GET', '/v1/data/subscriptions/' + str(sid) + '/pull', {}, self.store, self.owner)

    def test_news_backfill_is_additive_resumable_and_pauseable(self):
        import base64
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from realtime.news_backfill import run
        target = b'https://publisher.example/article'
        wrapper = 'https://news.google.com/rss/articles/' + base64.urlsafe_b64encode(b'\x08\x13\x22' + bytes([len(target)]) + target).decode().rstrip('=')
        _, _, rid = self.record(source='google_news', query='backfill', private=False, response={'news_results': [{'title': 'News', 'link': wrapper}]})
        before = self.subs.store.base
        from realtime.customer_data import CustomerDataStore
        data = CustomerDataStore(self.store, self.owner['id'])
        original = data.request(rid)
        result = run(before, apply=False)
        self.assertEqual(result['resolved'], 1)
        with before.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_news_link_enrichments').fetchone()['n'], 0)
        result = run(before, apply=True)
        self.assertEqual(result['resolved'], 1)
        self.assertEqual(run(before, apply=True)['outcomes'], [])
        self.assertEqual(data.request(rid), original)
        with TemporaryDirectory() as temp:
            pause = Path(temp) / 'pause'
            pause.touch()
            self.assertTrue(run(before, pause_file=pause)['stopped'])

    def test_backfill_deferred_does_not_starve_and_failed_outcomes_can_retry(self):
        from realtime.news_backfill import run
        from unittest.mock import patch
        wrapper = 'https://news.google.com/rss/articles/ARTICLE_TOKEN_12345'
        for q in ('deferred-one', 'deferred-two'):
            self.record(source='google_news', query=q, private=False, response={'news_results': [{'title': q, 'link': wrapper}]})
        self.record(source='google_news', query='private', response={'news_results': [{'title': 'Private', 'link': wrapper}]})
        base = self.store.base
        first = run(base, max_requests=1, max_links=1, apply=True)
        self.assertEqual(first['deferred'], 1)
        second = run(base, max_requests=1, max_links=1, apply=True)
        self.assertEqual(second['deferred'], 1)
        self.assertNotEqual(first['outcomes'][0]['request_id'], second['outcomes'][0]['request_id'])
        self.assertEqual(run(base, apply=True)['attempted'], 0)
        with base.connect() as db:
            db.execute("UPDATE sf_news_link_enrichments SET next_retry_at=now()-interval '1 second'")
            db.execute("UPDATE sf_news_backfill_progress SET next_retry_at=now()-interval '1 second'")
        with patch('realtime.google_news_links.NewsLinkResolver') as adapter:
            adapter.return_value.resolve.return_value = dict(status='unresolved', target=None, reason='google_http_500', http_request_count=1)
            result = run(base, Mock(), max_links=1, apply=True, retry_unresolved=True)
            self.assertEqual(result['unresolved'], 1)
            self.assertEqual(result['outcomes'][0]['attempts'], 2)
            adapter.return_value.resolve.assert_called_once()
        with base.connect() as db:
            db.execute("UPDATE sf_news_link_enrichments SET next_retry_at=now()-interval '1 second'")
            db.execute("UPDATE sf_news_backfill_progress SET next_retry_at=now()-interval '1 second'")
        with patch('realtime.google_news_links.NewsLinkResolver') as adapter:
            adapter.return_value.resolve.return_value = dict(status='resolved', target='https://publisher.example/real', http_request_count=2)
            result = run(base, Mock(), max_links=1, apply=True, retry_unresolved=True)
            self.assertEqual(result['resolved'], 1)
            self.assertEqual(result['http_requests'], 2)
        with self.assertRaises(ValueError): run(base, Mock(), apply=False)

    def test_integrity_probe_reads_acknowledged_batch_without_modifying_it(self):
        from scripts.watch_data_delivery import probe
        self.record(query='soak')
        sub = self.subscription()
        self.subs.action(sub['id'], 'resume')
        batch = self.subs.batch(sub['id'])
        self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256'])
        one = probe(self.store, sub['id'], batch['batch_id'])
        two = probe(self.store, sub['id'], batch['batch_id'])
        self.assertEqual(one, two)
        self.assertEqual(one['request_count'], 1)
        self.assertEqual(one['batch_sha256'], batch['sha256'])
        self.assertEqual(self.subs.status(sub['id'])['remaining_requests'], 0)

    def test_unique_urls_and_full_context_are_separate(self):
        for query in ('one', 'two'):
            self.record(query=query, response={'organic_results': [dict(title='Same', link='https://example.com/same', number=1e100)]})
        sub = self.subscription('new_urls')
        sid = sub['id']
        self.subs.action(sid, 'resume')
        first = self.subs.batch(sid, limit=1)
        self.assertEqual(first['payload']['event_count'], 1)
        self.subs.acknowledge(sid, first['batch_id'], first['sha256'])
        second = self.subs.batch(sid)
        self.assertEqual(second['payload']['event_count'], 0)
        self.assertEqual(second['payload']['request_count'], 1)
        self.assertEqual(len(second['payload']['requests'][0]['occurrences']), 1)

    def test_public_to_private_revokes_frozen_batch(self):
        _, cid, _ = self.record(query='was-public', private=False)
        sub = self.subscription(scope='public')
        self.subs.action(sub['id'], 'resume')
        batch = self.subs.batch(sub['id'])
        with self.base.connect() as db:
            db.execute("UPDATE serp_task_options SET options=options || '{\"customer_private\":true}'::jsonb WHERE campaign_id=%s", (cid,))
        with self.assertRaises(ProductError):
            self.subs.batch(sub['id'], bid=batch['batch_id'])
        with self.assertRaises(ProductError):
            self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256'])
        self.assertFalse(self.subs.discard(sub['id'], batch['batch_id'])['progress_advanced'])
        self.assertIsNone(self.subs.batch(sub['id'])['batch_id'])


class AuditFixTests(unittest.TestCase):
    def test_rankings_explicit_formats_strip_transport_parameter(self):
        from realtime.customer_http import handle
        for fmt in ('json', 'csv'):
            store, handler = Mock(), Mock()
            store.history_analysis.return_value = {'samples': 0, 'timeline': []}
            handle(handler, 'GET', '/v1/analytics/rankings', {'format': [fmt], 'query': ['test']}, store, {'id': 'id'})
            store.history_analysis.assert_called_once_with(query='test')
            (handler.send_json if fmt == 'json' else handler.send_body).assert_called_once()

    def test_catalog_status_country_and_examples(self):
        from realtime.customer_data import field_catalog, selection
        from realtime.product_ui import JS
        self.assertTrue({'image_url', 'source_page_url', 'thumbnail_url', 'width', 'height', 'upstream_rank', 'observed_order', 'rank_basis', 'media_downloaded'} <= set(field_catalog()['occurrence_fields']))
        upper = selection({'country': 'US', 'classification': 'service_error'}, '00000000-0000-0000-0000-000000000001')
        lower = selection({'country': 'us', 'classification': 'service_error'}, '00000000-0000-0000-0000-000000000001')
        self.assertEqual(upper, lower)
        self.assertEqual(JS.count("-H 'X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY'"), 2)
