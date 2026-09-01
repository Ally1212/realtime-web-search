from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

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
        engines: tuple[str, ...] = (
            "bing", "brave", "duckduckgo", "google", "marginalia", "mojeek",
            "qwant", "startpage", "wikidata", "wikipedia", "yahoo", "yep",
        ),
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

    def discover_rss(self, query: str) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        endpoints = (
            ("bing-web-rss", "https://www.bing.com/search"),
            ("bing-news-rss", "https://www.bing.com/news/search"),
        )
        for engine, url in endpoints:
            try:
                response = self.session.get(
                    url, params={"q": query, "format": "rss"}, timeout=self.timeout
                )
                response.raise_for_status()
                root = ElementTree.fromstring(response.content)
                for item in root.findall(".//item"):
                    link = (item.findtext("link") or "").strip()
                    if not link:
                        continue
                    title = (item.findtext("title") or link).strip()
                    found.setdefault(link, SearchResult(link, title, (engine,)))
            except Exception as exc:
                errors.append(f"{engine}: {type(exc).__name__}")
        return list(found.values()), errors

    def discover_many(
        self, queries: tuple[str, ...], pages: int,
    ) -> tuple[list[SearchResult], list[str]]:
        if not queries:
            return [], []
        found: dict[str, SearchResult] = {}
        errors: list[str] = []

        def discover_query(query: str) -> tuple[list[SearchResult], list[str]]:
            results, query_errors = self.discover(query, pages)
            rss_results, rss_errors = self.discover_rss(query)
            results.extend(rss_results)
            query_errors.extend(rss_errors)
            return results, query_errors

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(queries))) as executor:
            batches = executor.map(discover_query, queries)
            query_batches = zip(queries, batches)
            for query, (results, query_errors) in query_batches:
                for result in results:
                    previous = found.get(result.url)
                    if previous:
                        engines = tuple(dict.fromkeys((*previous.engines, *result.engines)))
                        found[result.url] = SearchResult(result.url, previous.title, engines)
                    else:
                        found[result.url] = result
                errors.extend(f"{query}: {value}" for value in query_errors)
        return list(found.values()), list(dict.fromkeys(errors))
