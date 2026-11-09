"""Selected SERP facts read from a pinned Whale version.

This is an internal typed adapter port, not an assertion about Whale's external
schema. It cannot carry a raw response, article body, credentials or AIO token.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from collections.abc import Mapping
import re
from urllib.parse import urlsplit

from .google_domain import (
    DomainError, ObservationLimitations, Query, QueryVariant,
    parse_time, plain_json, text, utc_text,
)
from .google_evidence import EvidenceReference
from .google_parser_quality import ParsedMessage

PROJECTION_VERSION = "serp-projection-1"


def _optional_text(value, name, maximum=4096):
    if value is not None:
        text(value, name, maximum=maximum)


def _integer(value, name, *, minimum=0):
    if value is not None and (type(value) is not int or value < minimum):
        raise DomainError("invalid_argument", f"{name} must be an integer >= {minimum} or unknown")


def _url(value, name):
    if value is not None:
        text(value, name, maximum=16384)
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                raise ValueError()
        except ValueError:
            raise DomainError("invalid_argument", f"{name} requires a credential-free HTTP(S) URL") from None


def _dict(value):
    return {f.name: utc_text(v) if isinstance(v := getattr(value, f.name), datetime)
            else plain_json(v)
            for f in fields(value)}


def _safe_config(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
            flat = name.replace("_", "")
            if (flat in {"key", "apikey", "secret", "clientsecret", "password", "authorization",
                         "cookie", "token"} or flat.endswith("token")):
                raise DomainError("invalid_argument", "Secret configuration cannot enter a selected-fact projection")
            _safe_config(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe_config(child)


@dataclass(frozen=True)
class SERPItem:
    item_id: str
    result_type: str
    provider_rank_group: int | None = None
    provider_rank_absolute: int | None = None
    organic_position: int | None = None
    page: int | None = None
    offset: int | None = None
    parent_block_id: str | None = None
    title: str | None = None
    snippet: str | None = None
    display_url: str | None = None
    target_url: str | None = None
    domain: str | None = None
    sponsored_label: str | None = None
    source_published_at: str | None = None
    publication_source: str | None = None
    content_semantics: str = "serp_snippet"

    def __post_init__(self):
        text(self.item_id, "item_id")
        text(self.result_type, "result_type")
        for name in ("provider_rank_group", "provider_rank_absolute", "organic_position", "page"):
            _integer(getattr(self, name), name, minimum=1)
        _integer(self.offset, "offset")
        if self.result_type != "organic" and self.organic_position is not None:
            raise DomainError("invalid_argument", "Only natural result occurrences have organic positions")
        for name in ("parent_block_id", "title", "display_url", "domain", "sponsored_label", "publication_source"):
            _optional_text(getattr(self, name), name, maximum=16384)
        _optional_text(self.snippet, "snippet", maximum=32768)
        if self.content_semantics not in {"serp_snippet", "news_index", "publisher_article"}:
            raise DomainError("invalid_argument", "Unknown content semantics")
        if self.content_semantics == "publisher_article" and not self.publication_source:
            raise DomainError("invalid_argument", "Publisher content requires its actual source")
        _url(self.target_url, "target_url")
        if self.source_published_at is not None:
            if not self.publication_source:
                raise DomainError("invalid_argument", "Publication time requires its actual source")
            object.__setattr__(self, "source_published_at", utc_text(self.source_published_at))

    def fields_used(self):
        result = set()
        for name in ("title", "snippet", "target_url", "display_url", "domain"):
            if getattr(self, name) is not None:
                result.add(name)
        if any(getattr(self, n) is not None for n in (
                "provider_rank_group", "provider_rank_absolute", "organic_position", "page", "offset")):
            result.add("ranks")
        if self.source_published_at is not None or self.publication_source is not None:
            result.add("publication")
        if self.sponsored_label is not None:
            result.add("sponsored_label")
        return result

    def to_dict(self):
        return _dict(self)


@dataclass(frozen=True)
class SERPFeature:
    feature_id: str
    feature_type: str
    presence: bool | None
    block_order: int | None = None
    expanded_state: str | None = None
    parent_block_id: str | None = None
    target_urls: tuple[str, ...] = ()
    citation_urls: tuple[str, ...] = ()

    def __post_init__(self):
        text(self.feature_id, "feature_id")
        text(self.feature_type, "feature_type")
        if self.presence is not None and type(self.presence) is not bool:
            raise DomainError("invalid_argument", "Feature presence must be true, false or unknown")
        _integer(self.block_order, "block_order")
        for name in ("expanded_state", "parent_block_id"):
            _optional_text(getattr(self, name), name)
        for name in ("target_urls", "citation_urls"):
            values = tuple(getattr(self, name))
            if len(values) > 1000:
                raise DomainError("invalid_argument", "Feature URL list exceeds the typed port limit")
            for value in values:
                _url(value, name)
                if value is None:
                    raise DomainError("invalid_argument", "Feature URL cannot be null")
            object.__setattr__(self, name, values)

    def to_dict(self):
        return _dict(self)


@dataclass(frozen=True)
class SERPObservation:
    reference: EvidenceReference
    query: Query
    variant: QueryVariant
    provider_namespace: str
    native_id: str
    run_id: str
    collection_version: str
    status: str
    returned_depth: int | None
    complete: bool | None
    cache_hit: bool | None
    cache_age_seconds: int | None
    items: tuple[SERPItem, ...] = ()
    features: tuple[SERPFeature, ...] = ()
    scheduled_at: str | None = None
    requested_at: str | None = None
    completed_at: str | None = None
    received_at: str | None = None
    ingested_at: str | None = None
    cache_original_observed_at: str | None = None
    source_task_id: str | None = None
    missing_reason: str | None = None
    truncated: bool | None = None
    parser_message: ParsedMessage | None = None
    limitations: ObservationLimitations = field(default_factory=ObservationLimitations)

    def __post_init__(self):
        if (not isinstance(self.reference, EvidenceReference) or not isinstance(self.query, Query)
                or not isinstance(self.variant, QueryVariant)):
            raise DomainError("invalid_argument", "A typed version reference, query and variant are required")
        for name in ("provider_namespace", "native_id", "run_id", "collection_version"):
            text(getattr(self, name), name)
        if self.variant.query_id != self.query.query_id or self.reference.observation_id != self.run_id:
            raise DomainError("invalid_argument", "Query/variant/run/reference identities disagree")
        _safe_config(self.variant.parameters)
        _safe_config(self.variant.location)
        if self.status not in {"succeeded", "partial", "failed"}:
            raise DomainError("invalid_argument", "Observation status is not a recognized source outcome")
        _integer(self.returned_depth, "returned_depth")
        _integer(self.cache_age_seconds, "cache_age_seconds")
        for name in ("complete", "cache_hit", "truncated"):
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise DomainError("invalid_argument", f"{name} must be boolean or unknown")
        if self.cache_hit is True and self.cache_original_observed_at is None:
            raise DomainError("invalid_argument", "Cached observations require the actual original observation time")
        if not isinstance(self.limitations, ObservationLimitations):
            raise DomainError("invalid_argument", "limitations must be typed")
        if self.cache_hit is True and "cache" not in self.limitations.entries:
            object.__setattr__(
                self, "limitations",
                self.limitations.with_entries(
                    cache={"status": "source_applied",
                           "detail": "source-reported cache hit"}))
        if self.truncated is True and "truncated" not in self.limitations.entries:
            object.__setattr__(
                self, "limitations",
                self.limitations.with_entries(
                    truncated={"status": "source_applied",
                               "detail": "source-reported truncated observation"}))
        for kind, observed in (("cache", self.cache_hit),
                               ("truncated", self.truncated)):
            entry = self.limitations.entries.get(kind)
            if entry is not None and (
                    observed is True and entry.status != "source_applied"
                    or observed is False and entry.status == "source_applied"):
                raise DomainError(
                    "invalid_argument",
                    "Limitation status disagrees with the source observation",
                )
        if self.complete is True and (self.status != "succeeded" or self.returned_depth is None
                                      or self.truncated is True):
            raise DomainError("invalid_argument", "Only successful, depth-known, nontruncated runs can be complete")
        for name in ("scheduled_at", "requested_at", "completed_at", "received_at", "ingested_at",
                     "cache_original_observed_at"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, utc_text(value))
        if (self.cache_original_observed_at is not None
                and parse_time(self.cache_original_observed_at) > self.reference.observed_at):
            raise DomainError("invalid_argument", "Original cache observation time is in the future")
        for name in ("source_task_id", "missing_reason"):
            _optional_text(getattr(self, name), name)
        for name, cls, identifier in (("items", SERPItem, "item_id"), ("features", SERPFeature, "feature_id")):
            values = tuple(getattr(self, name))
            if len(values) > 10000 or any(not isinstance(v, cls) for v in values):
                raise DomainError("invalid_argument", "Observation list exceeds the typed adapter contract")
            if len({getattr(v, identifier) for v in values}) != len(values):
                raise DomainError("invalid_argument", "Conflicting occurrence identifiers in one run")
            object.__setattr__(self, name, values)
        if self.parser_message is not None and not isinstance(
                self.parser_message, ParsedMessage):
            raise DomainError(
                "invalid_argument", "parser_message must be a typed parser result")
        for item in self.items:
            if item.organic_position is not None and (
                    self.returned_depth is None or item.organic_position > self.returned_depth):
                raise DomainError("invalid_argument", "Organic position exceeds the actual observed depth")
        if self.status == "failed" and self.items:
            raise DomainError("invalid_argument", "Failed runs must not masquerade as item observations")
        if set(self.reference.fields) != self.fields_used():
            raise DomainError("invalid_argument", "Evidence fields must exactly describe the selected projection")

    def fields_used(self):
        result = {"query", "variant", "run"}
        for item in self.items:
            result.update(item.fields_used())
        if self.features:
            result.add("features")
            if any(f.target_urls for f in self.features):
                result.add("target_url")
            if any(f.citation_urls for f in self.features):
                result.add("citation_url")
        return frozenset(result)

    def header(self):
        result = {
            f.name: (getattr(self, f.name).to_dict() if f.name == "limitations"
                     else plain_json(getattr(self, f.name)))
            for f in fields(self) if f.name not in {
                      "reference", "query", "variant", "items", "features",
                      "parser_message",
                  }}
        result.update(
            query_id=self.query.query_id, variant_id=self.variant.variant_id,
            query={"query_id": self.query.query_id, "original_text": self.query.original_text,
                   "normalized_text": self.query.normalized_text,
                   "normalization_version": self.query.normalization_version},
            variant=dict(self.variant.to_dict(), variant_id=self.variant.variant_id),
            observed_at=utc_text(self.reference.observed_at),
            source_observed_at=(utc_text(self.reference.source_observed_at)
                                if self.reference.source_observed_at else None),
            requested_depth=self.variant.depth, projection_version=PROJECTION_VERSION)
        return result

    def to_dict(self):
        return dict(self.header(), evidence=self.reference.to_dict(),
                    items=[i.to_dict() for i in self.items], features=[f.to_dict() for f in self.features])
