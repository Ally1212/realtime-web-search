from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import threading
import time

from .campaign_queue import CampaignQueue
from .campaign_store import CampaignStore
from .config import Config
from .proxy_pool import ProxyApiError, ProxySynchronizer


class WorkerManager:
    def __init__(self, config: Config):
        self.config = config
        self.store = CampaignStore(config.database_url)
        self.queue = CampaignQueue(config.valkey_url)
        self.syncer = ProxySynchronizer(config)
        self.stop = threading.Event()

    def proxy_sync_loop(self) -> None:
        last_purge = 0.0
        while not self.stop.is_set():
            retry = self.config.proxy_sync_seconds
            profiles = self.store.local_proxy_profiles() - {"direct"}
            for profile in profiles:
                try:
                    self.syncer.sync(profile)
                except ProxyApiError as exc:
                    retry = min(retry, int(exc.retry_after or 60))
                    print(f"proxy sync failed profile={profile} status={exc.status or 'transport'}", flush=True)
            if time.monotonic() - last_purge >= 86400:
                self.store.purge_old_events(30)
                last_purge = time.monotonic()
            self.stop.wait(max(5, retry))

    def run_campaign(self, campaign_id: str) -> None:
        campaign = self.store.campaign(campaign_id)
        if (
            not campaign
            or campaign["status"] != "active"
            or not self.store.is_local_campaign(campaign_id)
        ):
            return
        profile = str(campaign["proxy_profile"])
        if profile != "direct":
            try:
                count = self.syncer.sync(profile)
            except ProxyApiError as exc:
                self.store.record_event(
                    campaign_id, "", "proxy_unavailable", error_code=f"http_{exc.status or 'transport'}"
                )
                self.queue.schedule(campaign_id, int(exc.retry_after or 60))
                return
            if count == 0:
                self.store.record_event(campaign_id, "", "proxy_unavailable", error_code="empty_pool")
                self.queue.schedule(campaign_id, 300)
                return
        result = subprocess.run(
            [sys.executable, "-m", "realtime.scrapy_runner", campaign_id],
            check=False,
        )
        campaign = self.store.campaign(campaign_id)
        if campaign and campaign["status"] == "active":
            if result.returncode:
                self.store.record_event(
                    campaign_id, "", "worker_failed", error_code=f"exit_{result.returncode}"
                )
                self.queue.schedule(campaign_id, 60)
            else:
                frontier_delay = self.store.next_google_frontier_delay(campaign_id)
                self.queue.schedule(campaign_id, frontier_delay if frontier_delay is not None else 300)

    def worker_loop(self) -> None:
        while not self.stop.is_set():
            self.queue.promote_due()
            campaign_id = self.queue.pop(timeout=5)
            if campaign_id:
                self.run_campaign(campaign_id)

    def run(self) -> None:
        for campaign_id in self.store.active_local_campaign_ids():
            self.queue.recover(campaign_id)
        threading.Thread(target=self.proxy_sync_loop, daemon=True).start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.crawler_slots) as pool:
            futures = [pool.submit(self.worker_loop) for _ in range(self.config.crawler_slots)]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                self.stop.set()


def run_worker() -> None:
    WorkerManager(Config()).run()
