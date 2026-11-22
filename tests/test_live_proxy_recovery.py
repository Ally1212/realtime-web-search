import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

from realtime.config import Config
from realtime.discovery import GoogleBlocked, SearchDiscovery
from realtime.google_feeds import collect as collect_feed
from realtime.keyword_trends import collect_saved
from realtime.live_collection import collection_error
from realtime.middle_live_runtime import LiveRunner
from realtime.product import ProductError, now
from realtime.proxy_pool import ProxyApiError, ProxyPool, ProxyRecord, ProxySynchronizer
from realtime.worker import WorkerManager


class FeedProxyRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.config = Config(proxy_cache_dir=Path(directory), proxy_selection_window=1)
        self.pool = ProxyPool(self.config)
        stamp = now().isoformat()
        self.records = [
            ProxyRecord('192.0.2.1', 8080, 'http', quality=100, last_checked=stamp),
            ProxyRecord('192.0.2.1', 1080, 'socks5', quality=99, last_checked=stamp),
            ProxyRecord('192.0.2.2', 8080, 'http', quality=98, last_checked=stamp),
        ]
        self.pool.cache.publish('private', self.records, None)
        self.store, self.serp = Mock(), Mock()
        self.serp.cached_feed.return_value = None

    def discovery(self, session=None):
        return SearchDiscovery(session=session, proxy_pool=self.pool, proxy_profile='private')

    def test_news_rotates_on_connect_failure_and_persists_success(self):
        session = Mock()
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [
            b'<rss><channel><item><title>Energy</title><link>https://example.org/energy</link></item></channel></rss>',
        ]
        session.get.side_effect = [requests.exceptions.ProxyError('private detail'), response]
        discovery = self.discovery(session)
        discovery.on_proxy_switch = Mock()
        rows, errors = collect_feed(discovery, self.store, self.serp,
                                    'campaign', 'google_news', 'energy', 'US:en', 3600)
        self.assertEqual(errors, [])
        self.assertEqual([r.url for r in rows], ['https://example.org/energy'])
        self.assertEqual([c.kwargs['proxies']['https'] for c in session.get.call_args_list],
                         ['http://192.0.2.1:8080', 'http://192.0.2.2:8080'])
        self.serp.record.assert_called_once()
        discovery.on_proxy_switch.assert_called_once_with()

    def test_only_three_exits_are_tried_per_poll(self):
        records = [ProxyRecord(f'192.0.2.{index}', 8080, 'http', quality=100-index,
                               last_checked=now().isoformat()) for index in range(1, 6)]
        self.pool.cache.publish('private', records, None)
        session = Mock()
        session.get.side_effect = requests.exceptions.ConnectTimeout()
        _, errors = collect_feed(self.discovery(session), self.store, self.serp, 'campaign',
                                 'google_news', 'energy', 'US:en', 3600)
        self.assertEqual(errors, ['google_news: proxy_transport_failed'])
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(self.pool.available_count('private', 'news.google.com'), 2)
        self.serp.cache_feed.assert_not_called()

    def test_news_does_not_cache_an_empty_proxy_pool(self):
        self.pool.cache.publish('private', [], None)
        session = Mock()
        rows, errors = collect_feed(self.discovery(session), self.store, self.serp,
                                    'campaign', 'google_news', 'energy', 'US:en', 3600)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ['google_news: proxy_pool_unavailable'])
        self.serp.cache_feed.assert_not_called()
        session.get.assert_not_called()

    def test_keyword_trends_replays_warmup_and_explore_on_new_proxy(self):
        first, second = MagicMock(), MagicMock()
        for session in (first, second):
            session.__enter__.return_value = session
            session.proxies = {}
        warm = Mock(status_code=200)
        widget = Mock(status_code=200, text='{"widgets":[{"id":"TIMESERIES","request":{},"token":"token"}]}')
        series = Mock(status_code=200, text='{"default":{"timelineData":[{"time":"100","value":[42]}]}}')
        first.get.side_effect = [warm, widget, requests.exceptions.ReadTimeout()]
        second.get.side_effect = [warm, widget, series]
        with patch('realtime.keyword_trends.requests.Session', side_effect=[first, second]):
            rows, errors = collect_saved(self.discovery(), self.store, self.serp,
                                         'campaign', 'energy', 'US', 'today 3-m')
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(first.proxies['https'], 'http://192.0.2.1:8080')
        self.assertEqual(second.proxies['https'], 'http://192.0.2.2:8080')
        self.assertEqual([c.args[0] for c in first.get.call_args_list],
                         [c.args[0] for c in second.get.call_args_list])
        self.assertEqual(self.serp.record.call_args.kwargs['response']['keyword_trends']['points'][0]['value'], 42)

    def test_keyword_trends_does_not_change_exits_on_google_rate_limit(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.proxies = {}
        session.get.return_value = Mock(status_code=429)
        with patch('realtime.keyword_trends.requests.Session', return_value=session) as create:
            _, errors = collect_saved(self.discovery(), self.store, self.serp,
                                      'campaign', 'energy', 'US', 'today 3-m')
        self.assertEqual(errors, ['trends_rate_limited'])
        create.assert_called_once()
        self.assertEqual(self.serp.record.call_args.kwargs['http_status'], 429)

    def test_news_rate_limit_is_exposed_even_with_generic_feed_exception(self):
        session = Mock()
        response = Mock(status_code=429, headers={})
        response.iter_content.return_value = [b'Rate limited']
        session.get.return_value = response
        _, errors = collect_feed(self.discovery(session), self.store, self.serp,
                                 'campaign', 'google_news', 'energy', 'US:en', 3600)
        session.get.assert_called_once()
        self.assertEqual(collection_error([self.serp.record.call_args.kwargs], errors),
                         'upstream_rate_limited')

    def test_failed_refresh_preserves_only_still_fresh_endpoints(self):
        stale = ProxyRecord('192.0.2.3', 8080, 'http', quality=101,
                            last_checked=(now()-timedelta(hours=3)).isoformat())
        self.pool.cache.publish('private', [stale, self.records[0]], None)
        client = Mock()
        client.fetch_all.side_effect = ProxyApiError('unavailable', 503)
        with self.assertRaises(ProxyApiError):
            ProxySynchronizer(self.config, client=client).sync('private', force=True)
        self.assertEqual(self.pool.available_count('private', 'news.google.com'), 1)
        self.assertEqual(self.pool.choose('private', 'news.google.com')[1], self.records[0].key)


class WebsiteCollectionRecoveryTests(unittest.TestCase):
    def test_wait_heartbeats_and_resumes_the_same_website_task(self):
        runner = object.__new__(LiveRunner)
        runner.source = 'google_web'
        runner.middle, runner.store, runner.collector = Mock(), Mock(), Mock()
        runner.save, runner.heartbeat, runner.upload = Mock(), Mock(), Mock()
        runner.middle.status.return_value = {'platform_status': 'running'}
        runner.collector.post.return_value = []
        state = dict(middle_query_id='lq', platform_task_ids=['task'], task={'id': 'task'})
        job = dict(id='job', status='running', deadline_at=now()+timedelta(minutes=5), response=state)
        runner.collect = Mock(side_effect=ProductError('proxy_pool_unavailable', 'no proxy', 503))
        runner.advance(job)
        self.assertEqual(state['stage'], 'waiting_for_collection')
        runner.advance(job)
        self.assertEqual(runner.heartbeat.call_count, 2)
        runner.collect.assert_called_once()
        runner.store.finish.assert_not_called()

        state['collection_retry_at'] = 0
        def recover(job, state):
            state['messages'] = [{'dataset_id': 'dataset', 'source': {'source_record_key': 'record'}}]
        runner.collect.side_effect = recover
        with patch('realtime.middle_live_runtime.receipt_query', return_value={}), \
             patch('realtime.middle_live_runtime.processing_receipts', return_value=[{'status': 'unknown'}]):
            runner.advance(job)
        runner.upload.assert_called_once_with(state)
        runner.middle.create.assert_not_called()

    def test_trends_rate_limit_waits_past_failure_cache_and_then_recovers(self):
        runner = object.__new__(LiveRunner)
        runner.source = 'google_trends'
        runner.middle, runner.store, runner.collector = Mock(), Mock(), Mock()
        runner.save, runner.heartbeat, runner.upload = Mock(), Mock(), Mock()
        runner.middle.status.return_value = {'platform_status': 'running'}
        state = dict(middle_query_id='lq', platform_task_ids=['task'], task={'id': 'task'})
        job = dict(id='job', status='running', deadline_at=now()+timedelta(minutes=5), response=state)
        runner.collect = Mock(side_effect=ProductError('upstream_rate_limited', 'limited', 503))
        with patch('realtime.middle_live_runtime.time.time',return_value=1000):
            runner.advance(job)
        self.assertEqual(state['stage'],'waiting_for_collection')
        self.assertEqual(state['collection_retry_at'],1120)
        runner.store.finish.assert_not_called()
        runner.collector.post.assert_not_called()
        with patch('realtime.middle_live_runtime.time.time',return_value=1119):
            runner.advance(job)
        runner.collect.assert_called_once()
        state['collection_retry_at']=0
        def recover(job,state):
            state['messages']=[{'dataset_id':'dataset','source':{'source_record_key':'record'}}]
        runner.collect.side_effect=recover
        with patch('realtime.middle_live_runtime.receipt_query',return_value={}), \
             patch('realtime.middle_live_runtime.processing_receipts',return_value=[{'status':'unknown'}]):
            runner.advance(job)
        self.assertEqual(runner.collect.call_count,2)
        runner.upload.assert_called_once_with(state)

    def test_http403_is_sent_to_platform_without_recollecting(self):
        runner = object.__new__(LiveRunner)
        runner.source = 'google_web'
        runner.middle, runner.store, runner.collector = Mock(), Mock(), Mock()
        runner.save, runner.heartbeat, runner.stop_campaign = Mock(), Mock(), Mock()
        runner.middle.status.return_value = {'platform_status': 'running'}
        runner.collect = Mock(side_effect=ProductError('google_http_403', 'private detail', 503))
        state = dict(middle_query_id='lq', platform_task_ids=['task'], task={'id': 'task'})
        job = dict(id='job', status='running', deadline_at=now()+timedelta(minutes=5), response=state)
        runner.advance(job)
        self.assertEqual(runner.collector.post.call_args.args[1]['message'], 'google_http_403')
        self.assertEqual(runner.store.finish.call_args.args[-1], 'google_http_403')


class WebCollectionRecoveryTests(unittest.TestCase):
    def test_page_loop_stops_on_empty_pool_and_skips_completed_page_on_resume(self):
        discovery = SearchDiscovery(google_web_max_pages=11)
        discovery.continue_on_page_error = True
        discovery.pause_on_proxy_unavailable = True
        discovery.completed_web_pages = {1}
        discovery._discover_google_page = Mock(side_effect=GoogleBlocked('google_proxy_unavailable'))
        _, errors = discovery.discover('energy', 11)
        self.assertEqual(errors, ['page 2: google web google_proxy_unavailable'])
        discovery._discover_google_page.assert_called_once_with('energy', 2)
        discovery._discover_google_page = Mock(return_value=[])
        discovery.discover('energy', 11)
        self.assertEqual([c.args[1] for c in discovery._discover_google_page.call_args_list],
                         list(range(2, 12)))

    def test_worker_refreshes_live_proxy_profile_without_active_local_campaigns(self):
        worker = object.__new__(WorkerManager)
        worker.config = Config(proxy_api_key='test-only', default_proxy_profile='private')
        worker.store, worker.syncer, worker.stop = Mock(), Mock(), Mock()
        worker.store.local_proxy_profiles.return_value = set()
        worker.stop.is_set.side_effect = [False, True]
        worker.proxy_sync_loop()
        worker.syncer.sync.assert_called_once_with('private')
