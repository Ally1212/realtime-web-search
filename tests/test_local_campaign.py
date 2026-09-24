import hashlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from realtime.campaign_store import CampaignStore, PageRecord
from realtime.config import Config
from realtime.whale_collector import whale_message


class WhaleMessageTests(unittest.TestCase):
    def test_whale_message_uses_explicit_site_publication_time(self):
        task = {
            "task_id": "task-1",
            "dataset_id": "dataset",
            "source_platform": "google_search",
            "task_type": "keyword_search",
        }
        item = {
            "campaign_id": "campaign",
            "url": "https://example.com/article",
            "title": "Article",
            "content": "Body",
            "source_engines": ("google_web",),
            "discovered_at": "2026-09-23T00:00:00+00:00",
            "fetched_at": "2026-09-24T00:00:00+00:00",
            "published_at": "2026-09-23T08:00:00+08:00",
            "publication_source": "jsonld:datePublished",
        }
        _, message = whale_message(item, task, Config(database_url="postgresql://invalid"))
        self.assertEqual(message["content"]["published_at"], "2026-09-23T08:00:00+08:00")
        metadata = message["discovery"]["metadata"]
        self.assertEqual(metadata["publication_source"], "jsonld:datePublished")
        self.assertFalse(metadata["published_at_is_collector_fallback"])

    def test_whale_message_falls_back_to_collector_time(self):
        task = {
            "task_id": "task-1",
            "dataset_id": "dataset",
            "source_platform": "google_search",
            "task_type": "keyword_search",
        }
        item = {
            "campaign_id": "campaign",
            "url": "https://example.com/article",
            "content": "Body",
            "discovered_at": "2026-09-23T00:00:00+00:00",
            "fetched_at": "2026-09-24T00:00:00+00:00",
        }
        _, message = whale_message(item, task, Config(database_url="postgresql://invalid"))
        self.assertEqual(message["content"]["published_at"], "2026-09-24T00:00:00+00:00")
        metadata = message["discovery"]["metadata"]
        self.assertEqual(metadata["publication_source"], "collector:fetched_at")
        self.assertTrue(metadata["published_at_is_collector_fallback"])



@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires TEST_DATABASE_URL")
class LocalCampaignIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = CampaignStore(os.environ["TEST_DATABASE_URL"])
        self.campaign_ids: list[str] = []
        self.page_ids: list[int] = []

    def tearDown(self):
        if not self.campaign_ids:
            return
        with self.store.connect() as connection:
            connection.execute(
                "DELETE FROM campaigns WHERE id=ANY(%s)", (self.campaign_ids,)
            )
            if self.page_ids:
                connection.execute("DELETE FROM pages WHERE id=ANY(%s)", (self.page_ids,))

    @staticmethod
    def page(url: str, content: str) -> PageRecord:
        return PageRecord(
            url=url,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            title="Local result",
            summary=content[:500],
            content=content,
            language="en",
            http_status=200,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_engines=("google_web",),
        )

    def test_local_campaign_keeps_body_and_exposes_results(self):
        campaign_id = self.store.create_campaign("local test", [], 1_000_000, "direct")
        self.campaign_ids.append(campaign_id)
        body = "Locally retained article body for browser display."

        page_id, _, _, _ = self.store.record_page(
            campaign_id,
            self.page(f"https://example.com/local-{campaign_id}", body),
        )
        self.page_ids.append(page_id)

        detail = self.store.local_campaign_detail(campaign_id)
        self.assertIsNotNone(detail)
        self.assertFalse(detail["upload_to_whale"])
        self.assertEqual(detail["daily_target"], 0)
        self.assertEqual(detail["saved_count"], 1)
        self.assertIn(body, detail["pages"][0]["preview"])
        with self.store.connect() as connection:
            stored = connection.execute(
                "SELECT content FROM pages p JOIN campaign_pages cp ON cp.page_id=p.id "
                "WHERE cp.campaign_id=%s", (campaign_id,),
            ).fetchone()
        self.assertEqual(stored["content"], body)

    def test_whale_campaign_does_not_keep_body_in_pages(self):
        task_id = f"test-whale-local-storage-{os.getpid()}"
        campaign_id = self.store.create_whale_campaign(
            task_id=task_id,
            dataset_id="test",
            source_platform="google_search",
            task_type="keyword_search",
            query="whale storage test",
            aliases=[],
            daily_target=1,
            proxy_profile="direct",
            task_payload={"keyword": "whale storage test"},
        )
        self.campaign_ids.append(campaign_id)
        body = "This body belongs in the transient Whale payload only."
        page = self.page(f"https://example.com/whale-{campaign_id}", body)

        page_id, _, _, _ = self.store.record_page(
            campaign_id,
            page,
            whale_task_id=task_id,
            source_record_key=f"test:{campaign_id}",
            whale_payload={"content": {"body_text": body}},
        )
        self.page_ids.append(page_id)

        self.assertIsNone(self.store.local_campaign_detail(campaign_id))
        with self.store.connect() as connection:
            stored = connection.execute(
                "SELECT content FROM pages p JOIN campaign_pages cp ON cp.page_id=p.id "
                "WHERE cp.campaign_id=%s", (campaign_id,),
            ).fetchone()
        self.assertEqual(stored["content"], "")

    def test_whale_outbox_merges_updated_payload_and_keeps_receipt(self):
        task_id = f"test-whale-outbox-{os.getpid()}"
        campaign_id = self.store.create_whale_campaign(
            task_id=task_id,
            dataset_id="test",
            source_platform="google_search",
            task_type="keyword_search",
            query="whale outbox test",
            aliases=[],
            daily_target=1,
            proxy_profile="direct",
            task_payload={"keyword": "whale outbox test"},
        )
        self.campaign_ids.append(campaign_id)
        source_record_key = f"google_search:{uuid4()}"
        first = {
            "source": {"source_record_key": source_record_key, "payload_hash": "sha256:first"},
            "discovery": {"metadata": {"readiness": {"provided_capabilities": ["identity"]}}},
            "content": {"title": "First"},
            "provided_capabilities": ["identity", "title"],
        }
        second = {
            "source": {"source_record_key": source_record_key, "payload_hash": "sha256:second"},
            "discovery": {"metadata": {"readiness": {"provided_capabilities": ["identity", "body"]}}},
            "content": {"body_text": "Second body"},
            "provided_capabilities": ["identity", "body"],
        }

        self.store.queue_whale_message(task_id, source_record_key, first)
        self.store.queue_whale_message(task_id, source_record_key, second)

        row = self.store.whale_outbox(task_id, 1)[0]
        self.assertEqual(row["payload"]["source"]["payload_hash"], "sha256:second")
        self.assertEqual(
            row["payload"]["provided_capabilities"],
            ["identity", "title", "body"],
        )
        self.assertTrue(row["payload"]["discovery"]["metadata"]["readiness"]["ready"])
        self.assertEqual(row["payload"]["content"]["title"], "First")
        self.assertEqual(row["payload"]["content"]["body_text"], "Second body")
        self.store.mark_whale_outbox(
            [row["id"]],
            status="delivered",
            receipt_status="queued",
            receipt={"receipt_status": "queued", "id": "remote-1"},
        )
        self.assertEqual(self.store.whale_outbox_counts(task_id), {"delivered": 1})
        self.assertEqual(self.store.whale_receipt_counts(task_id), {"queued": 1})

    def test_continuous_pause_resume_updates_campaigns_and_tasks(self):
        task_id = f"continuous:{uuid4().hex[:12]}"
        campaign_id = self.store.create_whale_campaign(
            task_id=task_id,
            dataset_id="test",
            source_platform="google_search",
            task_type="keyword_search",
            query="continuous pause test",
            aliases=[],
            daily_target=1,
            proxy_profile="direct",
            task_payload={"keyword": "continuous pause test"},
        )
        self.campaign_ids.append(campaign_id)

        self.assertEqual(self.store.set_continuous_status("paused"), 1)
        self.assertTrue(self.store.continuous_is_paused())
        self.assertEqual(self.store.due_continuous_keywords(1), [])

        self.assertEqual(self.store.set_continuous_status("active"), 1)
        self.assertFalse(self.store.continuous_is_paused())

    def test_legacy_non_html_failure_gets_one_retry_for_pdf_support(self):
        campaign_id = self.store.create_campaign("pdf retry", [], 1, "direct")
        self.campaign_ids.append(campaign_id)
        legacy_url = f"https://example.com/legacy-{campaign_id}.pdf"
        permanent_url = f"https://example.com/unsupported-{campaign_id}.zip"
        self.store.record_event(
            campaign_id, legacy_url, "permanent_failed", 200, "non_html"
        )
        self.store.record_event(
            campaign_id,
            permanent_url,
            "permanent_failed",
            200,
            "不支持的内容类型: application/zip",
        )

        processed = self.store.processed_urls(
            campaign_id, [legacy_url, permanent_url]
        )

        self.assertNotIn(legacy_url, processed)
        self.assertIn(permanent_url, processed)

    def test_concurrent_same_query_requests_reuse_one_active_campaign(self):
        query = f"Same   Keyword {os.getpid()} {uuid4()}"

        def create(index):
            return self.store.get_or_create_active_local_campaign(
                query if index % 2 else " ".join(query.lower().split()),
                [f"alias-{index % 3}"], 100 + index, "direct",
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(create, range(64)))
        campaign_ids = {campaign_id for campaign_id, _ in results}
        self.assertEqual(len(campaign_ids), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        campaign_id = campaign_ids.pop()
        self.campaign_ids.append(campaign_id)
        campaign = self.store.campaign(campaign_id)
        self.assertEqual(campaign["daily_target"], 163)
        self.assertEqual(set(campaign["aliases"]), {"alias-0", "alias-1", "alias-2"})


if __name__ == "__main__":
    unittest.main()
