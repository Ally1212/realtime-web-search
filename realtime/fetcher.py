from __future__ import annotations

import ipaddress
import io
import json
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, SoupStrainer
from trafilatura import bare_extraction

from .publication import publication_metadata


MAX_DOWNLOAD_BYTES = 5_000_000
MAX_PDF_DOWNLOAD_BYTES = 12_000_000
MAX_TEXT_CHARS = 100_000
MAX_PDF_PAGES = 300
PDF_MEDIA_TYPES = {'application/pdf', 'application/x-pdf', 'text/pdf', 'text/x-pdf'}
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "srsltid",
}


def publication_metadata_for(raw: bytes) -> tuple[str | None, str | None]:
    """Best-effort explicit publication metadata from an HTML response."""
    try:
        return publication_metadata(raw, parser="lxml")
    except Exception:
        return None, None


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


_DNS_CACHE: dict[tuple[str, int], tuple[bool, float]] = {}
_DNS_LOCK = threading.Lock()


def _is_public_endpoint(hostname: str, port: int) -> bool:
    key = (hostname, port)
    now = time.monotonic()
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(key)
        if cached and cached[1] > now:
            return cached[0]
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        result = bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except OSError:
        result = False
    with _DNS_LOCK:
        if len(_DNS_CACHE) >= 32_768:
            for item in [key for key, value in _DNS_CACHE.items() if value[1] <= now][:4096]:
                _DNS_CACHE.pop(item, None)
        _DNS_CACHE[key] = (result, now + (1800 if result else 300))
    return result


def is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return _is_public_endpoint(parsed.hostname.lower(), port)
    except ValueError:
        return False


def _fallback_extract_text(raw: bytes, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "canvas", "template", "nav", "footer"]):
        node.decompose()
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    text = " ".join(soup.get_text(" ", strip=True).split())[:MAX_TEXT_CHARS]
    return (title or url)[:300], text


def _json_ld_article(raw: bytes, url: str, *, parser: str = 'html.parser') -> tuple[str, str] | None:
    soup = BeautifulSoup(raw, parser, parse_only=SoupStrainer('script', attrs={'type': 'application/ld+json'}))
    scripts = [node.string or node.get_text() or '' for node in soup.select('script[type="application/ld+json"]')]
    soup.decompose()

    def objects(value: object):  # type: ignore[no-untyped-def]
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    for script in scripts:
        try:
            # Some publishers emit literal newlines/tabs inside JSON-LD
            # strings. Keep full JSON validation while tolerating only those
            # control characters.
            payload = json.loads(script, strict=False)
        except (ValueError, TypeError):
            continue
        for item in objects(payload):
            body = " ".join(str(item.get("articleBody") or "").split())[:MAX_TEXT_CHARS]
            if len(body) < 100:
                continue
            title = " ".join(str(item.get("headline") or item.get("name") or "").split())
            return (title or url)[:300], body
    return None


def _title_only_metadata(raw: bytes):
    """Use Trafilatura's exact title precedence without unused metadata work."""
    from trafilatura.metadata import examine_meta, extract_meta_json, extract_title
    from trafilatura.utils import load_html
    tree = load_html(raw)
    if tree is None:
        raise ValueError('unparseable HTML')
    metadata = examine_meta(tree)
    try:
        metadata = extract_meta_json(tree, metadata)
    except Exception:
        pass  # Matches extract_metadata()'s JSON metadata fallback.
    if not metadata.title:
        metadata.title = extract_title(tree)
    metadata.clean_and_trim()
    return tree, metadata.title


def extract_text(raw: bytes, url: str, use_trafilatura: bool = True, *, lean_metadata: bool = False) -> tuple[str, str]:
    structured = _json_ld_article(raw, url, parser='lxml' if lean_metadata else 'html.parser')
    if structured:
        return structured
    if use_trafilatura:
        try:
            prepared, prepared_title, title_only = raw, None, False
            if lean_metadata:
                try:
                    prepared, prepared_title = _title_only_metadata(raw)
                    title_only = True
                except Exception:
                    pass  # Fall back to the original metadata path if its API changes.
            document = bare_extraction(
                prepared,
                url=url,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
                deduplicate=True,
                with_metadata=not title_only,
            )
            if document:
                title = " ".join(str((prepared_title if title_only else document.title) or "").split())
                text = " ".join(str(document.text or "").split())[:MAX_TEXT_CHARS]
                if len(text) >= 100:
                    return (title or url)[:300], text
        except Exception:
            pass
    return _fallback_extract_text(raw, url)


def extract_pdf_text(raw: bytes, url: str) -> tuple[str, str]:
    """Extract a bounded, complete text layer from a public PDF."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise ValueError('PDF 已加密')
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f'PDF 超过 {MAX_PDF_PAGES} 页限制')
        metadata = reader.metadata or {}
        title = ' '.join(str(metadata.get('/Title') or '').split())[:300]
        parts = []
        characters = 0
        for page in reader.pages:
            text = ' '.join((page.extract_text() or '').split())
            if not text:
                continue
            remaining = MAX_TEXT_CHARS - characters
            parts.append(text[:remaining])
            characters += min(len(text), remaining)
            if characters >= MAX_TEXT_CHARS:
                break
        return (title or url)[:300], ' '.join(parts)[:MAX_TEXT_CHARS]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('PDF 正文解析失败') from exc


def is_pdf_response(raw: bytes, content_type: str) -> bool:
    media_type = content_type.partition(';')[0].strip().lower()
    return media_type in PDF_MEDIA_TYPES or b'%PDF-' in raw[:1024]


def extract_response_text(
    raw: bytes,
    url: str,
    content_type: str,
    use_trafilatura: bool = True,
    *,
    lean_metadata: bool = False,
) -> tuple[str, str]:
    """Extract supported responses, including PDFs served as generic binary."""
    media_type = content_type.partition(';')[0].strip().lower()
    if is_pdf_response(raw, content_type):
        return extract_pdf_text(raw, url)
    if media_type in {'text/html', 'application/xhtml+xml'}:
        return extract_text(raw, url, use_trafilatura, lean_metadata=lean_metadata)
    raise ValueError(f"不支持的内容类型: {media_type or 'unknown'}")


class LiveFetcher:
    def __init__(self, user_agent: str, timeout: int = 20, use_trafilatura: bool = True, *, reuse_sessions: bool = False, lean_metadata: bool = False):
        self.user_agent = user_agent
        self.timeout = timeout
        self.use_trafilatura = use_trafilatura
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_request: dict[str, float] = {}
        self.reuse_sessions = reuse_sessions
        self.lean_metadata = lean_metadata
        self._sessions: OrderedDict[str, requests.Session] = OrderedDict()
        self._robots_expiry: dict[str, float] = {}

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    def _session(self, url: str) -> requests.Session:
        if not self.reuse_sessions:
            return requests.Session()
        parsed = urlsplit(url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        session = self._sessions.get(origin)
        if session is None:
            if len(self._sessions) >= 64:
                _, old = self._sessions.popitem(last=False)
                old.close()
            session = self._sessions[origin] = requests.Session()
        self._sessions.move_to_end(origin)
        return session

    def _request(self, url: str, accepted: tuple[str, ...]) -> tuple[requests.Response, bytes]:
        current = normalize_url(url)
        session = self._session(current)
        try:
            for _ in range(6):
                if not is_public_url(current):
                    raise ValueError("目标不是公网 HTTP/HTTPS 地址")
                response = session.get(
                    current,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,application/pdf;q=0.8,*/*;q=0.1",
                    },
                    timeout=(8, self.timeout),
                    stream=True,
                    allow_redirects=False,
                )
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    response.close()
                    if not location:
                        raise ValueError("重定向缺少 Location")
                    # Keep one cookie jar for the full transaction. Identity
                    # providers commonly set cross-domain cookies before
                    # redirecting back to the article origin.
                    current = normalize_url(urljoin(current, location))
                    continue
                content_type = response.headers.get("Content-Type", "").lower()
                if accepted and not any(value in content_type for value in accepted):
                    response.close()
                    raise ValueError(f"不支持的内容类型: {content_type or 'unknown'}")
                media_type = content_type.partition(';')[0].strip()
                download_limit = (
                    MAX_PDF_DOWNLOAD_BYTES if media_type in PDF_MEDIA_TYPES
                    else MAX_DOWNLOAD_BYTES
                )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(65_536):
                    if (not chunks and media_type in {'', 'application/octet-stream',
                                                       'binary/octet-stream'}
                            and b'%PDF-' in chunk[:1024]):
                        download_limit = MAX_PDF_DOWNLOAD_BYTES
                    size += len(chunk)
                    if size > download_limit:
                        response.close()
                        raise ValueError(
                            f"页面超过 {download_limit // 1_000_000} MB 限制"
                        )
                    chunks.append(chunk)
                response.url = current
                return response, b"".join(chunks)
            raise ValueError("重定向次数过多")
        finally:
            if not self.reuse_sessions:
                session.close()

    def _allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            parser = self._robots.get(origin)
            if self.reuse_sessions and self._robots_expiry.get(origin, 0) <= time.monotonic():
                parser = None
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
                self._robots_expiry[origin] = time.monotonic() + 21600
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
                response, raw = self._request(
                    url,
                    ("text/html", "application/xhtml+xml", "pdf", "octet-stream"),
                )
                self._last_request[host] = time.monotonic()
            if response.status_code >= 400:
                return FetchResult("failed", url, fallback_title, response.status_code, error=f"HTTP {response.status_code}")
            content_type = response.headers.get('Content-Type', '').lower()
            title, content = extract_response_text(
                raw,
                response.url,
                content_type,
                self.use_trafilatura,
                lean_metadata=self.lean_metadata,
            )
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
