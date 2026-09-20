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
from .google_experiment import (
    DatedFetcher, EXCLUDE, FAMILIES, FINAL_STATES, Runner, archive_audit, export_report, iso,
    normalize_languages, seed_queries,
)
from .keyword_catalog import base_keyword_specs
from .proxy_pool import ProxySynchronizer


YIELD_FAMILIES = ('site', 'site', 'site', 'site', 'recent',
                  'site', 'site', 'site', 'site', 'topic',
                  'site', 'site', 'site', 'site', 'recent',
                  'site', 'site', 'site', 'site', 'event')
SUPPLY_PAGE_COUNT = 11
# The adaptive metric is accepted unique documents per successful page. Use a
# weak neutral prior: the former new-body priors (especially site=2/page)
# overvalued families whose documents often lacked an admissible publication
# timestamp and delayed convergence to the observed delivery yield.
FAMILY_PRIORS = {family: (.5, 10) for family in FAMILIES}


def weighted_family_schedule(observations, slots=20):
    """Build a fair interleaved schedule from smoothed accepted-doc/page yield."""
    if slots < len(FAMILIES):
        raise ValueError('family schedule must include every family')
    scores = {}
    for family in FAMILIES:
        new, pages = observations.get(family, (0, 0))
        prior, strength = FAMILY_PRIORS[family]
        scores[family] = (max(0, new) + prior * strength) / (max(0, pages) + strength)
    # Maximize observed delivery yield while preserving exploration. At the
    # standard 20 slots every family keeps 10%, and no family may monopolize
    # more than 40%; the two best measured families receive the spare slots.
    floor = 2 if slots >= len(FAMILIES) * 2 else 1
    ceiling = min(8, slots - floor * (len(FAMILIES) - 1))
    allocations = {family: floor for family in FAMILIES}
    for _ in range(slots - floor * len(FAMILIES)):
        eligible = [family for family in FAMILIES if allocations[family] < ceiling]
        family = max(eligible, key=lambda item: (scores[item], -FAMILIES.index(item)))
        allocations[family] += 1
    used = Counter()
    schedule = []
    for position in range(slots):
        family = max(
            FAMILIES,
            key=lambda item: (
                (position + 1) * allocations[item] / slots - used[item],
                -FAMILIES.index(item),
            ),
        )
        schedule.append(family)
        used[family] += 1
    return tuple(schedule), {
        'scores': scores,
        'allocations': allocations,
        'allocation_policy': {'minimum_per_family': floor, 'maximum_per_family': ceiling},
    }


def adaptive_family_schedule(store):
    pages = dict(store.db.execute(
        "SELECT q.family,count(*) FROM searches s JOIN queries q ON q.id=s.query_id "
        "WHERE s.status='success' GROUP BY q.family"
    ))
    new = dict(store.db.execute(
        "SELECT q.family,count(DISTINCT d.id) FROM queries q "
        "JOIN discoveries x ON x.query_id=q.id JOIN documents d ON d.url=x.url "
        "WHERE d.quality='[]' AND d.classification='new' GROUP BY q.family"
    ))
    accepted = dict(store.db.execute(
        "SELECT q.family,count(DISTINCT d.id) FROM queries q "
        "JOIN discoveries x ON x.query_id=q.id JOIN documents d ON d.url=x.url "
        "JOIN outbox o ON o.document_id=d.id "
        "WHERE d.quality='[]' AND d.classification='new' AND o.status='accepted' "
        "GROUP BY q.family"
    ))
    observations = {
        family: (int(accepted.get(family, 0)), int(pages.get(family, 0)))
        for family in FAMILIES
    }
    schedule, audit = weighted_family_schedule(observations)
    audit['observations'] = observations
    audit['new_documents'] = {family: int(new.get(family, 0)) for family in FAMILIES}
    audit['optimization_metric'] = 'accepted_unique_documents_per_success_page'
    return schedule, audit


def _ranked_domain_hosts(domains, minimum_total, limit):
    if isinstance(domains, dict) and 'valid' in domains:
        valid = domains['valid']
        deliverable = domains.get('deliverable', Counter())
        hosts = (host for host, count in valid.items() if count >= minimum_total)
        return sorted(
            hosts,
            key=lambda host: (
                -deliverable[host] / max(valid[host], 1),
                -deliverable[host],
                -valid[host],
                host,
            ),
        )[:limit]
    return [host for host, count in domains.most_common(limit) if count >= minimum_total]


def supply_plan(domains, languages, today):
    specs = [s for s in base_keyword_specs() if s.key.endswith(':topic')]
    topics = [(s.query, language) for language in languages
              for s in [s for s in specs if s.language == language][::4]]
    return {'hosts': _ranked_domain_hosts(domains, 3, 160),
            'topics': topics, 'month_anchor': today.year * 12 + today.month - 1,
            'months': 120, 'cursor': 0}


def dense_supply_plan(domains, languages, today):
    hosts = _ranked_domain_hosts(domains, 20, 100)
    topics = [('(人工智能 OR AI OR 大模型 OR ChatGPT OR 机器学习)' if language == 'zh' else
               '(artificial intelligence OR AI OR ChatGPT OR machine learning)', language) for language in languages]
    return {'kind': 'dense', 'hosts': hosts, 'topics': topics,
            'month_anchor': today.year*12+today.month-1, 'months': 96, 'cursor': 0}


def ranked_supply_domains(directories):
    """Rank eligible hosts by historically deliverable, valid unique bodies."""
    outcomes = {}
    loaded = []
    skipped = []
    for directory in directories:
        path = Path(directory) / 'experiment.sqlite3'
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            columns = {row[1] for row in connection.execute('PRAGMA table_info(documents)')}
            publication = 'd.published_at' if 'published_at' in columns else 'NULL'
            rows = connection.execute(
                f"SELECT d.canonical,{publication},o.status FROM documents d "
                "LEFT JOIN outbox o ON o.document_id=d.id "
                "WHERE d.quality='[]' AND d.classification='new'"
            )
            for url, published_at, status in rows:
                # The same canonical can occur in multiple baseline ledgers.
                # A real receipt is stronger evidence than a previous missing-date result.
                rank = 2 if status == 'accepted' or published_at else 1 if status == 'blocked_missing_publication' else 0
                outcomes[url] = max(outcomes.get(url, -1), rank)
            loaded.append(str(path))
        except (OSError, sqlite3.DatabaseError) as exc:
            skipped.append({'path': str(path), 'error': type(exc).__name__})
        finally:
            if connection is not None:
                connection.close()
    total, accepted, blocked, other = Counter(), Counter(), Counter(), Counter()
    for url, outcome in outcomes.items():
        if not url:
            continue
        host = (urlsplit(url).hostname or '').lower()
        if not host:
            continue
        total[host] += 1
        (accepted if outcome == 2 else blocked if outcome == 1 else other)[host] += 1
    rankings = {'valid': total, 'deliverable': accepted, 'blocked': blocked}
    audit = {
        'unique_canonicals': len(outcomes),
        'accepted': sum(accepted.values()),
        'blocked_missing_publication': sum(blocked.values()),
        'other_not_accepted': sum(other.values()),
        'ranked_hosts': len(total),
        'eligible_hosts': sum(count >= 3 for count in total.values()),
        'ranking': 'minimum_valid_unique_then_deliverable_ratio_and_count',
        'loaded_ledgers': loaded,
        'skipped_ledgers': skipped,
    }
    return rankings, audit


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
            key = store.query_id('site', query, row['language'], row['locale_label'])
            store.db.execute('INSERT OR IGNORE INTO queries(id,family,query,language,locale_label,topic) VALUES(?,?,?,?,?,?)',
                             (key, 'site', query, row['language'], row['locale_label'], row['topic']))
            store.db.executemany(
                'INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)',
                ((key, page) for page in range(1, SUPPLY_PAGE_COUNT + 1)),
            )
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


def replenish_queries(store, locales, *, low_water=1, batch_size=200, retry_backlog_limit=500):
    """Add a bounded cohort after first attempts, unless failed-page debt is high."""
    plan = store.get('query_supply_cursor')
    if not plan:
        return 0
    if plan.get('kind') == 'dense':
        batch_size = min(batch_size, 100)
    now = time.time()
    unattempted_pages = store.db.execute(
        "SELECT count(*) FROM (SELECT 1 FROM schedule s JOIN queries q ON q.id=s.query_id "
        "WHERE q.family='site' AND q.enabled=1 AND s.due<=? AND NOT EXISTS "
        "(SELECT 1 FROM searches x WHERE x.query_id=s.query_id AND x.page=s.page) LIMIT ?)",
        (now, low_water),
    ).fetchone()[0]
    if unattempted_pages >= low_water:
        return 0
    retrying_pages = store.db.execute(
        "SELECT count(*) FROM (SELECT 1 FROM schedule s JOIN queries q ON q.id=s.query_id "
        "WHERE q.family='site' AND q.enabled=1 AND NOT EXISTS "
        "(SELECT 1 FROM searches x WHERE x.query_id=s.query_id AND x.page=s.page "
        "AND x.status='success') AND EXISTS (SELECT 1 FROM searches x "
        "WHERE x.query_id=s.query_id AND x.page=s.page AND x.status='failed') LIMIT ?)",
        (retry_backlog_limit,),
    ).fetchone()[0]
    if retrying_pages >= retry_backlog_limit:
        return 0
    added = 0
    # Cursor and rows commit atomically; duplicate seeds do not reset their schedule.
    with store.db:
        for _ in range(batch_size):
            item = supply_query(plan, plan['cursor'])
            if item is None:
                break
            query, language, topic = item
            for locale in (candidate for candidate in locales if candidate.language == language):
                key = store.query_id('site', query, language, locale.label)
                result = store.db.execute(
                    'INSERT OR IGNORE INTO queries(id,family,query,language,locale_label,topic) '
                    'VALUES(?,?,?,?,?,?)',
                    (key, 'site', query, language, locale.label, topic)
                )
                if result.rowcount:
                    added += 1
                    store.db.executemany(
                        'INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)',
                        ((key, page) for page in range(1, SUPPLY_PAGE_COUNT + 1)),
                    )
            plan['cursor'] += 1
        store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                         ('query_supply_cursor', json.dumps(plan, ensure_ascii=False)))
    return added


def restore_page_frontier(store, now):
    """Carry the daily successful-page refresh horizon across experiment versions."""
    if store.get('page_frontier_restored'):
        return
    inherited = 0
    loaded = []
    skipped = []
    for directory in store.get('baseline_run_paths', []):
        path = Path(directory) / 'experiment.sqlite3'
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
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
            loaded.append(str(path))
        except (OSError, sqlite3.DatabaseError) as exc:
            # Baseline URLs and hashes were already copied into this ledger.
            # Losing an optional refresh horizon must not discard current work.
            skipped.append({'path': str(path), 'error': type(exc).__name__})
        finally:
            if connection is not None:
                connection.close()
    store.set('page_frontier_restored', {
        'pages_deferred': inherited,
        'at': now,
        'loaded_ledgers': loaded,
        'skipped_ledgers': skipped,
    })


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
    def __init__(self, size=24, per_domain=2, deadline=65, max_tasks=200, max_rss_mib=192):
        self.size, self.per_domain, self.deadline = size, per_domain, deadline
        self.max_tasks, self.max_rss_mib = max_tasks, max_rss_mib
        self.selector = selectors.DefaultSelector()
        self.workers = []
        self.domains = Counter()

    def _spawn(self):
        process = subprocess.Popen([sys.executable, '-m', 'realtime.fast_experiment', 'fetch'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
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

    def resize(self, size, max_rss_mib):
        if not 1 <= size <= 64 or not 64 <= max_rss_mib <= 512:
            raise ValueError('invalid body pool resource configuration')
        self.size, self.max_rss_mib = size, max_rss_mib
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
        for w in list(self.workers):
            if w['row'] is not None and time.monotonic() - w['started'] >= self.deadline:
                completed.append({'requested_url': w['row']['url'], 'status': 'failed',
                                  'error': 'body_hard_deadline', 'document': None,
                                  'finished': time.time(), 'seconds': self.deadline})
                self.domains[w['host']] -= 1
                self._retire(w)
        return completed

    def close(self):
        for worker in list(self.workers):
            self._retire(worker)
        self.selector.close()


def retry_delay(record):
    error = str(record.get('error') or '')
    if (record.get('status') == 'blocked' or '不支持的内容类型' in error
            or 'HTTP 404' in error or 'PDF ' in error or '重定向次数过多' in error):
        return 86400
    if record.get('document'):
        return 86400  # This experiment measures new content; updates are a separate workload.
    if 'HTTP 403' in error or '不足 100' in error:
        return 21600
    return 3600


class SearchRunner(Runner):
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


class PipelineRunner(Runner):
    def __init__(self, *args):
        super().__init__(*args)
        self.body_size = max(1, min(64, self.store.get('body_workers', 24)))
        self.search_size = max(1, min(24, self.store.get('search_workers', 3)))
        self.jobs = queue.Queue(maxsize=self.search_size)
        self.events = queue.Queue()
        self.shutdown = threading.Event()
        self.search_inflight = 0
        self.threads = []
        self.search_thread_count = 0
        self.search_dispatches = 0
        self.page_round: dict[str, int] = {}
        self.search_families = YIELD_FAMILIES if self.store.get('query_plan') in {'yield', 'dense'} else FAMILIES

    def _stage(self, kind):
        store = None
        runner = None
        try:
            # Each thread owns its SQLite connection. WAL serializes short writes.
            store = ExperimentStore(self.store.path.parent)
            runner = SearchRunner(store, self.config, self.production, self.output)
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
                    try:
                        synchronizer = ProxySynchronizer(self.config)
                        for profile in self.config.google_proxy_profiles:
                            synchronizer.sync(profile)
                    except Exception as exc:
                        self.events.put(('proxy_error', type(exc).__name__))
                    # sync() enforces the shared cache's configured interval;
                    # poll it so a skipped startup sync does not add 30 minutes.
                    self.shutdown.wait(60)
        except Exception as exc:
            self.events.put(('stage_error', kind + ':' + type(exc).__name__))
        finally:
            if runner:
                for client in runner.clients.values():
                    client.close()
                if runner.whale:
                    runner.whale.session.close()
            if store:
                store.db.close()

    def _seed(self):
        languages = normalize_languages(self.store.get('languages'))
        seed_queries(self.store, self.config, time.time(), self.store.get('preflight'), languages, self.locales)
        if self.store.get('pipeline_seeded') or self.store.get('preflight'):
            return
        # Separate historical years expand finite SERP supply without changing provenance.
        this_year = datetime.now(timezone.utc).year
        for spec in base_keyword_specs():
            if spec.key.endswith(':topic') and spec.language in languages:
                for year in range(this_year - 4, this_year):
                    for locale in (candidate for candidate in self.locales if candidate.language == spec.language):
                        self.store.add_query('recent', f'{spec.query} after:{year}-01-01 before:{year+1}-01-01 '
                                             + EXCLUDE.lstrip(), spec.language, spec.query, locale_label=locale.label)
        domains, domain_audit = ranked_supply_domains(self.store.get('baseline_run_paths', []))
        self.store.set('query_supply_domain_audit', domain_audit)
        if self.store.get('query_plan') == 'dense':
            self.store.set('query_supply_cursor', dense_supply_plan(domains, languages, datetime.now(timezone.utc)))
            replenish_queries(self.store, self.locales)
            self.store.set('query_supply', {'plan': 'dense', 'site_weight': .8,
                                          'queries': self.store.db.execute('SELECT count(*) FROM queries').fetchone()[0]})
            self.store.set('pipeline_seeded', True)
            return
        for host in _ranked_domain_hosts(domains, 3, 60):
            for language in languages:
                topics = ('人工智能', '大模型', '机器学习', '智能体', '生成式AI') if language == 'zh' else (
                    'artificial intelligence', 'large language models', 'machine learning', 'AI agents', 'generative AI')
                for topic in topics:
                    for locale in (candidate for candidate in self.locales if candidate.language == language):
                        self.store.add_query('site', f'site:{host} {topic}', language, topic, locale_label=locale.label)
        if self.store.get('query_plan') == 'yield':
            self.store.set('query_supply_cursor', supply_plan(domains, languages, datetime.now(timezone.utc)))
            replenish_queries(self.store, self.locales)
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
            # A redirect target may be discovered after its alias was fetched.
            # This experiment counts first-seen unique content; fetching the
            # already validated canonical again can only produce a duplicate or
            # an update, which is a separate workload.
            self.store.db.execute(
                "UPDATE urls SET state='canonical_skipped',next_fetch=? WHERE last_fetch=0 "
                "AND state='pending' AND EXISTS(SELECT 1 FROM documents d "
                "WHERE d.canonical=urls.url AND d.quality='[]')",
                (self.store.get('deadline') + 86400,),
            )
        # Cover the 1,500-URL discovery backlog so a busy host at its head
        # cannot hide work for other domains from otherwise idle body slots.
        rows = self.store.due_urls(2048, now)
        for row in rows:
            if pool.busy >= pool.size:
                break
            item = dict(row)
            if pool.submit(item):
                with self.store.db:
                    self.store.db.execute("UPDATE urls SET state='fetching' WHERE url=?", (row['url'],))

    def _submit_searches(self, family_index):
        now = time.time()
        pending = self.store.db.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0]
        if pending >= 1500 or now < self.store.get('search_cooling_until', 0):
            return family_index
        while self.search_inflight < self.search_size and not self.jobs.full():
            # Untouched pages are always first. Retries only get a short burst
            # slot; otherwise one provider outage can consume the whole window.
            prefer_retry = self.search_dispatches % 4 == 3
            families = list(self.search_families)
            deficit = {
                family: self.store.next_page_deficit(family)
                for family in families
            }

            # Select the least-covered page number across active families, then
            # rotate families at that page. A bounded budget advances breadth
            # before refreshing an already-covered page 1 again.
            page_deficit: dict[int, int] = {}
            for pages in deficit.values():
                for page, count in pages.items():
                    page_deficit[int(page)] = page_deficit.get(int(page), 0) + count
            if not page_deficit:
                break
            # Choose the page number with the largest unfinished backlog, not
            # the smallest. Selecting the minimum would keep finishing page 1
            # forever because its remaining count becomes slightly smaller.
            available = sorted(page_deficit, key=lambda page: (-page_deficit[page], page))
            for target in available:
                for offset in range(len(families)):
                    index = (family_index + offset) % len(families)
                    family = families[index]
                    if not deficit[family].get(str(target)):
                        continue
                    row = self.store.claim_due_page(
                        family, target, now, prefer_retry=prefer_retry,
                    )
                    if row:
                        self.jobs.put_nowait(row)
                        self.search_inflight += 1
                        self.search_dispatches += 1
                        self.page_round[family] = target + 1
                        family_index = (index + 1) % len(families)
                        break
                else:
                    continue
                break
            else:
                # Every due row is leased or cooling.
                break

        return family_index

    def _configure_pool(self, pool):
        requested = (int(self.store.get('body_workers', self.body_size)),
                     int(self.store.get('body_max_rss_mib', 192)))
        current = (pool.size, pool.max_rss_mib)
        if requested != current:
            pool.resize(*requested)
            self.body_size = pool.size
            self.store.set('body_resource_change_' + str(time.time_ns()),
                           {'at': time.time(), 'before': current, 'after': requested,
                            'fields': ['workers', 'max_rss_mib']})

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
            # Refresh the partial predicate when upgrading an existing ledger.
            store.db.execute('DROP INDEX IF EXISTS pipeline_url_ready')
            store.db.execute("CREATE INDEX pipeline_url_ready ON urls(last_fetch,first_seen,url) "
                             "WHERE state NOT IN ('fetching','baseline_skipped','canonical_skipped') "
                             "AND (last_fetch=0 OR last_seen>last_fetch)")
            store.db.execute('CREATE INDEX IF NOT EXISTS pipeline_document_url ON documents(url)')
        self._seed()
        restore_page_frontier(store, time.time())
        pool = BodyPool(self.body_size, max_rss_mib=self.store.get('body_max_rss_mib', 192))
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
                    # A reserved page has a future due time before its search
                    # record exists, just like inherited fresh coverage. Wait
                    # for all live reservations before deciding the cohort is done.
                    if not self.search_inflight:
                        replenish_queries(store, self.locales)
                    metrics = store.counts(detailed=False)
                    if store.get('query_plan') in {'yield', 'dense'}:
                        self.search_families, family_audit = adaptive_family_schedule(store)
                        store.set('family_schedule', {
                            'at': now, 'schedule': self.search_families, **family_audit,
                        })
                    with store.db:
                        store.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?)', (now, json.dumps(metrics)))
                    store.set('pipeline', {'body_busy': pool.busy, 'body_workers': self.body_size,
                                          'body_rss_recycle_mib': pool.max_rss_mib,
                                          'search_busy': self.search_inflight, 'search_workers': self.search_size,
                                          'search_threads': self.search_thread_count,
                                          'baseline_skipped': store.db.execute("SELECT count(*) FROM urls WHERE state='baseline_skipped'").fetchone()[0],
                                          'canonical_skipped': store.db.execute("SELECT count(*) FROM urls WHERE state='canonical_skipped'").fetchone()[0]})
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
            archive_audit(store)
            if not self.remote_only:
                export_report(store, self.output, full=True)


if __name__ == '__main__' and sys.argv[1:] == ['fetch']:
    serve_fetches()
