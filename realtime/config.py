from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from .whale_protocol import SUPPORTED_CAPABILITIES, SUPPORTED_TASK_TYPES

DEFAULT_CONTINUOUS_AI_KEYWORDS = (
    "中国经济,GDP,通货膨胀,美联储,人民币汇率"
)

DEFAULT_CONTINUOUS_AI_EXPANSIONS = (
    "货币政策,财政政策,A股,房地产市场,关税,供应链,"
    "黄金价格,原油价格,企业财报,货币政策 降息"
)


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value.strip()
            for value in os.getenv(name, default).split(",")
            if value.strip()
        )
    )


def _int_csv(name: str, default: str) -> tuple[int, ...]:
    values: list[int] = []
    for value in os.getenv(name, default).split(","):
        value = value.strip()
        if value:
            values.append(int(value))
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class Config:
    opensearch_url: str = os.getenv("OPENSEARCH_URL", "http://127.0.0.1:9201")
    index_name: str = os.getenv("OPENSEARCH_INDEX", "realtime-pages-v3")
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
    static_proxies: tuple[str, ...] = _csv("STATIC_PROXIES", "")
    proxy_username: str = os.getenv("SHARED_PROXY_USERNAME", "")
    proxy_password: str = os.getenv("SHARED_PROXY_PASSWORD", "")
    proxy_cache_dir: Path = Path(os.getenv("PROXY_CACHE_DIR", "state/proxies"))
    proxy_sync_seconds: int = int(os.getenv("PROXY_SYNC_SECONDS", "1800"))
    proxy_sticky_seconds: int = int(os.getenv("PROXY_STICKY_SECONDS", "0"))
    google_proxy_sticky_seconds: int = int(os.getenv("GOOGLE_PROXY_STICKY_SECONDS", "120"))
    proxy_selection_window: int = int(os.getenv("PROXY_SELECTION_WINDOW", "20"))
    proxy_sort: str = os.getenv("PROXY_SORT", "quality")
    default_proxy_profile: str = os.getenv("DEFAULT_PROXY_PROFILE", "private")
    google_proxy_profiles: tuple[str, ...] = _csv(
        "GOOGLE_PROXY_PROFILES", "private,public_google,public"
    )
    crawler_slots: int = int(os.getenv("CRAWLER_SLOTS", "2"))
    crawler_concurrency: int = int(os.getenv("CRAWLER_CONCURRENCY", "32"))
    crawler_concurrency_per_domain: int = int(os.getenv("CRAWLER_CONCURRENCY_PER_DOMAIN", "4"))
    crawler_download_delay: float = float(os.getenv("CRAWLER_DOWNLOAD_DELAY", "0.1"))
    crawler_autothrottle_enabled: bool = _enabled("CRAWLER_AUTOTHROTTLE_ENABLED", True)
    crawler_autothrottle_target: float = float(os.getenv("CRAWLER_AUTOTHROTTLE_TARGET", "3"))
    crawler_retry_http_codes: tuple[int, ...] = _int_csv("CRAWLER_RETRY_HTTP_CODES", "408,425,429,500,502,503,504")
    crawler_depth_limit: int = int(os.getenv("CRAWLER_DEPTH_LIMIT", "12"))
    crawler_min_content_chars: int = int(os.getenv("CRAWLER_MIN_CONTENT_CHARS", "100"))
    browser_fallback_enabled: bool = _enabled("BROWSER_FALLBACK_ENABLED", True)
    browser_fallback_ratio: float = float(os.getenv("BROWSER_FALLBACK_RATIO", "0.05"))
    browser_max_pages: int = int(os.getenv("BROWSER_MAX_PAGES", "2"))
    browser_timeout_ms: int = int(os.getenv("BROWSER_TIMEOUT_MS", "15000"))
    discovery_pages: int = int(os.getenv("DISCOVERY_PAGES", "20"))
    discovery_global_concurrency: int = int(os.getenv("DISCOVERY_GLOBAL_CONCURRENCY", "24"))
    discovery_query_concurrency: int = int(os.getenv("DISCOVERY_QUERY_CONCURRENCY", "4"))
    discovery_history_start: date = date.fromisoformat(
        os.getenv("DISCOVERY_HISTORY_START", "2015-01-01")
    )
    discovery_window_days: int = int(os.getenv("DISCOVERY_WINDOW_DAYS", "7"))
    discovery_min_novelty_ratio: float = float(
        os.getenv("DISCOVERY_MIN_NOVELTY_RATIO", "0.05")
    )
    discovery_exhausted_cooldown_seconds: int = int(
        os.getenv("DISCOVERY_EXHAUSTED_COOLDOWN_SECONDS", "21600")
    )
    max_links_per_page: int = int(os.getenv("MAX_LINKS_PER_PAGE", "100"))
    google_web_enabled: bool = _enabled("GOOGLE_WEB_ENABLED", True)
    google_free_providers: tuple[str, ...] = _csv("GOOGLE_FREE_PROVIDERS", "wml,wml_direct,searxng")
    searxng_url: str = os.getenv("SEARXNG_URL", "http://127.0.0.1:8092")
    google_web_deep_cache_seconds: int = int(os.getenv("GOOGLE_WEB_DEEP_CACHE_SECONDS", "86400"))
    google_web_initial_rps: float = float(os.getenv("GOOGLE_WEB_INITIAL_RPS", "0.5"))
    google_web_max_rps: float = float(os.getenv("GOOGLE_WEB_MAX_RPS", "2"))
    google_web_burst: int = int(os.getenv("GOOGLE_WEB_BURST", "1"))
    google_web_max_pages: int = int(os.getenv("GOOGLE_WEB_MAX_PAGES", "11"))
    google_web_pages_per_batch: int = int(os.getenv("GOOGLE_WEB_PAGES_PER_BATCH", "3"))
    google_web_query_cache_seconds: int = int(
        os.getenv("GOOGLE_WEB_QUERY_CACHE_SECONDS", "21600")
    )
    google_singleflight_wait_seconds: float = float(
        os.getenv("GOOGLE_SINGLEFLIGHT_WAIT_SECONDS", "30")
    )
    google_query_cooldown_seconds: int = int(
        os.getenv("GOOGLE_QUERY_COOLDOWN_SECONDS", "15")
    )
    google_serp_parse_mode: str = os.getenv("GOOGLE_SERP_PARSE_MODE", "light")
    google_web_proxy_min_interval_seconds: int = int(
        os.getenv("GOOGLE_WEB_PROXY_MIN_INTERVAL_SECONDS", "30")
    )
    google_web_proxy_cooldown_seconds: int = int(
        os.getenv("GOOGLE_WEB_PROXY_COOLDOWN_SECONDS", "300")
    )
    google_web_proxy_cooldown_cap_seconds: int = int(
        os.getenv("GOOGLE_WEB_PROXY_COOLDOWN_CAP_SECONDS", "7200")
    )
    google_web_circuit_probe_rps: float = float(
        os.getenv("GOOGLE_WEB_CIRCUIT_PROBE_RPS", "0.05")
    )
    google_web_circuit_max_seconds: int = int(
        os.getenv("GOOGLE_WEB_CIRCUIT_MAX_SECONDS", "7200")
    )
    google_web_source_cooldown_seconds: int = int(
        os.getenv("GOOGLE_WEB_SOURCE_COOLDOWN_SECONDS", "1800")
    )
    google_web_captcha_threshold: float = float(
        os.getenv("GOOGLE_WEB_CAPTCHA_THRESHOLD", "0.02")
    )
    persistent_browser_enabled: bool = _enabled("PERSISTENT_BROWSER_ENABLED", False)
    persistent_browser_profile_root: Path = Path(
        os.getenv("PERSISTENT_BROWSER_PROFILE_ROOT", "state/browser-profiles")
    )
    persistent_browser_max_contexts: int = int(os.getenv("PERSISTENT_BROWSER_MAX_CONTEXTS", "4"))
    persistent_browser_request_interval_seconds: int = int(
        os.getenv("PERSISTENT_BROWSER_REQUEST_INTERVAL_SECONDS", "30")
    )
    persistent_browser_max_requests_per_context: int = int(
        os.getenv("PERSISTENT_BROWSER_MAX_REQUESTS_PER_CONTEXT", "100")
    )
    persistent_browser_max_context_lifetime_seconds: int = int(
        os.getenv("PERSISTENT_BROWSER_MAX_CONTEXT_LIFETIME_SECONDS", "21600")
    )
    persistent_browser_failure_threshold: int = int(
        os.getenv("PERSISTENT_BROWSER_FAILURE_THRESHOLD", "3")
    )
    google_serp_save_html: bool = _enabled("GOOGLE_SERP_SAVE_HTML", False)
    google_serp_evidence_dir: Path = Path(
        os.getenv("GOOGLE_SERP_EVIDENCE_DIR", "state/serp-evidence")
    )
    trafilatura_enabled: bool = _enabled("TRAFILATURA_ENABLED")
    robots_bypass_domains: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("ROBOTS_BYPASS_DOMAINS", "").split(",")
        if value.strip()
    )
    crawler_obey_robots: bool = _enabled("CRAWLER_OBEY_ROBOTS", True)
    whale_enabled: bool = _enabled("WHALE_ENABLED", False)
    whale_base_url: str = os.getenv("WHALE_BASE_URL", "http://20.169.21.11")
    whale_collector_api_key: str = os.getenv("WHALE_COLLECTOR_API_KEY", "")
    whale_agent_id: str = os.getenv("WHALE_AGENT_ID", "realtime-web-search-01")
    whale_dataset_id: str = os.getenv("WHALE_DATASET_ID", "web_raw")
    whale_source_platform: str = os.getenv("WHALE_SOURCE_PLATFORM", "google_search")
    whale_source_name: str = os.getenv("WHALE_SOURCE_NAME", "realtime-web-search")
    whale_supported_task_types: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv(
            "WHALE_SUPPORTED_TASK_TYPES", ",".join(SUPPORTED_TASK_TYPES)
        ).split(",") if value.strip()
    )
    whale_declared_capabilities: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv(
            "WHALE_DECLARED_CAPABILITIES", ",".join(SUPPORTED_CAPABILITIES)
        ).split(",") if value.strip()
    )
    whale_max_concurrency: int = int(os.getenv("WHALE_MAX_CONCURRENCY", "2"))
    whale_claim_limit: int = int(os.getenv("WHALE_CLAIM_LIMIT", "2"))
    whale_heartbeat_seconds: int = int(os.getenv("WHALE_HEARTBEAT_SECONDS", "20"))
    whale_ingest_batch_size: int = int(os.getenv("WHALE_INGEST_BATCH_SIZE", "50"))
    whale_verify_url_template: str = os.getenv("WHALE_VERIFY_URL_TEMPLATE", "")
    continuous_whale_enabled: bool = _enabled("CONTINUOUS_WHALE_ENABLED", False)
    continuous_date_slicing_enabled: bool = _enabled(
        "CONTINUOUS_DATE_SLICING_ENABLED", False
    )
    continuous_ai_keywords: tuple[str, ...] = _csv(
        "CONTINUOUS_AI_KEYWORDS",
        DEFAULT_CONTINUOUS_AI_KEYWORDS,
    )
    continuous_keyword_expansions: tuple[str, ...] = _csv(
        "CONTINUOUS_KEYWORD_EXPANSIONS",
        DEFAULT_CONTINUOUS_AI_EXPANSIONS,
    )
    continuous_expand_keywords: bool = _enabled("CONTINUOUS_EXPAND_KEYWORDS", True)
    continuous_interval_seconds: int = int(os.getenv("CONTINUOUS_INTERVAL_SECONDS", "60"))
    continuous_max_items_per_keyword: int = int(os.getenv("CONTINUOUS_MAX_ITEMS_PER_KEYWORD", "1000000"))
    # 0 means unlimited continuous collection.
    continuous_daily_target: int = int(os.getenv("CONTINUOUS_DAILY_TARGET", "0"))
    continuous_keyword_concurrency: int = int(os.getenv("CONTINUOUS_KEYWORD_CONCURRENCY", "8"))
    continuous_keyword_concurrency_max: int = int(os.getenv("CONTINUOUS_KEYWORD_CONCURRENCY_MAX", "12"))
    continuous_keywords_per_round: int = int(os.getenv("CONTINUOUS_KEYWORDS_PER_ROUND", "24"))
    adaptive_concurrency_enabled: bool = _enabled("ADAPTIVE_CONCURRENCY_ENABLED", True)
    adaptive_evaluation_seconds: int = int(os.getenv("ADAPTIVE_EVALUATION_SECONDS", "300"))
    continuous_proxy_profile: str = os.getenv("CONTINUOUS_PROXY_PROFILE", "direct")
    continuous_executor: str = os.getenv("CONTINUOUS_EXECUTOR", "persistent")
    persistent_max_crawls: int = int(os.getenv("PERSISTENT_MAX_CRAWLS", "12"))
    outbox_flush_seconds: float = float(os.getenv("OUTBOX_FLUSH_SECONDS", "1"))
    outbox_max_pending: int = int(os.getenv("OUTBOX_MAX_PENDING", "5000"))

    def __post_init__(self) -> None:
        # Enabled persistent Chrome is always tried first; when disabled the
        # historical wml -> wml_direct -> searxng order is untouched.
        if self.persistent_browser_enabled and "persistent_browser" not in self.google_free_providers:
            object.__setattr__(
                self, "google_free_providers",
                ("persistent_browser", *self.google_free_providers),
            )
