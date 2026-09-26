import json
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from realtime.config import Config
from realtime.discovery import GoogleBlocked
from realtime.experiment_store import ExperimentStore
from realtime.fast_experiment import (BodyPool, PipelineRunner, SearchRunner, archive_queries,
                                      dense_supply_plan, replenish_queries, restore_page_frontier, retry_delay,
                                      split_full_query, supply_plan, supply_query)
from realtime.fetcher import LiveFetcher, _json_ld_article, extract_text
from realtime.google_experiment import Runner, publication_metadata


class PipelineTests(unittest.TestCase):
    def test_body_dispatch_looks_past_a_busy_domain_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ExperimentStore(Path(tmp),create=True)
            try:
                store.set('deadline',time.time()+3600)
                with store.db:
                    store.db.executemany('INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?)',
                                         ((f'https://busy.example/{i}','AI',i,i) for i in range(600)))
                    store.db.execute('INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?)',
                                     ('https://free.example/article','AI',601,601))
                runner=PipelineRunner(store,Config(),Mock(),Path(tmp));pool=Mock(size=3,busy=2)
                def submit(row):
                    if row['url'].startswith('https://busy.example/'):
                        return False
                    pool.busy+=1
                    return True
                pool.submit.side_effect=submit
                runner._submit_bodies(pool)
                self.assertEqual(store.db.execute('SELECT state FROM urls WHERE url=?',('https://free.example/article',)).fetchone()[0],'fetching')
                self.assertEqual(pool.busy,3)
            finally:
                store.db.close()

    def test_empty_body_pool_clears_leaked_domain_leases(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ExperimentStore(Path(tmp),create=True)
            try:
                store.set('deadline',time.time()+3600)
                with store.db:
                    store.db.execute('INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?)',
                                     ('https://free.example/article','AI',1,1))
                runner=PipelineRunner(store,Config(),Mock(),Path(tmp))
                pool=Mock(size=1,busy=0,workers=[],spawn_errors=0)
                pool.domains=Counter({'stale.example':2})
                pool.submit.return_value=True
                runner._submit_bodies(pool)
                self.assertEqual(pool.domains, {})
                self.assertTrue(pool.submit.called)
                self.assertFalse(store.db.execute("SELECT key FROM settings WHERE key LIKE 'body_dispatch_stall_%'").fetchone())
            finally:
                store.db.close()

    def test_body_dispatch_stall_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ExperimentStore(Path(tmp),create=True)
            try:
                store.set('deadline',time.time()+3600)
                with store.db:
                    store.db.execute('INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?)',
                                     ('https://free.example/article','AI',1,1))
                runner=PipelineRunner(store,Config(),Mock(),Path(tmp))
                pool=Mock(size=1,busy=0,workers=[],spawn_errors=7)
                pool.domains=Counter()
                pool.submit.return_value=False
                runner._submit_bodies(pool)
                row=store.db.execute("SELECT value FROM settings WHERE key LIKE 'body_dispatch_stall_%'").fetchone()
                self.assertIsNotNone(row)
                self.assertIn('"spawn_errors": 7',row[0])
            finally:
                store.db.close()

    def test_body_dispatch_spreads_requests_across_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ExperimentStore(Path(tmp),create=True)
            try:
                store.set('deadline',time.time()+3600)
                with store.db:
                    for rank in range(4):
                        for index in range(3):
                            store.db.execute('INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?)',
                                             (f'https://domain{rank}.example/{index}','AI',rank*3+index,rank*3+index))
                runner=PipelineRunner(store,Config(),Mock(),Path(tmp))
                submitted=[]
                def submit(row):
                    submitted.append(row['url'])
                    pool.busy += 1
                    return True
                pool=Mock(size=4,busy=0,workers=[],spawn_errors=0)
                pool.submit.side_effect=submit
                runner._submit_bodies(pool)
                self.assertEqual(len(submitted),4)
                hosts={urlsplit(url).hostname for url in submitted}
                self.assertGreaterEqual(len(hosts),3)
            finally:
                store.db.close()

    def test_dense_supply_uses_proven_sites_and_combined_ai_query(self):
        plan = dense_supply_plan(Counter({'good.example':20,'weak.example':19}), ('zh',),
                                 datetime(2026, 1, 1, tzinfo=timezone.utc))
        query, language, _ = supply_query(plan, 0)
        self.assertEqual(plan['hosts'], ['good.example'])
        self.assertEqual(language, 'zh')
        self.assertIn('(人工智能 OR AI OR 大模型 OR ChatGPT OR 机器学习)', query)
        self.assertIn('after:2025-12-01 before:2026-01-01', query)

    def test_full_dated_pages_split_into_overlapping_smaller_windows_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                key = store.add_query('site','site:example.com (AI OR 人工智能) after:2024-02-01 before:2024-03-01','zh','AI',pages=3)
                row = store.db.execute('SELECT * FROM queries WHERE id=?',(key,)).fetchone()
                self.assertEqual(split_full_query(store,row,[{}]*9),[])
                children = split_full_query(store,row,[{}]*10)
                self.assertEqual(len(children),2)
                self.assertEqual([store.db.execute('SELECT count(*) FROM schedule WHERE query_id=?',(child,)).fetchone()[0] for child in children], [11, 11])
                queries=[store.db.execute('SELECT query FROM queries WHERE id=?',(child,)).fetchone()[0] for child in children]
                self.assertIn('after:2024-02-01 before:2024-02-16',queries[0])
                self.assertIn('after:2024-02-15 before:2024-03-01',queries[1])
                with store.db:
                    store.db.execute('UPDATE schedule SET due=123 WHERE query_id=?',(children[0],))
                self.assertEqual(split_full_query(store,row,[{}]*10),[])
                self.assertEqual(store.db.execute('SELECT min(due) FROM schedule WHERE query_id=?',(children[0],)).fetchone()[0],123)
                self.assertEqual(store.db.execute('SELECT count(*) FROM documents').fetchone()[0],0)
                leaf = dict(row, id='leaf',query='site:example.com AI after:2024-02-01 before:2024-02-03')
                self.assertEqual(split_full_query(store,leaf,[{}]*10),[])
            finally:
                store.db.close()

    def test_search_concurrency_tuning_preserves_inflight_pages_and_records_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                runner=PipelineRunner(store,Config(),Mock(),Path(tmp));runner.search_thread_count=runner.search_size
                original=runner.search_size
                store.set('search_workers',16)
                def start(kind):
                    self.assertEqual(kind,'search');runner.search_thread_count+=1
                with patch.object(runner,'_start_stage',side_effect=start):
                    runner._configure_searches()
                self.assertEqual(runner.search_thread_count,16)
                runner.search_inflight=12
                store.set('search_workers',4)
                runner._configure_searches()
                self.assertEqual(runner.search_inflight,12)
                self.assertEqual(runner._submit_searches(0),0)
                self.assertEqual(store.db.execute("SELECT count(*) FROM settings WHERE key LIKE 'search_resource_change_%'").fetchone()[0],2)
            finally:
                store.db.close()

    def test_lean_metadata_preserves_title_precedence_and_body_without_date_work(self):
        from trafilatura.deduplication import LRU_TEST
        paragraphs = ''.join(f'<p>人工智能系统研究第{i}个问题，讨论模型训练、推理效率、测试结果与工程实践。'
                              f'本段的具体编号为{i}，说明独立的研究观察和数据处理步骤。</p>' for i in range(20))
        heads = [('<meta property="og:title" content="OpenGraph &amp; 人工智能">', '<h1>Different H1</h1>'),
                 ('<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','headline':'JSON AI 标题'})+'</script>', '<h1>Another H1</h1>'),
                 ('', '<h1>人工智能正文标题</h1>'), ('', '')]
        for extra, h1 in heads:
            with self.subTest(extra=extra, h1=h1):
                raw = ('<html><head><meta charset="utf-8"><title>AI Article | Example</title>'+extra+
                       '</head><body><nav>Site navigation</nav><main><article>'+h1+paragraphs+
                       '</article></main><footer>Site footer</footer></body></html>').encode()
                LRU_TEST.clear()
                expected = extract_text(raw, 'https://example.com/article')
                LRU_TEST.clear()
                with patch('trafilatura.metadata.find_date', side_effect=AssertionError('unused date extraction')) as dates:
                    actual = extract_text(raw, 'https://example.com/article', lean_metadata=True)
                    dates.assert_not_called()
                self.assertEqual(actual, expected)
                self.assertGreater(len(actual[1]), 500)
        LRU_TEST.clear()

    def test_fast_parser_preserves_multibyte_metadata(self):
        body = '人工智能模型训练与推理。'*80
        script = json.dumps({'@type':'NewsArticle','headline':'中文标题','articleBody':body,
                             'datePublished':'2026-01-02T03:04:05+08:00'}, ensure_ascii=False)
        for encoding in ('utf-8', 'utf-16', 'gb18030'):
            raw = (f'<html><head><meta charset="{encoding}"></head><body><div><script type="application/ld+json">'+script+'</script></div></body></html>').encode(encoding)
            self.assertEqual(extract_text(raw, 'https://example.com', lean_metadata=True), ('中文标题', body))
            self.assertEqual(publication_metadata(raw, parser='lxml'), publication_metadata(raw))

    def test_due_bodies_skip_inflight_baselines_and_unmodified_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                with store.db:
                    store.db.executemany('INSERT INTO urls(url,state,first_seen,last_seen,last_fetch,next_fetch) VALUES(?,?,?,?,?,?)',
                                          [('https://a.example/'+state, state, 1, 2, fetched, due) for state, fetched, due in
                                           [('pending', 0, 0), ('baseline_skipped', 0, 0), ('fetching', 0, 0),
                                            ('success', 3, 0), ('failed', 1, 0), ('blocked', 1, 100)]])
                rows = store.due_urls(10, 50)
                self.assertEqual([r['state'] for r in rows], ['pending', 'failed'])
                full, light = store.counts(), store.counts(detailed=False)
                full.pop('by_family')
                light.pop('by_family')
                self.assertEqual(full, light)
            finally:
                store.db.close()

    def test_page_frontier_inherits_only_recent_success_and_is_restart_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = ExperimentStore(Path(tmp) / 'previous', create=True)
            current = ExperimentStore(Path(tmp) / 'current', create=True)
            now = time.time()
            try:
                key = current.add_query('site', 'site:example.com AI', 'zh', 'AI', pages=4)
                other = current.add_query('site', 'untouched', 'zh', 'AI', pages=1)
                failed_only = current.add_query('site', 'previously failed', 'zh', 'AI', pages=1)
                current.set('baseline_run_paths', [str(previous.path.parent)])
                with previous.db:
                    previous.db.executemany('INSERT INTO searches(query_id,page,finished,status) VALUES(?,?,?,?)',
                                            [(key, 1, now-60, 'success'), (key, 2, now-60, 'failed'),
                                             (key, 3, now-86401, 'success'), (key, 4, now+60, 'success'),
                                             (failed_only, 1, now-120, 'failed')])
                restore_page_frontier(current, now)
                due = dict(current.db.execute('SELECT page,due FROM schedule WHERE query_id=?', (key,)))
                self.assertEqual(due, {1: now-60+86400, 2: 0, 3: 0, 4: 0})
                self.assertEqual(current.due_query('site', now)['id'], other)
                self.assertEqual(current.db.execute('SELECT last_served FROM queries WHERE id=?', (failed_only,)).fetchone()[0], now-120)
                self.assertEqual(current.db.execute('SELECT due FROM schedule WHERE query_id=?', (failed_only,)).fetchone()[0], 0)
                restore_page_frontier(current, now+1)
                self.assertEqual(current.get('page_frontier_restored')['pages_deferred'], 1)
                self.assertEqual(current.get('page_frontier_restored')['at'], now)
            finally:
                previous.db.close()
                current.db.close()

    def test_storage_budget_counts_nested_export_once_and_preserves_free_space_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            output = Path(tmp) / 'export'
            output.mkdir()
            (output / 'sample').write_bytes(b'x' * 100000)
            runner = Runner(store, Config(), Mock(), output)
            try:
                used = sum(p.stat().st_size for p in Path(tmp).rglob('*') if p.is_file())
                store.set('storage_budget_gib', (used+50000) / 1024**3)
                with patch('realtime.google_experiment.shutil.disk_usage', return_value=Mock(free=3*1024**3)):
                    self.assertTrue(runner.storage_ok())
                    store.set('storage_budget_gib', .000001)
                    self.assertFalse(runner.storage_ok())
                store.set('storage_budget_gib', 64)
                with patch('realtime.google_experiment.shutil.disk_usage', return_value=Mock(free=1024**3)):
                    self.assertFalse(runner.storage_ok())
            finally:
                store.db.close()

    def test_query_supply_interleaves_hosts_and_exhausts_without_future_months(self):
        plan = supply_plan(Counter({'a.example': 4, 'b.example': 3, 'skip.example': 2}),
                           ('zh',), datetime(2026, 1, 1, tzinfo=timezone.utc))
        first, second = supply_query(plan, 0), supply_query(plan, 1)
        self.assertIn('site:a.example ', first[0])
        self.assertIn('site:b.example ', second[0])
        self.assertEqual(first[1:], second[1:])
        self.assertIn('after:2025-12-01 before:2026-01-01', first[0])
        total = len(plan['hosts']) * len(plan['topics']) * plan['months']
        self.assertIn('after:2016-01-01 before:2016-02-01', supply_query(plan, total-1)[0])
        self.assertIsNone(supply_query(plan, total))
        self.assertIsNone(supply_query(supply_plan(Counter(), ('zh',), datetime.now(timezone.utc)), 0))

    def test_query_supply_resumes_atomically_and_keeps_existing_page_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            plan = supply_plan(Counter({'a.example': 4, 'b.example': 3}),
                               ('zh',), datetime(2026, 1, 1, tzinfo=timezone.utc))
            store.set('query_supply_cursor', plan)
            query, language, topic = supply_query(plan, 0)
            key = store.add_query('site', query, language, topic, pages=1)
            with store.db:
                store.db.execute('UPDATE schedule SET due=123 WHERE query_id=?', (key,))
            self.assertEqual(replenish_queries(store, low_water=5, batch_size=2), 1)
            self.assertEqual(store.db.execute('SELECT due FROM schedule WHERE query_id=?', (key,)).fetchone()[0], 123)
            store.db.close()
            store = ExperimentStore(Path(tmp))
            try:
                self.assertEqual(store.get('query_supply_cursor')['cursor'], 2)
                self.assertEqual(replenish_queries(store, low_water=5, batch_size=3), 3)
                self.assertEqual(replenish_queries(store, low_water=5, batch_size=3), 0)
                self.assertEqual(store.get('query_supply_cursor')['cursor'], 5)
                self.assertEqual(store.db.execute('SELECT count(*) FROM queries').fetchone()[0], 5)
                self.assertEqual(store.db.execute('SELECT count(*) FROM schedule').fetchone()[0], 55)
            finally:
                store.db.close()

    def test_query_schedule_serves_untouched_queries_then_lowest_due_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                first = store.add_query('site', 'first query', 'zh', 'AI', pages=3)
                second = store.add_query('site', 'second query', 'zh', 'AI', pages=3)
                with store.db:
                    store.db.execute('UPDATE queries SET last_served=10 WHERE id=?', (first,))
                    store.db.execute('UPDATE schedule SET due=100 WHERE query_id=? AND page=1', (second,))
                row = store.due_query('site', 20)
                self.assertEqual((row['id'], row['page']), (second, 2))
            finally:
                store.db.close()

    def test_selective_metadata_parse_preserves_unicode_and_article_graph(self):
        body = '人工智能 模型训练与推理。' * 80
        payload = {'@graph': [{'@type': 'NewsArticle', 'headline': '中文标题', 'articleBody': body,
                               'datePublished': '2026-01-02T03:04:05+08:00'}]}
        raw = ('<html><body><div><p>unrelated layout</p><script type="application/ld+json">' +
               json.dumps(payload, ensure_ascii=False) + '</script></div></body></html>').encode('utf-16')
        self.assertEqual(_json_ld_article(raw, 'https://example.com'), ('中文标题', body))
        self.assertEqual(publication_metadata(raw), ('2026-01-02T03:04:05+08:00', 'jsonld:datePublished'))

    def test_archive_supply_is_google_query_only_and_handles_year_rollover(self):
        queries = list(archive_queries(Counter({'good.example': 3, 'unproven.example': 2}), ('zh',),
                                       datetime(2026, 1, 15, tzinfo=timezone.utc)))
        self.assertEqual(len(queries), 165)
        self.assertEqual(len({q[0] for q in queries}), 165)
        self.assertTrue(all(q.startswith('site:good.example ') and language == 'zh' for q, language, _ in queries))
        self.assertTrue(any('after:2025-12-01 before:2026-01-01' in q for q, _, _ in queries))
        self.assertTrue(any('after:2018-01-01 before:2019-01-01' in q for q, _, _ in queries))

    def test_yield_plan_reserves_twenty_percent_for_other_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('deadline', time.time()+7200)
                store.set('query_plan', 'yield')
                store.set('search_workers', 4)
                for family in ('site', 'topic', 'event', 'recent'):
                    for i in range(20):
                        store.add_query(family, f'{family} query {i}', 'zh', '人工智能', pages=1)
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                index, counts = 0, Counter()
                for _ in range(5):
                    index = runner._submit_searches(index)
                    while not runner.jobs.empty():
                        counts[runner.jobs.get_nowait()['family']] += 1
                        runner.search_inflight -= 1
                self.assertEqual(counts, {'site': 16, 'recent': 2, 'topic': 1, 'event': 1})
            finally:
                store.db.close()

    def test_search_family_weights_are_runtime_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('deadline', time.time()+7200)
                store.set('query_plan', 'yield')
                store.set('search_workers', 4)
                store.set('search_family_weights', ('recent', 'topic', 'event'))
                for family in ('site', 'topic', 'event', 'recent'):
                    store.add_query(family, f'{family} query', 'zh', '人工智能', pages=1)
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                index = runner._submit_searches(0)
                self.assertEqual([runner.jobs.get_nowait()['family'] for _ in range(3)], ['recent', 'topic', 'event'])
                self.assertEqual(index, 0)
            finally:
                store.db.close()

    def test_empty_primary_with_cooling_fallback_does_not_stop_other_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('topic', '人工智能', 'zh', '人工智能')
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[])
                def discover(*args):
                    client.attempts.append({'provider': 'wml', 'success': True, 'results': 0})
                    raise GoogleBlocked('google_provider_cooling')
                client._discover_google_page.side_effect = discover
                runner = Runner(store, Config(), Mock(), Path(tmp))
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertEqual(store.get('search_cooling_until', 0), 0)
                self.assertIsNone(store.cached(key, 1, time.time()))
                self.assertGreater(store.db.execute('SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)).fetchone()[0], time.time()+1700)
                self.assertIsNotNone(store.due_query('topic', time.time()))
            finally:
                store.db.close()

    def test_all_providers_cooling_still_delays_searches(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('topic', '人工智能', 'zh', '人工智能')
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[{'provider': 'wml', 'success': True, 'results': 10}])
                client._discover_google_page.side_effect = GoogleBlocked('google_provider_cooling')
                runner = Runner(store, Config(), Mock(), Path(tmp))
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertGreater(store.get('search_cooling_until'), time.time()+55)
            finally:
                store.db.close()

    def test_pipeline_prioritizes_unvisited_pages_after_first_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('site', 'site:example.com 人工智能', 'zh', '人工智能')
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[])
                client._discover_google_page.return_value = [Mock(url='https://example.com/article', title='人工智能')]
                runner = SearchRunner(store, Config(), Mock(), Path(tmp))
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertEqual(store.due_query('site', time.time()+3601)['page'], 2)
                self.assertGreater(store.db.execute('SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)).fetchone()[0], time.time()+86300)
            finally:
                store.db.close()

    def test_pipeline_failure_still_retries_before_daily_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('site', 'site:example.com 人工智能', 'zh', '人工智能')
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[])
                client._discover_google_page.side_effect = GoogleBlocked('google_timeout')
                runner = SearchRunner(store, Config(), Mock(), Path(tmp))
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertEqual(store.due_query('site', time.time()+61)['page'], 1)
            finally:
                store.db.close()

    def test_proxy_exhaustion_pauses_dispatch_until_next_compatible_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('recent', 'AI after:2026-09-01', 'en', 'AI')
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[], proxy_wait_seconds=120)
                client._discover_google_page.side_effect = GoogleBlocked('google_proxy_unavailable')
                shared_pool = Mock()
                runner = SearchRunner(store, Config(), Mock(), Path(tmp), proxy_pool=shared_pool)
                self.assertIs(runner.pool, shared_pool)
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertGreater(store.get('search_cooling_until'), time.time()+115)
                due = store.db.execute(
                    'SELECT due FROM schedule WHERE query_id=? AND page=1', (key,),
                ).fetchone()[0]
                self.assertGreater(due, time.time()+115)
            finally:
                store.db.close()

    def test_global_circuit_remains_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('state', 'running')
                store.set('deadline', time.time()+7200)
                production = Mock()
                production.acquire_discovery_slot.return_value = {'allowed': False, 'wait': 120}
                runner = Runner(store, Config(), production, Path(tmp))
                self.assertFalse(runner.slot('google_web', 1)['allowed'])
                self.assertGreater(store.get('search_cooling_until'), time.time()+115)
            finally:
                store.db.close()

    def test_connection_sessions_reuse_origin_and_close_on_eviction(self):
        with patch('realtime.fetcher.requests.Session') as factory:
            factory.side_effect = lambda: Mock()
            fetcher = LiveFetcher('test', reuse_sessions=True)
            first = fetcher._session('https://one.example/a')
            self.assertIs(first, fetcher._session('https://one.example/b'))
            self.assertIsNot(first, fetcher._session('https://two.example/a'))
            for i in range(64):
                fetcher._session(f'https://host-{i}.example/')
            self.assertEqual(len(fetcher._sessions), 64)
            first.close.assert_called_once()
            fetcher.close()
            self.assertFalse(fetcher._sessions)

    def test_explicit_publication_formats_without_inventing_timezone(self):
        self.assertEqual(publication_metadata(b'<meta name="parsely-pub-date" content="2026-09-01T10:00:00Z">')[0],
                         '2026-09-01T10:00:00+00:00')
        self.assertEqual(publication_metadata(b'<meta name="pubdate" content="Tue, 01 Sep 2026 10:00:00 +0800">')[0],
                         '2026-09-01T10:00:00+08:00')
        self.assertIsNone(publication_metadata(b'<meta name="pubdate" content="2026-09-01 10:00:00">')[0])
        self.assertIsNone(publication_metadata(b'<meta name="last-modified" content="2026-09-01T10:00:00Z">')[0])

    def test_nonproductive_pages_do_not_retry_hourly(self):
        self.assertEqual(retry_delay({'status': 'blocked'}), 86400)
        self.assertEqual(retry_delay({'error': '不支持的内容类型: application/pdf'}), 86400)
        self.assertEqual(retry_delay({'error': 'HTTP 404'}), 86400)
        self.assertEqual(retry_delay({'error': 'HTTP 403'}), 21600)
        self.assertEqual(retry_delay({'error': 'ReadTimeout'}), 3600)
        self.assertEqual(retry_delay({'document': {'content': 'valid'}}), 86400)

    def _protocol(self, sleep=0):
        return ('import sys,json,time,os\nfor line in sys.stdin:\n'
                f' time.sleep({sleep})\n'
                ' row=json.loads(line)\n'
                ' print(json.dumps({"requested_url":row["url"],"status":"success","pid":os.getpid()}),flush=True)\n')

    def _wait(self, pool):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            records = pool.poll(.05)
            if records:
                return records
        self.fail('worker did not return within deadline')

    def test_worker_reused_and_busy_domain_does_not_block_another_host(self):
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            return real_popen([sys.executable, '-u', '-c', self._protocol(.1)], **kwargs)
        pool = BodyPool(size=2, per_domain=1)
        try:
            with patch('realtime.fast_experiment.subprocess.Popen', side_effect=launch):
                self.assertTrue(pool.submit({'url': 'https://one.example/a'}))
                self.assertFalse(pool.submit({'url': 'https://one.example/b'}))
                self.assertTrue(pool.submit({'url': 'https://two.example/a'}))
                records = []
                while len(records) < 2:
                    records.extend(self._wait(pool))
                pid = next(r['pid'] for r in records if 'one.example' in r['requested_url'])
                self.assertTrue(pool.submit({'url': 'https://one.example/b'}))
                self.assertEqual(self._wait(pool)[0]['pid'], pid)
                self.assertEqual(pool.busy, 0)
        finally:
            pool.close()

    def test_worker_recycling_preserves_result_and_releases_domain_slot(self):
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            return real_popen([sys.executable, '-u', '-c', self._protocol()], **kwargs)
        for task_limit, memory_due in [(1, False), (200, True)]:
            with self.subTest(task_limit=task_limit, memory_due=memory_due):
                pool = BodyPool(size=1, per_domain=1, max_tasks=task_limit)
                try:
                    with patch('realtime.fast_experiment.subprocess.Popen', side_effect=launch), \
                         patch('realtime.fast_experiment.Path.read_text', return_value='VmRSS: 999999 kB' if memory_due else 'VmRSS: 1 kB'):
                        self.assertTrue(pool.submit({'url': 'https://one.example/a'}))
                        first = self._wait(pool)[0]
                        self.assertEqual(first['status'], 'success')
                        self.assertEqual(len(pool.workers), 0)
                        self.assertEqual(pool.domains['one.example'], 0)
                        self.assertTrue(pool.submit({'url': 'https://one.example/b'}))
                        second = self._wait(pool)[0]
                        self.assertNotEqual(first['pid'], second['pid'])
                finally:
                    pool.close()

    def test_pool_shrink_drains_inflight_tasks_and_growth_reopens_capacity(self):
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            return real_popen([sys.executable, '-u', '-c', self._protocol(.1)], **kwargs)
        pool = BodyPool(size=2, per_domain=1)
        try:
            with patch('realtime.fast_experiment.subprocess.Popen', side_effect=launch):
                self.assertTrue(pool.submit({'url': 'https://one.example/a'}))
                self.assertTrue(pool.submit({'url': 'https://two.example/a'}))
                pool.resize(1, 1, 128)
                self.assertEqual(pool.busy, 2)
                records = []
                while len(records) < 2:
                    records.extend(self._wait(pool))
                self.assertEqual({r['requested_url'] for r in records}, {'https://one.example/a', 'https://two.example/a'})
                self.assertEqual(pool.busy, 0)
                self.assertEqual(len(pool.workers), 1)
                pool.resize(2, 1, 192)
                self.assertTrue(pool.submit({'url': 'https://one.example/b'}))
                self.assertTrue(pool.submit({'url': 'https://two.example/b'}))
                with self.assertRaises(ValueError):
                    pool.resize(1000, 1, 192)
        finally:
            pool.close()

    def test_live_body_tuning_keeps_per_domain_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            pool = BodyPool(size=2, per_domain=1)
            try:
                store.set('body_workers', 8)
                # A value from an old ledger must not create workers that only
                # wait behind the fetcher's per-host lock.
                store.set('body_per_domain', 2)
                store.set('body_max_rss_mib', 192)
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                runner._configure_pool(pool)
                self.assertEqual((pool.size, pool.per_domain, pool.max_rss_mib), (8, 1, 192))
                with self.assertRaises(ValueError):
                    pool.resize(8, 2, 192)
            finally:
                pool.close()
                store.db.close()

    def test_live_body_tuning_is_recorded_without_changing_fixed_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            pool = BodyPool(size=32)
            try:
                end = time.time()+86400
                store.set('deadline', end)
                store.set('body_workers', 40)
                store.set('body_max_rss_mib', 128)
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                runner._configure_pool(pool)
                runner._configure_pool(pool)
                self.assertEqual((pool.size, pool.per_domain, pool.max_rss_mib), (40, 1, 128))
                self.assertEqual(store.get('deadline'), end)
                changes = list(store.db.execute("SELECT value FROM settings WHERE key LIKE 'body_resource_change_%'"))
                self.assertEqual(len(changes), 1)
                self.assertEqual(json.loads(changes[0][0])['before'], [32, 1, 192])
                self.assertEqual(json.loads(changes[0][0])['after'], [40, 1, 128])
            finally:
                pool.close()
                store.db.close()

    def test_hung_worker_killed_and_slot_recovers(self):
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            return real_popen([sys.executable, '-u', '-c', self._protocol(10)], **kwargs)
        pool = BodyPool(size=1, deadline=.2)
        try:
            with patch('realtime.fast_experiment.subprocess.Popen', side_effect=launch):
                pool.submit({'url': 'https://one.example/a'})
                process = pool.workers[0]['process']
                result = self._wait(pool)[0]
                self.assertEqual(result['error'], 'body_hard_deadline')
                self.assertIsNotNone(process.poll())
                self.assertEqual(pool.busy, 0)
                self.assertEqual(pool.domains['one.example'], 0)
                self.assertFalse(pool.workers)
        finally:
            pool.close()

    def test_baseline_urls_skipped_before_network_and_search_leases_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('deadline', time.time()+7200)
                store.set('state', 'running')
                store.set('search_workers', 3)
                key = store.add_query('topic', '人工智能', 'zh', '人工智能')
                row = store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone()
                store.search(row, 1, time.time(), [{'url': 'https://old.example/a', 'title': 'old'},
                                                 {'url': 'https://new.example/a', 'title': 'new'}], [])
                store.baseline(['https://old.example/a'])
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                pool = Mock(busy=0, size=24)
                runner._submit_bodies(pool)
                self.assertEqual(pool.submit.call_count, 1)
                self.assertEqual(pool.submit.call_args.args[0]['url'], 'https://new.example/a')
                self.assertEqual(store.db.execute("SELECT state FROM urls WHERE url='https://old.example/a'").fetchone()[0], 'baseline_skipped')
                runner._submit_searches(0)
                jobs = []
                while not runner.jobs.empty():
                    jobs.append(runner.jobs.get_nowait())
                self.assertEqual(len(jobs), 3)
                self.assertEqual(len({(j['id'], j['page']) for j in jobs}), 3)
            finally:
                store.db.close()

    def test_body_pool_survives_process_spawn_failure(self):
        pool = BodyPool(size=1, per_domain=1)
        try:
            with patch('realtime.fast_experiment.subprocess.Popen', side_effect=OSError(24, 'Too many open files')):
                self.assertFalse(pool.submit({'url': 'https://one.example/a'}))
                self.assertEqual(pool.busy, 0)
                self.assertEqual(pool.domains['one.example'], 0)
        finally:
            pool.close()


if __name__ == '__main__':
    unittest.main()
