from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

from .locales import SearchLocale, locale_for_language
from .proxy_pool import ProxyPool


_DISCOVERY_LIMITERS: dict[int, threading.BoundedSemaphore] = {}
_DISCOVERY_LIMITERS_LOCK = threading.Lock()
SERP_PARSER_VERSION = "google-serp-v2"
SERP_PARSE_MODES = frozenset({"fast", "light", "full"})
GOOGLE_TIME_FILTERS = frozenset({"qdr:h", "qdr:d", "qdr:w", "qdr:m", "qdr:y"})
BLOCKED_RESULT_DOMAINS = (
    "youtube.com", "youtu.be", "linkedin.com", "facebook.com", "reddit.com",
    "x.com", "instagram.com", "tiktok.com", "quora.com", "pinterest.com",
    "google.com", "baidu.com",
)


def _discovery_limiter(limit: int) -> threading.BoundedSemaphore:
    bounded = max(1, limit)
    with _DISCOVERY_LIMITERS_LOCK:
        return _DISCOVERY_LIMITERS.setdefault(bounded, threading.BoundedSemaphore(bounded))


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    engines: tuple[str, ...]
    rank: int | None = None
    description: str | None = None
    display_link: str | None = None
    source: str | None = None
    date: str | None = None
    serp_module: str = "web"


DATE_PREFIX = re.compile(
    r"^(?P<date>(?:\d+\s+(?:minutes?|hours?|days?|weeks?|months?|years?)\s+ago|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d+\s*(?:分钟|小时|天|周|个月|年)前))\s*(?:—|-)?\s*",
    re.IGNORECASE,
)


def serp_metadata(url: str, title: str, *, rank: int, description: str | None = None,
                  module: str = "web") -> dict[str, object]:
    """Normalize provider-neutral SERP fields without treating snippets as body text."""
    snippet = " ".join((description or "").split())[:1000] or None
    date = None
    if snippet:
        match = DATE_PREFIX.match(snippet)
        if match:
            date = match.group("date")
            snippet = snippet[match.end():].strip() or None
    host = (urlsplit(url).hostname or "").removeprefix("www.").lower()
    source = host.rsplit(".", 2)[0] if host.count(".") >= 1 else host
    return {
        "rank": rank, "description": snippet, "display_link": host or None,
        "source": source or None, "date": date, "serp_module": module,
    }


class GoogleBlocked(RuntimeError):
    def __init__(
        self, reason: str, status: int | None = None, *, captcha: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.captcha = captcha
        self.retry_after = retry_after


def normalize_query_audit(
    requested: str, *, effective: str = "", detected: str = "",
    spelling_correction: str = "",
) -> dict[str, object]:
    """Audit whether Google preserved the requested search semantics."""
    requested_norm = " ".join(requested.casefold().split())
    effective_norm = " ".join((effective or requested).casefold().split())
    detected_norm = " ".join((detected or effective_norm).casefold().split())
    mismatch = ""
    if requested_norm != effective_norm:
        mismatch = "request_transformed"
    elif detected_norm and detected_norm != requested_norm:
        mismatch = "result_page_query_changed"
    return {
        "requested_query": requested,
        "effective_query": effective or requested,
        "detected_query": detected or effective or requested,
        "spelling_correction": spelling_correction or None,
        "query_mismatch": mismatch or None,
    }


def field_extraction_stats(results: list[SearchResult]) -> dict[str, object]:
    total = len(results)
    if not total:
        return {"result_count": 0, "title_rate": 0.0, "description_rate": 0.0, "date_rate": 0.0}
    return {
        "result_count": total,
        "title_rate": round(sum(bool(row.title) for row in results) / total, 4),
        "description_rate": round(sum(bool(row.description) for row in results) / total, 4),
        "date_rate": round(sum(bool(row.date) for row in results) / total, 4),
    }


def error_scope(error_code: str, *, http_status: int | None = None) -> str:
    """Map failures to the component that owns the next action."""
    if error_code in {
        "google_proxy_unavailable", "google_transport_error", "google_timeout",
        "searxng_engine_timeout", "searxng_engine_error",
    }:
        return "proxy" if error_code.startswith("google_") else "provider"
    if error_code in {"google_unrecognized_page", "searxng_empty_unverified", "google_invalid_response"}:
        return "parser"
    if error_code in {"google_query_mismatch", "google_query_rejected", "google_query_cooling"}:
        return "query"
    if error_code == "google_web_circuit_open":
        return "global"
    if error_code.startswith("google_http_") or error_code in {"google_captcha", "google_consent"}:
        return "global" if http_status == 429 else "proxy"
    return "provider"


class SearchDiscovery:
    """Google-only discovery with durable caching and cross-process throttling hooks."""

    def __init__(
        self,
        timeout: int = 20,
        session: requests.Session | None = None,
        proxy_pool: ProxyPool | None = None,
        proxy_profile: str = "direct",
        language: str = "en",
        search_locale: SearchLocale | None = None,
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
        proxy_sticky_seconds: int = 0,
        proxy_cooldown_seconds: int = 300,
        source_cooldown_seconds: int = 1800,
        captcha_threshold: float = 0.02,
        web_query_eligible: bool = True,
        cache_get: Callable[..., dict[str, Any] | None] | None = None,
        cache_put: Callable[..., None] | None = None,
        source_slot_acquirer: Callable[[str, float], dict[str, Any]] | None = None,
        source_result_recorder: Callable[..., None] | None = None,
        proxy_reserver: Callable[[str, str, int], tuple[bool, float]] | None = None,
        proxy_group_reserver: Callable[..., tuple[bool, float]] | None = None,
        proxy_result_recorder: Callable[..., None] | None = None,
        proxy_profiles: tuple[str, ...] | None = None,
        novelty_counter: Callable[[list[str]], int] | None = None,
        page_batch_acquirer: Callable[..., dict[str, Any] | None] | None = None,
        page_result_recorder: Callable[..., bool] | None = None,
        providers: tuple[str, ...] = ("wml", "wml_direct", "searxng"),
        searxng_url: str = "http://127.0.0.1:8092",
        deep_cache_seconds: int = 86400,
        singleflight_acquirer: Callable[..., str | None] | None = None,
        singleflight_releaser: Callable[..., None] | None = None,
        singleflight_wait_seconds: float = 30.0,
        query_cooldown_seconds: int = 15,
        query_cooldown_checker: Callable[[str], dict[str, Any] | None] | None = None,
        query_cooldown_recorder: Callable[..., None] | None = None,
        persistent_browser_enabled: bool = False,
        persistent_browser_profile_root: str = "state/browser-profiles",
        persistent_browser_max_contexts: int = 4,
        persistent_browser_request_interval_seconds: int = 30,
        persistent_browser_max_requests_per_context: int = 100,
        persistent_browser_max_context_lifetime_seconds: int = 21600,
        persistent_browser_failure_threshold: int = 3,
        google_serp_save_html: bool = False,
        google_serp_evidence_dir: str = "state/serp-evidence",
        serp_attempt_recorder: Callable[..., None] | None = None,
        proxy_provider_attempts: int = 1,
        parse_mode: str = "light",
        time_filter: str = "",
        session_id: str = "",
    ):
        self.timeout = timeout
        self.proxy_pool = proxy_pool
        self.proxy_profile = proxy_profile
        self.proxy_profiles = tuple(proxy_profiles or [proxy_profile]) or ("direct",)
        self.language = language if language in {"en", "zh"} else "en"
        self.locale = "zh-CN" if self.language == "zh" else "en-SG"
        self.search_locale = search_locale or locale_for_language(self.language)
        if self.search_locale.language != self.language:
            raise ValueError("search locale language does not match search language")
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
        self.proxy_sticky_seconds = max(0, proxy_sticky_seconds)
        self.proxy_cooldown_seconds = max(1, proxy_cooldown_seconds)
        self.source_cooldown_seconds = max(1, source_cooldown_seconds)
        self.captcha_threshold = max(0.0, captcha_threshold)
        self.web_query_eligible = web_query_eligible
        self.cache_get = cache_get
        self.cache_put = cache_put
        self.source_slot_acquirer = source_slot_acquirer
        self.source_result_recorder = source_result_recorder
        self.proxy_reserver = proxy_reserver
        self.proxy_group_reserver = proxy_group_reserver
        self.proxy_result_recorder = proxy_result_recorder
        self.novelty_counter = novelty_counter
        self.page_batch_acquirer = page_batch_acquirer
        self.page_result_recorder = page_result_recorder
        self._proxy_usage: dict[tuple[str, str], int] = {}
        self._proxy_usage_lock = threading.Lock()
        allowed = {
            "searxng", "wml", "wml_direct", "curl", "curl_direct",
            "browser", "browser_direct", "persistent_browser",
        }
        if not providers or any(provider not in allowed for provider in providers):
            raise ValueError("invalid free Google providers")
        if "persistent_browser" in providers and not persistent_browser_enabled:
            raise ValueError("persistent_browser provider requires PERSISTENT_BROWSER_ENABLED=true")
        self.providers = tuple(dict.fromkeys(providers))
        if proxy_profile == "direct" and "wml_direct" in self.providers:
            self.providers = tuple(provider for provider in self.providers if provider != "wml")
        self.deep_cache_seconds = max(self.query_cache_seconds, deep_cache_seconds)
        self.singleflight_acquirer = singleflight_acquirer
        self.singleflight_releaser = singleflight_releaser
        self.singleflight_wait_seconds = max(0.0, singleflight_wait_seconds)
        self.query_cooldown_seconds = max(0, query_cooldown_seconds)
        self.query_cooldown_checker = query_cooldown_checker
        self.query_cooldown_recorder = query_cooldown_recorder
        from .free_google import GoogleTransport
        self.transport = GoogleTransport(
            timeout, self.language, searxng_url,
            search_locale=self.search_locale,
            persistent_browser_enabled=persistent_browser_enabled,
            persistent_browser_profile_root=persistent_browser_profile_root,
            persistent_browser_max_contexts=persistent_browser_max_contexts,
            persistent_browser_request_interval_seconds=persistent_browser_request_interval_seconds,
            persistent_browser_max_requests_per_context=persistent_browser_max_requests_per_context,
            persistent_browser_max_context_lifetime_seconds=persistent_browser_max_context_lifetime_seconds,
            persistent_browser_failure_threshold=persistent_browser_failure_threshold,
            google_serp_save_html=google_serp_save_html,
            google_serp_evidence_dir=google_serp_evidence_dir,
            parse_mode=parse_mode,
            time_filter=time_filter,
        )
        self.serp_attempt_recorder = serp_attempt_recorder
        # Zero means exhaust every currently available rotating proxy before
        # moving to a direct/shared-exit fallback.
        self.proxy_provider_attempts = max(0, proxy_provider_attempts)
        if self.proxy_provider_attempts == 0:
            self.proxy_provider_attempts = max(1, min(80, global_concurrency))
        self.attempts: list[dict[str, Any]] = []
        self._attempt_lock = threading.Lock()
        self._local_next_request = 0.0
        self._local_failures: dict[str, int] = {}
        self._local_cooldowns: dict[str, float] = {}
        self._query_cooldowns: dict[str, float] = {}
        self._profile_lock = threading.Lock()
        self._profile_cursor = 0
        if parse_mode not in SERP_PARSE_MODES:
            raise ValueError(f"parse_mode must be one of {','.join(sorted(SERP_PARSE_MODES))}")
        if time_filter and time_filter not in GOOGLE_TIME_FILTERS:
            raise ValueError("time_filter must be one of qdr:h,d,w,m,y")
        self.parse_mode = parse_mode
        self.time_filter = time_filter
        self.session_id = re.sub(r"[^A-Za-z0-9_.:-]", "-", session_id)[:128] if session_id else ""
        self.last_search_evidence: dict[str, Any] = {}
        self.discovery_global_concurrency = global_concurrency

    def _cache_key(
        self, source: str, query: str, locale: str, page: int = 1,
        *, time_filter: str = "",
    ) -> str:
        value = json.dumps(
            [source, query, locale, page, time_filter or self.time_filter],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _serialize(results: list[SearchResult]) -> list[dict[str, Any]]:
        fields = ("rank", "description", "display_link", "source", "date", "serp_module")
        return [
            {
                "url": row.url, "title": row.title, "engines": list(row.engines),
                **{field: getattr(row, field) for field in fields},
            }
            for row in results
        ]

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
                rank=int(row["rank"]) if row.get("rank") is not None else None,
                description=str(row["description"]) if row.get("description") else None,
                display_link=str(row["display_link"]) if row.get("display_link") else None,
                source=str(row["source"]) if row.get("source") else None,
                date=str(row["date"]) if row.get("date") else None,
                serp_module=str(row.get("serp_module") or "web"),
            ))
        return results

    @staticmethod
    def _project_results(
        results: list[SearchResult], *, include_metadata: bool,
    ) -> list[SearchResult]:
        """Keep fast mode focused on discovery instead of snippet extraction."""
        if include_metadata:
            return results
        return [
            SearchResult(row.url, row.title, row.engines, rank=row.rank)
            for row in results
        ]

    @staticmethod
    def project_serp_fields(
        results: list[SearchResult], *, include_metadata: bool,
    ) -> list[SearchResult]:
        return SearchDiscovery._project_results(
            results, include_metadata=include_metadata
        )

    def _cached(self, source: str, query: str, locale: str, page: int = 1) -> tuple[str, list[SearchResult] | None]:
        key = self._cache_key(source, query, locale, page)
        if not self.cache_get:
            self.last_search_evidence = {"cache_key": key, "cache_hit": False}
            return key, None
        row = self.cache_get(key, source)
        if not row:
            self.last_search_evidence = {"cache_key": key, "cache_hit": False}
            return key, None
        metadata = row.get("metadata") if isinstance(row, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        self.last_search_evidence = {
            **metadata,
            "parser_version": metadata.get("parser_version") or SERP_PARSER_VERSION,
            "cache_key": key,
            "cache_hit": True,
        }
        return key, self._deserialize(row.get("payload"))

    def _store_cache(
        self, key: str, source: str, query: str, locale: str, page: int,
        results: list[SearchResult], ttl: int, novel_count: int = 0,
        metadata: dict[str, Any] | None = None,
        response: requests.Response | None = None,
    ) -> None:
        if not self.cache_put:
            return
        self.cache_put(
            key, source, hashlib.sha256(query.encode()).hexdigest(), locale, page,
            self._serialize(results), ttl, novel_count=novel_count,
            metadata=metadata,
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
        rank = 0
        for anchor in soup.select("a[href]"):
            href = str(anchor.get("href") or "").strip()
            if href.startswith("/url?"):
                values = parse_qs(urlsplit(href).query)
                href = str((values.get("q") or values.get("url") or [""])[0])
            host = (urlsplit(href).hostname or "").lower()
            if (
                not href.startswith(("http://", "https://"))
                or any(
                    host == domain or host.endswith("." + domain)
                    for domain in BLOCKED_RESULT_DOMAINS
                )
            ):
                continue
            title_node = anchor.find(["h3", "h2"])
            if title_node:
                title = title_node.get_text(" ", strip=True)
                if title:
                    rank += 1
                    snippet_node = None
                    for parent in tuple(anchor.parents)[:5]:
                        snippet_node = parent.select_one(
                            "div.VwiC3b, div[data-sncf], span.aCOpRe, div.IsZvec"
                        )
                        if snippet_node:
                            break
                    description = snippet_node.get_text(" ", strip=True) if snippet_node else None
                    metadata = serp_metadata(href, title, rank=rank, description=description)
                    results.append(SearchResult(href, title, ("google_web",), **metadata))
        return results

    def _close_browser(self) -> None:
        self.transport.close_thread()

    def close(self) -> None:
        # Full shutdown, including shared persistent browser contexts.
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
                raise GoogleBlocked(
                    "google_web_circuit_open"
                    if source == "google_web" or source.startswith("google_web:")
                    else "google_provider_cooling"
                )
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

    def _select_proxy(self, provider: str) -> tuple[str | None, str, str]:
        if provider == "searxng" or provider.endswith("_direct") or self.proxy_profile == "direct":
            return None, "", "direct"
        if not self.proxy_pool:
            raise GoogleBlocked("google_proxy_unavailable")
        minimum_wait = None
        with self._profile_lock:
            start = self._profile_cursor % max(1, len(self.proxy_profiles))
            self._profile_cursor += 1
        profiles = self.proxy_profiles[start:] + self.proxy_profiles[:start]
        for profile in profiles:
            if profile == "direct":
                continue
            count = self.proxy_pool.available_count(profile, "www.google.com")
            for _ in range(max(1, count)):
                selected = self.proxy_pool.choose(
                    profile, "www.google.com", sticky_seconds=self.proxy_sticky_seconds,
                    sticky_key=self._proxy_sticky_key(), full_pool=True,
                )
                if not selected:
                    break
                url, key = selected
                if provider.startswith("browser") and not url.startswith("http://"):
                    self.proxy_pool.defer(key, "www.google.com", 60)
                    continue
                proxy_hash = hashlib.sha256(key.encode()).hexdigest()
                if self.proxy_group_reserver:
                    group, aliases = self.proxy_pool.google_identity(key)
                    allowed, wait = self.proxy_group_reserver(group, aliases, self.locale, self.proxy_min_interval_seconds)
                else:
                    allowed, wait = self.proxy_reserver(proxy_hash, self.locale, self.proxy_min_interval_seconds) if self.proxy_reserver else (True, 0)
                if allowed:
                    return url, key, profile
                minimum_wait = wait if minimum_wait is None else min(minimum_wait, wait)
                self.proxy_pool.defer(key, "www.google.com", min(max(wait, 0.1), self.proxy_cooldown_seconds))
        raise GoogleBlocked("google_proxy_unavailable", retry_after=minimum_wait)

    def _proxy_sticky_key(self) -> str:
        return f"google:{self.session_id or threading.get_ident()}"

    def _query_cooldown_key(self, query: str) -> str:
        value = json.dumps(
            [query, self.search_locale.cache_identity, self.time_filter],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def _assert_query_available(self, query: str, cooldown_key: str) -> None:
        if not self.query_cooldown_seconds:
            return
        wait = 0.0
        with self._attempt_lock:
            wait = max(0.0, self._query_cooldowns.get(cooldown_key, 0) - time.monotonic())
        if not wait and self.query_cooldown_checker:
            row = self.query_cooldown_checker(cooldown_key)
            if row:
                wait = max(0.0, float(row.get("retry_after") or 0))
        if wait:
            raise GoogleBlocked("google_query_cooling", retry_after=wait)

    def _record_query_cooldown(self, query: str, cooldown_key: str, reason: str) -> None:
        if not self.query_cooldown_seconds or reason not in {
            "google_query_mismatch", "google_query_rejected",
        }:
            return
        retry_after = float(self.query_cooldown_seconds)
        with self._attempt_lock:
            self._query_cooldowns[cooldown_key] = time.monotonic() + retry_after
        if self.query_cooldown_recorder:
            try:
                self.query_cooldown_recorder(
                    cooldown_key=cooldown_key, query=query,
                    locale_label=self.search_locale.label,
                    reason=reason, retry_after=retry_after,
                )
            except Exception:
                pass

    def _record_query_success(self, cooldown_key: str) -> None:
        with self._attempt_lock:
            self._query_cooldowns.pop(cooldown_key, None)

    def _acquire_singleflight(
        self, cache_key: str, source: str, query: str, locale: str, page: int,
    ) -> tuple[str | None, list[SearchResult] | None]:
        deadline = time.monotonic() + self.singleflight_wait_seconds
        while True:
            lease = self.singleflight_acquirer(cache_key) if self.singleflight_acquirer else ""
            if lease:
                return lease, None
            _, cached = self._cached(source, query, locale, page)
            if cached is not None:
                self.last_search_evidence = {
                    "cache_hit": True,
                    "parser_version": SERP_PARSER_VERSION,
                    "parse_mode": self.parse_mode,
                    "requested_query": query,
                    "effective_query": query,
                    "detected_query": query,
                    "time_filter": self.time_filter or None,
                }
                return None, cached
            if time.monotonic() >= deadline:
                raise GoogleBlocked("google_query_inflight", retry_after=0.1)
            time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        raise GoogleBlocked("google_query_inflight", retry_after=0.1)

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
        proxy_url, proxy_key, selected_profile = self._select_proxy(provider)
        self._reserve_source(f"google_web:{selected_profile}")
        proxy_hash = hashlib.sha256(proxy_key.encode()).hexdigest() if proxy_key else ""
        if proxy_hash:
            with self._proxy_usage_lock:
                key = (selected_profile, proxy_hash)
                self._proxy_usage[key] = self._proxy_usage.get(key, 0) + 1
        started = time.monotonic()
        results: list[SearchResult] = []
        failure: Exception | None = None
        try:
            with self.global_limiter:
                results = self.transport.fetch(provider, query, page, proxy_url, proxy_key=proxy_key)
            results = self._project_results(
                results, include_metadata=self.parse_mode != "fast"
            )
            evidence = dict(self.transport.last_evidence)
            audit = normalize_query_audit(
                query,
                effective=str(evidence.get("effective_query") or query),
                detected=str(evidence.get("detected_query") or ""),
                spelling_correction=str(evidence.get("spelling_correction") or ""),
            )
            if audit["query_mismatch"] and results:
                raise GoogleBlocked("google_query_mismatch")
            return results
        except Exception as exc:
            failure = exc
            raise GoogleBlocked(self._error_code(exc), captcha=isinstance(exc, GoogleBlocked) and exc.captcha) from None
        finally:
            elapsed = time.monotonic() - started
            code = self._error_code(failure) if failure else ""
            captcha = isinstance(failure, GoogleBlocked) and failure.captcha
            limited = captcha or code in {"google_http_403", "google_http_429", "google_javascript_required", "google_consent"}
            evidence = dict(self.transport.last_evidence)
            query_audit = normalize_query_audit(
                query,
                effective=str(evidence.get("effective_query") or query),
                detected=str(evidence.get("detected_query") or ""),
                spelling_correction=str(evidence.get("spelling_correction") or ""),
            )
            if failure is None and query_audit["query_mismatch"] and results:
                failure = GoogleBlocked(str(query_audit["query_mismatch"]))
                code = str(query_audit["query_mismatch"])
            classification = evidence.get("classification") or (
                ("results" if results else "empty") if failure is None
                else "timeout" if code == "google_timeout"
                else "http_error" if code.startswith("google_http_") else "error"
            )
            with self._attempt_lock:
                streak = self._local_failures.get(provider, 0) + 1 if failure else 0
                self._local_failures[provider] = streak
                # A failed rotating proxy is quarantined below; it must not
                # cool the whole provider and prevent trying another exit.
                if not proxy_hash and (streak >= 3 or captcha):
                    self._local_cooldowns[provider] = time.monotonic() + self.source_cooldown_seconds
                self.attempts.append({
                    "provider": provider, "query": query, "page": page,
                    "locale_label": self.search_locale.label,
                    "success": failure is None, "results": len(results),
                    "seconds": round(elapsed, 3), "error": code,
                    "error_scope": error_scope(code, http_status=evidence.get("http_status")),
                    "parser_version": evidence.get("parser_version") or SERP_PARSER_VERSION,
                    "parse_mode": evidence.get("parse_mode") or self.parse_mode,
                    "requested_query": query_audit["requested_query"],
                    "effective_query": query_audit["effective_query"],
                    "detected_query": query_audit["detected_query"],
                    "spelling_correction": query_audit["spelling_correction"],
                    "query_mismatch": query_audit["query_mismatch"],
                    "session_id": self.session_id or None,
                    "field_extraction": field_extraction_stats(results),
                    "proxy_hash": proxy_hash,
                    "proxy_profile": selected_profile,
                    "http_status": evidence.get("http_status"),
                    "page_classification": classification,
                    "raw_sha256": evidence.get("raw_sha256"),
                    "raw_html_path": evidence.get("raw_html_path"),
                    "request_url": evidence.get("request_url"),
                    "headless": evidence.get("headless"),
                    "time_filter": self.time_filter or None,
                })
            if self.serp_attempt_recorder:
                try:
                    self.serp_attempt_recorder(
                        provider=provider, query=query, page=page,
                        locale_label=self.search_locale.label,
                        proxy_key_hash=proxy_hash or None,
                        proxy_profile=selected_profile,
                        request_url=evidence.get("request_url"),
                        http_status=evidence.get("http_status"),
                        result_count=len(results), elapsed_seconds=round(elapsed, 3),
                        classification=classification, error_code=code or None,
                        raw_sha256=evidence.get("raw_sha256"),
                        raw_html_path=evidence.get("raw_html_path"),
                        headless=evidence.get("headless"),
                        error_scope=error_scope(code, http_status=evidence.get("http_status")),
                        parser_version=evidence.get("parser_version") or SERP_PARSER_VERSION,
                        parse_mode=evidence.get("parse_mode") or self.parse_mode,
                        requested_query=query_audit["requested_query"],
                        effective_query=query_audit["effective_query"],
                        detected_query=query_audit["detected_query"],
                        spelling_correction=query_audit["spelling_correction"],
                        query_mismatch=query_audit["query_mismatch"],
                        field_extraction=json.dumps(field_extraction_stats(results), ensure_ascii=False),
                        session_id=self.session_id or None,
                    )
                except Exception:
                    pass
            if proxy_key and self.proxy_pool:
                # A valid HTTP 200 envelope with an unknown result layout does
                # not establish a broken exit. Keep the page failed, but avoid
                # quarantining healthy exits for five minutes on sparse queries.
                parse_only_failure = code == 'google_unrecognized_page' and evidence.get('http_status') == 200
                proxy_delay = self.proxy_cooldown_seconds if limited else (
                    max(30, self.proxy_min_interval_seconds) if parse_only_failure else 300 if failure else 0)
                if failure:
                    self.proxy_pool.defer(proxy_key, "www.google.com", proxy_delay)
                if self.proxy_result_recorder:
                    self.proxy_result_recorder(
                        proxy_hash, success=failure is None,
                        cooldown_seconds=proxy_delay,
                        error_code=code,
                        elapsed_seconds=elapsed,
                        result_count=len(results),
                        http_status=evidence.get("http_status"),
                    )
            if self.source_result_recorder:
                novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter and results else len(results)
                for name in (f"google_web:{selected_profile}", f"{source}:{selected_profile}"):
                    self.source_result_recorder(
                        name, success=failure is None,
                        limited=limited and not bool(proxy_hash),
                        captcha=captcha and not bool(proxy_hash),
                        result_count=len(results), novel_count=novel,
                        maximum_rps=self.google_web_max_rps, captcha_threshold=self.captcha_threshold,
                        source_cooldown_seconds=self.source_cooldown_seconds,
                        error_code=code, elapsed_seconds=elapsed,
                        shared_exit=not bool(proxy_hash),
                    )

    def _discover_google_page(self, query: str, page: int) -> list[SearchResult]:
        cache_locale = self.search_locale.cache_identity + ":free-v1:" + ",".join(self.providers)
        cache_key, cached = self._cached("google_web", query, cache_locale, page)
        if cached is not None:
            return cached
        cooldown_key = self._query_cooldown_key(query)
        self._assert_query_available(query, cooldown_key)
        lease = None
        if self.singleflight_acquirer:
            lease, cached = self._acquire_singleflight(
                cache_key, "google_web", query, cache_locale, page,
            )
            if cached is not None:
                return cached
        self.last_search_evidence = {
            "parser_version": SERP_PARSER_VERSION,
            "parse_mode": self.parse_mode,
            "cache_key": cache_key,
            "cache_hit": False,
            "requested_query": query,
            "effective_query": query,
            "detected_query": query,
            "query_mismatch": None,
            "session_id": self.session_id or None,
            "time_filter": self.time_filter or None,
        }
        try:
            # Another process may have filled the cache before our lease was acquired.
            if lease:
                _, cached = self._cached("google_web", query, cache_locale, page)
                if cached is not None:
                    self.last_search_evidence = dict(self.last_search_evidence) | {
                        "cache_hit": True,
                    }
                    return cached
            errors = []
            # An empty page is confirmed by at most one backup channel before
            # being cached; a parse failure raises instead and is never cached
            # as "no results".
            empty_seen = False
            for provider in self.providers:
                proxy_provider = not (
                    provider == "searxng" or provider.endswith("_direct")
                    or self.proxy_profile == "direct"
                )
                attempts = self.proxy_provider_attempts if proxy_provider else 1
                results = None
                attempt = 0
                while attempts == 0 or attempt < attempts:
                    attempt += 1
                    try:
                        results = self._attempt(provider, query, page)
                        break
                    except GoogleBlocked as exc:
                        errors.append(exc)
                        if exc.reason == "google_web_circuit_open":
                            raise
                        if exc.reason == "google_proxy_unavailable":
                            break
                        if exc.reason not in {
                            "google_captcha", "google_http_403", "google_http_429",
                            "google_consent",
                        }:
                            break
                if results is None:
                    continue
                if not results and not empty_seen and provider != self.providers[-1]:
                    empty_seen = True
                    continue
                novel = self.novelty_counter([row.url for row in results]) if self.novelty_counter else len(results)
                ttl = self.query_cache_seconds if page <= 3 else self.deep_cache_seconds
                evidence = dict(self.last_search_evidence)
                evidence.update(self.transport.last_evidence)
                evidence.update(field_extraction_stats(results))
                self._store_cache(
                    cache_key, "google_web", query, cache_locale, page, results, ttl, novel,
                    metadata=evidence,
                )
                self._record_query_success(cooldown_key)
                return results
            # Preserve actionable failure over a later skipped/cooling provider.
            substantive = [exc for exc in errors if exc.reason != "google_provider_cooling"]
            failure = (substantive or errors)[-1]
            self._record_query_cooldown(query, cooldown_key, failure.reason)
            raise failure
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
                        query, self.search_locale.label, page_limit, self.google_web_pages_per_batch
                    )
                except Exception as exc:
                    errors.append(f"google frontier acquire {type(exc).__name__}")
                if batch:
                    page_start = max(1, int(batch["start_page"]))
                    page_end = min(page_limit, int(batch["end_page"]))
                else:
                    page_start, page_end = 1, 0
            pages = tuple(range(page_start, page_end + 1))
            # A frontier lease owns a page batch, not a serial page cursor. Run
            # the batch concurrently so one page cannot leave the global RPS
            # limiter idle while unrelated workers also have runnable pages.
            page_workers = max(1, min(self.google_web_pages_per_batch, len(pages)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=page_workers) as page_executor:
                futures = {
                    page: page_executor.submit(self._discover_google_page, query, page)
                    for page in pages
                }
                for page, future in futures.items():
                    try:
                        page_results = future.result()
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
                        metadata = {
                            field: getattr(previous or result, field)
                            for field in ("rank", "description", "display_link", "source", "date", "serp_module")
                        }
                        found[result.url] = SearchResult(
                            result.url, previous.title if previous else result.title,
                            tuple(dict.fromkeys((*(previous.engines if previous else ()), *result.engines))),
                            **metadata,
                        )
                    errors.extend(f"{query}: {value}" for value in query_errors)
            return list(found.values()), list(dict.fromkeys(errors))
        finally:
            self._flush_proxy_usage()
