import unittest

from realtime.config import Config
from realtime.whale_collector import WhaleRunner, whale_message


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


if __name__ == "__main__":
    unittest.main()
