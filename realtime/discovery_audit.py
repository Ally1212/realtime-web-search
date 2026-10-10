"""Append-only auxiliary discovery evidence, separate from Google Web SERPs."""
from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from .fetcher import is_public_url, normalize_url


AUDIT_DATA_KEYS = {
    'request_started': {'method', 'request_host', 'request_url_hash'},
    'request_finished': {'attempted', 'http_status', 'redirect_hops', 'final_response_host',
                         'url_captcha', 'error_type', 'elapsed_seconds'},
    'content_examined': {'bytes_examined', 'prefix_sha256', 'explicit_captcha'},
    'discovery_result': {'query_hash', 'locale', 'cache_hit', 'offline', 'error_type',
                         'raw_result_count', 'result_urls', 'eligible_result_urls',
                         'input_result_urls', 'request_ids', 'elapsed_seconds'},
}


class DiscoveryAuditFailure(RuntimeError):
    """Audit persistence failed; do not expose underlying credentials or payloads."""


class DiscoveryAudit:
    def __init__(self, recorder: Callable[..., None] | None):
        self.recorder = recorder
        self.local = threading.local()

    def _emit(self, scope: dict[str, Any], kind: str, data: dict[str, Any],
              request_id: str | None = None) -> None:
        if self.recorder is None:
            return
        event_id = str(uuid5(NAMESPACE_URL, f"discovery:{kind}:{request_id}")) if request_id else str(uuid4())
        try:
            self.recorder(
                event_id=event_id, trace_id=scope['trace_id'], request_id=request_id,
                kind=kind, source=scope['source'], operation=scope['operation'],
                occurred_at=datetime.now(timezone.utc).isoformat(), data=data,
            )
        except Exception:
            raise DiscoveryAuditFailure('auxiliary discovery audit persistence failed') from None

    @staticmethod
    def urls(results: list[Any], *, check_public: bool = True) -> tuple[list[str], list[str]]:
        urls, eligible = set(), set()
        for result in results:
            raw = str(result.url)
            try:
                parsed = urlsplit(raw)
                if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                    continue
                normalized = normalize_url(raw)
                urls.add(normalized)
                if check_public and is_public_url(raw):
                    eligible.add(normalized)
            except (ValueError, TypeError):
                continue
        return sorted(urls), sorted(eligible)

    @contextmanager
    def phase(self, source: str, operation: str, query: str = '', locale: str = ''):
        if self.recorder is None:
            yield {}
            return
        scope = dict(trace_id=str(uuid4()), source=source, operation=operation, request_ids=[])
        phase = dict(results=[], input_results=[], cache_hit=False, offline=False, error_type=None)
        previous = getattr(self.local, 'scope', None)
        self.local.scope = scope
        started = time.monotonic()
        try:
            yield phase
        except Exception as exc:
            phase['error_type'] = type(exc).__name__
            raise
        finally:
            self.local.scope = previous
            urls, eligible = self.urls(phase['results'])
            input_urls, _ = self.urls(phase['input_results'], check_public=False)
            self._emit(scope, 'discovery_result', dict(
                query_hash=hashlib.sha256(query.encode()).hexdigest(), locale=locale,
                cache_hit=phase['cache_hit'], offline=phase['offline'], error_type=phase['error_type'],
                raw_result_count=len(phase['results']), result_urls=urls,
                eligible_result_urls=eligible, input_result_urls=input_urls,
                request_ids=scope['request_ids'], elapsed_seconds=time.monotonic() - started,
            ))

    @contextmanager
    def request(self, method: str, url: str):
        scope = getattr(self.local, 'scope', None)
        if self.recorder is None or scope is None:
            yield {}
            return
        request_id = str(uuid4())
        scope['request_ids'].append(request_id)
        # Never persist proxy credentials, request headers, query strings or RPC payloads.
        self._emit(scope, 'request_started', dict(
            method=method.upper(), request_host=(urlsplit(url).hostname or '').lower(),
            request_url_hash=hashlib.sha256(url.encode()).hexdigest(),
        ), request_id)
        record = dict(request_id=request_id, scope=scope, attempted=False,
                      http_status=None, redirect_hops=None, final_response_host=None,
                      url_captcha=False, error_type=None)
        started = time.monotonic()
        try:
            yield record
        except Exception as exc:
            record['error_type'] = type(exc).__name__
            raise
        finally:
            self._emit(scope, 'request_finished', dict(
                attempted=record['attempted'], http_status=record['http_status'],
                redirect_hops=record['redirect_hops'], url_captcha=record['url_captcha'],
                final_response_host=record['final_response_host'],
                error_type=record['error_type'], elapsed_seconds=time.monotonic() - started,
            ), request_id)

    def response(self, record: dict[str, Any], response: Any) -> None:
        if not record:
            return
        status = getattr(response, 'status_code', None)
        record['http_status'] = status if isinstance(status, int) else None
        history = getattr(response, 'history', None)
        record['redirect_hops'] = len(history) if isinstance(history, (list, tuple)) else None
        parsed = urlsplit(str(getattr(response, 'url', '') or ''))
        host = (parsed.hostname or '').lower()
        record['final_response_host'] = host
        record['url_captcha'] = (host == 'google.com' or host.endswith('.google.com')) and parsed.path.lower().startswith('/sorry/')
        response._discovery_audit_request = record

    def content(self, response: Any, content: bytes) -> None:
        if self.recorder is None:
            return
        record = getattr(response, '_discovery_audit_request', None)
        if not isinstance(record, dict) or not record.get('request_id'):
            return
        sample = content[:500_000]
        lowered = sample.lower()
        host = record['final_response_host'] or ''
        google_response = host == 'google.com' or host.endswith('.google.com')
        html = lowered.lstrip().startswith((b'<!doctype html', b'<html'))
        captcha = record['url_captcha'] or (google_response and html and any(marker in lowered for marker in (
            b'unusual traffic', b'our systems have detected', b'g-recaptcha', b'/sorry/',
        )))
        self._emit(record['scope'], 'content_examined', dict(
            bytes_examined=len(sample), prefix_sha256=hashlib.sha256(sample).hexdigest(),
            explicit_captcha=captcha,
        ), record['request_id'])
