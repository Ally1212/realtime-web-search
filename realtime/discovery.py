from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    engines: tuple[str, ...]


class SearchDiscovery:
    def __init__(self, base_url: str, timeout: int = 20, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def discover(
        self, query: str, pages: int,
        engines: tuple[str, ...] = ("bing", "brave", "duckduckgo", "mojeek"),
    ) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        for page in range(1, pages + 1):
            try:
                response = self.session.get(
                    f"{self.base_url}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "categories": "general",
                        "engines": ",".join(engines),
                        "pageno": page,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                for item in payload.get("results", []):
                    url = str(item.get("url", "")).strip()
                    if not url:
                        continue
                    engines = tuple(str(value) for value in (item.get("engines") or [item.get("engine", "unknown")]))
                    found.setdefault(url, SearchResult(url, str(item.get("title") or url), engines))
                for item in payload.get("unresponsive_engines", []):
                    errors.append(": ".join(str(value) for value in item))
            except Exception as exc:
                errors.append(f"page {page}: {exc}")
        return list(found.values()), list(dict.fromkeys(errors))

    def discover_many(
        self, queries: tuple[str, ...], pages: int,
    ) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        for query in queries:
            results, query_errors = self.discover(query, pages)
            for result in results:
                previous = found.get(result.url)
                if previous:
                    engines = tuple(dict.fromkeys((*previous.engines, *result.engines)))
                    found[result.url] = SearchResult(result.url, previous.title, engines)
                else:
                    found[result.url] = result
            errors.extend(f"{query}: {value}" for value in query_errors)
        return list(found.values()), list(dict.fromkeys(errors))
