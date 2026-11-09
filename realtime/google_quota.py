"""Local atomic quota buckets for independent Google source domains.

Limits are configured by the control plane and keyed by service/project/
property/CID/vendor-right scopes. Account references are audit labels only, so
rotating accounts cannot create an independent allowance.
"""
from __future__ import annotations

import threading
import hashlib
from contextlib import contextmanager
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, decimal, identity, parse_time, text, utc_text,
)

RESOURCE_KINDS = frozenset({
    "service", "cloud_project", "property", "cid", "vendor_right",
})
MAX_WINDOW_SECONDS = 31 * 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_quota_limits (
 tenant_id text NOT NULL, source_adapter text NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN
   ('service','cloud_project','property','cid','vendor_right')),
 resource_ref text NOT NULL, window_seconds integer NOT NULL CHECK(window_seconds BETWEEN 1 AND 2678400),
 limit_units bigint NOT NULL CHECK(limit_units>=0), updated_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,source_adapter,resource_kind,resource_ref)
);
CREATE TABLE IF NOT EXISTS sf_google_quota_buckets (
 tenant_id text NOT NULL, source_adapter text NOT NULL,
 resource_kind text NOT NULL, resource_ref text NOT NULL,
 window_started_at timestamptz NOT NULL, used_units bigint NOT NULL CHECK(used_units>=0),
 PRIMARY KEY(tenant_id,source_adapter,resource_kind,resource_ref)
);
CREATE TABLE IF NOT EXISTS sf_google_quota_reservations (
 tenant_id text NOT NULL, source_adapter text NOT NULL, request_id text NOT NULL,
 account_ref text NOT NULL, payload_hash char(64) NOT NULL,
 reservation_id text NOT NULL, units bigint NOT NULL CHECK(units>=0),
 reserved_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,source_adapter,request_id)
);
"""


def _scope(value):
    if not isinstance(value, dict):
        raise DomainError("invalid_argument", "Quota resource must be an object")
    if set(value) != {"kind", "ref", "cost"}:
        raise DomainError("invalid_argument", "Quota resource fields are invalid")
    kind, ref = value["kind"], value["ref"]
    if kind not in RESOURCE_KINDS:
        raise DomainError("invalid_argument", "Unknown quota resource kind")
    text(ref, "resource ref", maximum=256)
    cost = decimal(value["cost"])
    if cost < 0 or cost != cost.to_integral_value():
        raise DomainError("invalid_argument", "Quota cost must be a nonnegative integer")
    return {"kind": kind, "ref": ref, "cost": int(cost)}


class SourceQuotaScheduler:
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
            db.execute("SELECT pg_advisory_xact_lock(20261104,4)")
            db.execute(SCHEMA)

    def set_limit(self, *, tenant_id, source_adapter, resource_kind, resource_ref,
                  window_seconds, limit_units, now):
        text(tenant_id, "tenant_id")
        text(source_adapter, "source_adapter")
        if resource_kind not in RESOURCE_KINDS:
            raise DomainError("invalid_argument", "Unknown quota resource kind")
        text(resource_ref, "resource_ref", maximum=256)
        if (
                type(window_seconds) is not int
                or not 1 <= window_seconds <= MAX_WINDOW_SECONDS
                or type(limit_units) is not int
                or limit_units < 0
        ):
            raise DomainError("invalid_argument", "Quota limit/window is invalid")
        with self.connect() as db:
            db.execute("""
                INSERT INTO sf_google_quota_limits
                (tenant_id,source_adapter,resource_kind,resource_ref,window_seconds,
                 limit_units,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,source_adapter,resource_kind,resource_ref)
                DO UPDATE SET window_seconds=EXCLUDED.window_seconds,
                 limit_units=EXCLUDED.limit_units,updated_at=EXCLUDED.updated_at
            """, (
                tenant_id, source_adapter, resource_kind, resource_ref,
                window_seconds, limit_units, parse_time(now),
            ))

    def consume_external(self, *, tenant_id, source_adapter, resource_kind,
                         resource_ref, units, now):
        """Record another consumer of a shared property/project/CID bucket."""
        if resource_kind not in RESOURCE_KINDS:
            raise DomainError("invalid_argument", "Unknown quota resource kind")
        text(resource_ref, "resource_ref", maximum=256)
        if type(units) is not int or units < 0:
            raise DomainError("invalid_argument", "Quota units must be nonnegative")
        current = parse_time(now)
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,5)")
            limit = db.execute("""
                SELECT window_seconds FROM sf_google_quota_limits
                WHERE tenant_id=%s AND source_adapter=%s AND resource_kind=%s
                  AND resource_ref=%s
            """, (
                tenant_id, source_adapter, resource_kind, resource_ref,
            )).fetchone()
            if not limit:
                raise DomainError("not_covered", "Quota resource has no configured limit", 404)
            bucket = db.execute("""
                SELECT * FROM sf_google_quota_buckets
                WHERE tenant_id=%s AND source_adapter=%s AND resource_kind=%s
                  AND resource_ref=%s FOR UPDATE
            """, (
                tenant_id, source_adapter, resource_kind, resource_ref,
            )).fetchone()
            if (
                    bucket is None
                    or current >= bucket["window_started_at"] + timedelta(
                        seconds=limit["window_seconds"])
            ):
                used, started = units, current
                db.execute("""
                    INSERT INTO sf_google_quota_buckets
                    (tenant_id,source_adapter,resource_kind,resource_ref,
                     window_started_at,used_units) VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,source_adapter,resource_kind,resource_ref)
                    DO UPDATE SET window_started_at=EXCLUDED.window_started_at,
                     used_units=EXCLUDED.used_units
                """, (
                    tenant_id, source_adapter, resource_kind, resource_ref,
                    started, used,
                ))
            else:
                used, started = bucket["used_units"] + units, bucket["window_started_at"]
                db.execute("""
                    UPDATE sf_google_quota_buckets SET used_units=%s
                    WHERE tenant_id=%s AND source_adapter=%s
                      AND resource_kind=%s AND resource_ref=%s
                """, (
                    used, tenant_id, source_adapter, resource_kind, resource_ref,
                ))
        return {
            "resource_kind": resource_kind,
            "resource_ref": resource_ref,
            "window_started_at": utc_text(started),
            "used_units": used,
        }

    def reserve(self, *, tenant_id, source_adapter, request_id, account_ref,
                resources, now):
        text(tenant_id, "tenant_id")
        text(source_adapter, "source_adapter")
        text(request_id, "request_id", maximum=256)
        text(account_ref, "account_ref", maximum=256)
        checked = tuple(_scope(item) for item in resources)
        if not checked:
            raise DomainError("invalid_argument", "At least one quota resource is required")
        keys = {(item["kind"], item["ref"]) for item in checked}
        if len(keys) != len(checked):
            raise DomainError("invalid_argument", "Quota resources must be unique")
        payload = {
            "account_ref": account_ref,
            "resources": sorted(checked, key=lambda item: (item["kind"], item["ref"])),
        }
        payload_hash = hashlib.sha256(
            canonical_json(payload).encode()).hexdigest()
        units = sum(item["cost"] for item in checked)
        current = parse_time(now)
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,5)")
            existing = db.execute("""
                SELECT * FROM sf_google_quota_reservations
                WHERE tenant_id=%s AND source_adapter=%s AND request_id=%s
            """, (tenant_id, source_adapter, request_id)).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise DomainError(
                        "idempotency_conflict", "Quota request identity changed", 409)
                return self._reservation(existing)

            limits = {}
            for item in checked:
                limit = db.execute("""
                    SELECT * FROM sf_google_quota_limits
                    WHERE tenant_id=%s AND source_adapter=%s
                      AND resource_kind=%s AND resource_ref=%s
                    FOR UPDATE
                """, (
                    tenant_id, source_adapter, item["kind"], item["ref"],
                )).fetchone()
                if not limit:
                    raise DomainError(
                        "not_covered",
                        "Quota resource has no configured limit",
                        404,
                    )
                limits[(item["kind"], item["ref"])] = limit

            denied = []
            projected = []
            for item in sorted(checked, key=lambda value: (value["kind"], value["ref"])):
                key = (item["kind"], item["ref"])
                limit = limits[key]
                bucket = db.execute("""
                    SELECT * FROM sf_google_quota_buckets
                    WHERE tenant_id=%s AND source_adapter=%s
                      AND resource_kind=%s AND resource_ref=%s FOR UPDATE
                """, (tenant_id, source_adapter, *key)).fetchone()
                if (
                        bucket is None
                        or current >= bucket["window_started_at"] + timedelta(
                            seconds=limit["window_seconds"])
                ):
                    used, started = 0, current
                else:
                    used, started = bucket["used_units"], bucket["window_started_at"]
                if used + item["cost"] > limit["limit_units"]:
                    retry = max(
                        1,
                        int((
                            started + timedelta(seconds=limit["window_seconds"])
                            - current
                        ).total_seconds()),
                    )
                    denied.append({
                        "resource_kind": item["kind"],
                        "resource_ref": item["ref"],
                        "retry_after_seconds": retry,
                    })
                projected.append((item, used, started))
            if denied:
                error = DomainError(
                    "quota_exceeded", "One or more source quota buckets are exhausted", 429)
                error.retry_after = min(
                    item["retry_after_seconds"] for item in denied)
                error.resources = denied
                raise error

            for item, used, started in projected:
                db.execute("""
                    INSERT INTO sf_google_quota_buckets
                    (tenant_id,source_adapter,resource_kind,resource_ref,
                     window_started_at,used_units) VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,source_adapter,resource_kind,resource_ref)
                    DO UPDATE SET window_started_at=EXCLUDED.window_started_at,
                     used_units=EXCLUDED.used_units
                """, (
                    tenant_id, source_adapter, item["kind"], item["ref"],
                    started, used + item["cost"],
                ))
            reservation = {
                "tenant_id": tenant_id,
                "source_adapter": source_adapter,
                "request_id": request_id,
                "reservation_id": identity("quota-reservation", {
                    "tenant_id": tenant_id,
                    "source_adapter": source_adapter,
                    "request_id": request_id,
                }),
                "payload_hash": payload_hash,
                "account_ref": account_ref,
                "units": units,
                "reserved_at": utc_text(current),
            }
            db.execute("""
                INSERT INTO sf_google_quota_reservations
                (tenant_id,source_adapter,request_id,account_ref,payload_hash,
                 reservation_id,units,reserved_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                tenant_id, source_adapter, request_id, account_ref, payload_hash,
                reservation["reservation_id"], units, current,
            ))
            return reservation

    @staticmethod
    def retry_delay(*, error_code, retry_after, attempt, jitter_seconds):
        if error_code not in {"http_429", "http_503"}:
            raise DomainError("invalid_argument", "Only 429/503 are retryable")
        retry = decimal(retry_after)
        jitter = decimal(jitter_seconds)
        if (
                retry < 0 or retry > 86400
                or jitter < 0 or jitter > 60
                or type(attempt) is not int or attempt < 1
        ):
            raise DomainError("invalid_argument", "Invalid retry delay/jitter/attempt")
        exponential = min(3600, 2 ** min(attempt - 1, 12))
        return max(retry, decimal(exponential)) + jitter

    @staticmethod
    def _reservation(row):
        return {
            "tenant_id": row["tenant_id"],
            "source_adapter": row["source_adapter"],
            "request_id": row["request_id"],
            "reservation_id": row["reservation_id"],
            "payload_hash": row["payload_hash"],
            "account_ref": row["account_ref"],
            "units": row["units"],
            "reserved_at": utc_text(row["reserved_at"]),
        }
