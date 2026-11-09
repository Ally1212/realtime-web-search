"""Typed approved Merchant API report contracts.

Only an explicitly approved read-only adapter can return these rows. This
module never exposes the retired Content API, accepts arbitrary source methods,
or fabricates product/competitor data.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_domain import (
    DomainError, MetricValue, canonical_json, decimal, frozen_json,
    identity, parse_time, plain_json, text, utc_text,
)
from .google_evidence import page_limit
from .google_rights import authorize

MERCHANT_VERSION = "google-merchant-report-1"
MERCHANT_ROUTES = frozenset({"/merchant/reports"})
DIMENSIONS = frozenset({"date", "product_id", "country", "device"})
METRICS = frozenset({
    "impressions", "clicks", "cost_micros", "conversions", "sales_micros",
})
MONEY_METRICS = frozenset({"cost_micros", "sales_micros"})
UNITS = {
    "impressions": "count", "clicks": "count", "conversions": "count",
    "cost_micros": "money_micros", "sales_micros": "money_micros",
}
MAX_ROWS = 10000


def _denied(message):
    return DomainError("use_not_permitted", message, 403)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DomainError("invalid_argument", "Merchant date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DomainError("invalid_argument", "Invalid Merchant date") from None


@dataclass(frozen=True)
class MerchantAccessPolicy:
    access_id: str
    source_adapter: str
    source_version: str
    merchant_id: str
    caller_subject_refs: tuple[str, ...]
    approved_templates: tuple[str, ...]
    approved_report_versions: tuple[str, ...]
    approved_scopes: tuple[dict, ...]
    approval_ref: str
    expires_at: str
    approved: bool = False
    production_approved: bool = False
    revoked: bool = False

    def __post_init__(self):
        for name in (
                "access_id", "source_adapter", "source_version", "merchant_id",
                "approval_ref"):
            text(getattr(self, name), name, maximum=256)
        if not re.fullmatch(r"\d{10}", self.merchant_id):
            raise DomainError("invalid_argument", "Policy merchant_id must be ten digits")
        for name in (
                "caller_subject_refs", "approved_templates",
                "approved_report_versions"):
            values = tuple(getattr(self, name))
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise DomainError("invalid_argument", f"{name} is invalid")
            if len(set(values)) != len(values):
                raise DomainError("invalid_argument", f"{name} must be unique")
            object.__setattr__(self, name, values)
        scopes = tuple(self.approved_scopes)
        if not scopes:
            raise DomainError("invalid_argument", "Approved Merchant scopes are required")
        checked = []
        for item in scopes:
            if not isinstance(item, Mapping):
                raise DomainError("invalid_argument", "Merchant scope must be an object")
            try:
                parsed = MerchantScope(**plain_json(item))
            except TypeError:
                raise DomainError(
                    "invalid_argument", "Invalid Merchant policy scope") from None
            if parsed.merchant_id != self.merchant_id:
                raise DomainError(
                    "invalid_argument", "Merchant policy/scope binding mismatch")
            checked.append(frozen_json(parsed.to_dict()))
        object.__setattr__(self, "approved_scopes", tuple(checked))
        for name in ("approved", "production_approved", "revoked"):
            if type(getattr(self, name)) is not bool:
                raise DomainError("invalid_argument", f"{name} must be boolean")
        object.__setattr__(self, "expires_at", utc_text(self.expires_at))

    def to_dict(self):
        return {
            f.name: plain_json(getattr(self, f.name))
            for f in fields(self)
        }

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, Mapping):
            raise _denied("Merchant access policy has not been approved")
        try:
            return cls(**plain_json(value))
        except (TypeError, DomainError):
            raise _denied("Merchant access policy is incomplete or invalid") from None

    def check(self, principal, request, *, now):
        if (
                not self.approved or not self.production_approved or self.revoked
                or parse_time(self.expires_at) <= parse_time(now)
                or principal.tenant_id != request["tenant_id"]
                or principal.subject_ref not in self.caller_subject_refs
                or request["scope"]["merchant_id"] != self.merchant_id
                or request["scope"]["template_version"] not in self.approved_templates
                or request["scope"]["report_version"] not in self.approved_report_versions
                or request["scope"] not in self.approved_scopes):
            raise _denied("Merchant caller/asset/template/window admission does not match")


@dataclass(frozen=True)
class MerchantEvidence:
    adapter_id: str
    adapter_version: str
    source_api_version: str
    contract_ref: str
    dataset_id: str
    source_observed_at: str
    evidence_expires_at: str
    revision: str
    provider_request_id: str | None = None

    def __post_init__(self):
        for name in (
                "adapter_id", "adapter_version", "source_api_version",
                "contract_ref", "dataset_id", "revision"):
            text(getattr(self, name), name)
        observed, expires = (
            parse_time(self.source_observed_at),
            parse_time(self.evidence_expires_at),
        )
        if expires <= observed:
            raise DomainError(
                "invalid_argument", "Merchant evidence expiry must follow observation")
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "evidence_expires_at", utc_text(expires))
        if self.provider_request_id is not None:
            text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class MerchantScope:
    kind: str
    merchant_id: str
    template_version: str
    report_version: str
    period_start: str
    period_end: str
    source_timezone: str
    country: str
    currency: str

    def __post_init__(self):
        if self.kind != "report":
            raise DomainError("invalid_argument", "Only Merchant reports are covered")
        for f in fields(self):
            text(getattr(self, f.name), f.name, maximum=256)
        if not re.fullmatch(r"\d{10}", self.merchant_id):
            raise DomainError("invalid_argument", "merchant_id must be ten digits")
        if not re.fullmatch(r"[A-Z]{2}", self.country):
            raise DomainError("invalid_argument", "country must be ISO 3166-1 alpha-2")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise DomainError("invalid_argument", "currency must be an ISO code")
        start, end = _day(self.period_start), _day(self.period_end)
        if start >= end:
            raise DomainError(
                "invalid_argument", "Merchant period must be left-closed/right-open")
        try:
            ZoneInfo(self.source_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "Merchant source_timezone must be IANA") from None

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class MerchantRow:
    row_id: str
    dimensions: dict
    metrics: tuple[MetricValue, ...]
    row_role: str = "snapshot"
    product_title: str | None = None

    def __post_init__(self):
        text(self.row_id, "row_id")
        if not isinstance(self.dimensions, Mapping) or not self.dimensions:
            raise DomainError("invalid_argument", "Merchant dimensions are required")
        for name, value in self.dimensions.items():
            text(name, "dimension name", maximum=256)
            if value is not None:
                text(value, name, maximum=4096)
        object.__setattr__(self, "dimensions", frozen_json(self.dimensions))
        if self.row_role not in {"snapshot", "revision_delta"}:
            raise DomainError("invalid_argument", "Unknown Merchant row role")
        if self.product_title is not None:
            text(self.product_title, "product_title", maximum=1024)
        metrics = tuple(self.metrics)
        if not metrics or any(not isinstance(m, MetricValue) for m in metrics):
            raise DomainError("invalid_argument", "Merchant metrics must be typed")
        ids = [m.metric_scope["metric"] for m in metrics]
        if len(set(ids)) != len(ids):
            raise DomainError("invalid_argument", "Duplicate Merchant metric")
        object.__setattr__(self, "metrics", metrics)

    def to_dict(self):
        return {
            "row_id": self.row_id,
            "dimensions": plain_json(self.dimensions),
            "metrics": {m.metric_scope["metric"]: m.to_dict() for m in self.metrics},
            "row_role": self.row_role,
            "product_title": self.product_title,
        }


@dataclass(frozen=True)
class MerchantResult:
    evidence: MerchantEvidence
    scope: MerchantScope
    rows: tuple[MerchantRow, ...]
    response_complete: bool
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, MerchantEvidence) or not isinstance(
                self.scope, MerchantScope):
            raise DomainError("invalid_argument", "Merchant result requires typed values")
        rows = tuple(self.rows)
        if len(rows) > MAX_ROWS or any(not isinstance(r, MerchantRow) for r in rows):
            raise DomainError("invalid_argument", "Merchant result exceeds row contract")
        if len({r.row_id for r in rows}) != len(rows):
            raise DomainError("incompatible_scope", "Duplicate Merchant row ID", 409)
        grains = set()
        for row in rows:
            if set(row.dimensions) - DIMENSIONS:
                raise _denied("Unapproved Merchant dimension")
            if "date" not in row.dimensions:
                raise DomainError("invalid_argument", "Merchant rows require a date")
            day = _day(row.dimensions["date"])
            if not _day(self.scope.period_start) <= day < _day(self.scope.period_end):
                raise DomainError(
                    "incompatible_scope", "Merchant row escapes the window", 409)
            grain = canonical_json(row.dimensions)
            if grain in grains:
                raise DomainError(
                    "incompatible_scope", "Duplicate Merchant report grain", 409)
            grains.add(grain)
            expected = {
                **self.scope.to_dict(),
                "row_id": row.row_id,
                "row_role": row.row_role,
                "dimensions": plain_json(row.dimensions),
                "revision": self.evidence.revision,
            }
            for metric in row.metrics:
                metric_id = metric.metric_scope["metric"]
                if metric_id not in METRICS:
                    raise DomainError(
                        "invalid_argument", "Merchant metric is not allowed")
                if (set(metric.metric_scope) != set(expected) | {"metric"}
                        or any(metric.metric_scope.get(k) != v for k, v in expected.items())):
                    raise DomainError(
                        "incompatible_scope", "Merchant metric scope mismatch", 409)
                if metric.unit != UNITS[metric_id]:
                    raise DomainError(
                        "invalid_argument", "Merchant metric unit mismatch")
                if metric.value is not None:
                    value = decimal(metric.value)
                    if row.row_role == "snapshot" and value < 0:
                        raise DomainError(
                            "invalid_argument",
                            "Negative Merchant values require revision_delta")
                    if metric_id in MONEY_METRICS | {"impressions", "clicks", "conversions"}:
                        if value != value.to_integral_value():
                            raise DomainError(
                                "invalid_argument",
                                "Merchant count/micros metrics must be integral")
        if type(self.response_complete) is not bool:
            raise DomainError("invalid_argument", "response_complete must be boolean")
        limitations = tuple(self.limitations)
        for item in limitations:
            text(item, "limitation")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "limitations", limitations)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "evidence": self.evidence.to_dict(),
            "scope": self.scope.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
            "response_complete": self.response_complete,
            "limitations": list(self.limitations),
        }


class MerchantService:
    def __init__(self, registry, adapter, *, cursor_codec, clock):
        self.registry = registry
        self.adapter = adapter
        self.cursor_codec = cursor_codec
        self.clock = clock

    @staticmethod
    def route(path):
        if path not in MERCHANT_ROUTES:
            raise DomainError("not_found", "No Merchant route matches", 404)
        return "report"

    def _request(self, parameters):
        scope_fields = set(MerchantScope.__dataclass_fields__) - {"kind"}
        allowed = scope_fields | {
            "source", "source_version", "asset", "access_id", "limit", "cursor",
        }
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument",
                "Unknown Merchant parameter; raw source proxying is disabled")
        for name in scope_fields | {
                "source", "source_version", "asset", "access_id"}:
            text(parameters.get(name), name, maximum=256)
        scope = MerchantScope(
            kind="report",
            **{name: parameters[name] for name in scope_fields})
        if parameters["asset"] != "merchant:" + scope.merchant_id:
            raise DomainError(
                "incompatible_scope", "Merchant asset and merchant_id differ", 409)
        return {
            "scope": scope.to_dict(),
            "access_id": parameters["access_id"],
        }

    def _admit(self, principal, parameters, request, *, now):
        source = self.registry.source(
            principal.tenant_id, parameters["source"], parameters["source_version"])
        grants = []
        for grant_id in sorted(principal.grant_ids):
            grant = self.registry.grant(principal.tenant_id, grant_id)
            if (grant.source_adapter == source.adapter_id
                    and grant.source_version == source.adapter_version
                    and grant.asset_id == parameters["asset"]
                    and "merchant_report" in grant.fields):
                grants.append(grant)
        if "reader" not in principal.roles or not grants:
            raise DomainError(
                "not_authorized", "No Merchant asset grant covers this report", 403)
        grant = grants[0]
        right = authorize(
            principal, grant, source, "merchant_report", "internal_display",
            now=now, production=True)
        deadlines = [grant.expires_at]
        if right.retention_deadline is not None:
            deadlines.append(right.retention_deadline)
        policy = self.registry.merchant_access(
            principal.tenant_id, parameters["access_id"])
        if (
                policy.source_adapter != source.adapter_id
                or policy.source_version != source.adapter_version):
            raise _denied("Merchant access policy does not match this source")
        policy.check(
            principal,
            {"tenant_id": principal.tenant_id, "scope": request["scope"]},
            now=now,
        )
        deadlines.append(parse_time(policy.expires_at))
        return source, grant, policy, min(deadlines)

    def _evidence(self, result, *, source, deadline):
        evidence = result.evidence
        now = parse_time(self.clock())
        expiry = parse_time(evidence.evidence_expires_at)
        if (evidence.adapter_id != source.adapter_id
                or evidence.adapter_version != source.adapter_version
                or evidence.source_api_version != source.source_api_version
                or evidence.contract_ref != getattr(self.adapter, "contract_ref", None)
                or evidence.dataset_id != getattr(self.adapter, "dataset_id", None)):
            raise DomainError(
                "incompatible_scope", "Merchant evidence does not match adapter", 409)
        if parse_time(evidence.source_observed_at) > now:
            raise DomainError(
                "incompatible_scope", "Merchant observation is in the future", 409)
        if expiry <= now:
            raise DomainError("expired", "Merchant evidence has expired", 410)
        if expiry > deadline:
            raise _denied("Merchant evidence exceeds the approved retention")
        return now, expiry

    def read(self, principal, path, parameters, *, now):
        request = self._request(parameters)
        self.registry.assert_current(principal, now=now)
        source, grant, policy, deadline = self._admit(
            principal, parameters, request, now=now)
        result = self.adapter.read_report(principal.tenant_id, request)
        if not isinstance(result, MerchantResult):
            raise DomainError("source_error", "Merchant adapter returned untyped data", 503)
        if result.scope.to_dict() != request["scope"]:
            raise DomainError(
                "incompatible_scope", "Merchant scope differs from request", 409)
        current, expiry = self._evidence(result, source=source, deadline=deadline)
        self.registry.assert_current(principal, now=current)
        _, _, current_policy, current_deadline = self._admit(
            principal, parameters, request, now=current)
        if current_policy != policy:
            raise _denied("Merchant policy changed while reading")
        if current_deadline <= parse_time(self.clock()):
            raise DomainError("expired", "Merchant access expired while reading", 410)
        evidence_id = identity("merchant-report-evidence", result.to_dict())
        limit = page_limit(parameters.get("limit"))
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {**request, "limit": limit},
            "sort": ["row_id"],
            "source": f"merchant:{source.adapter_id}:{source.adapter_version}",
            "manifest_id": evidence_id,
        }
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered", "Merchant cursor signing is not initialized", 503)
        after = None
        if "cursor" in parameters:
            position = self.cursor_codec.decode(
                parameters["cursor"], binding=binding, now=current)
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError("invalid_argument", "Invalid Merchant cursor")
            after = position[0]
        rows = sorted(
            (r for r in result.rows if after is None or r.row_id > after),
            key=lambda r: r.row_id)
        page, more = rows[:limit], len(rows) > limit
        cursor = self.cursor_codec.encode(
            binding=binding, position=[page[-1].row_id],
            expires_at=expiry, now=current,
        ) if more else None
        return {
            "merchant_version": MERCHANT_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": result.evidence.contract_ref,
            "dataset_id": result.evidence.dataset_id,
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": evidence_id,
            "scope": result.scope.to_dict(),
            "rows": [r.to_dict() for r in page],
            "next_cursor": cursor,
            "page_row_count": len(page),
            "total_returned_rows": len(result.rows),
            "response_complete": result.response_complete,
            "coverage": {
                "status": "merchant_report" if result.response_complete else "partial_merchant_report",
                "scope": "approved_merchant_report_rows",
            },
            "limitations": list(result.limitations),
            "not_claimed": [
                "content_api_compatibility", "competitor_product_sales",
                "product_write_or_publish", "all_marketplace_inventory",
                "merchant_revenue_equals_business_revenue",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }
