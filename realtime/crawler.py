from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import scrapy
from scrapy import signals
from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import IgnoreRequest
from twisted.internet.task import LoopingCall

from .campaign_store import CampaignStore, PageRecord
from .config import Config
from .discovery import SearchDiscovery
from .fetcher import detect_language, extract_text, is_public_url, normalize_url, relevant_to
from .keyword_catalog import AI_ANCHORS
from .proxy_pool import ProxyPool
from .whale_collector import whale_message


SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3",
    ".zip", ".gz", ".rar", ".7z", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
)


def is_javascript_shell(raw: bytes) -> bool:
    sample = raw[:500_000].lower()
    markers = (
        b"enable javascript", b"javascript is required", b'id="__next"',
        b'id="root"', b'id="app"', b"__next_data__", b"window.__nuxt__",
    )
    return any(marker in sample for marker in markers) and sample.count(b"<script") >= 1


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
        self.store = CampaignStore(config.database_url)
        self._usage: dict[tuple[str, str], int] = {}
        self._last_usage_flush = time.monotonic()

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler) -> "ProxyDownloaderMiddleware":
        middleware = cls(Config())
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    def _flush_usage(self) -> None:
        if not self._usage:
            return
        rows = [(key, profile, count) for (profile, key), count in self._usage.items()]
        self.store.record_proxy_usage(rows)
        self._usage = {}
        self._last_usage_flush = time.monotonic()

    def spider_closed(self, spider: scrapy.Spider, reason: str) -> None:
        self._flush_usage()

    def process_request(self, request: scrapy.Request, spider: scrapy.Spider) -> None:
        profile = str(request.meta.get("proxy_profile") or getattr(spider, "proxy_profile", "direct"))
        if profile == "direct" or request.meta.get("dont_proxy"):
            return
        domain = urlsplit(request.url).hostname or ""
        selected = self.pool.choose(profile, domain)
        if selected is None:
            raise IgnoreRequest(f"{profile} proxy pool unavailable")
        proxy_url, proxy_key = selected
        usage_key = (profile, hashlib.sha256(proxy_key.encode()).hexdigest())
        self._usage[usage_key] = self._usage.get(usage_key, 0) + 1
        if sum(self._usage.values()) >= 500 or time.monotonic() - self._last_usage_flush >= 30:
            self._flush_usage()
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

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler) -> "PagePipeline":
        return cls(Config())

    def open_spider(self, spider: scrapy.Spider) -> None:
        return None

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
        whale_task = self.store.whale_task_for_campaign(campaign_id)
        record_key = None
        message = None
        if whale_task:
            record_key, message = whale_message(item, whale_task, self.config)
        started = time.monotonic()
        _, inserted, duplicate_content, _ = self.store.record_page(
            campaign_id,
            page,
            whale_task_id=str(whale_task["task_id"]) if whale_task else None,
            source_record_key=record_key,
            whale_payload=message,
        )
        observe = getattr(spider, "_observe_stage", None)
        if observe:
            observe("postgres_store", time.monotonic() - started)
        if inserted:
            setattr(spider, "accepted", int(getattr(spider, "accepted", 0)) + 1)
            target = int(getattr(spider, "daily_target", 50_000))
            accepted = int(getattr(spider, "accepted", 0))
            starting = int(getattr(spider, "starting_daily_count", 0))
            if (
                target > 0
                and starting + accepted >= target
                and not getattr(spider, "closing_for_target", False)
            ):
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
        return item

    def close_spider(self, spider: scrapy.Spider) -> None:
        return None


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
            "http": "realtime.hybrid_handler.HybridDownloadHandler",
            "https": "realtime.hybrid_handler.HybridDownloadHandler",
        },
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "CURL_CFFI_OPTIONS": {"impersonate": "chrome", "verify": True},
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_ABORT_REQUEST": "realtime.hybrid_handler.abort_browser_resource",
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
        self.whale_task = self.store.whale_task_for_campaign(campaign_id)
        task_payload = dict((self.whale_task or {}).get("payload") or {})
        aliases = tuple(campaign["aliases"])
        self.terms = tuple(dict.fromkeys(
            aliases if task_payload.get("continuous") and aliases else (self.query, *aliases)
        ))
        inferred_language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in self.query) else "en"
        self.search_language = str(task_payload.get("language") or inferred_language)
        self.keyword_kind = str(task_payload.get("kind") or "base")
        # Zero means unlimited for a locally-created campaign. Whale tasks keep
        # their explicit platform-provided limit.
        self.daily_target = int(campaign["daily_target"]) if self.whale_task else 0
        self.proxy_profile = str(campaign["proxy_profile"])
        self.robots_bypass_domains = self.config.robots_bypass_domains
        self.accepted = 0
        self.starting_daily_count = self.store.daily_count(campaign_id)
        self.pending_accepts = 0
        self.closing_for_target = False
        self._pending_counters: dict[str, int] = {}
        self._counter_loop: LoopingCall | None = None
        self._pending_stages: dict[str, tuple[float, int]] = {}
        self.page_responses = 0
        self.browser_requests = 0

    def _increment(self, **values: int) -> None:
        for field, value in values.items():
            self._pending_counters[field] = self._pending_counters.get(field, 0) + value
        if sum(self._pending_counters.values()) >= 100:
            self._flush_counters()

    def _observe_stage(self, stage: str, seconds: float) -> None:
        total, count = self._pending_stages.get(stage, (0.0, 0))
        self._pending_stages[stage] = (total + seconds, count + 1)

    def _flush_counters(self) -> None:
        if not self._pending_counters and not self._pending_stages:
            return
        values = self._pending_counters
        self._pending_counters = {}
        if values:
            try:
                self.store.increment(self.campaign_id, **values)
            except Exception as exc:
                for field, value in values.items():
                    self._pending_counters[field] = self._pending_counters.get(field, 0) + value
                self.logger.error("campaign counter flush failed: %s", type(exc).__name__)
        stages = self._pending_stages
        self._pending_stages = {}
        for stage, (seconds, observations) in stages.items():
            try:
                self.store.observe_stage(stage, seconds, observations)
            except Exception as exc:
                self._pending_stages[stage] = (seconds, observations)
                self.logger.error("stage metric flush failed: %s", type(exc).__name__)

    def closed(self, reason: str) -> None:
        if self._counter_loop and self._counter_loop.running:
            self._counter_loop.stop()
        self._flush_counters()

    async def start(self):  # type: ignore[no-untyped-def]
        self._counter_loop = LoopingCall(self._flush_counters)
        self._counter_loop.start(2, now=False)
        whale_task = self.whale_task
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
            self.config.request_timeout,
            proxy_pool=ProxyPool(self.config),
            proxy_profile=self.proxy_profile,
            language=self.search_language,
            global_concurrency=self.config.discovery_global_concurrency,
            query_concurrency=self.config.discovery_query_concurrency,
            proxy_usage_recorder=self.store.record_proxy_usage,
            google_web_enabled=self.config.google_web_enabled,
            google_web_initial_rps=self.config.google_web_initial_rps,
            google_web_max_rps=self.config.google_web_max_rps,
            google_web_max_pages=self.config.google_web_max_pages,
            google_web_pages_per_batch=self.config.google_web_pages_per_batch,
            query_cache_seconds=self.config.google_web_query_cache_seconds,
            proxy_min_interval_seconds=self.config.google_web_proxy_min_interval_seconds,
            proxy_cooldown_seconds=self.config.google_web_proxy_cooldown_seconds,
            source_cooldown_seconds=self.config.google_web_source_cooldown_seconds,
            captcha_threshold=self.config.google_web_captcha_threshold,
            web_query_eligible=True,
            cache_get=self.store.get_discovery_cache,
            cache_put=self.store.put_discovery_cache,
            source_slot_acquirer=self.store.acquire_discovery_slot,
            source_result_recorder=self.store.record_discovery_result,
            proxy_reserver=self.store.reserve_google_proxy,
            proxy_result_recorder=self.store.record_google_proxy_result,
            novelty_counter=lambda urls: len(urls) - len(
                self.store.processed_urls(self.campaign_id, urls)
            ),
            page_batch_acquirer=lambda query, locale, max_page, batch_size: (
                self.store.acquire_google_page_batch(
                    self.campaign_id, query, locale, max_page, batch_size,
                    self.config.google_web_query_cache_seconds,
                )
            ),
            page_result_recorder=self.store.record_google_page_result,
        )
        queries = self.terms
        if whale_task and dict(whale_task.get("payload") or {}).get("continuous"):
            after, before = self.store.discovery_window(
                self.campaign_id,
                self.config.discovery_history_start,
                self.config.discovery_window_days,
            )
            today = datetime.now(timezone.utc).date()
            recent_after = today - timedelta(days=2)
            queries = tuple(dict.fromkeys(
                query
                for term in self.terms
                for query in (
                    f"{term} after:{recent_after.isoformat()}",
                    f"{term} after:{after.date().isoformat()} before:{before.date().isoformat()}",
                )
            ))
        pages = self.config.google_web_max_pages
        # Discovery uses requests and its own bounded thread pools. Never let
        # those blocking calls stall the shared long-lived Scrapy reactor.
        discovery_started = time.monotonic()
        results, errors = await asyncio.to_thread(discovery.discover_many, queries, pages)
        self._observe_stage("discovery", time.monotonic() - discovery_started)
        self._increment(discovered=len(results))
        for error in errors:
            self.store.record_event(self.campaign_id, "", "discovery_failed", error_code=error[:120])
        candidate_map: dict[str, tuple[str, ...]] = {}
        for result in results:
            try:
                url = normalize_url(result.url)
            except Exception:
                continue
            previous = candidate_map.get(url, ())
            candidate_map[url] = tuple(dict.fromkeys((*previous, *result.engines)))
        candidates = list(candidate_map.items())
        candidate_urls = list(candidate_map)
        reusable = self.store.reusable_pages(self.campaign_id, candidate_urls)
        for row in reusable:
            page_id = int(row["id"])
            if not self.store.attach_page(self.campaign_id, page_id):
                continue
            self.pending_accepts += 1
            if whale_task and str(row.get("content") or ""):
                item = {
                    "campaign_id": self.campaign_id,
                    "url": str(row["url"]),
                    "title": str(row["title"]),
                    "content": str(row["content"]),
                    "content_hash": str(row["content_hash"]),
                    "language": str(row["language"] or "unknown"),
                    "fetched_at": str(row["fetched_at"]),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                    "query": self.query,
                    "source_engines": tuple(row.get("source_engines") or ()),
                    "http_status": int(row["http_status"]),
                }
                record_key, message = whale_message(item, whale_task, self.config)
                self.store.queue_whale_message(str(whale_task["task_id"]), record_key, message)
        processed = self.store.processed_urls(self.campaign_id, candidate_urls)
        if processed:
            self._increment(duplicates=len(processed))
            self.store.record_event(
                self.campaign_id, "", "skipped", error_code=f"already_processed:{len(processed)}"
            )
        novel_candidates = [(url, engines) for url, engines in candidates if url not in processed]
        blocked_domains = self.store.blocked_domains([
            urlsplit(url).hostname or "" for url, _ in novel_candidates
        ])
        if blocked_domains:
            novel_candidates = [
                (url, engines) for url, engines in novel_candidates
                if (urlsplit(url).hostname or "").lower() not in blocked_domains
            ]
        if whale_task and dict(whale_task.get("payload") or {}).get("continuous"):
            self.store.record_discovery_novelty(
                self.campaign_id, len(candidates), len(novel_candidates),
                self.config.discovery_min_novelty_ratio,
                self.config.discovery_exhausted_cooldown_seconds,
            )
        # DNS/public-address validation is expensive. Never run it for URLs
        # already known to be processed.
        for offset in range(0, len(novel_candidates), 64):
            batch = novel_candidates[offset:offset + 64]
            public = await asyncio.gather(*(
                asyncio.to_thread(is_public_url, url) for url, _ in batch
            ))
            for (url, engines), allowed in zip(batch, public):
                if not allowed:
                    continue
                yield self._page_request(url, engines)
                # Keep Whale google_search tasks restricted to Google-discovered URLs.

    def _page_request(
        self, url: str, engines: tuple[str, ...] = (), *, short_retry: bool = False,
        browser: bool = False,
    ) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_page,
            errback=self.on_error,
            meta={
                "source_engines": engines,
                "proxy_profile": self.proxy_profile,
                "short_retry": short_retry,
                "playwright": browser,
            },
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
            dont_filter=True,
        )

    def parse_page(self, response: scrapy.http.Response):  # type: ignore[no-untyped-def]
        self.page_responses += 1
        self._increment(fetched=1)
        content_type = response.headers.get(b"Content-Type", b"").decode(errors="ignore").lower()
        if "html" not in content_type:
            self.store.record_domain_result(response.url, False)
            self._increment(failed=1)
            self.store.record_event(
                self.campaign_id, response.url, "permanent_failed", response.status, "non_html"
            )
            return
        extraction_started = time.monotonic()
        title, content = extract_text(
            response.body, response.url, self.config.trafilatura_enabled
        )
        self._observe_stage("extraction", time.monotonic() - extraction_started)
        if len(content) < self.config.crawler_min_content_chars:
            self._increment(failed=1)
            domain = (urlsplit(response.url).hostname or "").lower()
            if response.meta.get("playwright"):
                self.store.record_browser_result(domain, False)
            browser_budget = (
                self.config.browser_fallback_enabled
                and self.page_responses >= 20
                and (self.browser_requests + 1) / self.page_responses
                <= min(max(self.config.browser_fallback_ratio, 0), 0.05)
            )
            if (
                not response.meta.get("playwright")
                and response.status == 200
                and is_javascript_shell(response.body)
                and browser_budget
                and self.store.browser_domain_allowed(domain)
            ):
                self.browser_requests += 1
                self.store.record_event(
                    self.campaign_id, response.url, "browser_fallback", response.status, "js_shell"
                )
                yield self._page_request(
                    response.url, tuple(response.meta.get("source_engines") or ()),
                    short_retry=True, browser=True,
                )
                return
            if not response.meta.get("short_retry"):
                yield self._page_request(
                    response.url,
                    tuple(response.meta.get("source_engines") or ()),
                    short_retry=True,
                )
                return
            self.store.record_domain_result(response.url, False)
            self.store.record_event(
                self.campaign_id, response.url, "permanent_failed", response.status, "short_content"
            )
            return
        language = detect_language(content)
        if response.meta.get("playwright"):
            self.store.record_browser_result(
                (urlsplit(response.url).hostname or "").lower(), True
            )
        self.store.record_domain_result(response.url, True)
        relevant = relevant_to(content, title, self.terms)
        if relevant and self.keyword_kind == "trend":
            searchable_text = f"{title} {content}"
            searchable_folded = searchable_text.casefold()
            relevant = bool(AI_ANCHORS.search(searchable_text)) and any(
                alias.casefold() in searchable_folded
                for alias in self.terms if alias.strip()
            )
        if relevant and language in {"zh", "en"}:
            if (
                self.daily_target > 0
                and self.starting_daily_count + self.pending_accepts >= self.daily_target
            ):
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
            if (
                self.daily_target > 0
                and self.starting_daily_count + self.pending_accepts >= self.daily_target
            ):
                return
        else:
            self._increment(irrelevant=1)
            self.store.record_event(
                self.campaign_id, response.url, "permanent_failed", response.status, "irrelevant"
            )

        return

    def on_error(self, failure):  # type: ignore[no-untyped-def]
        request = failure.request
        if request.meta.get("playwright"):
            self.store.record_browser_result(
                (urlsplit(request.url).hostname or "").lower(), False
            )
        self.store.record_domain_result(request.url, False)
        self._increment(failed=1)
        self.store.record_event(
            self.campaign_id, request.url, "retryable_failed", error_code=type(failure.value).__name__
        )

    def ignore_error(self, failure):  # type: ignore[no-untyped-def]
        return None
