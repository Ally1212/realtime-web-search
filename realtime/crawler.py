from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

import scrapy
from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import IgnoreRequest
from twisted.internet.task import LoopingCall

from .campaign_store import CampaignStore, PageRecord
from .config import Config
from .discovery import SearchDiscovery
from .fetcher import detect_language, extract_text, is_public_url, normalize_url, relevant_to
from .proxy_pool import ProxyPool
from .search import SearchIndex
from .whale_collector import whale_message


SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3",
    ".zip", ".gz", ".rar", ".7z", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
)


def _authorized(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


class ScopedRobotsTxtMiddleware(RobotsTxtMiddleware):
    def process_request(self, request: scrapy.Request, spider: scrapy.Spider):  # type: ignore[no-untyped-def]
        domains = getattr(spider, "robots_bypass_domains", ())
        if _authorized(urlsplit(request.url).hostname or "", domains):
            return None
        return super().process_request(request, spider)


class ProxyDownloaderMiddleware:
    def __init__(self, config: Config):
        self.config = config
        self.pool = ProxyPool(config)

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler) -> "ProxyDownloaderMiddleware":
        return cls(Config())

    def process_request(self, request: scrapy.Request, spider: scrapy.Spider) -> None:
        profile = str(request.meta.get("proxy_profile") or getattr(spider, "proxy_profile", "direct"))
        if profile == "direct" or request.meta.get("dont_proxy"):
            return
        domain = urlsplit(request.url).hostname or ""
        selected = self.pool.choose(profile, domain)
        if selected is None:
            raise IgnoreRequest(f"{profile} proxy pool unavailable")
        proxy_url, proxy_key = selected
        request.meta["proxy"] = proxy_url
        request.meta["proxy_key"] = proxy_key

    def process_response(
        self, request: scrapy.Request, response: scrapy.http.Response, spider: scrapy.Spider
    ) -> scrapy.http.Response:
        key = request.meta.get("proxy_key")
        if key:
            self.pool.report(str(key), urlsplit(request.url).hostname or "", response.status)
        # Never allow credential-bearing proxy URLs to enter retry/redirect disk queues.
        request.meta.pop("proxy", None)
        return response

    def process_exception(self, request: scrapy.Request, exception: BaseException, spider: scrapy.Spider) -> None:
        key = request.meta.get("proxy_key")
        if key:
            self.pool.report(str(key), urlsplit(request.url).hostname or "", failed=True)
        request.meta.pop("proxy", None)


class PagePipeline:
    def __init__(self, config: Config):
        self.config = config
        self.store = CampaignStore(config.database_url)
        self.index = SearchIndex(config.opensearch_url, config.index_name, config.request_timeout)
        self.buffer: list[dict[str, object]] = []
        self.flush_loop: LoopingCall | None = None

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler) -> "PagePipeline":
        return cls(Config())

    def open_spider(self, spider: scrapy.Spider) -> None:
        self.index.ensure_index()
        self.flush_loop = LoopingCall(self.flush)
        self.flush_loop.start(15, now=False)

    def process_item(self, item: dict[str, object], spider: scrapy.Spider) -> dict[str, object]:
        campaign_id = str(item["campaign_id"])
        page = PageRecord(
            url=str(item["url"]),
            content_hash=str(item["content_hash"]),
            title=str(item["title"]),
            summary=str(item["summary"]),
            content=str(item["content"]),
            language=str(item["language"]),
            http_status=int(item["http_status"]),
            fetched_at=str(item["fetched_at"]),
            source_engines=tuple(item.get("source_engines") or ()),
        )
        _, inserted, duplicate_content, needs_indexing = self.store.record_page(campaign_id, page)
        whale_task = self.store.whale_task_for_campaign(campaign_id)
        if whale_task:
            record_key, message = whale_message(item, whale_task, self.config)
            self.store.queue_whale_message(str(whale_task["task_id"]), record_key, message)
        if needs_indexing:
            self.buffer.append(item)
        if inserted:
            setattr(spider, "accepted", int(getattr(spider, "accepted", 0)) + 1)
            target = int(getattr(spider, "daily_target", 50_000))
            accepted = int(getattr(spider, "accepted", 0))
            starting = int(getattr(spider, "starting_daily_count", 0))
            if starting + accepted >= target and not getattr(spider, "closing_for_target", False):
                setattr(spider, "closing_for_target", True)
                asyncio.get_running_loop().create_task(
                    spider.crawler.engine.close_spider_async(reason="daily_target_reached")
                )
        else:
            increment = getattr(spider, "_increment", None)
            if increment:
                increment(duplicates=1)
            else:
                self.store.increment(campaign_id, duplicates=1)
        if duplicate_content and not inserted:
            spider.crawler.stats.inc_value("content_duplicates")
        if len(self.buffer) >= 20:
            self.flush()
        return item

    def flush(self) -> None:
        if not self.buffer:
            return
        batch = self.buffer
        self.buffer = []
        indexed, errors = self.index.bulk_index(batch)
        if errors:
            self.buffer = batch + self.buffer
            raise RuntimeError(f"OpenSearch bulk indexing failed for {len(errors)} documents")
        self.store.mark_indexed([str(item["content_hash"]) for item in batch])

    def close_spider(self, spider: scrapy.Spider) -> None:
        if self.flush_loop and self.flush_loop.running:
            self.flush_loop.stop()
        self.flush()
        self.index.refresh()


class FocusedSpider(scrapy.Spider):
    name = "focused"
    robots_bypass_domains: tuple[str, ...] = ()

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0.1,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 3.0,
        "RETRY_TIMES": 3,
        "RETRY_HTTP_CODES": [403, 408, 425, 429, 500, 502, 503, 504],
        "DOWNLOAD_MAXSIZE": 5_000_000,
        "DEPTH_LIMIT": 12,
        "LOG_LEVEL": "INFO",
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": False,
        "ITEM_PIPELINES": {"realtime.crawler.PagePipeline": 300},
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
            "realtime.crawler.ScopedRobotsTxtMiddleware": 100,
            "realtime.crawler.ProxyDownloaderMiddleware": 760,
            "scrapy_curl_cffi.middlewares.CurlCffiMiddleware": 380,
            "scrapy_curl_cffi.middlewares.DefaultHeadersMiddleware": 400,
            "scrapy_curl_cffi.middlewares.UserAgentMiddleware": 500,
            "scrapy.downloadermiddlewares.defaultheaders.DefaultHeadersMiddleware": None,
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
        },
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_curl_cffi.handlers.CurlCffiDownloadHandler",
            "https": "scrapy_curl_cffi.handlers.CurlCffiDownloadHandler",
        },
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "CURL_CFFI_OPTIONS": {"impersonate": "chrome", "verify": True},
    }

    def __init__(self, campaign_id: str, *args: object, **kwargs: object):
        super().__init__(*args, **kwargs)
        self.config = Config()
        self.store = CampaignStore(self.config.database_url)
        campaign = self.store.campaign(campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        self.campaign_id = campaign_id
        self.query = str(campaign["query"])
        self.terms = tuple(dict.fromkeys((self.query, *campaign["aliases"])))
        self.daily_target = int(campaign["daily_target"])
        self.proxy_profile = str(campaign["proxy_profile"])
        self.robots_bypass_domains = self.config.robots_bypass_domains
        self.accepted = 0
        self.starting_daily_count = self.store.daily_count(campaign_id)
        self.pending_accepts = 0
        self.closing_for_target = False
        self._pending_counters: dict[str, int] = {}
        self._counter_loop: LoopingCall | None = None

    def _increment(self, **values: int) -> None:
        for field, value in values.items():
            self._pending_counters[field] = self._pending_counters.get(field, 0) + value
        if sum(self._pending_counters.values()) >= 100:
            self._flush_counters()

    def _flush_counters(self) -> None:
        if not self._pending_counters:
            return
        values = self._pending_counters
        self._pending_counters = {}
        try:
            self.store.increment(self.campaign_id, **values)
        except Exception as exc:
            for field, value in values.items():
                self._pending_counters[field] = self._pending_counters.get(field, 0) + value
            self.logger.error("campaign counter flush failed: %s", type(exc).__name__)

    def closed(self, reason: str) -> None:
        if self._counter_loop and self._counter_loop.running:
            self._counter_loop.stop()
        self._flush_counters()

    async def start(self):  # type: ignore[no-untyped-def]
        self._counter_loop = LoopingCall(self._flush_counters)
        self._counter_loop.start(2, now=False)
        whale_task = self.store.whale_task_for_campaign(self.campaign_id)
        if whale_task and whale_task["task_type"] == "content_detail":
            payload = dict(whale_task.get("payload") or {})
            for raw_url in payload.get("urls") or []:
                try:
                    url = normalize_url(str(raw_url))
                except Exception:
                    continue
                if is_public_url(url):
                    yield self._page_request(url, ("whale-content-detail",))
            return
        discovery = SearchDiscovery(
            self.config.searxng_url,
            self.config.request_timeout,
            feeds=self.config.discovery_feeds,
        )
        results, errors = discovery.discover_many(self.terms, self.config.discovery_pages)
        self._increment(discovered=len(results))
        for error in errors:
            self.store.record_event(self.campaign_id, "", "discovery_failed", error_code=error[:120])
        candidates: list[tuple[str, tuple[str, ...]]] = []
        for result in results:
            try:
                url = normalize_url(result.url)
            except Exception:
                continue
            candidates.append((url, tuple(result.engines)))
        candidate_urls = list(dict.fromkeys(url for url, _ in candidates))
        processed = self.store.processed_urls(self.campaign_id, candidate_urls)
        if processed:
            self._increment(duplicates=len(processed))
            self.store.record_event(
                self.campaign_id, "", "skipped", error_code=f"already_processed:{len(processed)}"
            )
        for offset in range(0, len(candidates), 64):
            batch = candidates[offset:offset + 64]
            public = await asyncio.gather(*(
                asyncio.to_thread(is_public_url, url) for url, _ in batch
            ))
            for (url, engines), allowed in zip(batch, public):
                if not allowed or url in processed:
                    continue
                yield self._page_request(url, engines)
                # Keep Whale google_search tasks restricted to Google-discovered URLs.

    def _page_request(self, url: str, engines: tuple[str, ...] = ()) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_page,
            errback=self.on_error,
            meta={"source_engines": engines, "proxy_profile": self.proxy_profile},
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
        )

    def parse_page(self, response: scrapy.http.Response):  # type: ignore[no-untyped-def]
        self._increment(fetched=1)
        content_type = response.headers.get(b"Content-Type", b"").decode(errors="ignore").lower()
        if "html" not in content_type:
            self._increment(failed=1)
            return
        title, content = extract_text(
            response.body, response.url, self.config.trafilatura_enabled
        )
        if len(content) < 100:
            self._increment(failed=1)
            self.store.record_event(self.campaign_id, response.url, "failed", response.status, "short_content")
            return
        language = detect_language(content)
        relevant = relevant_to(content, title, self.terms)
        if relevant and language in {"zh", "en"}:
            if self.starting_daily_count + self.pending_accepts >= self.daily_target:
                return
            self.pending_accepts += 1
            normalized = normalize_url(response.url)
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            yield {
                "document_id": content_hash,
                "campaign_id": self.campaign_id,
                "url": normalized,
                "title": title[:300],
                "content": content,
                "summary": content[:500],
                "query": self.query,
                "source_engines": tuple(response.meta.get("source_engines") or ()),
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "http_status": response.status,
                "content_hash": content_hash,
                "language": language,
            }
            if self.starting_daily_count + self.pending_accepts >= self.daily_target:
                return
        else:
            self._increment(irrelevant=1)

        return

    def on_error(self, failure):  # type: ignore[no-untyped-def]
        request = failure.request
        self._increment(failed=1)
        self.store.record_event(
            self.campaign_id, request.url, "failed", error_code=type(failure.value).__name__
        )

    def ignore_error(self, failure):  # type: ignore[no-untyped-def]
        return None
