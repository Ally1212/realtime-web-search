"""Paused, key-scoped change subscriptions without outbound delivery."""
from __future__ import annotations

import re
from datetime import timedelta

from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError,
    canonical_json,
    identity,
    parse_time,
    plain_json,
    text,
    utc_text,
)
from .google_evidence import page_limit
from .google_rights import authorize

SUBSCRIPTION_VERSION = "google-subscriptions-1"
FIELD = "change_subscription"
EVENT_TYPES = frozenset({
    "rank_change", "new_url", "news_event", "ai_citation_change",
})
FILTER_KEYS = frozenset({
    "source", "source_version", "target_domains", "queries",
    "market", "language",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_subscriptions (
 tenant_id text NOT NULL, subscription_id text NOT NULL,
 idempotency_key text NOT NULL, request_hash text NOT NULL,
 subject_ref text NOT NULL, name text NOT NULL, target_ref text NOT NULL,
 event_types jsonb NOT NULL, filters jsonb NOT NULL,
 version integer NOT NULL, active boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 deleted_at timestamptz,
 PRIMARY KEY(tenant_id,subscription_id),
 UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS sf_google_subscriptions_tenant
 ON sf_google_subscriptions(tenant_id,created_at);
"""


class SubscriptionService:
    def __init__(self, dsn, *, registry, cursor_codec, clock):
        self.dsn = dsn
        self.registry = registry
        self.cursor_codec = cursor_codec
        self.clock = clock

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,11)")
            db.execute(SCHEMA)

    @staticmethod
    def route(path):
        if path in {"/subscriptions", "/subscriptions/"}:
            return "list", None
        match = re.fullmatch(r"/subscriptions/([^/]+)", path)
        if match:
            return "detail", match[1]
        raise DomainError(
            "not_found", "No subscription route matches", 404)

    @staticmethod
    def _etag(row):
        return identity("subscription-version", {
            "subscription_id": row["subscription_id"],
            "version": row["version"],
        })

    def _public(self, row, *, now):
        deleted = row["deleted_at"] is not None
        return {
            "subscription_version": SUBSCRIPTION_VERSION,
            "subscription_id": row["subscription_id"],
            "name": row["name"],
            "target_ref": row["target_ref"],
            "event_types": plain_json(row["event_types"]),
            "filters": plain_json(row["filters"]),
            "version": row["version"],
            "etag": self._etag(row),
            "active": bool(row["active"]),
            "state": "deleted" if deleted else
                     "configured_paused" if not row["active"] else
                     "configured_no_delivery_connector",
            "created_at": utc_text(row["created_at"]),
            "updated_at": utc_text(row["updated_at"]),
            "deleted_at": (
                utc_text(row["deleted_at"])
                if row["deleted_at"] else None),
            "delivery": {
                "status": "not_connected",
                "reason": "approved_outbound_target_and_signing_connector_required",
                "at_least_once_required": True,
                "event_id_required": True,
                "signature": "not_connected",
                "replay_window_seconds": 300,
                "max_backoff_seconds": 86400,
                "outbound_send_enabled": False,
            },
            "raw_content_change": "not_covered_use_whale_changes",
        }

    @staticmethod
    def _validate_filters(value, *, now):
        if not isinstance(value, dict) or set(value) - FILTER_KEYS:
            raise DomainError(
                "invalid_argument",
                "Subscription filters contain unsupported fields",
            )
        canonical_json(value)
        source, version = value.get("source"), value.get("source_version")
        if not source or not version:
            raise DomainError(
                "invalid_argument",
                "Subscription filters require source and source_version",
            )
        text(source, "source", maximum=128)
        text(version, "source_version", maximum=128)
        for key in ("target_domains", "queries"):
            rows = value.get(key)
            if rows is None:
                continue
            if (
                    not isinstance(rows, list)
                    or len(rows) > 1000
                    or len(rows) != len(set(rows))
                    or any(not isinstance(item, str) or not item for item in rows)
            ):
                raise DomainError(
                    "invalid_argument",
                    f"{key} must contain unique nonempty strings",
                )
        return value

    def _source(self, principal, filters, *, now):
        source = self.registry.source(
            principal.tenant_id,
            filters["source"],
            filters["source_version"],
        )
        grants = [
            self.registry.grant(principal.tenant_id, grant_id)
            for grant_id in principal.grant_ids
        ]
        matching = [
            grant for grant in grants
            if (
                grant.source_adapter == source.adapter_id
                and grant.source_version == source.adapter_version
                and FIELD in grant.fields
            )
        ]
        if not matching:
            raise DomainError(
                "not_authorized",
                "No asset grant covers change subscriptions for this source",
                403,
            )
        if "reader" not in principal.roles:
            raise DomainError(
                "use_not_permitted",
                "The reader role is required for subscriptions",
                403,
            )
        for grant in matching:
            authorize(
                principal, grant, source, FIELD, "internal_display",
                now=now, production=True,
            )
        grant = matching[0]
        deadline = grant.expires_at
        for right in source.field_rights:
            if (
                    right.field == FIELD
                    and right.retention_deadline is not None
            ):
                deadline = min(deadline, right.retention_deadline)
        return source, grant, deadline

    def create(self, principal, body, *, idempotency_key, now):
        text(idempotency_key, "Idempotency-Key", maximum=120)
        required = {"name", "target_ref", "event_types", "filters"}
        allowed = required | {"active"}
        if not isinstance(body, dict) or set(body) - allowed or required - set(body):
            raise DomainError(
                "invalid_argument",
                "Subscription body is incomplete or contains unsupported fields",
            )
        name = body["name"]
        target_ref = body["target_ref"]
        text(name, "name", maximum=500)
        text(target_ref, "target_ref", maximum=4096)
        event_types = body["event_types"]
        if (
                not isinstance(event_types, list)
                or not event_types
                or len(event_types) > len(EVENT_TYPES)
                or len(event_types) != len(set(event_types))
                or set(event_types) - EVENT_TYPES
        ):
            raise DomainError(
                "invalid_argument",
                "event_types must be unique approved event types",
            )
        filters = self._validate_filters(body["filters"], now=now)
        active = body.get("active", False)
        if type(active) is not bool:
            raise DomainError(
                "invalid_argument", "active must be boolean")
        if active:
            raise DomainError(
                "not_covered",
                "Outbound subscription delivery is not connected",
                501,
            )
        source, grant, deadline = self._source(
            principal, filters, now=now)
        request_hash = identity("subscription-request", {
            "name": name,
            "target_ref": target_ref,
            "event_types": event_types,
            "filters": filters,
            "active": active,
        })
        with self.registry.connect() as db:
            existing = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency key was used for another subscription",
                    409,
                )
            return self.detail(
                principal, existing["subscription_id"], now=now)
        if deadline <= parse_time(now):
            raise DomainError(
                "expired",
                "Subscription source rights have expired",
                410,
            )
        subscription_id = identity("subscription", {
            "tenant_id": principal.tenant_id,
            "idempotency_key": idempotency_key,
        })
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            conflict = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
            if conflict:
                if conflict["request_hash"] != request_hash:
                    raise DomainError(
                        "idempotency_conflict",
                        "Idempotency key was used for another subscription",
                        409,
                    )
                return self._public(conflict, now=now)
            db.execute(
                """INSERT INTO sf_google_subscriptions
                   (tenant_id,subscription_id,idempotency_key,request_hash,
                    subject_ref,name,target_ref,event_types,filters,version,
                    active,created_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,false,%s,%s)""",
                (
                    principal.tenant_id,
                    subscription_id,
                    idempotency_key,
                    request_hash,
                    principal.subject_ref,
                    name,
                    target_ref,
                    Jsonb(event_types),
                    Jsonb(filters),
                    parse_time(now),
                    parse_time(now),
                ),
            )
            row = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND subscription_id=%s""",
                (principal.tenant_id, subscription_id),
            ).fetchone()
        return self._public(row, now=now)

    def detail(self, principal, subscription_id, *, now):
        text(subscription_id, "subscription_id")
        with self.registry.connect() as db:
            row = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND subscription_id=%s""",
                (principal.tenant_id, subscription_id),
            ).fetchone()
        if not row:
            raise DomainError(
                "not_found", "No subscription with this ID", 404)
        if (
                row["subject_ref"] != principal.subject_ref
                and "manager" not in principal.roles
        ):
            raise DomainError(
                "not_authorized",
                "Subscription belongs to another key",
                403,
            )
        self._source(
            principal,
            plain_json(row["filters"]),
            now=now,
        )
        return self._public(row, now=now)

    def list(self, principal, parameters, *, now):
        if set(parameters) - {"limit", "cursor"}:
            raise DomainError(
                "invalid_argument",
                "Unknown subscription list parameter",
            )
        limit = page_limit(parameters.get("limit"))
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                "limit": limit,
                "subject": principal.subject_ref,
            },
            "sort": ["created_at", "subscription_id"],
            "source": SUBSCRIPTION_VERSION,
            "manifest_id": None,
        }
        after = None
        if "cursor" in parameters:
            after = self.cursor_codec.decode(
                parameters["cursor"], binding=binding, now=now)
        with self.registry.connect() as db:
            rows = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND subject_ref=%s
                   ORDER BY created_at DESC,subscription_id DESC""",
                (principal.tenant_id, principal.subject_ref),
            ).fetchall()
        if after is not None:
            rows = [
                row for row in rows
                if (
                    utc_text(row["created_at"]),
                    row["subscription_id"],
                ) < tuple(after)
            ]
        page, more = rows[:limit], len(rows) > limit
        cursor = None
        if more:
            last = page[-1]
            cursor = self.cursor_codec.encode(
                binding=binding,
                position=[
                    utc_text(last["created_at"]),
                    last["subscription_id"],
                ],
                expires_at=parse_time(now) + timedelta(minutes=10),
                now=now,
            )
        return {
            "subscriptions": [self._public(row, now=now) for row in page],
            "next_cursor": cursor,
            "delivery_state": "not_connected",
        }

    def delete(self, principal, subscription_id, *, now):
        text(subscription_id, "subscription_id")
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND subscription_id=%s FOR UPDATE""",
                (principal.tenant_id, subscription_id),
            ).fetchone()
            if not row:
                raise DomainError(
                    "not_found", "No subscription with this ID", 404)
            if (
                    row["subject_ref"] != principal.subject_ref
                    and "manager" not in principal.roles
            ):
                raise DomainError(
                    "not_authorized",
                    "Subscription belongs to another key",
                    403,
                )
            if row["deleted_at"] is None:
                db.execute(
                    """UPDATE sf_google_subscriptions
                       SET active=false,deleted_at=%s,updated_at=%s,
                           version=version+1
                       WHERE tenant_id=%s AND subscription_id=%s""",
                    (
                        parse_time(now),
                        parse_time(now),
                        principal.tenant_id,
                        subscription_id,
                    ),
                )
            row = db.execute(
                """SELECT * FROM sf_google_subscriptions
                   WHERE tenant_id=%s AND subscription_id=%s""",
                (principal.tenant_id, subscription_id),
            ).fetchone()
        return self._public(row, now=now)
