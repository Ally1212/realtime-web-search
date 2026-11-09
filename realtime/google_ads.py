"""Approved managed Ads outputs, never an arbitrary Google Ads API proxy.

Policy records live in the internal versioned rights registry. Caller input
cannot supply project/OAuth bindings, approval flags, GAQL or source methods.
No credentials, collection, scheduler or provider calls are created here.
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

ADS_VERSION = "google-managed-ads-1"
ADS_ROUTES = frozenset({"/ads/keyword-plans", "/ads/reports"})
ADS_MODES = frozenset({"internal_analysis", "report_service"})
REPORT_METRICS = frozenset({
    "clicks", "impressions", "cost_micros", "conversions", "conversion_value",
})
PLAN_METRICS = frozenset({
    "search_volume", "competition_index", "cpc_low_micros", "cpc_high_micros",
})
MONEY_METRICS = frozenset({
    "cost_micros", "cpc_low_micros", "cpc_high_micros",
})
REPORT_DIMENSIONS = frozenset({
    "date", "campaign_id", "ad_group_id", "criterion_id",
    "device", "network", "country",
})
METRIC_UNITS = {
    "clicks": "count", "impressions": "count", "conversions": "count",
    "cost_micros": "money_micros",
    "conversion_value": "money_major",
    "search_volume": "searches_per_month",
    "competition_index": "index_0_100",
    "cpc_low_micros": "money_micros", "cpc_high_micros": "money_micros",
}
MAX_ROWS = 10000


def _strings(values, name, *, maximum=100):
    if isinstance(values, str):
        raise DomainError("invalid_argument", f"{name} must be a sequence")
    try:
        result = tuple(values)
    except TypeError:
        raise DomainError("invalid_argument", f"{name} must be a sequence") from None
    if not result or len(result) > maximum:
        raise DomainError("invalid_argument", f"{name} is empty or too large")
    for item in result:
        text(item, name, maximum=256)
    if len(set(result)) != len(result):
        raise DomainError("invalid_argument", f"{name} must be unique")
    return result


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DomainError("invalid_argument", "Ads date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DomainError("invalid_argument", "Invalid Ads calendar date") from None


def _denied(message):
    return DomainError("use_not_permitted", message, 403)


@dataclass(frozen=True)
class AdsBinding:
    tenant_id: str
    business_subject_ref: str
    cloud_project_ref: str
    oauth_subject_ref: str
    customer_id: str
    access_level: str
    source_api_version: str

    def __post_init__(self):
        for field in fields(self):
            text(getattr(self, field.name), field.name, maximum=256)
        if not re.fullmatch(r"\d{10}", self.customer_id):
            raise DomainError("invalid_argument", "Ads customer_id must be ten digits")
        if self.access_level not in {"Basic", "Standard", "Explorer", "Test", "unknown"}:
            raise DomainError("invalid_argument", "Unknown Ads project access level")

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class AdsAccessPolicy:
    access_id: str
    source_adapter: str
    source_version: str
    binding: AdsBinding
    caller_subject_refs: tuple[str, ...]
    modes: tuple[str, ...]
    approved_plans: tuple[str, ...]
    approved_templates: tuple[str, ...]
    approved_scopes: tuple[dict, ...]
    permissible_use_ref: str
    combined_serp_approval_ref: str
    expires_at: str
    approved: bool = False
    production_approved: bool = False
    revoked: bool = False

    def __post_init__(self):
        for name in (
                "access_id", "source_adapter", "source_version",
                "permissible_use_ref", "combined_serp_approval_ref"):
            text(getattr(self, name), name, maximum=256)
        if not isinstance(self.binding, AdsBinding):
            raise DomainError("invalid_argument", "Ads policy requires a typed binding")
        object.__setattr__(self, "caller_subject_refs", _strings(
            self.caller_subject_refs, "caller_subject_refs"))
        modes = _strings(self.modes, "modes", maximum=2)
        if set(modes) - ADS_MODES:
            raise DomainError("invalid_argument", "Ads proxy mode is not supported")
        object.__setattr__(self, "modes", modes)
        for name in ("approved_plans", "approved_templates"):
            values = getattr(self, name)
            if values:
                object.__setattr__(self, name, _strings(values, name))
            else:
                object.__setattr__(self, name, ())
        scopes = tuple(self.approved_scopes)
        if not scopes or len(scopes) > 100:
            raise DomainError("invalid_argument", "Ads policy requires approved output scopes")
        checked = []
        for item in scopes:
            if not isinstance(item, Mapping):
                raise DomainError("invalid_argument", "Ads policy scope must be an object")
            try:
                scope = AdsScope(**plain_json(item))
            except TypeError:
                raise DomainError("invalid_argument", "Invalid Ads policy scope") from None
            if scope.access_id != self.access_id or scope.customer_id != self.binding.customer_id:
                raise DomainError("invalid_argument", "Ads policy/output scope binding mismatch")
            checked.append(frozen_json(scope.to_dict()))
        object.__setattr__(self, "approved_scopes", tuple(checked))
        for name in ("approved", "production_approved", "revoked"):
            if type(getattr(self, name)) is not bool:
                raise DomainError("invalid_argument", f"{name} must be boolean")
        object.__setattr__(self, "expires_at", utc_text(self.expires_at))

    def to_dict(self):
        return {
            field.name: (
                self.binding.to_dict() if field.name == "binding"
                else plain_json(getattr(self, field.name))
            )
            for field in fields(self)
        }

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, Mapping):
            raise _denied("Ads access policy has not been approved")
        try:
            value = plain_json(value)
            value["binding"] = AdsBinding(**value["binding"])
            return cls(**value)
        except (TypeError, KeyError, DomainError):
            raise _denied("Ads access policy is incomplete or invalid") from None

    def check(self, principal, request, actual_binding, *, now):
        if (
                not self.approved or not self.production_approved or self.revoked
                or parse_time(self.expires_at) <= parse_time(now)
                or not isinstance(actual_binding, AdsBinding)
                or self.binding != actual_binding
                or principal.tenant_id != self.binding.tenant_id
                or principal.subject_ref not in self.caller_subject_refs
                or request["customer_id"] != self.binding.customer_id
                or request["mode"] not in self.modes
                or actual_binding.access_level not in {"Basic", "Standard"}
        ):
            raise _denied("Ads caller/project/OAuth/customer/use admission does not match")
        approved = (
            self.approved_plans if request["kind"] == "keyword_plan"
            else self.approved_templates
        )
        if request["template_version"] not in approved:
            raise _denied("Ads plan or report template is not approved")
        scope = {key: value for key, value in request.items() if key != "mode"}
        if scope not in self.approved_scopes:
            raise _denied("Ads output window/geo/network/currency/attribution is not approved")


@dataclass(frozen=True)
class AdsScope:
    kind: str
    access_id: str
    template_version: str
    customer_id: str
    period_start: str
    period_end: str
    source_timezone: str
    network: str
    geo: str
    language: str
    currency: str
    attribution_version: str

    def __post_init__(self):
        if self.kind not in {"keyword_plan", "report"}:
            raise DomainError("invalid_argument", "Unknown Ads output kind")
        for field in fields(self):
            text(getattr(self, field.name), field.name, maximum=256)
        if not re.fullmatch(r"\d{10}", self.customer_id):
            raise DomainError("invalid_argument", "Ads customer_id must be ten digits")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise DomainError("invalid_argument", "Ads currency must be an ISO code")
        if _day(self.period_start) >= _day(self.period_end):
            raise DomainError("invalid_argument", "Ads period must be left-closed/right-open")
        try:
            ZoneInfo(self.source_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError("invalid_argument", "Ads source timezone must be IANA") from None

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class AdsEvidence:
    adapter_id: str
    adapter_version: str
    source_api_version: str
    contract_ref: str
    dataset_id: str
    binding: AdsBinding
    source_observed_at: str
    evidence_expires_at: str
    revision: str
    provider_request_id: str | None = None

    def __post_init__(self):
        for name in (
                "adapter_id", "adapter_version", "source_api_version",
                "contract_ref", "dataset_id", "revision"):
            text(getattr(self, name), name)
        if not isinstance(self.binding, AdsBinding):
            raise DomainError("invalid_argument", "Ads evidence requires a typed binding")
        if self.binding.source_api_version != self.source_api_version:
            raise DomainError("incompatible_scope", "Ads binding/API version mismatch", 409)
        if parse_time(self.source_observed_at) >= parse_time(self.evidence_expires_at):
            raise DomainError("invalid_argument", "Ads evidence expiry must follow observation")
        for name in ("source_observed_at", "evidence_expires_at"):
            object.__setattr__(self, name, utc_text(getattr(self, name)))
        if self.provider_request_id is not None:
            text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {
            field.name: (
                self.binding.to_dict() if field.name == "binding"
                else getattr(self, field.name)
            )
            for field in fields(self)
        }


@dataclass(frozen=True)
class AdsRow:
    row_id: str
    dimensions: dict
    metrics: tuple[MetricValue, ...]
    row_role: str = "snapshot"
    requested_queries: tuple[str, ...] = ()
    returned_text: str | None = None
    close_variant_group: str | None = None

    def __post_init__(self):
        text(self.row_id, "row_id")
        if not isinstance(self.dimensions, Mapping) or not self.dimensions:
            raise DomainError("invalid_argument", "Ads dimensions are required")
        for name, value in self.dimensions.items():
            text(name, "dimension name", maximum=256)
            if value is not None:
                text(value, name)
        object.__setattr__(self, "dimensions", frozen_json(self.dimensions))
        if self.row_role not in {"snapshot", "revision_delta"}:
            raise DomainError("invalid_argument", "Unknown Ads row role")
        metrics = tuple(self.metrics)
        if not metrics or any(not isinstance(item, MetricValue) for item in metrics):
            raise DomainError("invalid_argument", "Ads metrics must be typed")
        ids = [item.metric_scope.get("metric") for item in metrics]
        for metric_id in ids:
            text(metric_id, "metric ID", maximum=256)
        if len(set(ids)) != len(ids):
            raise DomainError("invalid_argument", "Duplicate Ads metric ID")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "requested_queries", (
            _strings(self.requested_queries, "requested_queries")
            if self.requested_queries else ()
        ))
        for name in ("returned_text", "close_variant_group"):
            if getattr(self, name) is not None:
                text(getattr(self, name), name)

    def to_dict(self):
        return {
            "row_id": self.row_id,
            "dimensions": plain_json(self.dimensions),
            "metrics": {
                item.metric_scope["metric"]: item.to_dict() for item in self.metrics
            },
            "row_role": self.row_role,
            "requested_queries": list(self.requested_queries),
            "returned_text": self.returned_text,
            "close_variant_group": self.close_variant_group,
        }


@dataclass(frozen=True)
class AdsResult:
    evidence: AdsEvidence
    scope: AdsScope
    rows: tuple[AdsRow, ...]
    response_complete: bool
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, AdsEvidence) or not isinstance(self.scope, AdsScope):
            raise DomainError("invalid_argument", "Ads result must have typed evidence/scope")
        if self.scope.customer_id != self.evidence.binding.customer_id:
            raise DomainError("incompatible_scope", "Ads evidence/customer mismatch", 409)
        rows = tuple(self.rows)
        if len(rows) > MAX_ROWS or any(not isinstance(row, AdsRow) for row in rows):
            raise DomainError("invalid_argument", "Ads result exceeds the bounded row contract")
        if len({row.row_id for row in rows}) != len(rows):
            raise DomainError("incompatible_scope", "Duplicate Ads row ID", 409)
        plan = self.scope.kind == "keyword_plan"
        groups, queries, report_grains = set(), set(), set()
        for row in rows:
            if set(row.dimensions) - ({"month"} if plan else REPORT_DIMENSIONS):
                raise DomainError("use_not_permitted", "Unapproved Ads output dimension", 403)
            if plan:
                if (
                        not row.requested_queries or not row.returned_text
                        or not row.close_variant_group or row.row_role != "snapshot"):
                    raise DomainError("invalid_argument", "Planning requires an explicit variant group")
                month = row.dimensions.get("month")
                if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
                    raise DomainError("invalid_argument", "Planning row requires a source month")
                day = _day(month + "-01")
                if not _day(self.scope.period_start) <= day < _day(self.scope.period_end):
                    raise DomainError("incompatible_scope", "Planning month escapes the window", 409)
                group = (row.close_variant_group, month)
                if group in groups or any((query, month) in queries for query in row.requested_queries):
                    raise DomainError("incompatible_scope", "Duplicate close-variant contribution", 409)
                groups.add(group)
                queries.update((query, month) for query in row.requested_queries)
            else:
                day = _day(row.dimensions.get("date"))
                if not _day(self.scope.period_start) <= day < _day(self.scope.period_end):
                    raise DomainError("incompatible_scope", "Ads row date escapes the window", 409)
                if row.requested_queries or row.returned_text or row.close_variant_group:
                    raise DomainError("invalid_argument", "Report rows cannot claim planning groups")
                grain = (canonical_json(row.dimensions), row.row_role)
                if grain in report_grains:
                    raise DomainError("incompatible_scope", "Duplicate Ads report grain", 409)
                report_grains.add(grain)
            for item in row.metrics:
                metric_id = item.metric_scope["metric"]
                if metric_id not in (PLAN_METRICS if plan else REPORT_METRICS):
                    raise DomainError("invalid_argument", "Ads metric not allowed for this output")
                expected = {
                    **self.scope.to_dict(),
                    "row_id": row.row_id,
                    "row_role": row.row_role,
                    "dimensions": plain_json(row.dimensions),
                    "revision": self.evidence.revision,
                }
                if (
                        set(item.metric_scope) != set(expected) | {"metric"}
                        or any(item.metric_scope.get(key) != value for key, value in expected.items())):
                    raise DomainError("incompatible_scope", "Ads metric scope mismatch", 409)
                if item.evidence_expires_at is not None and (
                        parse_time(item.evidence_expires_at)
                        > parse_time(self.evidence.evidence_expires_at)):
                    raise DomainError("incompatible_scope", "Ads metric exceeds evidence expiry", 409)
                if item.unit != METRIC_UNITS[metric_id]:
                    raise DomainError("invalid_argument", "Ads metric unit does not match its definition")
                if plan and item.additivity != "non_additive":
                    raise DomainError("invalid_argument", "Close-variant planning metrics are non-additive")
                if item.value is not None:
                    value = decimal(item.value)
                    if row.row_role == "snapshot" and value < 0:
                        raise DomainError("invalid_argument", "Negative Ads values require a revision delta")
                    if metric_id in MONEY_METRICS | {"clicks", "impressions"} and value != value.to_integral_value():
                        raise DomainError("invalid_argument", "Ads counts/micros must remain integral")
                    if metric_id == "competition_index" and not 0 <= value <= 100:
                        raise DomainError("invalid_argument", "Competition index must be 0..100")
        if type(self.response_complete) is not bool:
            raise DomainError("invalid_argument", "Ads response_complete must be boolean")
        limitations = tuple(self.limitations)
        for value in limitations:
            text(value, "limitation")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "limitations", limitations)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "evidence": self.evidence.to_dict(),
            "scope": self.scope.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "response_complete": self.response_complete,
            "limitations": list(self.limitations),
        }


class AdsService:
    def __init__(self, registry, adapter, *, cursor_codec, clock):
        self.registry = registry
        self.adapter = adapter
        self.cursor_codec = cursor_codec
        self.clock = clock

    @staticmethod
    def route(path):
        if path not in ADS_ROUTES:
            raise DomainError("not_found", "No managed Ads route matches", 404)
        return "keyword_plan" if path == "/ads/keyword-plans" else "report"

    def _request(self, path, parameters, *, now):
        kind = self.route(path)
        scope_fields = {field.name for field in fields(AdsScope)} - {"kind"}
        allowed = scope_fields | {"source", "source_version", "mode", "limit", "cursor"}
        if set(parameters) - allowed:
            raise DomainError("invalid_argument", "Unknown Ads parameter; raw source proxying is disabled")
        for name in scope_fields | {"source", "source_version", "mode"}:
            text(parameters.get(name), name, maximum=256)
        if parameters["mode"] not in ADS_MODES:
            raise _denied("Ads is available only for approved internal analysis/report service")
        scope = AdsScope(kind=kind, **{
            name: parameters[name] for name in scope_fields
        })
        if kind == "keyword_plan":
            start, end = _day(scope.period_start), _day(scope.period_end)
            current = parse_time(now).astimezone(ZoneInfo(scope.source_timezone)).date()
            earliest = current.year * 12 + current.month - 1 - 48
            if (
                    start.day != 1 or end.day != 1
                    or start.year * 12 + start.month - 1 < earliest
                    or end.year * 12 + end.month - 1 > current.year * 12 + current.month
                    or (end.year - start.year) * 12 + end.month - start.month > 48
            ):
                raise DomainError("invalid_argument", "Planning requires a month-aligned four-year window")
        return {"scope": scope.to_dict(), "mode": parameters["mode"]}

    def _admit(self, principal, parameters, request, *, now):
        source = self.registry.source(
            principal.tenant_id, parameters["source"], parameters["source_version"])
        field = "ads_keyword_plan" if request["scope"]["kind"] == "keyword_plan" else "ads_report"
        grants = [
            self.registry.grant(principal.tenant_id, grant_id)
            for grant_id in sorted(principal.grant_ids)
        ]
        grants = [
            grant for grant in grants
            if grant.source_adapter == source.adapter_id
            and grant.source_version == source.adapter_version
            and grant.asset_id == "customers/" + request["scope"]["customer_id"]
            and field in grant.fields
        ]
        if "reader" not in principal.roles or not grants:
            raise DomainError("not_authorized", "No Ads asset grant covers this output", 403)
        grant = grants[0]
        right = authorize(
            principal, grant, source, field, "internal_display",
            now=now, production=True)
        policy = self.registry.ads_access(
            principal.tenant_id, request["scope"]["access_id"])
        if (
                policy.source_adapter != source.adapter_id
                or policy.source_version != source.adapter_version):
            raise _denied("Ads access policy does not match this source")
        binding = self.adapter.binding(principal.tenant_id, policy.access_id)
        policy.check(
            principal, {**request["scope"], "mode": request["mode"]}, binding, now=now)
        if binding.source_api_version != source.source_api_version:
            raise _denied("Ads approved project/API version does not match source")
        deadlines = [grant.expires_at, parse_time(policy.expires_at)]
        if right.retention_deadline is not None:
            deadlines.append(right.retention_deadline)
        return source, grant, policy, min(deadlines)

    def read(self, principal, path, parameters, *, now):
        request = self._request(path, parameters, now=now)
        limit = page_limit(parameters.get("limit"))
        self.registry.assert_current(principal, now=now)
        source, grant, policy, deadline = self._admit(
            principal, parameters, request, now=now)
        # Only approved stored plan/template outputs are read, never caller GAQL.
        result = self.adapter.read_managed(principal.tenant_id, request)
        if not isinstance(result, AdsResult):
            raise DomainError("source_error", "Ads adapter returned an untyped output", 503)
        evidence = result.evidence
        if (
                result.scope.to_dict() != request["scope"]
                or evidence.binding != policy.binding
                or evidence.adapter_id != source.adapter_id
                or evidence.adapter_version != source.adapter_version
                or evidence.source_api_version != source.source_api_version
                or evidence.contract_ref != getattr(self.adapter, "contract_ref", None)
                or evidence.dataset_id != getattr(self.adapter, "dataset_id", None)
        ):
            raise DomainError("incompatible_scope", "Managed Ads output scope/evidence mismatch", 409)
        current = parse_time(self.clock())
        expiry = parse_time(evidence.evidence_expires_at)
        if parse_time(evidence.source_observed_at) > current:
            raise DomainError("incompatible_scope", "Ads observation is in the future", 409)
        if expiry <= current:
            raise DomainError("expired", "Ads evidence has expired", 410)
        if expiry > deadline:
            raise _denied("Ads evidence exceeds policy/grant/field retention")
        self.registry.assert_current(principal, now=current)
        _, _, current_policy, current_deadline = self._admit(
            principal, parameters, request, now=current)
        if current_policy != policy:
            raise _denied("Ads policy changed while reading")
        current = parse_time(self.clock())
        self.registry.assert_current(principal, now=current)
        if expiry <= current or current_deadline <= current:
            raise DomainError("expired", "Ads access/evidence expired while reading", 410)
        evidence_id = identity("managed-ads-evidence", result.to_dict())
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {**request, "limit": limit, "policy": policy.to_dict()},
            "sort": ["row_id"],
            "source": f"ads:{source.adapter_id}:{source.adapter_version}",
            "manifest_id": evidence_id,
        }
        if self.cursor_codec is None:
            raise DomainError("not_covered", "Ads cursor signing is not initialized", 503)
        after = None
        if "cursor" in parameters:
            position = self.cursor_codec.decode(parameters["cursor"], binding=binding, now=current)
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError("invalid_argument", "Invalid Ads cursor position")
            after = position[0]
        rows = sorted(
            (row for row in result.rows if after is None or row.row_id > after),
            key=lambda row: row.row_id)
        page, more = rows[:limit], len(rows) > limit
        cursor = self.cursor_codec.encode(
            binding=binding, position=[page[-1].row_id],
            expires_at=expiry, now=current,
        ) if more else None
        return {
            "ads_version": ADS_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "license_id": source.license_id,
            "license_version": source.license_version,
            "contract_ref": evidence.contract_ref,
            "dataset_id": evidence.dataset_id,
            "evidence_id": evidence_id,
            "source_observed_at": evidence.source_observed_at,
            "data_as_of": evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "scope": result.scope.to_dict(),
            "revision": evidence.revision,
            "rows": [row.to_dict() for row in page],
            "next_cursor": cursor,
            "page_row_count": len(page),
            "total_returned_rows": len(result.rows),
            "response_complete": result.response_complete,
            "mode": request["mode"],
            "access_id": policy.access_id,
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
            "coverage": {
                "status": "managed_output" if result.response_complete else "partial_managed_output",
                "scope": "approved_ads_plan" if result.scope.kind == "keyword_plan" else "approved_ads_report",
            },
            "limitations": list(result.limitations),
            "not_claimed": [
                "arbitrary_google_ads_proxy", "competition_is_seo_difficulty",
                "close_variant_volume_is_per_query", "roas_is_profit_or_causal_lift",
                "competitor_actual_spend", "real_project_admission_without_poc",
            ],
        }
