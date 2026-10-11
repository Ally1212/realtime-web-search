from __future__ import annotations

import concurrent.futures
import os
import signal
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
        self.store = CampaignStore(config.database_url, initialize=False)
        from .serp_store import SerpStore
        self.serp = SerpStore(config.database_url)
        self.serp.initialize(config.content_fetch_enabled)
        from .serp_console import ConsoleStore
        self.console = ConsoleStore(config.database_url)
        self.console.initialize_console(config)
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
        config = self.config
        campaign = self.store.campaign(campaign_id)
        if (
            not campaign
            or campaign["status"] != "active"
            or not self.store.is_local_campaign(campaign_id)
        ):
            return
        if hasattr(self, 'console'):
            if not self.console.runnable(campaign_id):
                # Only active automatic tasks need to wait for the global switch.
                # Finished/paused tasks must not live forever in the delayed queue.
                self.queue.schedule(campaign_id, 30)
                return
            config = self.console.effective_config(self.config)
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
        returncode = 0
        if not self.serp.content_fetch_enabled(self.config.content_fetch_enabled):
            from .serp_worker import run_serp_campaign
            run_serp_campaign(campaign_id, config, self.store, self.serp)
        else:
            # The child gets the effective runtime switch, not a stale env default.
            child_env = dict(os.environ, CONTENT_FETCH_ENABLED='true')
            child = subprocess.Popen([sys.executable, "-m", "realtime.scrapy_runner", campaign_id], env=child_env, start_new_session=True)
            try:
                while child.poll() is None:
                    if self.stop.wait(1) or not self.serp.content_fetch_enabled() or (hasattr(self, 'console') and not self.console.runnable(campaign_id)):
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        break
                returncode = child.returncode or 0
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        campaign = self.store.campaign(campaign_id)
        if campaign and campaign["status"] == "active":
            if returncode:
                self.store.record_event(
                    campaign_id, "", "worker_failed", error_code=f"exit_{returncode}"
                )
                self.queue.schedule(campaign_id, 60)
            else:
                frontier_delay = self.store.next_google_frontier_delay(campaign_id)
                delay = frontier_delay if frontier_delay is not None else 300
                if 'google_news' in (campaign.get('sources') or []):
                    delay = min(delay,config.google_news_base_interval_seconds)
                if 'google_trends' in (campaign.get('sources') or []):
                    delay = min(delay,config.google_trends_interval_seconds)
                self.queue.schedule(campaign_id,delay)

    def worker_loop(self) -> None:
        while not self.stop.is_set():
            self.queue.promote_due()
            campaign_id = self.queue.pop(timeout=5)
            if campaign_id:
                try:
                    self.run_campaign(campaign_id)
                except Exception as exc:
                    print(f"campaign retry after {type(exc).__name__}", flush=True)
                    self.queue.schedule(campaign_id, 60)

    def supply_loop(self) -> None:
        from .serp_worker import SerpSupply
        supply = SerpSupply(self.config, self.store, self.queue)
        while not self.stop.is_set():
            try:
                supply.tick()
            except Exception as exc:
                print(f"SERP keyword supply retry after {type(exc).__name__}", flush=True)
            self.stop.wait(30)

    def run(self) -> None:
        removed = self.queue.deduplicate_ready()
        if removed:
            print(f"removed duplicate ready entries={removed}", flush=True)
        for campaign_id in self.store.active_local_campaign_ids():
            self.queue.recover(campaign_id)
        threading.Thread(target=self.proxy_sync_loop, daemon=True).start()
        # The website can enable supply even when the startup environment was off.
        threading.Thread(target=self.supply_loop, daemon=True).start()
        if self.config.whale_serp_enabled:
            from .whale_serp import WhaleSerpStore, WhaleSerpRuntime
            whale = WhaleSerpStore(self.config.database_url)
            whale.initialize()
            threading.Thread(target=WhaleSerpRuntime(self.config, whale).run, args=(self.stop,), daemon=True).start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.crawler_slots) as pool:
            futures = [pool.submit(self.worker_loop) for _ in range(self.config.crawler_slots)]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                self.stop.set()


def run_worker() -> None:
    WorkerManager(Config()).run()
