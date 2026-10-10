import base64
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from unittest.mock import Mock, patch

import requests

from realtime.discovery import SearchDiscovery, SearchResult
from realtime.discovery_audit import DiscoveryAuditFailure
from realtime.discovery_audit_metrics import summarize_auxiliary


FEED = b'''<rss><channel><item><title>secret-body-not-stored</title><link>https://publisher.example/a?utm_source=test#part</link></item><item><title>Private</title><link>http://127.0.0.1/private</link></item></channel></rss>'''


def response(url, content=FEED, status=200):
    r = requests.Response()
    r.status_code, r.url, r._content, r._content_consumed = status, url, content, True
    r.headers['Content-Length'] = str(len(content))
    return r


class AuxiliaryDiscoveryAuditTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.session = Mock()
        self.public = patch('realtime.discovery_audit.is_public_url',
                            side_effect=lambda url: urlsplit(url).hostname != '127.0.0.1')
        self.public.start()
        self.addCleanup(self.public.stop)

    def discovery(self, **kwargs):
        return SearchDiscovery(session=self.session,
                               auxiliary_audit_recorder=lambda **e: self.events.append(e), **kwargs)

    def kind(self, name):
        return [e for e in self.events if e['kind'] == name]

    def test_network_feed_links_and_request_correlations_have_no_payloads(self):
        url = 'https://news.google.com/rss?secret-query=1'
        self.session.get.return_value = response(url)
        d = self.discovery(feeds=(('google_news_rss', url),))
        results, errors = d.discover_feeds(('secret-query',), ('generic',))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.kind('request_started')), 1)
        self.assertEqual(len(self.kind('request_finished')), 1)
        event = self.kind('discovery_result')[0]
        self.assertEqual(event['data']['eligible_result_urls'], ['https://publisher.example/a'])
        self.assertEqual(event['data']['raw_result_count'], 2)
        self.assertEqual(event['data']['request_ids'], [self.kind('request_started')[0]['request_id']])
        self.assertTrue(self.kind('request_finished')[0]['data']['attempted'])
        serialized = json.dumps(self.events)
        self.assertNotIn('secret-query', serialized)
        self.assertNotIn('secret-body-not-stored', serialized)

    def test_cache_hit_records_urls_but_no_network_calls(self):
        cached = dict(payload=[dict(url='https://publisher.example/cached', title='Cache', engines=['google_news'])])
        d = self.discovery(news_locales=('US:en',), cache_get=Mock(return_value=cached))
        results, errors = d.discover_feeds(('AI',), ('news',))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(self.kind('request_started')), 0)
        self.assertTrue(self.kind('discovery_result')[0]['data']['cache_hit'])
        self.session.get.assert_not_called()

    def test_not_modified_is_a_real_call_with_cached_links(self):
        cached = dict(etag='fixture', payload=[dict(url='https://publisher.example/a', title='A', engines=['google_news'])])
        d = self.discovery(news_locales=('US:en',), cache_get=Mock(side_effect=[None, cached]))
        self.session.get.return_value = response(d._news_url('AI', 'US:en'), b'', 304)
        results, errors = d.discover_feeds(('AI',), ('news',))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(self.kind('request_finished')[0]['data']['http_status'], 304)
        self.assertFalse(self.kind('discovery_result')[0]['data']['cache_hit'])
        self.assertEqual(len(self.kind('content_examined')), 0)

    def test_proxy_unavailable_is_not_a_network_call(self):
        pool = Mock()
        pool.choose.return_value = None
        d = self.discovery(proxy_pool=pool, proxy_profile='private', news_locales=('US:en',))
        results, errors = d.discover_feeds(('AI',), ('news',))
        self.assertEqual(results, [])
        self.assertEqual(errors, ['google_news: ProxyError'])
        self.assertFalse(self.kind('request_finished')[0]['data']['attempted'])
        self.session.get.assert_not_called()

    def test_timeout_is_attempted_and_keeps_only_error_class(self):
        self.session.get.side_effect = requests.ReadTimeout('secret-password-in-error')
        d = self.discovery(news_locales=('US:en',))
        results, errors = d.discover_feeds(('AI',), ('news',))
        self.assertEqual(results, [])
        self.assertEqual(errors, ['google_news: ReadTimeout'])
        finished = self.kind('request_finished')[0]['data']
        self.assertTrue(finished['attempted'])
        self.assertEqual(finished['error_type'], 'ReadTimeout')
        self.assertNotIn('secret-password-in-error', json.dumps(self.events))

    def test_plain_429_is_distinct_from_explicit_captcha(self):
        self.session.get.return_value = response('https://news.google.com/rss', b'Rate limited', 429)
        d = self.discovery(news_locales=('US:en',))
        d.discover_feeds(('AI',), ('news',))
        finished = self.kind('request_finished')[0]['data']
        self.assertEqual(finished['http_status'], 429)
        self.assertFalse(finished['url_captcha'])
        self.assertEqual(self.kind('content_examined'), [])

    def test_google_html_captcha_identified_without_storing_body(self):
        body = b'<html><body>Our systems have detected unusual traffic. <div class="g-recaptcha"/></body></html>'
        self.session.get.return_value = response('https://news.google.com/rss', body)
        d = self.discovery(news_locales=('US:en',))
        d.discover_feeds(('AI',), ('news',))
        self.assertTrue(self.kind('content_examined')[0]['data']['explicit_captcha'])
        self.assertNotIn('Our systems have detected', json.dumps(self.events))

    def test_rss_mention_of_captcha_does_not_become_a_challenge(self):
        body = b'<rss><channel><item><title>Our systems have detected unusual traffic and g-recaptcha</title><link>https://publisher.example/a</link></item></channel></rss>'
        self.session.get.return_value = response('https://news.google.com/rss', body)
        d = self.discovery(news_locales=('US:en',))
        d.discover_feeds(('AI',), ('news',))
        self.assertFalse(self.kind('content_examined')[0]['data']['explicit_captcha'])

    def test_news_get_and_rpc_post_are_separate_calls_and_resolve_one_link(self):
        self.session.get.return_value = response('https://news.google.com/articles/token',
            b'<div data-n-a-id="article" data-n-a-ts="123" data-n-a-sg="secret-signature"></div>')
        payload = json.dumps([['wrb.fr', 'Fbv4je', json.dumps(['garturlres', 'https://publisher.example/article', 1])]])
        self.session.post.return_value = response('https://news.google.com/_/DotsSplashUi/data/batchexecute', payload.encode())
        d = self.discovery()
        result = d._resolve_google_news(SearchResult('https://news.google.com/rss/articles/token', 'Title', ('google_news',)))
        self.assertEqual(result.url, 'https://publisher.example/article')
        starts = self.kind('request_started')
        self.assertEqual([e['data']['method'] for e in starts], ['GET', 'POST'])
        self.assertEqual(len(self.kind('request_finished')), 2)
        self.assertEqual(len(self.kind('content_examined')), 2)
        self.assertEqual(self.kind('discovery_result')[0]['data']['eligible_result_urls'], [result.url])
        self.assertNotIn('secret-signature', json.dumps(self.events))
        self.assertNotIn('f.req', json.dumps(self.events))

    def test_offline_news_resolution_never_counts_a_network_call(self):
        token = base64.urlsafe_b64encode(b'\x00https://publisher.example/article').decode().rstrip('=')
        d = self.discovery()
        result = d._resolve_google_news(SearchResult(f'https://news.google.com/rss/articles/{token}', 'Title', ('google_news',)))
        self.assertEqual(result.url, 'https://publisher.example/article')
        self.assertTrue(self.kind('discovery_result')[0]['data']['offline'])
        self.assertEqual(self.kind('request_started'), [])
        self.session.get.assert_not_called()

    def test_concurrent_feed_traces_do_not_mix_request_ids(self):
        self.session.get.side_effect = lambda url, **kwargs: response(url, b'<rss/>')
        d = self.discovery(news_locales=('US:en',))
        d.discover_feeds(('one', 'two', 'three'), ('news',))
        starts = {e['request_id']: e['trace_id'] for e in self.kind('request_started')}
        phases = self.kind('discovery_result')
        self.assertEqual(len(phases), 3)
        self.assertEqual(len({p['trace_id'] for p in phases}), 3)
        for p in phases:
            self.assertEqual(len(p['data']['request_ids']), 1)
            self.assertEqual(starts[p['data']['request_ids'][0]], p['trace_id'])

    def test_audit_write_failure_prevents_an_unrecorded_request(self):
        d = SearchDiscovery(session=self.session, news_locales=('US:en',),
                            auxiliary_audit_recorder=Mock(side_effect=RuntimeError('secret-password')))
        with self.assertRaises(DiscoveryAuditFailure) as error:
            d.discover_feeds(('AI',), ('news',))
        self.assertNotIn('secret-password', str(error.exception))
        self.session.get.assert_not_called()

    def test_metrics_count_explicit_captcha_separately_from_ordinary_429(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.session.get.side_effect = [
            response('https://news.google.com/rss', b'Rate limited', 429),
            response('https://news.google.com/rss', b'<html><body>Our systems have detected unusual traffic</body></html>'),
        ]
        d = self.discovery(news_locales=('US:en',))
        d.discover_feeds(('one',), ('news',))
        d.discover_feeds(('two',), ('news',))
        report = summarize_auxiliary(self.events, start, datetime.now(timezone.utc) + timedelta(seconds=1), set())
        self.assertEqual(report['completed_client_calls'], 2)
        self.assertEqual(report['explicit_captcha_calls'], 1)
        self.assertEqual(report['explicit_captcha_rate'], 0.5)
        self.assertEqual(report['body_not_examined'], 1)
        self.assertTrue(report['request_audit_complete'])

    def test_trends_cache_has_batches_without_client_calls(self):
        cached = dict(payload=[dict(url='https://publisher.example/trend', title='Trend', engines=['google_trends'])])
        d = self.discovery(news_locales=('US:en', 'SG:en'), cache_get=Mock(return_value=cached))
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        results, errors = d.discover_trends()
        report = summarize_auxiliary(self.events, start, datetime.now(timezone.utc) + timedelta(seconds=1), set())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(report['completed_client_calls'], 0)
        self.assertEqual(report['cache_hit_batches'], 2)
        self.assertEqual(report['unique_eligible_discovered_links'], 1)
        self.assertIsNone(report['explicit_captcha_rate'])

    def test_cached_news_candidate_resolution_records_link_without_rpc(self):
        from realtime.crawler import FocusedSpider
        cached = dict(payload=[dict(url='https://publisher.example/cached', title='Cached', engines=['google_news'])])
        d = self.discovery(cache_get=Mock(return_value=cached))
        result = FocusedSpider._resolve_news_candidates(d, [('https://news.google.com/articles/token', ('google_news',))])
        self.assertEqual(result, {'https://news.google.com/articles/token': 'https://publisher.example/cached'})
        self.assertTrue(self.kind('discovery_result')[0]['data']['cache_hit'])
        self.assertEqual(self.kind('request_started'), [])
        self.session.get.assert_not_called()

    def test_metrics_reject_mismatched_content_and_batch_request_correlations(self):
        self.session.get.return_value = response('https://news.google.com/rss',
            b'<html>Our systems have detected unusual traffic</html>')
        self.discovery(news_locales=('US:en',)).discover_feeds(('one',), ('news',))
        events = copy.deepcopy(self.events)
        for event in events:
            if event['kind'] in ('content_examined', 'discovery_result'):
                event['trace_id'] = 'incorrect-trace'
        report = summarize_auxiliary(events, datetime.now(timezone.utc) - timedelta(minutes=1),
                                     datetime.now(timezone.utc) + timedelta(seconds=1), set())
        self.assertEqual(report['explicit_captcha_calls'], 0)
        self.assertEqual(report['content_correlation_mismatches'], 1)
        self.assertEqual(report['batch_request_correlation_mismatches'], 1)
        self.assertFalse(report['request_audit_complete'])
        self.assertFalse(report['discovery_batch_audit_complete'])
