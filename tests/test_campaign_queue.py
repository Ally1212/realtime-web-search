import unittest
from unittest.mock import MagicMock, patch

from realtime.campaign_queue import CampaignQueue


class CampaignQueueTests(unittest.TestCase):
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
