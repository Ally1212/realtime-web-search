import hashlib
import os
import unittest
from datetime import datetime, timezone

from realtime.campaign_store import CampaignStore, PageRecord


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


if __name__ == "__main__":
    unittest.main()
