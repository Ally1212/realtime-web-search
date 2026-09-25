"""Google transports with OpenSERP as the production entry point."""
from __future__ import annotations

import os
import json
import hashlib
import signal
import subprocess
import sys
import uuid
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
        "did not find any results", "no results found", "没有找到结果", "没有找到相关结果",
    ))


class GoogleTransport:
    def __init__(
        self, timeout: int, language: str, searxng_url: str,
        *, persistent_browser_enabled: bool = False,
        openserp_url: str = "http://127.0.0.1:7000",
        openserp_request_timeout_seconds: int = 25,
        persistent_browser_profile_root: str = "state/browser-profiles",
        persistent_browser_max_contexts: int = 4,
        persistent_browser_request_interval_seconds: int = 30,
        persistent_browser_max_requests_per_context: int = 100,
        persistent_browser_max_context_lifetime_seconds: int = 21600,
        persistent_browser_failure_threshold: int = 3,
        max_curl_sessions: int = 64,
        google_serp_save_html: bool = False,
        google_serp_evidence_dir: str = "state/serp-evidence",
    ):
        self.timeout = timeout
        self.language = language
        self.searxng_url = searxng_url.rstrip("/")
        self.openserp_url = openserp_url.rstrip("/")
        self.openserp_request_timeout_seconds = max(1, openserp_request_timeout_seconds)
        self.local = threading.local()
        self.local.last_evidence = {}
        self.persistent_browser_enabled = persistent_browser_enabled
        self.persistent_browser_profile_root = Path(persistent_browser_profile_root)
        self.persistent_browser_max_contexts = max(1, persistent_browser_max_contexts)
        self.persistent_browser_request_interval_seconds = max(0, persistent_browser_request_interval_seconds)
        self.persistent_browser_max_requests_per_context = max(1, persistent_browser_max_requests_per_context)
        self.persistent_browser_max_context_lifetime_seconds = max(1, persistent_browser_max_context_lifetime_seconds)
        self.persistent_browser_failure_threshold = max(1, persistent_browser_failure_threshold)
        self.max_curl_sessions = max(1, max_curl_sessions)
        self.google_serp_save_html = google_serp_save_html
        self.google_serp_evidence_dir = Path(google_serp_evidence_dir)
        # Persistent contexts are shared across discovery threads so one proxy
        # always maps to one browser profile. Each context serializes its own
        # requests through a dedicated lock.
        self._persistent: dict[str, dict] = {}
        self._persistent_guard = threading.Lock()
        self._persistent_restarts = 0

    @property
    def last_evidence(self) -> dict:
        return getattr(self.local, "last_evidence", {}) or {}

    def fetch(
        self, provider: str, query: str, page: int, proxy_url: str | None,
        *, proxy_key: str | None = None,
    ) -> list[SearchResult]:
        self.local.last_evidence = {}
        if provider == "openserp":
            return self.openserp(query, page, proxy_url, proxy_key)
        if provider == "searxng":
            return self.searxng(query, page)
        if provider.startswith("wml"):
            return self.curl(query, page, proxy_url, wml=True)
        if provider.startswith("curl"):
            return self.curl(query, page, proxy_url)
        if provider.startswith("browser"):
            return self.browser(query, page, proxy_url)
        if provider == "persistent_browser":
            return self.persistent_browser(query, page, proxy_url, proxy_key)
        raise ValueError("unknown free Google provider")

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _openserp_evidence(
        self, response: requests.Response | None, raw: bytes, classification: str,
        request_url: str, payload: object = None, **extra: object,
    ) -> None:
        headers = response.headers if response is not None else {}
        meta = payload.get("meta") if isinstance(payload, dict) else None
        header_request_id = str(headers.get("X-Request-ID") or "").strip()
        body_request_id = str(meta.get("request_id") or "").strip() if isinstance(meta, dict) else ""
        self._evidence(
            raw,
            http_status=response.status_code if response is not None else None,
            classification=classification,
            request_url=request_url,
            headless=True,
            upstream_request_id=header_request_id or body_request_id or None,
            upstream_version=(str(meta.get("version") or "").strip() or None) if isinstance(meta, dict) else None,
            upstream_attempts=(self._positive_int(headers.get("X-Proxy-Attempts")) or 1) if response is not None else None,
            upstream_cache_status=str(headers.get("X-Cache") or "").strip().upper() or None,
            network_bytes=self._positive_int(headers.get("X-Network-Bytes")),
            **extra,
        )

    def _resolve_google_goto(
        self, session: requests.Session, url: str, proxy_url: str | None,
    ) -> str:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if host != "google.com" and not host.endswith(".google.com"):
            return url
        if parsed.path.rstrip("/") != "/goto":
            return url
        response: requests.Response | None = None
        try:
            response = session.get(
                url,
                allow_redirects=False,
                stream=True,
                timeout=min(self.openserp_request_timeout_seconds, 15),
                proxies=({"http": proxy_url, "https": proxy_url} if proxy_url else None),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return ""
            location = urljoin(url, str(response.headers.get("Location") or "").strip())
            return location if public_result(location) else ""
        except requests.RequestException:
            return ""
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _openserp_error(status: int, payload: object) -> GoogleBlocked:
        error = str(payload.get("error") or "").strip().lower() if isinstance(payload, dict) else ""
        reason = str(payload.get("reason") or "").strip().lower() if isinstance(payload, dict) else ""
        code = error or reason
        if status == 429:
            return GoogleBlocked(
                "google_captcha" if code == "captcha_detected" else "google_http_429",
                status,
                captcha=code == "captcha_detected",
            )
        if status == 403:
            return GoogleBlocked("google_http_403", status)
        if status == 504 or code == "search_timeout":
            return GoogleBlocked("google_timeout", status)
        if code in {"proxy_connect", "proxy_auth", "proxy_timeout", "proxy_unavailable"}:
            return GoogleBlocked(f"openserp_{code}", status)
        if status == 502 and code == "parser_failure":
            return GoogleBlocked("openserp_parser_failure", status)
        if status == 400:
            return GoogleBlocked("openserp_bad_request", status)
        if status == 503:
            return GoogleBlocked("openserp_unavailable", status)
        if status == 502:
            return GoogleBlocked("openserp_engine_error", status)
        return GoogleBlocked(f"openserp_http_{status}", status)

    def openserp(
        self, query: str, page: int, proxy_url: str | None, proxy_key: str | None,
    ) -> list[SearchResult]:
        if not self.openserp_url:
            raise GoogleBlocked("openserp_not_configured")
        session = getattr(self.local, "openserp_session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            self.local.openserp_session = session
        params = {
            "text": query,
            "start": (page - 1) * 10,
            "limit": 10,
            "lang": "ZH" if self.language == "zh" else "EN",
            "region": "US",
            "format": "json",
            "features": "false",
            "extract": 0,
        }
        request_url = self.openserp_url + "/google/search?" + urlencode(params)
        request_id = str(uuid.uuid4())
        headers = {"Accept": "application/json", "X-Request-ID": request_id}
        if proxy_url:
            headers["X-Proxy-URL"] = proxy_url
            headers["X-Proxy-Session-ID"] = hashlib.sha256(
                (proxy_key or proxy_url).encode()
            ).hexdigest()
        else:
            headers["X-Use-Proxy"] = "direct"
        response: requests.Response | None = None
        raw = b""
        payload: object = None
        try:
            response = session.get(
                self.openserp_url + "/google/search",
                params=params,
                headers=headers,
                timeout=self.openserp_request_timeout_seconds,
            )
            raw = response.content
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if response.status_code != 200:
                failure = self._openserp_error(response.status_code, payload)
                self._openserp_evidence(
                    response, raw,
                    "captcha" if failure.captcha else "timeout" if failure.reason == "google_timeout" else "error",
                    request_url, payload,
                )
                raise failure
            if not isinstance(payload, dict):
                raise GoogleBlocked("openserp_invalid_response", 200)
            query_meta = payload.get("query")
            meta = payload.get("meta")
            pagination = payload.get("pagination")
            rows = payload.get("results")
            requested = query_meta.get("engines_requested") if isinstance(query_meta, dict) else None
            failed = meta.get("engines_failed") if isinstance(meta, dict) else None
            proxy_attempts = self._positive_int(response.headers.get("X-Proxy-Attempts")) or 1
            cache_status = str(response.headers.get("X-Cache") or "").strip().upper()
            if (
                not isinstance(query_meta, dict)
                or query_meta.get("text") != query
                or requested != ["google"]
                or not isinstance(meta, dict)
                or failed != []
                or str(meta.get("request_id") or "").strip() != request_id
                or str(response.headers.get("X-Request-ID") or "").strip() != request_id
                or not str(meta.get("version") or "").strip()
                or not isinstance(rows, list)
                or not isinstance(pagination, dict)
                or not isinstance(pagination.get("page"), int)
                or not isinstance(pagination.get("has_more"), bool)
                or not isinstance(pagination.get("next_start"), int)
                or pagination.get("page") != page
                or bool(response.headers.get("X-Fallback-Engine"))
                or proxy_attempts != 1
                or cache_status == "HIT"
            ):
                raise GoogleBlocked("openserp_invalid_response", 200)
            results: dict[str, SearchResult] = {}
            wrapped_google_results = 0
            resolved_google_results = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if str(item.get("engine") or "").lower() != "google":
                    raise GoogleBlocked("openserp_invalid_response", 200)
                if str(item.get("type") or "").lower() != "organic":
                    continue
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                if (urlsplit(url).hostname or "").lower().endswith("google.com") and urlsplit(url).path.rstrip("/") == "/goto":
                    wrapped_google_results += 1
                    url = self._resolve_google_goto(session, url, proxy_url)
                    if url:
                        resolved_google_results += 1
                if public_result(url) and url not in results:
                    results[url] = SearchResult(url, title, ("google_web",))
            classification = "results" if results else "empty"
            self._openserp_evidence(
                response, raw, classification, request_url, payload,
                wrapped_google_results=wrapped_google_results,
                resolved_google_results=resolved_google_results,
            )
            return list(results.values())
        except GoogleBlocked:
            if not self.last_evidence:
                self._openserp_evidence(response, raw, "parse_failure", request_url, payload)
            raise
        except requests.RequestException:
            self._openserp_evidence(response, raw, "service_error", request_url, payload)
            raise GoogleBlocked("openserp_unavailable") from None
        finally:
            if response is not None:
                response.close()

    def _evidence(self, content: bytes = b"", **values) -> None:
        # Content may be str from Playwright or a stub object in tests; only
        # real bytes are hashed/saved so evidence never breaks the fetch path.
        if isinstance(content, bytes):
            raw = content
        elif isinstance(content, str):
            raw = content.encode()
        else:
            raw = b""
        digest = hashlib.sha256(raw).hexdigest() if raw else None
        evidence = {"raw_sha256": digest, "raw_html_path": None, **values}
        if raw and self.google_serp_save_html:
            evidence["raw_html_path"] = self._save_serp_html(digest, raw)
        self.local.last_evidence = evidence

    def _save_serp_html(self, digest: str | None, raw: bytes) -> str | None:
        if not digest:
            return None
        # Test-mode full-page evidence. Files stay local, 0600, and never
        # contain proxy credentials (only the rendered Google page).
        root = self.google_serp_evidence_dir
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        path = root / f"{digest}.html"
        if not path.exists():
            path.write_bytes(raw)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return str(path)

    def searxng(self, query: str, page: int) -> list[SearchResult]:
        if not self.searxng_url:
            raise GoogleBlocked("searxng_not_configured")
        # This service is internal; never send proxy credentials or use a public instance.
        with requests.Session() as session:
            session.trust_env = False
            params = {
                "q": query, "engines": "google", "categories": "general",
                "format": "json", "language": "zh-CN" if self.language == "zh" else "en",
                "pageno": page, "safesearch": 0,
            }
            request_url = self.searxng_url + "/search?" + urlencode(params)
            response = session.get(self.searxng_url + "/search", params=params, timeout=self.timeout)
            try:
                response.raise_for_status()
                raw = response.content
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
                    self._evidence(raw, http_status=response.status_code, classification="parse_failure", request_url=request_url)
                    raise GoogleBlocked("searxng_empty_unverified")
                self._evidence(raw, http_status=response.status_code, classification="results", request_url=request_url)
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
        if key not in sessions and len(sessions) >= self.max_curl_sessions:
            # Long-running workers rotate across many exits. Without this bound
            # every proxy keeps a curl session/FD until the process hits the
            # container file-descriptor ceiling and all new requests fail.
            oldest_key = next(iter(sessions))
            try:
                sessions[oldest_key].close()
            except Exception:
                pass
            sessions.pop(oldest_key, None)
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
        # The evidence URL identifies the Google request; proxy credentials never appear in it.
        request_url = "https://www.google.com/" + ("wml/search" if wml else "search") + "?" + urlencode(params)
        try:
            response = session.get("https://www.google.com/" + ("wml/search" if wml else "search"),
                                   params=params, proxy=proxy_url, timeout=self.timeout, headers=headers)
        except Exception:
            # A transport failure may leave the cached curl session unusable.
            # Drop this thread-local instance so the next attempt rebuilds it.
            try:
                session.close()
            except Exception:
                pass
            sessions.pop(key, None)
            raise
        try:
            raw = response.content
            blocked = SearchDiscovery._google_block(response)
            if blocked:
                self._evidence(raw, http_status=response.status_code, classification="captcha" if blocked.captcha else "http_error", request_url=request_url)
                raise blocked
            response.raise_for_status()
            results = self.parse_wml(raw) if wml else SearchDiscovery._parse_google_html(raw)
            parsed = [row for row in results if public_result(row.url)]
            if parsed:
                self._evidence(raw, http_status=response.status_code, classification="results", request_url=request_url)
                return parsed
            # A real WML shell still carries result anchors whose public-host
            # DNS checks fail transiently. Parsing succeeded in that case; do
            # not misclassify the whole page as an unknown layout.
            if results or explicit_empty(raw):
                self._evidence(raw, http_status=response.status_code, classification="empty", request_url=request_url)
                return []
            if b"enablejs" in raw or b"enable javascript" in raw.lower():
                self._evidence(raw, http_status=response.status_code, classification="javascript_verification", request_url=request_url)
                raise GoogleBlocked("google_javascript_required")
            self._evidence(raw, http_status=response.status_code, classification="parse_failure", request_url=request_url)
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

    def persistent_browser(
        self, query: str, page_number: int, proxy_url: str | None, proxy_key: str | None,
    ) -> list[SearchResult]:
        if not self.persistent_browser_enabled:
            raise GoogleBlocked("persistent_browser_disabled")
        return self._persistent_browser_page(query, page_number, proxy_url, proxy_key)

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
        request_url = "https://www.google.com/search?" + urlencode({
            "q": query, "start": (page_number - 1) * 10, "num": 10,
            "hl": "zh-CN" if self.language == "zh" else "en", "pws": 0,
        })
        try:
            response = page.goto(request_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            status = response.status if response else 200
            while True:
                content = page.content().encode()
                blocked = SearchDiscovery._google_block(type("Response", (), {
                    "url": page.url, "status_code": status, "content": content,
                })())
                if blocked:
                    self._evidence(content, http_status=status, classification="captcha" if blocked.captcha else "http_error", request_url=request_url)
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
                    self._evidence(content, http_status=status, classification="results", headless=not bool(os.getenv("DISPLAY")), request_url=request_url)
                    return list(results.values())
                if explicit_empty(content):
                    self._evidence(content, http_status=status, classification="empty", headless=not bool(os.getenv("DISPLAY")), request_url=request_url)
                    return []
                if time.monotonic() >= deadline:
                    reason = "google_javascript_required" if b"enablejs" in content else "google_browser_no_results"
                    self._evidence(content, http_status=status, classification="javascript_verification" if "javascript" in reason else "parse_failure", headless=not bool(os.getenv("DISPLAY")), request_url=request_url)
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
    def _proxy_config(proxy_url: str | None) -> dict | None:
        if not proxy_url:
            return None
        parsed = urlsplit(proxy_url)
        proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy

    def _new_persistent_context(self, proxy_hash: str, proxy_url: str | None) -> dict:
        from playwright.sync_api import sync_playwright
        self.persistent_browser_profile_root.mkdir(parents=True, exist_ok=True)
        try:
            self.persistent_browser_profile_root.chmod(0o700)
        except OSError:
            pass
        profile = self.persistent_browser_profile_root / proxy_hash
        profile.mkdir(parents=True, exist_ok=True)
        try:
            profile.chmod(0o700)
        except OSError:
            pass
        runtime = sync_playwright().start()
        try:
            context = runtime.chromium.launch_persistent_context(
                str(profile), headless=not bool(os.getenv("DISPLAY")),
                proxy=self._proxy_config(proxy_url),
                locale="zh-CN" if self.language == "zh" else "en-SG",
                timeout=self.timeout * 1000,
            )
        except Exception:
            runtime.stop()
            raise
        return {
            "runtime": runtime, "context": context, "proxy_url": proxy_url,
            "created": time.monotonic(), "last_request": 0.0,
            "requests": 0, "failures": 0, "lock": threading.Lock(),
        }

    def _close_persistent_resource(self, resource: dict) -> None:
        try:
            resource["context"].close()
        finally:
            resource["runtime"].stop()

    def _persistent_expired(self, resource: dict, now: float) -> bool:
        return (
            resource["requests"] >= self.persistent_browser_max_requests_per_context
            or now - resource["created"] >= self.persistent_browser_max_context_lifetime_seconds
            or resource["failures"] >= self.persistent_browser_failure_threshold
        )

    def _drop_persistent(self, proxy_hash: str, resource: dict) -> None:
        with self._persistent_guard:
            if self._persistent.get(proxy_hash) is resource:
                self._persistent.pop(proxy_hash, None)
                self._persistent_restarts += 1
        try:
            self._close_persistent_resource(resource)
        except Exception:
            pass

    def _acquire_persistent(self, proxy_hash: str, proxy_url: str | None) -> dict:
        with self._persistent_guard:
            resource = self._persistent.get(proxy_hash)
        if resource and self._persistent_expired(resource, time.monotonic()):
            self._drop_persistent(proxy_hash, resource)
            resource = None
        if resource is None:
            candidate = self._new_persistent_context(proxy_hash, proxy_url)
            with self._persistent_guard:
                resource = self._persistent.get(proxy_hash)
                if resource is None:
                    while len(self._persistent) >= self.persistent_browser_max_contexts:
                        oldest = min(self._persistent, key=lambda key: self._persistent[key]["last_request"])
                        victim = self._persistent[oldest]
                        if not victim["lock"].acquire(blocking=False):
                            break
                        try:
                            self._persistent.pop(oldest, None)
                            self._persistent_restarts += 1
                        finally:
                            victim["lock"].release()
                        try:
                            self._close_persistent_resource(victim)
                        except Exception:
                            pass
                    self._persistent[proxy_hash] = candidate
                    self._persistent_restarts += 1
                    resource = candidate
            if resource is not candidate:
                try:
                    self._close_persistent_resource(candidate)
                except Exception:
                    pass
        return resource

    def persistent_browser_stats(self) -> dict:
        with self._persistent_guard:
            contexts = [
                {"proxy_hash": key, "requests": resource["requests"],
                 "failures": resource["failures"],
                 "age_seconds": round(time.monotonic() - resource["created"], 1)}
                for key, resource in self._persistent.items()
            ]
        return {
            "enabled": self.persistent_browser_enabled,
            "headless": not bool(os.getenv("DISPLAY")),
            "contexts": len(contexts), "restarts": self._persistent_restarts,
            "profiles": contexts,
        }

    def _persistent_browser_page(
        self, query: str, page_number: int, proxy_url: str | None, proxy_key: str | None,
    ) -> list[SearchResult]:
        proxy_hash = hashlib.sha256((proxy_key or proxy_url or "direct").encode()).hexdigest()
        while True:
            resource = self._acquire_persistent(proxy_hash, proxy_url)
            # One in-flight request per context so the 30s interval is guaranteed
            # even when several discovery threads share the same proxy.
            with resource["lock"]:
                if self._persistent.get(proxy_hash) is resource:
                    break
        return self._persistent_request(resource, proxy_hash, query, page_number)

    def _persistent_request(
        self, resource: dict, proxy_hash: str, query: str, page_number: int,
    ) -> list[SearchResult]:
        from .discovery import SearchDiscovery
        with resource["lock"]:
            wait = max(0.0, resource["last_request"] + self.persistent_browser_request_interval_seconds - time.monotonic())
            if wait:
                time.sleep(wait)
            resource["last_request"] = time.monotonic()
            resource["requests"] += 1
            page = resource["context"].new_page()
            deadline = time.monotonic() + self.timeout
            headless = not bool(os.getenv("DISPLAY"))
            request_url = "https://www.google.com/search?" + urlencode({
                "q": query, "start": (page_number - 1) * 10, "num": 10,
                "hl": "zh-CN" if self.language == "zh" else "en", "pws": 0,
            })
            try:
                response = page.goto(request_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                status = response.status if response else 200
                while True:
                    content = page.content().encode()
                    blocked = SearchDiscovery._google_block(type("Response", (), {
                        "url": page.url, "status_code": status, "content": content,
                    })())
                    if blocked:
                        resource["failures"] += 1
                        self._evidence(content, http_status=status, classification="captcha" if blocked.captcha else "http_error", headless=headless, request_url=request_url)
                        raise blocked
                    anchors = page.eval_on_selector_all(
                        "a:has(h3)",
                        "nodes => nodes.map(a => ({href: a.getAttribute('href'), title: a.querySelector('h3').textContent}))",
                    )
                    results = {}
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
                        resource["failures"] = 0
                        self._evidence(content, http_status=status, classification="results", headless=headless, request_url=request_url)
                        return list(results.values())
                    if explicit_empty(content):
                        resource["failures"] = 0
                        self._evidence(content, http_status=status, classification="empty", headless=headless, request_url=request_url)
                        return []
                    if time.monotonic() >= deadline:
                        resource["failures"] += 1
                        self._evidence(content, http_status=status, classification="javascript_verification" if b"enablejs" in content.lower() else "parse_failure", headless=headless, request_url=request_url)
                        raise GoogleBlocked("google_javascript_required" if b"enablejs" in content.lower() else "google_browser_no_results")
                    page.wait_for_timeout(250)
            except GoogleBlocked:
                raise
            except Exception as exc:
                resource["failures"] += 1
                # A crashed/closed context is dropped immediately so the next
                # request rebuilds it; other proxies' contexts are untouched.
                if "closed" in str(exc).lower() or "crash" in str(exc).lower():
                    resource["failures"] = self.persistent_browser_failure_threshold
                try:
                    content = page.content().encode()
                except Exception:
                    content = b""
                self._evidence(content, http_status=None, classification="timeout", headless=headless, request_url=request_url)
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

    def close_thread(self):
        # Per-discover cleanup of resources owned by the calling thread only.
        # Shared persistent contexts deliberately survive so cookies and
        # sessions carry across queries.
        self.close_browser()
        for session in getattr(self.local, "sessions", {}).values():
            session.close()
        self.local.sessions = {}
        openserp_session = getattr(self.local, "openserp_session", None)
        self.local.openserp_session = None
        if openserp_session is not None:
            openserp_session.close()

    def close(self):
        self.close_thread()
        with self._persistent_guard:
            resources = list(self._persistent.values())
            self._persistent = {}
        for resource in resources:
            try:
                self._close_persistent_resource(resource)
            except Exception:
                pass
