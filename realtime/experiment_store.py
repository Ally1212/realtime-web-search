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
CREATE TABLE IF NOT EXISTS queries(id TEXT PRIMARY KEY,family TEXT,query TEXT,language TEXT,
 topic TEXT,enabled INTEGER DEFAULT 1,last_served REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS schedule(query_id TEXT,page INTEGER,due REAL DEFAULT 0,
 PRIMARY KEY(query_id,page));
CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY,query_id TEXT,page INTEGER,
 started REAL,finished REAL,status TEXT,error TEXT,cache_hit INTEGER,attempts TEXT,results TEXT);
CREATE INDEX IF NOT EXISTS search_cache ON searches(query_id,page,finished);
CREATE TABLE IF NOT EXISTS urls(url TEXT PRIMARY KEY,title TEXT,first_seen REAL,last_seen REAL,
 last_fetch REAL DEFAULT 0,next_fetch REAL DEFAULT 0,state TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS discoveries(url TEXT,query_id TEXT,page INTEGER,position INTEGER,
 first_seen REAL,last_seen REAL,PRIMARY KEY(url,query_id,page,position));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,url TEXT,canonical TEXT,hash TEXT,
 finished REAL,quality TEXT,classification TEXT,document TEXT,status TEXT,error TEXT,seconds REAL);
CREATE INDEX IF NOT EXISTS doc_hash ON documents(hash);
CREATE INDEX IF NOT EXISTS doc_canonical ON documents(canonical);
CREATE TABLE IF NOT EXISTS outbox(document_id INTEGER PRIMARY KEY,payload TEXT,status TEXT,
 attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,finished REAL,error TEXT);
CREATE TABLE IF NOT EXISTS samples(at REAL PRIMARY KEY,metrics TEXT);
CREATE TABLE IF NOT EXISTS runtime(kind TEXT PRIMARY KEY,seconds REAL DEFAULT 0);
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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

    def add_query(self, family: str, query: str, language: str, topic: str, pages: int = 11):
        key = digest(f"{family}:{language}:{query}")[:24]
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO queries(id,family,query,language,topic) VALUES(?,?,?,?,?)",
                            (key, family, query, language, topic))
            self.db.execute("UPDATE queries SET enabled=1 WHERE id=?", (key,))
            self.db.executemany("INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)", ((key, p) for p in range(1, pages + 1)))
        return key

    def due_query(self, family: str, now: float):
        return self.db.execute("SELECT q.*,s.page FROM queries q JOIN schedule s ON q.id=s.query_id "
                               "WHERE q.enabled=1 AND q.family=? AND s.due<=? ORDER BY q.last_served,s.page,q.id LIMIT 1",
                               (family, now)).fetchone()

    def cached(self, query_id: str, page: int, now: float):
        return self.db.execute("SELECT * FROM searches WHERE query_id=? AND page=? AND status='success' "
                               "AND finished>? ORDER BY id DESC LIMIT 1",
                               (query_id, page, now - (3600 if page <= 3 else 21600))).fetchone()

    def search(self, query, page: int, started: float, results: list[dict], attempts: list[dict], error: str = "", cache_hit: bool = False):
        now = time.time()
        with self.db:
            cursor = self.db.execute("INSERT INTO searches(query_id,page,started,finished,status,error,cache_hit,attempts,results) "
                                     "VALUES(?,?,?,?,?,?,?,?,?)", (query['id'], page, started, now, 'failed' if error else 'success',
                                     error, int(cache_hit), json.dumps(attempts), json.dumps(results, ensure_ascii=False)))
            delay = (1800 if 'circuit' in error or 'cooling' in error else 60) if error else (3600 if page <= 3 else 21600)
            self.db.execute("UPDATE schedule SET due=? WHERE query_id=? AND page=?", (now + delay, query['id'], page))
            self.db.execute("UPDATE queries SET last_served=? WHERE id=?", (now, query['id']))
            for position, item in enumerate(results, 1):
                url = item['url']
                self.db.execute("INSERT INTO urls(url,title,first_seen,last_seen) VALUES(?,?,?,?) "
                                "ON CONFLICT(url) DO UPDATE SET last_seen=excluded.last_seen", (url, item['title'], now, now))
                self.db.execute("INSERT INTO discoveries VALUES(?,?,?,?,?,?) ON CONFLICT(url,query_id,page,position) "
                                "DO UPDATE SET last_seen=excluded.last_seen", (url, query['id'], page, position, now, now))
        return cursor.lastrowid

    def due_urls(self, limit: int, now: float):
        return self.db.execute("SELECT u.*,(SELECT q.query FROM queries q JOIN discoveries d ON q.id=d.query_id "
                               "WHERE d.url=u.url ORDER BY d.first_seen LIMIT 1) AS query FROM urls u "
                               "WHERE state<>'fetching' AND next_fetch<=? AND (last_fetch=0 OR last_seen>last_fetch) "
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
            cursor = self.db.execute("INSERT INTO documents(url,canonical,hash,finished,quality,classification,document,status,error,seconds) "
                                     "VALUES(?,?,?,?,?,?,?,?,?,?)", (url, canonical, fingerprint, now, json.dumps(quality, ensure_ascii=False),
                                     classification, json.dumps(doc, ensure_ascii=False), record['status'], record.get('error'), record['seconds']))
            self.db.execute("UPDATE urls SET last_fetch=?,next_fetch=?,state=? WHERE url=?",
                            (now, now + (21600 if doc else 3600), record['status'], url))
        return cursor.lastrowid, classification

    def counts(self, cutoff: float | None = None):
        end = cutoff or self.get('deadline', time.time())
        start = self.get('started_at', 0)
        counts = {row['classification']: row['n'] for row in self.db.execute(
            "SELECT classification,count(*) n FROM documents WHERE finished<=? GROUP BY classification", (end,))}
        searches = self.db.execute("SELECT count(*) n,sum(status='failed') failed,sum(cache_hit) cached,"
                                   "sum(status='success' AND results='[]') empty FROM searches WHERE finished<=?", (end,)).fetchone()
        counts.update(search_pages=searches['n'], search_failures=searches['failed'] or 0,
                      cache_hits=searches['cached'] or 0, empty_pages=searches['empty'] or 0,
                      unique_urls=self.db.execute("SELECT count(*) FROM urls WHERE first_seen<=?", (end,)).fetchone()[0])
        attempts = [a for r in self.db.execute("SELECT attempts FROM searches WHERE finished<=?", (end,)) for a in json.loads(r[0])]
        counts['google_requests'] = len(attempts)
        counts['google_successes'] = sum(a['success'] for a in attempts)
        counts['google_captchas'] = sum(a.get('error') == 'google_captcha' for a in attempts)
        counts['google_attempt_seconds'] = round(sum(a['seconds'] for a in attempts), 2)
        counts['fetch_seconds'] = round(self.db.execute("SELECT coalesce(sum(seconds),0) FROM documents WHERE finished<=?", (end,)).fetchone()[0], 2)
        for row in self.db.execute("SELECT status,count(*) n FROM outbox GROUP BY status"):
            counts['whale_' + row['status']] = row['n']
        counts['whale_confirmed_before_deadline'] = self.db.execute(
            "SELECT count(*) FROM outbox WHERE status IN ('accepted','duplicate') AND finished<=?", (end,)).fetchone()[0]
        elapsed_end = min(time.time(), end)
        if self.get('state') in {'complete','storage_stopped','stopped'}:
            elapsed_end = min(elapsed_end, self.get('finished_at', elapsed_end))
        counts['elapsed_seconds'] = round(max(0, elapsed_end - start), 1)
        counts['by_family'] = {}
        for family in ('topic', 'event', 'site', 'recent'):
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
