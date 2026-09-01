from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from .campaign_queue import CampaignQueue
from .campaign_store import CampaignStore
from .config import Config


def run_benchmark(query: str, hours: float, profile: str, target: int) -> int:
    config = Config()
    store = CampaignStore(config.database_url)
    queue = CampaignQueue(config.valkey_url)
    campaign_id = store.create_campaign(query, [], target, profile)
    queue.enqueue(campaign_id)
    started = time.monotonic()
    deadline = started + hours * 3600
    print(json.dumps({"event": "started", "campaign_id": campaign_id, "at": datetime.now(timezone.utc).isoformat()}))
    while time.monotonic() < deadline:
        campaign = store.campaign(campaign_id)
        today = store.daily_count(campaign_id)
        elapsed = max(time.monotonic() - started, 1)
        print(json.dumps({
            "event": "sample",
            "at": datetime.now(timezone.utc).isoformat(),
            "today": today,
            "rate_per_second": round(today / elapsed, 4),
            "projected_daily": round(today / elapsed * 86400),
            "fetched": int(campaign["fetched"]),
            "failed": int(campaign["failed"]),
            "duplicates": int(campaign["duplicates"]),
            "irrelevant": int(campaign["irrelevant"]),
        }), flush=True)
        if today >= target:
            return 0
        time.sleep(min(60, max(1, deadline - time.monotonic())))
    return 0 if store.daily_count(campaign_id) >= target else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real campaign throughput benchmark")
    parser.add_argument("--query", required=True)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--profile", choices=("private", "public", "direct"), default="private")
    parser.add_argument("--target", type=int, default=50_000)
    args = parser.parse_args()
    raise SystemExit(run_benchmark(args.query, args.hours, args.profile, args.target))


if __name__ == "__main__":
    main()
