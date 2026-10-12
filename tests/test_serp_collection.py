import concurrent.futures
import hashlib
import json
import os
import signal
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, _POOLS, _INITIALIZED_DSNS
from realtime.config import Config
from realtime.discovery import SearchDiscovery, SearchResult
from realtime.serp_fields import organic_result, response_envelope, normalize_serp_url, public_serp_url, URL_NORMALIZATION_VERSION
from realtime.serp_store import SerpStore
from realtime.serp_worker import run_serp_campaign, SerpSupply
from realtime.worker import WorkerManager


def envelope(url='https://example.com/article', query='solar research', page=1):
    row = SearchResult(url, 'Research', ('google_web',), organic_result(
        {'title': 'Research', 'url': url, 'snippet': 'Actual search snippet.', 'rank': 1,
         'position': {'absolute': 3}, 'sitelinks': [{'title': 'Lab', 'url': 'https://example.com/lab'}]}, url=url, page=page))
    response = response_envelope({'results': [], 'serp_features': [{'type': 'people_also_ask', 'question': 'Why?'}],
                                  'pagination': {'page': page, 'has_more': False, 'next_start': 10}},
                                 {'text': query, 'lang': 'EN', 'region': 'US', 'start': (page-1)*10, 'limit': 10, 'features': 'true'}, [row])
    return row, response


class SerpCollectionTests(unittest.TestCase):
    def test_url_registry_preserves_page_identity_without_dns_or_article_http(self):
        self.assertEqual(normalize_serp_url('https://EXAMPLE.com/a?ref=chapter2&page=3&utm_source=google#x'), 'https://example.com/a?ref=chapter2&page=3')
        self.assertEqual(normalize_serp_url('https://example.com/a?article=17&srsltid=token&page=3&SrsLtId=another#x'), 'https://example.com/a?article=17&page=3')
        with patch('socket.getaddrinfo', side_effect=AssertionError('no target DNS')):
            self.assertTrue(public_serp_url('https://docs.google.com/document/d/public'))
            self.assertTrue(public_serp_url('https://not-resolved.example/article'))
            self.assertFalse(public_serp_url('http://127.0.0.1/article'))
            self.assertFalse(public_serp_url('http://user:secret@example.com/a'))
            self.assertFalse(public_serp_url('https://www.google.com/search?q=test'))
    def test_fields_survive_cache_roundtrip_and_optional_fields_stay_unknown(self):
        result, response = envelope()
        decoded = SearchDiscovery._deserialize(json.loads(json.dumps(SearchDiscovery._serialize([result]))))[0]
        self.assertEqual(decoded.serp_fields, result.serp_fields)
        self.assertEqual(decoded.serp_fields['snippet'], 'Actual search snippet.')
        self.assertEqual(decoded.serp_fields['position'], 1)
        self.assertEqual(decoded.serp_fields['rank_absolute'], 3)
        self.assertIsNone(decoded.serp_fields['author'])
        self.assertIsNone(response['ai_overview'])
        self.assertEqual(response['module_status']['ai_overview'], 'not_implemented')
        self.assertEqual(len(response['serp_features']), 1)

    def test_merge_keeps_snippet_and_rank(self):
        result, _ = envelope()
        d = SearchDiscovery(query_concurrency=1)
        d._discover = Mock(return_value=([result], []))
        d._close_browser = Mock()
        rows, _ = d.discover_many(('one', 'two'), 1)
        self.assertEqual(rows[0].serp_fields, result.serp_fields)

    def test_failed_mandatory_write_does_not_cache_or_finish_page(self):
        result, response = envelope()
        recorder = Mock(side_effect=RuntimeError('storage failure'))
        cache = Mock()
        d = SearchDiscovery(proxy_profile='direct', serp_response_recorder=recorder, cache_put=cache)
        d._reserve_source = Mock()
        d._select_proxy = Mock(return_value=(None, ''))
        d.transport.fetch = Mock(return_value=[result])
        d.transport.local.last_serp_response = response
        rows, errors = d.discover('solar research', 1)
        self.assertEqual(rows, [])
        self.assertTrue(errors)
        cache.assert_not_called()

    def test_once_task_uses_task_page_count_and_completes_clean_run(self):
        store = Mock()
        cid = str(uuid4())
        task_options = {'mode':'once','web_pages':3}
        store.campaign.return_value = {'id': cid, 'query': 'solar research', 'aliases': [], 'status': 'active', 'proxy_profile': 'direct', 'sources': ['google_web'], 'serp_options': task_options}
        store.whale_task_for_campaign.return_value = None
        discovery = Mock()
        discovery.discover_many.return_value = ([], [])
        with patch('realtime.serp_worker.make_discovery', return_value=discovery), patch('subprocess.Popen', side_effect=AssertionError('no body subprocess')):
            run_serp_campaign(cid, Config(), store, Mock())
        discovery.discover_many.assert_called_once_with(('solar research',), 3)

    def test_serp_campaign_never_starts_article_download_or_auxiliary_sources(self):
        store = Mock()
        cid = str(uuid4())
        store.campaign.return_value = {'id': cid, 'query': 'solar research', 'aliases': [], 'status': 'active', 'proxy_profile': 'direct', 'sources': ['google_web']}
        store.whale_task_for_campaign.return_value = None
        discovery = Mock()
        discovery.discover_many.return_value = ([envelope()[0]], [])
        with patch('realtime.serp_worker.make_discovery', return_value=discovery), patch('subprocess.Popen', side_effect=AssertionError('no body subprocess')):
            run_serp_campaign(cid, Config(), store, Mock())
        discovery.discover_feeds.assert_not_called()
        discovery.discover_trends.assert_not_called()
        store.record_page.assert_not_called()
        discovery.close.assert_called_once()

    def test_worker_off_switch_uses_serp_instead_of_scrapy(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.config = Config(content_fetch_enabled=False)
        manager.store = Mock()
        manager.store.campaign.return_value = {'status': 'active', 'proxy_profile': 'direct'}
        manager.store.is_local_campaign.return_value = True
        manager.store.next_google_frontier_delay.return_value = 300
        manager.serp = Mock()
        manager.serp.content_fetch_enabled.return_value = False
        manager.queue = Mock()
        with patch('realtime.serp_worker.run_serp_campaign') as collect, patch('realtime.worker.subprocess.Popen', side_effect=AssertionError('no Scrapy')):
            manager.run_campaign(str(uuid4()))
        collect.assert_called_once()

    def test_inactive_tasks_leave_queue_and_disabled_active_tasks_remain_retryable(self):
        for state in ('stopped', 'paused', 'failed', 'active'):
            with self.subTest(state=state):
                manager = WorkerManager.__new__(WorkerManager)
                manager.config = Config()
                manager.store = Mock()
                manager.store.campaign.return_value = {'status':state, 'proxy_profile':'direct'}
                manager.store.is_local_campaign.return_value = True
                manager.console = Mock()
                manager.console.runnable.return_value = False
                manager.queue = Mock()
                cid = str(uuid4())
                manager.run_campaign(cid)
                if state == 'active':
                    manager.queue.schedule.assert_called_once_with(cid, 30)
                else:
                    manager.queue.schedule.assert_not_called()

    def test_turning_off_cancels_running_body_group_and_child_uses_effective_switch(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.config = Config(content_fetch_enabled=False)
        manager.stop = threading.Event()
        manager.store = Mock()
        manager.store.campaign.return_value = {'status': 'active', 'proxy_profile': 'direct'}
        manager.store.is_local_campaign.return_value = True
        manager.serp = Mock()
        manager.serp.content_fetch_enabled.side_effect = [True, False]
        manager.queue = Mock()
        child = Mock(pid=123456)
        child.poll.return_value = None
        def cancelled(timeout=None):
            child.returncode = -signal.SIGTERM
            child.poll.return_value = child.returncode
        child.wait.side_effect = cancelled
        with patch('realtime.worker.subprocess.Popen', return_value=child) as spawn, patch('realtime.worker.os.killpg') as kill, patch.object(manager.stop, 'wait', return_value=False):
            manager.run_campaign(str(uuid4()))
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.kwargs['env']['CONTENT_FETCH_ENABLED'], 'true')
        kill.assert_called_once_with(child.pid, signal.SIGTERM)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires real isolated PostgreSQL')
class SerpStoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ['TEST_DATABASE_URL']
        self.schema = 'serp_' + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema}')
        self.base = CampaignStore(self.dsn)
        self.cid = self.base.create_campaign('solar research', [], 1000000, 'direct', ['google_web'])
        self.serp = SerpStore(self.dsn)
        self.serp.initialize()

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def event(self, url='https://example.com/article', query='solar research', request_id=None):
        result, response = envelope(url, query)
        return dict(provider='openserp', query=query, page=1, language='en', results=[result], response=response,
                    elapsed_seconds=1.5, classification='results', http_status=200, error_code=None,
                    upstream_request_id=request_id or str(uuid4()), upstream_version='test', upstream_cache_status='BYPASS')

    def test_same_url_across_queries_counts_once_but_preserves_both_rankings(self):
        first = self.serp.record(self.cid, **self.event())
        second = self.serp.record(self.cid, **self.event(query='energy policy'))
        self.assertEqual(self.serp.request(first)['new_unique_links'], 1)
        self.assertEqual(self.serp.request(second)['new_unique_links'], 0)
        self.assertEqual(self.serp.request(second)['organic_results'][0]['snippet'], 'Actual search snippet.')
        self.assertEqual(len(self.serp.recent()), 2)
        self.assertEqual(self.serp.stats()['links']['total_new'], 1)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM pages').fetchone()['n'], 0)

    def test_google_tracking_tokens_do_not_create_new_links_but_content_parameters_do(self):
        base = 'https://example.com/article?page=3'
        first = self.serp.record(self.cid, **self.event(url=base+'&srsltid=one'))
        second = self.serp.record(self.cid, **self.event(url=base+'&srsltid=two', query='other query'))
        third = self.serp.record(self.cid, **self.event(url='https://example.com/article?page=4&srsltid=three'))
        self.assertEqual([self.serp.request(x)['new_unique_links'] for x in (first,second,third)], [1,0,1])
        self.assertEqual(self.serp.request(second)['organic_results'][0]['normalized_link'], base)
        self.assertEqual(self.serp.request(second)['organic_results'][0]['link'], base+'&srsltid=two')
        self.assertEqual(self.serp.stats()['links']['total_new'], 2)

    def test_legacy_tracking_repair_preserves_rows_and_originals_and_unions_sources(self):
        base = 'https://example.com/article?page=3'
        one = self.event(url=base+'&srsltid=one')
        two = self.event(url=base+'&srsltid=two', query='news query');two['source']='google_news'
        bare = self.event(url=base, query='bare query')
        with patch('realtime.serp_store.normalize_serp_url', side_effect=lambda url:url):
            first = self.serp.record(self.cid, **one)
            second = self.serp.record(self.cid, **two)
            third = self.serp.record(self.cid, **bare)
        originals = {x:self.serp.request(x) for x in (first,second,third)}
        known = 'https://example.com/known?srsltid=old'
        known_hash = hashlib.sha256(known.encode()).hexdigest()
        with self.base.connect() as db:
            db.execute('INSERT INTO serp_urls(url_hash,normalized_url,historical) VALUES(%s,%s,true)', (known_hash,known))
            before = db.execute('SELECT count(*) n FROM serp_urls').fetchone()['n']
            db.execute('DELETE FROM serp_schema_versions WHERE name=%s', (URL_NORMALIZATION_VERSION,))
        self.serp.initialize()
        self.assertEqual([self.serp.request(x)['new_unique_links'] for x in (first,second,third)], [1,0,0])
        stats = self.serp.stats()
        self.assertEqual(stats['links']['total_new'], 1)
        self.assertEqual(stats['sources']['google_news']['unique_new_urls'], 1)
        self.assertEqual(stats['sources']['google_news']['first_discovery_contribution'], 0)
        self.assertEqual(stats['sources']['google_web']['first_discovery_contribution'], 1)
        with self.base.connect() as db:
            self.assertGreaterEqual(db.execute('SELECT count(*) n FROM serp_urls').fetchone()['n'], before)
            for request_id, original in originals.items():
                saved = db.execute('SELECT normalization_original_response FROM serp_requests WHERE request_id=%s', (request_id,)).fetchone()['normalization_original_response']
                self.assertEqual(saved, original)
            old = db.execute('SELECT * FROM serp_urls WHERE url_hash=%s', (known_hash,)).fetchone()
            self.assertEqual(old['normalized_url'], known)
            self.assertTrue(old['historical'])
            self.assertIsNotNone(old['canonical_url_hash'])
            self.assertEqual(old['normalization_original']['row']['normalized_url'], known)
            self.assertEqual(db.execute('SELECT count(*) n FROM pages').fetchone()['n'], 0)
        future = self.serp.record(self.cid, **self.event(url='https://example.com/known?srsltid=changed'))
        self.assertEqual(self.serp.request(future)['new_unique_links'], 0)
        self.assertEqual(self.serp.record(self.cid, **two), second)
        self.serp.initialize()
        self.assertEqual(self.serp.stats()['links']['total_new'], 1)

    def test_retry_is_idempotent_and_identity_conflict_is_rejected(self):
        event = self.event()
        first = self.serp.record(self.cid, **event)
        self.assertEqual(self.serp.record(self.cid, **event), first)
        self.assertEqual(len(self.serp.recent()), 1)
        changed = self.event(url='https://example.com/other', request_id=event['upstream_request_id'])
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.serp.record(self.cid, **changed)

    def test_concurrent_workers_count_same_url_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(lambda _: self.serp.record(self.cid, **self.event()), range(4)))
        self.assertEqual(sum(self.serp.request(i)['new_unique_links'] for i in ids), 1)

    def test_write_failure_rolls_back_request_and_url_then_retry_succeeds(self):
        with self.base.connect() as db:
            db.execute("CREATE FUNCTION reject_serp_url() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$")
            db.execute("CREATE TRIGGER reject_serp BEFORE INSERT ON serp_urls FOR EACH ROW EXECUTE FUNCTION reject_serp_url()")
        event = self.event()
        with self.assertRaises(psycopg.Error):
            self.serp.record(self.cid, **event)
        self.assertEqual(self.serp.recent(), [])
        with self.base.connect() as db:
            db.execute('DROP TRIGGER reject_serp ON serp_urls')
        self.assertEqual(self.serp.request(self.serp.record(self.cid, **event))['new_unique_links'], 1)

    def test_existing_retained_url_is_seeded_not_counted_as_new(self):
        url = 'https://example.com/historical?utm_source=old'
        with self.base.connect() as db:
            db.execute('DELETE FROM serp_schema_versions')
            db.execute('INSERT INTO google_known_result_urls(url) VALUES(%s)', (url,))
        self.serp.initialize()
        request_id = self.serp.record(self.cid, **self.event(url='https://example.com/historical'))
        self.assertEqual(self.serp.request(request_id)['new_unique_links'], 0)

    def test_body_switch_persists_and_rejects_string_false(self):
        self.assertFalse(self.serp.content_fetch_enabled())
        with self.assertRaises(ValueError):
            self.serp.set_content_fetch('false')
        self.serp.set_content_fetch(True)
        self.assertTrue(SerpStore(self.dsn).content_fetch_enabled())
        self.serp.set_content_fetch(False)
        self.assertFalse(self.serp.content_fetch_enabled())

    def test_supply_does_not_reenqueue_running_campaigns_on_every_tick(self):
        queue = Mock()
        supply = SerpSupply(Config(database_url=self.dsn, default_proxy_profile='direct', serp_continuous_enabled=True), self.base, queue)
        supply.tick()
        self.assertEqual(queue.enqueue.call_count, 256)
        queue.reset_mock()
        supply.tick()
        queue.enqueue.assert_not_called()
