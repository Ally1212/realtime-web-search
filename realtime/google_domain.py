"""Google domain value/identity contracts, independent of storage and credentials.

PRD GO-DAT-001/004/008/010, GO-ID-001/003/004 and GT-01/02/03.
Decimal values use JSON strings to preserve source precision across runtimes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field, fields
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = "google-domain-1"
IDENTITY_VERSION = "sha256-canonical-json-1"
PROVENANCE = frozenset({
    "public_observed", "authorized_reported", "provider_estimated",
    "research_derived", "provider_unknown",
})
NATURES = frozenset({
    "exact_in_scope", "rounded", "relative_index", "sampled", "thresholded",
    "approximate_distinct", "modeled", "unknown",
})
AVAILABILITY = frozenset({
    "available", "true_zero", "not_observed", "outside_observed_depth",
    "not_authorized", "not_covered", "privacy_suppressed", "below_threshold",
    "unsupported", "source_error", "expired", "unknown",
})
LOCATION_PRECISION = frozenset({
    "country", "administrative_area", "city", "coordinates", "unknown",
})
LIMITATION_KINDS = frozenset({
    "anonymous", "low_volume", "rounded", "top_n", "sampled", "modeled",
    "cache", "truncated",
})
LIMITATION_STATUSES = frozenset({
    "source_applied", "not_applicable", "not_observed",
    "below_threshold", "privacy_suppressed", "unknown",
})


class DomainError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(message)


def text(value, name, *, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DomainError("invalid_argument", f"{name} must be nonempty text <= {maximum} characters")
    return value


def decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise DomainError("invalid_argument", "Expected a finite decimal number")
    try:
        result = Decimal(str(value))
    except Exception:
        raise DomainError("invalid_argument", "Expected a finite decimal number") from None
    if not result.is_finite():
        raise DomainError("invalid_argument", "NaN and Infinity are not permitted")
    return result


def _json_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DomainError("invalid_argument", "Nonfinite decimal")
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise DomainError("invalid_argument", "Nonfinite float")
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise DomainError("invalid_argument", "JSON object keys must be strings")
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise DomainError("invalid_argument", f"Unsupported JSON value: {type(value).__name__}")


def canonical_json(value) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def frozen_json(value):
    """Detach and freeze nested configuration before using it as an identity."""
    canonical_json(value)
    if isinstance(value, Mapping):
        return MappingProxyType({k: frozen_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(frozen_json(v) for v in value)
    return value


def plain_json(value):
    if isinstance(value, Mapping):
        return {k: plain_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_json(v) for v in value]
    return value


@dataclass(frozen=True)
class Limitation:
    status: str
    detail: str | None = None

    def __post_init__(self):
        if self.status not in LIMITATION_STATUSES:
            raise DomainError("invalid_argument", "Unknown limitation status")
        if self.detail is not None:
            text(self.detail, "limitation detail", maximum=2048)

    def to_dict(self):
        return {"status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class ObservationLimitations:
    """Source-reported limitations that must stay separate and combinable."""
    entries: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.entries, Mapping):
            raise DomainError("invalid_argument", "limitations must be an object")
        clean = {}
        for kind, value in self.entries.items():
            if kind not in LIMITATION_KINDS:
                raise DomainError("invalid_argument", "Unknown limitation kind")
            if not isinstance(value, Limitation):
                if not isinstance(value, Mapping) or set(value) - {"status", "detail"}:
                    raise DomainError("invalid_argument", "Invalid limitation entry")
                value = Limitation(**value)
            clean[kind] = value
        object.__setattr__(self, "entries", MappingProxyType(clean))
        canonical_json(self.to_dict())

    def with_entries(self, **entries):
        return ObservationLimitations({**self.entries, **entries})

    def to_dict(self):
        return {kind: value.to_dict() for kind, value in self.entries.items()}


def identity(namespace: str, value, *, version=IDENTITY_VERSION) -> str:
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", namespace):
        raise DomainError("invalid_argument", "Invalid identity namespace")
    text(version, "identity version")
    raw = canonical_json({"namespace": namespace, "version": version, "value": value})
    return f"google:{namespace}:{hashlib.sha256(raw.encode()).hexdigest()}"


def parse_time(value) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
            raise DomainError("invalid_argument", "Expected RFC3339 date-time with timezone")
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise DomainError("invalid_argument", "Invalid RFC3339 timezone offset")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise DomainError("invalid_argument", "Invalid date-time") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError("invalid_argument", "Date-time must have a timezone")
    return value.astimezone(timezone.utc)


def utc_text(value) -> str:
    return parse_time(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Query:
    original_text: str
    normalization_version: str = "nfc-only-1"

    def __post_init__(self):
        text(self.original_text, "original_text")
        if self.normalization_version != "nfc-only-1":
            raise DomainError("unsupported", "Unknown query normalization version")

    @property
    def normalized_text(self):
        # No case folding, NFKC, punctuation removal or quote/whitespace rewriting.
        return unicodedata.normalize("NFC", self.original_text)

    @property
    def query_id(self):
        return identity("query", {"text": self.normalized_text,
                                  "normalization_version": self.normalization_version})


@dataclass(frozen=True)
class QueryVariant:
    query_id: str
    country: str
    language: str
    location: dict
    device: str
    search_engine: str
    surface: str
    channel: str
    login_state: str
    safe_search: str
    depth: int
    parameters_version: str
    parameters: dict = field(default_factory=dict)

    def __post_init__(self):
        for f in fields(self):
            if f.name not in {"location", "depth", "parameters"}:
                text(getattr(self, f.name), f.name)
        if type(self.depth) is not int or self.depth < 0:
            raise DomainError("invalid_argument", "depth must be a nonnegative integer")
        if not isinstance(self.location, Mapping) or not self.location.get("precision"):
            raise DomainError("invalid_argument", "location precision is required; use explicit unknown")
        if not isinstance(self.parameters, Mapping):
            raise DomainError("invalid_argument", "parameters must be an object")
        precision = self.location.get("precision")
        if precision not in LOCATION_PRECISION:
            raise DomainError("invalid_argument", "Unknown location precision")
        basis = self.location.get("basis")
        if basis is not None and basis not in {
                "source_provided", "user_provided", "ip_country", "unknown"}:
            raise DomainError("invalid_argument", "Unknown location basis")
        if basis == "ip_country" and precision not in {"country", "unknown"}:
            raise DomainError(
                "invalid_argument",
                "IP country evidence cannot imply an administrative or precise location",
            )
        if precision in {"administrative_area", "city", "coordinates"} and basis not in {
                "source_provided", "user_provided"}:
            raise DomainError(
                "invalid_argument",
                "Precise locations require an explicit source or user basis",
            )
        if precision in {"administrative_area", "city"}:
            text(self.location.get("code"), "location code", maximum=256)
        coordinates = self.location.get("coordinates")
        latitude, longitude = (
            self.location.get("latitude"), self.location.get("longitude"))
        if (latitude is None) != (longitude is None):
            raise DomainError(
                "invalid_argument", "Latitude and longitude must be paired")
        if precision == "coordinates":
            if coordinates is None and (latitude is None or longitude is None):
                raise DomainError(
                    "invalid_argument", "Coordinate precision requires source coordinates")
            if coordinates is not None and (
                    not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2
                    or not -90 <= decimal(coordinates[0]) <= 90
                    or not -180 <= decimal(coordinates[1]) <= 180):
                raise DomainError("invalid_argument", "Invalid coordinates")
            if coordinates is not None and latitude is not None and longitude is not None and (
                    decimal(coordinates[0]) != decimal(latitude)
                    or decimal(coordinates[1]) != decimal(longitude)):
                raise DomainError(
                    "invalid_argument", "Coordinate representations disagree")
        elif coordinates is not None:
            raise DomainError(
                "invalid_argument",
                "Coordinates cannot be attached to a non-coordinate precision",
            )
        for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            if self.location.get(key) is not None:
                if precision != "coordinates":
                    raise DomainError(
                        "invalid_argument",
                        "Coordinates cannot be attached to a non-coordinate precision",
                    )
                if not low <= decimal(self.location[key]) <= high:
                    raise DomainError("invalid_argument", f"Invalid {key}")
        canonical_json(self.to_dict())
        object.__setattr__(self, "location", frozen_json(self.location))
        object.__setattr__(self, "parameters", frozen_json(self.parameters))

    def to_dict(self):
        return {f.name: plain_json(getattr(self, f.name)) for f in fields(self)}

    @property
    def variant_id(self):
        return identity("variant", self.to_dict())


def observation_event(run_id, observed_at, payload):
    text(run_id, "run_id")
    time = utc_text(observed_at)
    return {
        "event_id": identity("event", {"run_id": run_id, "observed_at": time}),
        "run_id": run_id, "observed_at": time,
        "payload_hash": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
    }


def report_identity(*, asset_id, template_version, grain, period_start,
                    period_end, source_timezone, scope):
    text(asset_id, "asset_id")
    text(template_version, "template_version")
    try:
        ZoneInfo(source_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise DomainError("invalid_argument", "Expected IANA source timezone") from None
    if not isinstance(grain, list) or not grain or any(not isinstance(x, str) or not x for x in grain):
        raise DomainError("invalid_argument", "Report grain must be explicit")
    if len(grain) != len(set(grain)) or not isinstance(scope, dict) or not scope:
        raise DomainError("invalid_argument", "Invalid grain or scope")
    try:
        start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    except (ValueError, TypeError):
        raise DomainError("invalid_argument", "Expected date report boundaries") from None
    if start >= end:
        raise DomainError("invalid_argument", "Report period must be left-closed, right-open")
    return identity("report", dict(asset_id=asset_id, template_version=template_version,
                                   grain=grain, period_start=period_start, period_end=period_end,
                                   source_timezone=source_timezone, scope=scope))


@dataclass(frozen=True)
class MetricValue:
    value: int | float | Decimal | str | None
    unit: str
    provenance_kind: str
    measurement_nature: str
    availability: str
    metric_scope: dict
    definition_version: str
    interval: dict | None = None
    scale_id: str | None = None
    cohort_version: str | None = None
    additivity: str = "non_additive"
    compatible_with: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    evidence_expires_at: str | None = None
    limitations: ObservationLimitations = field(default_factory=ObservationLimitations)

    def __post_init__(self):
        if self.provenance_kind not in PROVENANCE or self.measurement_nature not in NATURES:
            raise DomainError("invalid_argument", "Unknown provenance or measurement nature")
        if self.availability not in AVAILABILITY:
            raise DomainError("invalid_argument", "Unknown availability")
        text(self.unit, "unit")
        text(self.definition_version, "definition_version")
        if not isinstance(self.metric_scope, Mapping) or not self.metric_scope:
            raise DomainError("invalid_argument", "metric_scope must be explicit")
        if (isinstance(self.value, bool) or self.value is not None
                and not isinstance(self.value, (str, int, float, Decimal))):
            raise DomainError("invalid_argument", "Metric value must be a scalar, not boolean or container")
        canonical_json(self.value)
        if self.availability in {"available", "true_zero"}:
            if self.value is None:
                raise DomainError("invalid_argument", "Available metric needs a value")
            if self.availability == "true_zero" and decimal(self.value) != 0:
                raise DomainError("invalid_argument", "true_zero requires numeric zero")
        elif self.value is not None:
            raise DomainError("invalid_argument", "Unavailable metric must have null value")
        if self.interval is not None:
            if not isinstance(self.interval, Mapping):
                raise DomainError("invalid_argument", "interval must be an object")
            required = {"level", "lower", "upper", "method", "calibration_population"}
            if not required <= self.interval.keys():
                raise DomainError("invalid_argument", "Interval requires method and calibration population")
            low, high, level = (decimal(self.interval[k]) for k in ("lower", "upper", "level"))
            if low > high or not 0 < level < 1 or self.value is None:
                raise DomainError("invalid_argument", "Invalid interval bounds or level")
            text(self.interval["method"], "interval method")
            text(self.interval["calibration_population"], "calibration population")
        if self.evidence_expires_at is not None:
            parse_time(self.evidence_expires_at)
        if self.additivity not in {"non_additive", "additive_in_scope", "semi_additive", "unknown"}:
            raise DomainError("invalid_argument", "Unknown additivity")
        object.__setattr__(self, "metric_scope", frozen_json(self.metric_scope))
        object.__setattr__(self, "interval", frozen_json(self.interval))
        flags = tuple(self.quality_flags)
        if any(not isinstance(x, str) or not x for x in flags):
            raise DomainError("invalid_argument", "Invalid quality flags")
        if self.interval is None and "uncertainty_not_provided" not in flags:
            flags += ("uncertainty_not_provided",)
        object.__setattr__(self, "quality_flags", flags)
        if not isinstance(self.limitations, ObservationLimitations):
            raise DomainError("invalid_argument", "limitations must be typed")
        canonical_json(self.to_dict())

    def to_dict(self):
        return {
            f.name: (getattr(self, f.name).to_dict() if f.name == "limitations"
                     else plain_json(getattr(self, f.name)))
            for f in fields(self)
        }
