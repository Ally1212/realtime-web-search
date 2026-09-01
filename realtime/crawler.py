from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import scrapy
from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import IgnoreRequest

from .campaign_store import CampaignStore, PageRecord
from .config import Config
from .discovery import SearchDiscovery
from .fetcher import detect_language, extract_text, is_public_url, normalize_url, relevant_to
from .proxy_pool import ProxyPool
from .search import SearchIndex


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

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler) -> "PagePipeline":
        return cls(Config())

    def open_spider(self, spider: scrapy.Spider) -> None:
        self.index.ensure_index()

    def process_item(self, item: dict[str, object], spider: scrapy.Spider) -> dict[str, object]:
        campaign_id = str(item["campaign_id"])
        page = PageRecord(
            url=str(item["url"]),
            content_hash=str(item["content_hash"]),
            title=str(item["title"]),
            summary=str(item["summary"]),
            language=str(item["language"]),
            http_status=int(item["http_status"]),
            fetched_at=str(item["fetched_at"]),
            source_engines=tuple(item.get("source_engines") or ()),
        )
        _, inserted, duplicate_content = self.store.record_page(campaign_id, page)
        if inserted:
            self.buffer.append(item)
            setattr(spider, "accepted", int(getattr(spider, "accepted", 0)) + 1)
            if len(self.buffer) >= 100:
                self.flush()
            target = int(getattr(spider, "daily_target", 50_000))
            if self.store.daily_count(campaign_id) >= target and not getattr(spider, "closing_for_target", False):
                setattr(spider, "closing_for_target", True)
                asyncio.get_running_loop().create_task(
                    spider.crawler.engine.close_spider_async(reason="daily_target_reached")
                )
        else:
            self.store.increment(campaign_id, duplicates=1)
        if duplicate_content and not inserted:
            spider.crawler.stats.inc_value("content_duplicates")
        return item

    def flush(self) -> None:
        if not self.buffer:
            return
        indexed, errors = self.index.bulk_index(self.buffer)
        if errors:
            raise RuntimeError(f"OpenSearch bulk indexing failed for {len(errors)} documents")
        self.buffer.clear()

    def close_spider(self, spider: scrapy.Spider) -> None:
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
        self._sitemap_hosts: set[str] = set()

    async def start(self):  # type: ignore[no-untyped-def]
        discovery = SearchDiscovery(self.config.searxng_url, self.config.request_timeout)
        results, errors = discovery.discover_many(self.terms, self.config.discovery_pages)
        self.store.increment(self.campaign_id, discovered=len(results))
        for error in errors:
            self.store.record_event(self.campaign_id, "", "discovery_failed", error_code=error[:120])
        for result in results:
            try:
                url = normalize_url(result.url)
            except Exception:
                continue
            if not is_public_url(url):
                continue
            yield self._page_request(url, tuple(result.engines))
            sitemap = self._sitemap_request(url)
            if sitemap:
                yield sitemap

    def _page_request(self, url: str, engines: tuple[str, ...] = ()) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_page,
            errback=self.on_error,
            meta={"source_engines": engines, "proxy_profile": self.proxy_profile},
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
        )

    def _sitemap_request(self, url: str) -> scrapy.Request | None:
        parsed = urlsplit(url)
        host = parsed.netloc.lower()
        if host in self._sitemap_hosts:
            return None
        self._sitemap_hosts.add(host)
        return scrapy.Request(
            f"{parsed.scheme}://{host}/sitemap.xml",
            callback=self.parse_sitemap,
            errback=self.ignore_error,
            meta={"proxy_profile": self.proxy_profile, "handle_httpstatus_all": True},
            dont_filter=True,
        )

    def parse_sitemap(self, response: scrapy.http.Response):  # type: ignore[no-untyped-def]
        if response.status >= 400 or len(response.body) > 5_000_000:
            return
        try:
            root = ElementTree.fromstring(response.body)
        except ElementTree.ParseError:
            return
        count = 0
        for node in root.iter():
            if not node.tag.endswith("loc") or not node.text:
                continue
            try:
                url = normalize_url(node.text.strip())
            except Exception:
                continue
            if url.lower().endswith(SKIP_SUFFIXES) or not is_public_url(url):
                continue
            count += 1
            if count > 10_000:
                break
            if url.lower().endswith((".xml", ".xml.gz")):
                yield scrapy.Request(
                    url, callback=self.parse_sitemap, errback=self.ignore_error,
                    meta={"proxy_profile": self.proxy_profile},
                )
            else:
                yield self._page_request(url)

    def parse_page(self, response: scrapy.http.Response):  # type: ignore[no-untyped-def]
        self.store.increment(self.campaign_id, fetched=1)
        content_type = response.headers.get(b"Content-Type", b"").decode(errors="ignore").lower()
        if "html" not in content_type:
            self.store.increment(self.campaign_id, failed=1)
            return
        title, content = extract_text(response.body, response.url)
        if len(content) < 100:
            self.store.increment(self.campaign_id, failed=1)
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
            self.store.increment(self.campaign_id, irrelevant=1)

        parsed = urlsplit(response.url)
        candidates: list[tuple[int, str]] = []
        for anchor in response.css("a"):
            href = anchor.attrib.get("href")
            if not href:
                continue
            try:
                url = normalize_url(urljoin(response.url, href))
            except Exception:
                continue
            if url.lower().endswith(SKIP_SUFFIXES) or not is_public_url(url):
                continue
            text = " ".join(anchor.css("::text").getall())
            same_host = urlsplit(url).netloc == parsed.netloc
            linked_relevant = relevant_to(f"{text} {url}", text, self.terms)
            if linked_relevant or (same_host and relevant):
                candidates.append((0 if linked_relevant else 1, url))
        for _, url in sorted(candidates)[: self.config.max_links_per_page]:
            yield self._page_request(url, tuple(response.meta.get("source_engines") or ()))

    def on_error(self, failure):  # type: ignore[no-untyped-def]
        request = failure.request
        self.store.increment(self.campaign_id, failed=1)
        self.store.record_event(
            self.campaign_id, request.url, "failed", error_code=type(failure.value).__name__
        )

    def ignore_error(self, failure):  # type: ignore[no-untyped-def]
        return None
