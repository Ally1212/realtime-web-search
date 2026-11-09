"""Fail-closed source and field/use admission; no vendor rights are assumed.

This module does not activate adapters, provision grants or approve production.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from collections.abc import Mapping
import re
from urllib.parse import parse_qsl, urlsplit

from .google_domain import DomainError, canonical_json, frozen_json, parse_time, plain_json, text

USES = frozenset({
    "raw_store", "derived_store", "aggregate", "embedding", "model_input",
    "internal_display", "export", "redistribute",
})


@dataclass(frozen=True)
class FieldRight:
    field: str
    allowed_uses: frozenset[str]
    retention_deadline: datetime | None
    no_fixed_expiry_approved: bool = False

    def __post_init__(self):
        text(self.field, "field")
        uses = frozenset(self.allowed_uses)
        if uses - USES:
            raise DomainError("invalid_argument", "Unknown allowed use")
        object.__setattr__(self, "allowed_uses", uses)
        if self.retention_deadline is None:
            if self.no_fixed_expiry_approved is not True:
                raise DomainError("use_not_permitted", "No expiry requires explicit retention approval", 403)
        else:
            object.__setattr__(self, "retention_deadline", parse_time(self.retention_deadline))

    def check(self, use, *, now):
        if self.retention_deadline is not None and parse_time(now) >= self.retention_deadline:
            raise DomainError("expired", "Field retention has expired", 410)
        if use not in USES or use not in self.allowed_uses:
            raise DomainError("use_not_permitted", "Field use is not licensed", 403)
        return self


@dataclass(frozen=True)
class SourceRegistration:
    adapter_id: str
    adapter_version: str | None
    source_api_version: str | None
    endpoint: str | None
    coverage: dict
    admission: str
    approval_ref: str | None
    license_id: str | None
    license_version: str | None
    refresh_seconds: int | None
    cache_seconds: int | None
    billing_unit: str | None
    field_rights: tuple[FieldRight, ...]
    retention_policy: str | None
    production_approved: bool = False

    def __post_init__(self):
        text(self.adapter_id, "adapter_id")
        if self.admission not in {"planned", "testing", "approved", "revoked", "not_covered"}:
            raise DomainError("invalid_argument", "Unknown source admission")
        if not isinstance(self.coverage, Mapping):
            raise DomainError("invalid_argument", "coverage must be an object")
        canonical_json(self.coverage)
        object.__setattr__(self, "coverage", frozen_json(self.coverage))
        object.__setattr__(self, "field_rights", tuple(self.field_rights))
        if self.endpoint:
            try:
                endpoint = urlsplit(self.endpoint)
                if (not endpoint.hostname or endpoint.username or endpoint.password
                        or endpoint.fragment
                        or endpoint.scheme not in {"https", "http"}
                        or endpoint.scheme == "http" and endpoint.hostname not in
                        {"openserp", "127.0.0.1", "::1", "localhost"}
                        or any(re.search(r"token|secret|key|password|auth|cookie", key, re.I)
                               for key, _ in parse_qsl(endpoint.query, keep_blank_values=True))):
                    raise ValueError()
            except (ValueError, TypeError):
                raise DomainError("invalid_argument", "Endpoint must not contain secrets or use public HTTP") from None
        if any(not isinstance(r, FieldRight) for r in self.field_rights):
            raise DomainError("invalid_argument", "Invalid field rights")
        if len({r.field for r in self.field_rights}) != len(self.field_rights):
            raise DomainError("invalid_argument", "Duplicate field rights")
        for v in (self.refresh_seconds, self.cache_seconds):
            if v is not None and (type(v) is not int or v < 0):
                raise DomainError("invalid_argument", "Cache/refresh seconds must be nonnegative integers")
        if type(self.production_approved) is not bool:
            raise DomainError("invalid_argument", "production_approved must be boolean")

    def missing(self):
        result = []
        for name in ("adapter_version", "source_api_version", "endpoint", "coverage",
                     "approval_ref", "license_id", "license_version", "billing_unit",
                     "field_rights", "retention_policy"):
            value = getattr(self, name)
            if not value or isinstance(value, str) and value.strip().lower() in {"unknown", "planned"}:
                result.append(name)
        for name in ("refresh_seconds", "cache_seconds"):
            if getattr(self, name) is None:
                result.append(name)
        return result

    def capability(self, *, now):
        missing = self.missing()
        live_rights = [r for r in self.field_rights
                       if r.retention_deadline is None or r.retention_deadline > parse_time(now)]
        ready = self.admission == "approved" and not missing and bool(live_rights)
        status = ("available" if ready else "expired" if self.admission == "approved"
                  and not missing and not live_rights else self.admission)
        if missing and self.admission not in {"revoked", "not_covered"}:
            status = "planned"
        return {
            "source_adapter": self.adapter_id, "source_version": self.adapter_version,
            "source_api_version": self.source_api_version, "coverage": plain_json(self.coverage),
            "refresh_seconds": self.refresh_seconds, "cache_seconds": self.cache_seconds,
            "billing_unit": self.billing_unit, "retention_policy": self.retention_policy,
            "status": status, "missing_requirements": missing,
            "production_ready": ready and self.production_approved,
            "readiness_basis": "rights_registration_only_not_poc_or_adapter",
            "license_id": self.license_id, "license_version": self.license_version,
            "fields": [
                {"field": r.field, "allowed_uses": sorted(r.allowed_uses) if ready else [],
                 "retention_deadline": r.retention_deadline.isoformat() if r.retention_deadline else None}
                for r in live_rights
            ],
        }

    def admit(self, field, use, *, now, production=False):
        cap = self.capability(now=now)
        if cap["status"] != "available" or production and not cap["production_ready"]:
            code = "expired" if cap["status"] == "expired" else "use_not_permitted"
            raise DomainError(code, "Source/production admission is not approved", 403)
        right = next((r for r in self.field_rights if r.field == field), None)
        if right is None:
            raise DomainError("not_covered", "Field has no source rights", 403)
        return right.check(use, now=now)


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    grant_ids: frozenset[str]
    rights_version: str
    grant_versions: Mapping[str, str] | None = None
    roles: frozenset[str] = dataclass_field(default_factory=lambda: frozenset({"reader"}))
    subject_ref: str | None = None

    def __post_init__(self):
        text(self.tenant_id, "tenant_id")
        text(self.rights_version, "rights_version")
        object.__setattr__(self, "grant_ids", frozenset(self.grant_ids))
        roles = frozenset(self.roles)
        if roles - {"reader", "manager"}:
            raise DomainError("invalid_argument", "Unknown domain role")
        object.__setattr__(self, "roles", roles)
        if self.grant_versions is not None:
            if set(self.grant_versions) != set(self.grant_ids):
                raise DomainError("invalid_argument", "Grant versions must bind every permitted asset")
            for value in self.grant_versions.values():
                text(value, "grant version")
            object.__setattr__(self, "grant_versions", frozen_json(self.grant_versions))


@dataclass(frozen=True)
class Grant:
    tenant_id: str
    asset_id: str
    grant_id: str
    version: str
    fields: frozenset[str]
    uses: frozenset[str]
    expires_at: datetime
    revoked: bool = False
    source_adapter: str | None = None
    source_version: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "fields", frozenset(self.fields))
        object.__setattr__(self, "uses", frozenset(self.uses))
        object.__setattr__(self, "expires_at", parse_time(self.expires_at))
        if self.uses - USES or type(self.revoked) is not bool:
            raise DomainError("invalid_argument", "Invalid grant uses/revocation")
        if (self.source_adapter is None) != (self.source_version is None):
            raise DomainError("invalid_argument", "Source adapter and version must be bound together")


def capability_admission(source, field, use, *, now, production=False):
    """Single source of truth used by capability metadata and task admission."""
    right = source.admit(field, use, now=now, production=production)
    return {
        "field": field,
        "use": use,
        "allowed": True,
        "retention_deadline": right.retention_deadline.isoformat()
        if right.retention_deadline else None,
    }


def authorize(principal, grant, source, field, use, *, now, production=False):
    grant_version = (principal.grant_versions.get(grant.grant_id)
                     if principal.grant_versions is not None else principal.rights_version)
    if (not principal.tenant_id or not grant.asset_id or not grant.grant_id or not grant.version
            or grant.revoked is not False or principal.tenant_id != grant.tenant_id
            or grant.grant_id not in principal.grant_ids
            or grant_version != grant.version
            or grant.source_adapter is not None and (
                grant.source_adapter != source.adapter_id or grant.source_version != source.adapter_version)):
        raise DomainError("not_authorized", "Tenant/asset/grant/field/use is not authorized", 403)
    if field not in grant.fields or use not in grant.uses:
        raise DomainError("use_not_permitted", "Field/use is not permitted by this asset grant", 403)
    if parse_time(now) >= parse_time(grant.expires_at):
        raise DomainError("expired", "Asset grant has expired", 410)
    return source.admit(field, use, now=now, production=production)


@dataclass(frozen=True)
class DeliveryContext:
    dataset_id: str
    source_platform: str
    agent_id: str
    credential_scope_ref: str
    task_id: str | None
    task_type: str | None
    provided_capabilities: frozenset[str]

    def __post_init__(self):
        for name in ("dataset_id", "source_platform", "agent_id", "credential_scope_ref"):
            text(getattr(self, name), name)
        object.__setattr__(self, "provided_capabilities", frozenset(self.provided_capabilities))
        for capability in self.provided_capabilities:
            text(capability, "provided capability")

    def to_dict(self):
        return dict(dataset_id=self.dataset_id, source_platform=self.source_platform,
                    agent_id=self.agent_id, credential_scope_ref=self.credential_scope_ref,
                    task_id=self.task_id, task_type=self.task_type,
                    provided_capabilities=sorted(self.provided_capabilities))


@dataclass(frozen=True)
class IngestPermit:
    permit_id: str
    tenant_id: str
    grant_id: str
    asset_id: str
    source_adapter: str
    source_version: str
    dataset_id: str
    source_platform: str
    agent_id: str
    credential_scope_ref: str
    modes: frozenset[str]
    ingest_classes: frozenset[str]
    capabilities: frozenset[str]
    task_types: frozenset[str]
    autonomous_allowed: bool
    admission: str
    approval_ref: str
    expires_at: datetime
    version: str
    production_approved: bool = False
    allowed_task_ids: frozenset[str] = dataclass_field(default_factory=frozenset)

    def __post_init__(self):
        for name in ("permit_id", "tenant_id", "grant_id", "asset_id", "source_adapter",
                     "source_version", "dataset_id", "source_platform", "agent_id",
                     "credential_scope_ref", "version", "approval_ref"):
            text(getattr(self, name), name)
        for name in ("modes", "ingest_classes", "capabilities", "task_types", "allowed_task_ids"):
            value = frozenset(getattr(self, name))
            for item in value:
                text(item, name)
            object.__setattr__(self, name, value)
        if (self.modes - {"scheduled", "snapshot", "live", "backfill"}
                or self.ingest_classes - {"normal", "realtime", "backfill"}
                or self.admission not in {"planned", "testing", "approved", "revoked"}
                or type(self.production_approved) is not bool
                or type(self.autonomous_allowed) is not bool):
            raise DomainError("invalid_argument", "Invalid ingest admission")
        object.__setattr__(self, "expires_at", parse_time(self.expires_at))

    def check(self, *, principal, grant, source, delivery, mode, ingest_class,
              fields, now, production=True):
        if type(production) is not bool:
            raise DomainError("invalid_argument", "Production admission must be explicit boolean")
        if parse_time(now) >= self.expires_at:
            raise DomainError("expired", "Ingest permit has expired", 410)
        if (self.admission not in {"testing", "approved"}
                or production and (self.admission != "approved" or not self.production_approved)
                or principal.tenant_id != self.tenant_id or grant.grant_id != self.grant_id
                or grant.asset_id != self.asset_id or source.adapter_id != self.source_adapter
                or source.adapter_version != self.source_version
                or mode not in self.modes or ingest_class not in self.ingest_classes):
            raise DomainError("use_not_permitted", "Ingest mode/class/source/asset is not admitted", 403)
        if (mode == "live" and ingest_class != "realtime"
                or mode == "backfill" and ingest_class != "backfill"
                or ingest_class == "backfill" and mode != "backfill"):
            raise DomainError("use_not_permitted", "Live and historical work require their own ingest class", 403)
        if not isinstance(delivery, DeliveryContext) or any(
                getattr(delivery, name) != getattr(self, name)
                for name in ("dataset_id", "source_platform", "agent_id", "credential_scope_ref")):
            raise DomainError("not_authorized", "Dataset/platform/agent/credential scope does not match", 403)
        if delivery.task_id is None:
            if delivery.task_type is not None or not self.autonomous_allowed:
                raise DomainError("not_authorized", "Autonomous delivery is not admitted", 403)
        elif (not delivery.task_id or delivery.task_id not in self.allowed_task_ids
              or delivery.task_type not in self.task_types):
            raise DomainError("not_authorized", "Task identity/type is not owned by this permit", 403)
        if not delivery.provided_capabilities <= self.capabilities:
            raise DomainError("use_not_permitted", "Message advertises unapproved capabilities", 403)
        for capability in delivery.provided_capabilities - {"identity"}:
            required = {"body": "body_text"}.get(capability, capability)
            if required not in fields:
                raise DomainError("invalid_argument", "Advertised capability has no real field")
        if "title" in fields and "title" not in delivery.provided_capabilities:
            raise DomainError("invalid_argument", "Actual title capability must be declared")
        for name in fields:
            authorize(principal, grant, source, name, "raw_store", now=now, production=production)
        return self


def inherit_rights(field, inputs):
    if not inputs:
        raise DomainError("use_not_permitted", "Derived rights require all input rights", 403)
    uses = set(inputs[0].allowed_uses)
    for right in inputs:
        uses.intersection_update(right.allowed_uses)
    deadlines = [r.retention_deadline for r in inputs if r.retention_deadline is not None]
    return FieldRight(field, frozenset(uses), min(deadlines) if deadlines else None,
                      no_fixed_expiry_approved=all(r.no_fixed_expiry_approved for r in inputs))
