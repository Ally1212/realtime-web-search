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
  language varchar(8),
  http_status integer NOT NULL,
  fetched_at timestamptz NOT NULL,
  source_engines jsonb NOT NULL DEFAULT '[]'::jsonb
);
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
"""


@dataclass(frozen=True)
class PageRecord:
    url: str
    content_hash: str
    title: str
    summary: str
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

    def record_page(self, campaign_id: str, page: PageRecord) -> tuple[int, bool, bool]:
        """Returns (page_id, new campaign association, duplicate content)."""
        with self.connect() as connection:
            with connection.transaction():
                existing = connection.execute(
                    "SELECT id,url,content_hash FROM pages WHERE url=%s OR content_hash=%s "
                    "ORDER BY (content_hash=%s) DESC LIMIT 1 FOR UPDATE",
                    (page.url, page.content_hash, page.content_hash),
                ).fetchone()
                duplicate_content = bool(existing and existing["content_hash"] == page.content_hash)
                if existing:
                    page_id = int(existing["id"])
                    connection.execute(
                        "UPDATE pages SET title=%s,summary=%s,language=%s,http_status=%s,"
                        "fetched_at=%s,source_engines=%s::jsonb WHERE id=%s",
                        (
                            page.title, page.summary, page.language, page.http_status,
                            page.fetched_at, json.dumps(page.source_engines), page_id,
                        ),
                    )
                else:
                    row = connection.execute(
                        "INSERT INTO pages(url,content_hash,title,summary,language,http_status,fetched_at,source_engines) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING id",
                        (
                            page.url, page.content_hash, page.title, page.summary, page.language,
                            page.http_status, page.fetched_at, json.dumps(page.source_engines),
                        ),
                    ).fetchone()
                    if row:
                        page_id = int(row["id"])
                    else:
                        # Another worker inserted the same URL or content hash concurrently.
                        concurrent = connection.execute(
                            "SELECT id,content_hash FROM pages WHERE url=%s OR content_hash=%s "
                            "ORDER BY (content_hash=%s) DESC LIMIT 1 FOR UPDATE",
                            (page.url, page.content_hash, page.content_hash),
                        ).fetchone()
                        if not concurrent:
                            raise RuntimeError("page deduplication race could not be resolved")
                        page_id = int(concurrent["id"])
                        duplicate_content = concurrent["content_hash"] == page.content_hash
                inserted = connection.execute(
                    "INSERT INTO campaign_pages(campaign_id,page_id) VALUES(%s,%s) "
                    "ON CONFLICT DO NOTHING RETURNING page_id",
                    (campaign_id, page_id),
                ).fetchone()
        return page_id, inserted is not None, duplicate_content

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
        for campaign in campaigns:
            elapsed = max(float(campaign.pop("elapsed_seconds") or 0), 1)
            campaign["rate_per_second"] = round(int(campaign["today"]) / elapsed, 3)
            campaign["projected_daily"] = round(campaign["rate_per_second"] * 86400)
        return {"totals": totals, "campaigns": campaigns, "events": events}

    def purge_old_events(self, days: int = 30) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM crawl_events WHERE created_at < now()-(%s * interval '1 day')", (days,)
            )
            return cursor.rowcount
