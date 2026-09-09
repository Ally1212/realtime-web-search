from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row


_POOLS: dict[tuple[str, int, int], ConnectionPool[Any]] = {}
_POOLS_LOCK = threading.Lock()
_INITIALIZED_DSNS: set[str] = set()
_INITIALIZE_LOCK = threading.Lock()


def _shared_pool(dsn: str) -> ConnectionPool[Any]:
    role = os.getenv("PROCESS_ROLE", "collector").strip().lower()
    default_min, default_max = ((1, 4) if role == "web" else (4, 16))
    min_size = int(os.getenv("POSTGRES_POOL_MIN", str(default_min)))
    max_size = int(os.getenv("POSTGRES_POOL_MAX", str(default_max)))
    if min_size < 0 or max_size < 1 or min_size > max_size:
        raise ValueError("invalid PostgreSQL pool size")
    key = (dsn, min_size, max_size)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ConnectionPool(
                conninfo=dsn,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row, "connect_timeout": 5},
                timeout=10,
                open=True,
            )
            _POOLS[key] = pool
        return pool


SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id uuid PRIMARY KEY,
  query text NOT NULL,
  aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
  daily_target integer NOT NULL CHECK (daily_target BETWEEN 1 AND 1000000),
  proxy_profile text NOT NULL CHECK (proxy_profile IN ('private','public','direct')),
  status text NOT NULL CHECK (status IN ('active','paused','stopped','failed')),
  discovered bigint NOT NULL DEFAULT 0,
  fetched bigint NOT NULL DEFAULT 0,
  failed bigint NOT NULL DEFAULT 0,
  duplicates bigint NOT NULL DEFAULT 0,
  irrelevant bigint NOT NULL DEFAULT 0,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pages (
  id bigserial PRIMARY KEY,
  url text NOT NULL UNIQUE,
  content_hash char(64) NOT NULL UNIQUE,
  title text NOT NULL,
  summary text NOT NULL,
  content text NOT NULL DEFAULT '',
  language varchar(8),
  http_status integer NOT NULL,
  fetched_at timestamptz NOT NULL,
  source_engines jsonb NOT NULL DEFAULT '[]'::jsonb,
  indexed_at timestamptz
);
ALTER TABLE pages ADD COLUMN IF NOT EXISTS indexed_at timestamptz;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS content text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS campaign_pages (
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  page_id bigint NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  first_seen timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (campaign_id, page_id)
);
CREATE INDEX IF NOT EXISTS campaign_pages_daily ON campaign_pages(campaign_id, first_seen);
CREATE INDEX IF NOT EXISTS campaign_pages_page ON campaign_pages(page_id);
CREATE TABLE IF NOT EXISTS crawl_events (
  id bigserial PRIMARY KEY,
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  url text,
  status text NOT NULL,
  http_status integer,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_events_recent ON crawl_events(campaign_id, created_at DESC);
CREATE TABLE IF NOT EXISTS whale_task_runs (
  task_id text PRIMARY KEY,
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  dataset_id text NOT NULL,
  source_platform text NOT NULL,
  task_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  cursor text,
  status text NOT NULL CHECK (status IN ('running','paused','canceled','succeeded','failed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE whale_task_runs ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS whale_task_runs_campaign ON whale_task_runs(campaign_id);
CREATE TABLE IF NOT EXISTS whale_ingest_outbox (
  id bigserial PRIMARY KEY,
  task_id text NOT NULL REFERENCES whale_task_runs(task_id) ON DELETE CASCADE,
  source_record_key text NOT NULL UNIQUE,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','delivered','rejected')),
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
  delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS whale_ingest_outbox_pending ON whale_ingest_outbox(task_id, status, id);
CREATE TABLE IF NOT EXISTS discovery_cursors (
  campaign_id uuid PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
  history_before date NOT NULL DEFAULT CURRENT_DATE,
  consecutive_empty integer NOT NULL DEFAULT 0,
  last_candidates integer NOT NULL DEFAULT 0,
  last_novel integer NOT NULL DEFAULT 0,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS continuous_keywords (
  keyword_key text PRIMARY KEY,
  concept_id text NOT NULL,
  query text NOT NULL,
  aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
  language varchar(8) NOT NULL CHECK (language IN ('en','zh')),
  category text NOT NULL,
  kind text NOT NULL CHECK (kind='base'),
  state text NOT NULL CHECK (state IN ('active','cooldown','retired')),
  priority integer NOT NULL DEFAULT 50,
  score double precision NOT NULL DEFAULT 50,
  runs integer NOT NULL DEFAULT 0,
  low_yield_runs integer NOT NULL DEFAULT 0,
  last_candidates integer NOT NULL DEFAULT 0,
  last_fetched integer NOT NULL DEFAULT 0,
  last_delivered integer NOT NULL DEFAULT 0,
  last_failed integer NOT NULL DEFAULT 0,
  last_duplicates integer NOT NULL DEFAULT 0,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_run_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS continuous_keywords_due
  ON continuous_keywords(state,next_run_at,last_run_at);
CREATE TABLE IF NOT EXISTS continuous_keyword_runs (
  id bigserial PRIMARY KEY,
  keyword_key text NOT NULL REFERENCES continuous_keywords(keyword_key) ON DELETE CASCADE,
  candidates integer NOT NULL DEFAULT 0,
  fetched integer NOT NULL DEFAULT 0,
  delivered integer NOT NULL DEFAULT 0,
  failed integer NOT NULL DEFAULT 0,
  duplicates integer NOT NULL DEFAULT 0,
  duration_seconds double precision NOT NULL DEFAULT 0,
  score double precision NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS continuous_keyword_runs_recent
  ON continuous_keyword_runs(keyword_key,created_at DESC);
CREATE TABLE IF NOT EXISTS crawl_domain_health (
  domain text PRIMARY KEY,
  attempts bigint NOT NULL DEFAULT 0,
  failures bigint NOT NULL DEFAULT 0,
  consecutive_failures integer NOT NULL DEFAULT 0,
  cooldown_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crawl_browser_health (
  domain text PRIMARY KEY,
  attempts bigint NOT NULL DEFAULT 0,
  successes bigint NOT NULL DEFAULT 0,
  disabled_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crawl_stage_totals (
  stage text PRIMARY KEY,
  observations bigint NOT NULL DEFAULT 0,
  total_seconds double precision NOT NULL DEFAULT 0,
  last_seconds double precision NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS continuous_runtime (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id=1),
  enabled boolean NOT NULL DEFAULT true,
  current_concurrency integer NOT NULL DEFAULT 8,
  min_concurrency integer NOT NULL DEFAULT 4,
  max_concurrency integer NOT NULL DEFAULT 12,
  state text NOT NULL DEFAULT 'warming_up',
  reason text NOT NULL DEFAULT 'startup',
  limited_ratio double precision NOT NULL DEFAULT 0,
  success_rate double precision NOT NULL DEFAULT 0,
  outbox_pending integer NOT NULL DEFAULT 0,
  upload_errors integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crawl_proxy_usage (
  proxy_key_hash char(64) NOT NULL,
  profile text NOT NULL,
  requests bigint NOT NULL DEFAULT 0,
  last_used_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(proxy_key_hash,profile)
);
CREATE TABLE IF NOT EXISTS discovery_source_runtime (
  source text PRIMARY KEY,
  state text NOT NULL DEFAULT 'warming_up',
  current_rps double precision NOT NULL DEFAULT 0.5,
  next_request_at timestamptz NOT NULL DEFAULT now(),
  circuit_until timestamptz,
  window_started_at timestamptz NOT NULL DEFAULT now(),
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  requests_window bigint NOT NULL DEFAULT 0,
  successes_window bigint NOT NULL DEFAULT 0,
  errors_window bigint NOT NULL DEFAULT 0,
  limited_window bigint NOT NULL DEFAULT 0,
  captcha_window bigint NOT NULL DEFAULT 0,
  requests_total bigint NOT NULL DEFAULT 0,
  successes_total bigint NOT NULL DEFAULT 0,
  errors_total bigint NOT NULL DEFAULT 0,
  limited_total bigint NOT NULL DEFAULT 0,
  captcha_total bigint NOT NULL DEFAULT 0,
  result_count bigint NOT NULL DEFAULT 0,
  novel_count bigint NOT NULL DEFAULT 0,
  cache_hits bigint NOT NULL DEFAULT 0,
  cache_misses bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS discovery_query_cache (
  cache_key char(64) PRIMARY KEY,
  source text NOT NULL,
  query_hash char(64) NOT NULL,
  locale text NOT NULL,
  page integer NOT NULL DEFAULT 1,
  payload jsonb NOT NULL,
  etag text,
  last_modified text,
  result_count integer NOT NULL DEFAULT 0,
  novel_count integer NOT NULL DEFAULT 0,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS discovery_query_cache_expiry
  ON discovery_query_cache(expires_at);
CREATE TABLE IF NOT EXISTS google_proxy_sessions (
  proxy_key_hash char(64) PRIMARY KEY,
  locale text NOT NULL DEFAULT '',
  successes bigint NOT NULL DEFAULT 0,
  failures bigint NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  cooldown_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS google_page_frontier (
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  query_hash char(64) NOT NULL,
  query text NOT NULL,
  locale text NOT NULL,
  next_page integer NOT NULL DEFAULT 1 CHECK (next_page >= 1),
  max_page integer NOT NULL DEFAULT 11 CHECK (max_page >= 1),
  batch_start integer,
  batch_end integer,
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','running','completed','cooling','failed')),
  attempts integer NOT NULL DEFAULT 0,
  lease_token uuid,
  lease_expires_at timestamptz,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  page_stats jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (campaign_id,query_hash,locale)
);
CREATE INDEX IF NOT EXISTS google_page_frontier_due
  ON google_page_frontier(state,next_run_at,lease_expires_at);
"""


@dataclass(frozen=True)
class PageRecord:
    url: str
    content_hash: str
    title: str
    summary: str
    content: str
    language: str
    http_status: int
    fetched_at: str
    source_engines: tuple[str, ...]


class CampaignStore:
    COUNTERS = {"discovered", "fetched", "failed", "duplicates", "irrelevant"}

    def __init__(self, dsn: str, initialize: bool = True):
        self.dsn = dsn
        self.pool = _shared_pool(dsn)
        if initialize:
            with _INITIALIZE_LOCK:
                if dsn not in _INITIALIZED_DSNS:
                    self.initialize()
                    _INITIALIZED_DSNS.add(dsn)

    def connect(self):  # type: ignore[no-untyped-def]
        """Borrow a connection; callers keep the existing `with` contract."""
        return self.pool.connection()

    def browser_domain_allowed(self, domain: str, *, require_success: bool = False) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT attempts,successes,disabled_until FROM crawl_browser_health WHERE domain=%s",
                (domain.lower(),),
            ).fetchone()
        if not row:
            return not require_success
        if row["disabled_until"] and row["disabled_until"] > datetime.now(timezone.utc):
            return False
        if require_success and int(row["successes"]) == 0:
            return False
        return not (
            int(row["attempts"]) >= 5
            and int(row["successes"]) / max(int(row["attempts"]), 1) < 0.1
        )

    def record_browser_result(self, domain: str, success: bool) -> None:
        domain = domain.lower()
        with self.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    "INSERT INTO crawl_browser_health(domain,attempts,successes) VALUES(%s,1,%s) "
                    "ON CONFLICT(domain) DO UPDATE SET attempts=crawl_browser_health.attempts+1,"
                    "successes=crawl_browser_health.successes+EXCLUDED.successes,updated_at=now() "
                    "RETURNING attempts,successes",
                    (domain, int(success)),
                ).fetchone()
                if int(row["attempts"]) >= 5 and int(row["successes"]) / int(row["attempts"]) < 0.1:
                    connection.execute(
                        "UPDATE crawl_browser_health SET disabled_until=now()+interval '7 days' WHERE domain=%s",
                        (domain,),
                    )

    def observe_stage(self, stage: str, seconds: float, observations: int = 1) -> None:
        if observations <= 0:
            return
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO crawl_stage_totals(stage,observations,total_seconds,last_seconds) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(stage) DO UPDATE SET "
                "observations=crawl_stage_totals.observations+EXCLUDED.observations,"
                "total_seconds=crawl_stage_totals.total_seconds+EXCLUDED.total_seconds,"
                "last_seconds=EXCLUDED.last_seconds,updated_at=now()",
                (stage[:80], observations, max(seconds, 0), max(seconds / observations, 0)),
            )

    def continuous_runtime_health(self, minutes: int = 5) -> dict[str, float | int]:
        window = max(1, minutes)
        with self.connect() as connection:
            row = connection.execute(
                "WITH cc AS (SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id LIKE 'continuous:%%'), ev AS (SELECT ce.* FROM crawl_events ce "
                "JOIN cc ON cc.id=ce.campaign_id WHERE ce.created_at>=now()-(%s*interval '1 minute')) "
                "SELECT (SELECT count(*) FROM whale_ingest_outbox WHERE task_id LIKE 'continuous:%%' "
                "AND status='delivered' AND delivered_at>=now()-(%s*interval '1 minute')) AS delivered,"
                "(SELECT count(*) FROM ev WHERE status IN ('retryable_failed','permanent_failed')) AS failed,"
                "(SELECT count(*) FROM ev WHERE http_status IN (403,429) OR "
                "(status='discovery_failed' AND error_code ILIKE '%%captcha%%')) AS limited,"
                "(SELECT count(*) FROM whale_ingest_outbox WHERE task_id LIKE 'continuous:%%' "
                "AND status='pending') AS pending,"
                "(SELECT count(*) FROM whale_ingest_outbox WHERE task_id LIKE 'continuous:%%' "
                "AND updated_at>=now()-(%s*interval '1 minute') AND "
                "(status='rejected' OR (status='pending' AND attempts>0))) AS upload_errors",
                (window, window, window),
            ).fetchone()
        delivered, failed = int(row["delivered"]), int(row["failed"])
        attempts = max(delivered + failed, 1)
        return {
            "delivered": delivered,
            "failed": failed,
            "limited": int(row["limited"]),
            "limited_ratio": round(int(row["limited"]) / attempts, 4),
            "success_rate": round(delivered / attempts, 4),
            "outbox_pending": int(row["pending"]),
            "upload_errors": int(row["upload_errors"]),
        }

    def update_continuous_runtime(
        self, *, enabled: bool, current: int, minimum: int, maximum: int,
        state: str, reason: str, health: dict[str, float | int] | None = None,
    ) -> None:
        values = health or {}
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO continuous_runtime(id,enabled,current_concurrency,min_concurrency,"
                "max_concurrency,state,reason,limited_ratio,success_rate,outbox_pending,upload_errors) "
                "VALUES(1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                "enabled=excluded.enabled,current_concurrency=excluded.current_concurrency,"
                "min_concurrency=excluded.min_concurrency,max_concurrency=excluded.max_concurrency,"
                "state=excluded.state,reason=excluded.reason,limited_ratio=excluded.limited_ratio,"
                "success_rate=excluded.success_rate,outbox_pending=excluded.outbox_pending,"
                "upload_errors=excluded.upload_errors,updated_at=now()",
                (
                    enabled, current, minimum, maximum, state, reason,
                    float(values.get("limited_ratio", 0)), float(values.get("success_rate", 0)),
                    int(values.get("outbox_pending", 0)), int(values.get("upload_errors", 0)),
                ),
            )

    def record_proxy_usage(self, rows: list[tuple[str, str, int]]) -> None:
        if not rows:
            return
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO crawl_proxy_usage(proxy_key_hash,profile,requests) VALUES(%s,%s,%s) "
                    "ON CONFLICT(proxy_key_hash,profile) DO UPDATE SET "
                    "requests=crawl_proxy_usage.requests+EXCLUDED.requests,last_used_at=now()",
                    rows,
                )

    def get_discovery_cache(self, cache_key: str, source: str = "cache") -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload,etag,last_modified,result_count,novel_count "
                "FROM discovery_query_cache WHERE cache_key=%s AND expires_at>now()",
                (cache_key,),
            ).fetchone()
            connection.execute(
                "INSERT INTO discovery_source_runtime(source,cache_hits,cache_misses) "
                "VALUES(%s,%s,%s) ON CONFLICT(source) DO UPDATE SET "
                "cache_hits=discovery_source_runtime.cache_hits+EXCLUDED.cache_hits,"
                "cache_misses=discovery_source_runtime.cache_misses+EXCLUDED.cache_misses,updated_at=now()",
                (source, int(bool(row)), int(not row)),
            )
        return dict(row) if row else None

    def put_discovery_cache(
        self, cache_key: str, source: str, query_hash: str, locale: str, page: int,
        payload: list[dict[str, Any]], ttl_seconds: int, *, novel_count: int = 0,
        etag: str | None = None, last_modified: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO discovery_query_cache(cache_key,source,query_hash,locale,page,payload,"
                "etag,last_modified,result_count,novel_count,expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,now()+(%s*interval '1 second')) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload=EXCLUDED.payload,etag=EXCLUDED.etag,"
                "last_modified=EXCLUDED.last_modified,result_count=EXCLUDED.result_count,"
                "novel_count=EXCLUDED.novel_count,expires_at=EXCLUDED.expires_at,updated_at=now()",
                (
                    cache_key, source, query_hash, locale, page,
                    json.dumps(payload, ensure_ascii=False), etag, last_modified,
                    len(payload), novel_count, max(1, ttl_seconds),
                ),
            )

    def acquire_discovery_slot(self, source: str, initial_rps: float) -> dict[str, Any]:
        """Reserve one global request slot shared by every crawler process."""
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO discovery_source_runtime(source,current_rps) VALUES(%s,%s) "
                    "ON CONFLICT(source) DO NOTHING", (source, max(initial_rps, 0.01)),
                )
                row = connection.execute(
                    "SELECT * FROM discovery_source_runtime WHERE source=%s FOR UPDATE", (source,)
                ).fetchone()
                circuit_until = row["circuit_until"]
                if circuit_until and circuit_until > now:
                    return {
                        "allowed": False, "wait": (circuit_until - now).total_seconds(),
                        "state": "circuit_open", "current_rps": float(row["current_rps"]),
                        "circuit_until": circuit_until,
                    }
                rps = max(float(row["current_rps"]), 0.01)
                state = str(row["state"])
                if circuit_until and circuit_until <= now:
                    rps, state = min(0.25, rps), "warming_up"
                    connection.execute(
                        "UPDATE discovery_source_runtime SET circuit_until=NULL,current_rps=%s,state=%s,"
                        "window_started_at=now(),requests_window=0,successes_window=0,errors_window=0,"
                        "limited_window=0,captcha_window=0 WHERE source=%s", (rps, state, source),
                    )
                next_at = max(row["next_request_at"], now)
                wait = max(0.0, (next_at - now).total_seconds())
                connection.execute(
                    "UPDATE discovery_source_runtime SET next_request_at=%s,updated_at=now() WHERE source=%s",
                    (next_at + timedelta(seconds=1.0 / rps), source),
                )
        return {"allowed": True, "wait": wait, "state": state, "current_rps": rps}

    def record_discovery_result(
        self, source: str, *, success: bool, limited: bool = False,
        captcha: bool = False, result_count: int = 0, novel_count: int = 0,
        maximum_rps: float = 2.0, captcha_threshold: float = 0.02,
        source_cooldown_seconds: int = 1800,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO discovery_source_runtime(source) VALUES(%s) ON CONFLICT DO NOTHING",
                    (source,),
                )
                row = connection.execute(
                    "SELECT * FROM discovery_source_runtime WHERE source=%s FOR UPDATE", (source,)
                ).fetchone()
                reset = (now - row["window_started_at"]).total_seconds() >= 300
                requests = 1 if reset else int(row["requests_window"]) + 1
                successes = int(success) if reset else int(row["successes_window"]) + int(success)
                errors = int(not success) if reset else int(row["errors_window"]) + int(not success)
                limited_count = int(limited) if reset else int(row["limited_window"]) + int(limited)
                captcha_count = int(captcha) if reset else int(row["captcha_window"]) + int(captcha)
                rps = float(row["current_rps"])
                state = "healthy" if success else "degraded"
                circuit_until = row["circuit_until"]
                if captcha_count / max(requests, 1) > max(captcha_threshold, 0):
                    rps = max(0.25, rps / 2)
                    state = "circuit_open"
                    circuit_until = now + timedelta(seconds=max(1, source_cooldown_seconds))
                evaluated_at = row["evaluated_at"]
                if (now - evaluated_at).total_seconds() >= 1800 and not circuit_until:
                    ratio = captcha_count / max(requests, 1)
                    if ratio < 0.01:
                        rps = min(maximum_rps, rps * 1.25)
                        state = "healthy"
                    elif ratio > captcha_threshold:
                        rps = max(0.25, rps / 2)
                    evaluated_at = now
                connection.execute(
                    "UPDATE discovery_source_runtime SET state=%s,current_rps=%s,circuit_until=%s,"
                    "window_started_at=%s,evaluated_at=%s,requests_window=%s,successes_window=%s,"
                    "errors_window=%s,limited_window=%s,captcha_window=%s,"
                    "requests_total=requests_total+1,successes_total=successes_total+%s,"
                    "errors_total=errors_total+%s,limited_total=limited_total+%s,"
                    "captcha_total=captcha_total+%s,result_count=result_count+%s,"
                    "novel_count=novel_count+%s,updated_at=now() WHERE source=%s",
                    (
                        state, rps, circuit_until, now if reset else row["window_started_at"],
                        evaluated_at, requests, successes, errors, limited_count, captcha_count,
                        int(success), int(not success), int(limited), int(captcha),
                        max(0, result_count), max(0, novel_count), source,
                    ),
                )

    def reserve_google_proxy(
        self, proxy_hash: str, locale: str, minimum_interval_seconds: int,
    ) -> tuple[bool, float]:
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO google_proxy_sessions(proxy_key_hash,locale) VALUES(%s,%s) "
                    "ON CONFLICT(proxy_key_hash) DO NOTHING", (proxy_hash, locale),
                )
                row = connection.execute(
                    "SELECT * FROM google_proxy_sessions WHERE proxy_key_hash=%s FOR UPDATE",
                    (proxy_hash,),
                ).fetchone()
                available_at = row["cooldown_until"] or now
                if row["last_used_at"]:
                    available_at = max(
                        available_at,
                        row["last_used_at"] + timedelta(seconds=max(0, minimum_interval_seconds)),
                    )
                if available_at > now:
                    return False, (available_at - now).total_seconds()
                connection.execute(
                    "UPDATE google_proxy_sessions SET locale=%s,last_used_at=now(),updated_at=now() "
                    "WHERE proxy_key_hash=%s", (locale, proxy_hash),
                )
        return True, 0.0

    def record_google_proxy_result(
        self, proxy_hash: str, *, success: bool, cooldown_seconds: int = 0,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO google_proxy_sessions(proxy_key_hash,successes,failures,cooldown_until) "
                "VALUES(%s,%s,%s,CASE WHEN %s>0 THEN now()+(%s*interval '1 second') END) "
                "ON CONFLICT(proxy_key_hash) DO UPDATE SET successes=google_proxy_sessions.successes+%s,"
                "failures=google_proxy_sessions.failures+%s,cooldown_until=CASE WHEN %s>0 "
                "THEN now()+(%s*interval '1 second') ELSE google_proxy_sessions.cooldown_until END,"
                "updated_at=now()",
                (
                    proxy_hash, int(success), int(not success), cooldown_seconds, cooldown_seconds,
                    int(success), int(not success), cooldown_seconds, cooldown_seconds,
                ),
            )

    def acquire_google_page_batch(
        self, campaign_id: str, query: str, locale: str, max_page: int,
        pages_per_batch: int, repeat_seconds: int, lease_seconds: int = 900,
    ) -> dict[str, Any] | None:
        """Atomically lease the next Google result-page batch for one query."""
        query_hash = hashlib.sha256(query.encode()).hexdigest()
        maximum = max(1, max_page)
        batch_size = max(1, pages_per_batch)
        with self.connect() as connection:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO google_page_frontier(campaign_id,query_hash,query,locale,max_page) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(campaign_id,query_hash,locale) DO NOTHING",
                    (campaign_id, query_hash, query, locale, maximum),
                )
                row = connection.execute(
                    "SELECT * FROM google_page_frontier WHERE campaign_id=%s AND query_hash=%s "
                    "AND locale=%s FOR UPDATE SKIP LOCKED", (campaign_id, query_hash, locale),
                ).fetchone()
                if not row:
                    return None
                now = datetime.now(timezone.utc)
                state = str(row["state"])
                connection.execute(
                    "UPDATE google_page_frontier SET query=%s,max_page=%s,updated_at=now() "
                    "WHERE campaign_id=%s AND query_hash=%s AND locale=%s",
                    (query, maximum, campaign_id, query_hash, locale),
                )
                if state == "running" and row["lease_expires_at"] and row["lease_expires_at"] <= now:
                    state = "pending"
                if state in {"cooling", "failed"} and row["next_run_at"] <= now:
                    state = "pending"
                if state == "completed" and row["next_run_at"] <= now:
                    state = "pending"
                    connection.execute(
                        "UPDATE google_page_frontier SET next_page=1,page_stats='{}'::jsonb,"
                        "attempts=0,last_error=NULL WHERE campaign_id=%s AND query_hash=%s AND locale=%s",
                        (campaign_id, query_hash, locale),
                    )
                    row = dict(row)
                    row["next_page"] = 1
                if state != "pending" or row["next_run_at"] > now:
                    if state != str(row["state"]):
                        connection.execute(
                            "UPDATE google_page_frontier SET state=%s,lease_token=NULL,"
                            "lease_expires_at=NULL,updated_at=now() WHERE campaign_id=%s "
                            "AND query_hash=%s AND locale=%s",
                            (state, campaign_id, query_hash, locale),
                        )
                    return None
                start_page = min(max(int(row["next_page"]), 1), maximum)
                end_page = min(start_page + batch_size - 1, maximum)
                lease_token = uuid4()
                connection.execute(
                    "UPDATE google_page_frontier SET state='running',batch_start=%s,batch_end=%s,"
                    "lease_token=%s,lease_expires_at=now()+(%s*interval '1 second'),updated_at=now() "
                    "WHERE campaign_id=%s AND query_hash=%s AND locale=%s",
                    (
                        start_page, end_page, lease_token, max(30, lease_seconds), campaign_id,
                        query_hash, locale,
                    ),
                )
        return {
            "campaign_id": campaign_id, "query_hash": query_hash, "locale": locale,
            "lease_token": str(lease_token), "start_page": start_page, "end_page": end_page,
            "max_page": maximum, "repeat_seconds": max(1, repeat_seconds),
        }

    def record_google_page_result(
        self, batch: dict[str, Any], page: int, *, success: bool,
        result_count: int = 0, unique_count: int = 0, novel_count: int = 0,
        error: str | None = None, captcha: bool = False,
    ) -> bool:
        """Advance a leased frontier only after a page was fetched or read from cache."""
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT * FROM google_page_frontier WHERE campaign_id=%s AND query_hash=%s "
                    "AND locale=%s AND lease_token=%s AND state='running' FOR UPDATE",
                    (
                        batch["campaign_id"], batch["query_hash"], batch["locale"],
                        batch["lease_token"],
                    ),
                ).fetchone()
                if not row:
                    return False
                stats = dict(row["page_stats"] or {})
                stats[str(page)] = {
                    "status": "success" if success else "failed",
                    "candidates": max(0, result_count),
                    "unique_urls": max(0, unique_count),
                    "novel_urls": max(0, novel_count),
                    "captcha": bool(captcha),
                    "updated_at": now.isoformat(),
                }
                if success:
                    if page != int(row["next_page"]):
                        return False
                    next_page = page + 1
                    completed = next_page > int(row["max_page"])
                    batch_done = page >= int(row["batch_end"])
                    state = "completed" if completed else ("pending" if batch_done else "running")
                    next_run_at = (
                        now + timedelta(seconds=max(1, int(batch["repeat_seconds"])))
                        if completed else now
                    )
                    clear_lease = completed or batch_done
                    connection.execute(
                        "UPDATE google_page_frontier SET next_page=%s,state=%s,next_run_at=%s,"
                        "attempts=0,last_error=NULL,page_stats=%s::jsonb,lease_token=CASE WHEN %s THEN NULL "
                        "ELSE lease_token END,lease_expires_at=CASE WHEN %s THEN NULL ELSE "
                        "lease_expires_at END,updated_at=now() WHERE campaign_id=%s AND query_hash=%s "
                        "AND locale=%s AND lease_token=%s",
                        (
                            next_page, state, next_run_at, json.dumps(stats), clear_lease,
                            clear_lease, batch["campaign_id"], batch["query_hash"],
                            batch["locale"], batch["lease_token"],
                        ),
                    )
                    return True
                attempts = int(row["attempts"]) + 1
                limited = captcha or str(error or "") in {
                    "google_captcha", "google_http_403", "google_http_429",
                    "google_web_circuit_open",
                }
                delay = 1800 if limited else min(60 * (2 ** min(attempts - 1, 6)), 3600)
                state = "cooling" if limited else ("failed" if attempts >= 8 else "pending")
                if state == "failed":
                    delay = max(delay, 21600)
                connection.execute(
                    "UPDATE google_page_frontier SET state=%s,attempts=%s,next_run_at=%s,"
                    "last_error=%s,page_stats=%s::jsonb,lease_token=NULL,lease_expires_at=NULL,"
                    "updated_at=now() WHERE campaign_id=%s AND query_hash=%s AND locale=%s "
                    "AND lease_token=%s",
                    (
                        state, attempts, now + timedelta(seconds=delay), str(error or "unknown")[:200],
                        json.dumps(stats), batch["campaign_id"], batch["query_hash"],
                        batch["locale"], batch["lease_token"],
                    ),
                )
                return True

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(SCHEMA)
            # Delivered Whale payloads must not retain a second body copy.
            # Locally-created campaigns keep their extracted body in pages.
            connection.execute(
                "UPDATE whale_ingest_outbox SET payload='{}'::jsonb "
                "WHERE status IN ('delivered','rejected') AND payload<>'{}'::jsonb"
            )

    def create_campaign(
        self, query: str, aliases: list[str], daily_target: int, proxy_profile: str
    ) -> str:
        campaign_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,status) "
                "VALUES(%s,%s,%s::jsonb,%s,%s,'active')",
                (campaign_id, query, json.dumps(aliases, ensure_ascii=False), daily_target, proxy_profile),
            )
        return campaign_id

    def create_whale_campaign(
        self, *, task_id: str, dataset_id: str, source_platform: str, task_type: str,
        query: str, aliases: list[str], daily_target: int, proxy_profile: str, task_payload: dict[str, Any],
        reactivate_existing: bool = False,
    ) -> str:
        """Create one local campaign for a claimed Whale task, exactly once."""
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT campaign_id FROM whale_task_runs WHERE task_id=%s", (task_id,)
            ).fetchone()
            if existing:
                campaign_id = str(existing["campaign_id"])
                if reactivate_existing:
                    with connection.transaction():
                        connection.execute(
                            "UPDATE campaigns SET daily_target=%s,proxy_profile=%s,status='active',"
                            "last_error=NULL,updated_at=now() WHERE id=%s",
                            (daily_target, proxy_profile, campaign_id),
                        )
                        connection.execute(
                            "UPDATE whale_task_runs SET dataset_id=%s,source_platform=%s,task_type=%s,"
                            "payload=%s::jsonb,status='running',updated_at=now() WHERE task_id=%s",
                            (
                                dataset_id, source_platform, task_type,
                                json.dumps(task_payload, ensure_ascii=False), task_id,
                            ),
                        )
                return campaign_id
            campaign_id = str(uuid4())
            with connection.transaction():
                connection.execute(
                    "INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,status) "
                    "VALUES(%s,%s,%s::jsonb,%s,%s,'active')",
                    (campaign_id, query, json.dumps(aliases, ensure_ascii=False), daily_target, proxy_profile),
                )
                connection.execute(
                    "INSERT INTO whale_task_runs(task_id,campaign_id,dataset_id,source_platform,task_type,payload,status) "
                    "VALUES(%s,%s,%s,%s,%s,%s::jsonb,'running')",
                    (task_id, campaign_id, dataset_id, source_platform, task_type,
                     json.dumps(task_payload, ensure_ascii=False)),
                )
        return campaign_id

    def whale_task_for_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM whale_task_runs WHERE campaign_id=%s", (campaign_id,)
            ).fetchone()

    def update_whale_task(self, task_id: str, *, status: str | None = None, cursor: str | None = None) -> None:
        assignments = ["updated_at=now()"]
        values: list[Any] = []
        if status is not None:
            assignments.append("status=%s")
            values.append(status)
        if cursor is not None:
            assignments.append("cursor=%s")
            values.append(cursor)
        values.append(task_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE whale_task_runs SET {','.join(assignments)} WHERE task_id=%s", values
            )

    def queue_whale_message(self, task_id: str, source_record_key: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO whale_ingest_outbox(task_id,source_record_key,payload) VALUES(%s,%s,%s::jsonb) "
                "ON CONFLICT (source_record_key) DO NOTHING",
                (task_id, source_record_key, json.dumps(payload, ensure_ascii=False)),
            )

    def whale_outbox(self, task_id: str, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,source_record_key,payload,attempts FROM whale_ingest_outbox "
                "WHERE task_id=%s AND status='pending' ORDER BY id LIMIT %s",
                (task_id, limit),
            ).fetchall()

    def mark_whale_outbox(self, ids: list[int], *, status: str, error: str | None = None) -> None:
        if not ids:
            return
        delivered = "now()" if status == "delivered" else "NULL"
        with self.connect() as connection:
            scrub = ",payload='{}'::jsonb" if status in {"delivered", "rejected"} else ""
            connection.execute(
                f"UPDATE whale_ingest_outbox SET status=%s,attempts=attempts+1,last_error=%s,"
                f"delivered_at={delivered},updated_at=now(){scrub} WHERE id=ANY(%s)",
                (status, (error or "")[:500] or None, ids),
            )

    def retry_whale_outbox(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        with self.connect() as connection:
            connection.execute(
                "UPDATE whale_ingest_outbox SET attempts=attempts+1,last_error=%s,updated_at=now() "
                "WHERE id=ANY(%s)", (error[:500], ids),
            )

    def whale_outbox_counts(self, task_id: str) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status,count(*) AS count FROM whale_ingest_outbox WHERE task_id=%s GROUP BY status",
                (task_id,),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def pending_whale_task_ids(self, prefix: str = "") -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT task_id FROM whale_ingest_outbox "
                "WHERE status='pending' AND task_id LIKE %s ORDER BY task_id",
                (f"{prefix}%",),
            ).fetchall()
        return [str(row["task_id"]) for row in rows]

    def campaign(self, campaign_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM campaigns WHERE id=%s", (campaign_id,)
            ).fetchone()

    def active_proxy_profiles(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT proxy_profile FROM campaigns WHERE status='active'"
            ).fetchall()
        return {str(row["proxy_profile"]) for row in rows}

    def active_campaign_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM campaigns WHERE status='active' ORDER BY created_at"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def active_local_campaign_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT c.id FROM campaigns c WHERE c.status='active' AND NOT EXISTS ("
                "SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id) ORDER BY c.created_at"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def is_local_campaign(self, campaign_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM campaigns c WHERE c.id=%s AND NOT EXISTS ("
                "SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id)) AS value",
                (campaign_id,),
            ).fetchone()
        return bool(row and row["value"])

    def local_campaigns(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT c.id,c.query,c.status,c.discovered,c.fetched,c.failed,c.duplicates,"
                "c.irrelevant,c.last_error,c.created_at,c.updated_at,"
                "count(cp.page_id) AS saved_count FROM campaigns c "
                "LEFT JOIN campaign_pages cp ON cp.campaign_id=c.id "
                "WHERE NOT EXISTS (SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id) "
                "GROUP BY c.id ORDER BY c.created_at DESC LIMIT %s",
                (min(max(limit, 1), 100),),
            ).fetchall()

    def local_campaign_detail(self, campaign_id: str, limit: int = 100) -> dict[str, Any] | None:
        with self.connect() as connection:
            campaign = connection.execute(
                "SELECT c.*,count(cp.page_id) AS saved_count FROM campaigns c "
                "LEFT JOIN campaign_pages cp ON cp.campaign_id=c.id WHERE c.id=%s "
                "AND NOT EXISTS (SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id) "
                "GROUP BY c.id",
                (campaign_id,),
            ).fetchone()
            if not campaign:
                return None
            pages = connection.execute(
                "SELECT p.id,p.url,p.title,left(CASE WHEN p.content<>'' THEN p.content "
                "ELSE p.summary END,600) AS preview,p.language,p.http_status,p.fetched_at,"
                "p.source_engines,cp.first_seen FROM campaign_pages cp JOIN pages p ON p.id=cp.page_id "
                "WHERE cp.campaign_id=%s ORDER BY cp.first_seen DESC,p.id DESC LIMIT %s",
                (campaign_id, min(max(limit, 1), 500)),
            ).fetchall()
            frontier = connection.execute(
                "SELECT count(*) AS queries,COALESCE(sum(LEAST(GREATEST(next_page-1,0),max_page)),0) "
                "AS covered_pages,COALESCE(sum(max_page),0) AS total_pages,"
                "count(*) FILTER (WHERE state='pending') AS pending,"
                "count(*) FILTER (WHERE state='running') AS running,"
                "count(*) FILTER (WHERE state IN ('cooling','failed')) AS waiting,"
                "count(*) FILTER (WHERE state='completed') AS completed "
                "FROM google_page_frontier WHERE campaign_id=%s",
                (campaign_id,),
            ).fetchone()
        result = dict(campaign)
        # Local campaigns are intentionally unlimited. The persisted value is
        # only a legacy schema-compatible placeholder.
        result["daily_target"] = 0
        result["pages"] = pages
        result["frontier"] = dict(frontier)
        result["upload_to_whale"] = False
        return result

    def local_proxy_profiles(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT c.proxy_profile FROM campaigns c WHERE c.status='active' "
                "AND NOT EXISTS (SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id)"
            ).fetchall()
        return {str(row["proxy_profile"]) for row in rows}

    def next_google_frontier_delay(self, campaign_id: str) -> int | None:
        """Return the delay until the next unfinished page batch is ready."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT state,next_run_at,lease_expires_at FROM google_page_frontier "
                "WHERE campaign_id=%s",
                (campaign_id,),
            ).fetchall()
        if not rows:
            return None
        now = datetime.now(timezone.utc)
        delays: list[int] = []
        for row in rows:
            due_at = row["lease_expires_at"] if row["state"] == "running" else row["next_run_at"]
            delays.append(max(1, int((due_at - now).total_seconds()) + 1) if due_at else 1)
        return min(delays)

    def set_status(self, campaign_id: str, status: str, error: str | None = None) -> bool:
        if status not in {"active", "paused", "stopped", "failed"}:
            raise ValueError("invalid campaign status")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE campaigns SET status=%s,last_error=%s,updated_at=now() WHERE id=%s",
                (status, error, campaign_id),
            )
            return cursor.rowcount == 1

    def increment(self, campaign_id: str, **values: int) -> None:
        if not values or set(values) - self.COUNTERS:
            raise ValueError("invalid campaign counter")
        assignments = ",".join(f"{field}={field}+%s" for field in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE campaigns SET {assignments},updated_at=now() WHERE id=%s",
                (*values.values(), campaign_id),
            )

    def record_event(
        self, campaign_id: str, url: str, status: str,
        http_status: int | None = None, error_code: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO crawl_events(campaign_id,url,status,http_status,error_code) VALUES(%s,%s,%s,%s,%s)",
                (campaign_id, url[:4096], status, http_status, (error_code or "")[:120]),
            )

    def processed_urls(self, campaign_id: str, urls: list[str]) -> set[str]:
        if not urls:
            return set()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT url FROM pages WHERE url=ANY(%s) "
                "UNION SELECT url FROM crawl_events WHERE campaign_id=%s AND url=ANY(%s) "
                "AND status='permanent_failed'",
                (urls, campaign_id, urls),
            ).fetchall()
        return {str(row["url"]) for row in rows}

    def discovery_window(
        self, campaign_id: str, history_start: date, window_days: int
    ) -> tuple[datetime, datetime]:
        """Atomically reserve the next historical time slice for a campaign."""
        days = max(1, window_days)
        with self.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    "INSERT INTO discovery_cursors(campaign_id) VALUES(%s) "
                    "ON CONFLICT (campaign_id) DO UPDATE SET updated_at=now() "
                    "RETURNING history_before",
                    (campaign_id,),
                ).fetchone()
                before = row["history_before"]
                start_date = history_start
                after = max(start_date, before - timedelta(days=days))
                next_before = after if after > start_date else datetime.now(timezone.utc).date()
                connection.execute(
                    "UPDATE discovery_cursors SET history_before=%s,updated_at=now() WHERE campaign_id=%s",
                    (next_before, campaign_id),
                )
        return (
            datetime.combine(after, datetime.min.time(), tzinfo=timezone.utc),
            datetime.combine(before, datetime.min.time(), tzinfo=timezone.utc),
        )

    def record_discovery_novelty(
        self, campaign_id: str, candidates: int, novel: int,
        minimum_ratio: float, cooldown_seconds: int,
    ) -> None:
        ratio = novel / max(candidates, 1)
        empty = novel == 0 or ratio < minimum_ratio
        with self.connect() as connection:
            connection.execute(
                "UPDATE discovery_cursors SET last_candidates=%s,last_novel=%s,"
                "consecutive_empty=CASE WHEN %s THEN consecutive_empty+1 ELSE 0 END,"
                "next_run_at=CASE WHEN %s AND consecutive_empty>=2 "
                "THEN now()+(%s * interval '1 second') ELSE now() END,updated_at=now() "
                "WHERE campaign_id=%s",
                (candidates, novel, empty, empty, max(0, cooldown_seconds), campaign_id),
            )

    def continuous_delivered_today(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT count(*) AS value FROM whale_ingest_outbox o "
                "WHERE o.task_id LIKE 'continuous:%' AND o.status='delivered' "
                "AND o.delivered_at >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            ).fetchone()
        return int(row["value"])

    def sync_continuous_keywords(self, specs: tuple[Any, ...]) -> None:
        with self.connect() as connection:
            with connection.transaction():
                for spec in specs:
                    connection.execute(
                        "INSERT INTO continuous_keywords(keyword_key,concept_id,query,aliases,language,"
                        "category,kind,state,priority,expires_at) "
                        "VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT(keyword_key) DO UPDATE SET query=excluded.query,aliases=excluded.aliases,"
                        "category=excluded.category,priority=excluded.priority,expires_at=excluded.expires_at,"
                        "state=CASE WHEN continuous_keywords.state='retired' THEN excluded.state "
                        "ELSE continuous_keywords.state END,updated_at=now()",
                        (
                            spec.key, spec.concept_id, spec.query,
                            json.dumps(spec.aliases, ensure_ascii=False), spec.language,
                            spec.category, "base", "active", spec.priority, None,
                        ),
                    )

    def due_continuous_keywords(self, limit: int) -> list[dict[str, Any]]:
        bounded_limit = max(1, limit)
        with self.connect() as connection:
            with connection.transaction():
                return connection.execute(
                    "SELECT * FROM continuous_keywords WHERE kind='base' "
                    "AND state IN ('active','cooldown') AND next_run_at<=now() "
                    "ORDER BY (last_run_at IS NULL) DESC,"
                    "CASE state WHEN 'active' THEN 0 ELSE 1 END,"
                    "priority DESC,score DESC,last_run_at NULLS FIRST LIMIT %s",
                    (bounded_limit,),
                ).fetchall()

    def continuous_keyword_snapshot(self, campaign_id: str, task_id: str) -> dict[str, int]:
        with self.connect() as connection:
            campaign = connection.execute(
                "SELECT discovered,fetched,failed,duplicates FROM campaigns WHERE id=%s",
                (campaign_id,),
            ).fetchone() or {}
            delivered = connection.execute(
                "SELECT count(*) AS value FROM whale_ingest_outbox "
                "WHERE task_id=%s AND status='delivered'", (task_id,),
            ).fetchone()
        return {
            "candidates": int(campaign.get("discovered") or 0),
            "fetched": int(campaign.get("fetched") or 0),
            "failed": int(campaign.get("failed") or 0),
            "duplicates": int(campaign.get("duplicates") or 0),
            "delivered": int((delivered or {}).get("value") or 0),
        }

    def record_continuous_keyword_run(
        self, keyword_key: str, before: dict[str, int], after: dict[str, int],
        duration_seconds: float,
    ) -> None:
        delta = {name: max(0, after.get(name, 0) - before.get(name, 0)) for name in before}
        candidates = delta["candidates"]
        fetched = delta["fetched"]
        delivered = delta["delivered"]
        failed = delta["failed"]
        duplicates = delta["duplicates"]
        unique_yield = min(delivered / max(fetched, 1), 1.0)
        novelty = min(max((candidates - duplicates) / max(candidates, 1), 0.0), 1.0)
        freshness = min(delivered / 20.0, 1.0)
        reliability = 1.0 - min(failed / max(fetched + failed, 1), 1.0)
        raw_score = 100 * (
            0.40 * unique_yield + 0.25 * novelty + 0.20 * freshness + 0.15 * reliability
        )
        bad = unique_yield < 0.02 or novelty < 0.05
        with self.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT state,runs,low_yield_runs,score FROM continuous_keywords "
                    "WHERE keyword_key=%s FOR UPDATE", (keyword_key,),
                ).fetchone()
                if not row:
                    return
                runs = int(row["runs"]) + 1
                score = round(0.65 * raw_score + 0.35 * float(row["score"]), 2)
                low_runs = int(row["low_yield_runs"]) + 1 if bad else 0
                state = str(row["state"])
                interval_seconds = 1800 if score >= 60 else 10800 if score >= 30 else 43200
                if state == "retired":
                    interval_seconds = 86400
                elif low_runs >= 3:
                    state = "cooldown"
                    interval_seconds = 86400
                else:
                    state = "active"
                connection.execute(
                    "UPDATE continuous_keywords SET state=%s,score=%s,runs=%s,low_yield_runs=%s,"
                    "last_candidates=%s,last_fetched=%s,last_delivered=%s,last_failed=%s,"
                    "last_duplicates=%s,last_run_at=now(),next_run_at=now()+(%s * interval '1 second'),"
                    "updated_at=now() WHERE keyword_key=%s",
                    (state, score, runs, low_runs, candidates, fetched, delivered, failed,
                     duplicates, interval_seconds, keyword_key),
                )
                connection.execute(
                    "INSERT INTO continuous_keyword_runs(keyword_key,candidates,fetched,delivered,"
                    "failed,duplicates,duration_seconds,score) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (keyword_key, candidates, fetched, delivered, failed, duplicates,
                     max(0.0, duration_seconds), score),
                )

    def continuous_keyword_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = connection.execute(
                "SELECT kind,state,language,count(*) AS count FROM continuous_keywords "
                "GROUP BY kind,state,language"
            ).fetchall()
            categories = connection.execute(
                "SELECT category,sum(last_delivered)::bigint AS delivered,count(*) AS keywords "
                "FROM continuous_keywords WHERE state<>'retired' GROUP BY category "
                "ORDER BY delivered DESC,category LIMIT 12"
            ).fetchall()
            top = connection.execute(
                "SELECT keyword_key,query,language,category,kind,state,score,last_candidates,"
                "last_fetched,last_delivered,last_failed,last_duplicates,last_run_at,next_run_at "
                "FROM continuous_keywords WHERE state<>'retired' "
                "ORDER BY score DESC,last_delivered DESC LIMIT 20"
            ).fetchall()
            running = connection.execute(
                "SELECT query,language,category,last_run_at,next_run_at FROM continuous_keywords "
                "WHERE state<>'retired' ORDER BY next_run_at,last_run_at NULLS FIRST LIMIT 12"
            ).fetchall()
        summary = {"base": 0, "cooldown": 0, "en": 0, "zh": 0}
        for row in counts:
            count = int(row["count"])
            if row["state"] != "retired":
                summary[str(row["kind"])] += count
                summary[str(row["language"])] += count
            if row["state"] == "cooldown":
                summary[str(row["state"])] += count
        return {"summary": summary, "categories": categories, "top": top, "next": running}

    def blocked_domains(self, domains: list[str]) -> set[str]:
        domains = list(dict.fromkeys(value.lower() for value in domains if value))
        if not domains:
            return set()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT domain FROM crawl_domain_health WHERE domain=ANY(%s) "
                "AND cooldown_until>now()", (domains,),
            ).fetchall()
        return {str(row["domain"]) for row in rows}

    def record_domain_result(self, url: str, success: bool) -> None:
        from urllib.parse import urlsplit

        domain = (urlsplit(url).hostname or "").lower()
        if not domain:
            return
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO crawl_domain_health(domain,attempts,failures,consecutive_failures,"
                "cooldown_until) VALUES(%s,1,%s,%s,NULL) ON CONFLICT(domain) DO UPDATE SET "
                "attempts=crawl_domain_health.attempts+1,"
                "failures=crawl_domain_health.failures+excluded.failures,"
                "consecutive_failures=CASE WHEN %s THEN 0 "
                "ELSE crawl_domain_health.consecutive_failures+1 END,"
                "cooldown_until=CASE WHEN NOT %s AND "
                "crawl_domain_health.consecutive_failures+1>=5 THEN now()+interval '6 hours' "
                "WHEN %s THEN NULL ELSE crawl_domain_health.cooldown_until END,updated_at=now()",
                (domain, int(not success), int(not success), success, success, success),
            )

    def reusable_pages(self, campaign_id: str, urls: list[str]) -> list[dict[str, Any]]:
        if not urls:
            return []
        with self.connect() as connection:
            return connection.execute(
                "SELECT p.* FROM pages p WHERE p.url=ANY(%s) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM campaign_pages cp WHERE cp.campaign_id=%s AND cp.page_id=p.id"
                ")",
                (urls, campaign_id),
            ).fetchall()

    def attach_page(self, campaign_id: str, page_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "INSERT INTO campaign_pages(campaign_id,page_id) VALUES(%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING page_id",
                (campaign_id, page_id),
            ).fetchone()
        return row is not None

    def record_page(
        self,
        campaign_id: str,
        page: PageRecord,
        *,
        whale_task_id: str | None = None,
        source_record_key: str | None = None,
        whale_payload: dict[str, Any] | None = None,
    ) -> tuple[int, bool, bool, bool]:
        """Returns (page_id, new association, duplicate content, needs indexing)."""
        with self.connect() as connection:
            with connection.transaction():
                existing = connection.execute(
                    "SELECT id,url,content_hash,content,indexed_at FROM pages WHERE url=%s OR content_hash=%s "
                    "ORDER BY (content_hash=%s) DESC LIMIT 1 FOR UPDATE",
                    (page.url, page.content_hash, page.content_hash),
                ).fetchone()
                duplicate_content = bool(existing and existing["content_hash"] == page.content_hash)
                needs_indexing = not existing or existing["indexed_at"] is None
                if existing:
                    page_id = int(existing["id"])
                    stored_content = str(existing["content"] or "") if whale_task_id else page.content
                    connection.execute(
                        "UPDATE pages SET title=%s,summary=%s,content=%s,language=%s,http_status=%s,"
                        "fetched_at=%s,source_engines=%s::jsonb WHERE id=%s",
                        (
                            page.title, page.summary, stored_content, page.language, page.http_status,
                            page.fetched_at, json.dumps(page.source_engines), page_id,
                        ),
                    )
                else:
                    stored_content = "" if whale_task_id else page.content
                    row = connection.execute(
                        "INSERT INTO pages(url,content_hash,title,summary,content,language,http_status,fetched_at,source_engines) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING id",
                        (
                            page.url, page.content_hash, page.title, page.summary, stored_content, page.language,
                            page.http_status, page.fetched_at, json.dumps(page.source_engines),
                        ),
                    ).fetchone()
                    if row:
                        page_id = int(row["id"])
                    else:
                        # Another worker inserted the same URL or content hash concurrently.
                        concurrent = connection.execute(
                            "SELECT id,content_hash,indexed_at FROM pages WHERE url=%s OR content_hash=%s "
                            "ORDER BY (content_hash=%s) DESC LIMIT 1 FOR UPDATE",
                            (page.url, page.content_hash, page.content_hash),
                        ).fetchone()
                        if not concurrent:
                            raise RuntimeError("page deduplication race could not be resolved")
                        page_id = int(concurrent["id"])
                        duplicate_content = concurrent["content_hash"] == page.content_hash
                        needs_indexing = concurrent["indexed_at"] is None
                inserted = connection.execute(
                    "INSERT INTO campaign_pages(campaign_id,page_id) VALUES(%s,%s) "
                    "ON CONFLICT DO NOTHING RETURNING page_id",
                    (campaign_id, page_id),
                ).fetchone()
                if whale_task_id and source_record_key and whale_payload is not None:
                    connection.execute(
                        "INSERT INTO whale_ingest_outbox(task_id,source_record_key,payload) "
                        "VALUES(%s,%s,%s::jsonb) ON CONFLICT (source_record_key) DO NOTHING",
                        (
                            whale_task_id,
                            source_record_key,
                            json.dumps(whale_payload, ensure_ascii=False),
                        ),
                    )
        return page_id, inserted is not None, duplicate_content, needs_indexing

    def mark_indexed(self, content_hashes: list[str]) -> None:
        if not content_hashes:
            return
        with self.connect() as connection:
            connection.execute(
                "UPDATE pages SET indexed_at=now() WHERE content_hash=ANY(%s)",
                (content_hashes,),
            )

    def daily_count(self, campaign_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT count(*) AS value FROM campaign_pages WHERE campaign_id=%s "
                "AND first_seen >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
                (campaign_id,),
            ).fetchone()
            return int(row["value"])

    def stats(
        self,
        continuous_daily_target: int | None = None,
        continuous_proxy_profile: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            campaigns = connection.execute(
                "SELECT c.*, (SELECT count(*) FROM campaign_pages cp WHERE cp.campaign_id=c.id "
                "AND cp.first_seen >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS today, "
                "(SELECT count(*) FROM campaign_pages cp WHERE cp.campaign_id=c.id "
                "AND cp.first_seen >= now()-interval '60 seconds') AS recent_count, "
                "EXTRACT(EPOCH FROM (now()-GREATEST(c.created_at, "
                "date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))) AS elapsed_seconds "
                "FROM campaigns c ORDER BY c.created_at DESC"
            ).fetchall()
            totals = connection.execute(
                "SELECT count(*) AS campaigns, COALESCE(sum(discovered),0) AS discovered,"
                "COALESCE(sum(fetched),0) AS fetched,COALESCE(sum(failed),0) AS failed,"
                "COALESCE(sum(duplicates),0) AS duplicates,COALESCE(sum(irrelevant),0) AS irrelevant "
                "FROM campaigns"
            ).fetchone()
            events = connection.execute(
                "SELECT campaign_id,url,status,http_status,error_code,created_at "
                "FROM crawl_events ORDER BY id DESC LIMIT 20"
            ).fetchall()
            source_stats = connection.execute(
                "SELECT cp.campaign_id, source.value AS source, count(DISTINCT cp.page_id) AS today "
                "FROM campaign_pages cp JOIN pages p ON p.id=cp.page_id "
                "CROSS JOIN LATERAL jsonb_array_elements_text(p.source_engines) AS source(value) "
                "WHERE cp.first_seen >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "GROUP BY cp.campaign_id,source.value ORDER BY today DESC,source.value"
            ).fetchall()
            continuous = connection.execute(
                "WITH continuous_campaigns AS ("
                "SELECT c.* FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id ~ '^continuous:[0-9a-f]{12}$'"
                ") SELECT 'continuous' AS id,'AI 持续采集总览' AS query,"
                "COALESCE(max(daily_target),0) AS daily_target,"
                "COALESCE(min(proxy_profile),'private') AS proxy_profile,"
                "CASE WHEN COALESCE(bool_or(status='active'),false) THEN 'active' "
                "WHEN COALESCE(bool_or(status='paused'),false) THEN 'paused' "
                "WHEN COALESCE(bool_or(status='failed'),false) THEN 'failed' ELSE 'stopped' END AS status,"
                "COALESCE(sum(discovered),0) AS discovered,COALESCE(sum(fetched),0) AS fetched,"
                "COALESCE(sum(failed),0) AS failed,COALESCE(sum(duplicates),0) AS duplicates,"
                "COALESCE(sum(irrelevant),0) AS irrelevant,NULL AS last_error,"
                "min(created_at) AS created_at,max(updated_at) AS updated_at,"
                "(SELECT count(DISTINCT cp.page_id) FROM campaign_pages cp JOIN continuous_campaigns cc "
                "ON cc.id=cp.campaign_id JOIN pages p ON p.id=cp.page_id "
                "WHERE cp.first_seen >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(p.source_engines) AS src(value) "
                "WHERE src.value LIKE 'google%')) AS today,"
                "(SELECT count(*) FROM whale_ingest_outbox o WHERE o.task_id LIKE 'continuous:%' "
                "AND o.status='delivered' AND o.delivered_at>=now()-interval '60 seconds') AS recent_count,"
                "EXTRACT(EPOCH FROM (now()-GREATEST(COALESCE(min(created_at),now()), "
                "date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))) AS elapsed_seconds,"
                "(SELECT COALESCE(sum(dc.last_candidates),0) FROM discovery_cursors dc "
                "JOIN continuous_campaigns dcc ON dcc.id=dc.campaign_id) AS last_candidates,"
                "(SELECT COALESCE(sum(dc.last_novel),0) FROM discovery_cursors dc "
                "JOIN continuous_campaigns dcc ON dcc.id=dc.campaign_id) AS last_novel,"
                "count(*) AS keyword_count FROM continuous_campaigns"
            ).fetchone()
            continuous_source_stats = connection.execute(
                "WITH continuous_campaigns AS ("
                "SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id ~ '^continuous:[0-9a-f]{12}$'"
                ") SELECT 'continuous' AS campaign_id, 'google_web' AS source, "
                "count(DISTINCT cp.page_id) AS today "
                "FROM campaign_pages cp JOIN continuous_campaigns cc ON cc.id=cp.campaign_id "
                "JOIN pages p ON p.id=cp.page_id "
                "CROSS JOIN LATERAL jsonb_array_elements_text(p.source_engines) AS source(value) "
                "WHERE cp.first_seen >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "AND source.value LIKE 'google%'"
            ).fetchall()
            continuous_whale_counts = connection.execute(
                "SELECT o.status,count(*) AS count FROM whale_ingest_outbox o "
                "JOIN whale_task_runs w ON w.task_id=o.task_id "
                "WHERE w.task_id LIKE 'continuous:%' "
                "GROUP BY o.status"
            ).fetchall()
            continuous_bottlenecks = connection.execute(
                "WITH continuous_campaigns AS ("
                "SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id ~ '^continuous:[0-9a-f]{12}$'"
                ") SELECT CASE "
                "WHEN ce.status='skipped' THEN 'already_processed' "
                "WHEN ce.error_code='IgnoreRequest' THEN 'blocked_by_site_rules' "
                "WHEN ce.error_code='short_content' THEN 'short_content' "
                "WHEN ce.status='discovery_failed' THEN 'google_discovery_error' "
                "ELSE 'fetch_failed' END AS reason,count(*) AS count "
                "FROM crawl_events ce JOIN continuous_campaigns cc ON cc.id=ce.campaign_id "
                "WHERE ce.created_at >= now()-interval '30 minutes' "
                "GROUP BY reason ORDER BY count DESC"
            ).fetchall()
            browser_stats = connection.execute(
                "SELECT count(*) FILTER (WHERE created_at>=now()-interval '1 hour') AS attempts_hour,"
                "count(DISTINCT NULLIF(url,'')) FILTER (WHERE created_at>=now()-interval '1 hour') AS urls_hour "
                "FROM crawl_events WHERE status='browser_fallback'"
            ).fetchone()
            stage_stats = connection.execute(
                "SELECT stage,observations,total_seconds,last_seconds,updated_at FROM crawl_stage_totals"
            ).fetchall()
            adaptive = connection.execute(
                "SELECT enabled,current_concurrency,min_concurrency,max_concurrency,state,reason,"
                "limited_ratio,success_rate,outbox_pending,upload_errors,updated_at "
                "FROM continuous_runtime WHERE id=1"
            ).fetchone()
            discovery_errors = connection.execute(
                "WITH cc AS (SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id LIKE 'continuous:%') SELECT 'google_web' AS source,"
                "count(*) AS errors,count(*) FILTER (WHERE ce.http_status IN (403,429) OR "
                "ce.error_code ILIKE '%%captcha%%') AS limited FROM crawl_events ce JOIN cc ON cc.id=ce.campaign_id "
                "WHERE ce.status='discovery_failed' AND ce.created_at>=now()-interval '5 minutes'"
            ).fetchall()
            discovery_success = connection.execute(
                "WITH cc AS (SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id LIKE 'continuous:%') SELECT 'google_web' AS source,"
                "count(DISTINCT cp.page_id) AS accepted "
                "FROM campaign_pages cp JOIN cc ON cc.id=cp.campaign_id JOIN pages p ON p.id=cp.page_id "
                "CROSS JOIN LATERAL jsonb_array_elements_text(p.source_engines) source(value) "
                "WHERE cp.first_seen>=now()-interval '5 minutes' "
                "AND source.value IN ('google','google_web')"
            ).fetchall()
            proxy_utilization = connection.execute(
                "SELECT profile,count(*) FILTER (WHERE last_used_at>=now()-interval '5 minutes') AS active,"
                "COALESCE(sum(requests) FILTER (WHERE last_used_at>=now()-interval '5 minutes'),0) AS requests "
                "FROM crawl_proxy_usage GROUP BY profile"
            ).fetchall()
            google_sources = connection.execute(
                "SELECT source,state,current_rps,circuit_until,requests_window,successes_window,"
                "errors_window,limited_window,captcha_window,requests_total,successes_total,"
                "errors_total,limited_total,captcha_total,result_count,novel_count,cache_hits,"
                "cache_misses,updated_at FROM discovery_source_runtime WHERE source<>'cache' ORDER BY source"
            ).fetchall()
            google_proxy_health = connection.execute(
                "SELECT count(*) AS total,count(*) FILTER (WHERE cooldown_until>now()) AS cooling,"
                "count(*) FILTER (WHERE cooldown_until IS NULL OR cooldown_until<=now()) AS healthy,"
                "count(*) FILTER (WHERE last_used_at>=now()-interval '5 minutes') AS active "
                "FROM google_proxy_sessions"
            ).fetchone()
            google_frontier = connection.execute(
                "SELECT count(*) AS queries,"
                "count(*) FILTER (WHERE state='pending') AS pending,"
                "count(*) FILTER (WHERE state='running') AS running,"
                "count(*) FILTER (WHERE state='cooling') AS cooling,"
                "count(*) FILTER (WHERE state='failed') AS failed,"
                "count(*) FILTER (WHERE state='completed') AS completed,"
                "COALESCE(sum(LEAST(GREATEST(next_page-1,0),max_page)),0) AS covered_pages,"
                "COALESCE(sum(max_page),0) AS total_pages FROM google_page_frontier"
            ).fetchone()
            google_frontier_tasks = connection.execute(
                "SELECT campaign_id,left(query,160) AS query,locale,state,next_page,max_page,"
                "batch_start,batch_end,attempts,next_run_at,last_error,page_stats,updated_at "
                "FROM google_page_frontier ORDER BY "
                "CASE state WHEN 'running' THEN 0 WHEN 'cooling' THEN 1 WHEN 'pending' THEN 2 "
                "WHEN 'failed' THEN 3 ELSE 4 END,updated_at DESC LIMIT 20"
            ).fetchall()
        for campaign in campaigns:
            elapsed = max(float(campaign.pop("elapsed_seconds") or 0), 1)
            recent = int(campaign.pop("recent_count") or 0)
            recent_window = min(elapsed, 60)
            campaign["rate_per_second"] = round(recent / recent_window, 3)
            campaign["projected_daily"] = round(campaign["rate_per_second"] * 86400)
        whale_counts = {str(row["status"]): int(row["count"]) for row in continuous_whale_counts}
        bottlenecks = {str(row["reason"]): int(row["count"]) for row in continuous_bottlenecks}
        if continuous and int(continuous.get("keyword_count") or 0):
            if continuous_daily_target is not None:
                continuous["daily_target"] = max(0, continuous_daily_target)
            if continuous_proxy_profile is not None:
                continuous["proxy_profile"] = continuous_proxy_profile
            elapsed = max(float(continuous.pop("elapsed_seconds") or 0), 1)
            recent = int(continuous.pop("recent_count") or 0)
            recent_window = min(elapsed, 60)
            continuous["rate_per_second"] = round(recent / recent_window, 3)
            continuous["projected_daily"] = round(continuous["rate_per_second"] * 86400)
            continuous["continuous"] = True
            continuous["whale_delivered"] = whale_counts.get("delivered", 0)
            continuous["whale_delivered_today"] = self.continuous_delivered_today()
            continuous["today"] = continuous["whale_delivered_today"]
            continuous["required_rate"] = (
                round(
                    max(int(continuous["daily_target"]) - int(continuous["today"]), 0)
                    / max(86400 - int(datetime.now(timezone.utc).timestamp()) % 86400, 1),
                    3,
                )
                if int(continuous["daily_target"]) > 0 else 0
            )
            continuous["validated_unique_today"] = continuous["today"]
            continuous["novelty_ratio"] = round(
                int(continuous.pop("last_novel") or 0)
                / max(int(continuous.pop("last_candidates") or 0), 1),
                3,
            )
            continuous["fetch_success_rate"] = round(
                int(continuous["whale_delivered"])
                / max(int(continuous["fetched"]), 1),
                3,
            )
            continuous["whale_pending"] = whale_counts.get("pending", 0)
            continuous["queue_depth"] = continuous["whale_pending"]
            continuous["whale_rejected"] = whale_counts.get("rejected", 0)
            continuous["bottlenecks"] = bottlenecks
            continuous["collector_state"] = str(adaptive["state"]) if adaptive else "unknown"
            continuous["collector_reason"] = str(adaptive["reason"]) if adaptive else "unknown"
        else:
            continuous = None
        keyword_pool = self.continuous_keyword_stats()
        discovery_map: dict[str, dict[str, Any]] = {
            name: {"source": name, "accepted": 0, "errors": 0, "limited": 0}
            for name in ("google_web",)
        }
        for row in discovery_success:
            discovery_map[str(row["source"])]["accepted"] += int(row["accepted"])
        for row in discovery_errors:
            discovery_map[str(row["source"])]["errors"] += int(row["errors"])
            discovery_map[str(row["source"])]["limited"] += int(row["limited"])
        if continuous:
            summary = keyword_pool["summary"]
            continuous["keyword_count"] = int(summary["base"])
        return {
            "totals": totals,
            "campaigns": campaigns,
            "continuous_job": continuous,
            "events": events,
            "source_stats": source_stats,
            "continuous_source_stats": continuous_source_stats,
            "keyword_pool": keyword_pool,
            "browser_fallback": browser_stats,
            "stage_metrics": stage_stats,
            "adaptive_concurrency": adaptive,
            "discovery_health": list(discovery_map.values()),
            "proxy_utilization": proxy_utilization,
            "google_sources": google_sources,
            "google_proxy_health": google_proxy_health,
            "google_page_frontier": {
                "summary": google_frontier,
                "tasks": google_frontier_tasks,
            },
        }

    def purge_old_events(self, days: int = 30) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM crawl_events WHERE created_at < now()-(%s * interval '1 day')", (days,)
            )
            return cursor.rowcount
