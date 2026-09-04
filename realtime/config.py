from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULT_DISCOVERY_FEEDS: tuple[tuple[str, str], ...] = (
    (
        "google-news-rss",
        "https://news.google.com/rss/search?q={query}&hl=en-SG&gl=SG&ceid=SG:en",
    ),
)


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _discovery_feeds() -> tuple[tuple[str, str], ...]:
    raw = os.getenv("DISCOVERY_FEEDS_JSON", "").strip()
    if not raw:
        return DEFAULT_DISCOVERY_FEEDS
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("DISCOVERY_FEEDS_JSON must be valid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("DISCOVERY_FEEDS_JSON must be a JSON array")
    feeds: list[tuple[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each discovery feed must be an object")
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url.startswith(("http://", "https://")):
            raise ValueError("each discovery feed requires name and an HTTP(S) url")
        if "{" in url.replace("{query}", "") or "}" in url.replace("{query}", ""):
            raise ValueError("discovery feed URLs only support the {query} placeholder")
        host = (urlsplit(url).hostname or "").lower()
        if host != "news.google.com":
            continue
        feeds.append((name[:80], url))
    return tuple(dict.fromkeys(feeds))


@dataclass(frozen=True)
class Config:
    opensearch_url: str = os.getenv("OPENSEARCH_URL", "http://127.0.0.1:9201")
    index_name: str = os.getenv("OPENSEARCH_INDEX", "realtime-pages-v3")
    searxng_url: str = os.getenv("SEARXNG_URL", "http://127.0.0.1:8082")
    state_db: Path = Path(os.getenv("STATE_DB", "state/realtime-v2.db"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "20"))
    user_agent: str = os.getenv(
        "CRAWLER_USER_AGENT",
        "RealtimeResearchCrawler/1.0 (+http://localhost:8091)",
    )
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://realtime:realtime@127.0.0.1:5432/realtime"
    )
    valkey_url: str = os.getenv("VALKEY_URL", "redis://127.0.0.1:6379/0")
    proxy_api_base: str = os.getenv("PROXY_API_BASE", "https://proxyapi.pekpik.com")
    proxy_api_key: str = os.getenv("PRIVATE_READER_API_KEY", os.getenv("PROXY_API_KEY", ""))
    proxy_username: str = os.getenv("SHARED_PROXY_USERNAME", "")
    proxy_password: str = os.getenv("SHARED_PROXY_PASSWORD", "")
    proxy_cache_dir: Path = Path(os.getenv("PROXY_CACHE_DIR", "state/proxies"))
    proxy_sync_seconds: int = int(os.getenv("PROXY_SYNC_SECONDS", "1800"))
    proxy_sticky_seconds: int = int(os.getenv("PROXY_STICKY_SECONDS", "300"))
    proxy_selection_window: int = int(os.getenv("PROXY_SELECTION_WINDOW", "20"))
    proxy_sort: str = os.getenv("PROXY_SORT", "quality")
    default_proxy_profile: str = os.getenv("DEFAULT_PROXY_PROFILE", "private")
    crawler_slots: int = int(os.getenv("CRAWLER_SLOTS", "2"))
    crawler_concurrency: int = int(os.getenv("CRAWLER_CONCURRENCY", "32"))
    crawler_concurrency_per_domain: int = int(os.getenv("CRAWLER_CONCURRENCY_PER_DOMAIN", "4"))
    crawler_download_delay: float = float(os.getenv("CRAWLER_DOWNLOAD_DELAY", "0.1"))
    crawler_autothrottle_target: float = float(os.getenv("CRAWLER_AUTOTHROTTLE_TARGET", "3"))
    crawler_depth_limit: int = int(os.getenv("CRAWLER_DEPTH_LIMIT", "12"))
    discovery_pages: int = int(os.getenv("DISCOVERY_PAGES", "10"))
    max_links_per_page: int = int(os.getenv("MAX_LINKS_PER_PAGE", "100"))
    discovery_feeds: tuple[tuple[str, str], ...] = _discovery_feeds()
    trafilatura_enabled: bool = _enabled("TRAFILATURA_ENABLED")
    robots_bypass_domains: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("ROBOTS_BYPASS_DOMAINS", "").split(",")
        if value.strip()
    )
    whale_enabled: bool = _enabled("WHALE_ENABLED", False)
    whale_base_url: str = os.getenv("WHALE_BASE_URL", "http://20.169.21.11")
    whale_collector_api_key: str = os.getenv("WHALE_COLLECTOR_API_KEY", "")
    whale_agent_id: str = os.getenv("WHALE_AGENT_ID", "realtime-web-search-01")
    whale_dataset_id: str = os.getenv("WHALE_DATASET_ID", "web_raw")
    whale_source_platform: str = os.getenv("WHALE_SOURCE_PLATFORM", "google_search")
    whale_source_name: str = os.getenv("WHALE_SOURCE_NAME", "realtime-web-search")
    whale_supported_task_types: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv("WHALE_SUPPORTED_TASK_TYPES", "keyword_search").split(",") if value.strip()
    )
    whale_max_concurrency: int = int(os.getenv("WHALE_MAX_CONCURRENCY", "2"))
    whale_claim_limit: int = int(os.getenv("WHALE_CLAIM_LIMIT", "2"))
    whale_heartbeat_seconds: int = int(os.getenv("WHALE_HEARTBEAT_SECONDS", "20"))
    whale_ingest_batch_size: int = int(os.getenv("WHALE_INGEST_BATCH_SIZE", "50"))
