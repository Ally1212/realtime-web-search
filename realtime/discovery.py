from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .proxy_pool import ProxyPool


MAX_FEED_BYTES = 5_000_000
GOOGLE_ENGINES = ("google",)
_DISCOVERY_LIMITERS: dict[int, threading.BoundedSemaphore] = {}
_DISCOVERY_LIMITERS_LOCK = threading.Lock()
_NEWS_RESOLUTION_PENDING: set[str] = set()
_NEWS_RESOLUTION_LOCK = threading.Lock()
_NEWS_RESOLUTION_LIMIT = 32
_NEWS_RESOLUTION_WORKERS = threading.BoundedSemaphore(2)


def _discovery_limiter(limit: int) -> threading.BoundedSemaphore:
    bounded = max(1, limit)
    with _DISCOVERY_LIMITERS_LOCK:
        return _DISCOVERY_LIMITERS.setdefault(bounded, threading.BoundedSemaphore(bounded))


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    engines: tuple[str, ...]


class GoogleBlocked(RuntimeError):
    def __init__(self, reason: str, status: int | None = None, *, captcha: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.captcha = captcha


class SearchDiscovery:
    """Google-only discovery with durable caching and cross-process throttling hooks."""

    def __init__(
        self,
        base_url: str,
        timeout: int = 20,
        session: requests.Session | None = None,
        feeds: tuple[tuple[str, str], ...] = (),
        proxy_pool: ProxyPool | None = None,
        proxy_profile: str = "direct",
        language: str = "en",
        global_concurrency: int = 24,
        query_concurrency: int = 4,
        proxy_usage_recorder: Callable[[list[tuple[str, str, int]]], None] | None = None,
        *,
        google_web_enabled: bool = True,
        searxng_enabled: bool = False,
        google_web_initial_rps: float = 0.5,
        google_web_max_rps: float = 2.0,
        google_web_max_pages: int = 2,
        second_page_min_novelty: float = 0.15,
        query_cache_seconds: int = 21600,
        proxy_min_interval_seconds: int = 30,
        proxy_cooldown_seconds: int = 21600,
        source_cooldown_seconds: int = 1800,
        captcha_threshold: float = 0.02,
        news_locales: tuple[str, ...] = (),
        news_base_interval_seconds: int = 3600,
        news_trend_interval_seconds: int = 900,
        trends_interval_seconds: int = 900,
        web_query_eligible: bool = True,
        cache_get: Callable[..., dict[str, Any] | None] | None = None,
        cache_metadata_get: Callable[[str], dict[str, Any] | None] | None = None,
        cache_put: Callable[..., None] | None = None,
        source_slot_acquirer: Callable[[str, float], dict[str, Any]] | None = None,
        source_result_recorder: Callable[..., None] | None = None,
        proxy_reserver: Callable[[str, str, int], tuple[bool, float]] | None = None,
        proxy_result_recorder: Callable[..., None] | None = None,
        novelty_counter: Callable[[list[str]], int] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._provided_session = session is not None
        self._thread_sessions = threading.local()
        self.feeds = feeds
        self.proxy_pool = proxy_pool
        self.proxy_profile = proxy_profile
        self.language = language if language in {"en", "zh"} else "en"
        self.locale = "zh-CN" if self.language == "zh" else "en-SG"
        self.global_limiter = _discovery_limiter(global_concurrency)
        self.query_concurrency = max(1, query_concurrency)
        self.proxy_usage_recorder = proxy_usage_recorder
        self.google_web_enabled = google_web_enabled
        self.searxng_enabled = searxng_enabled
        self.google_web_initial_rps = max(0.01, google_web_initial_rps)
        self.google_web_max_rps = max(self.google_web_initial_rps, google_web_max_rps)
        self.google_web_max_pages = max(1, google_web_max_pages)
        self.second_page_min_novelty = max(0.0, min(second_page_min_novelty, 1.0))
        self.query_cache_seconds = max(1, query_cache_seconds)
        self.proxy_min_interval_seconds = max(0, proxy_min_interval_seconds)
        self.proxy_cooldown_seconds = max(1, proxy_cooldown_seconds)
        self.source_cooldown_seconds = max(1, source_cooldown_seconds)
        self.captcha_threshold = max(0.0, captcha_threshold)
        self.news_locales = news_locales
        self.news_base_interval_seconds = max(60, news_base_interval_seconds)
        self.news_trend_interval_seconds = max(60, news_trend_interval_seconds)
        self.trends_interval_seconds = max(60, trends_interval_seconds)
        self.web_query_eligible = web_query_eligible
        self.cache_get = cache_get
        self.cache_metadata_get = cache_metadata_get
        self.cache_put = cache_put
        self.source_slot_acquirer = source_slot_acquirer
        self.source_result_recorder = source_result_recorder
        self.proxy_reserver = proxy_reserver
        self.proxy_result_recorder = proxy_result_recorder
        self.novelty_counter = novelty_counter
        self._proxy_usage: dict[tuple[str, str], int] = {}
        self._proxy_usage_lock = threading.Lock()
        self._proxy_sessions: dict[str, tuple[requests.Session, threading.Lock]] = {}
        self._proxy_sessions_lock = threading.Lock()

    def _general_session(self) -> requests.Session:
        if self._provided_session:
            return self.session
        value = getattr(self._thread_sessions, "session", None)
        if value is None:
            value = requests.Session()
            self._thread_sessions.session = value
        return value

    @staticmethod
    def _cache_key(source: str, query: str, locale: str, page: int = 1) -> str:
        value = json.dumps([source, query, locale, page], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _serialize(results: list[SearchResult]) -> list[dict[str, Any]]:
        return [{"url": row.url, "title": row.title, "engines": list(row.engines)} for row in results]

    @staticmethod
    def _deserialize(payload: object) -> list[SearchResult]:
        if not isinstance(payload, list):
            return []
        results: list[SearchResult] = []
        for row in payload:
            if not isinstance(row, dict) or not str(row.get("url") or "").startswith(("http://", "https://")):
                continue
            results.append(SearchResult(
                str(row["url"]), str(row.get("title") or row["url"]),
                tuple(str(value) for value in row.get("engines") or ()),
            ))
        return results

    def _cached(self, source: str, query: str, locale: str, page: int = 1) -> tuple[str, list[SearchResult] | None]:
        key = self._cache_key(source, query, locale, page)
        if not self.cache_get:
            return key, None
        row = self.cache_get(key, source)
        return key, self._deserialize(row.get("payload")) if row else None

    def _store_cache(
        self, key: str, source: str, query: str, locale: str, page: int,
        results: list[SearchResult], ttl: int, novel_count: int = 0,
        response: requests.Response | None = None,
    ) -> None:
        if not self.cache_put:
            return
        self.cache_put(
            key, source, hashlib.sha256(query.encode()).hexdigest(), locale, page,
            self._serialize(results), ttl, novel_count=novel_count,
            etag=response.headers.get("ETag") if response else None,
            last_modified=response.headers.get("Last-Modified") if response else None,
        )

    def _external_get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._external_request("get", url, **kwargs)

    def _external_post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._external_request("post", url, **kwargs)

    def _external_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        selected = None
        domain = urlsplit(url).hostname or ""
        if self.proxy_pool and self.proxy_profile != "direct":
            selected = self.proxy_pool.choose(self.proxy_profile, domain)
            if selected is None:
                raise requests.ProxyError(f"{self.proxy_profile} proxy pool unavailable")
            proxy_url, proxy_key = selected
            usage_key = (self.proxy_profile, hashlib.sha256(proxy_key.encode()).hexdigest())
            with self._proxy_usage_lock:
                self._proxy_usage[usage_key] = self._proxy_usage.get(usage_key, 0) + 1
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        try:
            response = getattr(self._general_session(), method)(url, **kwargs)
        except requests.RequestException:
            if selected:
                self.proxy_pool.report(selected[1], domain, failed=True)
            raise
        if selected:
            self.proxy_pool.report(selected[1], domain, response.status_code)
        return response

    def _flush_proxy_usage(self) -> None:
        if not self.proxy_usage_recorder:
            return
        with self._proxy_usage_lock:
            usage, self._proxy_usage = self._proxy_usage, {}
        if usage:
            self.proxy_usage_recorder([(key, profile, count) for (profile, key), count in usage.items()])

    @staticmethod
    def _parse_google_html(content: bytes) -> list[SearchResult]:
        results: list[SearchResult] = []
        soup = BeautifulSoup(content, "html.parser")
        for anchor in soup.select("a[href]"):
            href = str(anchor.get("href") or "").strip()
            if href.startswith("/url?"):
                values = parse_qs(urlsplit(href).query)
                href = str((values.get("q") or values.get("url") or [""])[0])
            host = (urlsplit(href).hostname or "").lower()
            if not href.startswith(("http://", "https://")) or host.endswith("google.com"):
                continue
            title_node = anchor.find(["h3", "h2"])
            if title_node:
                title = title_node.get_text(" ", strip=True)
                if title:
                    results.append(SearchResult(href, title, ("google_web",)))
        return results

    @staticmethod
    def _google_block(response: requests.Response) -> GoogleBlocked | None:
        status_value = getattr(response, "status_code", 200)
        status = status_value if isinstance(status_value, int) else 200
        url = str(getattr(response, "url", "") or "").lower()
        body = bytes(getattr(response, "content", b"") or b"")[:500_000].lower()
        markers = (b"unusual traffic", b"our systems have detected", b"g-recaptcha", b"/sorry/")
        if any(marker in body for marker in markers) or "/sorry/" in url:
            return GoogleBlocked("google_captcha", status, captcha=True)
        if "consent.google." in url or b"before you continue to google" in body:
            return GoogleBlocked("google_consent", status)
        if status in {403, 429}:
            return GoogleBlocked(f"google_http_{status}", status)
        return None

    def _proxy_session(self, proxy_hash: str) -> tuple[requests.Session, threading.Lock]:
        with self._proxy_sessions_lock:
            return self._proxy_sessions.setdefault(proxy_hash, (requests.Session(), threading.Lock()))

    def _retire_proxy_session(self, proxy_hash: str) -> None:
        with self._proxy_sessions_lock:
            value = self._proxy_sessions.pop(proxy_hash, None)
        if value:
            value[0].close()

    def _google_web_get(self, query: str, page: int) -> requests.Response:
        if self.source_slot_acquirer:
            slot = self.source_slot_acquirer("google_web", self.google_web_initial_rps)
            if not slot.get("allowed"):
                raise GoogleBlocked("google_web_circuit_open")
            wait = float(slot.get("wait") or 0)
            if wait > 0:
                time.sleep(wait)
        params = {
            "q": query, "num": 10, "start": (page - 1) * 10, "filter": 0,
            "hl": "zh-CN" if self.language == "zh" else "en",
        }
        headers = {"Accept-Language": (
            "zh-CN,zh;q=0.9,en;q=0.5" if self.language == "zh" else "en-SG,en;q=0.8"
        )}
        selected: tuple[str, str] | None = None
        proxy_hash = ""
        if self.proxy_pool and self.proxy_profile != "direct":
            for _ in range(64):
                candidate = self.proxy_pool.choose(
                    self.proxy_profile, "www.google.com", sticky_seconds=0,
                    sticky_key=f"google-web:{query}", full_pool=True,
                )
                if not candidate:
                    break
                candidate_hash = hashlib.sha256(candidate[1].encode()).hexdigest()
                allowed, wait = (
                    self.proxy_reserver(candidate_hash, self.locale, self.proxy_min_interval_seconds)
                    if self.proxy_reserver else (True, 0.0)
                )
                if allowed:
                    selected, proxy_hash = candidate, candidate_hash
                    break
                self.proxy_pool.defer(candidate[1], "www.google.com", min(max(wait, 0.1), 60))
            if not selected:
                raise requests.ProxyError(f"{self.proxy_profile} Google proxy pool unavailable")
        request_session, request_lock = self._proxy_session(proxy_hash) if selected else (self._general_session(), threading.Lock())
        kwargs: dict[str, Any] = {"params": params, "headers": headers, "timeout": self.timeout}
        if selected:
            kwargs["proxies"] = {"http": selected[0], "https": selected[0]}
            with self._proxy_usage_lock:
                key = (self.proxy_profile, proxy_hash)
                self._proxy_usage[key] = self._proxy_usage.get(key, 0) + 1
        try:
            with self.global_limiter, request_lock:
                response = request_session.get("https://www.google.com/search", **kwargs)
        except requests.RequestException:
            if selected:
                self.proxy_pool.report(selected[1], "www.google.com", failed=True)
                if self.proxy_result_recorder:
                    self.proxy_result_recorder(proxy_hash, success=False)
            if self.source_result_recorder:
                self.source_result_recorder(
                    "google_web", success=False, maximum_rps=self.google_web_max_rps,
                    captcha_threshold=self.captcha_threshold,
                    source_cooldown_seconds=self.source_cooldown_seconds,
                )
            raise
        blocked = self._google_block(response)
        if selected:
            self.proxy_pool.report(selected[1], "www.google.com", response.status_code)
        if blocked:
            if selected:
                self.proxy_pool.defer(selected[1], "www.google.com", self.proxy_cooldown_seconds)
                self._retire_proxy_session(proxy_hash)
                if self.proxy_result_recorder:
                    self.proxy_result_recorder(proxy_hash, success=False, cooldown_seconds=self.proxy_cooldown_seconds)
            if self.source_result_recorder:
                self.source_result_recorder(
                    "google_web", success=False, limited=True, captcha=blocked.captcha,
                    maximum_rps=self.google_web_max_rps,
                    captcha_threshold=self.captcha_threshold,
                    source_cooldown_seconds=self.source_cooldown_seconds,
                )
            response.close()
            raise blocked
        try:
            response.raise_for_status()
        except requests.RequestException:
            if selected and self.proxy_result_recorder:
                self.proxy_result_recorder(proxy_hash, success=False)
            if self.source_result_recorder:
                self.source_result_recorder(
                    "google_web", success=False, maximum_rps=self.google_web_max_rps,
                    captcha_threshold=self.captcha_threshold,
                    source_cooldown_seconds=self.source_cooldown_seconds,
                )
            raise
        if selected and self.proxy_result_recorder:
            self.proxy_result_recorder(proxy_hash, success=True)
        return response

    def _discover_google_page(self, query: str, page: int) -> list[SearchResult]:
        cache_key, cached = self._cached("google_web", query, self.locale, page)
        if cached is not None:
            return cached
        response = self._google_web_get(query, page)
        results = self._parse_google_html(response.content)
        novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter else len(results)
        self._store_cache(cache_key, "google_web", query, self.locale, page, results, self.query_cache_seconds, novel, response)
        if self.source_result_recorder:
            self.source_result_recorder(
                "google_web", success=True, result_count=len(results), novel_count=novel,
                maximum_rps=self.google_web_max_rps, captcha_threshold=self.captcha_threshold,
                source_cooldown_seconds=self.source_cooldown_seconds,
            )
        return results

    @staticmethod
    def _read_limited(response: requests.Response) -> bytes:
        declared = int(response.headers.get("Content-Length", "0") or 0)
        if declared > MAX_FEED_BYTES:
            raise ValueError("feed_too_large")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(65_536):
            size += len(chunk)
            if size > MAX_FEED_BYTES:
                raise ValueError("feed_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_feed(content: bytes, source: str) -> list[SearchResult]:
        root = ElementTree.fromstring(content)
        results: list[SearchResult] = []
        for entry in root.iter():
            if entry.tag.rsplit("}", 1)[-1].lower() not in {"item", "entry"}:
                continue
            title, link = "", ""
            trend_links: list[str] = []
            for child in entry.iter():
                tag = child.tag.rsplit("}", 1)[-1].lower()
                if tag == "title" and not title:
                    title = "".join(child.itertext()).strip()
                elif child is not entry and tag == "link" and not link:
                    rel = child.attrib.get("rel", "alternate")
                    if rel == "alternate":
                        link = (child.attrib.get("href") or child.text or "").strip()
                elif tag == "news_item_url":
                    value = (child.text or "").strip()
                    if value:
                        trend_links.append(value)
            for value in trend_links or ([link] if link else []):
                results.append(SearchResult(value, title or value, (source,)))
        return results

    @staticmethod
    def _offline_google_news_url(url: str) -> str | None:
        host = (urlsplit(url).hostname or "").lower()
        if host != "news.google.com":
            return url
        token = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        except (ValueError, TypeError):
            return None
        match = re.search(rb"https?://[^\x00-\x20]+", raw)
        return match.group(0).decode("utf-8", "ignore") if match else None

    def _resolve_google_news(self, result: SearchResult) -> SearchResult | None:
        host = (urlsplit(result.url).hostname or "").lower()
        if host != "news.google.com":
            return result
        offline = self._offline_google_news_url(result.url)
        if offline:
            return SearchResult(offline, result.title, result.engines)
        try:
            response = self._external_get(result.url, timeout=min(self.timeout, 5), allow_redirects=True, stream=True)
            try:
                response.raise_for_status()
                resolved = str(response.url or "").strip()
                if resolved and resolved != result.url:
                    return SearchResult(resolved, result.title, result.engines)
                soup = BeautifulSoup(response.content, "html.parser")
                node = soup.select_one("[data-n-a-id][data-n-a-ts][data-n-a-sg]")
                if node:
                    article_id = str(node.get("data-n-a-id") or "")
                    timestamp = int(str(node.get("data-n-a-ts") or "0"))
                    signature = str(node.get("data-n-a-sg") or "")
                    request_payload = json.dumps([
                        "garturlreq", [
                            ["en-US", "US", ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"], None, None, 1, 1, "US:en", None, 180, None, None, None, None, None, 0, None, None, [1608992183, 723341000]],
                            "en-US", "US", 1, [2, 3, 4, 8], 1, 0, "655000234", 0, 0, None, 0,
                        ], article_id, timestamp, signature], separators=(",", ":"))
                    batch = json.dumps([[['Fbv4je', request_payload, None, 'generic']]], separators=(",", ":"))
                    decoded = self._external_post(
                        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                        data={"f.req": batch}, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                        timeout=self.timeout,
                    )
                    try:
                        decoded.raise_for_status()
                        match = re.search(r'garturlres\\",\\"(https?[^"\\]+)', decoded.text)
                        if match:
                            resolved = json.loads(f'"{match.group(1)}"')
                            if resolved and "news.google.com" not in resolved:
                                return SearchResult(resolved, result.title, result.engines)
                    finally:
                        decoded.close()
            finally:
                response.close()
        except Exception:
            return None
        return None

    def _prepare_google_news(self, result: SearchResult) -> SearchResult:
        """Decode cheaply now and bound slower network resolution in the background."""
        offline = self._offline_google_news_url(result.url)
        if offline:
            return SearchResult(offline, result.title, result.engines)
        key, cached = self._cached("google_news_resolve", result.url, "global")
        if cached:
            return cached[0]
        with _NEWS_RESOLUTION_LOCK:
            if result.url in _NEWS_RESOLUTION_PENDING or len(_NEWS_RESOLUTION_PENDING) >= _NEWS_RESOLUTION_LIMIT:
                return result
            _NEWS_RESOLUTION_PENDING.add(result.url)

        def resolve() -> None:
            try:
                with _NEWS_RESOLUTION_WORKERS:
                    resolved = self._resolve_google_news(result)
                if resolved:
                    self._store_cache(
                        key, "google_news_resolve", result.url, "global", 1,
                        [resolved], 604800,
                    )
            finally:
                with _NEWS_RESOLUTION_LOCK:
                    _NEWS_RESOLUTION_PENDING.discard(result.url)

        threading.Thread(target=resolve, name="google-news-resolver", daemon=True).start()
        return result

    @staticmethod
    def _news_url(query: str, locale: str) -> str:
        country, language = (locale.split(":", 1) + ["en"])[:2]
        hl = f"{language}-{country}" if language in {"en", "zh"} else language
        return "https://news.google.com/rss/search?q=" + quote(query, safe="") + f"&hl={quote(hl)}&gl={quote(country)}&ceid={quote(locale)}"

    def _feed_targets(self, queries: tuple[str, ...]) -> dict[str, tuple[str, str, str, int]]:
        rendered: dict[str, tuple[str, str, str, int]] = {}
        locales = self.news_locales
        for query in queries:
            ttl = self.news_base_interval_seconds
            for locale in locales:
                rendered[self._news_url(query, locale)] = ("google_news", query, locale, ttl)
            for name, template in self.feeds:
                if "google-news" in name and self.news_locales:
                    continue
                encoded = quote(query, safe="")
                url = (template.replace("{query}", encoded)
                       .replace("{hl}", "zh-CN" if self.language == "zh" else "en-SG")
                       .replace("{ceid}", "SG:zh-Hans" if self.language == "zh" else "SG:en"))
                rendered[url] = (name, query, self.locale, ttl)
        return rendered

    def discover_feeds(self, queries: tuple[str, ...]) -> tuple[list[SearchResult], list[str]]:
        rendered = self._feed_targets(queries)
        if not rendered:
            return [], []

        def fetch(item: tuple[str, tuple[str, str, str, int]]) -> tuple[list[SearchResult], str | None]:
            url, (name, query, locale, ttl) = item
            key, cached = self._cached(name, query, locale)
            if cached is not None:
                return cached, None
            try:
                stale = self.cache_metadata_get(key) if self.cache_metadata_get else None
                headers = {}
                if stale and stale.get("etag"):
                    headers["If-None-Match"] = str(stale["etag"])
                if stale and stale.get("last_modified"):
                    headers["If-Modified-Since"] = str(stale["last_modified"])
                response = self._external_get(
                    url, timeout=self.timeout, stream=True, headers=headers,
                )
                if response.status_code == 304 and stale:
                    results = self._deserialize(stale.get("payload"))
                    if self.cache_put:
                        self.cache_put(
                            key, name, hashlib.sha256(query.encode()).hexdigest(), locale, 1,
                            self._serialize(results), ttl,
                            etag=stale.get("etag"), last_modified=stale.get("last_modified"),
                        )
                    return results, None
                response.raise_for_status()
                results = self._parse_feed(self._read_limited(response), name)
                if "google_news" in name or "google-news" in name:
                    results = [self._prepare_google_news(row) for row in results]
                self._store_cache(key, name, query, locale, 1, results, ttl, response=response)
                if self.source_result_recorder:
                    self.source_result_recorder(name, success=True, result_count=len(results))
                return results, None
            except Exception as exc:
                if self.source_result_recorder:
                    self.source_result_recorder(name, success=False)
                return [], f"{name}: {type(exc).__name__}"

        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(rendered))) as executor:
            for results, error in executor.map(fetch, rendered.items()):
                if error:
                    errors.append(error)
                for result in results:
                    previous = found.get(result.url)
                    if previous:
                        found[result.url] = SearchResult(result.url, previous.title, tuple(dict.fromkeys((*previous.engines, *result.engines))))
                    else:
                        found[result.url] = result
        return list(found.values()), errors

    def discover_trends(self) -> tuple[list[SearchResult], list[str]]:
        geos = tuple(dict.fromkeys(locale.split(":", 1)[0] for locale in self.news_locales)) or ("US", "SG")
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        for geo in geos:
            key, cached = self._cached("google_trends", "daily-trends", geo)
            if cached is not None:
                results = cached
            else:
                try:
                    response = self._external_get(f"https://trends.google.com/trending/rss?geo={geo}", timeout=self.timeout, stream=True)
                    response.raise_for_status()
                    results = self._parse_feed(self._read_limited(response), "google_trends")
                    self._store_cache(key, "google_trends", "daily-trends", geo, 1, results, self.trends_interval_seconds, response=response)
                    if self.source_result_recorder:
                        self.source_result_recorder("google_trends", success=True, result_count=len(results))
                except Exception as exc:
                    errors.append(f"google_trends: {type(exc).__name__}")
                    if self.source_result_recorder:
                        self.source_result_recorder("google_trends", success=False)
                    continue
            for result in results:
                title = result.title.casefold()
                if (
                    re.search(r"\b(ai|llm|gpt)\b", title)
                    or any(marker in title for marker in (
                        "artificial intelligence", "machine learning", "deep learning",
                        "generative", "openai", "anthropic", "gemini", "人工智能",
                        "机器学习", "大模型", "生成式",
                    ))
                ):
                    found.setdefault(result.url, result)
        return list(found.values()), errors

    def _discover_searx(self, query: str, page: int, engines: tuple[str, ...]) -> tuple[list[SearchResult], list[str]]:
        try:
            response = self.session.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "categories": "general", "engines": ",".join(engines), "pageno": page},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            results = [
                SearchResult(str(item["url"]), str(item.get("title") or item["url"]), ("google_web",))
                for item in payload.get("results", []) if str(item.get("url") or "").startswith(("http://", "https://"))
                and "google" in tuple(str(value).lower() for value in item.get("engines") or (item.get("engine"),))
            ]
            errors = [": ".join(str(value) for value in row) for row in payload.get("unresponsive_engines", []) if row and str(row[0]).lower() == "google"]
            return results, errors
        except Exception as exc:
            return [], [f"page {page}: searxng {type(exc).__name__}"]

    def discover(self, query: str, pages: int, engines: tuple[str, ...] = GOOGLE_ENGINES) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        page_limit = min(max(1, pages), self.google_web_max_pages)
        if self.google_web_enabled and self.web_query_eligible:
            try:
                first = self._discover_google_page(query, 1)
                for result in first:
                    found.setdefault(result.url, result)
                novel = self.novelty_counter([row.url for row in first]) if self.novelty_counter else len(first)
                ratio = novel / max(len(first), 1)
                if page_limit >= 2 and ratio >= self.second_page_min_novelty:
                    for result in self._discover_google_page(query, 2):
                        found.setdefault(result.url, result)
            except Exception as exc:
                reason = exc.reason if isinstance(exc, GoogleBlocked) else type(exc).__name__
                errors.append(f"page 1: google web {reason}")
        if not found and self.searxng_enabled:
            for page in range(1, page_limit + 1):
                results, page_errors = self._discover_searx(query, page, engines)
                for result in results:
                    found.setdefault(result.url, result)
                errors.extend(page_errors)
        return list(found.values()), list(dict.fromkeys(errors))

    def discover_many(self, queries: tuple[str, ...], pages: int) -> tuple[list[SearchResult], list[str]]:
        if not queries:
            return [], []
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.query_concurrency, len(queries))) as executor:
                batches = executor.map(lambda query: self.discover(query, pages), queries)
                for query, (results, query_errors) in zip(queries, batches):
                    for result in results:
                        previous = found.get(result.url)
                        found[result.url] = SearchResult(
                            result.url, previous.title if previous else result.title,
                            tuple(dict.fromkeys((*(previous.engines if previous else ()), *result.engines))),
                        )
                    errors.extend(f"{query}: {value}" for value in query_errors)
            feed_results, feed_errors = self.discover_feeds(queries)
            trend_results, trend_errors = self.discover_trends()
            for result in (*feed_results, *trend_results):
                previous = found.get(result.url)
                found[result.url] = SearchResult(
                    result.url, previous.title if previous else result.title,
                    tuple(dict.fromkeys((*(previous.engines if previous else ()), *result.engines))),
                )
            errors.extend(feed_errors)
            errors.extend(trend_errors)
            google_news = [
                (url, result) for url, result in found.items()
                if (urlsplit(url).hostname or "").lower() == "news.google.com"
            ]
            for original, row in google_news:
                prepared = self._prepare_google_news(row)
                if prepared.url != original:
                    found.pop(original, None)
                    found.setdefault(prepared.url, prepared)
            return list(found.values()), list(dict.fromkeys(errors))
        finally:
            self._flush_proxy_usage()
