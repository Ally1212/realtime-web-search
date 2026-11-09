"""Versioned source/asset/delivery rights registry; no credentials or raw facts.

Writers are internal control-plane operations. They require explicit actor and
CAS revisions, and are deliberately not exposed as customer API writes.
Initialization touches only the two independent sf_google_* registry tables.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import fields

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import DomainError, canonical_json, identity, parse_time, plain_json, text, utc_text
from .google_rights import FieldRight, Grant, IngestPermit, Principal, SourceRegistration

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_tenant_rights (
 tenant_id text PRIMARY KEY, epoch bigint NOT NULL DEFAULT 0 CHECK(epoch>=0)
);
CREATE TABLE IF NOT EXISTS sf_google_registry (
 tenant_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('source','grant','permit','subject')),
 record_key text NOT NULL, revision integer NOT NULL CHECK(revision>=1),
 is_current boolean NOT NULL, payload jsonb NOT NULL,
 actor_ref text NOT NULL, changed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,kind,record_key,revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS sf_google_registry_head
 ON sf_google_registry(tenant_id,kind,record_key) WHERE is_current;
"""


def source_payload(source):
    result = {f.name: plain_json(getattr(source, f.name)) for f in fields(source)
              if f.name != "field_rights"}
    result["field_rights"] = [
        dict(field=r.field, allowed_uses=sorted(r.allowed_uses),
             retention_deadline=utc_text(r.retention_deadline) if r.retention_deadline else None,
             no_fixed_expiry_approved=r.no_fixed_expiry_approved)
        for r in source.field_rights
    ]
    return result


def source_from_payload(payload):
    payload = dict(payload)
    payload["field_rights"] = tuple(FieldRight(
        field=r["field"], allowed_uses=frozenset(r["allowed_uses"]),
        retention_deadline=r["retention_deadline"],
        no_fixed_expiry_approved=r["no_fixed_expiry_approved"]) for r in payload["field_rights"])
    return SourceRegistration(**payload)


def _payload(value):
    if isinstance(value, Mapping):
        return plain_json(value)
    if isinstance(value, SourceRegistration):
        return source_payload(value)
    result = {}
    for f in fields(value):
        item = getattr(value, f.name)
        result[f.name] = (sorted(item) if isinstance(item, frozenset) else
                          utc_text(item) if f.name == "expires_at" else item)
    return result


class RightsRegistry:
    def __init__(self, dsn):
        self.dsn = dsn
        self._connections = threading.BoundedSemaphore(2)

    @contextmanager
    def connect(self):
        with self._connections:
            with psycopg.connect(self.dsn, row_factory=dict_row) as db:
                yield db

    def initialize(self):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,3)")
            db.execute(SCHEMA)

    @staticmethod
    def source_key(adapter_id, version):
        text(adapter_id, "source adapter")
        text(version, "source version")
        return identity("registry-source", [adapter_id, version])

    def _write(self, tenant_id, kind, key, value, *, expected_revision, actor_ref, now):
        text(tenant_id, "tenant_id")
        text(actor_ref, "actor_ref", maximum=256)
        if type(expected_revision) is not int or expected_revision < 0:
            raise DomainError("invalid_argument", "An explicit nonnegative CAS revision is required")
        body = _payload(value)
        raw = canonical_json(body)
        if len(raw.encode()) > 65536:
            raise DomainError("invalid_argument", "Registry record exceeds 64 KiB")
        with self.connect() as db:
            db.execute("INSERT INTO sf_google_tenant_rights(tenant_id) VALUES(%s) ON CONFLICT DO NOTHING",
                       (tenant_id,))
            db.execute("SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR UPDATE",
                       (tenant_id,))
            row = db.execute("""
                SELECT revision FROM sf_google_registry
                WHERE tenant_id=%s AND kind=%s AND record_key=%s AND is_current
            """, (tenant_id, kind, key)).fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise DomainError("precondition_failed", "Registry revision changed; reload before writing", 412)
            db.execute("""
                UPDATE sf_google_registry SET is_current=false
                WHERE tenant_id=%s AND kind=%s AND record_key=%s AND is_current
            """, (tenant_id, kind, key))
            db.execute("""
                INSERT INTO sf_google_registry(tenant_id,kind,record_key,revision,is_current,
                 payload,actor_ref,changed_at) VALUES(%s,%s,%s,%s,true,%s,%s,%s)
            """, (tenant_id, kind, key, revision + 1, Jsonb(json.loads(raw)), actor_ref, parse_time(now)))
            epoch = db.execute("""
                UPDATE sf_google_tenant_rights SET epoch=epoch+1 WHERE tenant_id=%s RETURNING epoch
            """, (tenant_id,)).fetchone()["epoch"]
        return {"key": key, "revision": revision + 1, "rights_version": str(epoch)}

    def _read(self, tenant_id, kind, key, *, db=None):
        if db is None:
            with self.connect() as connection:
                return self._read(tenant_id, kind, key, db=connection)
        row = db.execute("""
            SELECT payload FROM sf_google_registry
            WHERE tenant_id=%s AND kind=%s AND record_key=%s AND is_current
        """, (tenant_id, kind, key)).fetchone()
        if not row:
            raise DomainError("not_covered" if kind == "source" else "not_authorized",
                              "Registry scope is not available", 404)
        return row["payload"]

    def save_source(self, tenant_id, source, *, expected_revision, actor_ref, now):
        if not isinstance(source, SourceRegistration):
            raise DomainError("invalid_argument", "Expected validated source registration")
        key = self.source_key(source.adapter_id, source.adapter_version)
        return self._write(tenant_id, "source", key, source, expected_revision=expected_revision,
                           actor_ref=actor_ref, now=now)

    def source(self, tenant_id, adapter_id, version, *, db=None):
        return source_from_payload(self._read(tenant_id, "source", self.source_key(adapter_id, version), db=db))

    def sources(self, tenant_id, *, now):
        with self.connect() as db:
            rows = db.execute("""
                SELECT payload,revision FROM sf_google_registry
                WHERE tenant_id=%s AND kind='source' AND is_current ORDER BY record_key
            """, (tenant_id,)).fetchall()
        return [dict(source_from_payload(r["payload"]).capability(now=now), revision=r["revision"])
                for r in rows]

    def save_grant(self, grant, *, expected_revision, actor_ref, now):
        if not isinstance(grant, Grant) or not grant.source_adapter or not grant.source_version:
            raise DomainError("invalid_argument", "Grant must bind an actual source version")
        for name in ("tenant_id", "asset_id", "grant_id", "version"):
            text(getattr(grant, name), name)
        source = self.source(grant.tenant_id, grant.source_adapter, grant.source_version)
        available = {r.field: r for r in source.field_rights}
        if not grant.fields or any(
                name not in available or not grant.uses <= available[name].allowed_uses
                for name in grant.fields):
            raise DomainError("use_not_permitted", "Grant fields/uses exceed registered source rights", 403)
        return self._write(grant.tenant_id, "grant", grant.grant_id, grant,
                           expected_revision=expected_revision, actor_ref=actor_ref, now=now)

    def grant(self, tenant_id, grant_id, *, db=None):
        return Grant(**self._read(tenant_id, "grant", grant_id, db=db))

    def save_permit(self, permit, *, expected_revision, actor_ref, now):
        if not isinstance(permit, IngestPermit):
            raise DomainError("invalid_argument", "Expected validated ingest permit")
        grant = self.grant(permit.tenant_id, permit.grant_id)
        source = self.source(permit.tenant_id, permit.source_adapter, permit.source_version)
        if (grant.asset_id != permit.asset_id or grant.source_adapter != permit.source_adapter
                or grant.source_version != permit.source_version
                or permit.production_approved and not source.production_approved):
            raise DomainError("not_authorized", "Permit scope exceeds source/asset admission", 403)
        for cap in permit.capabilities - {"identity"}:
            if {"body": "body_text"}.get(cap, cap) not in grant.fields:
                raise DomainError("use_not_permitted", "Permit capability has no licensed grant field", 403)
        return self._write(permit.tenant_id, "permit", permit.permit_id, permit,
                           expected_revision=expected_revision, actor_ref=actor_ref, now=now)

    def permit(self, tenant_id, permit_id):
        return IngestPermit(**self._read(tenant_id, "permit", permit_id))

    def save_ads_access(self, policy, *, expected_revision, actor_ref, now):
        from .google_ads import AdsAccessPolicy

        if not isinstance(policy, AdsAccessPolicy):
            raise DomainError("invalid_argument", "Expected a typed managed Ads policy")
        source = self.source(
            policy.binding.tenant_id, policy.source_adapter, policy.source_version)
        if policy.binding.source_api_version != source.source_api_version:
            raise DomainError("incompatible_scope", "Ads policy/source API mismatch", 409)
        return self._write(
            policy.binding.tenant_id, "permit", "ads-access:" + policy.access_id,
            {"type": "managed_ads_access", "policy": policy.to_dict()},
            expected_revision=expected_revision, actor_ref=actor_ref, now=now)

    def ads_access(self, tenant_id, access_id):
        from .google_ads import AdsAccessPolicy

        text(access_id, "access_id", maximum=256)
        value = self._read(tenant_id, "permit", "ads-access:" + access_id)
        if value.get("type") != "managed_ads_access":
            raise DomainError("use_not_permitted", "No managed Ads access policy", 403)
        return AdsAccessPolicy.from_record(value.get("policy"))

    def save_merchant_access(
            self, tenant_id, policy, *, expected_revision, actor_ref, now):
        from .google_merchant import MerchantAccessPolicy

        if not isinstance(policy, MerchantAccessPolicy):
            raise DomainError("invalid_argument", "Expected a typed Merchant policy")
        source = self.source(
            tenant_id, policy.source_adapter, policy.source_version)
        return self._write(
            tenant_id, "permit", "merchant-access:" + policy.access_id,
            {"type": "managed_merchant_access", "policy": policy.to_dict()},
            expected_revision=expected_revision, actor_ref=actor_ref, now=now)

    def merchant_access(self, tenant_id, access_id):
        from .google_merchant import MerchantAccessPolicy

        text(access_id, "access_id", maximum=256)
        value = self._read(
            tenant_id, "permit", "merchant-access:" + access_id)
        if value.get("type") != "managed_merchant_access":
            raise DomainError(
                "use_not_permitted", "No Merchant access policy", 403)
        return MerchantAccessPolicy.from_record(value.get("policy"))

    def save_subject(self, tenant_id, subject_ref, *, grant_ids, roles,
                     expected_revision, actor_ref, now):
        text(subject_ref, "subject_ref")
        grants, roles = frozenset(grant_ids), frozenset(roles)
        if roles - {"reader", "manager"}:
            raise DomainError("invalid_argument", "Unknown domain role")
        for gid in grants:
            self.grant(tenant_id, gid)
        body = dict(subject_ref=subject_ref, grant_ids=sorted(grants), roles=sorted(roles))
        return self._write(tenant_id, "subject", subject_ref, body,
                           expected_revision=expected_revision, actor_ref=actor_ref, now=now)

    def principal(self, tenant_id, *, now, subject_ref=None, db=None):
        text(tenant_id, "tenant_id")
        if db is None:
            with self.connect() as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return self.principal(tenant_id, now=now, subject_ref=subject_ref, db=connection)
        # Standalone reads use a snapshot; caller-owned transactions hold the
        # tenant-rights row and reuse this connection to avoid nested-pool deadlock.
        state = db.execute("SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s",
                           (tenant_id,)).fetchone()
        rows = db.execute("""
            SELECT payload FROM sf_google_registry
            WHERE tenant_id=%s AND kind='grant' AND is_current
        """, (tenant_id,)).fetchall()
        subject = (db.execute("""
            SELECT payload FROM sf_google_registry
            WHERE tenant_id=%s AND kind='subject' AND record_key=%s AND is_current
        """, (tenant_id, subject_ref)).fetchone() if subject_ref is not None else None)
        grants = [Grant(**r["payload"]) for r in rows]
        if subject_ref is not None:
            permitted = set(subject["payload"]["grant_ids"]) if subject else set()
            grants = [g for g in grants if g.grant_id in permitted]
        versions = {g.grant_id: g.version for g in grants
                    if not g.revoked and g.expires_at > parse_time(now)}
        return Principal(tenant_id, frozenset(versions), str(state["epoch"] if state else 0),
                         grant_versions=versions, subject_ref=subject_ref,
                         roles=frozenset(subject["payload"]["roles"]) if subject else
                         frozenset() if subject_ref is not None else frozenset({"reader"}))

    def assert_current(self, principal, *, now, db=None):
        current = self.principal(principal.tenant_id, now=now, subject_ref=principal.subject_ref, db=db)
        if (current.rights_version != principal.rights_version
                or current.grant_ids != principal.grant_ids
                or current.grant_versions != principal.grant_versions
                or current.roles != principal.roles):
            raise DomainError("not_authorized", "Rights snapshot changed; authenticate the request again", 403)
        return current

    def history(self, tenant_id, kind, key):
        if kind not in {"source", "grant", "permit", "subject"}:
            raise DomainError("invalid_argument", "Unknown registry kind")
        with self.connect() as db:
            return db.execute("""
                SELECT revision,actor_ref,changed_at,payload FROM sf_google_registry
                WHERE tenant_id=%s AND kind=%s AND record_key=%s ORDER BY revision
            """, (tenant_id, kind, key)).fetchall()
