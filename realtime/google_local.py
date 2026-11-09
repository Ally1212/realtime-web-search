"""Conditional Local/Places/review contracts with fail-closed source rights.

No map tiles, reviews, or business metadata are fabricated. Every route requires
an independently approved adapter, exact asset grant, field/use rights and
unexpired typed evidence.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields

from .google_domain import (
    DomainError, canonical_json, decimal, identity, parse_time, plain_json,
    text, utc_text,
)
from .google_evidence import page_limit
from .google_rights import authorize

LOCAL_VERSION = "google-local-1"
LOCAL_ROUTES = frozenset({
    "/local/grids", "/local/grids/{id}/observations",
    "/places/{id}/reference", "/local/review-insights",
})
OBSERVATION_STATES = frozenset({"observed", "missing", "failed", "excluded"})


def _point(value, name):
    if not isinstance(value, dict) or set(value) != {"lat", "lng"}:
        raise DomainError("invalid_argument", f"{name} must contain lat/lng")
    lat, lng = decimal(value["lat"]), decimal(value["lng"])
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise DomainError("invalid_argument", f"{name} coordinates are invalid")
    return {"lat": str(lat), "lng": str(lng)}


@dataclass(frozen=True)
class LocalEvidence:
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
        observed, expires = (
            parse_time(self.source_observed_at),
            parse_time(self.evidence_expires_at),
        )
        if expires <= observed:
            raise DomainError(
                "invalid_argument", "Local evidence expiry must follow observation")
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "evidence_expires_at", utc_text(expires))
        if self.provider_request_id is not None:
            text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class GridPoint:
    point_id: str
    lat: str
    lng: str

    def __post_init__(self):
        text(self.point_id, "point_id")
        value = _point({"lat": self.lat, "lng": self.lng}, "grid point")
        object.__setattr__(self, "lat", value["lat"])
        object.__setattr__(self, "lng", value["lng"])

    def to_dict(self):
        return {"point_id": self.point_id, "lat": self.lat, "lng": self.lng}


@dataclass(frozen=True)
class GridDefinition:
    grid_id: str
    grid_version: str
    query: str
    variant_ref: str
    center: dict
    points: tuple[GridPoint, ...]
    excluded_points: tuple[GridPoint, ...]
    spacing_m: int
    method: str

    def __post_init__(self):
        for name in (
                "grid_id", "grid_version", "query", "variant_ref", "method"):
            text(getattr(self, name), name, maximum=1024)
        object.__setattr__(self, "center", _point(self.center, "grid center"))
        points, excluded = tuple(self.points), tuple(self.excluded_points)
        if (
                not points
                or len(points) > 10000
                or any(not isinstance(p, GridPoint) for p in points)
                or any(not isinstance(p, GridPoint) for p in excluded)
        ):
            raise DomainError("invalid_argument", "Grid points are invalid")
        ids = [p.point_id for p in points]
        if len(set(ids)) != len(ids):
            raise DomainError("invalid_argument", "Grid point IDs must be unique")
        excluded_ids = {p.point_id for p in excluded}
        if excluded_ids & set(ids):
            raise DomainError(
                "invalid_argument", "Excluded points cannot be sampled")
        if type(self.spacing_m) is not int or self.spacing_m <= 0:
            raise DomainError("invalid_argument", "Grid spacing must be positive")
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "excluded_points", excluded)

    def to_dict(self):
        return {
            "grid_id": self.grid_id,
            "grid_version": self.grid_version,
            "query": self.query,
            "variant_ref": self.variant_ref,
            "center": plain_json(self.center),
            "points": [p.to_dict() for p in self.points],
            "excluded_points": [p.to_dict() for p in self.excluded_points],
            "spacing_m": self.spacing_m,
            "method": self.method,
        }


@dataclass(frozen=True)
class GridItem:
    place_id: str
    rank: int
    title: str
    source_url: str | None = None

    def __post_init__(self):
        text(self.place_id, "place_id")
        text(self.title, "title", maximum=1024)
        if type(self.rank) is not int or self.rank < 1:
            raise DomainError("invalid_argument", "Grid item rank must be positive")
        if self.source_url is not None:
            text(self.source_url, "source_url", maximum=4096)

    def to_dict(self):
        return {
            "place_id": self.place_id, "rank": self.rank,
            "title": self.title, "source_url": self.source_url,
        }


@dataclass(frozen=True)
class GridObservation:
    observation_id: str
    run_id: str
    point_id: str
    status: str
    depth: int | None
    items: tuple[GridItem, ...]
    observed_at: str
    cost_units: str | None = None
    reason: str | None = None

    def __post_init__(self):
        for name in ("observation_id", "run_id", "point_id"):
            text(getattr(self, name), name)
        if self.status not in OBSERVATION_STATES:
            raise DomainError("invalid_argument", "Unknown grid observation state")
        if self.depth is not None and (
                type(self.depth) is not int or not 1 <= self.depth <= 1000):
            raise DomainError("invalid_argument", "Observation depth is invalid")
        items = tuple(self.items)
        if any(not isinstance(item, GridItem) for item in items):
            raise DomainError("invalid_argument", "Grid items are invalid")
        if self.status != "observed" and items:
            raise DomainError(
                "invalid_argument", "Missing/failed/excluded observations cannot contain items")
        ranks = [item.rank for item in items]
        if len(set(ranks)) != len(ranks) or any(
                rank > (self.depth or 0) for rank in ranks):
            raise DomainError(
                "incompatible_scope", "Grid item ranks disagree with depth", 409)
        if self.cost_units is not None:
            cost = decimal(self.cost_units)
            if cost < 0:
                raise DomainError("invalid_argument", "Cost cannot be negative")
            object.__setattr__(self, "cost_units", str(cost))
        if self.reason is not None:
            text(self.reason, "reason")
        object.__setattr__(self, "observed_at", utc_text(self.observed_at))
        object.__setattr__(self, "items", items)

    def to_dict(self):
        return {
            "observation_id": self.observation_id,
            "run_id": self.run_id,
            "point_id": self.point_id,
            "status": self.status,
            "depth": self.depth,
            "items": [item.to_dict() for item in self.items],
            "observed_at": self.observed_at,
            "cost_units": self.cost_units,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GridBundle:
    evidence: LocalEvidence
    asset_id: str
    grids: tuple[GridDefinition, ...]

    def __post_init__(self):
        text(self.asset_id, "asset_id")
        grids = tuple(self.grids)
        if not grids or any(not isinstance(g, GridDefinition) for g in grids):
            raise DomainError("invalid_argument", "Grid bundle is invalid")
        if len({g.grid_id for g in grids}) != len(grids):
            raise DomainError("incompatible_scope", "Duplicate grid ID", 409)
        object.__setattr__(self, "grids", grids)


@dataclass(frozen=True)
class ObservationBundle:
    evidence: LocalEvidence
    asset_id: str
    grid_id: str
    observations: tuple[GridObservation, ...]
    response_complete: bool

    def __post_init__(self):
        text(self.asset_id, "asset_id")
        text(self.grid_id, "grid_id")
        rows = tuple(self.observations)
        if any(not isinstance(r, GridObservation) for r in rows):
            raise DomainError("invalid_argument", "Grid observations are invalid")
        if len({r.observation_id for r in rows}) != len(rows):
            raise DomainError("incompatible_scope", "Duplicate observation ID", 409)
        if type(self.response_complete) is not bool:
            raise DomainError("invalid_argument", "response_complete must be boolean")
        object.__setattr__(self, "observations", rows)


@dataclass(frozen=True)
class PlaceReference:
    evidence: LocalEvidence
    asset_id: str
    place_id: str
    provider_namespace: str
    source_url: str | None
    display_allowed: bool
    raw_store_allowed: bool
    restrictions: tuple[str, ...]

    def __post_init__(self):
        for name in ("asset_id", "place_id", "provider_namespace"):
            text(getattr(self, name), name, maximum=1024)
        if self.source_url is not None:
            text(self.source_url, "source_url", maximum=4096)
        for name in ("display_allowed", "raw_store_allowed"):
            if type(getattr(self, name)) is not bool:
                raise DomainError("invalid_argument", f"{name} must be boolean")
        restrictions = tuple(self.restrictions)
        for item in restrictions:
            text(item, "restriction")
        object.__setattr__(self, "restrictions", restrictions)

    def to_dict(self):
        return {
            "place_id": self.place_id,
            "provider_namespace": self.provider_namespace,
            "source_url": self.source_url,
            "display_allowed": self.display_allowed,
            "raw_store_allowed": self.raw_store_allowed,
            "restrictions": list(self.restrictions),
        }


@dataclass(frozen=True)
class ReviewTheme:
    label: str
    count: int
    eligible_count: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        text(self.label, "review theme")
        if (
                type(self.count) is not int or self.count < 0
                or type(self.eligible_count) is not int
                or self.eligible_count < self.count
        ):
            raise DomainError("invalid_argument", "Review theme count is invalid")
        refs = tuple(self.evidence_refs)
        if not refs or any(not isinstance(r, str) or not r for r in refs):
            raise DomainError("invalid_argument", "Review theme evidence is required")
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self):
        return {
            "label": self.label,
            "count": self.count,
            "eligible_count": self.eligible_count,
            "proportion": str(
                decimal(self.count) / decimal(self.eligible_count)
                if self.eligible_count else 0),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ReviewInsight:
    evidence: LocalEvidence
    asset_id: str
    place_id: str
    sample_size: int
    eligible_count: int
    themes: tuple[ReviewTheme, ...]
    sorting: str
    limitations: tuple[str, ...]

    def __post_init__(self):
        for name in ("asset_id", "place_id", "sorting"):
            text(getattr(self, name), name, maximum=1024)
        if (
                type(self.sample_size) is not int or self.sample_size < 0
                or type(self.eligible_count) is not int
                or not 0 <= self.eligible_count <= self.sample_size
        ):
            raise DomainError("invalid_argument", "Review sample counts are invalid")
        themes = tuple(self.themes)
        if any(not isinstance(t, ReviewTheme) for t in themes):
            raise DomainError("invalid_argument", "Review themes are invalid")
        if any(t.eligible_count != self.eligible_count for t in themes):
            raise DomainError(
                "incompatible_scope", "Review theme denominator mismatch", 409)
        limitations = tuple(self.limitations)
        for item in limitations:
            text(item, "limitation")
        object.__setattr__(self, "themes", themes)
        object.__setattr__(self, "limitations", limitations)


FIELDS = {
    "grids": "local_grid_definition",
    "observations": "local_observation",
    "place": "place_reference",
    "reviews": "review_insight",
}


class LocalService:
    def __init__(self, registry, adapter, *, cursor_codec, clock):
        self.registry = registry
        self.adapter = adapter
        self.cursor_codec = cursor_codec
        self.clock = clock

    @staticmethod
    def route(path):
        if path == "/local/grids":
            return "grids", None
        match = re.fullmatch(r"/local/grids/([^/]+)/observations", path)
        if match:
            return "observations", match.group(1)
        match = re.fullmatch(r"/places/([^/]+)/reference", path)
        if match:
            return "place", match.group(1)
        if path == "/local/review-insights":
            return "reviews", None
        raise DomainError("not_found", "No Local route matches", 404)

    def _context(self, principal, parameters, field, *, now, allowed):
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown Local parameter; raw source proxy is disabled")
        for name in ("source", "source_version", "asset"):
            text(parameters.get(name), name, maximum=256)
        source = self.registry.source(
            principal.tenant_id, parameters["source"], parameters["source_version"])
        grants = []
        for grant_id in sorted(principal.grant_ids):
            grant = self.registry.grant(principal.tenant_id, grant_id)
            if (
                    grant.source_adapter == source.adapter_id
                    and grant.source_version == source.adapter_version
                    and grant.asset_id == parameters["asset"]
                    and field in grant.fields
            ):
                grants.append(grant)
        if "reader" not in principal.roles or not grants:
            raise DomainError(
                "not_authorized", "No Local asset grant covers this capability", 403)
        grant = grants[0]
        right = authorize(
            principal, grant, source, field, "internal_display",
            now=now, production=True)
        deadlines = [grant.expires_at]
        if right.retention_deadline is not None:
            deadlines.append(right.retention_deadline)
        return source, grant, min(deadlines)

    def _evidence(self, evidence, *, source, deadline):
        if not isinstance(evidence, LocalEvidence):
            raise DomainError("source_error", "Local adapter returned no typed evidence", 503)
        now = parse_time(self.clock())
        if (
                evidence.adapter_id != source.adapter_id
                or evidence.adapter_version != source.adapter_version
                or evidence.source_api_version != source.source_api_version
                or evidence.contract_ref != getattr(self.adapter, "contract_ref", None)
                or evidence.dataset_id != getattr(self.adapter, "dataset_id", None)
        ):
            raise DomainError(
                "incompatible_scope", "Local evidence does not match the adapter", 409)
        observed = parse_time(evidence.source_observed_at)
        expiry = parse_time(evidence.evidence_expires_at)
        if observed > now:
            raise DomainError(
                "incompatible_scope", "Local observation is in the future", 409)
        if expiry <= now:
            raise DomainError("expired", "Local evidence has expired", 410)
        if expiry > deadline:
            raise DomainError(
                "use_not_permitted", "Local evidence exceeds approved retention", 403)
        return now, expiry

    def _base(self, source, result, expiry, grant):
        return {
            "local_version": LOCAL_VERSION,
            "source_adapter": source.adapter_id,
            "source_version": source.adapter_version,
            "source_api_version": source.source_api_version,
            "contract_ref": result.evidence.contract_ref,
            "dataset_id": result.evidence.dataset_id,
            "source_observed_at": result.evidence.source_observed_at,
            "data_as_of": result.evidence.source_observed_at,
            "evidence_expires_at": utc_text(expiry),
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
        }

    def _page(self, parameters, rows, *, request, source, expiry, evidence_id):
        limit = page_limit(parameters.get("limit"))
        binding = {
            "tenant_id": request["tenant_id"],
            "rights_version": request["rights_version"],
            "filters": {**request, "limit": limit},
            "sort": ["row_id"],
            "source": f"local:{source.adapter_id}:{source.adapter_version}",
            "manifest_id": evidence_id,
        }
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered", "Local cursor signing is not initialized", 503)
        after = None
        if "cursor" in parameters:
            position = self.cursor_codec.decode(
                parameters["cursor"], binding=binding, now=parse_time(self.clock()))
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError("invalid_argument", "Invalid Local cursor")
            after = position[0]
        ordered = sorted(
            (r for r in rows if after is None or r["row_id"] > after),
            key=lambda r: r["row_id"])
        page, more = ordered[:limit], len(ordered) > limit
        cursor = self.cursor_codec.encode(
            binding=binding, position=[page[-1]["row_id"]],
            expires_at=expiry, now=parse_time(self.clock()),
        ) if more else None
        return [r["value"] for r in page], cursor

    def read(self, principal, path, parameters, *, now):
        kind, identifier = self.route(path)
        allowed = {"source", "source_version", "asset"}
        if kind in {"grids", "observations"}:
            allowed |= {"limit", "cursor"}
        if kind == "reviews":
            allowed.add("place_id")
        source, grant, deadline = self._context(
            principal, parameters, FIELDS[kind], now=now, allowed=allowed)
        if kind == "grids":
            result = self.adapter.grids(
                principal.tenant_id, {"asset_id": parameters["asset"]})
            if not isinstance(result, GridBundle):
                raise DomainError("source_error", "Local adapter returned invalid grids", 503)
            if result.asset_id != parameters["asset"]:
                raise DomainError("incompatible_scope", "Grid asset mismatch", 409)
            current, expiry = self._evidence(
                result.evidence, source=source, deadline=deadline)
            self.registry.assert_current(principal, now=current)
            evidence_id = identity("local-grids", result.grids[0].to_dict()
                                   if len(result.grids) == 1 else [g.to_dict() for g in result.grids])
            request = {
                "tenant_id": principal.tenant_id,
                "rights_version": principal.rights_version,
                "asset_id": parameters["asset"],
            }
            rows = [{"row_id": g.grid_id, "value": g.to_dict()} for g in result.grids]
            page, cursor = self._page(
                parameters, rows, request=request, source=source,
                expiry=expiry, evidence_id=evidence_id)
            return {
                **self._base(source, result, expiry, grant),
                "evidence_id": evidence_id,
                "asset_id": result.asset_id,
                "grids": page,
                "next_cursor": cursor,
                "coverage": {"status": "source_reported_grids", "scope": "approved_local_grids"},
                "not_claimed": ["maps_data_reuse_license", "real_user_location", "complete_market_inventory"],
            }
        if kind == "observations":
            grid_id = identifier
            result = self.adapter.observations(
                principal.tenant_id, {"asset_id": parameters["asset"], "grid_id": grid_id})
            if not isinstance(result, ObservationBundle):
                raise DomainError("source_error", "Local adapter returned invalid observations", 503)
            if result.asset_id != parameters["asset"] or result.grid_id != grid_id:
                raise DomainError("incompatible_scope", "Observation scope mismatch", 409)
            current, expiry = self._evidence(
                result.evidence, source=source, deadline=deadline)
            if any(parse_time(row.observed_at) > current
                   for row in result.observations):
                raise DomainError(
                    "incompatible_scope",
                    "Local observation time is in the future",
                    409,
                )
            self.registry.assert_current(principal, now=current)
            evidence_id = identity("local-observations", {
                "asset_id": result.asset_id, "grid_id": result.grid_id,
                "rows": [r.to_dict() for r in result.observations],
            })
            request = {
                "tenant_id": principal.tenant_id,
                "rights_version": principal.rights_version,
                "asset_id": parameters["asset"], "grid_id": grid_id,
            }
            rows = [{"row_id": r.observation_id, "value": r.to_dict()}
                    for r in result.observations]
            page, cursor = self._page(
                parameters, rows, request=request, source=source,
                expiry=expiry, evidence_id=evidence_id)
            return {
                **self._base(source, result, expiry, grant),
                "evidence_id": evidence_id,
                "asset_id": result.asset_id,
                "grid_id": result.grid_id,
                "observations": page,
                "response_complete": result.response_complete,
                "next_cursor": cursor,
                "coverage": {
                    "status": "source_complete" if result.response_complete else "partial_grid_coverage",
                    "scope": "approved_grid_points",
                },
                "not_claimed": ["point_is_real_user", "unobserved_is_no_ranking", "complete_geographic_coverage"],
            }
        if kind == "place":
            place_id = identifier
            result = self.adapter.place_reference(
                principal.tenant_id, {"asset_id": parameters["asset"], "place_id": place_id})
            if not isinstance(result, PlaceReference):
                raise DomainError("source_error", "Local adapter returned invalid place", 503)
            if result.asset_id != parameters["asset"] or result.place_id != place_id:
                raise DomainError("incompatible_scope", "Place scope mismatch", 409)
            if result.raw_store_allowed and "raw_store" not in grant.uses:
                raise DomainError(
                    "use_not_permitted",
                    "Place reference claims an unlicensed raw-store capability",
                    403,
                )
            current, expiry = self._evidence(
                result.evidence, source=source, deadline=deadline)
            self.registry.assert_current(principal, now=current)
            return {
                **self._base(source, result, expiry, grant),
                "evidence_id": identity("local-place", result.to_dict()),
                "place": result.to_dict(),
                "coverage": {"status": "reference_only", "scope": "approved_place_reference"},
                "not_claimed": ["unlicensed_full_business_profile", "all_reviews", "long_term_google_attribute_store"],
            }
        place_id = parameters.get("place_id")
        text(place_id, "place_id", maximum=1024)
        result = self.adapter.review_insights(
            principal.tenant_id, {"asset_id": parameters["asset"], "place_id": place_id})
        if not isinstance(result, ReviewInsight):
            raise DomainError("source_error", "Local adapter returned invalid review insight", 503)
        if result.asset_id != parameters["asset"] or result.place_id != place_id:
            raise DomainError("incompatible_scope", "Review insight scope mismatch", 409)
        current, expiry = self._evidence(
            result.evidence, source=source, deadline=deadline)
        self.registry.assert_current(principal, now=current)
        return {
            **self._base(source, result, expiry, grant),
            "evidence_id": identity("local-review-insight", {
                "asset_id": result.asset_id, "place_id": result.place_id,
                "sample_size": result.sample_size,
                "eligible_count": result.eligible_count,
                "themes": [t.to_dict() for t in result.themes],
            }),
            "asset_id": result.asset_id,
            "place_id": result.place_id,
            "sample_size": result.sample_size,
            "eligible_count": result.eligible_count,
            "sorting": result.sorting,
            "themes": [t.to_dict() for t in result.themes],
            "limitations": list(result.limitations),
            "coverage": {"status": "approved_sample_insight", "scope": "independent_review_source"},
            "not_claimed": ["consumer_sentiment_for_all_reviews", "raw_review_archive", "official_rating_equivalent"],
        }
