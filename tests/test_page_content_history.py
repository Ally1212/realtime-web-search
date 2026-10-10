"""Real PostgreSQL coverage for body refresh and global historical deduplication."""
import hashlib
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, PageRecord, _POOLS


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires TEST_DATABASE_URL")
class PageContentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ["TEST_DATABASE_URL"]
        self.schema = "body_history_" + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f"-c search_path={self.schema}")
        self.store = CampaignStore(self.dsn)
        with self.store.connect() as db:
            db.execute((Path(__file__).parents[1] / "migrations/20261009_body_admission_audit.sql").read_text())
        self.campaign = self.store.create_campaign("offline fixture", [], 1000000, "direct")

    def tearDown(self):
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @staticmethod
    def page(url, body):
        return PageRecord(
            url=url, content_hash=hashlib.sha256(body.encode()).hexdigest(),
            title=body[:30], summary=body[:500], content=body, language="en",
            http_status=200, fetched_at=datetime.now(timezone.utc).isoformat(),
            source_engines=("google_news",),
        )

    def record(self, url, body, **kwargs):
        return self.store.record_page(self.campaign, self.page(url, body), **kwargs)

    def rows(self):
        with self.store.connect() as db:
            return db.execute("SELECT * FROM pages ORDER BY id").fetchall()

    def test_refresh_and_old_and_new_body_dedup_preserve_first_admission(self):
        a, b = "Original article. " * 40, "Updated article. " * 40
        first = self.record("https://example.invalid/a", a)
        self.store.mark_indexed([self.page("", a).content_hash])
        refresh = self.record("https://example.invalid/a", b)
        self.assertEqual(refresh, (first[0], False, False, True))
        for body in (a, b):
            self.assertEqual(self.record("https://example.invalid/duplicate", body)[:3],
                             (first[0], False, True))
        # A fresh fetch of the canonical URL may legitimately revert its body.
        self.assertEqual(self.record("https://example.invalid/a", a), (first[0], False, True, True))
        self.assertEqual(self.rows()[0]["content"], a)
        self.assertEqual(self.record("https://example.invalid/a", b), (first[0], False, True, True))
        row = self.rows()[0]
        self.assertEqual(row["content"], b)
        self.assertEqual(row["content_hash"], self.page("", b).content_hash)
        self.assertIsNone(row["indexed_at"])
        with self.store.connect() as db:
            audit = db.execute("SELECT * FROM page_admission_audit").fetchall()
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["original_content_hash"], self.page("", a).content_hash)
            self.assertEqual(audit[0]["original_body_chars"], len(a))
            self.assertEqual(db.execute("SELECT count(*) n FROM page_content_hashes").fetchone()["n"], 2)

    def test_same_hash_leaves_indexed_body_indexed(self):
        body = "Same article. " * 40
        first = self.record("https://example.invalid/a", body)
        self.store.mark_indexed([self.page("", body).content_hash])
        self.assertEqual(self.record("https://example.invalid/a", body), (first[0], False, True, False))

    def concurrent_records(self, inputs):
        barrier = threading.Barrier(len(inputs))
        def run(value):
            barrier.wait(timeout=10)
            return self.record(*value)
        with ThreadPoolExecutor(max_workers=len(inputs)) as pool:
            return list(pool.map(run, inputs))

    def test_concurrent_new_urls_same_body_have_one_admission(self):
        results = self.concurrent_records([
            (f"https://example.invalid/{i}", "Shared body. " * 50) for i in range(8)
        ])
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_concurrent_same_url_different_bodies_preserve_all_hashes(self):
        bodies = [f"Revision {i}. " * 50 for i in range(8)]
        results = self.concurrent_records([( "https://example.invalid/a", b) for b in bodies])
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)
        row = self.rows()[0]
        self.assertEqual(row["content_hash"], hashlib.sha256(row["content"].encode()).hexdigest())
        for body in bodies:
            self.assertEqual(self.record("https://example.invalid/alias", body)[:3], (row["id"], False, True))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM page_content_hashes").fetchone()["n"], 8)
            self.assertEqual(db.execute("SELECT count(*) n FROM page_admission_audit").fetchone()["n"], 1)

    def test_concurrent_refresh_and_new_url_same_body_deduplicate(self):
        self.record("https://example.invalid/a", "Old body. " * 50)
        results = self.concurrent_records([
            ("https://example.invalid/a", "Fresh body. " * 50),
            ("https://example.invalid/b", "Fresh body. " * 50),
        ])
        self.assertEqual(results[0][0], results[1][0])
        rows = self.rows()
        self.assertEqual(sum(r["content"] == "Fresh body. " * 50 for r in rows), 1)
        self.assertTrue(all(r["content_hash"] == hashlib.sha256(r["content"].encode()).hexdigest() for r in rows))

    def whale_campaign(self):
        task = "offline-" + uuid4().hex
        campaign = self.store.create_whale_campaign(
            task_id=task, dataset_id="offline", source_platform="google_search",
            task_type="keyword_search", query="fixture", aliases=[], daily_target=1,
            proxy_profile="direct", task_payload={},
        )
        return campaign, task

    def test_whale_empty_storage_refresh_and_local_body_protection(self):
        campaign, task = self.whale_campaign()
        a, b = "Whale first. " * 50, "Whale refreshed. " * 50
        for i, body in enumerate((a, b)):
            self.store.record_page(campaign, self.page("https://example.invalid/whale", body),
                                   whale_task_id=task, source_record_key=f"whale-{i}",
                                   whale_payload={"body": body})
        row = self.rows()[0]
        self.assertEqual(row["content"], "")
        self.assertEqual(row["content_hash"], self.page("", b).content_hash)
        local_body = "Retained local article. " * 50
        local = self.record("https://example.invalid/local", local_body)
        upload_body = "New upload revision. " * 50
        self.store.record_page(campaign, self.page("https://example.invalid/local", upload_body),
                               whale_task_id=task, source_record_key="whale-local",
                               whale_payload={"body": upload_body})
        with self.store.connect() as db:
            local_row = db.execute("SELECT * FROM pages WHERE id=%s", (local[0],)).fetchone()
            payloads = db.execute("SELECT payload FROM whale_ingest_outbox ORDER BY id").fetchall()
        self.assertEqual(local_row["content"], local_body)
        self.assertEqual(local_row["title"], local_body[:30])
        self.assertEqual(local_row["content_hash"], self.page("", local_body).content_hash)
        self.assertEqual([p["payload"]["body"] for p in payloads], [a, b, upload_body])
        self.assertEqual(self.record("https://example.invalid/alias", upload_body)[:3], (local[0], False, True))

    def test_legacy_backfill_runs_once_and_preserves_unrecoverable_rows(self):
        a, b = "Legacy original. " * 50, "Legacy current. " * 50
        first = self.record("https://example.invalid/legacy", a)
        with self.store.connect() as db:
            db.execute("UPDATE pages SET content=%s WHERE id=%s", (b, first[0]))
            db.execute("DELETE FROM page_content_hashes")
            db.execute("DELETE FROM campaign_schema_migrations")
        self.store.initialize()
        with self.store.connect() as db:
            before = db.execute("SELECT * FROM campaign_schema_migrations").fetchone()
            hashes = db.execute("SELECT content_hash FROM page_content_hashes").fetchall()
            # A sentinel detects accidental full backfill on each initialize.
            db.execute("DELETE FROM page_content_hashes WHERE content_hash=%s", (self.page("", b).content_hash,))
        self.store.initialize()
        with self.store.connect() as db:
            after = db.execute("SELECT * FROM campaign_schema_migrations").fetchone()
            self.assertEqual(before, after)
            self.assertEqual(db.execute("SELECT count(*) n FROM page_content_hashes").fetchone()["n"], 1)
        self.assertEqual({r["content_hash"] for r in hashes}, {self.page("", a).content_hash, self.page("", b).content_hash})
        # Existing bad rows are retained, not silently merged or reconstructed.
        self.assertEqual(self.rows()[0]["content_hash"], self.page("", a).content_hash)
        self.assertEqual(self.rows()[0]["content"], b)

    def test_backfilled_current_body_can_repair_hash_on_verified_refresh(self):
        a, b = "Legacy original. " * 50, "Legacy current. " * 50
        first = self.record("https://example.invalid/legacy", a)
        with self.store.connect() as db:
            db.execute("UPDATE pages SET content=%s WHERE id=%s", (b, first[0]))
            db.execute("DELETE FROM page_content_hashes")
            db.execute("DELETE FROM campaign_schema_migrations")
        self.store.initialize()
        self.assertEqual(self.record("https://example.invalid/new-url", b)[:3], (first[0], False, True))
        self.assertEqual(self.rows()[0]["content_hash"], self.page("", b).content_hash)
        self.assertEqual(self.record("https://example.invalid/old-version", a)[:3], (first[0], False, True))
        self.assertEqual(self.rows()[0]["content"], b)
        with self.store.connect() as db:
            audit = db.execute("SELECT * FROM page_admission_audit").fetchone()
        self.assertEqual(audit["original_content_hash"], self.page("", a).content_hash)

    def test_incoming_mismatched_hash_rejected(self):
        from dataclasses import replace
        page = replace(self.page("https://example.invalid/a", "Body"), content_hash="a" * 64)
        with self.assertRaises(ValueError):
            self.store.record_page(self.campaign, page)
        self.assertEqual(self.rows(), [])
