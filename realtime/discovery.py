from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .proxy_pool import ProxyPool


_DISCOVERY_LIMITERS: dict[int, threading.BoundedSemaphore] = {}
_DISCOVERY_LIMITERS_LOCK = threading.Lock()


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
        timeout: int = 20,
        session: requests.Session | None = None,
        proxy_pool: ProxyPool | None = None,
        proxy_profile: str = "direct",
        language: str = "en",
        global_concurrency: int = 24,
        query_concurrency: int = 4,
        proxy_usage_recorder: Callable[[list[tuple[str, str, int]]], None] | None = None,
        *,
        google_web_enabled: bool = True,
        google_web_initial_rps: float = 0.5,
        google_web_max_rps: float = 2.0,
        google_web_max_pages: int = 11,
        google_web_pages_per_batch: int = 3,
        query_cache_seconds: int = 21600,
        proxy_min_interval_seconds: int = 30,
        proxy_cooldown_seconds: int = 21600,
        source_cooldown_seconds: int = 1800,
        captcha_threshold: float = 0.02,
        web_query_eligible: bool = True,
        cache_get: Callable[..., dict[str, Any] | None] | None = None,
        cache_put: Callable[..., None] | None = None,
        source_slot_acquirer: Callable[[str, float], dict[str, Any]] | None = None,
        source_result_recorder: Callable[..., None] | None = None,
        proxy_reserver: Callable[[str, str, int], tuple[bool, float]] | None = None,
        proxy_result_recorder: Callable[..., None] | None = None,
        novelty_counter: Callable[[list[str]], int] | None = None,
        page_batch_acquirer: Callable[..., dict[str, Any] | None] | None = None,
        page_result_recorder: Callable[..., bool] | None = None,
    ):
        self.timeout = timeout
        self.session = session or requests.Session()
        self._provided_session = session is not None
        self._thread_sessions = threading.local()
        self.proxy_pool = proxy_pool
        self.proxy_profile = proxy_profile
        self.language = language if language in {"en", "zh"} else "en"
        self.locale = "zh-CN" if self.language == "zh" else "en-SG"
        self.global_limiter = _discovery_limiter(global_concurrency)
        self.query_concurrency = max(1, query_concurrency)
        self.proxy_usage_recorder = proxy_usage_recorder
        self.google_web_enabled = google_web_enabled
        self.google_web_initial_rps = max(0.01, google_web_initial_rps)
        self.google_web_max_rps = max(self.google_web_initial_rps, google_web_max_rps)
        self.google_web_max_pages = max(1, google_web_max_pages)
        self.google_web_pages_per_batch = max(1, google_web_pages_per_batch)
        self.query_cache_seconds = max(1, query_cache_seconds)
        self.proxy_min_interval_seconds = max(0, proxy_min_interval_seconds)
        self.proxy_cooldown_seconds = max(1, proxy_cooldown_seconds)
        self.source_cooldown_seconds = max(1, source_cooldown_seconds)
        self.captcha_threshold = max(0.0, captcha_threshold)
        self.web_query_eligible = web_query_eligible
        self.cache_get = cache_get
        self.cache_put = cache_put
        self.source_slot_acquirer = source_slot_acquirer
        self.source_result_recorder = source_result_recorder
        self.proxy_reserver = proxy_reserver
        self.proxy_result_recorder = proxy_result_recorder
        self.novelty_counter = novelty_counter
        self.page_batch_acquirer = page_batch_acquirer
        self.page_result_recorder = page_result_recorder
        self._proxy_usage: dict[tuple[str, str], int] = {}
        self._proxy_usage_lock = threading.Lock()
        self._proxy_sessions: dict[str, tuple[requests.Session, threading.Lock]] = {}
        self._proxy_sessions_lock = threading.Lock()
        self._browser_state = threading.local()

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

    def _browser_resources(self):  # type: ignore[no-untyped-def]
        resources = getattr(self._browser_state, "resources", None)
        if resources is not None:
            return resources
        if not os.getenv("DISPLAY"):
            raise GoogleBlocked("google_javascript_required")
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="zh-CN" if self.language == "zh" else "en-SG",
        )
        resources = (playwright, browser, context)
        self._browser_state.resources = resources
        return resources

    def _close_browser(self) -> None:
        resources = getattr(self._browser_state, "resources", None)
        if resources is None:
            return
        self._browser_state.resources = None
        playwright, browser, _ = resources
        try:
            browser.close()
        finally:
            playwright.stop()

    def _discover_google_browser_page(self, query: str, page_number: int) -> list[SearchResult]:
        """Render Google's JavaScript retry shell and resolve result redirect tokens."""
        _, _, context = self._browser_resources()
        page = context.new_page()
        try:
            params = {
                "q": query,
                "num": 10,
                "start": (page_number - 1) * 10,
                "filter": 0,
                "hl": "zh-CN" if self.language == "zh" else "en",
                "pws": 0,
                "nfpr": 1,
            }
            page.goto(
                "https://www.google.com/search?" + urlencode(params),
                wait_until="commit",
                timeout=self.timeout * 1000,
            )
            page.wait_for_selector(
                "a:has(h3)", state="attached", timeout=min(self.timeout * 1000, 15_000)
            )
            blocked = self._google_block(type("BrowserResponse", (), {
                "status_code": 200,
                "url": page.url,
                "content": page.content().encode(),
            })())
            if blocked:
                raise blocked
            found: dict[str, SearchResult] = {}
            anchors = page.locator("a:has(h3)")
            for index in range(min(anchors.count(), 30)):
                anchor = anchors.nth(index)
                href = str(anchor.get_attribute("href") or "")
                title = str(anchor.locator("h3").first.inner_text() or "").strip()
                if not href or not title:
                    continue
                absolute = urljoin(page.url, href)
                host = (urlsplit(absolute).hostname or "").lower()
                if host.endswith("google.com") and urlsplit(absolute).path == "/goto":
                    try:
                        redirect = context.request.get(
                            absolute, max_redirects=0, timeout=self.timeout * 1000
                        )
                        absolute = str(redirect.headers.get("location") or "")
                    except Exception:
                        continue
                host = (urlsplit(absolute).hostname or "").lower()
                if absolute.startswith(("http://", "https://")) and not host.endswith("google.com"):
                    found.setdefault(absolute, SearchResult(absolute, title, ("google_web",)))
            if not found:
                raise GoogleBlocked("google_browser_no_results")
            return list(found.values())
        finally:
            page.close()

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
            "hl": "zh-CN" if self.language == "zh" else "en", "pws": 0,
            "nfpr": 1,
        }
        headers = {
            "Accept-Language": (
                "zh-CN,zh;q=0.9,en;q=0.5" if self.language == "zh" else "en-SG,en;q=0.8"
            ),
            "Cookie": "CONSENT=YES+cb.20220419-08-p0.en+FX+111; SOCS=CAESHAgBEhIaAB",
        }
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
                proxy_delay = 300 if blocked.reason == "google_consent" else self.proxy_cooldown_seconds
                self.proxy_pool.defer(selected[1], "www.google.com", proxy_delay)
                self._retire_proxy_session(proxy_hash)
                if self.proxy_result_recorder:
                    self.proxy_result_recorder(
                        proxy_hash, success=False, cooldown_seconds=proxy_delay
                    )
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
        response: requests.Response | None = None
        try:
            try:
                response = self._google_web_get(query, page)
            except GoogleBlocked as exc:
                if exc.reason not in {
                    "google_captcha", "google_consent", "google_http_403", "google_http_429",
                }:
                    raise
                results = self._discover_google_browser_page(query, page)
            else:
                results = self._parse_google_html(response.content)
                if not results and b"/httpservice/retry/enablejs" in response.content:
                    results = self._discover_google_browser_page(query, page)
            novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter else len(results)
            self._store_cache(cache_key, "google_web", query, self.locale, page, results, self.query_cache_seconds, novel, response)
        finally:
            if response is not None:
                response.close()
        if self.source_result_recorder:
            self.source_result_recorder(
                "google_web", success=True, result_count=len(results), novel_count=novel,
                maximum_rps=self.google_web_max_rps, captcha_threshold=self.captcha_threshold,
                source_cooldown_seconds=self.source_cooldown_seconds,
            )
        return results

    def _discover(self, query: str, pages: int) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        page_limit = min(max(1, pages), self.google_web_max_pages)
        if self.google_web_enabled and self.web_query_eligible:
            batch: dict[str, Any] | None = None
            page_start, page_end = 1, page_limit
            if self.page_batch_acquirer:
                try:
                    batch = self.page_batch_acquirer(
                        query, self.locale, page_limit, self.google_web_pages_per_batch
                    )
                except Exception as exc:
                    errors.append(f"google frontier acquire {type(exc).__name__}")
                if batch:
                    page_start = max(1, int(batch["start_page"]))
                    page_end = min(page_limit, int(batch["end_page"]))
                else:
                    page_start, page_end = 1, 0
            for page in range(page_start, page_end + 1):
                try:
                    page_results = self._discover_google_page(query, page)
                    for result in page_results:
                        found.setdefault(result.url, result)
                    novel = (
                        self.novelty_counter([row.url for row in page_results])
                        if self.novelty_counter else len(page_results)
                    )
                    if batch and self.page_result_recorder:
                        advanced = self.page_result_recorder(
                            batch, page, success=True, result_count=len(page_results),
                            unique_count=len({row.url for row in page_results}),
                            novel_count=novel,
                        )
                        if advanced is False:
                            raise RuntimeError("google_frontier_lease_lost")
                except Exception as exc:
                    reason = exc.reason if isinstance(exc, GoogleBlocked) else type(exc).__name__
                    errors.append(f"page {page}: google web {reason}")
                    if batch and self.page_result_recorder:
                        try:
                            self.page_result_recorder(
                                batch, page, success=False, error=reason,
                                captcha=isinstance(exc, GoogleBlocked) and exc.captcha,
                            )
                        except Exception as frontier_exc:
                            errors.append(f"google frontier release {type(frontier_exc).__name__}")
                    break
        return list(found.values()), list(dict.fromkeys(errors))

    def discover(self, query: str, pages: int) -> tuple[list[SearchResult], list[str]]:
        try:
            return self._discover(query, pages)
        finally:
            # Browser resources are thread-local and must be closed by the same
            # executor thread that created them.
            self._close_browser()

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
            return list(found.values()), list(dict.fromkeys(errors))
        finally:
            self._flush_proxy_usage()
