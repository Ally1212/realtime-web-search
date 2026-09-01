from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    default_proxy_profile: str = os.getenv("DEFAULT_PROXY_PROFILE", "private")
    crawler_slots: int = int(os.getenv("CRAWLER_SLOTS", "2"))
    crawler_concurrency: int = int(os.getenv("CRAWLER_CONCURRENCY", "32"))
    crawler_concurrency_per_domain: int = int(os.getenv("CRAWLER_CONCURRENCY_PER_DOMAIN", "4"))
    crawler_download_delay: float = float(os.getenv("CRAWLER_DOWNLOAD_DELAY", "0.1"))
    crawler_autothrottle_target: float = float(os.getenv("CRAWLER_AUTOTHROTTLE_TARGET", "3"))
    crawler_depth_limit: int = int(os.getenv("CRAWLER_DEPTH_LIMIT", "12"))
    discovery_pages: int = int(os.getenv("DISCOVERY_PAGES", "10"))
    max_links_per_page: int = int(os.getenv("MAX_LINKS_PER_PAGE", "100"))
    robots_bypass_domains: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("ROBOTS_BYPASS_DOMAINS", "").split(",")
        if value.strip()
    )
