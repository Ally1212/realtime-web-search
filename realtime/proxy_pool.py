from __future__ import annotations

import json
import os
import random
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .config import Config


PRIVATE_PARAMS = {
    "protocol": "http,socks5",
    "fresh_within": "120",
    "sort": "quality",
    "order": "desc",
    "limit": "1000",
    "format": "json",
}

PUBLIC_PARAMS = {
    "protocol": "http,socks5",
    "min_quality": "70",
    "max_latency": "2000",
    "min_streak": "3",
    "supports_https": "true",
    "threat_free": "true",
    "fresh_within": "30",
    "sort": "stability",
    "order": "desc",
    "limit": "1000",
    "format": "json",
}


class ProxyApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class ProxyRecord:
    host: str
    port: int
    protocol: str
    quality: int = 0
    latency_ms: int = 0
    last_checked: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProxyRecord":
        host = str(value.get("host") or "").strip()
        port = int(value.get("port") or 0)
        protocol = str(value.get("protocol") or "").lower()
        if not host or not (1 <= port <= 65535) or protocol not in {"http", "socks5"}:
            raise ValueError("invalid proxy record")
        return cls(
            host=host,
            port=port,
            protocol=protocol,
            quality=int(value.get("qualityScore") or value.get("quality") or 0),
            latency_ms=int(value.get("latencyMs") or value.get("latency_ms") or 0),
            last_checked=str(value.get("lastChecked") or value.get("last_checked") or ""),
        )

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}/{self.protocol}"

    def fresh(self, max_minutes: int) -> bool:
        checked = _parse_time(self.last_checked)
        if checked is None:
            return False
        return (datetime.now(timezone.utc) - checked).total_seconds() <= max_minutes * 60


class ProxyApiClient:
    def __init__(self, config: Config, session: requests.Session | None = None):
        self.base = config.proxy_api_base.rstrip("/")
        if self.base.endswith("/v1"):
            self.base = self.base[:-3]
        self.api_key = config.proxy_api_key
        self.session = session or requests.Session()

    def fetch_all(self, profile: str) -> tuple[list[ProxyRecord], str | None]:
        if not self.api_key:
            raise ProxyApiError("proxy API credential is missing", 401)
        if profile == "private":
            path, base_params = "/v1/private/proxies", PRIVATE_PARAMS
        elif profile == "public":
            path, base_params = "/v1/proxies", PUBLIC_PARAMS
        else:
            raise ValueError(f"unknown proxy profile: {profile}")
        cursor: str | None = None
        records: dict[str, ProxyRecord] = {}
        request_id: str | None = None
        while True:
            params = dict(base_params)
            if cursor:
                params["cursor"] = cursor
            try:
                response = self.session.get(
                    f"{self.base}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=(5, 20),
                )
            except requests.RequestException as exc:
                raise ProxyApiError(f"proxy API transport error: {type(exc).__name__}") from exc
            request_id = response.headers.get("X-Request-ID") or request_id
            if response.status_code != 200:
                retry_after = None
                if response.status_code == 429:
                    try:
                        retry_after = float(response.headers.get("Retry-After", "60"))
                    except ValueError:
                        retry_after = 60.0
                raise ProxyApiError(
                    f"proxy API returned HTTP {response.status_code}", response.status_code, retry_after
                )
            try:
                payload = response.json()
                data = payload["data"]
                meta = payload["meta"]
                if not isinstance(data, list) or not isinstance(meta, dict):
                    raise TypeError
            except (ValueError, KeyError, TypeError) as exc:
                raise ProxyApiError("proxy API returned an invalid JSON contract", 200) from exc
            for raw in data:
                try:
                    record = ProxyRecord.from_dict(raw)
                except (ValueError, TypeError):
                    continue
                records[record.key] = record
            next_cursor = meta.get("nextCursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return list(records.values()), request_id


class ProxyCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    def path(self, profile: str) -> Path:
        return self.directory / f"{profile}.json"

    def publish(self, profile: str, records: list[ProxyRecord], request_id: str | None) -> None:
        payload = {
            "version": 1,
            "profile": profile,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "records": [record.__dict__ for record in records],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with tempfile.NamedTemporaryFile("w", dir=self.directory, delete=False, encoding="utf-8") as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, self.path(profile))

    def load(self, profile: str) -> tuple[datetime | None, list[ProxyRecord]]:
        try:
            payload = json.loads(self.path(profile).read_text(encoding="utf-8"))
            synced_at = _parse_time(payload.get("synced_at"))
            records = [ProxyRecord(**value) for value in payload.get("records", [])]
            return synced_at, records
        except (OSError, ValueError, TypeError, KeyError):
            return None, []

    def stats(self, profile: str) -> dict[str, object]:
        synced_at, records = self.load(profile)
        max_age = 120 if profile == "private" else 30
        fresh = [record for record in records if record.fresh(max_age)]
        return {
            "profile": profile,
            "synced_at": synced_at.isoformat() if synced_at else None,
            "total": len(records),
            "fresh": len(fresh),
            "http": sum(record.protocol == "http" for record in fresh),
            "socks5": sum(record.protocol == "socks5" for record in fresh),
            "usable": bool(fresh and synced_at and (datetime.now(timezone.utc) - synced_at).total_seconds() <= 7200),
        }


class ProxySynchronizer:
    def __init__(self, config: Config, client: ProxyApiClient | None = None):
        self.config = config
        self.client = client or ProxyApiClient(config)
        self.cache = ProxyCache(config.proxy_cache_dir)
        self._lock = threading.Lock()

    def sync(self, profile: str, force: bool = False) -> int:
        with self._lock:
            synced_at, current = self.cache.load(profile)
            if not force and synced_at and (
                datetime.now(timezone.utc) - synced_at
            ).total_seconds() < self.config.proxy_sync_seconds:
                return len(current)
            records, request_id = self.client.fetch_all(profile)
            self.cache.publish(profile, records, request_id)
            return len(records)


class ProxyPool:
    """Reads atomically published proxy caches and keeps failures process-local."""

    def __init__(self, config: Config):
        self.config = config
        self.cache = ProxyCache(config.proxy_cache_dir)
        self._cooldown: dict[tuple[str, str], float] = {}
        self._sticky: dict[tuple[str, str], tuple[str, float]] = {}
        self._records: dict[str, dict[str, ProxyRecord]] = {}
        self._mtime: dict[str, float] = {}
        self._lock = threading.Lock()

    def _reload(self, profile: str) -> list[ProxyRecord]:
        path = self.cache.path(profile)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        if self._mtime.get(profile) != mtime:
            synced_at, records = self.cache.load(profile)
            max_age = 120 if profile == "private" else 30
            if not synced_at or (datetime.now(timezone.utc) - synced_at).total_seconds() > 7200:
                records = []
            records = [record for record in records if record.fresh(max_age)]
            self._records[profile] = {record.key: record for record in records}
            self._mtime[profile] = mtime
        return list(self._records.get(profile, {}).values())

    def choose(self, profile: str, domain: str) -> tuple[str, str] | None:
        if profile == "direct":
            return None
        now = time.monotonic()
        with self._lock:
            records = self._reload(profile)
            sticky = self._sticky.get((profile, domain))
            if sticky and sticky[1] > now:
                record = self._records.get(profile, {}).get(sticky[0])
                if record and max(
                    self._cooldown.get((record.key, domain), 0),
                    self._cooldown.get((record.key, "*"), 0),
                ) <= now:
                    return self._url(profile, record), record.key
            eligible = [
                record for record in records
                if max(
                    self._cooldown.get((record.key, domain), 0),
                    self._cooldown.get((record.key, "*"), 0),
                ) <= now
            ]
            if not eligible:
                return None
            # Prefer HTTP because it is cheapest; use measured latency for load tests.
            if self.config.proxy_sort == "latency":
                eligible.sort(
                    key=lambda item: (
                        item.protocol != "http", item.latency_ms or 999999, -item.quality
                    )
                )
            else:
                eligible.sort(
                    key=lambda item: (
                        item.protocol != "http", -item.quality, item.latency_ms or 999999
                    )
                )
            top = eligible[: max(1, min(self.config.proxy_selection_window, len(eligible)))]
            record = random.choice(top)
            if self.config.proxy_sticky_seconds > 0:
                self._sticky[(profile, domain)] = (
                    record.key, now + self.config.proxy_sticky_seconds
                )
            return self._url(profile, record), record.key

    def _url(self, profile: str, record: ProxyRecord) -> str:
        scheme = "socks5h" if record.protocol == "socks5" else "http"
        auth = ""
        if profile == "private" and self.config.proxy_username and self.config.proxy_password:
            auth = f"{quote(self.config.proxy_username, safe='')}:{quote(self.config.proxy_password, safe='')}@"
        return f"{scheme}://{auth}{record.host}:{record.port}"

    def report(self, key: str, domain: str, status: int | None = None, failed: bool = False) -> None:
        seconds = 0
        if failed:
            seconds = 300
        elif status == 407:
            seconds = 7200
        elif status == 429:
            seconds = 600
        elif status == 403:
            seconds = 300
        if seconds:
            scope = "*" if failed or status == 407 else domain
            with self._lock:
                self._cooldown[(key, scope)] = time.monotonic() + seconds
                self._sticky.pop(("private", domain), None)
                self._sticky.pop(("public", domain), None)
