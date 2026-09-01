from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    opensearch_url: str = os.getenv("OPENSEARCH_URL", "http://127.0.0.1:9201")
    index_name: str = os.getenv("OPENSEARCH_INDEX", "realtime-pages-v2")
    searxng_url: str = os.getenv("SEARXNG_URL", "http://127.0.0.1:8082")
    state_db: Path = Path(os.getenv("STATE_DB", "state/realtime-v2.db"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "20"))
    user_agent: str = os.getenv(
        "CRAWLER_USER_AGENT",
        "RealtimeResearchCrawler/1.0 (+http://localhost:8091)",
    )
