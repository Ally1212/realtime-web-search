import unittest
from unittest.mock import MagicMock, patch

from realtime.campaign_queue import CampaignQueue


class CampaignQueueTests(unittest.TestCase):
    @patch("realtime.campaign_queue.Redis.from_url")
    def test_recover_clears_stale_state_and_requeues(self, from_url):
        redis = MagicMock()
        pipeline = MagicMock()
        redis.pipeline.return_value.__enter__.return_value = pipeline
        redis.set.return_value = True
        from_url.return_value = redis
        queue = CampaignQueue("redis://example")

        queue.recover("campaign-id")

        pipeline.delete.assert_called_once_with("realtime:campaign:queued:campaign-id")
        pipeline.zrem.assert_called_once_with(queue.DELAYED, "campaign-id")
        pipeline.execute.assert_called_once_with()
        redis.lpush.assert_called_once_with(queue.READY, "campaign-id")


if __name__ == "__main__":
    unittest.main()
