import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from realtime.config import Config
from realtime.discovery import GoogleBlocked
from realtime.experiment_store import ExperimentStore
from realtime.fast_experiment import (BodyPool, PipelineRunner, SearchRunner,
                                      adaptive_family_schedule,
                                      dense_supply_plan, ranked_supply_domains, replenish_queries,
                                      restore_page_frontier, retry_delay,
                                      split_full_query, supply_plan, supply_query,
                                      weighted_family_schedule)
from realtime.fetcher import LiveFetcher, _json_ld_article, extract_text
from realtime.google_experiment import Runner, publication_metadata


class PipelineTests(unittest.TestCase):
    def test_family_schedule_adapts_to_measured_yield_and_keeps_fairness(self):
        schedule, audit = weighted_family_schedule({
            'topic': (120, 60), 'event': (30, 60),
            'site': (600, 600), 'recent': (150, 75),
        })

        self.assertEqual(len(schedule), 20)
        self.assertEqual(set(schedule), {'topic', 'event', 'site', 'recent'})
        self.assertGreater(audit['allocations']['topic'], audit['allocations']['event'])
        self.assertGreater(audit['allocations']['recent'], audit['allocations']['site'])
        self.assertLessEqual(max(schedule.count(name) for name in set(schedule)), 8)
        self.assertGreaterEqual(min(schedule.count(name) for name in set(schedule)), 2)

    def test_adaptive_family_schedule_optimizes_accepted_documents_and_audits_new(self):
        store = Mock()
        store.db.execute.side_effect = [
            [('topic', 30), ('event', 20), ('site', 200), ('recent', 40)],
            [('topic', 90), ('event', 10), ('site', 200), ('recent', 80)],
            [('topic', 9), ('event', 8), ('site', 160), ('recent', 64)],
        ]

        schedule, audit = adaptive_family_schedule(store)

        self.assertEqual(len(schedule), 20)
        self.assertEqual(audit['observations']['topic'], (9, 30))
        self.assertEqual(audit['new_documents']['topic'], 90)
        self.assertEqual(
            audit['optimization_metric'],
            'accepted_unique_documents_per_success_page',
        )
        self.assertGreater(audit['allocations']['recent'], audit['allocations']['topic'])

    def test_existing_ledger_adds_query_lease_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'experiment.sqlite3'
            connection = sqlite3.connect(path)
            connection.execute(
                'CREATE TABLE queries(id TEXT PRIMARY KEY,family TEXT,query TEXT,language TEXT,'
                'topic TEXT,enabled INTEGER DEFAULT 1,last_served REAL DEFAULT 0)'
            )
            connection.execute(
                'CREATE TABLE documents(id INTEGER PRIMARY KEY,url TEXT,canonical TEXT,hash TEXT,'
                'finished REAL,quality TEXT,classification TEXT,document TEXT,status TEXT,'
                'error TEXT,seconds REAL)'
            )
            connection.commit()
            connection.close()
            store = ExperimentStore(Path(tmp))
            try:
                columns = {row[1] for row in store.db.execute('PRAGMA table_info(queries)')}
                self.assertIn('lease_until', columns)
                document_columns = {
                    row[1] for row in store.db.execute('PRAGMA table_info(documents)')
                }
                self.assertIn('published_at', document_columns)
                self.assertIn('publication_source', document_columns)
            finally:
                store.db.close()

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

    def test_dense_supply_uses_proven_sites_and_combined_ai_query(self):
        plan = dense_supply_plan(Counter({'good.example':20,'weak.example':19}), ('zh',),
                                 datetime(2026, 1, 1, tzinfo=timezone.utc))
        query, language, _ = supply_query(plan, 0)
        self.assertEqual(plan['hosts'], ['good.example'])
        self.assertEqual(language, 'zh')
        self.assertIn('(人工智能 OR AI OR 大模型 OR ChatGPT OR 机器学习)', query)
        self.assertIn('after:2025-12-01 before:2026-01-01', query)

    def test_supply_domains_require_valid_volume_then_prioritize_deliverable_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ExperimentStore(root / 'first', create=True)
            second = ExperimentStore(root / 'second', create=True)
            try:
                def add(store, rows):
                    with store.db:
                        for identifier, url, status in rows:
                            store.db.execute(
                                "INSERT INTO documents(id,canonical,quality,classification) "
                                "VALUES(?,?,'[]','new')", (identifier, url),
                            )
                            if status is not None:
                                store.db.execute(
                                    'INSERT INTO outbox(document_id,status) VALUES(?,?)',
                                    (identifier, status),
                                )
                add(first, [
                    (1, 'https://good.example/1', 'accepted'),
                    (2, 'https://good.example/2', 'accepted'),
                    (3, 'https://poor.example/1', 'accepted'),
                    (4, 'https://poor.example/2', 'blocked_missing_publication'),
                    (5, 'https://poor.example/3', 'blocked_missing_publication'),
                    (6, 'https://mixed.example/1', 'accepted'),
                    (7, 'https://mixed.example/2', 'accepted'),
                    (8, 'https://mixed.example/3', 'blocked_missing_publication'),
                    (9, 'https://body.example/1', None),
                    (10, 'https://body.example/2', None),
                    (11, 'https://body.example/3', None),
                    (12, 'https://body.example/4', None),
                ])
                add(second, [
                    (1, 'https://good.example/1', 'blocked_missing_publication'),
                    (2, 'https://good.example/3', 'accepted'),
                ])
                scores, audit = ranked_supply_domains([root / 'first', root / 'second'])
                self.assertEqual(
                    scores['valid'],
                    Counter({'body.example': 4, 'good.example': 3,
                             'mixed.example': 3, 'poor.example': 3}),
                )
                self.assertEqual(
                    scores['deliverable'],
                    Counter({'good.example': 3, 'mixed.example': 2,
                             'poor.example': 1}),
                )
                plan = supply_plan(scores, ('zh',), datetime(2026, 1, 1, tzinfo=timezone.utc))
                self.assertEqual(
                    plan['hosts'],
                    ['good.example', 'mixed.example', 'poor.example', 'body.example'],
                )
                self.assertEqual(audit['unique_canonicals'], 13)
                self.assertEqual(audit['accepted'], 6)
                self.assertEqual(audit['blocked_missing_publication'], 3)
                self.assertEqual(audit['other_not_accepted'], 4)
                self.assertEqual(len(audit['loaded_ledgers']), 2)
                self.assertEqual(audit['skipped_ledgers'], [])
            finally:
                first.db.close()
                second.db.close()

    def test_supply_domain_ranking_skips_unreadable_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = ExperimentStore(root / 'valid', create=True)
            broken = root / 'broken'
            broken.mkdir()
            (broken / 'experiment.sqlite3').write_bytes(b'not a sqlite database')
            try:
                with valid.db:
                    valid.db.execute(
                        "INSERT INTO documents(id,canonical,quality,classification) "
                        "VALUES(1,'https://valid.example/1','[]','new')"
                    )
                scores, audit = ranked_supply_domains([root / 'missing', broken, root / 'valid'])
                self.assertEqual(scores['valid'], Counter({'valid.example': 1}))
                self.assertEqual(scores['deliverable'], Counter())
                self.assertEqual(len(audit['loaded_ledgers']), 1)
                self.assertEqual(
                    [item['error'] for item in audit['skipped_ledgers']],
                    ['OperationalError', 'DatabaseError'],
                )
            finally:
                valid.db.close()

    def test_full_dated_pages_split_into_overlapping_smaller_windows_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                key = store.add_query('site','site:example.com (AI OR 人工智能) after:2024-02-01 before:2024-03-01','zh','AI',pages=3)
                row = store.db.execute('SELECT * FROM queries WHERE id=?',(key,)).fetchone()
                self.assertEqual(split_full_query(store,row,[{}]*9),[])
                children = split_full_query(store,row,[{}]*10)
                self.assertEqual(len(children),2)
                self.assertTrue(all(store.db.execute(
                    'SELECT count(*) FROM schedule WHERE query_id=?', (child,)
                ).fetchone()[0] == 11 for child in children))
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
                                            ('canonical_skipped', 0, 0), ('success', 3, 0),
                                            ('failed', 1, 0), ('blocked', 1, 100)]])
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
            broken = Path(tmp) / 'broken'
            broken.mkdir()
            (broken / 'experiment.sqlite3').write_bytes(b'not a sqlite database')
            now = time.time()
            try:
                key = current.add_query('site', 'site:example.com AI', 'zh', 'AI', pages=4)
                other = current.add_query('site', 'untouched', 'zh', 'AI', pages=1)
                failed_only = current.add_query('site', 'previously failed', 'zh', 'AI', pages=1)
                current.set('baseline_run_paths', [
                    str(Path(tmp) / 'missing'), str(broken), str(previous.path.parent),
                ])
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
                audit = current.get('page_frontier_restored')
                self.assertEqual(audit['pages_deferred'], 1)
                self.assertEqual(audit['at'], now)
                self.assertEqual(len(audit['loaded_ledgers']), 1)
                self.assertEqual(
                    [item['error'] for item in audit['skipped_ledgers']],
                    ['OperationalError', 'DatabaseError'],
                )
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
                self.assertEqual(replenish_queries(store, low_water=5, batch_size=3), 0)
                with store.db:
                    store.db.execute(
                        "INSERT INTO searches(query_id,page,finished,status) "
                        "SELECT s.query_id,s.page,1,'failed' FROM schedule s",
                    )
                self.assertEqual(replenish_queries(
                    store, low_water=5, batch_size=3, retry_backlog_limit=1
                ), 0)
                self.assertEqual(replenish_queries(store, low_water=5, batch_size=3), 3)
                self.assertEqual(replenish_queries(store, low_water=5, batch_size=3), 0)
                self.assertEqual(store.get('query_supply_cursor')['cursor'], 5)
                self.assertEqual(store.db.execute('SELECT count(*) FROM queries').fetchone()[0], 5)
                self.assertEqual(store.db.execute('SELECT count(*) FROM schedule').fetchone()[0], 45)
            finally:
                store.db.close()

    def test_fresh_inherited_success_does_not_block_next_query_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                plan = supply_plan(Counter({'a.example': 4}), ('zh',),
                                   datetime(2026, 1, 1, tzinfo=timezone.utc))
                store.set('query_supply_cursor', plan)
                inherited = store.add_query('site', 'site:inherited.example AI', 'zh', 'AI', pages=11)
                with store.db:
                    store.db.execute('UPDATE schedule SET due=? WHERE query_id=?',
                                     (time.time() + 3600, inherited))
                self.assertEqual(replenish_queries(store, batch_size=2), 2)
                self.assertEqual(store.db.execute(
                    'SELECT count(*) FROM schedule WHERE query_id<>?', (inherited,)
                ).fetchone()[0], 22)
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

    def test_query_claim_is_cross_connection_single_keyword_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = ExperimentStore(Path(tmp), create=True)
            second = ExperimentStore(Path(tmp))
            try:
                now = time.time()
                key = first.add_query('site', 'first query', 'zh', 'AI', pages=3)
                claimed = first.claim_due_query('site', now)
                self.assertEqual(claimed['id'], key)
                other = first.add_query('site', 'second query', 'zh', 'AI', pages=1)
                concurrent = second.claim_due_query('site', now)
                self.assertEqual(concurrent['id'], other)
                self.assertNotEqual(claimed['id'], concurrent['id'])

                first.search(claimed, claimed['page'], now, [], [])
                next_page = second.claim_due_query('site', now + 1)
                self.assertEqual(next_page['id'], key)
                self.assertEqual(next_page['page'], 2)
            finally:
                first.db.close()
                second.db.close()

    def test_query_claim_can_reserve_capacity_for_pages_two_through_eleven(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                continued = store.add_query('site', 'site:continued.example AI', 'zh', 'AI')
                untouched = store.add_query('site', 'site:untouched.example AI', 'zh', 'AI')
                with store.db:
                    store.db.execute(
                        "INSERT INTO searches(query_id,page,finished,status) VALUES(?,?,?,?)",
                        (continued, 1, 900, 'success'),
                    )
                    store.db.execute(
                        'UPDATE queries SET last_served=? WHERE id=?', (900, continued),
                    )
                    store.db.execute(
                        'UPDATE schedule SET due=? WHERE query_id=? AND page=1',
                        (900 + 86400, continued),
                    )

                fresh = store.claim_due_query('site', 1000)
                self.assertEqual(fresh['id'], untouched)
                with store.db:
                    store.db.execute(
                        'UPDATE queries SET lease_until=0,last_served=0 WHERE id=?',
                        (untouched,),
                    )
                    store.db.execute(
                        'UPDATE schedule SET due=0 WHERE query_id=? AND page=?',
                        (untouched, fresh['page']),
                    )
                deeper = store.claim_due_query(
                    'site', 1000, prefer_continuation=True,
                )
                self.assertEqual(deeper['id'], continued)
                self.assertEqual(deeper['page'], 2)
            finally:
                store.db.close()

    def test_search_dispatch_prioritizes_global_page_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('search_workers', 2)
                first = store.add_query('topic', 'first query', 'zh', 'AI', pages=11)
                second = store.add_query('event', 'second query', 'zh', 'AI', pages=11)
                with store.db:
                    for query_id in (first, second):
                        store.db.execute(
                            "INSERT INTO searches(query_id,page,finished,status) "
                            "VALUES(?,1,900,'success')", (query_id,),
                        )
                        store.db.execute(
                            'UPDATE schedule SET due=? WHERE query_id=? AND page=1',
                            (93600, query_id),
                        )
                    store.db.execute('UPDATE queries SET last_served=900')

                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                runner._submit_searches(0)

                jobs = [runner.jobs.get_nowait() for _ in range(runner.search_size)]
                self.assertEqual({row['page'] for row in jobs}, {2})
                self.assertEqual(len({row['family'] for row in jobs}), 2)
            finally:
                store.db.close()

    def test_query_claim_recovers_after_expiry_without_stale_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = ExperimentStore(Path(tmp), create=True)
            second = ExperimentStore(Path(tmp))
            try:
                now = time.time()
                key = first.add_query('site', 'first query', 'zh', 'AI', pages=1)
                stale = first.claim_due_query('site', now, lease_seconds=1)
                self.assertIsNone(second.claim_due_query('site', now + .5))
                recovered = second.claim_due_query('site', now + 2, lease_seconds=300)
                self.assertEqual(recovered['id'], key)
                reserved_due = first.db.execute(
                    'SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)
                ).fetchone()[0]

                first.search(stale, stale['page'], now, [], [], 'google_timeout')
                lease = first.db.execute('SELECT lease_until FROM queries WHERE id=?', (key,)).fetchone()[0]
                self.assertEqual(lease, recovered['_lease_until'])
                self.assertEqual(first.db.execute(
                    'SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)
                ).fetchone()[0], reserved_due)
                second.search(recovered, recovered['page'], now + 2, [], [])
                self.assertEqual(first.db.execute(
                    'SELECT lease_until FROM queries WHERE id=?', (key,)
                ).fetchone()[0], 0)
            finally:
                first.db.close()
                second.db.close()

    def test_due_failed_page_precedes_new_query_without_starving_supply(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                failed = store.add_query('site', 'failed query', 'zh', 'AI', pages=1)
                store.add_query('site', 'new query', 'zh', 'AI', pages=1)
                with store.db:
                    store.db.execute(
                        "INSERT INTO searches(query_id,page,finished,status) VALUES(?,?,?,'failed')",
                        (failed, 1, time.time()),
                    )
                row = store.due_query('site', time.time())
                self.assertEqual((row['id'], row['page']), (failed, 1))
            finally:
                store.db.close()

    def test_unrecognized_page_backoff_persists_without_blocking_fresh_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ExperimentStore(root, create=True)
            failed = store.add_query('site', 'failed query', 'zh', 'AI', pages=1)
            row = store.db.execute('SELECT * FROM queries WHERE id=?', (failed,)).fetchone()
            with patch('realtime.experiment_store.time.time', return_value=1000):
                store.search(row, 1, 999, [], [], 'google_unrecognized_page')
            self.assertEqual(store.db.execute(
                'SELECT due FROM schedule WHERE query_id=? AND page=1', (failed,)
            ).fetchone()[0], 2800)
            self.assertIsNone(store.cached(failed, 1, 1001))

            fresh = store.add_query('site', 'fresh query', 'zh', 'AI', pages=1)
            claimed = store.claim_due_query('site', 1001)
            self.assertEqual(claimed['id'], fresh)
            store.db.close()

            # The next tier derives from durable search history after restart.
            store = ExperimentStore(root)
            try:
                row = store.db.execute('SELECT * FROM queries WHERE id=?', (failed,)).fetchone()
                with patch('realtime.experiment_store.time.time', return_value=2800):
                    store.search(row, 1, 2799, [], [], 'google_unrecognized_page')
                self.assertEqual(store.db.execute(
                    'SELECT due FROM schedule WHERE query_id=? AND page=1', (failed,)
                ).fetchone()[0], 6400)
                coverage = store.counts()['page_coverage']['1']
                self.assertEqual(coverage['covered'], 0)
                self.assertEqual(coverage['retrying'], 1)
                self.assertEqual(store.db.execute(
                    'SELECT count(*) FROM discoveries WHERE query_id=? AND page=1', (failed,)
                ).fetchone()[0], 0)
            finally:
                store.db.close()

    def test_search_dispatch_prefers_fresh_coverage_over_retry_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('deadline', time.time() + 3600)
                store.set('search_workers', 4)
                failed = []
                for index in range(4):
                    key = store.add_query('topic', f'failed query {index}', 'zh', 'AI', pages=1)
                    failed.append(key)
                    with store.db:
                        store.db.execute(
                            "INSERT INTO searches(query_id,page,finished,status) "
                            "VALUES(?,?,?,'failed')", (key, 1, time.time()),
                        )
                fresh = store.add_query('topic', 'fresh query', 'zh', 'AI', pages=1)
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                runner.search_families = ('topic',)
                runner._submit_searches(0)
                jobs = [runner.jobs.get_nowait() for _ in range(4)]
                self.assertEqual(jobs[0]['id'], fresh)
                self.assertEqual({job['id'] for job in jobs[1:3]}, set(failed[:2]))
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

    def test_pipeline_proxy_unavailable_uses_persistent_tiered_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('site', 'site:example.com 人工智能', 'zh', '人工智能', pages=1)
                row = dict(store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone(), page=1)
                client = Mock(attempts=[])
                client._discover_google_page.side_effect = GoogleBlocked('google_proxy_unavailable')
                runner = SearchRunner(store, Config(), Mock(), Path(tmp))
                with patch.object(runner, 'client', return_value=client):
                    with patch('realtime.experiment_store.time.time', return_value=1000):
                        runner.search(row)
                    self.assertEqual(store.db.execute(
                        'SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)
                    ).fetchone()[0], 1300)
                    with patch('realtime.experiment_store.time.time', return_value=1300):
                        runner.search(row)
                    self.assertEqual(store.db.execute(
                        'SELECT due FROM schedule WHERE query_id=? AND page=1', (key,)
                    ).fetchone()[0], 1900)
                self.assertEqual(store.counts()['page_coverage']['1']['retrying'], 1)
            finally:
                store.db.close()

    def test_proxy_exhaustion_temporarily_pauses_global_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(Path(tmp), create=True)
            try:
                store.set('remote_only', True)
                key = store.add_query('site', 'site:example.com 人工智能', 'zh', '人工智能', pages=1)
                row = dict(store.db.execute(
                    'SELECT * FROM queries WHERE id=?', (key,)
                ).fetchone(), page=1)
                client = Mock(attempts=[])
                client._discover_google_page.side_effect = GoogleBlocked(
                    'google_proxy_unavailable', retry_after=7
                )
                runner = SearchRunner(store, Config(), Mock(), Path(tmp))
                before = time.time()
                with patch.object(runner, 'client', return_value=client):
                    runner.search(row)
                self.assertGreater(store.get('search_cooling_until'), before + 6.5)
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
        self.assertEqual(retry_delay({'error': 'PDF 正文解析失败'}), 86400)
        self.assertEqual(retry_delay({'error': '重定向次数过多'}), 86400)
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
                pool.resize(1, 128)
                self.assertEqual(pool.busy, 2)
                records = []
                while len(records) < 2:
                    records.extend(self._wait(pool))
                self.assertEqual({r['requested_url'] for r in records}, {'https://one.example/a', 'https://two.example/a'})
                self.assertEqual(pool.busy, 0)
                self.assertEqual(len(pool.workers), 1)
                pool.resize(2, 192)
                self.assertTrue(pool.submit({'url': 'https://one.example/b'}))
                self.assertTrue(pool.submit({'url': 'https://two.example/b'}))
                with self.assertRaises(ValueError):
                    pool.resize(1000, 192)
        finally:
            pool.close()

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
                self.assertEqual((pool.size, pool.max_rss_mib), (40, 128))
                self.assertEqual(store.get('deadline'), end)
                changes = list(store.db.execute("SELECT value FROM settings WHERE key LIKE 'body_resource_change_%'"))
                self.assertEqual(len(changes), 1)
                self.assertEqual(json.loads(changes[0][0])['before'], [32, 192])
                self.assertEqual(json.loads(changes[0][0])['after'], [40, 128])
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
                                                 {'url': 'https://known.example/canonical', 'title': 'known'},
                                                 {'url': 'https://new.example/a', 'title': 'new'}], [])
                with store.db:
                    store.db.execute(
                        "INSERT INTO documents(canonical,quality,classification) "
                        "VALUES('https://known.example/canonical','[]','new')"
                    )
                store.add_query('topic', '机器学习', 'zh', '机器学习')
                store.add_query('topic', '大语言模型', 'zh', '大语言模型')
                store.baseline(['https://old.example/a'])
                runner = PipelineRunner(store, Config(), Mock(), Path(tmp))
                pool = Mock(busy=0, size=24)
                runner._submit_bodies(pool)
                self.assertEqual(pool.submit.call_count, 1)
                self.assertEqual(pool.submit.call_args.args[0]['url'], 'https://new.example/a')
                self.assertEqual(store.db.execute("SELECT state FROM urls WHERE url='https://old.example/a'").fetchone()[0], 'baseline_skipped')
                self.assertEqual(store.db.execute(
                    "SELECT state FROM urls WHERE url='https://known.example/canonical'"
                ).fetchone()[0], 'canonical_skipped')
                runner._submit_searches(0)
                jobs = []
                while not runner.jobs.empty():
                    jobs.append(runner.jobs.get_nowait())
                self.assertEqual(len(jobs), 3)
                self.assertEqual(len({j['id'] for j in jobs}), 3)
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()
