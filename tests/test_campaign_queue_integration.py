"""Run against disposable keys in a real Redis/Valkey instance."""
import os
import unittest
from uuid import uuid4

from realtime.campaign_queue import CampaignQueue


@unittest.skipUnless(os.getenv("TEST_REDIS_URL"), "TEST_REDIS_URL is required")
class CampaignQueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.prefix = "queue-test-" + uuid4().hex
        self.queue = CampaignQueue(os.environ["TEST_REDIS_URL"])
        self.queue.READY = self.prefix + ":ready"
        self.queue.DELAYED = self.prefix + ":delayed"
        self.ids = [self.prefix + ":" + str(i) for i in range(3)]

    def tearDown(self):
        self.queue.redis.delete(self.queue.READY, self.queue.DELAYED,
                                *("realtime:campaign:queued:" + x for x in self.ids))

    def test_repeated_recovery_queues_interrupted_work_once(self):
        cid = self.ids[0]
        self.queue.recover(cid)
        self.queue.recover(cid)
        self.assertEqual(self.queue.redis.lrange(self.queue.READY, 0, -1), [cid])

    def test_recovery_preserves_future_delay_and_removes_stale_ready_copy(self):
        cid = self.ids[0]
        self.queue.schedule(cid, 600)
        due = self.queue.redis.zscore(self.queue.DELAYED, cid)
        self.queue.redis.lpush(self.queue.READY, cid)
        self.queue.recover(cid)
        self.assertEqual(self.queue.redis.zscore(self.queue.DELAYED, cid), due)
        self.assertEqual(self.queue.redis.llen(self.queue.READY), 0)

    def test_expired_delay_is_recovered(self):
        cid = self.ids[0]
        self.queue.schedule(cid, -1)
        self.queue.recover(cid)
        self.assertIsNone(self.queue.redis.zscore(self.queue.DELAYED, cid))
        self.assertEqual(self.queue.pop(1), cid)

    def test_ready_recovery_clears_expired_delay_without_second_enqueue(self):
        cid = self.ids[0]
        self.queue.schedule(cid, -1)
        self.queue.redis.lpush(self.queue.READY, cid)
        self.queue.recover(cid)
        self.assertIsNone(self.queue.redis.zscore(self.queue.DELAYED, cid))
        self.assertEqual(self.queue.promote_due(), 0)
        self.assertEqual(self.queue.redis.llen(self.queue.READY), 1)

    def test_concurrent_promotions_queue_each_due_task_once(self):
        from concurrent.futures import ThreadPoolExecutor
        for cid in self.ids:
            self.queue.schedule(cid, -1)
        with ThreadPoolExecutor(4) as workers:
            counts = list(workers.map(lambda _: self.queue.promote_due(), range(4)))
        self.assertEqual(sum(counts), len(self.ids))
        self.assertEqual(set(self.queue.redis.lrange(self.queue.READY, 0, -1)), set(self.ids))
        self.assertEqual(self.queue.redis.llen(self.queue.READY), len(self.ids))

    def test_deduplication_preserves_oldest_first_order(self):
        a, b, c = self.ids
        self.queue.redis.rpush(self.queue.READY, a, b, c, b, a)
        self.assertEqual(self.queue.deduplicate_ready(), 2)
        self.assertEqual([self.queue.pop(1) for _ in range(3)], [a, b, c])
        self.assertEqual(self.queue.deduplicate_ready(), 0)
