"""Feed-specific lossless candidates; isolated checks never assert platform admission."""
import copy
import os
import json
import io
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock, patch

from realtime.google_domain import DomainError
from realtime.google_feed_delivery import (
    saved_feed_run_candidate, prepare_public_feed_run, validate_feed_payload)
from realtime.google_whale_readback import ReadbackTarget, WhaleReadbackClient
from tests.test_google_whale_readback import response
from tests import test_google_legacy as legacy_tests


class FeedDeliveryTests(unittest.TestCase):
    def row(self, source):
        news = {'link': 'https://news.google.com/rss/articles/opaque',
                'title': 'Real index title', 'snippet': 'Index summary',
                'publisher': 'Publisher', 'publisher_url': 'https://example.com',
                'date': '2026-11-05T00:00:00Z',
                'date_raw': 'Thu, 05 Nov 2026 00:00:00 GMT', 'feed_position': 1,
                'position': 9, 'unknown_provider_field': 'keep', 'api_key': 'never-deliver'}
        values = ({'news_results': [news, copy.deepcopy(news)]} if source == 'google_news'
                  else {'trending_searches': [
                      {'keyword': 'topic', 'approx_traffic': '20K+',
                       'articles': [news, copy.deepcopy(news)]},
                      {'keyword': 'topic without articles', 'approx_traffic': 'unknown', 'articles': []}]})
        return dict(request_id=str(uuid4()), source=source, query='original feed query', page=1,
                    classification='results', completed_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
                    response={'search_parameters': {'gl': 'us', 'hl': 'en', 'q': 'original feed query'},
                              **values})

    def test_news_preserves_time_publisher_unknowns_duplicates_without_organic_rank(self):
        row = self.row('google_news'); before = copy.deepcopy(row)
        candidate = saved_feed_run_candidate(row)
        self.assertEqual(row, before)
        self.assertEqual(candidate, saved_feed_run_candidate(row))
        self.assertEqual(candidate['admission_status'], 'candidate_not_approved')
        self.assertNotIn('content', candidate)
        self.assertNotIn('dataset_id', candidate)
        items = candidate['payload']['observation']['occurrences']
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0]['occurrence_id'], items[1]['occurrence_id'])
        self.assertEqual(items[0]['url']['url_id'], items[1]['url']['url_id'])
        for item in items:
            self.assertIsNone(item['organic_position'])
            self.assertIsNone(item['article_url'])
            self.assertEqual(item['resolution_status'], 'unresolved_wrapper')
            self.assertEqual(item['source_fields']['publisher'], 'Publisher')
            self.assertEqual(item['publication']['status'], 'verified_rss')
            self.assertFalse(item['publication']['publisher_page_verified'])
            self.assertEqual(item['source_fields']['unknown_provider_field'], 'keep')
        self.assertNotIn('never-deliver', str(candidate))
        other = saved_feed_run_candidate({**row, 'request_id': str(uuid4())})
        self.assertNotEqual(candidate['event_key'], other['event_key'])
        self.assertNotEqual(candidate['object_key'], other['object_key'])
        self.assertIsNone(candidate['payload']['original_source_observed_at'])
        self.assertEqual(candidate['payload']['source_response']['news_results'][0]['date_raw'],
                         row['response']['news_results'][0]['date_raw'])

    def test_trends_preserves_topics_and_reports_separately_without_numeric_series(self):
        candidate = saved_feed_run_candidate(self.row('google_trends'))
        observation = candidate['payload']['observation']
        self.assertEqual(len(observation['topics']), 2)
        self.assertEqual(len(observation['occurrences']), 2)
        self.assertEqual(observation['topics'][0]['approx_traffic_label'], '20K+')
        for topic in observation['topics']:
            self.assertIsNone(topic['numeric_trend_series'])
        for item in observation['occurrences']:
            self.assertEqual(item['parent_topic_id'], observation['topics'][0]['topic_observation_id'])
            self.assertIsNone(item['organic_position'])
        self.assertEqual(validate_feed_payload(candidate['payload'])['source'], 'google_trends')

    def test_empty_failure_malformed_records_and_unknown_times_are_not_discarded(self):
        for source, key in [('google_news', 'news_results'), ('google_trends', 'trending_searches')]:
            for status in ('empty', 'timeout'):
                row = self.row(source); row.update(classification=status, response={key: []})
                payload = saved_feed_run_candidate(row)['payload']
                self.assertEqual(payload['observation']['availability'],
                                 'true_zero' if status == 'empty' else 'source_error')
                self.assertEqual(payload['observation']['occurrences'], [])
                validate_feed_payload(payload)
            row = self.row(source); row['response'][key] = [None, 'unknown item']
            payload = saved_feed_run_candidate(row)['payload']
            self.assertEqual(len(payload['observation']['invalid_records']), 2)
            self.assertEqual(payload['source_response'][key], [None, 'unknown item'])
        row = self.row('google_news'); row['source'] = 'google_images'
        with self.assertRaises(DomainError): saved_feed_run_candidate(row)

    def test_validator_rejects_altered_order_parent_time_rank_hash_and_source(self):
        for source in ('google_news', 'google_trends'):
            candidate = saved_feed_run_candidate(self.row(source))
            original = candidate['payload']
            changes = [lambda p: p['observation']['occurrences'].reverse(),
                       lambda p: p['observation']['occurrences'][0].update(organic_position=1),
                       lambda p: p['observation'].update(source_observed_at='2026-11-06T00:00:00Z'),
                       lambda p: p['observation'].update(source_response_hash='0' * 64),
                       lambda p: p['observation'].update(source='google_web'),
                       lambda p: p.update(run_id=str(uuid4()))]
            if source == 'google_trends':
                changes += [lambda p: p['observation']['occurrences'][0].update(parent_topic_id='wrong'),
                            lambda p: p['observation']['topics'][0].update(numeric_trend_series=[1, 2])]
            for change in changes:
                bad = copy.deepcopy(original); change(bad)
                with self.subTest(source=source, change=change), self.assertRaises(DomainError):
                    validate_feed_payload(bad)

    def test_independent_pinned_feed_reader_is_bound_to_source_payload_and_candidate(self):
        for source, platform in [('google_news', 'google_news'), ('google_trends', 'google_trends')]:
            candidate = saved_feed_run_candidate(self.row(source))
            session = Mock(); client = WhaleReadbackClient('https://example.test', 'test-key', session=session)
            target = ReadbackTarget('doc-1', 'v-1', 'dataset', platform)
            document = {'document': {'id': 'doc-1', 'dataset_id': 'dataset',
                         'source_platform': platform, 'external_content_id': candidate['object_key']}}
            item = {'document_id': 'doc-1', 'version_id': 'v-1',
                    'version': {'document_id': 'doc-1', 'version_id': 'v-1',
                                'raw_payload_hash': candidate['payload_hash']},
                    'platform_payload': candidate['payload']}
            session.request.side_effect = [response(200, document), response(200, {'versions': [item]})]
            result = client.read_full_feed_run(target, expected_candidate=candidate)
            self.assertTrue(result['proof']['payload_roundtrip_verified'])
            self.assertFalse(result['proof']['run_item_schema_verified'])
            self.assertEqual(result['proof']['source'], source)
            self.assertEqual(result['proof']['occurrence_count'], 2)
            self.assertEqual(result['proof']['topic_count'], 0 if source == 'google_news' else 2)
            bad = copy.deepcopy(item); bad['platform_payload']['observation']['occurrences'].reverse()
            session.request.side_effect = [response(200, document), response(200, {'versions': [bad]})]
            with self.assertRaises(DomainError): client.read_full_feed_run(target, expected_candidate=candidate)
            # Current discovery metadata cannot replace a missing pinned payload.
            bad = {**item, 'platform_payload': None}
            document['document']['metadata'] = {'discovery': {'metadata': candidate['payload']}}
            session.request.side_effect = [response(200, document), response(200, {'versions': [bad]})]
            with self.assertRaises(DomainError): client.read_full_feed_run(target, expected_candidate=candidate)
            for change in ('wrong_source', 'wrong_object', 'wrong_hash'):
                bad_doc, bad_item = copy.deepcopy(document), copy.deepcopy(item)
                if change == 'wrong_source': bad_doc['document']['source_platform'] = 'google_search'
                if change == 'wrong_object': bad_doc['document']['external_content_id'] = 'url:merged'
                if change == 'wrong_hash': bad_item['version']['raw_payload_hash'] = 'sha256:' + 'a' * 64
                session.request.side_effect = [response(200, bad_doc), response(200, {'versions': [bad_item]})]
                with self.subTest(source=source, change=change), self.assertRaises(DomainError):
                    client.read_full_feed_run(target, expected_candidate=candidate)

    def test_probe_rejects_missing_key_and_tampered_candidate_without_network(self):
        from scripts.google_feed_delivery_probe import main
        candidate = saved_feed_run_candidate(self.row('google_news'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candidate.json'
            path.write_text(json.dumps(candidate))
            target = Path(folder) / 'target.json'
            target.write_text(json.dumps(dict(document_id='doc', version_id='ver',
                                             dataset_id='dataset', source_platform='google_news')))
            args = ['verify', '--candidate', str(path), '--target', str(target),
                    '--base-url', 'https://example.test', '--consumer-key-env', 'FEED_TEST_ABSENT_KEY']
            with patch.dict(os.environ, {}, clear=True), \
                 patch('scripts.google_feed_delivery_probe.WhaleReadbackClient') as client, \
                 patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(main(args), 2)
                self.assertEqual(json.loads(out.getvalue())['reason'], 'not_authorized')
                client.assert_not_called()
            candidate['event_key'] = 'tampered'
            path.write_text(json.dumps(candidate))
            with patch('scripts.google_feed_delivery_probe.WhaleReadbackClient') as client, \
                 patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(main(args), 2)
                self.assertEqual(json.loads(out.getvalue())['reason'], 'invalid_argument')
                client.assert_not_called()

    def test_probe_does_not_echo_connection_secrets(self):
        from scripts.google_feed_delivery_probe import main
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'FEED_TEST_DSN': 'postgresql://private-secret'}), \
             patch('scripts.google_feed_delivery_probe.prepare_public_feed_run',
                   side_effect=RuntimeError('postgresql://private-secret')), \
             patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(main(['prepare', '--source', 'google_news', '--request-id', str(uuid4()),
                                   '--output', str(Path(folder) / 'candidate.json'),
                                   '--database-url-env', 'FEED_TEST_DSN']), 2)
            self.assertNotIn('private-secret', out.getvalue())


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class FeedPublicDatabaseTests(unittest.TestCase):
    setUpClass = classmethod(legacy_tests.LegacyPreviewTests.setUpClass.__func__)
    tearDownClass = classmethod(legacy_tests.LegacyPreviewTests.tearDownClass.__func__)
    setUp = legacy_tests.LegacyPreviewTests.setUp
    insert = legacy_tests.LegacyPreviewTests.insert

    def test_prepare_public_feed_excludes_private_and_checks_source(self):
        for source, key in [('google_news', 'news_results'), ('google_trends', 'trending_searches')]:
            public = self.insert(source, {key: []}, classification='empty')
            private = self.insert(source, {key: []}, private=True, classification='empty')
            candidate = prepare_public_feed_run(self.dsn, public, source=source)
            self.assertEqual(candidate['payload']['run_id'], public)
            for request_id, selected in [(private, source), (public, 'google_web'),
                                         (public, 'google_trends' if source == 'google_news' else 'google_news')]:
                with self.subTest(source=source, selected=selected), self.assertRaises(DomainError):
                    prepare_public_feed_run(self.dsn, request_id, source=selected)

    def test_probe_creates_private_candidate_without_overwriting_evidence(self):
        from scripts.google_feed_delivery_probe import main, load_candidate
        request_id = self.insert('google_news', {'news_results': []}, classification='empty')
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'FEED_TEST_DSN': self.dsn}), \
             patch('sys.stdout', new_callable=io.StringIO):
            path = Path(folder) / 'candidate.json'
            args = ['prepare', '--source', 'google_news', '--request-id', request_id,
                    '--output', str(path), '--database-url-env', 'FEED_TEST_DSN']
            self.assertEqual(main(args), 0)
            before = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_candidate(path)['payload']['run_id'], request_id)
            self.assertEqual(main(args), 2)
            self.assertEqual(path.read_bytes(), before)
