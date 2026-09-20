"""Isolated, durable experiment ledger. Never mutates production page/campaign data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline_urls(url TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS baseline_hashes(hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS queries(id TEXT PRIMARY KEY,family TEXT,query TEXT,language TEXT,locale_label TEXT NOT NULL DEFAULT 'legacy',
 topic TEXT,enabled INTEGER DEFAULT 1,last_served REAL DEFAULT 0,lease_until REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS schedule(query_id TEXT,page INTEGER,due REAL DEFAULT 0,
 PRIMARY KEY(query_id,page));
CREATE INDEX IF NOT EXISTS pipeline_query_family ON queries(family,enabled,last_served,id);
CREATE TABLE IF NOT EXISTS query_splits(parent_id TEXT PRIMARY KEY,created_at REAL,children TEXT);
CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY,query_id TEXT,page INTEGER,
 started REAL,finished REAL,status TEXT,error TEXT,cache_hit INTEGER,attempts TEXT,results TEXT,
 metadata TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS search_cache ON searches(query_id,page,finished);
CREATE INDEX IF NOT EXISTS search_latest ON searches(query_id,page,id);
CREATE TABLE IF NOT EXISTS urls(url TEXT PRIMARY KEY,title TEXT,first_seen REAL,last_seen REAL,
 last_fetch REAL DEFAULT 0,next_fetch REAL DEFAULT 0,state TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS discoveries(url TEXT,query_id TEXT,page INTEGER,position INTEGER,
 first_seen REAL,last_seen REAL,description TEXT,date TEXT,serp_module TEXT,
 PRIMARY KEY(url,query_id,page,position));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,url TEXT,canonical TEXT,hash TEXT,
 finished REAL,quality TEXT,classification TEXT,document TEXT,status TEXT,error TEXT,seconds REAL,
 published_at TEXT,publication_source TEXT);
CREATE INDEX IF NOT EXISTS doc_hash ON documents(hash);
CREATE INDEX IF NOT EXISTS doc_canonical ON documents(canonical);
CREATE TABLE IF NOT EXISTS outbox(document_id INTEGER PRIMARY KEY,payload TEXT,status TEXT,
 attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,finished REAL,error TEXT);
CREATE INDEX IF NOT EXISTS outbox_status_due ON outbox(status,next_attempt,document_id);
CREATE INDEX IF NOT EXISTS outbox_status_finished ON outbox(status,finished);
CREATE TABLE IF NOT EXISTS samples(at REAL PRIMARY KEY,metrics TEXT);
CREATE TABLE IF NOT EXISTS runtime(kind TEXT PRIMARY KEY,seconds REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS inflight_experiments(
  name TEXT PRIMARY KEY,started_at REAL NOT NULL,finished_at REAL,
  wait_seconds REAL NOT NULL,concurrency INTEGER NOT NULL,
  network_requests INTEGER NOT NULL,cache_reuses INTEGER NOT NULL,
  errors INTEGER NOT NULL,latencies TEXT NOT NULL
);
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def search_retry_delay(error: str, consecutive_failures: int = 1) -> int:
    """Return a durable page retry delay without treating failures as empty pages."""
    if error == 'google_unrecognized_page':
        # A valid HTTP response with an unknown layout rarely recovers within a
        # minute. Preserve the unfinished page while avoiding repeated parsing
        # of the same response at the expense of first-time page coverage.
        step = min(max(1, consecutive_failures), 5) - 1
        return min(21600, 1800 * (2 ** step))
    if error == 'google_proxy_unavailable':
        # Keep the first recovery check prompt, then stop a depleted proxy pool
        # from generating a large five-minute retry debt across every page.
        step = min(max(1, consecutive_failures), 4) - 1
        return min(1800, 300 * (2 ** step))
    if 'circuit' in error or 'cooling' in error:
        return 1800
    return 60


class ExperimentStore:
    def __init__(self, directory: Path, *, create: bool = False):
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "experiment.sqlite3"
        if not create and not self.path.is_file():
            raise ValueError("experiment does not exist")
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(queries)")}
            if 'locale_label' not in columns:
                self.db.execute("ALTER TABLE queries ADD COLUMN locale_label TEXT NOT NULL DEFAULT 'legacy'")
            if 'lease_until' not in columns:
                self.db.execute("ALTER TABLE queries ADD COLUMN lease_until REAL DEFAULT 0")
            document_columns = {
                row[1] for row in self.db.execute("PRAGMA table_info(documents)")
            }
            discovery_columns = {
                row[1] for row in self.db.execute("PRAGMA table_info(discoveries)")
            }
            if 'description' not in discovery_columns:
                self.db.execute("ALTER TABLE discoveries ADD COLUMN description TEXT")
            if 'date' not in discovery_columns:
                self.db.execute("ALTER TABLE discoveries ADD COLUMN date TEXT")
            if 'serp_module' not in discovery_columns:
                self.db.execute("ALTER TABLE discoveries ADD COLUMN serp_module TEXT")
            search_columns = {
                row[1] for row in self.db.execute("PRAGMA table_info(searches)")
            }
            if 'metadata' not in search_columns:
                self.db.execute("ALTER TABLE searches ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            if 'published_at' not in document_columns:
                self.db.execute("ALTER TABLE documents ADD COLUMN published_at TEXT")
            if 'publication_source' not in document_columns:
                self.db.execute("ALTER TABLE documents ADD COLUMN publication_source TEXT")
            self.db.execute("CREATE INDEX IF NOT EXISTS pipeline_query_lease "
                            "ON queries(family,enabled,lease_until,last_served,id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS doc_publication_source "
                            "ON documents(publication_source)")
            self.db.execute("CREATE INDEX IF NOT EXISTS query_locale ON queries(locale_label)")
            self.db.execute("CREATE INDEX IF NOT EXISTS discovery_serp_date ON discoveries(date)")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def baseline(self, urls=(), hashes=()):
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO baseline_urls VALUES(?)", ((u,) for u in urls if u))
            self.db.executemany("INSERT OR IGNORE INTO baseline_hashes VALUES(?)", ((h,) for h in hashes if h))

    def add_query(self, family: str, query: str, language: str, topic: str, pages: int = 11, locale_label: str = "legacy"):
        key = self.query_id(family, query, language, locale_label)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO queries(id,family,query,language,locale_label,topic) VALUES(?,?,?,?,?,?)",
                            (key, family, query, language, locale_label, topic))
            self.db.execute("UPDATE queries SET enabled=1,family=?,query=?,language=?,locale_label=?,topic=? WHERE id=?",
                            (family, query, language, locale_label, topic, key))
            self.db.executemany("INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)", ((key, p) for p in range(1, pages + 1)))
        return key

    def query_id(self, family: str, query: str, language: str, locale_label: str = "legacy") -> str:
        # Preserve pre-locale ledger IDs so resuming an old experiment cannot
        # duplicate its already scheduled frontier.
        if locale_label == "legacy":
            return digest(f"{family}:{language}:{query}")[:24]
        return digest(f"{family}:{language}:{locale_label}:{query}")[:24]

    def due_query(self, family: str, now: float):
        return self.db.execute(
            "SELECT q.*,s.page FROM queries q JOIN schedule s ON q.id=s.query_id "
            "LEFT JOIN searches x ON x.id=(SELECT max(y.id) FROM searches y "
            "WHERE y.query_id=s.query_id AND y.page=s.page) "
            "WHERE q.enabled=1 AND q.family=? AND s.due<=? "
            "ORDER BY CASE WHEN x.status='failed' THEN 0 WHEN x.id IS NULL THEN 1 ELSE 2 END,"
            "q.last_served,q.id,s.page LIMIT 1",
            (family, now),
        ).fetchone()

    def claim_due_query(
        self, family: str, now: float, lease_seconds: float = 300, *,
        prefer_retry: bool = True, prefer_continuation: bool = False,
    ):
        """Atomically reserve one page while allowing only one page per query."""
        leased_until = now + lease_seconds
        priority = (
            "CASE WHEN x.status='failed' THEN 0 WHEN x.id IS NULL THEN 1 ELSE 2 END"
            if prefer_retry else
            "CASE WHEN x.id IS NULL THEN 0 WHEN x.status='failed' THEN 1 ELSE 2 END"
        )
        continuation = (
            "CASE WHEN q.last_served>0 THEN 0 ELSE 1 END,"
            if prefer_continuation else ""
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT q.*,s.page FROM queries q JOIN schedule s ON q.id=s.query_id "
                "LEFT JOIN searches x ON x.id=(SELECT max(y.id) FROM searches y "
                "WHERE y.query_id=s.query_id AND y.page=s.page) "
                "WHERE q.enabled=1 AND q.family=? AND q.lease_until<=? AND s.due<=? "
                f"ORDER BY {priority},{continuation}q.last_served,q.id,s.page LIMIT 1",
                (family, now, now),
            ).fetchone()
            if row is None:
                self.db.commit()
                return None
            changed = self.db.execute(
                "UPDATE queries SET lease_until=?,last_served=? WHERE id=? AND lease_until<=?",
                (leased_until, now, row['id'], now),
            )
            if changed.rowcount != 1:
                self.db.rollback()
                return None
            self.db.execute(
                "UPDATE schedule SET due=max(due,?) WHERE query_id=? AND page=?",
                (leased_until, row['id'], row['page']),
            )
            self.db.commit()
            claimed = dict(row)
            claimed['_lease_until'] = leased_until
            return claimed
        except Exception:
            self.db.rollback()
            raise

    def next_page_deficit(self, family: str) -> dict[str, int]:
        """Return scheduled-minus-covered pages for each result-page number."""
        rows = self.db.execute(
            "WITH covered AS (SELECT DISTINCT query_id,page FROM searches WHERE status='success') "
            "SELECT s.page,count(*) deficit FROM schedule s JOIN queries q ON q.id=s.query_id "
            "LEFT JOIN covered c ON c.query_id=s.query_id AND c.page=s.page "
            "WHERE q.enabled=1 AND q.family=? AND c.query_id IS NULL GROUP BY s.page ORDER BY s.page",
            (family,),
        )
        return {str(row['page']): int(row['deficit']) for row in rows}

    def claim_due_page(
        self, family: str, page: int, now: float, lease_seconds: float = 300, *,
        prefer_retry: bool = False,
    ):
        """Claim a specific result page while retaining one-page-per-query semantics."""
        leased_until = now + lease_seconds
        priority = (
            "CASE WHEN x.status='failed' THEN 0 WHEN x.id IS NULL THEN 1 ELSE 2 END"
            if prefer_retry else
            "CASE WHEN x.id IS NULL THEN 0 WHEN x.status='failed' THEN 1 ELSE 2 END"
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT q.*,s.page FROM queries q JOIN schedule s ON q.id=s.query_id "
                "LEFT JOIN searches x ON x.id=(SELECT max(y.id) FROM searches y "
                "WHERE y.query_id=s.query_id AND y.page=s.page) "
                "WHERE q.enabled=1 AND q.family=? AND q.lease_until<=? AND s.page=? AND s.due<=? "
                f"ORDER BY {priority},q.last_served,q.id LIMIT 1",
                (family, now, page, now),
            ).fetchone()
            if row is None:
                self.db.commit()
                return None
            changed = self.db.execute(
                "UPDATE queries SET lease_until=?,last_served=? WHERE id=? AND lease_until<=?",
                (leased_until, now, row['id'], now),
            )
            if changed.rowcount != 1:
                self.db.rollback()
                return None
            self.db.execute(
                "UPDATE schedule SET due=max(due,?) WHERE query_id=? AND page=?",
                (leased_until, row['id'], row['page']),
            )
            self.db.commit()
            claimed = dict(row)
            claimed['_lease_until'] = leased_until
            return claimed
        except Exception:
            self.db.rollback()
            raise

    def cached(self, query_id: str, page: int, now: float):
        return self.db.execute("SELECT * FROM searches WHERE query_id=? AND page=? AND status='success' "
                               "AND finished>? ORDER BY id DESC LIMIT 1",
                               (query_id, page, now - (3600 if page <= 3 else 21600))).fetchone()

    def search(self, query, page: int, started: float, results: list[dict], attempts: list[dict], error: str = "", cache_hit: bool = False, metadata: dict | None = None):
        now = time.time()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            lease_until = query['_lease_until'] if '_lease_until' in query.keys() else None
            owns_lease = True
            if lease_until is not None:
                current = self.db.execute(
                    'SELECT lease_until FROM queries WHERE id=?', (query['id'],)
                ).fetchone()
                owns_lease = current is not None and current['lease_until'] == lease_until
            consecutive_failures = 1
            if error:
                for previous in self.db.execute(
                    "SELECT status,error FROM searches WHERE query_id=? AND page=? "
                    "ORDER BY id DESC LIMIT 5", (query['id'], page),
                ):
                    if previous['status'] != 'failed' or previous['error'] != error:
                        break
                    consecutive_failures += 1
            cursor = self.db.execute("INSERT INTO searches(query_id,page,started,finished,status,error,cache_hit,attempts,results,metadata) "
                                     "VALUES(?,?,?,?,?,?,?,?,?,?)", (query['id'], page, started, now, 'failed' if error else 'success',
                                     error, int(cache_hit), json.dumps(attempts), json.dumps(results, ensure_ascii=False),
                                     json.dumps(metadata or {}, ensure_ascii=False)))
            delay = search_retry_delay(error, consecutive_failures) if error else (3600 if page <= 3 else 21600)
            if owns_lease:
                self.db.execute("UPDATE schedule SET due=? WHERE query_id=? AND page=?",
                                (now + delay, query['id'], page))
                self.db.execute("UPDATE queries SET last_served=? WHERE id=?", (now, query['id']))
                if lease_until is not None:
                    self.db.execute("UPDATE queries SET lease_until=0 WHERE id=? AND lease_until=?",
                                    (query['id'], lease_until))
            for position, item in enumerate(results, 1):
                url = item['url']
                self.db.execute("INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?) "
                                "ON CONFLICT(url) DO UPDATE SET last_seen=excluded.last_seen", (url, item['title'], now, now))
                self.db.execute(
                    "INSERT INTO discoveries(url,query_id,page,position,first_seen,last_seen,description,date,serp_module) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url,query_id,page,position) "
                    "DO UPDATE SET last_seen=excluded.last_seen,description=excluded.description,"
                    "date=excluded.date,serp_module=excluded.serp_module",
                    (url, query['id'], page, position, now, now,
                     item.get('description'), item.get('date'), item.get('serp_module')),
                )
            search_id = cursor.lastrowid
            self.db.commit()
            return search_id
        except Exception:
            self.db.rollback()
            raise

    def due_urls(self, limit: int, now: float):
        return self.db.execute("SELECT u.*,(SELECT q.query FROM queries q JOIN discoveries d ON q.id=d.query_id "
                               "WHERE d.url=u.url ORDER BY d.first_seen LIMIT 1) AS query FROM urls u "
                               "WHERE state NOT IN ('fetching','baseline_skipped','canonical_skipped') "
                               "AND next_fetch<=? AND (last_fetch=0 OR last_seen>last_fetch) "
                               "ORDER BY last_fetch,first_seen,url LIMIT ?", (now, limit)).fetchall()

    def save_document(self, record: dict, quality: list[str], deadline: float):
        doc = record.get('document')
        now = record.get('finished', time.time())
        url = record['requested_url']
        canonical = doc['url'] if doc else url
        fingerprint = doc['content_hash'] if doc else ''
        classification = 'invalid'
        with self.db:
            if doc and not quality:
                same = self.db.execute("SELECT 1 FROM documents WHERE hash=? AND quality='[]' LIMIT 1", (fingerprint,)).fetchone()
                old_hash = self.db.execute("SELECT 1 FROM baseline_hashes WHERE hash=?", (fingerprint,)).fetchone()
                old_url = self.db.execute("SELECT 1 FROM baseline_urls WHERE url IN (?,?)", (url, canonical)).fetchone()
                previous = self.db.execute("SELECT 1 FROM documents WHERE canonical=? AND quality='[]' LIMIT 1", (canonical,)).fetchone()
                classification = 'duplicate' if same or old_hash else 'update' if previous else 'baseline' if old_url else 'new'
                if now > deadline:
                    classification = 'late_' + classification
            cursor = self.db.execute(
                "INSERT INTO documents(url,canonical,hash,finished,quality,classification,"
                "document,status,error,seconds,published_at,publication_source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    url, canonical, fingerprint, now,
                    json.dumps(quality, ensure_ascii=False), classification,
                    json.dumps(doc, ensure_ascii=False), record['status'],
                    record.get('error'), record['seconds'],
                    doc.get('published_at') if doc else None,
                    doc.get('publication_source') if doc else None,
                ),
            )
            self.db.execute("UPDATE urls SET last_fetch=?,next_fetch=?,state=? WHERE url=?",
                            (now, now + (21600 if doc else 3600), record['status'], url))
        return cursor.lastrowid, classification

    def record_inflight_experiment(self, name: str, *, wait_seconds: float, concurrency: int,
                                   network_requests: int, cache_reuses: int, errors: int,
                                   latencies: list[float]):
        now = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO inflight_experiments"
                "(name,started_at,finished_at,wait_seconds,concurrency,network_requests,cache_reuses,errors,latencies) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "started_at=excluded.started_at,finished_at=excluded.finished_at,"
                "wait_seconds=excluded.wait_seconds,concurrency=excluded.concurrency,"
                "network_requests=excluded.network_requests,cache_reuses=excluded.cache_reuses,"
                "errors=excluded.errors,latencies=excluded.latencies",
                (name, now, now, wait_seconds, concurrency, network_requests,
                 cache_reuses, errors, json.dumps(latencies)),
            )

    def inflight_experiments(self) -> list[dict]:
        return [dict(row) | {'latencies': json.loads(row['latencies'])}
                for row in self.db.execute(
                    "SELECT * FROM inflight_experiments ORDER BY name"
                )]

    def counts(self, cutoff: float | None = None, *, detailed: bool = True):
        end = cutoff or self.get('deadline', time.time())
        start = self.get('started_at', 0)
        counts = {row['classification']: row['n'] for row in self.db.execute(
            "SELECT classification,count(*) n FROM documents WHERE finished<=? GROUP BY classification", (end,))}
        searches = self.db.execute("SELECT count(*) n,sum(status='failed') failed,sum(cache_hit) cached,"
                                   "sum(status='success' AND results='[]') empty FROM searches WHERE finished<=?", (end,)).fetchone()
        counts.update(search_pages=searches['n'], search_failures=searches['failed'] or 0,
                      cache_hits=searches['cached'] or 0, empty_pages=searches['empty'] or 0,
                      unique_urls=self.db.execute("SELECT count(*) FROM urls WHERE first_seen<=?", (end,)).fetchone()[0])
        requests = successes = captchas = 0
        attempt_seconds = 0.0
        attempt_latencies = []
        scopes: dict[str, int] = {}
        for row in self.db.execute("SELECT attempts FROM searches WHERE finished<=?", (end,)):
            for attempt in json.loads(row[0]):
                requests += 1
                successes += bool(attempt['success'])
                captchas += attempt.get('error') == 'google_captcha'
                scopes[attempt.get('error_scope') or 'unknown'] = scopes.get(attempt.get('error_scope') or 'unknown', 0) + 1
                seconds = float(attempt['seconds'])
                attempt_seconds += seconds
                attempt_latencies.append(seconds)
        counts['google_requests'] = requests
        counts['google_successes'] = successes
        counts['google_captchas'] = captchas
        counts['google_attempt_seconds'] = round(attempt_seconds, 2)
        attempt_latencies.sort()
        for percentile in (50, 95):
            index = max(0, (len(attempt_latencies) * percentile + 99) // 100 - 1)
            counts[f'google_attempt_p{percentile}_seconds'] = (
                round(attempt_latencies[index], 3) if attempt_latencies else None
            )
        counts['google_error_rate'] = round((requests - successes) / requests, 4) if requests else 0.0
        counts['google_error_scopes'] = scopes
        counts['google_captcha_rate'] = round(captchas / requests, 4) if requests else 0.0
        counts['fetch_seconds'] = round(self.db.execute("SELECT coalesce(sum(seconds),0) FROM documents WHERE finished<=?", (end,)).fetchone()[0], 2)
        for row in self.db.execute("SELECT status,count(*) n FROM outbox GROUP BY status"):
            counts['whale_' + row['status']] = row['n']
        counts['whale_confirmed_before_deadline'] = self.db.execute(
            "SELECT count(*) FROM outbox WHERE status IN ('accepted','duplicate') AND finished<=?", (end,)).fetchone()[0]
        counts['publication_sources'] = {
            row[0]: row[1] for row in self.db.execute(
                "SELECT coalesce(publication_source,'missing'),count(*) FROM documents "
                "WHERE classification='new' AND quality='[]' AND finished<=? GROUP BY 1",
                (end,),
            )
        }
        counts['locales'] = {}
        query_columns = {row[1] for row in self.db.execute("PRAGMA table_info(queries)")}
        has_locale = 'locale_label' in query_columns
        locale_labels = [row[0] for row in self.db.execute(
            "SELECT DISTINCT locale_label FROM queries ORDER BY locale_label"
        )] if has_locale else ['legacy']
        for label in locale_labels:
            # Ledgers written before the locale matrix have no locale_label
            # column; aggregate every query under a single 'legacy' bucket.
            locale_where = 'WHERE q.locale_label=? AND ' if has_locale else 'WHERE '
            locale_params = (label,) if has_locale else ()
            counts['locales'][label] = {
                'scheduled_pages': self.db.execute(
                    'SELECT count(*) FROM schedule s JOIN queries q ON q.id=s.query_id'
                    + (' WHERE q.locale_label=?' if has_locale else ''),
                    locale_params,
                ).fetchone()[0],
                'successful_pages': self.db.execute(
                    'SELECT count(*) FROM searches s JOIN queries q ON q.id=s.query_id '
                    + locale_where + "s.status='success' AND s.finished<=?",
                    locale_params + (end,),
                ).fetchone()[0],
                'candidate_urls': self.db.execute(
                    'SELECT count(DISTINCT x.url) FROM discoveries x JOIN queries q ON q.id=x.query_id '
                    + locale_where + 'x.first_seen<=?',
                    locale_params + (end,),
                ).fetchone()[0],
                'valid_documents': self.db.execute(
                    "SELECT count(DISTINCT d.id) FROM documents d JOIN discoveries x ON d.url=x.url "
                    "JOIN queries q ON q.id=x.query_id "
                    + locale_where
                    + "d.quality='[]' AND d.classification='new' AND d.finished<=?",
                    locale_params + (end,),
                ).fetchone()[0],
                'accepted_documents': self.db.execute(
                    "SELECT count(DISTINCT o.document_id) FROM outbox o JOIN documents d ON d.id=o.document_id "
                    "JOIN discoveries x ON d.url=x.url JOIN queries q ON q.id=x.query_id "
                    + locale_where
                    + "d.quality='[]' AND d.classification='new' "
                    "AND o.status IN ('accepted','duplicate') AND o.finished<=?",
                    locale_params + (end,),
                ).fetchone()[0],
            }
            item = counts['locales'][label]
            item['accepted_per_successful_page'] = round(
                item['accepted_documents'] / item['successful_pages'], 4
            ) if item['successful_pages'] else None
        elapsed_end = min(time.time(), end)
        if self.get('state') in {'complete','storage_stopped','stopped'}:
            elapsed_end = min(elapsed_end, self.get('finished_at', elapsed_end))
        counts['elapsed_seconds'] = round(max(0, elapsed_end - start), 1)
        counts['by_family'] = {}
        for family in (('topic', 'event', 'site', 'recent') if detailed else ()):
            row = self.db.execute("SELECT count(DISTINCT d.hash) FROM documents d JOIN discoveries x ON d.url=x.url "
                                  "JOIN queries q ON q.id=x.query_id WHERE q.family=? AND d.quality='[]' "
                                  "AND EXISTS(SELECT 1 FROM documents n WHERE n.hash=d.hash AND n.classification='new' AND n.finished<=?) "
                                  "AND d.finished<=? AND x.first_seen<=?", (family, end, end, end)).fetchone()
            exclusive = self.db.execute("SELECT count(*) FROM (SELECT d.hash FROM documents d JOIN discoveries x ON d.url=x.url "
                                       "JOIN queries q ON q.id=x.query_id WHERE d.quality='[]' AND d.finished<=? AND x.first_seen<=? "
                                       "AND EXISTS(SELECT 1 FROM documents n WHERE n.hash=d.hash AND n.classification='new' AND n.finished<=?) "
                                       "GROUP BY d.hash HAVING count(DISTINCT q.family)=1 AND min(q.family)=?)", (end, end, end, family)).fetchone()[0]
            counts['by_family'][family] = {'new_content_covered': row[0], 'exclusive_new_content': exclusive}
            requests = self.db.execute("SELECT count(*) pages,coalesce(sum(json_array_length(s.attempts)),0) attempts,"
                                       "coalesce(sum(s.status='failed'),0) failures FROM searches s JOIN queries q ON s.query_id=q.id "
                                       "WHERE q.family=? AND s.finished<=?", (family,end)).fetchone()
            counts['by_family'][family].update(dict(requests))
        counts['page_coverage'] = {}
        if detailed:
            page_bodies = {
                int(row['page']): int(row['valid'])
                for row in self.db.execute(
                    "SELECT x.page,count(DISTINCT d.hash) valid FROM discoveries x "
                    "JOIN documents d ON d.url=x.url WHERE x.first_seen<=? "
                    "AND d.quality='[]' AND d.classification='new' AND d.finished<=? "
                    "GROUP BY x.page", (end, end),
                )
            }
            page_deliverable = {
                int(row['page']): int(row['deliverable'])
                for row in self.db.execute(
                    "SELECT x.page,count(DISTINCT d.id) deliverable FROM discoveries x "
                    "JOIN documents d ON d.url=x.url LEFT JOIN outbox o ON o.document_id=d.id "
                    "WHERE x.first_seen<=? AND d.quality='[]' AND d.classification='new' "
                    "AND d.finished<=? AND d.published_at IS NOT NULL "
                    "GROUP BY x.page", (end, end),
                )
            }
            page_accepted = {
                int(row['page']): int(row['accepted'])
                for row in self.db.execute(
                    "SELECT x.page,count(DISTINCT o.document_id) accepted FROM discoveries x "
                    "JOIN documents d ON d.url=x.url JOIN outbox o ON o.document_id=d.id "
                    "WHERE x.first_seen<=? AND x.last_seen<=? AND d.finished<=? "
                    "AND o.status IN ('accepted','duplicate') AND o.finished<=? GROUP BY x.page",
                    (end, end, end, end),
                )
            }
            coverage_rows = self.db.execute(
                "WITH page_state AS (SELECT query_id,page,max(status='success') success,count(*) attempts,"
                "coalesce(sum(CASE WHEN status='success' THEN json_array_length(results) ELSE 0 END),0) candidates "
                "FROM searches WHERE finished<=? GROUP BY query_id,page), unique_urls AS ("
                "SELECT page,count(DISTINCT url) n FROM discoveries WHERE first_seen<=? GROUP BY page) "
                "SELECT s.page,count(*) scheduled,coalesce(sum(p.success),0) covered,"
                "coalesce(sum(p.attempts>0 AND p.success=0),0) retrying,"
                "coalesce(sum(p.candidates),0) candidates,coalesce(max(u.n),0) unique_urls "
                "FROM schedule s LEFT JOIN page_state p ON p.query_id=s.query_id AND p.page=s.page "
                "LEFT JOIN unique_urls u ON u.page=s.page GROUP BY s.page ORDER BY s.page",
                (end, end),
            )
            for row in coverage_rows:
                item = dict(row)
                item['pending'] = item['scheduled'] - item['covered'] - item['retrying']
                page = int(item['page'])
                item['valid_documents'] = page_bodies.get(page, 0)
                item['deliverable_documents'] = page_deliverable.get(page, 0)
                item['accepted_documents'] = page_accepted.get(page, 0)
                counts['page_coverage'][str(item.pop('page'))] = item
        counts['hourly'] = [dict(r) for r in self.db.execute(
            "SELECT cast((finished-?)/3600 AS INTEGER)+1 hour,count(*) new_documents FROM documents "
            "WHERE classification='new' AND finished<=? GROUP BY hour", (start, end))]
        counts['first_6h_new'] = self.db.execute("SELECT count(*) FROM documents WHERE classification='new' AND finished<=?", (min(start+21600,end),)).fetchone()[0]
        counts['remaining_18h_new'] = counts.get('new', 0) - counts['first_6h_new']
        elapsed = counts['elapsed_seconds']
        counts['first_6h_new_per_minute'] = round(counts['first_6h_new']*60/max(1,min(elapsed,21600)),3)
        counts['remaining_18h_new_per_minute'] = round(counts['remaining_18h_new']*60/max(1,elapsed-21600),3) if elapsed>21600 else None
        counts['late_results'] = self.db.execute('SELECT count(*) FROM documents WHERE finished>?', (end,)).fetchone()[0]
        counts['late_classifications'] = {r[0]:r[1] for r in self.db.execute('SELECT classification,count(*) FROM documents WHERE finished>? GROUP BY classification', (end,))}
        counts['runtime_seconds'] = {r['kind']: round(r['seconds'], 1) for r in self.db.execute('SELECT * FROM runtime')}
        return counts
