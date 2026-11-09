"""Typed Google Search Console report and URL-inspection contracts.

The adapter is injected only after an explicit source and production approval.
This module never calls Google, reconstructs anonymous rows, or upgrades a
completed top-rows page into statistical completeness.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import urlsplit
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

SEARCH_CONSOLE_VERSION = "google-search-console-1"
SEARCH_CONSOLE_ROUTES = frozenset({
    "/search-console/reports", "/search-console/diagnostics",
})
REPORT_DIMENSIONS = frozenset({
    "date", "hour", "query", "page", "country", "device",
    "searchAppearance", "type",
})
AGGREGATIONS = frozenset({
    "auto", "byPage", "byProperty", "byNewsShowcasePanel",
})
DATA_STATES = frozenset({"final", "all", "hourly_all"})
REPORT_KINDS = frozenset({"top_rows", "bulk"})
COMPLETENESS_STATES = frozenset({
    "top_rows_only", "bulk_partition_verified", "partial", "unknown",
})
MAX_REPORT_ROWS = 10000


def _optional_text(value, name, *, maximum=4096):
    if value is not None:
        text(value, name, maximum=maximum)


def _boolean(value, name):
    if type(value) is not bool:
        raise DomainError("invalid_argument", f"{name} must be boolean")
    return value


def _date(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError(
            "invalid_argument", f"{name} must use ISO YYYY-MM-DD") from None


def _asset_covers_url(asset_id, url):
    parsed = urlsplit(url)
    if asset_id.startswith("sc-domain:"):
        domain = asset_id[len("sc-domain:"):].strip().lower().rstrip(".")
        host = (parsed.hostname or "").lower().rstrip(".")
        return bool(
            domain
            and "/" not in domain
            and (host == domain or host.endswith("." + domain))
        )
    if asset_id.startswith(("http://", "https://")):
        property_url = urlsplit(asset_id)
        try:
            property_port = property_url.port
            inspection_port = parsed.port
        except ValueError:
            return False
        if (
                not property_url.hostname
                or parsed.scheme != property_url.scheme
                or (parsed.hostname or "").lower()
                != property_url.hostname.lower()
                or inspection_port != property_port
        ):
            return False
        property_path = property_url.path or "/"
        inspection_path = parsed.path or "/"
        if property_path == "/" or inspection_path == property_path:
            return True
        prefix = (
            property_path if property_path.endswith("/")
            else property_path + "/"
        )
        return inspection_path.startswith(prefix)
    return False


@dataclass(frozen=True)
class SearchConsoleEvidence:
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
                "Search Console evidence expiry must follow observation",
            )
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "evidence_expires_at", utc_text(expires))
        _optional_text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {
            field.name: plain_json(getattr(self, field.name))
            for field in fields(self)
        }


@dataclass(frozen=True)
class SearchConsoleRow:
    row_id: str
    day: str
    dimensions: dict
    clicks: int
    impressions: int
    ctr: Decimal | None
    position: Decimal | None

    def __post_init__(self):
        text(self.row_id, "row_id")
        parsed_day = _date(self.day, "day")
        object.__setattr__(self, "day", parsed_day.isoformat())
        if not isinstance(self.dimensions, Mapping) or not self.dimensions:
            raise DomainError(
                "invalid_argument", "Search Console row dimensions are required")
        if set(self.dimensions) - REPORT_DIMENSIONS:
            raise DomainError(
                "invalid_argument", "Unsupported Search Console row dimension")
        for name, value in self.dimensions.items():
            text(value, name, maximum=4096)
        if type(self.clicks) is not int or self.clicks < 0:
            raise DomainError(
                "invalid_argument", "Search Console clicks must be nonnegative")
        if type(self.impressions) is not int or self.impressions < 0:
            raise DomainError(
                "invalid_argument",
                "Search Console impressions must be nonnegative",
            )
        if self.ctr is not None:
            value = decimal(self.ctr)
            if not Decimal(0) <= value <= Decimal(1):
                raise DomainError(
                    "invalid_argument", "Search Console CTR must be between 0 and 1")
            object.__setattr__(self, "ctr", value)
        if self.position is not None:
            value = decimal(self.position)
            if value < 0:
                raise DomainError(
                    "invalid_argument",
                    "Search Console position cannot be negative",
                )
            object.__setattr__(self, "position", value)
        object.__setattr__(self, "dimensions", frozen_json(self.dimensions))
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "row_id": self.row_id,
            "day": self.day,
            "dimensions": plain_json(self.dimensions),
            "clicks": self.clicks,
            "impressions": self.impressions,
            "ctr": str(self.ctr) if self.ctr is not None else None,
            "position": str(self.position) if self.position is not None else None,
        }


@dataclass(frozen=True)
class SearchConsoleReport:
    evidence: SearchConsoleEvidence
    asset_id: str
    property_id: str
    template_version: str
    grain: tuple[str, ...]
    period_start: str
    period_end: str
    source_timezone: str
    data_state: str
    aggregation: str
    report_kind: str
    rows: tuple[SearchConsoleRow, ...]
    response_complete: bool
    statistical_completeness: str
    privacy: dict
    limitations: tuple[str, ...] = ()
    partition: str | None = None

    def __post_init__(self):
        if not isinstance(self.evidence, SearchConsoleEvidence):
            raise DomainError(
                "invalid_argument",
                "Search Console report requires typed source evidence",
            )
        for name in (
                "asset_id", "property_id", "template_version"):
            text(getattr(self, name), name)
        if self.asset_id != self.property_id:
            raise DomainError(
                "incompatible_scope",
                "Search Console property must match the grant asset",
                409,
            )
        grain = tuple(self.grain)
        if (
                not grain or len(grain) > len(REPORT_DIMENSIONS)
                or len(set(grain)) != len(grain)
                or set(grain) - REPORT_DIMENSIONS):
            raise DomainError(
                "invalid_argument", "Search Console grain is invalid")
        object.__setattr__(self, "grain", grain)
        start = _date(self.period_start, "period_start")
        end = _date(self.period_end, "period_end")
        if start >= end:
            raise DomainError(
                "invalid_argument",
                "Search Console period must be left-closed and right-open",
            )
        object.__setattr__(self, "period_start", start.isoformat())
        object.__setattr__(self, "period_end", end.isoformat())
        try:
            timezone_info = ZoneInfo(self.source_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise DomainError(
                "invalid_argument", "source_timezone must be IANA") from None
        # Resolve both boundaries explicitly so the source period is never
        # reinterpreted in the server's local timezone.
        datetime.combine(start, time.min, tzinfo=timezone_info)
        datetime.combine(end, time.min, tzinfo=timezone_info)
        if self.data_state not in DATA_STATES:
            raise DomainError(
                "invalid_argument", "Unknown Search Console data_state")
        if self.aggregation not in AGGREGATIONS:
            raise DomainError(
                "invalid_argument", "Unknown Search Console aggregation")
        if self.report_kind not in REPORT_KINDS:
            raise DomainError(
                "invalid_argument", "Unknown Search Console report_kind")
        if self.report_kind == "bulk":
            text(self.partition, "partition")
        elif self.partition is not None:
            raise DomainError(
                "invalid_argument",
                "Only a bulk report may identify a source partition",
            )
        rows = tuple(self.rows)
        if (
                len(rows) > MAX_REPORT_ROWS
                or any(not isinstance(row, SearchConsoleRow) for row in rows)):
            raise DomainError(
                "invalid_argument", "Search Console rows exceed the bounded result")
        if len({row.row_id for row in rows}) != len(rows):
            raise DomainError(
                "incompatible_scope",
                "Search Console row IDs must be unique",
                409,
            )
        for row in rows:
            if not start <= _date(row.day, "row.day") < end:
                raise DomainError(
                    "incompatible_scope",
                    "Search Console row is outside the requested period",
                    409,
                )
            if set(row.dimensions) != set(grain):
                raise DomainError(
                    "incompatible_scope",
                    "Search Console row dimensions disagree with the grain",
                    409,
                )
        object.__setattr__(self, "rows", rows)
        _boolean(self.response_complete, "response_complete")
        if self.statistical_completeness not in COMPLETENESS_STATES:
            raise DomainError(
                "invalid_argument",
                "Unknown Search Console statistical completeness",
            )
        if (
                self.report_kind == "top_rows"
                and self.statistical_completeness not in {"top_rows_only", "partial"}):
            raise DomainError(
                "invalid_argument",
                "Top-row responses must not claim statistical completeness",
            )
        if (
                self.statistical_completeness == "bulk_partition_verified"
                and not self.response_complete):
            raise DomainError(
                "invalid_argument",
                "Incomplete bulk responses cannot claim verified completeness",
            )
        if not isinstance(self.privacy, Mapping) or not self.privacy:
            raise DomainError(
                "invalid_argument", "Search Console privacy metadata is required")
        object.__setattr__(self, "privacy", frozen_json(self.privacy))
        limitations = tuple(self.limitations)
        if any(not isinstance(item, str) or not item for item in limitations):
            raise DomainError(
                "invalid_argument", "Invalid Search Console limitations")
        object.__setattr__(self, "limitations", limitations)
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "asset_id": self.asset_id,
            "property_id": self.property_id,
            "template_version": self.template_version,
            "grain": list(self.grain),
            "period": {
                "start": self.period_start,
                "end": self.period_end,
                "source_timezone": self.source_timezone,
            },
            "data_state": self.data_state,
            "aggregation": self.aggregation,
            "report_kind": self.report_kind,
            "partition": self.partition,
            "rows": [row.to_dict() for row in self.rows],
            "response_complete": self.response_complete,
            "statistical_completeness": self.statistical_completeness,
            "privacy": plain_json(self.privacy),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class SearchConsoleInspection:
    evidence: SearchConsoleEvidence
    asset_id: str
    property_id: str
    inspection_url: str
    inspection_version: str
    verdict: str | None
    coverage_state: str | None
    indexing_state: str | None
    robots_state: str | None
    page_fetch_state: str | None
    canonical_url: str | None
    last_crawl_time: str | None
    issues: tuple[dict, ...]
    quota: dict

    def __post_init__(self):
        if not isinstance(self.evidence, SearchConsoleEvidence):
            raise DomainError(
                "invalid_argument",
                "Search Console inspection requires typed source evidence",
            )
        for name in (
                "asset_id", "property_id", "inspection_url",
                "inspection_version"):
            text(getattr(self, name), name)
        if self.asset_id != self.property_id:
            raise DomainError(
                "incompatible_scope",
                "Search Console property must match the grant asset",
                409,
            )
        parsed = urlsplit(self.inspection_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise DomainError(
                "invalid_argument", "inspection_url must be an HTTP(S) URL")
        for name in (
                "verdict", "coverage_state", "indexing_state",
                "robots_state", "page_fetch_state", "canonical_url"):
            _optional_text(getattr(self, name), name)
        if self.canonical_url is not None:
            canonical = urlsplit(self.canonical_url)
            if canonical.scheme not in {"http", "https"} or not canonical.hostname:
                raise DomainError(
                    "invalid_argument", "canonical_url must be an HTTP(S) URL")
        if self.last_crawl_time is not None:
            parsed_time = parse_time(self.last_crawl_time)
            if parsed_time > parse_time(self.evidence.source_observed_at):
                raise DomainError(
                    "incompatible_scope",
                    "Inspection crawl time is later than source observation",
                    409,
                )
            object.__setattr__(self, "last_crawl_time", utc_text(parsed_time))
        issues = tuple(self.issues)
        if any(not isinstance(issue, dict) or not issue for issue in issues):
            raise DomainError(
                "invalid_argument", "Search Console issues must be objects")
        for issue in issues:
            canonical_json(issue)
        object.__setattr__(self, "issues", tuple(frozen_json(issue) for issue in issues))
        if not isinstance(self.quota, Mapping) or not self.quota:
            raise DomainError(
                "invalid_argument", "Search Console quota metadata is required")
        if "state" not in self.quota:
            raise DomainError(
                "invalid_argument", "Search Console quota state is required")
        if "reset_at" in self.quota and self.quota["reset_at"] is not None:
            parse_time(self.quota["reset_at"])
        if "remaining" in self.quota and self.quota["remaining"] is not None:
            if type(self.quota["remaining"]) is not int or self.quota["remaining"] < 0:
                raise DomainError(
                    "invalid_argument",
                    "Search Console quota remaining must be nonnegative",
                )
        object.__setattr__(self, "quota", frozen_json(self.quota))
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            "asset_id": self.asset_id,
            "property_id": self.property_id,
            "inspection_url": self.inspection_url,
            "inspection_version": self.inspection_version,
            "verdict": self.verdict,
            "coverage_state": self.coverage_state,
            "indexing_state": self.indexing_state,
            "robots_state": self.robots_state,
            "page_fetch_state": self.page_fetch_state,
            "canonical_url": self.canonical_url,
            "last_crawl_time": self.last_crawl_time,
            "issues": [plain_json(issue) for issue in self.issues],
            "quota": plain_json(self.quota),
        }


def _metric(value, unit, *, scope, availability, additivity):
    return MetricValue(
        value=value,
        unit=unit,
        provenance_kind="authorized_reported",
        measurement_nature="exact_in_scope",
        availability=availability,
        metric_scope=scope,
        definition_version="gsc-search-analytics-1",
        additivity=additivity,
    ).to_dict()


class SearchConsoleService:
    def __init__(self, registry, adapter, *, cursor_codec, clock,
                 revision_ledger=None):
        self.registry = registry
        self.adapter = adapter
        self.cursor_codec = cursor_codec
        self.clock = clock
        self.revision_ledger = revision_ledger

    @staticmethod
    def route(path):
        if path not in SEARCH_CONSOLE_ROUTES:
            raise DomainError(
                "not_found", "No Search Console route matches", 404)
        return path.rsplit("/", 1)[-1]

    def _source(self, principal, parameters, field, *, now, use="internal_display",
                db=None):
        source_id = parameters.get("source")
        source_version = parameters.get("source_version")
        asset_id = parameters.get("asset")
        if not source_id or not source_version or not asset_id:
            raise DomainError(
                "invalid_argument",
                "source, source_version and asset are required",
            )
        source = self.registry.source(
            principal.tenant_id, source_id, source_version, db=db)
        matching = []
        for grant_id in principal.grant_ids:
            grant = self.registry.grant(principal.tenant_id, grant_id, db=db)
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
                "No asset grant covers this Search Console source and field",
                403,
            )
        if "reader" not in principal.roles:
            raise DomainError(
                "use_not_permitted",
                "The reader role is required for Search Console access",
                403,
            )
        for grant in matching:
            authorize(
                principal,
                grant,
                source,
                field,
                use,
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

    def ingest_revision(self, principal, revision, *, now):
        """Explicit internal ingestion; report GET never persists or schedules."""
        from .google_gsc_revisions import GSCRevision

        if self.revision_ledger is None:
            raise DomainError("not_covered", "GSC revision ledger is not configured", 503)
        if not isinstance(revision, GSCRevision):
            raise DomainError("invalid_argument", "Expected typed GSC revision")
        scope = revision.scope
        if scope.tenant_id != principal.tenant_id:
            raise DomainError("not_authorized", "Partition tenant differs from principal", 403)
        if self.revision_ledger.dsn != self.registry.dsn:
            raise DomainError(
                "not_covered", "GSC ledger must share the rights registry transaction", 503)
        current = max(parse_time(now), parse_time(self.clock()))
        with self.registry.connect() as db:
            db.execute(
                "SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR SHARE",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=current, db=db)
            source, _, deadline = self._source(
                principal,
                {"source": scope.source_adapter, "source_version": scope.source_version,
                 "asset": scope.property_id},
                "gsc_report", now=current, use="derived_store", db=db,
            )
            self._evidence(
                revision.report.evidence, source=source, field_deadline=deadline)
            return self.revision_ledger.record(revision, db=db)

    def _evidence(self, evidence, *, source, field_deadline):
        if not isinstance(evidence, SearchConsoleEvidence):
            raise DomainError(
                "source_error",
                "Search Console adapter returned no typed evidence",
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
                "Search Console evidence does not match the approved adapter",
                409,
            )
        now = parse_time(self.clock())
        observed = parse_time(evidence.source_observed_at)
        expiry = parse_time(evidence.evidence_expires_at)
        if observed > now:
            raise DomainError(
                "incompatible_scope",
                "Search Console source observation is in the future",
                409,
            )
        if expiry <= now:
            raise DomainError(
                "expired", "Search Console evidence has expired", 410)
        if expiry > field_deadline:
            raise DomainError(
                "use_not_permitted",
                "Search Console evidence exceeds the approved retention deadline",
                403,
            )
        return expiry

    def _request(self, parameters):
        allowed = {
            "source", "source_version", "asset", "template",
            "period_start", "period_end", "data_state", "aggregation",
            "dimensions", "report_kind", "filters", "partition",
            "limit", "cursor",
        }
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument",
                "Unknown Search Console report parameter",
            )
        for name in (
                "template", "period_start", "period_end",
                "data_state", "aggregation", "dimensions"):
            if not parameters.get(name):
                raise DomainError(
                    "invalid_argument", f"{name} is required")
        template = parameters["template"]
        text(template, "template", maximum=256)
        start = _date(parameters["period_start"], "period_start")
        end = _date(parameters["period_end"], "period_end")
        if start >= end:
            raise DomainError(
                "invalid_argument",
                "Search Console period must be left-closed and right-open",
            )
        data_state = parameters["data_state"]
        if data_state not in DATA_STATES:
            raise DomainError(
                "invalid_argument", "Unknown Search Console data_state")
        aggregation = parameters["aggregation"]
        if aggregation not in AGGREGATIONS:
            raise DomainError(
                "invalid_argument", "Unknown Search Console aggregation")
        dimensions = [
            item.strip() for item in parameters["dimensions"].split(",")
            if item.strip()
        ]
        if (
                not dimensions or len(dimensions) > len(REPORT_DIMENSIONS)
                or len(set(dimensions)) != len(dimensions)
                or set(dimensions) - REPORT_DIMENSIONS):
            raise DomainError(
                "invalid_argument", "Search Console dimensions are invalid")
        report_kind = parameters.get("report_kind", "top_rows")
        if report_kind not in REPORT_KINDS:
            raise DomainError(
                "invalid_argument", "Unknown Search Console report_kind")
        partition = parameters.get("partition")
        if report_kind == "bulk":
            text(partition, "partition", maximum=1024)
        elif partition is not None:
            raise DomainError(
                "invalid_argument",
                "Only a bulk report may identify a partition",
            )
        filters = parameters.get("filters")
        if filters is None:
            filters_value = {}
        else:
            try:
                filters_value = json.loads(filters)
            except (TypeError, json.JSONDecodeError):
                raise DomainError(
                    "invalid_argument", "filters must be a JSON object") from None
            if (
                    not isinstance(filters_value, dict)
                    or len(filters_value) > 16
                    or set(filters_value) - REPORT_DIMENSIONS):
                raise DomainError(
                    "invalid_argument", "Search Console filters are invalid")
            for name, value in filters_value.items():
                text(value, name, maximum=4096)
        return {
            "asset_id": parameters["asset"],
            "property_id": parameters["asset"],
            "template_version": template,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "data_state": data_state,
            "aggregation": aggregation,
            "grain": dimensions,
            "report_kind": report_kind,
            "filters": filters_value,
            "partition": partition,
        }

    def _report_row(self, row, report):
        scope = {
            "asset_id": report.asset_id,
            "property_id": report.property_id,
            "template_version": report.template_version,
            "period_start": report.period_start,
            "period_end": report.period_end,
            "source_timezone": report.source_timezone,
            "data_state": report.data_state,
            "aggregation": report.aggregation,
            "report_kind": report.report_kind,
            "partition": report.partition,
            "row_id": row.row_id,
            "dimensions": plain_json(row.dimensions),
        }
        position = row.position
        position_flags = ()
        if report.report_kind == "bulk" and position is not None:
            position_flags = ("bulk_zero_based_position_unchanged",)
        return {
            **row.to_dict(),
            "metrics": {
                "clicks": _metric(
                    row.clicks,
                    "clicks",
                    scope={**scope, "metric": "clicks"},
                    availability=(
                        "true_zero" if row.clicks == 0 else "available"),
                    additivity="additive_in_scope",
                ),
                "impressions": _metric(
                    row.impressions,
                    "impressions",
                    scope={**scope, "metric": "impressions"},
                    availability=(
                        "true_zero" if row.impressions == 0 else "available"),
                    additivity="additive_in_scope",
                ),
                "ctr": _metric(
                    row.ctr,
                    "ratio",
                    scope={**scope, "metric": "ctr"},
                    availability=(
                        "not_observed" if row.ctr is None else "available"),
                    additivity="non_additive",
                ),
                "position": MetricValue(
                    value=position,
                    unit="rank",
                    provenance_kind="authorized_reported",
                    measurement_nature="exact_in_scope",
                    availability=(
                        "not_observed" if position is None else "available"),
                    metric_scope={**scope, "metric": "position"},
                    definition_version="gsc-search-analytics-1",
                    additivity="non_additive",
                    quality_flags=position_flags,
                ).to_dict(),
            },
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
                f"search-console-report:{source.adapter_id}:{source.adapter_version}"),
            "manifest_id": evidence_id,
        }
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered",
                "Search Console cursor signing is not initialized",
                503,
            )
        codec = self.cursor_codec
        after = None
        if "cursor" in parameters:
            position = codec.decode(
                parameters["cursor"], binding=binding, now=now)
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError(
                    "invalid_argument", "Invalid Search Console cursor")
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
        return self._read_diagnostics(principal, parameters, now=now)

    def _read_report(self, principal, parameters, *, now):
        request = self._request(parameters)
        source, grant, deadline = self._source(
            principal, parameters, "gsc_report", now=now)
        result = self.adapter.read_report(
            principal.tenant_id, request)
        if not isinstance(result, SearchConsoleReport):
            raise DomainError(
                "source_error",
                "Search Console adapter returned an invalid report",
                503,
            )
        expected = {
            "asset_id": request["asset_id"],
            "property_id": request["property_id"],
            "template_version": request["template_version"],
            "period_start": request["period_start"],
            "period_end": request["period_end"],
            "data_state": request["data_state"],
            "aggregation": request["aggregation"],
            "grain": tuple(request["grain"]),
            "report_kind": request["report_kind"],
            "partition": request["partition"],
        }
        if any(getattr(result, name) != value for name, value in expected.items()):
            raise DomainError(
                "incompatible_scope",
                "Search Console report scope differs from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence, source=source, field_deadline=deadline)
        evidence_id = identity("gsc-report-evidence", {
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
            "search_console_version": SEARCH_CONSOLE_VERSION,
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
            "data_state": result.data_state,
            "aggregation": result.aggregation,
            "response_complete": result.response_complete,
            "statistical_completeness": result.statistical_completeness,
            "privacy": plain_json(result.privacy),
            "rows": [self._report_row(row, result) for row in page],
            "next_cursor": cursor,
            "page_row_count": len(page),
            "total_row_count": len(result.rows),
            "coverage": {
                "status": (
                    "source_complete" if result.response_complete
                    and result.statistical_completeness
                    == "bulk_partition_verified"
                    else "top_rows_page_only" if result.report_kind == "top_rows"
                    else result.statistical_completeness),
                "scope": "approved_search_console_rows",
                "statistical_completeness": result.statistical_completeness,
                "row_count": len(result.rows),
                "page_row_count": len(page),
            },
            "limitations": list(result.limitations),
            "not_claimed": [
                "statistical_completeness_from_pagination",
                "anonymous_rows_reconstruction",
                "api_bulk_equivalence_without_grain_reconciliation",
                "user_level_cross_source_join",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }

    def _read_diagnostics(self, principal, parameters, *, now):
        allowed = {"source", "source_version", "asset", "url"}
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument",
                "Unknown Search Console diagnostics parameter",
            )
        url = parameters.get("url")
        text(url, "url", maximum=4096)
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise DomainError(
                "invalid_argument", "url must be an HTTP(S) URL")
        try:
            parsed.port
        except ValueError:
            raise DomainError(
                "invalid_argument", "url has an invalid port") from None
        if not _asset_covers_url(parameters["asset"], url):
            raise DomainError(
                "use_not_permitted",
                "Inspection URL is outside the authorized property",
                403,
            )
        source, grant, deadline = self._source(
            principal, parameters, "gsc_diagnostics", now=now)
        request = {
            "asset_id": parameters["asset"],
            "property_id": parameters["asset"],
            "inspection_url": url,
        }
        result = self.adapter.inspect_url(principal.tenant_id, request)
        if not isinstance(result, SearchConsoleInspection):
            raise DomainError(
                "source_error",
                "Search Console adapter returned an invalid inspection",
                503,
            )
        if (
                result.asset_id != request["asset_id"]
                or result.property_id != request["property_id"]
                or result.inspection_url != request["inspection_url"]):
            raise DomainError(
                "incompatible_scope",
                "Search Console inspection scope differs from the request",
                409,
            )
        expiry = self._evidence(
            result.evidence, source=source, field_deadline=deadline)
        payload = result.to_dict()
        return {
            "search_console_version": SEARCH_CONSOLE_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": getattr(self.adapter, "contract_ref", None),
            "dataset_id": getattr(self.adapter, "dataset_id", None),
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "evidence_id": identity("gsc-inspection-evidence", {
                "source": source.adapter_id,
                "version": source.adapter_version,
                "request": request,
                "inspection": payload,
            }),
            "inspection": payload,
            "coverage": {
                "status": "source_reported",
                "scope": "one_authorized_url_inspection",
            },
            "not_claimed": [
                "sitewide_index_completeness",
                "crawl_scheduling_effect",
                "unknown_fields_are_not_false",
            ],
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }
