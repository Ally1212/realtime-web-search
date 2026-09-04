import unittest
from unittest.mock import MagicMock, patch

from realtime.config import Config
from realtime.whale_collector import ContinuousWhaleRunner, WhaleRunner, whale_message


class WhaleCollectorTests(unittest.TestCase):
    def test_message_has_stable_identity_and_required_whale_fields(self):
        config = Config()
        task = {
            "task_id": "ctask_1", "dataset_id": "web_raw", "source_platform": "website",
            "task_type": "keyword_search",
        }
        item = {
            "campaign_id": "campaign_1", "url": "https://example.com/article",
            "title": "Example", "content": "A" * 120, "content_hash": "a" * 64,
            "language": "en", "fetched_at": "2026-09-02T00:00:00+00:00",
            "discovered_at": "2026-09-02T00:00:00+00:00", "query": "example",
            "source_engines": ("searxng",),
        }

        record_key, message = whale_message(item, task, config)

        self.assertTrue(record_key.startswith("website:" + __import__("hashlib").sha256(item["url"].encode()).hexdigest() + ":"))
        self.assertEqual(message["schema_version"], "whale.ingest.v1")
        self.assertEqual(message["dataset_id"], "web_raw")
        self.assertNotIn("collection_task_id", message["source"])
        self.assertEqual(message["content"]["canonical_url"], item["url"])
        self.assertEqual(message["content"]["body_text"], item["content"])
        self.assertEqual(message["provided_capabilities"], ["identity", "title", "body"])

    def test_task_payload_validation(self):
        task = {"task_type": "content_detail", "payload": {"urls": ["https://example.com"]}}
        query, aliases, target, profile = WhaleRunner._payload(task)
        self.assertEqual(query, "content detail")
        self.assertEqual(aliases, [])
        self.assertGreater(target, 0)
        self.assertIn(profile, {"private", "public", "direct"})

        with self.assertRaises(ValueError):
            WhaleRunner._payload({"task_type": "keyword_search", "payload": {}})

    def test_keyword_payload_is_supported(self):
        query, aliases, target, profile = WhaleRunner._payload({
            "task_type": "keyword_search",
            "payload": {"keyword": "AI engineer", "daily_target": 10, "proxy_profile": "direct"},
        })
        self.assertEqual(query, "AI engineer")
        self.assertEqual(aliases, [])
        self.assertEqual(target, 10)
        self.assertEqual(profile, "direct")

    def test_standard_whale_form_payload_is_supported(self):
        query, aliases, target, profile = WhaleRunner._payload({
            "task_type": "keyword_search",
            "payload": {"keyword": "AI engineer", "max_items": 20, "max_pages": 1, "page_size": 25},
        })
        self.assertEqual(query, "AI engineer")
        self.assertEqual(aliases, [])
        self.assertEqual(target, 20)
        self.assertEqual(profile, "direct")

    @patch("realtime.whale_collector.subprocess.Popen")
    @patch("realtime.whale_collector.WhaleRunner")
    @patch("realtime.whale_collector.CampaignStore")
    def test_continuous_runner_creates_keyword_campaign(self, store_class, runner_class, popen):
        store = MagicMock()
        store.create_whale_campaign.return_value = "campaign-1"
        store.whale_outbox_counts.return_value = {}
        store_class.return_value = store
        runner = MagicMock()
        runner._stats.return_value = {"collected_count": 3, "ingested_count": 3, "duplicate_count": 0}
        runner_class.return_value = runner
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        popen.return_value = process
        config = Config(
            whale_collector_api_key="key",
            whale_dataset_id="social_media_raw",
            whale_source_platform="google_search",
            continuous_max_items_per_keyword=7,
            continuous_proxy_profile="direct",
        )

        ContinuousWhaleRunner(config)._run_keyword("AI chips")

        store.create_whale_campaign.assert_called_once()
        kwargs = store.create_whale_campaign.call_args.kwargs
        self.assertEqual(kwargs["dataset_id"], "social_media_raw")
        self.assertEqual(kwargs["source_platform"], "google_search")
        self.assertEqual(kwargs["query"], "AI chips")
        self.assertEqual(kwargs["daily_target"], 7)
        self.assertEqual(kwargs["proxy_profile"], "direct")
        self.assertTrue(kwargs["reactivate_existing"])
        self.assertEqual(kwargs["task_id"], "continuous:b5ffddc26966")
        store.update_whale_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
