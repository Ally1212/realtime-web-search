import unittest
from unittest.mock import MagicMock, patch

from redis.exceptions import ConnectionError as RedisConnectionError

from realtime.campaign_queue import CampaignQueue


class CampaignQueueTests(unittest.TestCase):
    @patch("realtime.campaign_queue.Redis.from_url")
    def test_marker_cleanup_disconnect_keeps_the_popped_campaign(self, from_url):
        queue = CampaignQueue("redis://example")
        redis = from_url.return_value
        redis.brpop.return_value = (queue.READY, "campaign-id")
        redis.delete.side_effect = RedisConnectionError("connection interrupted")
        self.assertEqual(queue.pop(), "campaign-id")

    @patch("realtime.campaign_queue.Redis.from_url")
    def test_recover_uses_atomic_redis_script(self, from_url):
        redis = MagicMock()
        from_url.return_value = redis
        queue = CampaignQueue("redis://example")

        queue.recover("campaign-id")

        args = redis.eval.call_args.args
        self.assertEqual(args[1:5], (3, queue.READY, queue.DELAYED,
                                     "realtime:campaign:queued:campaign-id"))
        self.assertEqual(args[5], "campaign-id")
        redis.pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
