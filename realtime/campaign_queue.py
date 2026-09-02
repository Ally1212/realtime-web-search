from __future__ import annotations

import time

from redis import Redis


class CampaignQueue:
    READY = "realtime:campaigns:ready"
    DELAYED = "realtime:campaigns:delayed"

    def __init__(self, url: str):
        # Blocking pops wait up to five seconds, so the socket timeout must be longer.
        self.redis = Redis.from_url(url, decode_responses=True, socket_timeout=30)

    def enqueue(self, campaign_id: str) -> None:
        marker = f"realtime:campaign:queued:{campaign_id}"
        if self.redis.set(marker, "1", nx=True, ex=3600):
            self.redis.lpush(self.READY, campaign_id)

    def recover(self, campaign_id: str) -> None:
        """Requeue work interrupted when the single worker process stopped."""
        with self.redis.pipeline() as pipe:
            pipe.delete(f"realtime:campaign:queued:{campaign_id}")
            pipe.zrem(self.DELAYED, campaign_id)
            pipe.execute()
        self.enqueue(campaign_id)

    def schedule(self, campaign_id: str, delay_seconds: int = 300) -> None:
        self.redis.zadd(self.DELAYED, {campaign_id: time.time() + delay_seconds})

    def promote_due(self) -> int:
        due = self.redis.zrangebyscore(self.DELAYED, 0, time.time(), start=0, num=100)
        if not due:
            return 0
        with self.redis.pipeline() as pipe:
            for campaign_id in due:
                pipe.zrem(self.DELAYED, campaign_id)
                pipe.delete(f"realtime:campaign:queued:{campaign_id}")
            pipe.execute()
        for campaign_id in due:
            self.enqueue(campaign_id)
        return len(due)

    def pop(self, timeout: int = 5) -> str | None:
        result = self.redis.brpop(self.READY, timeout=timeout)
        if not result:
            return None
        campaign_id = result[1]
        self.redis.delete(f"realtime:campaign:queued:{campaign_id}")
        return campaign_id
