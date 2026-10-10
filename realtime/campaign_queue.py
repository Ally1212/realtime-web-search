from __future__ import annotations

import time

from redis import Redis


class CampaignQueue:
    READY = "realtime:campaigns:ready"
    DELAYED = "realtime:campaigns:delayed"

    def __init__(self, url: str):
        # Blocking pops wait up to five seconds, so the socket timeout must be longer.
        self.redis = Redis.from_url(url, decode_responses=True, socket_timeout=30)

    def enqueue(self, campaign_id: str, *, priority: bool = False) -> None:
        marker = f"realtime:campaign:queued:{campaign_id}"
        if self.redis.set(marker, "1", nx=True, ex=3600):
            if priority:
                self.redis.rpush(self.READY, campaign_id)
            else:
                self.redis.lpush(self.READY, campaign_id)

    def recover(self, campaign_id: str) -> None:
        """Recover interrupted work without duplicating ready work or bypassing delays."""
        self.redis.eval("""
            local due = redis.call('ZSCORE', KEYS[2], ARGV[1])
            if due and tonumber(due) > tonumber(ARGV[2]) then
                redis.call('LREM', KEYS[1], 0, ARGV[1])
                redis.call('DEL', KEYS[3])
                return 0
            end
            redis.call('ZREM', KEYS[2], ARGV[1])
            if redis.call('LPOS', KEYS[1], ARGV[1]) then
                redis.call('SET', KEYS[3], '1', 'EX', 3600)
                return 0
            end
            redis.call('SET', KEYS[3], '1', 'EX', 3600)
            redis.call('LPUSH', KEYS[1], ARGV[1])
            return 1
        """, 3, self.READY, self.DELAYED,
            f"realtime:campaign:queued:{campaign_id}", campaign_id, time.time())

    def deduplicate_ready(self) -> int:
        """Repair old queue duplication while preserving oldest-first pop order."""
        return int(self.redis.eval("""
            local rows = redis.call('LRANGE', KEYS[1], 0, -1)
            local seen, keep = {}, {}
            for i = #rows, 1, -1 do
                if not seen[rows[i]] then
                    seen[rows[i]] = true
                    keep[#keep + 1] = rows[i]
                end
            end
            if #rows ~= #keep then
                redis.call('DEL', KEYS[1])
                for i = #keep, 1, -1 do redis.call('RPUSH', KEYS[1], keep[i]) end
            end
            return #rows - #keep
        """, 1, self.READY))

    def schedule(self, campaign_id: str, delay_seconds: int = 300) -> None:
        self.redis.zadd(self.DELAYED, {campaign_id: time.time() + delay_seconds})

    def promote_due(self) -> int:
        # Several worker threads promote concurrently; claim and enqueue in a
        # single Redis operation so a stale due list cannot queue a task twice.
        return int(self.redis.eval("""
            local due = redis.call('ZRANGEBYSCORE', KEYS[2], 0, ARGV[1], 'LIMIT', 0, 100)
            local promoted = 0
            for _, cid in ipairs(due) do
                redis.call('ZREM', KEYS[2], cid)
                if not redis.call('LPOS', KEYS[1], cid) then
                    redis.call('LPUSH', KEYS[1], cid)
                    promoted = promoted + 1
                end
                redis.call('SET', ARGV[2] .. cid, '1', 'EX', 3600)
            end
            return promoted
        """, 2, self.READY, self.DELAYED, time.time(), "realtime:campaign:queued:"))

    def pop(self, timeout: int = 5) -> str | None:
        result = self.redis.brpop(self.READY, timeout=timeout)
        if not result:
            return None
        campaign_id = result[1]
        self.redis.delete(f"realtime:campaign:queued:{campaign_id}")
        return campaign_id
