from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .campaign_store import CampaignStore
from .config import Config


ALLOWED_WEB_SOURCES = ("google", "google_web")


def _preview(store: CampaignStore) -> dict[str, int]:
    with store.connect() as connection:
        row = connection.execute(
            "WITH non_web AS (SELECT encode(sha256(convert_to(url,'UTF8')),'hex') AS url_hash "
            "FROM pages WHERE NOT (source_engines ?| %s)), matched_outbox AS ("
            "SELECT o.id FROM whale_ingest_outbox o JOIN non_web n "
            "ON n.url_hash=split_part(o.source_record_key,':',2)) SELECT "
            "(SELECT count(*) FROM campaigns WHERE status='active') AS active_campaigns,"
            "(SELECT count(*) FROM pages WHERE NOT (source_engines ?| %s)) AS non_web_pages,"
            "(SELECT count(*) FROM pages WHERE source_engines ?| %s "
            "AND jsonb_array_length(source_engines)>1) AS mixed_pages,"
            "(SELECT count(*) FROM matched_outbox) AS matched_outbox,"
            "(SELECT count(*) FROM continuous_keywords WHERE kind='trend') AS trend_keywords,"
            "(SELECT count(*) FROM discovery_query_cache WHERE source<>'google_web') AS non_web_cache,"
            "(SELECT count(*) FROM discovery_source_runtime WHERE source<>'google_web') AS non_web_runtime",
            (list(ALLOWED_WEB_SOURCES), list(ALLOWED_WEB_SOURCES), list(ALLOWED_WEB_SOURCES)),
        ).fetchone()
    return {name: int(value or 0) for name, value in row.items()}


def _manifest_rows(store: CampaignStore) -> list[dict[str, Any]]:
    with store.connect() as connection:
        return connection.execute(
            "WITH non_web AS (SELECT encode(sha256(convert_to(p.url,'UTF8')),'hex') AS url_hash "
            "FROM pages p WHERE NOT (p.source_engines ?| %s)) "
            "SELECT w.dataset_id,w.source_platform,o.source_record_key,n.url_hash "
            "FROM whale_ingest_outbox o JOIN whale_task_runs w ON w.task_id=o.task_id "
            "JOIN non_web n ON n.url_hash=split_part(o.source_record_key,':',2) "
            "ORDER BY w.dataset_id,w.source_platform,o.source_record_key",
            (list(ALLOWED_WEB_SOURCES),),
        ).fetchall()


def _write_manifest(rows: list[dict[str, Any]], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"whale-delete-non-google-web-{timestamp}.csv"
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("dataset_id", "source_platform", "source_record_key", "url_hash"),
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def purge_non_google_web(config: Config, manifest_directory: Path, *, apply: bool) -> dict[str, Any]:
    store = CampaignStore(config.database_url)
    before = _preview(store)
    result: dict[str, Any] = {"mode": "apply" if apply else "preview", "before": before}
    if not apply:
        return result
    if before["active_campaigns"]:
        raise RuntimeError("refusing purge while campaigns are active")
    manifest_rows = _manifest_rows(store)
    if len(manifest_rows) != before["matched_outbox"]:
        raise RuntimeError("Whale manifest count changed during purge preparation")
    manifest_path = _write_manifest(manifest_rows, manifest_directory)
    with store.connect() as connection:
        with connection.transaction():
            connection.execute(
                "WITH non_web AS (SELECT encode(sha256(convert_to(p.url,'UTF8')),'hex') AS url_hash "
                "FROM pages p WHERE NOT (p.source_engines ?| %s)) "
                "DELETE FROM whale_ingest_outbox o USING non_web n "
                "WHERE n.url_hash=split_part(o.source_record_key,':',2)",
                (list(ALLOWED_WEB_SOURCES),),
            )
            connection.execute(
                "DELETE FROM pages WHERE NOT (source_engines ?| %s)",
                (list(ALLOWED_WEB_SOURCES),),
            )
            connection.execute(
                "UPDATE pages p SET source_engines=(SELECT jsonb_agg(source.value) "
                "FROM jsonb_array_elements_text(p.source_engines) source(value) "
                "WHERE source.value=ANY(%s)) WHERE source_engines ?| %s "
                "AND jsonb_array_length(source_engines)>1",
                (list(ALLOWED_WEB_SOURCES), list(ALLOWED_WEB_SOURCES)),
            )
            connection.execute("DELETE FROM discovery_query_cache WHERE source<>'google_web'")
            connection.execute("DELETE FROM discovery_source_runtime WHERE source<>'google_web'")
            connection.execute("DELETE FROM continuous_keyword_runs")
            connection.execute("DELETE FROM continuous_keywords WHERE kind='trend'")
            connection.execute(
                "UPDATE continuous_keywords SET kind='base',state='active',score=50,runs=0,"
                "low_yield_runs=0,last_candidates=0,last_fetched=0,last_delivered=0,last_failed=0,"
                "last_duplicates=0,last_run_at=NULL,next_run_at=now(),expires_at=NULL,updated_at=now()"
            )
            connection.execute("DELETE FROM crawl_events")
            connection.execute("DELETE FROM crawl_stage_totals")
            connection.execute("DELETE FROM crawl_proxy_usage")
            connection.execute(
                "UPDATE discovery_cursors SET consecutive_empty=0,last_candidates=0,last_novel=0,"
                "next_run_at=now(),updated_at=now()"
            )
            connection.execute(
                "UPDATE campaigns c SET discovered=(SELECT count(*) FROM campaign_pages cp "
                "WHERE cp.campaign_id=c.id),fetched=(SELECT count(*) FROM campaign_pages cp "
                "WHERE cp.campaign_id=c.id),failed=0,duplicates=0,irrelevant=0,last_error=NULL,"
                "updated_at=now()"
            )
            connection.execute(
                "UPDATE continuous_runtime SET state='paused',reason='operator_paused',"
                "limited_ratio=0,success_rate=0,outbox_pending=0,upload_errors=0,updated_at=now() "
                "WHERE id=1"
            )
            connection.execute(
                "ALTER TABLE continuous_keywords DROP CONSTRAINT IF EXISTS continuous_keywords_kind_check"
            )
            connection.execute(
                "ALTER TABLE continuous_keywords ADD CONSTRAINT continuous_keywords_kind_check "
                "CHECK (kind='base')"
            )
            connection.execute(
                "ALTER TABLE continuous_keywords DROP CONSTRAINT IF EXISTS continuous_keywords_state_check"
            )
            connection.execute(
                "ALTER TABLE continuous_keywords ADD CONSTRAINT continuous_keywords_state_check "
                "CHECK (state IN ('active','cooldown','retired'))"
            )
    result["manifest"] = str(manifest_path)
    result["manifest_rows"] = len(manifest_rows)
    result["after"] = _preview(store)
    return result


def run_purge(manifest_directory: Path, *, apply: bool) -> None:
    print(json.dumps(
        purge_non_google_web(Config(), manifest_directory, apply=apply),
        ensure_ascii=False,
        default=str,
    ))
