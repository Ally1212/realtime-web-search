from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


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
        if initialize:
            self.initialize()

    def connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(SCHEMA)

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
            connection.execute(
                f"UPDATE whale_ingest_outbox SET status=%s,attempts=attempts+1,last_error=%s,"
                f"delivered_at={delivered},updated_at=now() WHERE id=ANY(%s)",
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

    def record_page(self, campaign_id: str, page: PageRecord) -> tuple[int, bool, bool, bool]:
        """Returns (page_id, new association, duplicate content, needs indexing)."""
        with self.connect() as connection:
            with connection.transaction():
                existing = connection.execute(
                    "SELECT id,url,content_hash,indexed_at FROM pages WHERE url=%s OR content_hash=%s "
                    "ORDER BY (content_hash=%s) DESC LIMIT 1 FOR UPDATE",
                    (page.url, page.content_hash, page.content_hash),
                ).fetchone()
                duplicate_content = bool(existing and existing["content_hash"] == page.content_hash)
                needs_indexing = not existing or existing["indexed_at"] is None
                if existing:
                    page_id = int(existing["id"])
                    connection.execute(
                        "UPDATE pages SET title=%s,summary=%s,content=%s,language=%s,http_status=%s,"
                        "fetched_at=%s,source_engines=%s::jsonb WHERE id=%s",
                        (
                            page.title, page.summary, page.content, page.language, page.http_status,
                            page.fetched_at, json.dumps(page.source_engines), page_id,
                        ),
                    )
                else:
                    row = connection.execute(
                        "INSERT INTO pages(url,content_hash,title,summary,content,language,http_status,fetched_at,source_engines) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING id",
                        (
                            page.url, page.content_hash, page.title, page.summary, page.content, page.language,
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

    def stats(self) -> dict[str, Any]:
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
                "COALESCE(sum(daily_target),0) AS daily_target,"
                "'direct' AS proxy_profile,"
                "CASE WHEN COALESCE(bool_or(status='failed'),false) THEN 'failed' "
                "WHEN COALESCE(bool_or(status='active'),false) THEN 'active' "
                "WHEN COALESCE(bool_or(status='paused'),false) THEN 'paused' ELSE 'stopped' END AS status,"
                "COALESCE(sum(discovered),0) AS discovered,COALESCE(sum(fetched),0) AS fetched,"
                "COALESCE(sum(failed),0) AS failed,COALESCE(sum(duplicates),0) AS duplicates,"
                "COALESCE(sum(irrelevant),0) AS irrelevant,NULL AS last_error,"
                "min(created_at) AS created_at,max(updated_at) AS updated_at,"
                "(SELECT count(DISTINCT cp.page_id) FROM campaign_pages cp JOIN continuous_campaigns cc "
                "ON cc.id=cp.campaign_id WHERE cp.first_seen >= "
                "date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS today,"
                "(SELECT count(DISTINCT cp.page_id) FROM campaign_pages cp JOIN continuous_campaigns cc "
                "ON cc.id=cp.campaign_id WHERE cp.first_seen >= now()-interval '60 seconds') AS recent_count,"
                "EXTRACT(EPOCH FROM (now()-GREATEST(COALESCE(min(created_at),now()), "
                "date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))) AS elapsed_seconds,"
                "count(*) AS keyword_count FROM continuous_campaigns"
            ).fetchone()
            continuous_source_stats = connection.execute(
                "WITH continuous_campaigns AS ("
                "SELECT c.id FROM campaigns c JOIN whale_task_runs w ON w.campaign_id=c.id "
                "WHERE w.task_id ~ '^continuous:[0-9a-f]{12}$'"
                ") SELECT 'continuous' AS campaign_id, source.value AS source, "
                "count(DISTINCT cp.page_id) AS today "
                "FROM campaign_pages cp JOIN continuous_campaigns cc ON cc.id=cp.campaign_id "
                "JOIN pages p ON p.id=cp.page_id "
                "CROSS JOIN LATERAL jsonb_array_elements_text(p.source_engines) AS source(value) "
                "WHERE cp.first_seen >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "AND source.value='google' "
                "GROUP BY source.value ORDER BY today DESC,source.value"
            ).fetchall()
        for campaign in campaigns:
            elapsed = max(float(campaign.pop("elapsed_seconds") or 0), 1)
            recent = int(campaign.pop("recent_count") or 0)
            recent_window = min(elapsed, 60)
            campaign["rate_per_second"] = round(recent / recent_window, 3)
            campaign["projected_daily"] = round(campaign["rate_per_second"] * 86400)
        if continuous and int(continuous.get("keyword_count") or 0):
            elapsed = max(float(continuous.pop("elapsed_seconds") or 0), 1)
            recent = int(continuous.pop("recent_count") or 0)
            recent_window = min(elapsed, 60)
            continuous["rate_per_second"] = round(recent / recent_window, 3)
            continuous["projected_daily"] = round(continuous["rate_per_second"] * 86400)
            continuous["continuous"] = True
        else:
            continuous = None
        return {
            "totals": totals,
            "campaigns": campaigns,
            "continuous_job": continuous,
            "events": events,
            "source_stats": source_stats,
            "continuous_source_stats": continuous_source_stats,
        }

    def purge_old_events(self, days: int = 30) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM crawl_events WHERE created_at < now()-(%s * interval '1 day')", (days,)
            )
            return cursor.rowcount
