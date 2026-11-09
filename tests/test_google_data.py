import copy
import unittest
from datetime import datetime, timezone

from realtime.google_data import normalize_request, news_time, url_identity


def request(source='google_web', response=None, rid='run-1', status='results'):
    return dict(request_id=rid, source=source, query='C++ "Brand"', page=1,
                classification=status, completed_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
                response=response or {})


class GoogleDataTests(unittest.TestCase):
    def test_occurrences_not_deduplicated_and_stable_across_retry(self):
        row = request(response={'organic_results': [
            {'link': 'https://EXAMPLE.com/a?page=2&utm_source=x', 'position': 1, 'rank_absolute': 3},
            {'link': 'https://example.com/a?page=2', 'position': 2}]})
        value = normalize_request(row)
        self.assertEqual(value, normalize_request(copy.deepcopy(row)))
        a, b = value['occurrences']
        self.assertEqual(a['url']['url_id'], b['url']['url_id'])
        self.assertNotEqual(a['occurrence_id'], b['occurrence_id'])
        self.assertEqual((a['organic_position'], a['source_absolute_rank']), (1, 3))
        newer = normalize_request({**row, 'request_id': 'run-2'})
        self.assertEqual(value['query_variant_id'], newer['query_variant_id'])
        self.assertNotEqual(a['occurrence_id'], newer['occurrences'][0]['occurrence_id'])
        changed = copy.deepcopy(row)
        changed['response']['search_parameters'] = {'device': 'mobile'}
        self.assertNotEqual(value['query_variant_id'], normalize_request(changed)['query_variant_id'])
        self.assertIsNone(value['source_observed_at'])
        self.assertIsNone(value['requested_at'])
        parameters = copy.deepcopy(row)
        parameters['response']['search_parameters'] = {'safe_search': 'active'}
        self.assertNotEqual(value['query_variant_id'], normalize_request(parameters)['query_variant_id'])
        self.assertEqual(value['query_conditions']['effective_conditions_status'], 'unverified')

    def test_saved_collection_times_survive_without_inventing_source_cache_time(self):
        value = normalize_request(request(response={'search_metadata': {
            'cache_status': 'HIT', 'collection_times': {
                'requested_at': '2026-11-05T00:00:00+00:00',
                'received_at': '2026-11-05T00:00:01+00:00'}}}))
        self.assertEqual(value['requested_at'], '2026-11-05T00:00:00Z')
        self.assertEqual(value['received_at'], '2026-11-05T00:00:01Z')
        self.assertIsNone(value['source_observed_at'])
        self.assertIsNone(value['cache_original_observed_at'])
        self.assertEqual(value['cache_status'], 'HIT')

    def test_url_normalization_does_not_merge_business_parameters_or_paths(self):
        cases = ['https://example.com/a?id=1', 'https://example.com/a?id=2',
                 'https://example.com/b?id=1', 'https://m.example.com/a?id=1']
        self.assertEqual(len({url_identity(u)['url_id'] for u in cases}), 4)
        for url in ('http://127.0.0.1/a', 'https://u:p@example.com/', 'https://example.com:bad/a', 'https://example.com/a?api_key=secret', 'HTTPS://example.com/a?API-KEY=secret',
                    'https://example.com/a#access_token=secret'):
            self.assertIsNone(url_identity(url)['url_id'])
        for url in ('HTTPS://example.com/a?query=hello%20world', 'https://example.com/a?x=%2f'):
            self.assertEqual(url_identity(url)['status'], 'available')

    def test_time_presence_validation_and_publisher_verification_are_distinct(self):
        base = {'date': '2026-11-05T00:00:00Z', 'date_raw': 'Thu, 05 Nov 2026 00:00:00 GMT'}
        self.assertEqual(news_time(base)['status'], 'verified_rss')
        self.assertFalse(news_time(base)['publisher_page_verified'])
        self.assertEqual(news_time({'date': base['date']})['status'], 'unverified_missing_raw')
        self.assertEqual(news_time({})['status'], 'missing')
        self.assertEqual(news_time({**base, 'date': 'invalid'})['status'], 'invalid')
        self.assertEqual(news_time({**base, 'date': '2026-11-05T01:00:00Z'})['status'], 'mismatch')
        self.assertIsNone(news_time({**base, 'date': '2026-11-05T00:00:00'})['published_at'])

    def test_trends_topics_children_and_markers_are_lossless(self):
        value = normalize_request(request('google_trends', {'trending_searches': [
            {'keyword': 'topic', 'approx_traffic': '20,000+', 'geo': 'US', 'published_at': '2026-11-05T00:00:00Z', 'articles': [
                {'link': 'https://news.google.com/rss/articles/token', 'position': 1},
                {'link': 'https://publisher.example/a', 'title': 'Article'}]}]}))
        topic = value['topics'][0]
        self.assertEqual(topic['approx_traffic_label'], '20,000+')
        self.assertIsNone(topic['numeric_trend_series'])
        self.assertEqual(topic['source_fields']['geo'], 'US')
        self.assertEqual(topic['source_fields']['published_at'], '2026-11-05T00:00:00Z')
        a, b = value['occurrences']
        self.assertEqual(a['parent_topic_id'], topic['topic_observation_id'])
        self.assertIsNone(a['article_url'])
        self.assertIsNone(a['organic_position'])
        self.assertEqual(b['child_ordinal'], 2)

    def test_failure_empty_and_malformed_do_not_become_success(self):
        for status, expected in [('empty', 'true_zero'), ('timeout', 'source_error')]:
            value = normalize_request(request(status=status))
            self.assertEqual(value['availability'], expected)
        value = normalize_request(request(response={'organic_results': [None, {'title': 'unknown'}]}))
        self.assertEqual(len(value['invalid_records']), 1)
        self.assertIsNone(value['occurrences'][0]['url']['url_id'])
        self.assertEqual(len(normalize_request(request(response={'organic_results': {}}))['invalid_records']), 1)

    def test_transport_secrets_not_exported_in_extra_source_fields(self):
        value = normalize_request(request(response={'organic_results': [{
            'link': 'https://example.com/a', 'api_key': 'secret', 'new_field': {'answer': 42}, 'sitelinks': [{'url': 'https://example.com/?api_key=secret', 'token': 'secret'}]}],
            'serp_features': [{'type': 'unknown_new_type', 'token': 'secret'}]}))
        self.assertNotIn('secret', str(value))
        self.assertEqual(value['occurrences'][0]['source_fields']['new_field'], {'answer': 42})
        self.assertEqual(value['features'][0]['type'], 'unknown_new_type')

    def test_nested_query_conditions_use_the_same_secret_filter(self):
        value = normalize_request(request(response={'search_parameters':{
            'location':{'label':'London','api_key':'fixture-secret'},
            'device':{'name':'mobile','token':'fixture-secret'}}}))
        self.assertNotIn('fixture-secret', str(value))
        self.assertEqual(value['query_conditions']['location'], {'label':'London'})
