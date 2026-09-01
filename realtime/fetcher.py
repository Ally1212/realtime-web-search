from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


MAX_DOWNLOAD_BYTES = 5_000_000
MAX_TEXT_CHARS = 100_000
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


@dataclass(frozen=True)
class LiveDocument:
    document_id: str
    url: str
    title: str
    content: str
    summary: str
    query: str
    source_engines: tuple[str, ...]
    discovered_at: str
    fetched_at: str
    http_status: int
    content_hash: str
    language: str = "other"


@dataclass(frozen=True)
class FetchResult:
    status: str
    url: str
    title: str
    http_status: int | None = None
    document: LiveDocument | None = None
    error: str | None = None


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = urlencode([
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ])
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


def detect_language(text: str) -> str:
    sample = text[:5000]
    cjk = sum("\u3400" <= char <= "\u9fff" for char in sample)
    latin = sum(char.isascii() and char.isalpha() for char in sample)
    if cjk >= max(5, latin // 8):
        return "zh"
    return "en" if latin >= 20 else "other"


def relevant_to(text: str, title: str, terms: tuple[str, ...]) -> bool:
    title_folded = title.casefold()
    text_folded = text.casefold()
    for term in terms:
        phrase = " ".join(term.casefold().split())
        if not phrase:
            continue
        if phrase in title_folded or phrase in text_folded:
            return True
        tokens = [token for token in phrase.replace("-", " ").split() if len(token) >= 2]
        if tokens:
            distinct_hits = sum(token in title_folded or token in text_folded for token in tokens)
            required = 1 if len(tokens) == 1 else max(2, (len(tokens) * 3 + 4) // 5)
            if distinct_hits >= required:
                return True
    return False


def is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (ValueError, OSError):
        return False


def extract_text(raw: bytes, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "canvas", "template", "nav", "footer"]):
        node.decompose()
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    text = " ".join(soup.get_text(" ", strip=True).split())[:MAX_TEXT_CHARS]
    return (title or url)[:300], text


class LiveFetcher:
    def __init__(self, user_agent: str, timeout: int = 20):
        self.user_agent = user_agent
        self.timeout = timeout
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_request: dict[str, float] = {}

    def _request(self, url: str, accepted: tuple[str, ...]) -> tuple[requests.Response, bytes]:
        current = normalize_url(url)
        session = requests.Session()
        for _ in range(6):
            if not is_public_url(current):
                raise ValueError("目标不是公网 HTTP/HTTPS 地址")
            response = session.get(
                current,
                headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
                timeout=(8, self.timeout),
                stream=True,
                allow_redirects=False,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise ValueError("重定向缺少 Location")
                current = normalize_url(urljoin(current, location))
                continue
            content_type = response.headers.get("Content-Type", "").lower()
            if accepted and not any(value in content_type for value in accepted):
                response.close()
                raise ValueError(f"不支持的内容类型: {content_type or 'unknown'}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(65_536):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    response.close()
                    raise ValueError("页面超过 5 MB 限制")
                chunks.append(chunk)
            response.url = current
            return response, b"".join(chunks)
        raise ValueError("重定向次数过多")

    def _allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            parser = self._robots.get(origin)
        if parser is None:
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                response, raw = self._request(robots_url, ("text/plain", "text/"))
                if response.status_code in {401, 403}:
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif response.status_code >= 400:
                    parser.parse([])
                else:
                    parser.parse(raw.decode("utf-8", errors="replace").splitlines())
            except Exception:
                parser.parse([])
            with self._robots_lock:
                self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    def fetch(self, result: object, query: str, discovered_at: str) -> FetchResult:
        url = normalize_url(str(getattr(result, "url")))
        fallback_title = str(getattr(result, "title", url))
        if not is_public_url(url):
            return FetchResult("blocked", url, fallback_title, error="非公网或不安全 URL")
        if not self._allowed(url):
            return FetchResult("blocked", url, fallback_title, error="robots.txt 禁止抓取")
        host = urlsplit(url).netloc
        with self._robots_lock:
            host_lock = self._host_locks.setdefault(host, threading.Lock())
        try:
            with host_lock:
                wait = 0.35 - (time.monotonic() - self._last_request.get(host, 0.0))
                if wait > 0:
                    time.sleep(wait)
                response, raw = self._request(url, ("text/html", "application/xhtml+xml"))
                self._last_request[host] = time.monotonic()
            if response.status_code >= 400:
                return FetchResult("failed", url, fallback_title, response.status_code, error=f"HTTP {response.status_code}")
            title, content = extract_text(raw, response.url)
            if len(content) < 100:
                return FetchResult("failed", url, title, response.status_code, error="可提取正文不足 100 字符")
            normalized_url = normalize_url(response.url)
            document = LiveDocument(
                document_id=sha256(normalized_url.encode()).hexdigest(),
                url=normalized_url,
                title=title,
                content=content,
                summary=content[:500],
                query=query,
                source_engines=tuple(getattr(result, "engines", ())),
                discovered_at=discovered_at,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                http_status=response.status_code,
                content_hash=sha256(content.encode()).hexdigest(),
                language=detect_language(content),
            )
            return FetchResult("success", normalized_url, title, response.status_code, document=document)
        except Exception as exc:
            return FetchResult("failed", url, fallback_title, error=str(exc))
