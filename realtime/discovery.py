from __future__ import annotations

import concurrent.futures
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

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
        providers: tuple[str, ...] = ("wml", "wml_direct", "searxng"),
        searxng_url: str = "http://127.0.0.1:8092",
        deep_cache_seconds: int = 86400,
        singleflight_acquirer: Callable[..., str | None] | None = None,
        singleflight_releaser: Callable[..., None] | None = None,
    ):
        self.timeout = timeout
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
        allowed = {"searxng", "wml", "wml_direct", "curl", "curl_direct", "browser", "browser_direct"}
        if not providers or any(provider not in allowed for provider in providers):
            raise ValueError("invalid free Google providers")
        self.providers = tuple(dict.fromkeys(providers))
        if proxy_profile == "direct" and "wml_direct" in self.providers:
            self.providers = tuple(provider for provider in self.providers if provider != "wml")
        self.deep_cache_seconds = max(self.query_cache_seconds, deep_cache_seconds)
        self.singleflight_acquirer = singleflight_acquirer
        self.singleflight_releaser = singleflight_releaser
        from .free_google import GoogleTransport
        self.transport = GoogleTransport(timeout, self.language, searxng_url)
        self.attempts: list[dict[str, Any]] = []
        self._attempt_lock = threading.Lock()
        self._local_next_request = 0.0
        self._local_failures: dict[str, int] = {}
        self._local_cooldowns: dict[str, float] = {}

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

    def _close_browser(self) -> None:
        self.transport.close()

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

    def _reserve_source(self, source: str) -> None:
        if self.source_slot_acquirer:
            slot = self.source_slot_acquirer(source, self.google_web_initial_rps)
            if not slot.get("allowed"):
                raise GoogleBlocked("google_web_circuit_open" if source == "google_web" else "google_provider_cooling")
            wait = float(slot.get("wait") or 0)
        elif source == "google_web":
            with self._attempt_lock:
                now = time.monotonic()
                wait = max(0.0, self._local_next_request - now)
                self._local_next_request = now + wait + 1 / self.google_web_initial_rps
        else:
            wait = 0
        if wait > 0:
            time.sleep(wait)

    def _select_proxy(self, provider: str) -> tuple[str | None, str]:
        if provider == "searxng" or provider.endswith("_direct") or self.proxy_profile == "direct":
            return None, ""
        if not self.proxy_pool:
            raise GoogleBlocked("google_proxy_unavailable")
        for _ in range(64):
            selected = self.proxy_pool.choose(
                self.proxy_profile, "www.google.com", sticky_seconds=120,
                sticky_key=f"google:{threading.get_ident()}", full_pool=True,
            )
            if not selected:
                break
            url, key = selected
            if provider.startswith("browser") and not url.startswith("http://"):
                self.proxy_pool.defer(key, "www.google.com", 60)
                continue
            proxy_hash = hashlib.sha256(key.encode()).hexdigest()
            allowed, wait = self.proxy_reserver(proxy_hash, self.locale, self.proxy_min_interval_seconds) if self.proxy_reserver else (True, 0)
            if allowed:
                return url, key
            self.proxy_pool.defer(key, "www.google.com", min(max(wait, 0.1), self.proxy_cooldown_seconds))
        raise GoogleBlocked("google_proxy_unavailable")

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, GoogleBlocked):
            return exc.reason
        name = type(exc).__name__.lower()
        if "timeout" in name or getattr(exc, "code", None) == 28:
            return "google_timeout"
        if isinstance(exc, (ValueError, KeyError)):
            return "google_invalid_response"
        return "google_transport_error"

    def _attempt(self, provider: str, query: str, page: int) -> list[SearchResult]:
        source = f"google_{provider}"
        with self._attempt_lock:
            if self._local_cooldowns.get(provider, 0) > time.monotonic():
                raise GoogleBlocked("google_provider_cooling")
        self._reserve_source(source)
        self._reserve_source("google_web")
        proxy_url, proxy_key = self._select_proxy(provider)
        proxy_hash = hashlib.sha256(proxy_key.encode()).hexdigest() if proxy_key else ""
        if proxy_hash:
            with self._proxy_usage_lock:
                key = (self.proxy_profile, proxy_hash)
                self._proxy_usage[key] = self._proxy_usage.get(key, 0) + 1
        started = time.monotonic()
        results: list[SearchResult] = []
        failure: Exception | None = None
        try:
            with self.global_limiter:
                results = self.transport.fetch(provider, query, page, proxy_url)
            return results
        except Exception as exc:
            failure = exc
            raise GoogleBlocked(self._error_code(exc), captcha=isinstance(exc, GoogleBlocked) and exc.captcha) from None
        finally:
            elapsed = time.monotonic() - started
            code = self._error_code(failure) if failure else ""
            captcha = isinstance(failure, GoogleBlocked) and failure.captcha
            limited = captcha or code in {"google_http_403", "google_http_429", "google_javascript_required", "google_consent"}
            with self._attempt_lock:
                streak = self._local_failures.get(provider, 0) + 1 if failure else 0
                self._local_failures[provider] = streak
                if streak >= 3 or (captcha and not proxy_hash):
                    self._local_cooldowns[provider] = time.monotonic() + self.source_cooldown_seconds
                self.attempts.append({
                    "provider": provider, "query": query, "page": page,
                    "success": failure is None, "results": len(results),
                    "seconds": round(elapsed, 3), "error": code,
                })
            if proxy_key and self.proxy_pool:
                if failure:
                    self.proxy_pool.defer(proxy_key, "www.google.com", self.proxy_cooldown_seconds if limited else 300)
                if self.proxy_result_recorder:
                    self.proxy_result_recorder(
                        proxy_hash, success=failure is None,
                        cooldown_seconds=self.proxy_cooldown_seconds if limited else (300 if failure else 0),
                    )
            if self.source_result_recorder:
                novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter and results else len(results)
                for name in ("google_web", source):
                    self.source_result_recorder(
                        name, success=failure is None, limited=limited, captcha=captcha,
                        result_count=len(results), novel_count=novel,
                        maximum_rps=self.google_web_max_rps, captcha_threshold=self.captcha_threshold,
                        source_cooldown_seconds=self.source_cooldown_seconds,
                        error_code=code, elapsed_seconds=elapsed,
                        shared_exit=not bool(proxy_hash),
                    )

    def _discover_google_page(self, query: str, page: int) -> list[SearchResult]:
        cache_locale = self.locale + ":free-v1:" + ",".join(self.providers)
        cache_key, cached = self._cached("google_web", query, cache_locale, page)
        if cached is not None:
            return cached
        lease = None
        if self.singleflight_acquirer:
            lease = self.singleflight_acquirer(cache_key)
            if not lease:
                raise GoogleBlocked("google_query_inflight")
        try:
            # Another process may have filled the cache before our lease was acquired.
            if lease:
                _, cached = self._cached("google_web", query, cache_locale, page)
                if cached is not None:
                    return cached
            errors = []
            for provider in self.providers:
                try:
                    results = self._attempt(provider, query, page)
                except GoogleBlocked as exc:
                    errors.append(exc)
                    if exc.reason == "google_web_circuit_open":
                        raise
                    continue
                novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter else len(results)
                ttl = self.query_cache_seconds if page <= 3 else self.deep_cache_seconds
                self._store_cache(cache_key, "google_web", query, cache_locale, page, results, ttl, novel)
                return results
            # Preserve actionable failure over a later skipped/cooling provider.
            substantive = [exc for exc in errors if exc.reason != "google_provider_cooling"]
            raise (substantive or errors)[-1]
        finally:
            if lease and self.singleflight_releaser:
                self.singleflight_releaser(cache_key, lease)

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
