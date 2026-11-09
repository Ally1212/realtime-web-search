"""Immutable evidence manifests and scope-bound cursors (GO-AC-007/API-102).

Only references to independently readable Whale versions belong in a manifest.
Access always resolves current grants/rights; a historical manifest grants none.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, fields
from datetime import datetime

from .google_domain import (
    CONTRACT_VERSION, DomainError, canonical_json, frozen_json, identity,
    parse_time, plain_json, text, utc_text,
)
from .google_rights import authorize


def page_limit(value):
    if value is None:
        return 20
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value) if len(value) <= 3 else 101
    if type(value) is not int or not 1 <= value <= 100:
        raise DomainError("invalid_argument", "limit must be an integer between 1 and 100")
    return value


@dataclass(frozen=True)
class EvidenceReference:
    document_id: str
    document_version: str
    observation_id: str
    source_adapter: str
    source_adapter_version: str
    source_api_version: str
    license_id: str
    license_version: str
    parser_version: str
    source_hash: str
    grant_id: str
    asset_id: str
    fields: tuple[str, ...]
    source_observed_at: datetime | None
    observed_at: datetime
    evidence_expires_at: datetime | None

    def __post_init__(self):
        for name in ("document_id", "document_version", "observation_id",
                     "source_adapter", "source_adapter_version", "source_api_version",
                     "license_id", "license_version", "parser_version", "grant_id", "asset_id"):
            text(getattr(self, name), name)
        if not isinstance(self.source_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_hash):
            raise DomainError("invalid_argument", "Evidence requires a SHA256 source hash")
        if not self.fields or any(not isinstance(f, str) or not f for f in self.fields):
            raise DomainError("invalid_argument", "Evidence fields must be explicit")
        if len(set(self.fields)) != len(self.fields):
            raise DomainError("invalid_argument", "Duplicate evidence fields")
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "observed_at", parse_time(self.observed_at))
        if self.source_observed_at is not None:
            source_time = parse_time(self.source_observed_at)
            if source_time > self.observed_at:
                raise DomainError("invalid_argument", "Source time is after local observation")
            object.__setattr__(self, "source_observed_at", source_time)
        if self.evidence_expires_at is not None:
            object.__setattr__(self, "evidence_expires_at", parse_time(self.evidence_expires_at))

    def to_dict(self):
        return {f.name: utc_text(value) if isinstance(value := getattr(self, f.name), datetime)
                else plain_json(value) for f in fields(self)}

    @property
    def evidence_id(self):
        return identity("evidence", self.to_dict())


@dataclass(frozen=True)
class FrozenManifest:
    tenant_id: str
    rights_version: str
    references: tuple[EvidenceReference, ...]
    scope: dict
    query_template: str
    parameters: dict
    method_versions: dict
    collection_version: str
    rights_at_query: dict
    created_at: datetime
    expires_at: datetime | None

    def __post_init__(self):
        for name in ("tenant_id", "rights_version", "query_template", "collection_version"):
            text(getattr(self, name), name)
        if not self.references or any(not isinstance(r, EvidenceReference) for r in self.references):
            raise DomainError("invalid_argument", "Manifest requires versioned evidence references")
        object.__setattr__(self, "references", tuple(self.references))
        for name in ("scope", "parameters", "method_versions", "rights_at_query"):
            value = getattr(self, name)
            if not value:
                raise DomainError("invalid_argument", f"Manifest {name} must be explicit")
            object.__setattr__(self, name, frozen_json(value))
        object.__setattr__(self, "created_at", parse_time(self.created_at))
        if self.expires_at is not None:
            expiry = parse_time(self.expires_at)
            if expiry <= self.created_at:
                raise DomainError("expired", "Manifest evidence has expired", 410)
            object.__setattr__(self, "expires_at", expiry)

    @classmethod
    def create(cls, *, principal, references, scope, query_template, parameters,
               method_versions, collection_version, now, resolve):
        references = tuple(references)
        expiries, rights = [], {}
        for ref in references:
            source, grant = resolve(ref)
            if not cls._source_matches(ref, source) or grant.grant_id != ref.grant_id or grant.asset_id != ref.asset_id:
                raise DomainError("not_authorized", "Evidence source/grant/asset does not match", 403)
            allowed = {}
            for name in ref.fields:
                right = authorize(principal, grant, source, name, "internal_display", now=now)
                allowed[name] = sorted(right.allowed_uses.intersection(grant.uses))
                if right.retention_deadline is not None:
                    expiries.append(right.retention_deadline)
            key = identity("evidence-rights", ref.to_dict())
            if key in rights:
                raise DomainError("invalid_argument", "Duplicate evidence reference in manifest")
            rights[key] = {"grant_version": grant.version, "allowed_uses": allowed}
            expiries.append(parse_time(grant.expires_at))
            if ref.evidence_expires_at is not None:
                expiries.append(ref.evidence_expires_at)
        return cls(
            tenant_id=principal.tenant_id, rights_version=principal.rights_version,
            references=references, scope=scope, query_template=query_template,
            parameters=parameters, method_versions=method_versions,
            collection_version=collection_version, created_at=now,
            rights_at_query=rights,
            expires_at=min(expiries) if expiries else None,
        )

    @staticmethod
    def _source_matches(ref, source):
        return (source.adapter_id == ref.source_adapter
                and source.adapter_version == ref.source_adapter_version
                and source.source_api_version == ref.source_api_version
                and source.license_id == ref.license_id
                and source.license_version == ref.license_version)

    def check_access(self, principal, *, use, now, resolve):
        if principal.tenant_id != self.tenant_id or principal.rights_version != self.rights_version:
            raise DomainError("not_authorized", "Manifest tenant/rights version changed", 403)
        if self.expires_at is not None and parse_time(now) >= self.expires_at:
            raise DomainError("expired", "Manifest evidence has expired; it cannot be replayed", 410)
        for ref in self.references:
            if ref.evidence_expires_at is not None and parse_time(now) >= ref.evidence_expires_at:
                raise DomainError("expired", "Evidence is no longer reproducible", 410)
            source, grant = resolve(ref)
            if not self._source_matches(ref, source) or grant.asset_id != ref.asset_id or grant.grant_id != ref.grant_id:
                raise DomainError("not_authorized", "Current evidence authorization does not match", 403)
            key = identity("evidence-rights", ref.to_dict())
            snapshot = self.rights_at_query.get(key)
            if snapshot is None or snapshot["grant_version"] != grant.version:
                raise DomainError("not_authorized", "Evidence grant version changed", 403)
            for name in ref.fields:
                if use not in snapshot["allowed_uses"].get(name, ()):
                    raise DomainError("use_not_permitted", "Use exceeds frozen manifest rights", 403)
                authorize(principal, grant, source, name, use, now=now)

    def to_dict(self):
        return {
            "contract_version": CONTRACT_VERSION,
            "tenant_id": self.tenant_id, "rights_version": self.rights_version,
            "references": [r.to_dict() for r in self.references], "scope": plain_json(self.scope),
            "query_template": self.query_template, "parameters": plain_json(self.parameters),
            "method_versions": plain_json(self.method_versions), "collection_version": self.collection_version,
            "rights_at_query": plain_json(self.rights_at_query),
            "created_at": utc_text(self.created_at),
            "expires_at": utc_text(self.expires_at) if self.expires_at else None,
        }

    @property
    def manifest_id(self):
        return identity("manifest", self.to_dict())


class CursorCodec:
    def __init__(self, secret):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise DomainError("invalid_argument", "Cursor signing secret must have >= 32 bytes")
        self._secret = secret

    @staticmethod
    def _binding(binding):
        required = {"tenant_id", "rights_version", "filters", "sort", "source", "manifest_id"}
        if not isinstance(binding, dict) or set(binding) != required:
            raise DomainError("invalid_argument", "Cursor binding must include all scope dimensions")
        for key in ("tenant_id", "rights_version", "source"):
            text(binding[key], key)
        if not isinstance(binding["filters"], dict) or not isinstance(binding["sort"], list) or not binding["sort"]:
            raise DomainError("invalid_argument", "Invalid cursor filter/sort binding")
        # Null manifest explicitly means ordinary pagination, not a frozen snapshot.
        if binding["manifest_id"] is not None:
            text(binding["manifest_id"], "manifest_id")
        return hashlib.sha256(canonical_json(binding).encode()).hexdigest()

    def encode(self, *, binding, position, expires_at, now):
        if parse_time(expires_at) <= parse_time(now):
            raise DomainError("expired", "Cursor expiry is in the past", 410)
        if not isinstance(position, list) or not position or len(position) > 10:
            raise DomainError("invalid_argument", "Cursor position must be a bounded tuple")
        raw = canonical_json({
            "version": 1, "binding": self._binding(binding), "position": position,
            "expires_at": utc_text(expires_at),
        }).encode()
        token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        result = token + "." + hmac.new(self._secret, token.encode(), hashlib.sha256).hexdigest()
        if len(result) > 8192:
            raise DomainError("invalid_argument", "Cursor is too large")
        return result

    def decode(self, token, *, binding, now):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            raw, signature = token.split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", raw) or not re.fullmatch(r"[0-9a-f]{64}", signature):
                raise ValueError()
            expected = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            data = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True))
            if (data.get("version") != 1 or data.get("binding") != self._binding(binding)
                    or set(data) != {"version", "binding", "position", "expires_at"}
                    or not isinstance(data["position"], list) or not 1 <= len(data["position"]) <= 10):
                raise ValueError()
            if parse_time(now) >= parse_time(data["expires_at"]):
                raise DomainError("expired", "Cursor has expired", 410)
            return data["position"]
        except DomainError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, AttributeError):
            raise DomainError("invalid_argument", "Cursor is invalid or its scope changed") from None
