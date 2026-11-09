"""Typed keyword-metric and Trends source contract.

The adapter is injected only after an explicit source approval. This module
does not call a provider, synthesize trend values, or join incomparable scales.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_domain import (
    AVAILABILITY,
    DomainError,
    MetricValue,
    decimal,
    identity,
    parse_time,
    plain_json,
    text,
    utc_text,
)
from .google_rights import authorize

METRICS_SOURCE_VERSION = "google-metrics-source-1"
METRICS_ROUTES = frozenset({
    "/keyword-metrics", "/trends/series", "/trends/related",
})
GRANULARITIES = frozenset({"hour", "day", "week", "month"})
SCALE_POLICIES = frozenset({"provider_native", "official_consistent"})
KEYWORD_METRIC_FIELDS = frozenset({
    "search_volume", "competition_index", "cpc_low", "cpc_high",
})


def _optional_text(value, name, *, maximum=4096):
    if value is not None:
        text(value, name, maximum=maximum)


def _boolean(value, name):
    if type(value) is not bool:
        raise DomainError("invalid_argument", f"{name} must be boolean")
    return value


@dataclass(frozen=True)
class MetricSourceEvidence:
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
                "invalid_argument", "Metric evidence expiry must follow observation")
        object.__setattr__(
            self, "source_observed_at", utc_text(observed))
        object.__setattr__(
            self, "evidence_expires_at", utc_text(expires))
        _optional_text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {
            field.name: plain_json(getattr(self, field.name))
            for field in fields(self)
        }


@dataclass(frozen=True)
class KeywordMetricObservation:
    evidence: MetricSourceEvidence
    provider_row_id: str
    requested_query: str
    returned_text: str
    close_variant_group: str
    geo: str
    language: str
    network: str
    year: int
    month: int
    metrics: tuple[MetricValue, ...]

    def __post_init__(self):
        if not isinstance(self.evidence, MetricSourceEvidence):
            raise DomainError(
                "invalid_argument", "Keyword metrics require typed source evidence")
        for name in (
                "provider_row_id", "requested_query", "returned_text",
                "close_variant_group", "language", "network"):
            text(getattr(self, name), name)
        if not re.fullmatch(r"[A-Z]{2}", self.geo):
            raise DomainError(
                "invalid_argument", "Keyword metric geo must be an uppercase country code")
        if type(self.year) is not int or not 2000 <= self.year <= 2100:
            raise DomainError("invalid_argument", "year is outside the supported range")
        if type(self.month) is not int or not 1 <= self.month <= 12:
            raise DomainError("invalid_argument", "month must be between 1 and 12")
        rows = tuple(self.metrics)
        if len(rows) > len(KEYWORD_METRIC_FIELDS) or any(
                not isinstance(row, MetricValue) for row in rows):
            raise DomainError("invalid_argument", "Invalid keyword metric values")
        if len({row.metric_scope.get("metric") for row in rows}) != len(rows):
            raise DomainError(
                "invalid_argument", "Keyword metric values must use distinct metric IDs")
        for row in rows:
            metric_id = row.metric_scope.get("metric")
            if metric_id not in KEYWORD_METRIC_FIELDS:
                raise DomainError(
                    "invalid_argument", "Unsupported keyword metric")
            scope = row.metric_scope
            if (
                    scope.get("requested_query") != self.requested_query
                    or scope.get("geo") != self.geo
                    or scope.get("language") != self.language
                    or scope.get("network") != self.network
                    or str(scope.get("year")) != str(self.year)
                    or str(scope.get("month")) != str(self.month)
            ):
                raise DomainError(
                    "incompatible_scope",
                    "Keyword metric value scope disagrees with its observation",
                    409,
                )
        object.__setattr__(self, "metrics", rows)

    def to_dict(self):
        return {
            "provider_row_id": self.provider_row_id,
            "requested_query": self.requested_query,
            "returned_text": self.returned_text,
            "close_variant_group": self.close_variant_group,
            "geo": self.geo,
            "language": self.language,
            "network": self.network,
            "year": self.year,
            "month": self.month,
            "metrics": {
                row.metric_scope["metric"]: row.to_dict()
                for row in self.metrics
            },
        }


@dataclass(frozen=True)
class KeywordMetricsResult:
    requested_terms: tuple[str, ...]
    close_variant_group: bool
    geo: str
    language: str
    network: str
    year: int
    month: int
    rows: tuple[KeywordMetricObservation, ...]
    complete: bool
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        terms = tuple(self.requested_terms)
        if not terms or len(terms) > 100 or any(not isinstance(term, str) for term in terms):
            raise DomainError("invalid_argument", "Invalid requested keyword terms")
        object.__setattr__(self, "requested_terms", terms)
        _boolean(self.close_variant_group, "close_variant_group")
        _boolean(self.complete, "complete")
        rows = tuple(self.rows)
        if any(not isinstance(row, KeywordMetricObservation) for row in rows):
            raise DomainError("invalid_argument", "Invalid keyword metric rows")
        for row in rows:
            if (
                    row.geo != self.geo
                    or row.language != self.language
                    or row.network != self.network
                    or row.year != self.year
                    or row.month != self.month
            ):
                raise DomainError(
                    "incompatible_scope",
                    "Keyword rows include a different request scope",
                    409,
                )
        object.__setattr__(self, "rows", rows)
        limitations = tuple(self.limitations)
        if any(not isinstance(value, str) or not value for value in limitations):
            raise DomainError("invalid_argument", "Invalid metric limitations")
        object.__setattr__(self, "limitations", limitations)


@dataclass(frozen=True)
class TrendPoint:
    period_start: str
    period_end: str
    value: Decimal | None
    availability: str
    revision: str
    low_volume: bool = False
    missing_reason: str | None = None

    def __post_init__(self):
        start, end = parse_time(self.period_start), parse_time(self.period_end)
        if start >= end:
            raise DomainError(
                "invalid_argument", "Trend point period must be nonempty")
        object.__setattr__(self, "period_start", utc_text(start))
        object.__setattr__(self, "period_end", utc_text(end))
        if self.availability not in AVAILABILITY - {"source_error", "expired"}:
            raise DomainError(
                "invalid_argument", "Unsupported trend point availability")
        text(self.revision, "revision")
        _boolean(self.low_volume, "low_volume")
        _optional_text(self.missing_reason, "missing_reason")
        value = self.value
        if self.availability in {"available", "true_zero"}:
            if value is None:
                raise DomainError(
                    "invalid_argument", "Available trend point requires a value")
            number = decimal(value)
            if number < 0:
                raise DomainError(
                    "invalid_argument", "Trend interest cannot be negative")
            if self.availability == "true_zero" and number != 0:
                raise DomainError(
                    "invalid_argument", "true_zero trend point requires zero")
            object.__setattr__(self, "value", number)
        elif value is not None:
            raise DomainError(
                "invalid_argument", "Unavailable trend point must not carry a value")
        if self.low_volume and (
                self.availability != "below_threshold" or value is not None):
            raise DomainError(
                "invalid_argument",
                "Low-volume points must be thresholded and have no value",
            )

    def to_dict(self):
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "value": str(self.value) if self.value is not None else None,
            "availability": self.availability,
            "revision": self.revision,
            "low_volume": self.low_volume,
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True)
class TrendsSeriesResult:
    evidence: MetricSourceEvidence
    series_id: str
    subject_type: str
    term_or_topic: str
    provider_topic_id: str | None
    geo: str
    category: str
    requested_from: str
    requested_to: str
    granularity: str
    timezone: str
    scale_id: str
    scale_policy: str
    sample_version: str
    points: tuple[TrendPoint, ...]
    complete: bool
    partial_reason: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, MetricSourceEvidence):
            raise DomainError(
                "invalid_argument", "Trend series requires typed source evidence")
        for name in (
                "series_id", "term_or_topic", "category", "scale_id",
                "sample_version"):
            text(getattr(self, name), name)
        if self.subject_type not in {"term", "topic"}:
            raise DomainError(
                "invalid_argument", "Trend subject_type must be term or topic")
        if self.subject_type == "topic":
            text(self.provider_topic_id, "provider_topic_id")
        elif self.provider_topic_id is not None:
            raise DomainError(
                "invalid_argument", "A term series cannot claim a provider topic ID")
        if not re.fullmatch(r"[A-Z]{2}", self.geo):
            raise DomainError(
                "invalid_argument", "Trend geo must be an uppercase country code")
        start, end = parse_time(self.requested_from), parse_time(self.requested_to)
        if start >= end:
            raise DomainError(
                "invalid_argument", "Trend request window must be nonempty")
        object.__setattr__(self, "requested_from", utc_text(start))
        object.__setattr__(self, "requested_to", utc_text(end))
        if self.granularity not in GRANULARITIES:
            raise DomainError("invalid_argument", "Unknown trend granularity")
        if self.scale_policy not in SCALE_POLICIES:
            raise DomainError("invalid_argument", "Unknown trend scale policy")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "Trend timezone must be IANA") from None
        points = tuple(self.points)
        if any(not isinstance(point, TrendPoint) for point in points):
            raise DomainError("invalid_argument", "Invalid trend points")
        previous_end = None
        for point in points:
            point_start = parse_time(point.period_start)
            point_end = parse_time(point.period_end)
            if point_start < start or point_end > end or (
                    previous_end is not None and point_start < previous_end):
                raise DomainError(
                    "incompatible_scope",
                    "Trend points overlap or escape the requested window",
                    409,
                )
            previous_end = point_end
        object.__setattr__(self, "points", points)
        _boolean(self.complete, "complete")
        if self.complete:
            if self.partial_reason is not None:
                raise DomainError(
                    "invalid_argument", "Complete series cannot carry partial reason")
        else:
            text(self.partial_reason, "partial_reason")
        limitations = tuple(self.limitations)
        if any(not isinstance(value, str) or not value for value in limitations):
            raise DomainError("invalid_argument", "Invalid trend limitations")
        object.__setattr__(self, "limitations", limitations)

    def to_dict(self):
        return {
            "series_id": self.series_id,
            "subject_type": self.subject_type,
            "term_or_topic": self.term_or_topic,
            "provider_topic_id": self.provider_topic_id,
            "geo": self.geo,
            "category": self.category,
            "requested_window": {
                "from": self.requested_from,
                "to": self.requested_to,
            },
            "granularity": self.granularity,
            "timezone": self.timezone,
            "scale_id": self.scale_id,
            "scale_policy": self.scale_policy,
            "sample_version": self.sample_version,
            "complete": self.complete,
            "partial_reason": self.partial_reason,
            "points": [point.to_dict() for point in self.points],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class RelatedQuery:
    related_id: str
    label: str
    rank: int
    provider_score: Decimal | None
    breakout: bool
    breakout_flag: str | None

    def __post_init__(self):
        text(self.related_id, "related_id")
        text(self.label, "label")
        if type(self.rank) is not int or self.rank < 1:
            raise DomainError(
                "invalid_argument", "Related-query rank must be positive")
        if self.provider_score is not None and decimal(self.provider_score) < 0:
            raise DomainError(
                "invalid_argument", "Related-query score cannot be negative")
        _boolean(self.breakout, "breakout")
        _optional_text(self.breakout_flag, "breakout_flag")
        if self.breakout and not self.breakout_flag:
            raise DomainError(
                "invalid_argument", "Breakout requires the source's raw flag")

    def to_dict(self):
        return {
            "related_id": self.related_id,
            "label": self.label,
            "rank": self.rank,
            "provider_score": (
                str(self.provider_score)
                if self.provider_score is not None else None),
            "breakout": self.breakout,
            "breakout_flag": self.breakout_flag,
            "breakout_multiplier_inferred": False,
        }


@dataclass(frozen=True)
class RelatedQueriesResult:
    evidence: MetricSourceEvidence
    source_series_id: str | None
    query: str | None
    mode: str
    geo: str
    observed_from: str
    observed_to: str
    scale_id: str
    rows: tuple[RelatedQuery, ...]
    complete: bool
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, MetricSourceEvidence):
            raise DomainError(
                "invalid_argument", "Related queries require typed source evidence")
        if (self.source_series_id is None) == (self.query is None):
            raise DomainError(
                "invalid_argument", "Specify exactly one source series or query")
        if self.source_series_id is not None:
            text(self.source_series_id, "source_series_id")
        if self.query is not None:
            text(self.query, "query")
        if self.mode not in {"top", "rising"}:
            raise DomainError(
                "invalid_argument", "Related-query mode must be top or rising")
        if not re.fullmatch(r"[A-Z]{2}", self.geo):
            raise DomainError(
                "invalid_argument", "Related-query geo must be an uppercase country code")
        start, end = parse_time(self.observed_from), parse_time(self.observed_to)
        if start >= end:
            raise DomainError(
                "invalid_argument", "Related-query window must be nonempty")
        object.__setattr__(self, "observed_from", utc_text(start))
        object.__setattr__(self, "observed_to", utc_text(end))
        text(self.scale_id, "scale_id")
        rows = tuple(self.rows)
        if any(not isinstance(row, RelatedQuery) for row in rows):
            raise DomainError("invalid_argument", "Invalid related-query rows")
        if len({row.related_id for row in rows}) != len(rows):
            raise DomainError(
                "invalid_argument", "Duplicate related-query identities")
        object.__setattr__(self, "rows", rows)
        _boolean(self.complete, "complete")
        limitations = tuple(self.limitations)
        if any(not isinstance(value, str) or not value for value in limitations):
            raise DomainError(
                "invalid_argument", "Invalid related-query limitations")
        object.__setattr__(self, "limitations", limitations)

    def to_dict(self):
        return {
            "source_series_id": self.source_series_id,
            "query": self.query,
            "mode": self.mode,
            "geo": self.geo,
            "observed_window": {
                "from": self.observed_from,
                "to": self.observed_to,
            },
            "scale_id": self.scale_id,
            "complete": self.complete,
            "rows": [row.to_dict() for row in self.rows],
            "limitations": list(self.limitations),
        }


def _parse_bool(value, name):
    if value not in {"true", "false"}:
        raise DomainError(
            "invalid_argument", f"{name} must be explicitly true or false")
    return value == "true"


def _csv(value, name, *, required=False, maximum=100):
    if value is None:
        if required:
            raise DomainError("invalid_argument", f"{name} is required")
        return []
    text(value, name, maximum=16384)
    rows = [part.strip() for part in value.split(",") if part.strip()]
    if len(rows) > maximum or len(rows) != len(set(rows)):
        raise DomainError(
            "invalid_argument",
            f"{name} must contain at most {maximum} unique values",
        )
    if required and not rows:
        raise DomainError("invalid_argument", f"{name} is required")
    return rows


class MetricsSourceService:
    """Authorize and validate one injected metric/Trends adapter."""

    def __init__(self, registry, adapter, *, clock):
        self.registry = registry
        self.adapter = adapter
        self.clock = clock

    @staticmethod
    def route(path):
        if path not in METRICS_ROUTES:
            raise DomainError("not_found", "No metrics source route matches", 404)
        return path[1:]

    def _source(self, principal, parameters, field, *, now):
        source_id = parameters.get("source")
        source_version = parameters.get("source_version")
        if not source_id or not source_version:
            raise DomainError(
                "invalid_argument", "source and source_version are required")
        source = self.registry.source(
            principal.tenant_id, source_id, source_version)
        matching = []
        for grant_id in principal.grant_ids:
            grant = self.registry.grant(principal.tenant_id, grant_id)
            if (
                    grant.source_adapter == source_id
                    and grant.source_version == source_version
                    and field in grant.fields
            ):
                matching.append(grant)
        if not matching:
            raise DomainError(
                "not_authorized",
                "No asset grant covers this metrics source and field",
                403,
            )
        if "reader" not in principal.roles:
            raise DomainError(
                "use_not_permitted",
                "The reader role is required for metrics source access",
                403,
            )
        for grant in matching:
            authorize(
                principal, grant, source, field, "internal_display",
                now=now, production=True,
            )
        grant = matching[0]
        deadlines = [grant.expires_at]
        deadlines.extend(
            right.retention_deadline
            for right in source.field_rights
            if right.field == field and right.retention_deadline is not None
        )
        return source, grant, min(deadlines)

    def _evidence(self, evidence, *, source, grant, field_deadline):
        if not isinstance(evidence, MetricSourceEvidence):
            raise DomainError(
                "source_error",
                "Metrics adapter returned no typed evidence",
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
                "Metrics result source/contract does not match the approved adapter",
                409,
            )
        expiry = parse_time(evidence.evidence_expires_at)
        if expiry > field_deadline:
            raise DomainError(
                "use_not_permitted",
                "Metrics adapter evidence exceeds the approved retention deadline",
                403,
            )
        now = parse_time(self.clock())
        if parse_time(evidence.source_observed_at) > now:
            raise DomainError(
                "incompatible_scope",
                "Metrics source observation is in the future",
                409,
            )
        if expiry <= now:
            raise DomainError(
                "expired", "Metrics source evidence has expired", 410)
        return expiry

    def read(self, principal, path, parameters, *, now):
        kind = self.route(path)
        if kind == "keyword-metrics":
            return self._keyword_metrics(
                principal, parameters, now=now)
        if kind == "trends/series":
            return self._trends_series(
                principal, parameters, now=now)
        return self._trends_related(
            principal, parameters, now=now)

    def _keyword_metrics(self, principal, parameters, *, now):
        allowed = {
            "source", "source_version", "terms", "close_variant_group",
            "geo", "language", "network", "month",
        }
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown keyword-metrics parameter")
        terms = _csv(
            parameters.get("terms"), "terms", required=True, maximum=100)
        for term in terms:
            text(term, "term", maximum=256)
        close_variant_group = _parse_bool(
            parameters.get("close_variant_group"), "close_variant_group")
        geo = parameters.get("geo", "")
        if not re.fullmatch(r"[A-Z]{2}", geo):
            raise DomainError(
                "invalid_argument", "geo must be an uppercase country code")
        language = parameters.get("language", "en")
        text(language, "language", maximum=35)
        network = parameters.get("network", "google_search")
        text(network, "network", maximum=64)
        month = parameters.get("month", "")
        match = re.fullmatch(r"(\d{4})-(\d{2})", month)
        if not match:
            raise DomainError(
                "invalid_argument", "month must use YYYY-MM")
        year, month_number = int(match.group(1)), int(match.group(2))
        if not 1 <= month_number <= 12:
            raise DomainError("invalid_argument", "Invalid month")
        source, grant, deadline = self._source(
            principal, parameters, "keyword_metrics", now=now)
        result = self.adapter.read_keyword_metrics(
            principal.tenant_id,
            {
                "terms": terms,
                "close_variant_group": close_variant_group,
                "geo": geo,
                "language": language,
                "network": network,
                "year": year,
                "month": month_number,
            },
        )
        if not isinstance(result, KeywordMetricsResult):
            raise DomainError(
                "source_error",
                "Metrics adapter returned an invalid keyword result",
                503,
            )
        if (
                tuple(result.requested_terms) != tuple(terms)
                or result.close_variant_group != close_variant_group
                or result.geo != geo
                or result.language != language
                or result.network != network
                or result.year != year
                or result.month != month_number
        ):
            raise DomainError(
                "incompatible_scope",
                "Keyword result scope differs from the request",
                409,
            )
        evidence_by_result = result.rows[0].evidence if result.rows else None
        expiry = (
            self._evidence(
                evidence_by_result,
                source=source,
                grant=grant,
                field_deadline=deadline,
            )
            if evidence_by_result is not None else deadline
        )
        for row in result.rows[1:]:
            expiry = min(
                expiry,
                self._evidence(
                    row.evidence,
                    source=source,
                    grant=grant,
                    field_deadline=deadline,
                ),
            )
        source_observed = max(
            (parse_time(row.evidence.source_observed_at)
             for row in result.rows),
            default=parse_time(now),
        )
        return {
            "metric_source_version": METRICS_SOURCE_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": utc_text(source_observed),
            "data_as_of": utc_text(source_observed),
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": identity("keyword-metrics-evidence", {
                "source": source.adapter_id,
                "version": source.adapter_version,
                "request": {
                    "terms": terms,
                    "close_variant_group": close_variant_group,
                    "geo": geo,
                    "language": language,
                    "network": network,
                    "year": year,
                    "month": month_number,
                },
                "rows": [row.to_dict() for row in result.rows],
            }),
            "request": {
                "terms": list(terms),
                "close_variant_group": close_variant_group,
                "geo": geo,
                "language": language,
                "network": network,
                "year": year,
                "month": month_number,
            },
            "rows": [row.to_dict() for row in result.rows],
            "complete": result.complete,
            "coverage": {
                "status": (
                    "source_complete" if result.complete
                    else "partial_source_result"),
                "scope": "approved_keyword_metric_rows",
                "requested_terms": len(terms),
                "returned_rows": len(result.rows),
            },
            "limitations": list(result.limitations),
            "provenance_policy": (
                "source provenance/nature retained per metric; no traffic "
                "or exact-demand inference"),
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }

    def _trends_series(self, principal, parameters, *, now):
        allowed = {
            "source", "source_version", "term", "topic", "geo", "category",
            "observed_from", "observed_to", "granularity", "timezone",
            "scale_policy",
        }
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown trends-series parameter")
        term, topic = parameters.get("term"), parameters.get("topic")
        if (term is None) == (topic is None):
            raise DomainError(
                "invalid_argument", "Specify exactly one of term or topic")
        subject_value = term if term is not None else topic
        text(subject_value, "term or topic", maximum=4096)
        geo = parameters.get("geo", "")
        if not re.fullmatch(r"[A-Z]{2}", geo):
            raise DomainError(
                "invalid_argument", "geo must be an uppercase country code")
        category = parameters.get("category", "all")
        text(category, "category", maximum=256)
        start = parse_time(parameters.get("observed_from"))
        end = parse_time(parameters.get("observed_to"))
        if start >= end:
            raise DomainError(
                "invalid_argument", "observed window must be nonempty")
        granularity = parameters.get("granularity", "day")
        if granularity not in GRANULARITIES:
            raise DomainError("invalid_argument", "Unknown granularity")
        timezone_name = parameters.get("timezone", "UTC")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "timezone must be IANA") from None
        scale_policy = parameters.get(
            "scale_policy", "provider_native")
        if scale_policy not in SCALE_POLICIES:
            raise DomainError("invalid_argument", "Unknown scale_policy")
        source, grant, deadline = self._source(
            principal, parameters, "trend_series", now=now)
        request = {
            "term": term,
            "topic": topic,
            "geo": geo,
            "category": category,
            "from": utc_text(start),
            "to": utc_text(end),
            "granularity": granularity,
            "timezone": timezone_name,
            "scale_policy": scale_policy,
        }
        result = self.adapter.read_trend_series(
            principal.tenant_id, request)
        if not isinstance(result, TrendsSeriesResult):
            raise DomainError(
                "source_error",
                "Metrics adapter returned an invalid Trends series",
                503,
            )
        expected_subject = (
            "term" if term is not None else "topic")
        if (
                result.subject_type != expected_subject
                or result.term_or_topic != subject_value
                or result.geo != geo
                or result.category != category
                or parse_time(result.requested_from) != start
                or parse_time(result.requested_to) != end
                or result.granularity != granularity
                or result.timezone != timezone_name
                or result.scale_policy != scale_policy
        ):
            raise DomainError(
                "incompatible_scope",
                "Trends series scope differs from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence,
            source=source,
            grant=grant,
            field_deadline=deadline,
        )
        payload = result.to_dict()
        return {
            "metric_source_version": METRICS_SOURCE_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": identity("trend-series-evidence", {
                "source": source.adapter_id,
                "version": source.adapter_version,
                "request": request,
                "series": payload,
            }),
            "series": payload,
            "coverage": {
                "status": (
                    "source_complete" if result.complete
                    else "partial_source_result"),
                "scope": "approved_trends_series",
                "points": len(result.points),
                "partial_reason": result.partial_reason,
            },
            "limitations": list(result.limitations),
            "scale_policy": result.scale_policy,
            "scale_policy_limitations": [
                "values_are_relative_interest_not_absolute_search_volume",
                "different_scale_ids_are_never_concatenated",
                "research_derived_scale_merge_is_not_connected",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }

    def _trends_related(self, principal, parameters, *, now):
        allowed = {
            "source", "source_version", "series", "query", "mode", "geo",
            "observed_from", "observed_to", "limit",
        }
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown trends-related parameter")
        series_id, query = parameters.get("series"), parameters.get("query")
        if (series_id is None) == (query is None):
            raise DomainError(
                "invalid_argument", "Specify exactly one of series or query")
        source_value = series_id if series_id is not None else query
        text(source_value, "series or query", maximum=4096)
        mode = parameters.get("mode", "top")
        if mode not in {"top", "rising"}:
            raise DomainError(
                "invalid_argument", "mode must be top or rising")
        geo = parameters.get("geo", "")
        if not re.fullmatch(r"[A-Z]{2}", geo):
            raise DomainError(
                "invalid_argument", "geo must be an uppercase country code")
        start = parse_time(parameters.get("observed_from"))
        end = parse_time(parameters.get("observed_to"))
        if start >= end:
            raise DomainError(
                "invalid_argument", "observed window must be nonempty")
        try:
            limit = int(parameters.get("limit", "20"))
        except ValueError:
            raise DomainError(
                "invalid_argument", "limit must be an integer") from None
        if not 1 <= limit <= 100:
            raise DomainError(
                "invalid_argument", "limit must be between 1 and 100")
        source, grant, deadline = self._source(
            principal, parameters, "related_queries", now=now)
        request = {
            "series": series_id,
            "query": query,
            "mode": mode,
            "geo": geo,
            "from": utc_text(start),
            "to": utc_text(end),
            "limit": limit,
        }
        result = self.adapter.read_trend_related(
            principal.tenant_id, request)
        if not isinstance(result, RelatedQueriesResult):
            raise DomainError(
                "source_error",
                "Metrics adapter returned invalid related queries",
                503,
            )
        if (
                result.source_series_id != series_id
                or result.query != query
                or result.mode != mode
                or result.geo != geo
                or parse_time(result.observed_from) != start
                or parse_time(result.observed_to) != end
        ):
            raise DomainError(
                "incompatible_scope",
                "Related-query scope differs from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence,
            source=source,
            grant=grant,
            field_deadline=deadline,
        )
        rows = [
            row.to_dict()
            for row in sorted(
                result.rows,
                key=lambda value: (value.rank, value.related_id),
            )[:limit]
        ]
        payload = result.to_dict()
        return {
            "metric_source_version": METRICS_SOURCE_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": identity("related-queries-evidence", {
                "source": source.adapter_id,
                "version": source.adapter_version,
                "request": request,
                "rows": payload["rows"],
            }),
            "source_series_id": series_id,
            "query": query,
            "mode": mode,
            "geo": geo,
            "observed_window": {
                "from": utc_text(start),
                "to": utc_text(end),
            },
            "scale_id": result.scale_id,
            "rows": rows,
            "returned": len(rows),
            "complete": result.complete,
            "coverage": {
                "status": (
                    "source_complete" if result.complete
                    else "partial_source_result"),
                "scope": "approved_related_query_rows",
                "requested_limit": limit,
                "returned_rows": len(rows),
            },
            "limitations": list(result.limitations),
            "breakout_policy": (
                "preserve_source_raw_flag_and_never_infer_a_fixed_multiplier"),
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }
