"""Run against disposable keys in a real Redis/Valkey instance."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from redis.exceptions import ResponseError

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

    def test_retry_keeps_the_original_schedule_if_its_response_was_lost(self):
        scheduled, unscheduled, _ = self.ids
        self.queue.schedule(scheduled, 600)
        original_due = self.queue.redis.zscore(self.queue.DELAYED, scheduled)
        self.queue.schedule(scheduled, 60, only_if_unscheduled=True)
        self.queue.schedule(unscheduled, 60, only_if_unscheduled=True)
        self.assertEqual(self.queue.redis.zscore(self.queue.DELAYED, scheduled), original_due)
        self.assertIsNotNone(self.queue.redis.zscore(self.queue.DELAYED, unscheduled))

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

    def test_failed_enqueue_can_be_retried_without_an_orphaned_marker(self):
        cid=self.ids[0]
        self.queue.redis.set(self.queue.READY,"fixture incompatible key type")
        with self.assertRaises(ResponseError):
            self.queue.enqueue(cid)
        self.queue.redis.delete(self.queue.READY)
        self.queue.enqueue(cid)
        self.assertEqual(self.queue.redis.lrange(self.queue.READY,0,-1),[cid])

    def test_concurrent_enqueue_queues_once_and_preserves_priority(self):
        from concurrent.futures import ThreadPoolExecutor
        older,normal,priority=self.ids
        self.queue.enqueue(older)
        with ThreadPoolExecutor(8) as workers:
            list(workers.map(self.queue.enqueue,[normal]*16))
        self.queue.enqueue(priority,priority=True)
        self.assertEqual([self.queue.pop(1) for _ in range(3)],[priority,older,normal])

    def test_existing_ready_campaign_can_be_promoted_without_duplicates(self):
        older, queued, _ = self.ids
        self.queue.enqueue(older)
        self.queue.enqueue(queued)
        self.queue.enqueue(queued, priority=True)
        self.queue.enqueue(queued)
        self.assertEqual([self.queue.pop(1), self.queue.pop(1)], [queued, older])
        self.assertEqual(self.queue.redis.llen(self.queue.READY), 0)

    def test_priority_retry_recovers_stale_marker_after_failed_cleanup(self):
        cid = self.ids[0]
        marker = f'realtime:campaign:queued:{cid}'
        self.queue.redis.set(marker, '1', ex=3600)
        self.queue.enqueue(cid, priority=True)
        self.assertEqual(self.queue.redis.lrange(self.queue.READY, 0, -1), [cid])

    def test_delayed_campaign_wakes_worker_already_blocked_on_empty_ready_queue(self):
        from realtime.worker import WorkerManager

        waiting=threading.Event()
        collected=threading.Event()
        manager=object.__new__(WorkerManager)
        manager.queue=self.queue
        manager.stop=threading.Event()
        manager.config=SimpleNamespace(crawler_slots=1, whale_serp_enabled=False)
        manager.store=Mock()
        manager.store.active_local_campaign_ids.return_value=[]
        manager.proxy_sync_loop=Mock()
        manager.supply_loop=Mock()
        original_pop=self.queue.pop

        def pop(*args,**kwargs):
            waiting.set()
            return original_pop(*args,**kwargs)

        self.queue.pop=pop
        manager.run_campaign=lambda cid: collected.set() if cid==self.ids[0] else None
        runner=threading.Thread(target=manager.run,daemon=True)
        runner.start()
        try:
            self.assertTrue(waiting.wait(2))
            self.queue.schedule(self.ids[0],0.1)
            self.assertTrue(collected.wait(2),'an idle worker missed the delayed task until BRPOP timed out')
        finally:
            manager.stop.set()
            self.queue.enqueue(self.ids[1])
            runner.join(6)
        self.assertFalse(runner.is_alive())

    def test_deduplication_preserves_oldest_first_order(self):
        a, b, c = self.ids
        self.queue.redis.rpush(self.queue.READY, a, b, c, b, a)
        self.assertEqual(self.queue.deduplicate_ready(), 2)
        self.assertEqual([self.queue.pop(1) for _ in range(3)], [a, b, c])
        self.assertEqual(self.queue.deduplicate_ready(), 0)
