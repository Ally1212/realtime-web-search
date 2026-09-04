from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests


MAX_FEED_BYTES = 5_000_000


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    engines: tuple[str, ...]


class SearchDiscovery:
    def __init__(
        self,
        base_url: str,
        timeout: int = 20,
        session: requests.Session | None = None,
        feeds: tuple[tuple[str, str], ...] = (),
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.feeds = feeds

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
            title = ""
            link = ""
            for child in entry:
                tag = child.tag.rsplit("}", 1)[-1].lower()
                if tag == "title" and not title:
                    title = "".join(child.itertext()).strip()
                elif tag == "link" and not link:
                    rel = child.attrib.get("rel", "alternate")
                    if rel == "alternate":
                        link = (child.attrib.get("href") or child.text or "").strip()
            if link:
                results.append(SearchResult(link, title or link, (source,)))
        return results

    def _resolve_google_news(self, result: SearchResult) -> SearchResult:
        if not any("google-news" in engine for engine in result.engines):
            return result
        host = (urlsplit(result.url).hostname or "").lower()
        if host != "news.google.com":
            return result
        response = self.session.get(
            result.url, timeout=self.timeout, allow_redirects=True, stream=True
        )
        try:
            response.raise_for_status()
            resolved = str(response.url or "").strip()
            if resolved and resolved != result.url:
                return SearchResult(resolved, result.title, result.engines)
            return result
        finally:
            response.close()

    def discover_feeds(
        self, queries: tuple[str, ...]
    ) -> tuple[list[SearchResult], list[str]]:
        rendered: dict[str, str] = {}
        for query in queries:
            encoded = quote(query, safe="")
            for name, template in self.feeds:
                rendered.setdefault(template.replace("{query}", encoded), name)
        if not rendered:
            return [], []

        def fetch(item: tuple[str, str]) -> tuple[list[SearchResult], str | None]:
            url, name = item
            try:
                response = self.session.get(url, timeout=self.timeout, stream=True)
                response.raise_for_status()
                results = self._parse_feed(self._read_limited(response), name)
                if "google-news" in name:
                    resolved = [self._resolve_google_news(result) for result in results]
                    return resolved, None
                return results, None
            except Exception as exc:
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
                        engines = tuple(dict.fromkeys((*previous.engines, *result.engines)))
                        found[result.url] = SearchResult(result.url, previous.title, engines)
                    else:
                        found[result.url] = result
        return list(found.values()), errors

    def discover(
        self, query: str, pages: int,
        engines: tuple[str, ...] = ("google",),
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
        if not queries:
            return [], []
        found: dict[str, SearchResult] = {}
        errors: list[str] = []

        def discover_query(query: str) -> tuple[list[SearchResult], list[str]]:
            return self.discover(query, pages)

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
        feed_results, feed_errors = self.discover_feeds(queries)
        for result in feed_results:
            previous = found.get(result.url)
            if previous:
                engines = tuple(dict.fromkeys((*previous.engines, *result.engines)))
                found[result.url] = SearchResult(result.url, previous.title, engines)
            else:
                found[result.url] = result
        errors.extend(feed_errors)
        return list(found.values()), list(dict.fromkeys(errors))
