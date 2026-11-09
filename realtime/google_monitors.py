"""Versioned monitor configuration only; this module never schedules collection."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, decimal, identity, parse_time, plain_json,
    text, utc_text,
)
from .google_evidence import page_limit

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_monitors (
 tenant_id text NOT NULL, monitor_id text NOT NULL, idempotency_key text NOT NULL,
 request_hash text NOT NULL, subject_ref text NOT NULL, name text NOT NULL,
 source_adapter text NOT NULL, source_version text NOT NULL,
 version integer NOT NULL DEFAULT 1, config jsonb NOT NULL,
 enabled boolean NOT NULL DEFAULT false, scheduler_state text NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,monitor_id), UNIQUE(tenant_id,idempotency_key)
);
"""

FREQUENCY_MIN = 300
FREQUENCY_MAX = 30 * 24 * 60 * 60
LIST_FIELDS = ("queries", "prompts", "domains", "pools")


def _now():
    return datetime.now(timezone.utc)


def _if_match(value):
    if not isinstance(value, str):
        raise DomainError("invalid_argument", "If-Match is required for monitor updates")
    match = re.fullmatch(r'(?:W/)?"?([1-9][0-9]{0,8})"?', value.strip())
    if not match:
        raise DomainError("invalid_argument", "If-Match must be a monitor version ETag")
    return int(match.group(1))


class MonitorService:
    def __init__(self, dsn, *, registry, cursor_codec=None, clock=None):
        self.dsn, self.registry = dsn, registry
        self.cursor_codec = cursor_codec
        self.clock = clock or _now

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,12)")
            db.execute(SCHEMA)

    @staticmethod
    def route(path):
        if path in {"/monitors", "/monitors/"}:
            return "list", None
        match = re.fullmatch(r"/monitors/([^/]+)", path)
        if match:
            return "detail", unquote(match[1], errors="strict")
        raise DomainError("not_found", "No monitor route matches", 404)

    @staticmethod
    def _clean_text_list(value, name):
        if not isinstance(value, list) or len(value) > 1000:
            raise DomainError(
                "invalid_argument", f"{name} must be an array of at most 1000 strings")
        result, seen = [], set()
        for raw in value:
            item = text(raw, name, maximum=4096)
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @classmethod
    def _clean(cls, body):
        if not isinstance(body, dict):
            raise DomainError("invalid_argument", "Monitor body must be an object")
        allowed = {
            "name", "queries", "prompts", "domains", "pools",
            "source_adapter", "source_version", "frequency_seconds",
            "budget", "rules", "enabled",
        }
        if set(body) - allowed:
            raise DomainError("invalid_argument", "Monitor body has unknown fields")
        name = text(body.get("name"), "name", maximum=500)
        values = {
            field: cls._clean_text_list(body.get(field, []), field)
            for field in LIST_FIELDS
        }
        if not any(values.values()):
            raise DomainError(
                "invalid_argument",
                "Monitor requires at least one query, prompt, domain or pool",
            )
        source_adapter = text(body.get("source_adapter"), "source_adapter", maximum=200)
        source_version = text(body.get("source_version"), "source_version", maximum=200)
        frequency = body.get("frequency_seconds")
        if type(frequency) is not int or not FREQUENCY_MIN <= frequency <= FREQUENCY_MAX:
            raise DomainError(
                "invalid_argument",
                f"frequency_seconds must be an integer between {FREQUENCY_MIN} and {FREQUENCY_MAX}",
            )
        budget = body.get("budget")
        if not isinstance(budget, dict) or set(budget) != {"monthly_limit", "currency"}:
            raise DomainError(
                "invalid_argument",
                "budget requires monthly_limit and currency",
            )
        try:
            limit = decimal(budget["monthly_limit"])
        except DomainError:
            raise DomainError(
                "invalid_argument", "budget.monthly_limit must be a finite nonnegative decimal"
            ) from None
        if limit < 0:
            raise DomainError(
                "invalid_argument", "budget.monthly_limit must be a finite nonnegative decimal")
        currency = budget["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise DomainError(
                "invalid_argument", "budget.currency must be a three-letter uppercase code")
        rules = body.get("rules", {})
        if not isinstance(rules, dict):
            raise DomainError("invalid_argument", "rules must be an object")
        canonical_json(rules)
        if len(canonical_json(rules).encode()) > 32768:
            raise DomainError("invalid_argument", "rules is too large")
        enabled = body.get("enabled", False)
        if type(enabled) is not bool:
            raise DomainError("invalid_argument", "enabled must be a boolean")
        return {
            **values,
            "name": name,
            "source_adapter": source_adapter,
            "source_version": source_version,
            "frequency_seconds": frequency,
            "budget": {
                "monthly_limit": str(limit),
                "currency": currency,
            },
            "rules": rules,
            "enabled": False,
            "requested_enabled": enabled,
        }

    @staticmethod
    def _public(row):
        config = plain_json(row["config"])
        config.pop("enabled", None)
        config.pop("requested_enabled", None)
        return {
            "monitor_id": row["monitor_id"],
            "name": row["name"],
            "version": row["version"],
            "etag": f'"{row["version"]}"',
            "source_adapter": row["source_adapter"],
            "source_version": row["source_version"],
            "config": config,
            "enabled": False,
            "scheduler_state": row["scheduler_state"],
            "created_at": utc_text(row["created_at"]),
            "updated_at": utc_text(row["updated_at"]),
        }

    def _require_source(self, principal, source_adapter, source_version, *, now, db=None):
        grants = [
            self.registry.grant(principal.tenant_id, grant_id, db=db)
            for grant_id in principal.grant_ids
        ]
        if not any(
            grant.source_adapter == source_adapter
            and grant.source_version == source_version
            for grant in grants
        ):
            raise DomainError(
                "not_authorized",
                "No current asset grant covers this monitor source",
                403,
            )

    def create(self, principal, body, *, idempotency_key, now):
        text(idempotency_key, "Idempotency-Key", maximum=120)
        config = self._clean(body)
        if config["requested_enabled"]:
            raise DomainError(
                "not_covered",
                "Monitor configuration is available, but no scheduler is connected",
                501,
            )
        public_config = {
            key: value for key, value in config.items()
            if key not in {"name", "source_adapter", "source_version", "requested_enabled"}
        }
        request_hash = identity("monitor-request", {
            "name": config["name"],
            "source_adapter": config["source_adapter"],
            "source_version": config["source_version"],
            "config": public_config,
        })
        with self.registry.connect() as db:
            existing = db.execute(
                """SELECT * FROM sf_google_monitors
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency key was used for another monitor",
                    409,
                )
            return self._public(existing)
        monitor_id = identity("monitor", {
            "tenant_id": principal.tenant_id,
            "idempotency_key": idempotency_key,
        })
        try:
            with self.registry.connect() as db:
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261112))",
                    (principal.tenant_id,),
                )
                self.registry.assert_current(principal, now=now, db=db)
                self._require_source(
                    principal,
                    config["source_adapter"],
                    config["source_version"],
                    now=now,
                    db=db,
                )
                conflict = db.execute(
                    """SELECT * FROM sf_google_monitors
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
                if conflict:
                    if conflict["request_hash"] != request_hash:
                        raise DomainError(
                            "idempotency_conflict",
                            "Idempotency key was used for another monitor",
                            409,
                        )
                    return self._public(conflict)
                db.execute(
                    """INSERT INTO sf_google_monitors
                       (tenant_id,monitor_id,idempotency_key,request_hash,subject_ref,
                        name,source_adapter,source_version,version,config,enabled,
                        scheduler_state,created_at,updated_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1,%s,false,
                              'not_connected_no_scheduler',%s,%s)""",
                    (
                        principal.tenant_id,
                        monitor_id,
                        idempotency_key,
                        request_hash,
                        principal.subject_ref,
                        config["name"],
                        config["source_adapter"],
                        config["source_version"],
                        Jsonb(public_config),
                        now,
                        now,
                    ),
                )
                row = db.execute(
                    """SELECT * FROM sf_google_monitors
                       WHERE tenant_id=%s AND monitor_id=%s""",
                    (principal.tenant_id, monitor_id),
                ).fetchone()
            return self._public(row)
        except UniqueViolation:
            with self.registry.connect() as db:
                row = db.execute(
                    """SELECT * FROM sf_google_monitors
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
            if not row or row["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency race with another monitor",
                    409,
                ) from None
            return self._public(row)

    def list(self, principal, parameters, *, now):
        if set(parameters) - {"limit", "cursor"}:
            raise DomainError("invalid_argument", "Unknown monitor list parameter")
        limit = page_limit(parameters.get("limit"))
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered",
                "Monitor cursor pagination is not configured",
                503,
            )
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                "limit": limit,
                "subject": principal.subject_ref,
            },
            "sort": ["created_at", "monitor_id"],
            "source": "monitors:1",
            "manifest_id": None,
        }
        after = None
        if "cursor" in parameters:
            after = self.cursor_codec.decode(
                parameters["cursor"], binding=binding, now=now)
        with self.registry.connect() as db:
            self.registry.assert_current(principal, now=now, db=db)
            rows = db.execute(
                """SELECT * FROM sf_google_monitors
                   WHERE tenant_id=%s AND subject_ref=%s
                   ORDER BY created_at DESC,monitor_id DESC""",
                (principal.tenant_id, principal.subject_ref),
            ).fetchall()
        if after is not None:
            rows = [
                row for row in rows
                if (
                    utc_text(row["created_at"]),
                    row["monitor_id"],
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
                    last["monitor_id"],
                ],
                expires_at=parse_time(now) + timedelta(minutes=10),
                now=now,
            )
        return {
            "monitors": [self._public(row) for row in page],
            "next_cursor": cursor,
            "pagination_state": "signed_cursor",
            "execution_state": "paused_no_scheduler",
        }

    def detail(self, principal, monitor_id, *, now):
        text(monitor_id, "monitor_id")
        with self.registry.connect() as db:
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_monitors
                   WHERE tenant_id=%s AND monitor_id=%s""",
                (principal.tenant_id, monitor_id),
            ).fetchone()
        if not row:
            raise DomainError("not_found", "No monitor with this ID", 404)
        if row["subject_ref"] != principal.subject_ref:
            raise DomainError("not_authorized", "Monitor belongs to another key", 403)
        return self._public(row)

    def update(self, principal, monitor_id, body, *, if_match, now):
        text(monitor_id, "monitor_id")
        expected = _if_match(if_match)
        if not isinstance(body, dict) or not body:
            raise DomainError("invalid_argument", "Monitor patch must be a nonempty object")
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261112))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_monitors
                   WHERE tenant_id=%s AND monitor_id=%s FOR UPDATE""",
                (principal.tenant_id, monitor_id),
            ).fetchone()
            if not row:
                raise DomainError("not_found", "No monitor with this ID", 404)
            if row["subject_ref"] != principal.subject_ref:
                raise DomainError("not_authorized", "Monitor belongs to another key", 403)
            if row["version"] != expected:
                raise DomainError(
                    "precondition_failed",
                    "If-Match does not match the current monitor version",
                    412,
                )
            current = plain_json(row["config"])
            merged = {
                "name": row["name"],
                "source_adapter": row["source_adapter"],
                "source_version": row["source_version"],
                **current,
                **body,
            }
            config = self._clean(merged)
            if config["requested_enabled"]:
                raise DomainError(
                    "not_covered",
                    "Monitor configuration is available, but no scheduler is connected",
                    501,
                )
            self._require_source(
                principal,
                config["source_adapter"],
                config["source_version"],
                now=now,
                db=db,
            )
            public_config = {
                key: value for key, value in config.items()
                if key not in {
                    "name", "source_adapter", "source_version", "requested_enabled"
                }
            }
            db.execute(
                """UPDATE sf_google_monitors
                   SET name=%s,source_adapter=%s,source_version=%s,
                       version=version+1,config=%s,enabled=false,
                       scheduler_state='not_connected_no_scheduler',updated_at=%s
                   WHERE tenant_id=%s AND monitor_id=%s""",
                (
                    config["name"],
                    config["source_adapter"],
                    config["source_version"],
                    Jsonb(public_config),
                    now,
                    principal.tenant_id,
                    monitor_id,
                ),
            )
            updated = db.execute(
                """SELECT * FROM sf_google_monitors
                   WHERE tenant_id=%s AND monitor_id=%s""",
                (principal.tenant_id, monitor_id),
            ).fetchone()
        return self._public(updated)
