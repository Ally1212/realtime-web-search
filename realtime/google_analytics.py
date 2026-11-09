"""Typed GA4 report and reconciliation contracts.

The adapter is injected only after explicit source and production approval.
This module never calls Google, fabricates missing metadata, or forces an API
and BigQuery difference to zero.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_domain import (
    DomainError,
    MetricValue,
    canonical_json,
    decimal,
    frozen_json,
    identity,
    parse_time,
    plain_json,
    text,
    utc_text,
)
from .google_evidence import page_limit
from .google_rights import authorize

GA4_ANALYTICS_VERSION = "google-ga4-analytics-1"
AUTHORIZED_ANALYTICS_ROUTES = frozenset({
    "/analytics/reports", "/analytics/reconciliations",
})
QUOTA_STATES = frozenset({
    "source_reported", "not_reported_by_source", "unknown",
})
RECONCILIATION_STATUSES = frozenset({
    "exact_match", "tolerance_match", "difference_explained",
    "difference_unexplained", "not_comparable",
})
QUOTA_BUCKETS = frozenset({
    "tokensPerDay",
    "tokensPerHour",
    "tokensPerProjectPerPropertyPerHour",
    "concurrentRequests",
    "serverErrorsPerProjectPerHour",
    "potentiallyThresholdedRequestsPerHour",
})
MAX_REPORT_ROWS = 10000


def _text_tuple(value, name, *, maximum=100):
    values = tuple(value)
    if (
            not values
            or len(values) > maximum
            or any(not isinstance(item, str) or not item.strip() for item in values)
    ):
        raise DomainError("invalid_argument", f"{name} is invalid")
    for item in values:
        text(item, name, maximum=256)
    if len(set(values)) != len(values):
        raise DomainError("invalid_argument", f"{name} must be unique")
    return values


def _optional_bool(value, name):
    if value is not None and type(value) is not bool:
        raise DomainError(
            "invalid_argument", f"{name} must be boolean or null")
    return value


def _date(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError(
            "invalid_argument", f"{name} must use ISO YYYY-MM-DD") from None


@dataclass(frozen=True)
class AnalyticsEvidence:
    adapter_id: str
    adapter_version: str
    source_api_version: str
    contract_ref: str
    dataset_id: str
    source_observed_at: str
    evidence_expires_at: str
    provider_request_id: str | None = None

    def __post_init__(self):
        for name in (
                "adapter_id", "adapter_version", "source_api_version",
                "contract_ref", "dataset_id"):
            text(getattr(self, name), name)
        observed = parse_time(self.source_observed_at)
        expires = parse_time(self.evidence_expires_at)
        if expires <= observed:
            raise DomainError(
                "invalid_argument",
                "GA4 evidence expiry must follow observation",
            )
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "evidence_expires_at", utc_text(expires))
        if self.provider_request_id is not None:
            text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {
            field.name: plain_json(getattr(self, field.name))
            for field in fields(self)
        }


@dataclass(frozen=True)
class AnalyticsSampling:
    sample_size: int
    sampling_space: int
    sampling_rate: Decimal
    reason: str | None = None

    def __post_init__(self):
        if (
                type(self.sample_size) is not int
                or type(self.sampling_space) is not int
                or self.sample_size < 0
                or self.sampling_space < 0
                or self.sample_size > self.sampling_space
        ):
            raise DomainError(
                "invalid_argument", "GA4 sampling size/space is invalid")
        rate = decimal(self.sampling_rate)
        if not Decimal(0) <= rate <= Decimal(1):
            raise DomainError(
                "invalid_argument", "GA4 sampling rate must be between 0 and 1")
        if self.sampling_space and rate != (
                Decimal(self.sample_size) / Decimal(self.sampling_space)
        ):
            raise DomainError(
                "incompatible_scope",
                "GA4 sampling rate disagrees with size/space",
                409,
            )
        if self.reason is not None:
            text(self.reason, "sampling reason")
        object.__setattr__(self, "sampling_rate", rate)

    def to_dict(self):
        return {
            "sample_size": self.sample_size,
            "sampling_space": self.sampling_space,
            "sampling_rate": str(self.sampling_rate),
            "reason": self.reason,
        }


def _quota(value):
    if not isinstance(value, dict) or not value:
        raise DomainError(
            "invalid_argument", "GA4 property quota metadata is required")
    state = value.get("state")
    if state not in QUOTA_STATES:
        raise DomainError(
            "invalid_argument", "Unknown GA4 property quota state")
    unknown = set(value) - {"state"} - QUOTA_BUCKETS
    if unknown:
        raise DomainError(
            "invalid_argument", "Unknown GA4 property quota field")
    for name, bucket in value.items():
        if name == "state":
            continue
        if not isinstance(bucket, dict) or set(bucket) - {
                "consumed", "remaining"}:
            raise DomainError(
                "invalid_argument", "GA4 quota buckets must be objects")
        if not bucket:
            raise DomainError(
                "invalid_argument",
                "GA4 quota buckets must report consumed or remaining",
            )
        for field_name, amount in bucket.items():
            if amount is not None and (
                    type(amount) is not int or amount < 0):
                raise DomainError(
                    "invalid_argument",
                    "GA4 quota consumed/remaining must be nonnegative",
                )
    if state == "source_reported" and len(value) == 1:
        raise DomainError(
            "invalid_argument",
            "Source-reported GA4 quota requires at least one bucket",
        )
    canonical_json(value)
    return frozen_json(value)


@dataclass(frozen=True)
class AnalyticsRow:
    row_id: str
    dimensions: dict
    metrics: tuple[MetricValue, ...]
    sampling_metadata: tuple[AnalyticsSampling, ...] = ()
    subject_to_thresholding: bool | None = None

    def __post_init__(self):
        text(self.row_id, "row_id")
        if not isinstance(self.dimensions, dict) or not self.dimensions:
            raise DomainError(
                "invalid_argument", "GA4 row dimensions are required")
        for name, value in self.dimensions.items():
            text(name, "dimension name", maximum=256)
            if value is not None:
                text(value, name, maximum=4096)
        metrics = tuple(self.metrics)
        if (
                not metrics
                or len(metrics) > 100
                or any(not isinstance(metric, MetricValue) for metric in metrics)
        ):
            raise DomainError(
                "invalid_argument", "GA4 row metrics are invalid")
        metric_ids = [metric.metric_scope.get("metric") for metric in metrics]
        if any(not isinstance(item, str) or not item for item in metric_ids):
            raise DomainError(
                "invalid_argument", "GA4 metric IDs are required")
        if len(set(metric_ids)) != len(metric_ids):
            raise DomainError(
                "invalid_argument", "GA4 row metrics must be unique")
        sampling = tuple(self.sampling_metadata)
        if any(not isinstance(item, AnalyticsSampling) for item in sampling):
            raise DomainError(
                "invalid_argument", "Invalid GA4 sampling metadata")
        _optional_bool(self.subject_to_thresholding, "subject_to_thresholding")
        object.__setattr__(self, "dimensions", frozen_json(self.dimensions))
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "sampling_metadata", sampling)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "row_id": self.row_id,
            "dimensions": plain_json(self.dimensions),
            "metrics": {
                metric.metric_scope["metric"]: metric.to_dict()
                for metric in self.metrics
            },
            "sampling_metadata": [
                item.to_dict() for item in self.sampling_metadata],
            "subject_to_thresholding": self.subject_to_thresholding,
        }


@dataclass(frozen=True)
class AnalyticsReport:
    evidence: AnalyticsEvidence
    asset_id: str
    property_id: str
    template_version: str
    period_start: str
    period_end: str
    report_timezone: str
    dimensions: tuple[str, ...]
    metrics_requested: tuple[str, ...]
    rows: tuple[AnalyticsRow, ...]
    row_count: int
    source_response_complete: bool
    sampling_metadata: tuple[AnalyticsSampling, ...] = ()
    subject_to_thresholding: bool | None = None
    data_loss_from_other_row: bool | None = None
    schema_restrictions: tuple[str, ...] = ()
    property_quota: dict | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, AnalyticsEvidence):
            raise DomainError(
                "invalid_argument", "GA4 report requires typed evidence")
        for name in ("asset_id", "property_id", "template_version"):
            text(getattr(self, name), name)
        if self.asset_id != self.property_id:
            raise DomainError(
                "incompatible_scope",
                "GA4 property must match the grant asset",
                409,
            )
        start = _date(self.period_start, "period_start")
        end = _date(self.period_end, "period_end")
        if start >= end:
            raise DomainError(
                "invalid_argument",
                "GA4 period must be left-closed and right-open",
            )
        object.__setattr__(self, "period_start", start.isoformat())
        object.__setattr__(self, "period_end", end.isoformat())
        try:
            ZoneInfo(self.report_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "report_timezone must be IANA") from None
        dimensions = _text_tuple(self.dimensions, "dimensions", maximum=16)
        metric_ids = _text_tuple(
            self.metrics_requested, "metrics", maximum=100)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "metrics_requested", metric_ids)
        rows = tuple(self.rows)
        if (
                len(rows) > MAX_REPORT_ROWS
                or any(not isinstance(row, AnalyticsRow) for row in rows)
        ):
            raise DomainError(
                "invalid_argument", "GA4 rows exceed the bounded result")
        if type(self.row_count) is not int or self.row_count < len(rows):
            raise DomainError(
                "invalid_argument",
                "GA4 row_count must cover all returned rows",
            )
        if type(self.source_response_complete) is not bool:
            raise DomainError(
                "invalid_argument",
                "source_response_complete must be boolean",
            )
        if len({row.row_id for row in rows}) != len(rows):
            raise DomainError(
                "incompatible_scope",
                "GA4 row IDs must be unique",
                409,
            )
        for row in rows:
            if set(row.dimensions) != set(dimensions):
                raise DomainError(
                    "incompatible_scope",
                    "GA4 row dimensions disagree with the report",
                    409,
                )
            for metric in row.metrics:
                metric_id = metric.metric_scope["metric"]
                if metric_id not in metric_ids:
                    raise DomainError(
                        "incompatible_scope",
                        "GA4 row metric was not requested",
                        409,
                    )
                expected = {
                    "asset_id": self.asset_id,
                    "property_id": self.property_id,
                    "template_version": self.template_version,
                    "period_start": self.period_start,
                    "period_end": self.period_end,
                    "report_timezone": self.report_timezone,
                    "row_id": row.row_id,
                    "dimensions": plain_json(row.dimensions),
                }
                if any(
                        metric.metric_scope.get(name) != value
                        for name, value in expected.items()
                ):
                    raise DomainError(
                        "incompatible_scope",
                        "GA4 metric scope disagrees with its report row",
                        409,
                    )
        object.__setattr__(self, "rows", rows)
        sampling = tuple(self.sampling_metadata)
        if any(not isinstance(item, AnalyticsSampling) for item in sampling):
            raise DomainError(
                "invalid_argument", "Invalid GA4 sampling metadata")
        object.__setattr__(self, "sampling_metadata", sampling)
        _optional_bool(self.subject_to_thresholding, "subject_to_thresholding")
        _optional_bool(
            self.data_loss_from_other_row, "data_loss_from_other_row")
        restrictions = tuple(self.schema_restrictions)
        if any(
                not isinstance(item, str) or not item
                for item in restrictions
        ):
            raise DomainError(
                "invalid_argument", "Invalid GA4 schema restrictions")
        object.__setattr__(self, "schema_restrictions", restrictions)
        if self.property_quota is None:
            raise DomainError(
                "invalid_argument",
                "GA4 property quota must be source-reported or explicitly unknown",
            )
        object.__setattr__(
            self, "property_quota", _quota(self.property_quota))
        limitations = tuple(self.limitations)
        if any(not isinstance(item, str) or not item for item in limitations):
            raise DomainError(
                "invalid_argument", "Invalid GA4 limitations")
        object.__setattr__(self, "limitations", limitations)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "asset_id": self.asset_id,
            "property_id": self.property_id,
            "template_version": self.template_version,
            "period": {
                "start": self.period_start,
                "end": self.period_end,
                "report_timezone": self.report_timezone,
            },
            "dimensions": list(self.dimensions),
            "metrics": list(self.metrics_requested),
            "rows": [row.to_dict() for row in self.rows],
            "row_count": self.row_count,
            "source_response_complete": self.source_response_complete,
            "sampling_metadata": [
                item.to_dict() for item in self.sampling_metadata],
            "subject_to_thresholding": self.subject_to_thresholding,
            "data_loss_from_other_row": self.data_loss_from_other_row,
            "schema_restrictions": list(self.schema_restrictions),
            "property_quota": plain_json(self.property_quota),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ReconciliationMetric:
    metric_id: str
    api_value: Decimal | None
    bigquery_value: Decimal | None
    delta: Decimal | None
    status: str
    reasons: tuple[str, ...] = ()
    tolerance: Decimal | None = None

    def __post_init__(self):
        text(self.metric_id, "metric_id")
        api_value = None if self.api_value is None else decimal(self.api_value)
        bigquery_value = (
            None if self.bigquery_value is None
            else decimal(self.bigquery_value)
        )
        expected_delta = (
            None if api_value is None or bigquery_value is None
            else api_value - bigquery_value
        )
        if self.delta is None:
            if expected_delta is not None:
                raise DomainError(
                    "invalid_argument",
                    "Comparable GA4 values require their actual delta",
                )
        elif expected_delta is None or decimal(self.delta) != expected_delta:
            raise DomainError(
                "incompatible_scope",
                "GA4 reconciliation delta does not match source values",
                409,
            )
        if self.status not in RECONCILIATION_STATUSES:
            raise DomainError(
                "invalid_argument", "Unknown GA4 reconciliation status")
        tolerance = (
            None if self.tolerance is None else decimal(self.tolerance))
        if tolerance is not None and tolerance < 0:
            raise DomainError(
                "invalid_argument", "GA4 reconciliation tolerance is negative")
        reasons = tuple(self.reasons)
        if any(not isinstance(item, str) or not item for item in reasons):
            raise DomainError(
                "invalid_argument", "Invalid GA4 reconciliation reasons")
        if self.status == "not_comparable":
            if expected_delta is not None or not reasons:
                raise DomainError(
                    "invalid_argument",
                    "Non-comparable GA4 metrics require null delta and reasons",
                )
        elif expected_delta is None:
            raise DomainError(
                "invalid_argument",
                "Comparable GA4 status requires both source values",
            )
        elif self.status == "exact_match" and expected_delta != 0:
            raise DomainError(
                "invalid_argument", "exact_match requires a zero delta")
        elif self.status == "tolerance_match" and (
                tolerance is None or abs(expected_delta) > tolerance):
            raise DomainError(
                "invalid_argument",
                "tolerance_match requires a delta within tolerance",
            )
        elif self.status in {
                "difference_explained", "difference_unexplained"} and (
                    expected_delta == 0):
            raise DomainError(
                "invalid_argument",
                "A GA4 difference status requires a nonzero delta",
            )
        if self.status == "difference_explained" and not reasons:
            raise DomainError(
                "invalid_argument",
                "Explained GA4 differences require decomposition reasons",
            )
        object.__setattr__(self, "api_value", api_value)
        object.__setattr__(self, "bigquery_value", bigquery_value)
        object.__setattr__(self, "delta", expected_delta)
        object.__setattr__(self, "tolerance", tolerance)
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self):
        return {
            "metric_id": self.metric_id,
            "api_value": (
                str(self.api_value) if self.api_value is not None else None),
            "bigquery_value": (
                str(self.bigquery_value)
                if self.bigquery_value is not None else None),
            "delta": str(self.delta) if self.delta is not None else None,
            "status": self.status,
            "reasons": list(self.reasons),
            "tolerance": (
                str(self.tolerance) if self.tolerance is not None else None),
        }


@dataclass(frozen=True)
class AnalyticsReconciliation:
    evidence: AnalyticsEvidence
    asset_id: str
    property_id: str
    template_version: str
    period_start: str
    period_end: str
    report_timezone: str
    dimensions: tuple[str, ...]
    api_scope: dict
    bigquery_scope: dict
    compatible: bool
    comparisons: tuple[ReconciliationMetric, ...]
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, AnalyticsEvidence):
            raise DomainError(
                "invalid_argument",
                "GA4 reconciliation requires typed evidence",
            )
        for name in ("asset_id", "property_id", "template_version"):
            text(getattr(self, name), name)
        if self.asset_id != self.property_id:
            raise DomainError(
                "incompatible_scope",
                "GA4 reconciliation property must match the grant asset",
                409,
            )
        start = _date(self.period_start, "period_start")
        end = _date(self.period_end, "period_end")
        if start >= end:
            raise DomainError(
                "invalid_argument",
                "GA4 reconciliation period must be nonempty",
            )
        object.__setattr__(self, "period_start", start.isoformat())
        object.__setattr__(self, "period_end", end.isoformat())
        try:
            ZoneInfo(self.report_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "report_timezone must be IANA") from None
        object.__setattr__(
            self,
            "dimensions",
            _text_tuple(self.dimensions, "dimensions", maximum=16),
        )
        for name in ("api_scope", "bigquery_scope"):
            value = getattr(self, name)
            if not isinstance(value, dict) or not value:
                raise DomainError(
                    "invalid_argument", f"{name} must be an explicit object")
            canonical_json(value)
            object.__setattr__(self, name, frozen_json(value))
        if type(self.compatible) is not bool:
            raise DomainError(
                "invalid_argument", "compatible must be boolean")
        comparisons = tuple(self.comparisons)
        if (
                not comparisons
                or len(comparisons) > 100
                or any(
                    not isinstance(item, ReconciliationMetric)
                    for item in comparisons
                )
        ):
            raise DomainError(
                "invalid_argument", "GA4 reconciliation metrics are invalid")
        if len({item.metric_id for item in comparisons}) != len(comparisons):
            raise DomainError(
                "invalid_argument",
                "GA4 reconciliation metric IDs must be unique",
            )
        for item in comparisons:
            if not self.compatible and item.status != "not_comparable":
                raise DomainError(
                    "invalid_argument",
                    "Incompatible GA4 scopes cannot produce comparable deltas",
                )
        object.__setattr__(self, "comparisons", comparisons)
        limitations = tuple(self.limitations)
        if any(not isinstance(item, str) or not item for item in limitations):
            raise DomainError(
                "invalid_argument",
                "Invalid GA4 reconciliation limitations",
            )
        object.__setattr__(self, "limitations", limitations)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "asset_id": self.asset_id,
            "property_id": self.property_id,
            "template_version": self.template_version,
            "period": {
                "start": self.period_start,
                "end": self.period_end,
                "report_timezone": self.report_timezone,
            },
            "dimensions": list(self.dimensions),
            "api_scope": plain_json(self.api_scope),
            "bigquery_scope": plain_json(self.bigquery_scope),
            "compatible": self.compatible,
            "comparisons": [item.to_dict() for item in self.comparisons],
            "limitations": list(self.limitations),
        }


def _csv(value, name, *, maximum=100):
    if value is None:
        raise DomainError("invalid_argument", f"{name} is required")
    text(value, name, maximum=16384)
    values = [item.strip() for item in value.split(",") if item.strip()]
    if (
            not values
            or len(values) > maximum
            or len(set(values)) != len(values)
    ):
        raise DomainError(
            "invalid_argument", f"{name} contains invalid values")
    return tuple(values)


def _scope_json(value, name):
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise DomainError(
            "invalid_argument", f"{name} must be a JSON object") from None
    if not isinstance(result, dict) or not result:
        raise DomainError(
            "invalid_argument", f"{name} must be a JSON object")
    canonical_json(result)
    return result


class AnalyticsService:
    def __init__(self, registry, adapter, *, cursor_codec, clock):
        self.registry = registry
        self.adapter = adapter
        self.cursor_codec = cursor_codec
        self.clock = clock

    @staticmethod
    def route(path):
        if path not in AUTHORIZED_ANALYTICS_ROUTES:
            raise DomainError(
                "not_found", "No GA4 analytics route matches", 404)
        return path.rsplit("/", 1)[-1]

    def _source(self, principal, parameters, field, *, now):
        source_id = parameters.get("source")
        source_version = parameters.get("source_version")
        asset_id = parameters.get("asset")
        if not source_id or not source_version or not asset_id:
            raise DomainError(
                "invalid_argument",
                "source, source_version and asset are required",
            )
        source = self.registry.source(
            principal.tenant_id, source_id, source_version)
        matching = []
        for grant_id in principal.grant_ids:
            grant = self.registry.grant(principal.tenant_id, grant_id)
            if (
                    grant.source_adapter == source_id
                    and grant.source_version == source_version
                    and grant.asset_id == asset_id
                    and field in grant.fields
            ):
                matching.append(grant)
        if not matching:
            raise DomainError(
                "not_authorized",
                "No asset grant covers this GA4 source and field",
                403,
            )
        if "reader" not in principal.roles:
            raise DomainError(
                "use_not_permitted",
                "The reader role is required for GA4 analytics",
                403,
            )
        for grant in matching:
            authorize(
                principal,
                grant,
                source,
                field,
                "internal_display",
                now=now,
                production=True,
            )
        grant = matching[0]
        deadlines = [grant.expires_at]
        deadlines.extend(
            right.retention_deadline
            for right in source.field_rights
            if right.field == field and right.retention_deadline is not None
        )
        return source, grant, min(deadlines)

    def _evidence(self, evidence, *, source, field_deadline):
        if not isinstance(evidence, AnalyticsEvidence):
            raise DomainError(
                "source_error",
                "GA4 adapter returned no typed evidence",
                503,
            )
        if (
                evidence.adapter_id != source.adapter_id
                or evidence.adapter_version != source.adapter_version
                or evidence.source_api_version != source.source_api_version
                or evidence.contract_ref != getattr(
                    self.adapter, "contract_ref", None)
                or evidence.dataset_id != getattr(
                    self.adapter, "dataset_id", None)
        ):
            raise DomainError(
                "incompatible_scope",
                "GA4 evidence does not match the approved adapter",
                409,
            )
        now = parse_time(self.clock())
        observed = parse_time(evidence.source_observed_at)
        expiry = parse_time(evidence.evidence_expires_at)
        if observed > now:
            raise DomainError(
                "incompatible_scope",
                "GA4 source observation is in the future",
                409,
            )
        if expiry <= now:
            raise DomainError(
                "expired", "GA4 evidence has expired", 410)
        if expiry > field_deadline:
            raise DomainError(
                "use_not_permitted",
                "GA4 evidence exceeds the approved retention deadline",
                403,
            )
        return expiry

    def _period_request(self, parameters, allowed):
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown GA4 analytics parameter")
        for name in (
                "source", "source_version", "asset", "template",
                "period_start", "period_end", "dimensions", "metrics"):
            if not parameters.get(name):
                raise DomainError(
                    "invalid_argument", f"{name} is required")
        text(parameters["template"], "template", maximum=256)
        start = _date(parameters["period_start"], "period_start")
        end = _date(parameters["period_end"], "period_end")
        if start >= end:
            raise DomainError(
                "invalid_argument",
                "GA4 period must be left-closed and right-open",
            )
        return {
            "asset_id": parameters["asset"],
            "property_id": parameters["asset"],
            "template_version": parameters["template"],
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "dimensions": _csv(
                parameters["dimensions"], "dimensions", maximum=16),
            "metrics": _csv(parameters["metrics"], "metrics", maximum=100),
        }

    def _report_page(
            self, principal, parameters, request, *, now,
            source, expiry, result, evidence_id):
        limit = page_limit(parameters.get("limit"))
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {**request, "limit": limit},
            "sort": ["row_id"],
            "source": (
                f"ga4-report:{source.adapter_id}:{source.adapter_version}"),
            "manifest_id": evidence_id,
        }
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered",
                "GA4 cursor signing is not initialized",
                503,
            )
        codec = self.cursor_codec
        after = None
        if "cursor" in parameters:
            position = codec.decode(
                parameters["cursor"], binding=binding, now=now)
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError(
                    "invalid_argument", "Invalid GA4 report cursor")
            after = position[0]
        ordered = sorted(
            (row for row in result.rows if after is None or row.row_id > after),
            key=lambda row: row.row_id,
        )
        page, more = ordered[:limit], len(ordered) > limit
        cursor = (
            codec.encode(
                binding=binding,
                position=[page[-1].row_id],
                expires_at=expiry,
                now=now,
            )
            if more else None
        )
        return page, cursor

    def read(self, principal, path, parameters, *, now):
        kind = self.route(path)
        if kind == "reports":
            return self._read_report(principal, parameters, now=now)
        return self._read_reconciliation(principal, parameters, now=now)

    def _read_report(self, principal, parameters, *, now):
        allowed = {
            "source", "source_version", "asset", "template",
            "period_start", "period_end", "dimensions", "metrics",
            "limit", "cursor",
        }
        request = self._period_request(parameters, allowed)
        source, grant, deadline = self._source(
            principal, parameters, "ga4_report", now=now)
        result = self.adapter.read_report(
            principal.tenant_id, request)
        if not isinstance(result, AnalyticsReport):
            raise DomainError(
                "source_error",
                "GA4 adapter returned an invalid report",
                503,
            )
        expected = {
            "asset_id": request["asset_id"],
            "property_id": request["property_id"],
            "template_version": request["template_version"],
            "period_start": request["period_start"],
            "period_end": request["period_end"],
            "dimensions": request["dimensions"],
            "metrics_requested": request["metrics"],
        }
        if any(getattr(result, name) != value for name, value in expected.items()):
            raise DomainError(
                "incompatible_scope",
                "GA4 report scope differs from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence, source=source, field_deadline=deadline)
        evidence_id = identity("ga4-report-evidence", {
            "source": source.adapter_id,
            "version": source.adapter_version,
            "source_api_version": result.evidence.source_api_version,
            "dataset_id": result.evidence.dataset_id,
            "request": request,
            "report": result.to_dict(),
        })
        page, cursor = self._report_page(
            principal,
            parameters,
            request,
            now=now,
            source=source,
            expiry=expiry,
            result=result,
            evidence_id=evidence_id,
        )
        return {
            "ga4_analytics_version": GA4_ANALYTICS_VERSION,
            "operation": "report",
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": evidence_id,
            "request": request,
            "period": result.to_dict()["period"],
            "dimensions": list(result.dimensions),
            "metrics": list(result.metrics_requested),
            "rows": [row.to_dict() for row in page],
            "row_count": result.row_count,
            "page_row_count": len(page),
            "source_response_complete": result.source_response_complete,
            "metadata": {
                "sampling_metadata": [
                    item.to_dict() for item in result.sampling_metadata],
                "subject_to_thresholding": result.subject_to_thresholding,
                "data_loss_from_other_row": result.data_loss_from_other_row,
                "schema_restrictions": list(result.schema_restrictions),
                "property_quota": plain_json(result.property_quota),
            },
            "next_cursor": cursor,
            "coverage": {
                "status": (
                    "source_reported_rows"
                    if result.source_response_complete
                    else "partial_source_response"
                ),
                "scope": "authorized_ga4_report_rows",
                "row_count": result.row_count,
                "page_row_count": len(page),
                "statistical_completeness": "not_claimed",
            },
            "limitations": list(result.limitations),
            "not_claimed": [
                "statistical_completeness_from_row_count",
                "removed_rows_from_thresholding",
                "unique_user_additivity_across_scopes",
                "data_api_bigquery_equality_without_reconciliation",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }

    def _read_reconciliation(self, principal, parameters, *, now):
        allowed = {
            "source", "source_version", "asset", "template",
            "period_start", "period_end", "dimensions", "metrics",
            "api_scope", "bigquery_scope",
        }
        request = self._period_request(parameters, allowed)
        request["api_scope"] = _scope_json(
            parameters.get("api_scope"), "api_scope")
        request["bigquery_scope"] = _scope_json(
            parameters.get("bigquery_scope"), "bigquery_scope")
        source, grant, deadline = self._source(
            principal,
            parameters,
            "ga4_reconciliation",
            now=now,
        )
        result = self.adapter.read_reconciliation(
            principal.tenant_id, request)
        if not isinstance(result, AnalyticsReconciliation):
            raise DomainError(
                "source_error",
                "GA4 adapter returned an invalid reconciliation",
                503,
            )
        expected = {
            "asset_id": request["asset_id"],
            "property_id": request["property_id"],
            "template_version": request["template_version"],
            "period_start": request["period_start"],
            "period_end": request["period_end"],
            "dimensions": request["dimensions"],
            "api_scope": frozen_json(request["api_scope"]),
            "bigquery_scope": frozen_json(request["bigquery_scope"]),
        }
        if any(getattr(result, name) != value for name, value in expected.items()):
            raise DomainError(
                "incompatible_scope",
                "GA4 reconciliation scope differs from the request",
                409,
            )
        if {item.metric_id for item in result.comparisons} != set(
                request["metrics"]):
            raise DomainError(
                "incompatible_scope",
                "GA4 reconciliation metrics differ from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence, source=source, field_deadline=deadline)
        payload = result.to_dict()
        return {
            "ga4_analytics_version": GA4_ANALYTICS_VERSION,
            "operation": "reconciliation",
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": identity("ga4-reconciliation-evidence", {
                "source": source.adapter_id,
                "version": source.adapter_version,
                "request": request,
                "reconciliation": payload,
            }),
            "request": request,
            "period": payload["period"],
            "dimensions": payload["dimensions"],
            "api_scope": payload["api_scope"],
            "bigquery_scope": payload["bigquery_scope"],
            "compatible": result.compatible,
            "comparisons": payload["comparisons"],
            "coverage": {
                "status": "reconciliation_reported",
                "scope": "authorized_ga4_api_bigquery_comparison",
                "non_zero_difference_required": False,
                "unexplained_difference_allowed": True,
            },
            "limitations": list(result.limitations),
            "not_claimed": [
                "user_level_cross_source_join",
                "causal_attribution",
                "zero_difference_enforcement",
                "data_api_bigquery_equality",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }
