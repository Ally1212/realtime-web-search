import copy
import unittest
from unittest.mock import Mock

import requests

from realtime.google_domain import DomainError
from realtime.middle_platform_client import MiddlePlatformClient, upstream_idempotency_key, web_request
from realtime.product import ProductError


def envelope(status='pending'):
    return dict(live_query=dict(id='live-1', dataset_id='social_media_raw', resource_kind='content',
                               query='battery', max_items=10, status=status),
                runs=[dict(id='run-1', live_query_id='live-1', source_platform='google_search',
                           collection_task_id='task-1', status=status)])


def response(status, data):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


class MiddlePlatformTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = MiddlePlatformClient('https://middle.test', 'server-secret', session=self.session)
        self.session.request.return_value = response(202, envelope())

    def test_create_uses_official_contract_and_scoped_retry_key(self):
        result = self.client.create('tenant-a', {'query': ' battery ', 'source': 'google_web'}, 'retry-1')
        self.assertEqual(result['stage'], 'submitted_to_middle')
        call = self.session.request.call_args
        self.assertEqual(call.args, ('POST', 'https://middle.test/v1/live-queries'))
        self.assertEqual(call.kwargs['json'], dict(dataset_id='social_media_raw', resource_kind='content',
                         source_platforms=['google_search'], query='battery', region='US', language='en',
                         max_items=10, deadline_seconds=300))
        self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer server-secret')
        self.assertEqual(call.kwargs['headers']['Idempotency-Key'], upstream_idempotency_key('tenant-a', 'retry-1'))
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('server-secret', str(result))
        self.assertNotIn('tenant-a', call.kwargs['headers']['Idempotency-Key'])
        self.assertNotEqual(upstream_idempotency_key('tenant-a', 'retry-1'), upstream_idempotency_key('tenant-b', 'retry-1'))
        self.assertEqual(upstream_idempotency_key('tenant-a', 'retry-1'), upstream_idempotency_key('tenant-a', 'retry-1'))

    def test_unverified_collection_never_unlocks_download(self):
        stages = dict(pending='submitted_to_middle', collecting='collecting', partial='ingest_pending',
                      succeeded='ingest_pending', failed='failed', canceled='cancelled')
        for status, stage in stages.items():
            with self.subTest(status=status):
                self.session.request.return_value = response(200, envelope(status))
                result = self.client.status('live-1')
                self.assertEqual(result['stage'], stage)
                self.assertFalse(result['download_ready'])
                self.assertFalse(result['readback_verified'])

    def test_entitlement_and_credit_errors_are_explicit_and_redacted(self):
        for status, code in ((401, 'middle_unauthorized'), (402, 'middle_credit_required'),
                             (403, 'middle_entitlement_denied'), (404, 'not_found'),
                             (409, 'middle_not_ready'), (429, 'middle_rate_limited'), (302, 'middle_unavailable')):
            with self.subTest(status=status):
                self.session.request.return_value = response(status, {'message': 'server-secret'})
                with self.assertRaises(ProductError) as caught:
                    self.client.create('tenant-a', {'query': 'battery'}, 'retry-1')
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('server-secret', str(caught.exception))

    def test_timeout_is_unknown_outcome_without_automatic_retry(self):
        self.session.request.side_effect = requests.Timeout('server-secret')
        with self.assertRaises(ProductError) as caught:
            self.client.create('tenant-a', {'query': 'battery'}, 'retry-1')
        self.assertEqual(caught.exception.code, 'middle_transport_unknown')
        self.assertNotIn('server-secret', str(caught.exception))
        self.session.request.assert_called_once()

    def test_invalid_sources_parameters_and_retry_keys_fail_before_network(self):
        for payload in ({'query': 'x', 'source': 'google_images'}, {'query': 'x', 'source': 'google_news'},
                        {'query': 'x', 'source': 'google_trends'}, {'query': 'x', 'provider': 'openserp'},
                        {'query': ''}, {'query': 'a'*301}, {'query': 'x', 'region': 'invalid'},
                        {'query': 'x', 'language': 'invalid'}):
            with self.subTest(payload=payload), self.assertRaises(ProductError):
                self.client.create('tenant-a', payload, 'retry-1')
        for key in (None, '', ' '*3, 'a'*257, 10):
            with self.subTest(key=key), self.assertRaises(ProductError):
                self.client.create('tenant-a', {'query': 'x'}, key)
        self.session.request.assert_not_called()

    def test_https_and_path_injection_rejected(self):
        for url in ('http://middle.test', 'https://key@middle.test', 'https://middle.test/path',
                    'https://middle.test/?token=secret'):
            with self.subTest(url=url), self.assertRaises(DomainError):
                MiddlePlatformClient(url, 'server-secret')
        for ident in ('../other', 'id?x=y', 'id/other', '', None):
            for operation in (self.client.status, self.client.cancel, self.client.results_page):
                with self.subTest(ident=ident), self.assertRaises(ProductError):
                    operation(ident)
        self.session.request.assert_not_called()

    def test_wrong_scope_run_and_query_rejected(self):
        changes = [lambda d: d['live_query'].update(dataset_id='other'),
                   lambda d: d['live_query'].update(resource_kind='user'),
                   lambda d: d['live_query'].update(id='other'),
                   lambda d: d['live_query'].update(status='queued'),
                   lambda d: d['runs'][0].update(source_platform='reddit'),
                   lambda d: d['runs'][0].update(live_query_id='other'),
                   lambda d: d['runs'][0].update(collection_task_id='../task'),
                   lambda d: d.update(runs=[])]
        for change in changes:
            data = envelope(); change(data)
            self.session.request.return_value = response(200, data)
            with self.assertRaises(ProductError):
                self.client.status('live-1')
        data = envelope(); data['live_query']['query'] = 'different'
        self.session.request.return_value = response(202, data)
        with self.assertRaises(ProductError):
            self.client.create('tenant-a', {'query': 'battery'}, 'retry-1')

    def test_cancel_uses_middle_and_validates_response(self):
        self.session.request.return_value = response(200, envelope('canceled'))
        self.assertEqual(self.client.cancel('live-1')['stage'], 'cancelled')
        self.assertEqual(self.session.request.call_args.args,
                         ('POST', 'https://middle.test/v1/live-queries/live-1/cancel'))

    def test_results_scope_cursor_and_page_bounds(self):
        data = dict(live_query_id='live-1', items=[{'document': dict(id='doc-1',
                    dataset_id='social_media_raw', source_platform='google_search')}], next_cursor='next')
        self.session.request.return_value = response(200, data)
        self.assertEqual(self.client.results_page('live-1', cursor='a&tenant=other', limit=1), data)
        self.assertIn('cursor=a%26tenant%3Dother', self.session.request.call_args.args[1])
        for mutate in (lambda d: d.update(live_query_id='other'),
                       lambda d: d['items'][0]['document'].update(dataset_id='other'),
                       lambda d: d['items'][0]['document'].update(source_platform='reddit'),
                       lambda d: d.update(items=[{}, {}]), lambda d: d.update(next_cursor=1)):
            bad = copy.deepcopy(data); mutate(bad)
            self.session.request.return_value = response(200, bad)
            with self.assertRaises(ProductError):
                self.client.results_page('live-1', limit=1)
        for limit in (0, 101, True, '20'):
            with self.assertRaises(ProductError):
                self.client.results_page('live-1', limit=limit)

    def test_invalid_json_envelope_is_redacted(self):
        self.session.request.return_value.json.side_effect = ValueError('server-secret')
        with self.assertRaises(ProductError) as caught:
            self.client.status('live-1')
        self.assertEqual(caught.exception.code, 'middle_invalid_response')
        self.assertNotIn('server-secret', str(caught.exception))
        self.session.request.return_value.json.side_effect = None
        for data in ([], {}, {'live_query': None, 'runs': None}):
            self.session.request.return_value = response(200, data)
            with self.assertRaises(ProductError):
                self.client.status('live-1')
