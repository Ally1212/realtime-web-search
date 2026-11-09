"""Daily asset budget for separately approved screenshot and HTML capture.

This contract never captures media itself. Original videos and web images are
not valid asset kinds, and exhausting this budget only denies further assets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .google_domain import DomainError, identity, parse_time, text, utc_text

DEFAULT_DAILY_BYTES = 250_000_000
DEFAULT_DAILY_PROCESSING_SECONDS = 60 * 60
ASSET_KINDS = frozenset({
    "viewport_screenshot",
    "full_page_screenshot",
    "required_html",
})


def _day(value):
    return parse_time(value).astimezone(timezone.utc).date()


@dataclass(frozen=True)
class AssetGrant:
    grant_id: str
    tenant_id: str
    source: str
    asset_kinds: frozenset[str]
    use: str
    expires_at: str
    retention_days: int
    separate_full_page_approval_ref: str | None = None
    separate_full_page_approval_expires_at: str | None = None

    def __post_init__(self):
        for name in ("grant_id", "tenant_id", "source", "use"):
            text(getattr(self, name), name)
        kinds = frozenset(self.asset_kinds)
        if not kinds or not kinds <= ASSET_KINDS:
            raise DomainError(
                "invalid_argument", "Asset grant contains an unsupported kind")
        if type(self.retention_days) is not int or not 1 <= self.retention_days <= 365:
            raise DomainError(
                "invalid_argument", "Asset retention must be between 1 and 365 days")
        expires = parse_time(self.expires_at)
        if self.use != "internal_processing":
            raise DomainError(
                "use_not_permitted",
                "Asset storage requires the approved internal processing use",
                403,
            )
        if (self.separate_full_page_approval_ref is None) != (
                self.separate_full_page_approval_expires_at is None):
            raise DomainError(
                "invalid_argument",
                "Full-page approval reference and expiry must be supplied together",
            )
        if self.separate_full_page_approval_ref is not None:
            text(
                self.separate_full_page_approval_ref,
                "separate_full_page_approval_ref",
            )
            approval_expiry = parse_time(
                self.separate_full_page_approval_expires_at)
            if approval_expiry > expires:
                raise DomainError(
                    "invalid_argument",
                    "Full-page approval cannot outlive its asset grant",
                )
            object.__setattr__(
                self,
                "separate_full_page_approval_expires_at",
                utc_text(approval_expiry),
            )
        object.__setattr__(self, "asset_kinds", kinds)
        object.__setattr__(self, "expires_at", utc_text(expires))


@dataclass(frozen=True)
class AssetRequest:
    request_id: str
    tenant_id: str
    source: str
    asset_kind: str
    requested_at: str
    bytes_expected: int
    processing_seconds_expected: int
    full_page: bool = False

    def __post_init__(self):
        for name in ("request_id", "tenant_id", "source"):
            text(getattr(self, name), name)
        if self.asset_kind not in ASSET_KINDS:
            raise DomainError(
                "invalid_argument",
                "Only separately approved screenshots or required HTML are allowed",
            )
        if type(self.full_page) is not bool:
            raise DomainError("invalid_argument", "full_page must be boolean")
        if self.asset_kind == "viewport_screenshot" and self.full_page:
            raise DomainError(
                "invalid_argument", "Viewport screenshot cannot be marked full page")
        if self.asset_kind in {"full_page_screenshot", "required_html"} and not self.full_page:
            raise DomainError(
                "invalid_argument", "Full-page assets must explicitly declare full_page")
        if type(self.bytes_expected) is not int or self.bytes_expected <= 0:
            raise DomainError(
                "invalid_argument", "Asset bytes must be a positive integer")
        if (
                type(self.processing_seconds_expected) is not int
                or self.processing_seconds_expected <= 0):
            raise DomainError(
                "invalid_argument",
                "Asset processing seconds must be a positive integer",
            )
        object.__setattr__(
            self, "requested_at", utc_text(parse_time(self.requested_at)))


class DailyAssetBudget:
    """Atomic in-memory budget; fact-text ingestion is outside this ledger."""

    def __init__(
            self, *, daily_bytes=DEFAULT_DAILY_BYTES,
            daily_processing_seconds=DEFAULT_DAILY_PROCESSING_SECONDS):
        if type(daily_bytes) is not int or daily_bytes <= 0:
            raise DomainError("invalid_argument", "Daily bytes must be positive")
        if (
                type(daily_processing_seconds) is not int
                or daily_processing_seconds <= 0):
            raise DomainError(
                "invalid_argument", "Daily processing seconds must be positive")
        self.daily_bytes = daily_bytes
        self.daily_processing_seconds = daily_processing_seconds
        self._usage = {}
        self._requests = {}

    def reserve(self, grant, request):
        if not isinstance(grant, AssetGrant):
            raise DomainError("invalid_argument", "Expected a typed asset grant")
        if not isinstance(request, AssetRequest):
            raise DomainError("invalid_argument", "Expected a typed asset request")
        prior = self._requests.get(request.request_id)
        if prior is not None:
            if prior[:2] != (grant, request):
                raise DomainError(
                    "idempotency_conflict",
                    "Asset request identity changed grant or payload",
                    409,
                )
            return prior[2]
        if (
                request.tenant_id != grant.tenant_id
                or request.source != grant.source):
            raise DomainError(
                "not_authorized",
                "Asset request tenant or source differs from its grant",
                403,
            )
        if request.asset_kind not in grant.asset_kinds:
            raise DomainError(
                "use_not_permitted",
                "Asset kind is not approved for this source",
                403,
            )
        requested = parse_time(request.requested_at)
        if parse_time(grant.expires_at) <= requested:
            raise DomainError("expired", "Asset grant has expired", 410)
        if request.full_page:
            approval_expiry = grant.separate_full_page_approval_expires_at
            if (
                    grant.separate_full_page_approval_ref is None
                    or approval_expiry is None
                    or parse_time(approval_expiry) <= requested):
                raise DomainError(
                    "use_not_permitted",
                    "Full-page capture requires a current separate approval",
                    403,
                )
        key = (request.tenant_id, request.source, _day(request.requested_at))
        used_bytes, used_seconds = self._usage.get(key, (0, 0))
        next_bytes = used_bytes + request.bytes_expected
        next_seconds = used_seconds + request.processing_seconds_expected
        if (
                next_bytes > self.daily_bytes
                or next_seconds > self.daily_processing_seconds):
            raise DomainError(
                "quota_exceeded",
                "Daily asset budget is exhausted; fact-text ingestion may continue",
                429,
            )
        self._usage[key] = (next_bytes, next_seconds)
        retention_deadline = min(
            requested + timedelta(days=grant.retention_days),
            parse_time(grant.expires_at),
        )
        result = {
            "request_id": request.request_id,
            "asset_id": identity("google-asset", {
                "grant_id": grant.grant_id,
                "request_id": request.request_id,
                "kind": request.asset_kind,
            }),
            "asset_kind": request.asset_kind,
            "full_page": request.full_page,
            "full_page_approval_ref": grant.separate_full_page_approval_ref,
            "retention_expires_at": utc_text(retention_deadline),
            "bytes_reserved": request.bytes_expected,
            "processing_seconds_reserved": request.processing_seconds_expected,
            "fact_text_ingest_blocked": False,
            "budget": self.status(
                request.tenant_id, request.source,
                at=request.requested_at,
            ),
        }
        self._requests[request.request_id] = (grant, request, result)
        return result

    def status(self, tenant_id, source, *, at):
        text(tenant_id, "tenant_id")
        text(source, "source")
        key = (tenant_id, source, _day(at))
        used_bytes, used_seconds = self._usage.get(key, (0, 0))
        byte_ratio = used_bytes / self.daily_bytes
        time_ratio = used_seconds / self.daily_processing_seconds
        ratio = max(byte_ratio, time_ratio)
        threshold = (
            "exhausted" if ratio >= 1
            else "warning_80" if ratio >= 0.8
            else "ok"
        )
        return {
            "utc_day": _day(at).isoformat(),
            "bytes_used": used_bytes,
            "bytes_limit": self.daily_bytes,
            "bytes_remaining": max(0, self.daily_bytes - used_bytes),
            "processing_seconds_used": used_seconds,
            "processing_seconds_limit": self.daily_processing_seconds,
            "processing_seconds_remaining": max(
                0, self.daily_processing_seconds - used_seconds),
            "threshold": threshold,
            "asset_ingest_blocked_at_100_percent": ratio >= 1,
            "fact_text_ingest_blocked": False,
        }
