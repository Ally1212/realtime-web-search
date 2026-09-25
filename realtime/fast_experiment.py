"""Opt-in bounded experiment pipeline; Google quota and quality rules are unchanged."""
from __future__ import annotations

import json
import os
import queue
import re
import selectors
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config
from .discovery import SearchResult
from .experiment_store import ExperimentStore, digest
from .discovery import ProviderCooldownRegistry
from .google_experiment import (
    DatedFetcher, FAMILIES, FINAL_STATES, Runner, export_report, iso,
    normalize_languages, seed_queries,
)
from .keyword_catalog import base_keyword_specs
from .proxy_pool import ProxySynchronizer


YIELD_FAMILIES = ('site', 'site', 'site', 'site', 'recent',
                  'site', 'site', 'site', 'site', 'topic',
                  'site', 'site', 'site', 'site', 'recent',
                  'site', 'site', 'site', 'site', 'event')


def archive_queries(domains, languages, today):
    """Bounded Google-only supply; dates are query filters, not publication evidence."""
    selected = [host for host, count in domains.most_common(160) if count >= 3]
    for rank, host in enumerate(selected):
        for language in languages:
            topics = ('人工智能', '大模型', '机器学习', '智能体', '生成式AI') if language == 'zh' else (
                'artificial intelligence', 'large language models', 'machine learning', 'AI agents', 'generative AI')
            for topic in topics:
                base = f'site:{host} {topic}'
                yield base, language, topic
                for year in range(today.year-8, today.year):
                    yield f'{base} after:{year}-01-01 before:{year+1}-01-01', language, topic
                if rank < 60:
                    current_month = today.year*12 + today.month-1
                    for offset in range(1, 25):
                        left, right = current_month-offset, current_month-offset+1
                        lower = f'{left//12:04d}-{left%12+1:02d}-01'
                        upper = f'{right//12:04d}-{right%12+1:02d}-01'
                        yield f'{base} after:{lower} before:{upper}', language, topic


def supply_plan(domains, languages, today):
    specs = [s for s in base_keyword_specs() if s.key.endswith(':topic')]
    topics = [(s.query, language) for language in languages
              for s in [s for s in specs if s.language == language][::4]]
    return {'hosts': [host for host, count in domains.most_common(160) if count >= 3],
            'topics': topics, 'month_anchor': today.year * 12 + today.month - 1,
            'months': 120, 'cursor': 0}


def dense_supply_plan(domains, languages, today):
    hosts = [host for host, count in domains.most_common(100) if count >= 20]
    topics = [('(人工智能 OR AI OR 大模型 OR ChatGPT OR 机器学习)' if language == 'zh' else
               '(artificial intelligence OR AI OR ChatGPT OR machine learning)', language) for language in languages]
    return {'kind': 'dense', 'hosts': hosts, 'topics': topics,
            'month_anchor': today.year*12+today.month-1, 'months': 96, 'cursor': 0}


def split_full_query(store, row, results):
    """Subdivide full dated Google pages; overlap one day, retain real timestamps."""
    if row['family'] != 'site' or len(results) < 10:
        return []
    lower = re.search(r'(?<!\S)after:(\d{4}-\d{2}-\d{2})(?!\S)', row['query'])
    upper = re.search(r'(?<!\S)before:(\d{4}-\d{2}-\d{2})(?!\S)', row['query'])
    if not lower or not upper:
        return []
    try:
        left, right = date.fromisoformat(lower[1]), date.fromisoformat(upper[1])
    except ValueError:
        return []
    days = (right-left).days
    if days <= 2:
        return []
    if store.db.execute('SELECT 1 FROM query_splits WHERE parent_id=?', (row['id'],)).fetchone():
        return []
    fresh = store.db.execute("SELECT count(*) FROM (SELECT 1 FROM queries WHERE family='site' "
                             "AND enabled=1 AND last_served=0 LIMIT 20000)").fetchone()[0]
    if fresh >= 20000:
        return []
    middle = left + timedelta(days=(days+1)//2)
    children = []
    with store.db:
        for start, end in ((left, middle), (middle-timedelta(days=1), right)):
            query = row['query'].replace(lower[0], 'after:'+start.isoformat(), 1).replace(upper[0], 'before:'+end.isoformat(), 1)
            key = digest(f"site:{row['language']}:{query}")[:24]
            store.db.execute('INSERT OR IGNORE INTO queries(id,family,query,language,topic) VALUES(?,?,?,?,?)',
                             (key, 'site', query, row['language'], row['topic']))
            store.db.executemany('INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)', ((key, p) for p in range(1, 12)))
            children.append(key)
        store.db.execute('INSERT OR IGNORE INTO query_splits VALUES(?,?,?)', (row['id'], time.time(), json.dumps(children)))
    return children


def supply_query(plan, cursor):
    """Stable cursor, interleaving hosts before topics and older complete months."""
    hosts, topics = plan['hosts'], plan['topics']
    total = len(hosts) * len(topics) * plan['months']
    if cursor >= total or cursor < 0:
        return None
    group, host_index = divmod(cursor, len(hosts))
    month_offset, topic_index = divmod(group, len(topics))
    topic, language = topics[topic_index]
    left = plan['month_anchor'] - month_offset - 1
    right = left + 1
    query = (f'site:{hosts[host_index]} {topic} after:{left//12:04d}-{left%12+1:02d}-01 '
             f'before:{right//12:04d}-{right%12+1:02d}-01')
    return query, language, topic


def replenish_queries(store, *, low_water=5000, batch_size=2000):
    plan = store.get('query_supply_cursor')
    if not plan:
        return 0
    if plan.get('kind') == 'dense':
        low_water, batch_size = min(low_water, 1000), min(batch_size, 1000)
    untouched = store.db.execute("SELECT count(*) FROM (SELECT 1 FROM queries WHERE family='site' "
                                 "AND enabled=1 AND last_served=0 LIMIT ?)", (low_water,)).fetchone()[0]
    if untouched >= low_water:
        return 0
    added = 0
    # Cursor and rows commit atomically; duplicate seeds do not reset their schedule.
    with store.db:
        for _ in range(batch_size):
            item = supply_query(plan, plan['cursor'])
            if item is None:
                break
            query, language, topic = item
            key = digest(f'site:{language}:{query}')[:24]
            result = store.db.execute('INSERT OR IGNORE INTO queries(id,family,query,language,topic) VALUES(?,?,?,?,?)',
                                      (key, 'site', query, language, topic))
            if result.rowcount:
                added += 1
            # Existing IDs can predate the 11-page requirement, so backfill their
            # schedules without changing any existing page due time.
            store.db.executemany('INSERT OR IGNORE INTO schedule(query_id,page,due) VALUES(?,?,0)',
                                 ((key, page) for page in range(1, 12)))
            plan['cursor'] += 1
        store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                         ('query_supply_cursor', json.dumps(plan, ensure_ascii=False)))
    return added


def restore_page_frontier(store, now):
    """Carry the daily successful-page refresh horizon across experiment versions."""
    if store.get('page_frontier_restored'):
        return
    inherited = 0
    for directory in store.get('baseline_run_paths', []):
        path = Path(directory) / 'experiment.sqlite3'
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            # Failed attempts are still visited queries. Without this history,
            # inheriting only successful pages promotes old failures ahead of
            # genuinely untouched queries in the new experiment.
            visited = connection.execute('SELECT query_id,max(finished) FROM searches WHERE finished>? '
                                         'AND finished<=? GROUP BY query_id', (now-86400, now))
            while batch := visited.fetchmany(2000):
                with store.db:
                    store.db.executemany('UPDATE queries SET last_served=max(last_served,?) WHERE id=?',
                                         ((finished, key) for key, finished in batch))
            rows = connection.execute("SELECT query_id,page,max(finished) FROM searches WHERE status='success' "
                                      "AND finished>? AND finished<=? GROUP BY query_id,page", (now-86400, now))
            while batch := rows.fetchmany(2000):
                with store.db:
                    for key, page, finished in batch:
                        changed = store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=? AND due<?',
                                                   (finished+86400, key, page, finished+86400))
                        if changed.rowcount:
                            inherited += 1
                            store.db.execute('UPDATE queries SET last_served=max(last_served,?) WHERE id=?',
                                             (finished, key))
        finally:
            connection.close()
    store.set('page_frontier_restored', {'pages_deferred': inherited, 'at': now})


def serve_fetches():
    config = Config()
    fetcher = DatedFetcher(config.user_agent, timeout=15,
                          use_trafilatura=config.trafilatura_enabled, reuse_sessions=True, lean_metadata=True)
    try:
        for line in sys.stdin:
            row = json.loads(line)
            started = time.monotonic()
            fetcher.publication = (None, None)
            try:
                result = asdict(fetcher.fetch(SearchResult(row['url'], row['title'], ('google_web',)),
                                              row['query'], iso(row['first_seen'])))
                if result.get('document'):
                    doc = result['document']
                    doc['published_at'], doc['publication_source'] = fetcher.publication
                    doc['content'] = ' '.join(doc['content'].split())
                    doc['content_hash'] = digest(doc['content'])
            except Exception as exc:
                result = {'status': 'failed', 'error': type(exc).__name__, 'document': None}
            result.update(requested_url=row['url'], finished=time.time(),
                          seconds=round(time.monotonic() - started, 3))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        fetcher.close()


class BodyPool:
    """Reusable isolated workers with per-task hard kill and domain-aware dispatch."""
    def __init__(self, size=24, per_domain=1, deadline=65, max_tasks=200, max_rss_mib=192):
        self.size, self.per_domain, self.deadline = size, per_domain, deadline
        self.max_tasks, self.max_rss_mib = max_tasks, max_rss_mib
        self.selector = selectors.DefaultSelector()
        self.workers = []
        self.domains = Counter()
        self.spawn_errors = 0

    def _spawn(self):
        try:
            process = subprocess.Popen(
                [sys.executable, '-m', 'realtime.fast_experiment', 'fetch'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            # Body submission is opportunistic. If the process limit is reached,
            # leave this URL pending instead of crashing a multi-hour experiment.
            return None
        os.set_blocking(process.stdout.fileno(), False)
        worker = {'process': process, 'row': None, 'buffer': b'', 'hosts': set(), 'completed': 0}
        self.selector.register(process.stdout, selectors.EVENT_READ, worker)
        self.workers.append(worker)
        return worker

    @property
    def busy(self):
        return sum(w['row'] is not None for w in self.workers)

    def submit(self, row):
        host = urlsplit(row['url']).hostname
        if self.domains[host] >= self.per_domain:
            return False
        idle = [w for w in self.workers if w['row'] is None]
        worker = next((w for w in idle if host in w['hosts']), None)
        if worker is None:
            worker = idle[0] if idle else self._spawn() if len(self.workers) < self.size else None
        if worker is None:
            return False
        worker.update(row=row, started=time.monotonic(), host=host)
        worker['hosts'].add(host)
        self.domains[host] += 1
        try:
            worker['process'].stdin.write((json.dumps(row) + '\n').encode())
            worker['process'].stdin.flush()
        except (BrokenPipeError, OSError):
            worker['started'] = 0  # poll() reports and retires the failed worker.
        return True

    def _retire(self, worker):
        if worker.get('row') is not None:
            self.domains[worker['host']] -= 1
            worker['row'] = None
        process = worker['process']
        self.selector.unregister(process.stdout)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        process.stdout.close()
        process.stdin.close()
        self.workers.remove(worker)

    def _recycle_due(self, worker):
        if len(self.workers) > self.size or worker['completed'] >= self.max_tasks:
            return True
        try:
            status = Path(f"/proc/{worker['process'].pid}/status").read_text()
            rss_kib = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:'))
            return rss_kib >= self.max_rss_mib * 1024
        except (OSError, StopIteration, ValueError):
            return False  # Non-Linux systems still recycle by completed-task count.

    def resize(self, size, per_domain, max_rss_mib):
        if not 1 <= size <= 64 or not 1 <= per_domain <= 8 or not 64 <= max_rss_mib <= 512:
            raise ValueError('invalid body pool resource configuration')
        self.size, self.per_domain, self.max_rss_mib = size, per_domain, max_rss_mib
        for worker in list(self.workers):
            if len(self.workers) <= size:
                break
            if worker['row'] is None:
                self._retire(worker)

    def poll(self, wait=0.05):
        completed = []
        for key, _ in self.selector.select(wait):
            w = key.data
            chunk = os.read(key.fileobj.fileno(), 262144)
            if chunk:
                w['buffer'] += chunk
            elif w['row'] is not None:
                w['started'] = 0
            else:
                self._retire(w)
                continue
            if b'\n' in w['buffer'] and w['row'] is not None:
                line, w['buffer'] = w['buffer'].split(b'\n', 1)
                try:
                    record = json.loads(line)
                    if record['requested_url'] != w['row']['url']:
                        raise ValueError('mismatched worker result')
                except (ValueError, KeyError):
                    w['started'] = 0
                    continue
                completed.append(record)
                self.domains[w['host']] -= 1
                w['row'] = None
                w['completed'] += 1
                if self._recycle_due(w):
                    self._retire(w)
                    continue
        for w in list(self.workers):
            if w['row'] is not None and time.monotonic() - w['started'] >= self.deadline:
                completed.append({'requested_url': w['row']['url'], 'status': 'failed',
                                  'error': 'body_hard_deadline', 'document': None,
                                  'finished': time.time(), 'seconds': self.deadline})
                self._retire(w)
        return completed

    def close(self):
        for worker in list(self.workers):
            self._retire(worker)
        self.selector.close()


def retry_delay(record):
    error = str(record.get('error') or '')
    if record.get('status') == 'blocked' or '不支持的内容类型' in error or 'HTTP 404' in error:
        return 86400
    if record.get('document'):
        return 86400  # This experiment measures new content; updates are a separate workload.
    if 'HTTP 403' in error or '不足 100' in error:
        return 21600
    return 3600


class SearchRunner(Runner):
    def __init__(self, *args, proxy_pool=None, provider_cooldowns=None):
        super().__init__(*args, proxy_pool=proxy_pool)
        if provider_cooldowns is not None:
            self.provider_cooldowns = provider_cooldowns

    def client(self, language):
        client = super().client(language)
        client.proxy_provider_attempts = 2
        return client

    def search(self, row):
        super().search(row)
        last = self.store.db.execute('SELECT status,error,finished FROM searches WHERE query_id=? AND page=? '
                                     'ORDER BY id DESC LIMIT 1', (row['id'], row['page'])).fetchone()
        if last and last['status'] == 'success':
            # This executor prioritizes newly discovered content. Reopening
            # shallow pages hourly starves the still-unvisited page frontier.
            with self.store.db:
                self.store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=?',
                                      (last['finished'] + 86400, row['id'], row['page']))
            if self.store.get('query_plan') == 'dense':
                result = self.store.db.execute('SELECT results FROM searches WHERE query_id=? AND page=? '
                                               'ORDER BY id DESC LIMIT 1', (row['id'], row['page'])).fetchone()
                split_full_query(self.store, row, json.loads(result[0]))
        elif last and last['error'] == 'google_proxy_unavailable':
            delay = min(300.0, max(
                5.0,
                float(getattr(self.client(row['language']), 'proxy_wait_seconds', 30) or 30),
            ))
            self.store.set('search_cooling_until', time.time() + delay)
            with self.store.db:
                self.store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=?',
                                      (time.time() + delay, row['id'], row['page']))


class PipelineRunner(Runner):
    def __init__(self, *args):
        super().__init__(*args)
        self.body_size = max(1, min(64, self.store.get('body_workers', 24)))
        self.body_per_domain = max(1, min(8, self.store.get('body_per_domain', 1)))
        self.search_size = max(1, min(24, self.store.get('search_workers', 3)))
        self.jobs = queue.Queue(maxsize=self.search_size)
        self.events = queue.Queue()
        self.shutdown = threading.Event()
        self.search_inflight = 0
        self.threads = []
        self.search_thread_count = 0
        self.provider_cooldowns = ProviderCooldownRegistry()
        self.search_families = YIELD_FAMILIES if self.store.get('query_plan') in {'yield', 'dense'} else FAMILIES

    def _stage(self, kind):
        store = None
        runner = None
        try:
            # Each thread owns its SQLite connection. WAL serializes short writes.
            store = ExperimentStore(self.store.path.parent)
            # Share rotation and local cooldown state across every search
            # thread. PostgreSQL still coordinates with other processes.
            runner = SearchRunner(
                store, self.config, self.production, self.output,
                proxy_pool=self.pool, provider_cooldowns=self.provider_cooldowns,
            )
            if kind != 'upload':
                runner.whale = None
            while not self.shutdown.is_set():
                if kind == 'search':
                    try:
                        row = self.jobs.get(timeout=.5)
                    except queue.Empty:
                        continue
                    try:
                        if store.get('state') == 'running' and time.time() < store.get('deadline'):
                            runner.search(row)
                    finally:
                        self.events.put(('search_done', None))
                        self.jobs.task_done()
                elif kind == 'upload':
                    if store.get('state') in {'running', 'draining'}:
                        runner.flush_whale()
                    self.shutdown.wait(.5)
                else:
                    profiles = {store.get('proxy_profile', 'private')}
                    # Google public endpoints are an independent overflow pool.
                    # Refreshing both keeps failover possible without coupling
                    # the primary profile's sync cadence to the other pool.
                    if "private" in profiles:
                        profiles.add("public_google")
                    for profile in sorted(profiles):
                        try:
                            ProxySynchronizer(self.config).sync(profile)
                        except Exception as exc:
                            self.events.put((f"proxy_error_{profile}", type(exc).__name__))
                    # sync() enforces the shared cache's configured interval;
                    # poll it so a skipped startup sync does not add 30 minutes.
                    self.shutdown.wait(60)
        except Exception as exc:
            self.events.put(('stage_error', kind + ':' + type(exc).__name__))
        finally:
            if runner:
                for client in runner.clients.values():
                    client._close_browser()
                if runner.whale:
                    runner.whale.session.close()
            if store:
                store.db.close()

    def _seed(self):
        languages = normalize_languages(self.store.get('languages'))
        seed_queries(self.store, self.config, time.time(), self.store.get('preflight'), languages)
        if self.store.get('pipeline_seeded') or self.store.get('preflight'):
            return
        # Separate historical years expand finite SERP supply without changing provenance.
        this_year = datetime.now(timezone.utc).year
        for spec in base_keyword_specs():
            if spec.key.endswith(':topic') and spec.language in languages:
                for year in range(this_year - 4, this_year):
                    self.store.add_query('recent', f'{spec.query} after:{year}-01-01 before:{year+1}-01-01 '
                                         '-site:youtube.com -site:youtu.be', spec.language, spec.query)
        domains = Counter()
        for directory in self.store.get('baseline_run_paths', []):
            path = Path(directory) / 'experiment.sqlite3'
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                for (url,) in connection.execute("SELECT d.canonical FROM documents d JOIN outbox o ON o.document_id=d.id "
                                                 "WHERE o.status='accepted' AND d.quality='[]'"):
                    host = urlsplit(url).hostname
                    if host:
                        domains[host] += 1
            finally:
                connection.close()
        if self.store.get('query_plan') == 'dense':
            self.store.set('query_supply_cursor', dense_supply_plan(domains, languages, datetime.now(timezone.utc)))
            replenish_queries(self.store)
            self.store.set('query_supply', {'plan': 'dense', 'site_weight': .8,
                                          'queries': self.store.db.execute('SELECT count(*) FROM queries').fetchone()[0]})
            self.store.set('pipeline_seeded', True)
            return
        for host, _ in domains.most_common(60):
            for language in languages:
                topics = ('人工智能', '大模型', '机器学习', '智能体', '生成式AI') if language == 'zh' else (
                    'artificial intelligence', 'large language models', 'machine learning', 'AI agents', 'generative AI')
                for topic in topics:
                    self.store.add_query('site', f'site:{host} {topic}', language, topic)
        if self.store.get('query_plan') == 'yield':
            self.store.set('query_supply_cursor', supply_plan(domains, languages, datetime.now(timezone.utc)))
            # One bounded transaction avoids thousands of per-query fsyncs.
            with self.store.db:
                for query, language, topic in archive_queries(domains, languages, datetime.now(timezone.utc)):
                    key = digest(f'site:{language}:{query}')[:24]
                    self.store.db.execute('INSERT OR IGNORE INTO queries(id,family,query,language,topic) VALUES(?,?,?,?,?)',
                                          (key, 'site', query, language, topic))
                    self.store.db.executemany('INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)',
                                             ((key, page) for page in range(1, 12)))
                self.store.db.execute('CREATE INDEX IF NOT EXISTS pipeline_query_family ON queries(family,enabled,last_served,id)')
            self.store.set('query_supply', {'plan': 'yield', 'site_weight': .8,
                                          'queries': self.store.db.execute('SELECT count(*) FROM queries').fetchone()[0]})
        self.store.set('pipeline_seeded', True)

    def save_body(self, record):
        super().save_body(record)
        with self.store.db:
            self.store.db.execute('UPDATE urls SET next_fetch=? WHERE url=?',
                                  (time.time() + retry_delay(record), record['requested_url']))

    def _submit_bodies(self, pool):
        now = time.time()
        with self.store.db:
            self.store.db.execute("UPDATE urls SET state='baseline_skipped',next_fetch=? WHERE last_fetch=0 "
                                  "AND state='pending' AND EXISTS(SELECT 1 FROM baseline_urls b WHERE b.url=urls.url)",
                                  (self.store.get('deadline') + 86400,))
        # Cover the 1,500-URL discovery backlog so a busy host at its head
        # cannot hide work for other domains from otherwise idle body slots.
        # An empty pool cannot have in-flight domain leases. Clear leaked counters
        # so retired/killed workers cannot permanently block body dispatch.
        if not pool.workers and pool.busy == 0:
            pool.domains.clear()
        rows = self.store.due_urls(2048, now)
        # Fair dispatch prevents a single site at the FIFO head from consuming
        # all free workers while other discoverable domains remain idle.
        by_host = {}
        for row in rows:
            by_host.setdefault(urlsplit(row['url']).hostname or '', []).append(row)
        hosts = list(by_host)
        accepted = 0
        while hosts and pool.busy < pool.size:
            next_hosts = []
            for index, host in enumerate(hosts):
                if pool.busy >= pool.size:
                    next_hosts.extend(hosts[index:])
                    break
                row = by_host[host].pop(0)
                if not by_host[host]:
                    del by_host[host]
                if pool.submit(dict(row)):
                    accepted += 1
                    with self.store.db:
                        self.store.db.execute("UPDATE urls SET state='fetching' WHERE url=?", (row['url'],))
                else:
                    continue
                if by_host.get(host):
                    next_hosts.append(host)
            hosts = next_hosts
        if rows and accepted == 0 and pool.busy == 0:
            self.store.set('body_dispatch_stall_' + str(time.time_ns()), {
                'at': time.time(), 'ready_urls': len(rows), 'workers': len(pool.workers),
                'worker_limit': pool.size, 'spawn_errors': pool.spawn_errors,
                'domain_leases': sum(pool.domains.values()),
            })

    def _submit_searches(self, family_index):
        now = time.time()
        pending = self.store.db.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0]
        if pending >= 1500 or now < self.store.get('search_cooling_until', 0):
            return family_index
        weights = self.store.get('search_family_weights')
        families = tuple(weights) if weights else self.search_families
        while self.search_inflight < self.search_size and not self.jobs.full():
            for offset in range(len(families)):
                index = (family_index + offset) % len(families)
                family = families[index]
                row = self.store.due_query(family, now)
                if row:
                    with self.store.db:
                        self.store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=?',
                                              (now + 300, row['id'], row['page']))
                        self.store.db.execute('UPDATE queries SET last_served=? WHERE id=?', (now, row['id']))
                    self.jobs.put_nowait(dict(row))
                    self.search_inflight += 1
                    family_index = (index + 1) % len(families)
                    break
            else:
                break
        return family_index

    def _configure_pool(self, pool):
        requested = (int(self.store.get('body_workers', self.body_size)),
                     max(1, min(8, int(self.store.get('body_per_domain', self.body_per_domain)))),
                     int(self.store.get('body_max_rss_mib', 192)))
        current = (pool.size, pool.per_domain, pool.max_rss_mib)
        if requested != current:
            pool.resize(*requested)
            self.body_size, self.body_per_domain = requested[:2]
            self.store.set('body_resource_change_' + str(time.time_ns()),
                           {'at': time.time(), 'before': current, 'after': requested,
                            'fields': ['workers', 'per_domain', 'max_rss_mib']})

    def _start_stage(self, kind):
        thread = threading.Thread(target=self._stage, args=(kind,), daemon=True, name='pipeline-' + kind)
        thread.start()
        self.threads.append(thread)
        if kind == 'search':
            self.search_thread_count += 1

    def _configure_searches(self):
        requested = int(self.store.get('search_workers', self.search_size))
        if not 1 <= requested <= 24:
            raise ValueError('invalid search concurrency')
        if requested == self.search_size:
            return
        before, self.search_size = self.search_size, requested
        with self.jobs.mutex:
            self.jobs.maxsize = requested
            self.jobs.not_full.notify_all()
        while self.search_thread_count < requested:
            self._start_stage('search')
        # Shrink the dispatch ceiling; already-running pages finish normally.
        self.store.set('search_resource_change_' + str(time.time_ns()),
                       {'at': time.time(), 'before': before, 'after': requested,
                        'started_threads': self.search_thread_count})

    def run(self):
        store = self.store
        store.set('upload_interval', 1)
        store.set('upload_batch_size', 50)
        with store.db:
            store.db.execute("UPDATE urls SET state='pending' WHERE state='fetching'")
            store.db.execute('CREATE INDEX IF NOT EXISTS pipeline_url_due ON urls(state,next_fetch,last_fetch)')
            store.db.execute("CREATE INDEX IF NOT EXISTS pipeline_url_ready ON urls(last_fetch,first_seen,url) "
                             "WHERE state NOT IN ('fetching','baseline_skipped') AND (last_fetch=0 OR last_seen>last_fetch)")
            store.db.execute('CREATE INDEX IF NOT EXISTS pipeline_document_url ON documents(url)')
        self._seed()
        restore_page_frontier(store, time.time())
        pool = BodyPool(self.body_size, per_domain=self.body_per_domain,
                        max_rss_mib=self.store.get('body_max_rss_mib', 192))
        for kind in ['search'] * self.search_size + ['upload', 'proxy']:
            self._start_stage(kind)
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: setattr(self, 'stop', True))
        last_tick = time.time()
        last_sample = last_dispatch = 0
        family_index = 0
        drain_until = None
        try:
            while True:
                now = time.time()
                state = store.get('state')
                for record in pool.poll():
                    self.save_body(record)
                while not self.events.empty():
                    event, value = self.events.get_nowait()
                    if event == 'search_done':
                        self.search_inflight -= 1
                    elif event == 'stage_error':
                        store.set('pipeline_error', value)
                        raise RuntimeError(value)
                    else:
                        store.set(event, value)
                if now - last_tick >= 1:
                    kind = 'paused' if state == 'paused' else 'google_cooling' if store.get('search_cooling_until', 0) > now else 'active'
                    with store.db:
                        store.db.execute('INSERT INTO runtime VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET seconds=seconds+excluded.seconds',
                                         (kind, now-last_tick))
                    store.set('heartbeat', now)
                    last_tick = now
                preflight_done = store.get('preflight') and store.db.execute('SELECT count(*) FROM searches').fetchone()[0] >= store.get('preflight_search_target', 2)
                if now >= store.get('deadline') or self.stop or state in FINAL_STATES or drain_until is not None:
                    if drain_until is None:
                        drain_until = min(now+120, store.get('deadline')+120)
                        store.set('state', 'draining')
                        store.set('stop_reason', 'interrupted' if self.stop else 'deadline')
                    outbox = store.db.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0]
                    if (not pool.busy and not self.search_inflight and not outbox) or now >= drain_until:
                        break
                elif state == 'running':
                    if now-last_dispatch >= .1:
                        outbox = store.db.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0]
                        if outbox < 5000:
                            self._submit_bodies(pool)
                            if not preflight_done:
                                family_index = self._submit_searches(family_index)
                        last_dispatch = now
                    if preflight_done and not self.search_inflight and not pool.busy:
                        if not store.due_urls(1, now):
                            drain_until = now+90
                            store.set('state', 'draining')
                            store.set('stop_reason', 'preflight_complete')
                if now-last_sample >= 60:
                    self._configure_pool(pool)
                    self._configure_searches()
                    replenish_queries(store)
                    metrics = store.counts(detailed=False)
                    with store.db:
                        store.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?)', (now, json.dumps(metrics)))
                    store.set('pipeline', {'body_busy': pool.busy, 'body_workers': self.body_size,
                                          'body_rss_recycle_mib': pool.max_rss_mib,
                                          'search_busy': self.search_inflight, 'search_workers': self.search_size,
                                          'search_threads': self.search_thread_count,
                                          'baseline_skipped': store.db.execute("SELECT count(*) FROM urls WHERE state='baseline_skipped'").fetchone()[0]})
                    print(json.dumps({'experiment': store.get('id'), 'state': store.get('state'),
                                      'new': metrics.get('new', 0), 'requests': metrics['google_requests'],
                                      'whale_accepted': metrics.get('whale_accepted', 0),
                                      'body_busy': pool.busy}, ensure_ascii=False), flush=True)
                    last_sample = now
                    if not self.storage_ok():
                        store.set('state', 'storage_stopped')
                        store.set('stop_reason', 'disk_budget_or_free_space')
        finally:
            self.shutdown.set()
            pool.close()
            for thread in self.threads:
                thread.join(timeout=30)
            with store.db:
                store.db.execute("UPDATE urls SET state='pending' WHERE state='fetching'")
            if store.get('state') != 'storage_stopped':
                store.set('state', 'paused' if self.stop else 'complete' if drain_until else 'interrupted')
            store.set('finished_at', time.time())
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?)', (time.time(), json.dumps(store.counts())))
            if not self.remote_only:
                export_report(store, self.output, full=True)


if __name__ == '__main__' and sys.argv[1:] == ['fetch']:
    serve_fetches()
