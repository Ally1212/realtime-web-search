"""Free Google transports. SearXNG is an independent, Google-only service."""
from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .discovery import GoogleBlocked, SearchResult


def public_result(url: str) -> bool:
    from .fetcher import is_public_url
    host = (urlsplit(url).hostname or "").lower()
    return is_public_url(url) and not (
        host == "google.com" or host.endswith(".google.com")
        or host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
    )


def explicit_empty(content: bytes) -> bool:
    text = BeautifulSoup(content, "html.parser").get_text(" ", strip=True).lower()
    return any(value in text for value in (
        "did not match any documents", "no results found for", "没有找到与", "找不到和您查询",
    ))


class GoogleTransport:
    def __init__(self, timeout: int, language: str, searxng_url: str):
        self.timeout = timeout
        self.language = language
        self.searxng_url = searxng_url.rstrip("/")
        self.local = threading.local()

    def fetch(self, provider: str, query: str, page: int, proxy_url: str | None) -> list[SearchResult]:
        if provider == "searxng":
            return self.searxng(query, page)
        if provider.startswith("wml"):
            return self.curl(query, page, proxy_url, wml=True)
        if provider.startswith("curl"):
            return self.curl(query, page, proxy_url)
        if provider.startswith("browser"):
            return self.browser(query, page, proxy_url)
        raise ValueError("unknown free Google provider")

    def searxng(self, query: str, page: int) -> list[SearchResult]:
        if not self.searxng_url:
            raise GoogleBlocked("searxng_not_configured")
        # This service is internal; never send proxy credentials or use a public instance.
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(self.searxng_url + "/search", params={
                "q": query, "engines": "google", "categories": "general",
                "format": "json", "language": "zh-CN" if self.language == "zh" else "en",
                "pageno": page, "safesearch": 0,
            }, timeout=self.timeout)
            try:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise GoogleBlocked("searxng_invalid_response")
                errors = payload.get("unresponsive_engines") or []
                if errors:
                    reason = str(errors).lower()
                    if "captcha" in reason:
                        raise GoogleBlocked("google_captcha", captcha=True)
                    raise GoogleBlocked("searxng_engine_timeout" if "timeout" in reason else "searxng_engine_error")
                results = {}
                for item in payload["results"]:
                    if item.get("engine") != "google":
                        continue
                    url, title = str(item.get("url") or ""), str(item.get("title") or "")
                    if title and public_result(url):
                        results[url] = SearchResult(url, title, ("google_web",))
                # SearXNG's Google parser cannot distinguish a layout regression from
                # a genuine empty SERP. Never cache that ambiguity as a successful page.
                if not results:
                    raise GoogleBlocked("searxng_empty_unverified")
                return list(results.values())
            finally:
                response.close()

    def curl(self, query: str, page: int, proxy_url: str | None, *, wml: bool = False) -> list[SearchResult]:
        from curl_cffi import requests as curl_requests
        from .discovery import SearchDiscovery
        sessions = getattr(self.local, "sessions", None)
        if sessions is None:
            sessions = self.local.sessions = {}
        key = (proxy_url, wml)
        if key not in sessions:
            sessions[key] = curl_requests.Session(impersonate="chrome99_android" if wml else "chrome")
        session = sessions[key]
        params = {
            "q": query, "start": (page - 1) * 10,
            "hl": "zh-CN" if self.language == "zh" else "en", "pws": 0,
            "ie": "utf-8", "oe": "utf-8",
        }
        headers = {
            "Accept-Language": "zh-CN,zh;q=0.9" if self.language == "zh" else "en-SG,en;q=0.9",
        }
        if wml:
            params["sca_esv"] = "1"
            headers["User-Agent"] = "Nokia6230/2.0 (05.50) Profile/MIDP-2.0 Configuration/CLDC-1.1"
        response = session.get("https://www.google.com/" + ("wml/search" if wml else "search"),
                               params=params, proxy=proxy_url, timeout=self.timeout, headers=headers)
        try:
            blocked = SearchDiscovery._google_block(response)
            if blocked:
                raise blocked
            response.raise_for_status()
            results = self.parse_wml(response.content) if wml else SearchDiscovery._parse_google_html(response.content)
            if results:
                return [row for row in results if public_result(row.url)]
            if explicit_empty(response.content):
                return []
            if b"enablejs" in response.content or b"enable javascript" in response.content.lower():
                raise GoogleBlocked("google_javascript_required")
            raise GoogleBlocked("google_unrecognized_page")
        finally:
            response.close()

    @staticmethod
    def parse_wml(content: bytes) -> list[SearchResult]:
        # Parse the observed Google mobile result links without depending on
        # SearXNG's source or DOM classes. Navigation links have no title span.
        soup = BeautifulSoup(content, "html.parser", from_encoding="utf-8")
        results = {}
        for anchor in soup.select("a[href]"):
            raw = str(anchor.get("href") or "")
            if not raw.startswith("/url?"):
                continue
            args = parse_qs(urlsplit(raw).query)
            url = (args.get("q") or args.get("url") or [""])[0]
            title_node = anchor.find("span") or anchor.find(["h3", "h2"])
            title = title_node.get_text(" ", strip=True) if title_node else ""
            if title and public_result(url):
                results[url] = SearchResult(url, title, ("google_web",))
        return list(results.values())

    def browser(self, query: str, page_number: int, proxy_url: str | None) -> list[SearchResult]:
        # Chromium/driver shutdown can hang beyond Playwright navigation timeouts.
        # Isolate the last-resort browser so it cannot occupy a discovery worker forever.
        process = subprocess.Popen(
            [sys.executable, "-m", "realtime.browser_probe"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        payload = json.dumps({"query": query, "page": page_number, "proxy": proxy_url,
                              "language": self.language, "timeout": self.timeout})
        try:
            stdout, _ = process.communicate(payload, timeout=self.timeout + 5)
        except subprocess.TimeoutExpired:
            self._kill_browser_process(process)
            raise GoogleBlocked("google_browser_deadline") from None
        try:
            result = json.loads(stdout)
        except ValueError:
            raise GoogleBlocked("google_browser_runtime_error") from None
        if result.get("error"):
            raise GoogleBlocked(result["error"], captcha=bool(result.get("captcha")))
        if process.returncode:
            raise GoogleBlocked("google_browser_runtime_error")
        return [SearchResult(row["url"], row["title"], ("google_web",)) for row in result["results"]]

    @staticmethod
    def _kill_browser_process(process):
        # Only terminate descendants of the subprocess we created, including
        # Chromium's separately created process group. Never touch other browsers.
        try:
            if Path("/proc").is_dir():
                pairs = []
                for directory in Path("/proc").iterdir():
                    if directory.name.isdigit():
                        try:
                            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
                            pairs.append((int(directory.name), int(fields[1])))
                        except (OSError, ValueError, IndexError):
                            pass
            else:
                listing = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True, timeout=2)
                pairs = [tuple(map(int, line.split())) for line in listing.splitlines() if line.strip()]
            descendants = [process.pid]
            for parent in descendants:
                descendants.extend(pid for pid, ppid in pairs if ppid == parent and pid not in descendants)
            for pid in reversed(descendants[1:]):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)

    def _browser_page(self, query: str, page_number: int, proxy_url: str | None) -> list[SearchResult]:
        from playwright.sync_api import sync_playwright
        from .discovery import SearchDiscovery
        resources = getattr(self.local, "browser", None)
        if resources and resources[0] != proxy_url:
            self.close_browser()
            resources = None
        if not resources:
            proxy = None
            if proxy_url:
                parsed = urlsplit(proxy_url)
                proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
                if parsed.username:
                    proxy["username"] = unquote(parsed.username)
                if parsed.password:
                    proxy["password"] = unquote(parsed.password)
            runtime = sync_playwright().start()
            try:
                browser = runtime.chromium.launch(headless=not bool(os.getenv("DISPLAY")), proxy=proxy, timeout=self.timeout * 1000)
                context = browser.new_context(locale="zh-CN" if self.language == "zh" else "en-SG")
                context.set_default_timeout(min(self.timeout * 1000, 2000))
            except Exception:
                runtime.stop()
                raise
            resources = self.local.browser = (proxy_url, runtime, browser, context)
        page = resources[3].new_page()
        deadline = time.monotonic() + self.timeout
        try:
            response = page.goto("https://www.google.com/search?" + urlencode({
                "q": query, "start": (page_number - 1) * 10, "num": 10,
                "hl": "zh-CN" if self.language == "zh" else "en", "pws": 0,
            }), wait_until="domcontentloaded", timeout=self.timeout * 1000)
            status = response.status if response else 200
            while True:
                content = page.content().encode()
                blocked = SearchDiscovery._google_block(type("Response", (), {
                    "url": page.url, "status_code": status, "content": content,
                })())
                if blocked:
                    raise blocked
                results = {}
                anchors = page.eval_on_selector_all("a:has(h3)", "nodes => nodes.map(a => ({href: a.getAttribute('href'), title: a.querySelector('h3').textContent}))")
                for anchor in anchors[:30]:
                    raw = anchor.get("href") or ""
                    url = urljoin(page.url, raw)
                    if raw.startswith("/url?"):
                        args = parse_qs(urlsplit(raw).query)
                        url = (args.get("q") or args.get("url") or [""])[0]
                    title = str(anchor.get("title") or "").strip()
                    if title and public_result(url):
                        results[url] = SearchResult(url, title, ("google_web",))
                if results:
                    return list(results.values())
                if explicit_empty(content):
                    return []
                if time.monotonic() >= deadline:
                    reason = "google_javascript_required" if b"enablejs" in content else "google_browser_no_results"
                    raise GoogleBlocked(reason)
                page.wait_for_timeout(250)
        except GoogleBlocked:
            self.close_browser_page(page)
            raise
        except Exception:
            # The navigation itself can time out on a CAPTCHA; inspect before classifying.
            try:
                blocked = SearchDiscovery._google_block(type("Response", (), {
                    "url": page.url, "status_code": 200, "content": page.content().encode(),
                })())
                if blocked:
                    raise blocked
            finally:
                self.close_browser_page(page)
            raise
        finally:
            self.close_browser_page(page)

    @staticmethod
    def close_browser_page(page):
        if not page.is_closed():
            page.close()

    def close_browser(self):
        resources = getattr(self.local, "browser", None)
        self.local.browser = None
        if resources:
            try:
                resources[2].close()
            finally:
                resources[1].stop()

    def close(self):
        self.close_browser()
        for session in getattr(self.local, "sessions", {}).values():
            session.close()
        self.local.sessions = {}
