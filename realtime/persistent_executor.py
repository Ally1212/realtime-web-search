from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class CrawlHandle:
    """Small Popen-compatible facade for a crawl running in the shared reactor."""

    def __init__(self, executor: "PersistentCrawlExecutor", campaign_id: str):
        self.executor = executor
        self.campaign_id = campaign_id
        self.returncode: int | None = None
        self._done = threading.Event()
        self._crawler: Any = None

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise TimeoutError(f"crawl {self.campaign_id} did not stop in time")
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.executor.terminate(self)

    def _finish(self, returncode: int) -> None:
        self.returncode = returncode
        self._done.set()


class PersistentCrawlExecutor:
    """Runs all campaigns in one long-lived Twisted reactor and OS process."""

    def __init__(self, config: Any):
        self.config = config
        self._ready = threading.Event()
        self._handles: set[CrawlHandle] = set()
        self._thread = threading.Thread(target=self._run_reactor, name="scrapy-reactor", daemon=True)
        self._thread.start()
        if not self._ready.wait(20):
            raise RuntimeError("persistent Scrapy reactor failed to start")

    def _run_reactor(self) -> None:
        from twisted.internet.asyncioreactor import install

        try:
            install()
        except Exception as exc:
            if type(exc).__name__ != "ReactorAlreadyInstalledError":
                raise
        from scrapy.crawler import CrawlerRunner
        from twisted.internet import reactor

        from .scrapy_runner import crawler_settings, repair_jobdir

        self._reactor = reactor
        self._CrawlerRunner = CrawlerRunner
        self._crawler_settings = crawler_settings
        self._repair_jobdir = repair_jobdir
        self._ready.set()
        reactor.run(installSignalHandlers=False)

    def start(self, campaign_id: str) -> CrawlHandle:
        handle = CrawlHandle(self, campaign_id)
        self._handles.add(handle)

        def schedule() -> None:
            from .crawler import FocusedSpider

            job_dir = Path("state/jobs-v2") / campaign_id
            self._repair_jobdir(job_dir)
            settings = self._crawler_settings(self.config, job_dir)
            per_crawl = max(
                1, self.config.crawler_concurrency // max(self.config.persistent_max_crawls, 1)
            )
            settings.set("CONCURRENT_REQUESTS", per_crawl, priority="cmdline")
            runner = self._CrawlerRunner(settings)
            crawler = runner.create_crawler(FocusedSpider)
            handle._crawler = crawler
            deferred = runner.crawl(crawler, campaign_id=campaign_id)

            def succeeded(result: Any) -> Any:
                handle._finish(0)
                self._handles.discard(handle)
                return result

            def failed(failure: Any) -> Any:
                handle._finish(1)
                self._handles.discard(handle)
                return None

            deferred.addCallbacks(succeeded, failed)

        self._reactor.callFromThread(schedule)
        return handle

    def terminate(self, handle: CrawlHandle) -> None:
        def close() -> None:
            crawler = handle._crawler
            if crawler is None:
                handle._finish(0)
                return
            engine = getattr(crawler, "engine", None)
            spider = getattr(crawler, "spider", None)
            if engine is not None and spider is not None:
                engine.close_spider(spider, reason="executor_terminated")

        if not handle._done.is_set():
            self._reactor.callFromThread(close)


_EXECUTORS: dict[int, PersistentCrawlExecutor] = {}
_EXECUTORS_LOCK = threading.Lock()


def shared_executor(config: Any) -> PersistentCrawlExecutor:
    key = id(config)
    with _EXECUTORS_LOCK:
        executor = _EXECUTORS.get(key)
        if executor is None:
            executor = PersistentCrawlExecutor(config)
            _EXECUTORS[key] = executor
        return executor
