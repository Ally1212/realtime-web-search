from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .proxy_pool import ProxyPool


MAX_FEED_BYTES = 5_000_000
GOOGLE_ENGINES = ("google",)


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
        proxy_pool: ProxyPool | None = None,
        proxy_profile: str = "direct",
        language: str = "en",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.feeds = feeds
        self.proxy_pool = proxy_pool
        self.proxy_profile = proxy_profile
        self.language = language if language in {"en", "zh"} else "en"
        self._google_query_cooldown: dict[str, float] = {}
        self._google_lock = threading.Lock()

    def _external_get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._external_request("get", url, **kwargs)

    def _external_post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._external_request("post", url, **kwargs)

    def _external_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        selected = None
        domain = urlsplit(url).hostname or ""
        if self.proxy_pool and self.proxy_profile != "direct":
            sticky = (
                self.proxy_pool.config.google_proxy_sticky_seconds
                if domain.endswith("google.com") else None
            )
            selected = self.proxy_pool.choose(
                self.proxy_profile, domain, sticky_seconds=sticky
            )
            if selected is None:
                raise requests.ProxyError(f"{self.proxy_profile} proxy pool unavailable")
            proxy_url, _ = selected
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        try:
            response = getattr(self.session, method)(url, **kwargs)
        except requests.RequestException:
            if selected:
                self.proxy_pool.report(selected[1], domain, failed=True)
            raise
        if selected:
            self.proxy_pool.report(selected[1], domain, response.status_code)
        return response

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
            if not title_node:
                continue
            title = title_node.get_text(" ", strip=True)
            if title:
                results.append(SearchResult(href, title, ("google",)))
        return results

    def _discover_google_page(self, query: str, page: int) -> list[SearchResult]:
        with self._google_lock:
            if self._google_query_cooldown.get(query, 0) > time.monotonic():
                raise RuntimeError("google_query_cooldown")
        response = self._external_get(
            "https://www.google.com/search",
            params={
                "q": query, "num": 10, "start": (page - 1) * 10, "filter": 0,
                "hl": "zh-CN" if self.language == "zh" else "en",
            },
            headers={"Accept-Language": (
                "zh-CN,zh;q=0.9,en;q=0.5" if self.language == "zh" else "en-SG,en;q=0.8"
            )},
            timeout=self.timeout,
        )
        if response.status_code in {403, 429}:
            with self._google_lock:
                self._google_query_cooldown[query] = time.monotonic() + 300
        response.raise_for_status()
        return self._parse_google_html(response.content)

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

    def _resolve_google_news(self, result: SearchResult) -> SearchResult | None:
        host = (urlsplit(result.url).hostname or "").lower()
        if host != "news.google.com":
            return result
        try:
            response = self._external_get(
                result.url, timeout=min(self.timeout, 5), allow_redirects=True, stream=True
            )
            try:
                response.raise_for_status()
                resolved = str(response.url or "").strip()
                if resolved and resolved != result.url:
                    return SearchResult(resolved, result.title, result.engines)
                soup = BeautifulSoup(response.content, "html.parser")
                node = soup.select_one("[data-n-a-id][data-n-a-ts][data-n-a-sg]")
                if node:
                    article_id = str(node.get("data-n-a-id") or "")
                    timestamp = int(str(node.get("data-n-a-ts") or "0"))
                    signature = str(node.get("data-n-a-sg") or "")
                    request_payload = json.dumps(
                        [
                            "garturlreq",
                            [
                                [
                                    "en-US", "US",
                                    ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"],
                                    None, None, 1, 1, "US:en", None, 180,
                                    None, None, None, None, None, 0, None, None,
                                    [1608992183, 723341000],
                                ],
                                "en-US", "US", 1, [2, 3, 4, 8], 1, 0,
                                "655000234", 0, 0, None, 0,
                            ],
                            article_id, timestamp, signature,
                        ],
                        separators=(",", ":"),
                    )
                    batch = json.dumps(
                        [[["Fbv4je", request_payload, None, "generic"]]],
                        separators=(",", ":"),
                    )
                    decoded = self._external_post(
                        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                        data={"f.req": batch},
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
                        },
                        timeout=self.timeout,
                    )
                    try:
                        decoded.raise_for_status()
                        match = re.search(
                            r'garturlres\\",\\"(https?[^"\\]+)', decoded.text
                        )
                        if match:
                            resolved = json.loads(f'"{match.group(1)}"')
                            if resolved and "news.google.com" not in resolved:
                                return SearchResult(resolved, result.title, result.engines)
                    finally:
                        decoded.close()
            finally:
                response.close()
        except Exception:
            return None
        return None

    def discover_feeds(
        self, queries: tuple[str, ...]
    ) -> tuple[list[SearchResult], list[str]]:
        rendered: dict[str, str] = {}
        for query in queries:
            encoded = quote(query, safe="")
            for name, template in self.feeds:
                rendered.setdefault(
                    template.replace("{query}", encoded)
                    .replace("{hl}", "zh-CN" if self.language == "zh" else "en-SG")
                    .replace("{ceid}", "SG:zh-Hans" if self.language == "zh" else "SG:en"),
                    name,
                )
        if not rendered:
            return [], []

        def fetch(item: tuple[str, str]) -> tuple[list[SearchResult], str | None]:
            url, name = item
            try:
                response = self._external_get(url, timeout=self.timeout, stream=True)
                response.raise_for_status()
                results = self._parse_feed(self._read_limited(response), name)
                if "google-news" in name:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(results) or 1)) as executor:
                        resolved = list(executor.map(self._resolve_google_news, results))
                    return [item for item in resolved if item is not None], None
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
        engines: tuple[str, ...] = GOOGLE_ENGINES,
    ) -> tuple[list[SearchResult], list[str]]:
        found: dict[str, SearchResult] = {}
        errors: list[str] = []
        allowed_engines = {engine.lower() for engine in engines}

        def allowed_item_engines(values: tuple[str, ...]) -> tuple[str, ...]:
            filtered = tuple(
                engine for engine in values
                if engine.lower().split()[0] in allowed_engines
            )
            return filtered

        def fetch_page(page: int) -> tuple[list[SearchResult], list[str]]:
            direct_error = ""
            if engines == GOOGLE_ENGINES and self.proxy_pool and self.proxy_profile != "direct":
                try:
                    direct_results = self._discover_google_page(query, page)
                    if direct_results:
                        return direct_results, []
                    direct_error = f"page {page}: google direct returned no results"
                except Exception as exc:
                    direct_error = f"page {page}: google direct {type(exc).__name__}"
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
                page_results: list[SearchResult] = []
                page_errors: list[str] = []
                for item in payload.get("results", []):
                    url = str(item.get("url", "")).strip()
                    if not url:
                        continue
                    item_engines = tuple(str(value) for value in (item.get("engines") or [item.get("engine", "unknown")]))
                    item_engines = allowed_item_engines(item_engines)
                    if not item_engines:
                        continue
                    page_results.append(SearchResult(url, str(item.get("title") or url), item_engines))
                for item in payload.get("unresponsive_engines", []):
                    engine = str(item[0] if item else "").lower()
                    if engine in allowed_engines:
                        page_errors.append(": ".join(str(value) for value in item))
                if direct_error and not page_results:
                    page_errors.append(direct_error)
                return page_results, page_errors
            except Exception as exc:
                errors = [f"page {page}: {exc}"]
                if direct_error:
                    errors.insert(0, direct_error)
                return [], errors

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(pages, 1))) as executor:
            for results, page_errors in executor.map(fetch_page, range(1, pages + 1)):
                for result in results:
                    found.setdefault(result.url, result)
                errors.extend(page_errors)
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
        google_news = [
            (url, result) for url, result in found.items()
            if (urlsplit(url).hostname or "").lower() == "news.google.com"
        ]
        if google_news:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(google_news))
            ) as executor:
                resolved_results = executor.map(
                    self._resolve_google_news,
                    (result for _, result in google_news),
                )
                for (original_url, _), resolved in zip(google_news, resolved_results):
                    found.pop(original_url, None)
                    if resolved:
                        found.setdefault(resolved.url, resolved)
        return list(found.values()), list(dict.fromkeys(errors))
