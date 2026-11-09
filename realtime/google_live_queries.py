"""Durable, quota-bound on-demand Google query jobs.

The web process only creates queued jobs. A separately deployed worker must
hold the approved source executor; no source is called inline with an API read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError,
    Query,
    QueryVariant,
    identity,
    parse_time,
    plain_json,
    text,
    utc_text,
)
from .google_evidence import page_limit
from .google_observations import SERPFeature, SERPItem
from .google_rights import authorize

LIVE_QUERY_VERSION = "google-live-query-1"
MAX_DAILY_TASKS = 50
MAX_UNITS_PER_TASK = 100
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
TERMINAL = frozenset({"succeeded", "failed", "canceled", "expired"})
FIELD = "live_query"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_live_queries (
 tenant_id text NOT NULL, live_query_id text NOT NULL,
 idempotency_key text NOT NULL, request_hash text NOT NULL,
 subject_ref text NOT NULL, source_adapter text NOT NULL,
 source_version text NOT NULL, query_text text NOT NULL,
 variant_id text NOT NULL, variant_json jsonb NOT NULL,
 max_units integer NOT NULL CHECK(max_units BETWEEN 1 AND 100),
 status text NOT NULL, created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, started_at timestamptz,
 completed_at timestamptz, cancel_requested boolean NOT NULL DEFAULT false,
 worker_ref text, lease_token text, lease_expires_at timestamptz,
 attempts integer NOT NULL DEFAULT 0, result jsonb,
 error_code text, error_message text,
 PRIMARY KEY(tenant_id,live_query_id),
 UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS sf_google_live_queries_queue
 ON sf_google_live_queries(tenant_id,status,created_at);
CREATE INDEX IF NOT EXISTS sf_google_live_queries_daily
 ON sf_google_live_queries(tenant_id,created_at);
"""


def _now():
    return datetime.now(timezone.utc)


def _day_bounds(value):
    current = parse_time(value)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _live_id(tenant, key):
    return identity("live-query", {
        "tenant_id": tenant,
        "idempotency_key": key,
    })


@dataclass(frozen=True)
class LiveQueryEvidence:
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
                "Live-query evidence expiry must follow observation",
            )
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "evidence_expires_at", utc_text(expires))
        if self.provider_request_id is not None:
            text(self.provider_request_id, "provider_request_id")

    def to_dict(self):
        return {
            field.name: plain_json(getattr(self, field.name))
            for field in fields(self)
        }


@dataclass(frozen=True)
class LiveQueryResult:
    evidence: LiveQueryEvidence
    provider_namespace: str
    native_id: str
    returned_depth: int
    complete: bool
    units_used: int
    items: tuple[SERPItem, ...]
    features: tuple[SERPFeature, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.evidence, LiveQueryEvidence):
            raise DomainError(
                "invalid_argument",
                "Live query requires typed source evidence",
            )
        text(self.provider_namespace, "provider_namespace")
        text(self.native_id, "native_id")
        if type(self.returned_depth) is not int or not 1 <= self.returned_depth <= 100:
            raise DomainError(
                "invalid_argument",
                "returned_depth must be between 1 and 100",
            )
        if type(self.complete) is not bool:
            raise DomainError(
                "invalid_argument", "complete must be boolean")
        if type(self.units_used) is not int or not 0 <= self.units_used <= MAX_UNITS_PER_TASK:
            raise DomainError(
                "invalid_argument",
                "units_used must be a nonnegative integer within the task limit",
            )
        items = tuple(self.items)
        features = tuple(self.features)
        if len(items) > 10000 or any(
                not isinstance(item, SERPItem) for item in items):
            raise DomainError("invalid_argument", "Invalid live-query items")
        if len(features) > 10000 or any(
                not isinstance(feature, SERPFeature) for feature in features):
            raise DomainError("invalid_argument", "Invalid live-query features")
        if len({item.item_id for item in items}) != len(items):
            raise DomainError(
                "invalid_argument", "Duplicate live-query item IDs")
        if len({feature.feature_id for feature in features}) != len(features):
            raise DomainError(
                "invalid_argument", "Duplicate live-query feature IDs")
        for item in items:
            if (
                    item.organic_position is not None
                    and item.organic_position > self.returned_depth
            ):
                raise DomainError(
                    "invalid_argument",
                    "Live item position exceeds returned depth",
                )
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "features", features)
        limitations = tuple(self.limitations)
        if any(not isinstance(value, str) or not value for value in limitations):
            raise DomainError(
                "invalid_argument", "Invalid live-query limitations")
        object.__setattr__(self, "limitations", limitations)

    def to_dict(self):
        return {
            "evidence": self.evidence.to_dict(),
            "provider_namespace": self.provider_namespace,
            "native_id": self.native_id,
            "returned_depth": self.returned_depth,
            "complete": self.complete,
            "units_used": self.units_used,
            "items": [item.to_dict() for item in self.items],
            "features": [feature.to_dict() for feature in self.features],
            "limitations": list(self.limitations),
            "raw_response_stored": False,
        }


class LiveQueryService:
    def __init__(self, dsn, *, registry, executor, cursor_codec=None, clock=None):
        self.dsn = dsn
        self.registry = registry
        self.executor = executor
        self.cursor_codec = cursor_codec
        self.clock = clock or _now

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,9)")
            db.execute(SCHEMA)

    @staticmethod
    def route(path):
        if path in {"/live-queries", "/live-queries/"}:
            return "list", None
        match = re.fullmatch(r"/live-queries/([^/]+)(/cancel)?", path)
        if match:
            return (
                "cancel" if match[2] else "detail",
                match[1],
            )
        raise DomainError(
            "not_found", "No live-query route matches", 404)

    def _source(self, principal, source_id, source_version, *, now):
        source = self.registry.source(
            principal.tenant_id, source_id, source_version)
        grants = [
            self.registry.grant(principal.tenant_id, grant_id)
            for grant_id in principal.grant_ids
        ]
        matching = [
            grant for grant in grants
            if (
                grant.source_adapter == source_id
                and grant.source_version == source_version
                and FIELD in grant.fields
            )
        ]
        if not matching:
            raise DomainError(
                "not_authorized",
                "No asset grant covers live queries for this source",
                403,
            )
        if "reader" not in principal.roles:
            raise DomainError(
                "use_not_permitted",
                "The reader role is required for live queries",
                403,
            )
        if source.coverage.get("live_queries") is not True:
            raise DomainError(
                "use_not_permitted",
                "The source does not explicitly approve live queries",
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

    @staticmethod
    def _variant(query_text, value):
        if not isinstance(value, dict):
            raise DomainError(
                "invalid_argument", "variant must be an object")
        required = {
            "country", "language", "location", "device", "search_engine",
            "surface", "channel", "login_state", "safe_search", "depth",
            "parameters_version",
        }
        allowed = required | {"parameters"}
        if set(value) - allowed or required - set(value):
            raise DomainError(
                "invalid_argument",
                "variant fields are incomplete or unsupported",
            )
        query = Query(query_text)
        variant = QueryVariant(
            query_id=query.query_id,
            country=value["country"],
            language=value["language"],
            location=value["location"],
            device=value["device"],
            search_engine=value["search_engine"],
            surface=value["surface"],
            channel=value["channel"],
            login_state=value["login_state"],
            safe_search=value["safe_search"],
            depth=value["depth"],
            parameters_version=value["parameters_version"],
            parameters=value.get("parameters", {}),
        )
        return query, variant

    def _public(self, row, *, now, include_result=True):
        expired = (
            parse_time(now) >= parse_time(row["expires_at"])
            and row["status"] not in TERMINAL
        )
        status = "expired" if expired else row["status"]
        result = (
            plain_json(row["result"])
            if include_result and row["result"] is not None and not expired
            else None
        )
        return {
            "live_query_version": LIVE_QUERY_VERSION,
            "live_query_id": row["live_query_id"],
            "source_adapter": row["source_adapter"],
            "source_version": row["source_version"],
            "query": row["query_text"],
            "variant_id": row["variant_id"],
            "variant": plain_json(row["variant_json"]),
            "max_units": row["max_units"],
            "status": status,
            "cancel_requested": row["cancel_requested"],
            "created_at": utc_text(row["created_at"]),
            "expires_at": utc_text(row["expires_at"]),
            "started_at": (
                utc_text(row["started_at"])
                if row["started_at"] else None),
            "completed_at": (
                utc_text(row["completed_at"])
                if row["completed_at"] else None),
            "attempts": row["attempts"],
            "result": result,
            "error": (
                {
                    "code": row["error_code"],
                    "message": row["error_message"],
                }
                if row["error_code"] else None
            ),
            "execution": (
                "queued_not_started" if status == "queued" else
                "worker_required" if status == "running" else "terminal"
            ),
            "queued_is_not_success": True,
            "provider_cost": {
                "status": "not_covered",
                "reason": "no_invoice_or_provider_cost_ledger_is_connected",
            },
        }

    def create(self, principal, body, *, idempotency_key, now):
        text(idempotency_key, "Idempotency-Key", maximum=120)
        required = {
            "source", "source_version", "query", "variant", "max_units",
        }
        if not isinstance(body, dict) or set(body) != required:
            raise DomainError(
                "invalid_argument",
                "Live-query body requires source, source_version, query, "
                "variant and max_units",
            )
        source_id = body["source"]
        source_version = body["source_version"]
        query_text = body["query"]
        text(source_id, "source", maximum=128)
        text(source_version, "source_version", maximum=128)
        text(query_text, "query", maximum=4096)
        max_units = body["max_units"]
        if (
                type(max_units) is not int
                or not 1 <= max_units <= MAX_UNITS_PER_TASK
        ):
            raise DomainError(
                "invalid_argument",
                f"max_units must be between 1 and {MAX_UNITS_PER_TASK}",
            )
        query, variant = self._variant(query_text, body["variant"])
        source, grant, deadline = self._source(
            principal, source_id, source_version, now=now)
        request = {
            "source": source_id,
            "source_version": source_version,
            "query": query.original_text,
            "variant": variant.to_dict(),
            "max_units": max_units,
        }
        request_hash = identity("live-query-request", request)
        with self.registry.connect() as db:
            existing = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency key was used for another live query",
                    409,
                )
            return self.detail(
                principal, existing["live_query_id"], now=now)
        current = parse_time(now)
        if deadline <= current:
            raise DomainError(
                "expired", "Live-query source rights have expired", 410)
        start, end = _day_bounds(now)
        live_id = _live_id(principal.tenant_id, idempotency_key)
        expires_at = min(current + timedelta(days=1), deadline)
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261109))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            count = db.execute(
                """SELECT count(*) AS n FROM sf_google_live_queries
                   WHERE tenant_id=%s AND created_at>=%s AND created_at<%s""",
                (principal.tenant_id, start, end),
            ).fetchone()["n"]
            if count >= MAX_DAILY_TASKS:
                raise DomainError(
                    "quota_exceeded",
                    f"Live query limit is {MAX_DAILY_TASKS} tasks per UTC day",
                    429,
                )
            conflict = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
            if conflict:
                if conflict["request_hash"] != request_hash:
                    raise DomainError(
                        "idempotency_conflict",
                        "Idempotency key was used for another live query",
                        409,
                    )
                return self._public(conflict, now=now)
            db.execute(
                """INSERT INTO sf_google_live_queries
                   (tenant_id,live_query_id,idempotency_key,request_hash,
                    subject_ref,source_adapter,source_version,query_text,
                    variant_id,variant_json,max_units,status,created_at,expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s)""",
                (
                    principal.tenant_id,
                    live_id,
                    idempotency_key,
                    request_hash,
                    principal.subject_ref,
                    source_id,
                    source_version,
                    query.original_text,
                    variant.variant_id,
                    Jsonb(variant.to_dict()),
                    max_units,
                    current,
                    expires_at,
                ),
            )
            row = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s""",
                (principal.tenant_id, live_id),
            ).fetchone()
        return self._public(row, now=now)

    def detail(self, principal, live_id, *, now):
        text(live_id, "live_query_id")
        with self.registry.connect() as db:
            row = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s""",
                (principal.tenant_id, live_id),
            ).fetchone()
        if not row:
            raise DomainError(
                "not_found", "No live query with this ID", 404)
        if (
                row["subject_ref"] != principal.subject_ref
                and "manager" not in principal.roles
        ):
            raise DomainError(
                "not_authorized",
                "Live query belongs to another key",
                403,
            )
        self._source(
            principal, row["source_adapter"], row["source_version"], now=now)
        if row["result"] is not None:
            evidence = row["result"].get("evidence", {})
            if (
                    not evidence.get("evidence_expires_at")
                    or parse_time(evidence["evidence_expires_at"]) <= parse_time(now)
            ):
                return self._public(row, now=now, include_result=False)
        return self._public(row, now=now)

    def list(self, principal, parameters, *, now):
        if set(parameters) - {"limit", "cursor"}:
            raise DomainError(
                "invalid_argument",
                "Unknown live-query list parameter",
            )
        limit = page_limit(parameters.get("limit"))
        with self.registry.connect() as db:
            rows = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND subject_ref=%s
                   ORDER BY created_at DESC,live_query_id DESC""",
                (principal.tenant_id, principal.subject_ref),
            ).fetchall()
        codec = self.cursor_codec
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                "limit": limit,
                "subject": principal.subject_ref,
            },
            "sort": ["created_at", "live_query_id"],
            "source": LIVE_QUERY_VERSION,
            "manifest_id": None,
        }
        after = None
        if "cursor" in parameters:
            if codec is None:
                raise DomainError(
                    "not_covered",
                    "Live-query cursor signing is not configured",
                    503,
                )
            after = codec.decode(
                parameters["cursor"], binding=binding, now=now)
        if after is not None:
            rows = [
                row for row in rows
                if (
                    utc_text(row["created_at"]),
                    row["live_query_id"],
                ) < tuple(after)
            ]
        page, more = rows[:limit], len(rows) > limit
        cursor = None
        if more and codec is not None:
            last = page[-1]
            cursor = codec.encode(
                binding=binding,
                position=[
                    utc_text(last["created_at"]),
                    last["live_query_id"],
                ],
                expires_at=min(
                    parse_time(now) + timedelta(minutes=10),
                    min(parse_time(row["expires_at"]) for row in page),
                ),
                now=now,
            )
        return {
            "live_queries": [
                self._public(row, now=now, include_result=False)
                for row in page
            ],
            "next_cursor": cursor,
            "worker_state": (
                "external_worker_supported_not_deployed"),
            "limits": {
                "tasks_per_utc_day": MAX_DAILY_TASKS,
                "max_billable_units_per_task": MAX_UNITS_PER_TASK,
            },
        }

    def cancel(self, principal, live_id, *, now):
        text(live_id, "live_query_id")
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261109))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s FOR UPDATE""",
                (principal.tenant_id, live_id),
            ).fetchone()
            if not row:
                raise DomainError(
                    "not_found", "No live query with this ID", 404)
            if (
                    row["subject_ref"] != principal.subject_ref
                    and "manager" not in principal.roles
            ):
                raise DomainError(
                    "not_authorized",
                    "Live query belongs to another key",
                    403,
                )
            if row["status"] in TERMINAL:
                raise DomainError(
                    "precondition_failed",
                    "Terminal live queries cannot be canceled",
                    409,
                )
            if row["status"] == "queued":
                db.execute(
                    """UPDATE sf_google_live_queries
                       SET status='canceled',cancel_requested=true,completed_at=%s
                       WHERE tenant_id=%s AND live_query_id=%s""",
                    (parse_time(now), principal.tenant_id, live_id),
                )
            else:
                db.execute(
                    """UPDATE sf_google_live_queries
                       SET cancel_requested=true
                       WHERE tenant_id=%s AND live_query_id=%s""",
                    (principal.tenant_id, live_id),
                )
            row = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s""",
                (principal.tenant_id, live_id),
            ).fetchone()
        return self._public(row, now=now)

    def expire_due(self, *, now):
        with self.registry.connect() as db:
            rows = db.execute(
                """UPDATE sf_google_live_queries
                   SET status='expired',result=NULL,completed_at=%s
                   WHERE expires_at<=%s
                     AND status NOT IN ('succeeded','failed','canceled','expired')
                   RETURNING live_query_id""",
                (parse_time(now), parse_time(now)),
            ).fetchall()
        return len(rows)

    def _validate_result(
            self, result, *, source, max_units, query, variant):
        if not isinstance(result, LiveQueryResult):
            raise DomainError(
                "source_error",
                "Live executor returned an invalid typed result",
                503,
            )
        evidence = result.evidence
        if (
                evidence.adapter_id != source.adapter_id
                or evidence.adapter_version != source.adapter_version
                or evidence.source_api_version != source.source_api_version
                or evidence.contract_ref != getattr(
                    self.executor, "contract_ref", None)
                or evidence.dataset_id != getattr(
                    self.executor, "dataset_id", None)
        ):
            raise DomainError(
                "incompatible_scope",
                "Live result source/contract differs from the approved executor",
                409,
            )
        if result.units_used > max_units:
            raise DomainError(
                "quota_exceeded",
                "Live result exceeded the task billable-unit cap",
                429,
            )
        if result.complete and result.returned_depth < variant.depth:
            raise DomainError(
                "partial_result",
                "Complete live result has less depth than requested",
                413,
            )
        return result.to_dict()

    def run_once(self, *, tenant_id, worker_ref, now):
        text(tenant_id, "tenant_id")
        text(worker_ref, "worker_ref")
        self.recover_expired_leases(now=now, tenant_id=tenant_id)
        token = uuid4().hex
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))",
                (tenant_id,),
            )
            job = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND status='queued'
                     AND cancel_requested=false AND expires_at>%s
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (tenant_id, parse_time(now)),
            ).fetchone()
            if job:
                db.execute(
                    """UPDATE sf_google_live_queries
                       SET status='running',started_at=%s,worker_ref=%s,
                           lease_token=%s,lease_expires_at=%s,attempts=attempts+1
                       WHERE tenant_id=%s AND live_query_id=%s""",
                    (
                        parse_time(now),
                        worker_ref,
                        token,
                        parse_time(now) + timedelta(seconds=LEASE_SECONDS),
                        tenant_id,
                        job["live_query_id"],
                    ),
                )
        if not job:
            return None
        try:
            principal = self.registry.principal(
                tenant_id,
                now=now,
                subject_ref=job["subject_ref"],
            )
            source, grant, deadline = self._source(
                principal,
                job["source_adapter"],
                job["source_version"],
                now=now,
            )
            request = {
                "source": job["source_adapter"],
                "source_version": job["source_version"],
                "query": job["query_text"],
                "variant": plain_json(job["variant_json"]),
                "max_units": job["max_units"],
            }
            result = self.executor.execute(tenant_id, request)
            stored_variant = plain_json(job["variant_json"])
            stored_variant.pop("query_id", None)
            result_json = self._validate_result(
                result,
                source=source,
                max_units=job["max_units"],
                query=Query(job["query_text"]),
                variant=QueryVariant(
                    query_id=Query(job["query_text"]).query_id,
                    **stored_variant,
                ),
            )
            expiry = min(
                deadline,
                parse_time(result.evidence.evidence_expires_at),
            )
            if expiry <= parse_time(now):
                raise DomainError(
                    "expired", "Live-query evidence has expired", 410)
            status, error = "succeeded", None
        except DomainError as exc:
            status = "expired" if exc.code == "expired" else "failed"
            result_json, error = None, {
                "code": exc.code,
                "message": str(exc),
            }
        except Exception:
            status, result_json = "failed", None
            error = {
                "code": "source_error",
                "message": "Live-query worker failed",
            }
        with self.registry.connect() as db:
            current = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s FOR UPDATE""",
                (tenant_id, job["live_query_id"]),
            ).fetchone()
            if (
                    not current
                    or current["lease_token"] != token
                    or current["status"] != "running"
            ):
                return (
                    self._public(current, now=now) if current else None)
            if current["cancel_requested"]:
                status, result_json, error = "canceled", None, None
            if parse_time(now) >= parse_time(current["expires_at"]):
                status, result_json, error = "expired", None, None
            db.execute(
                """UPDATE sf_google_live_queries
                   SET status=%s,result=%s,error_code=%s,error_message=%s,
                       completed_at=%s,worker_ref=NULL,lease_token=NULL,
                       lease_expires_at=NULL
                   WHERE tenant_id=%s AND live_query_id=%s""",
                (
                    status,
                    Jsonb(result_json) if result_json is not None else None,
                    error["code"] if error else None,
                    error["message"] if error else None,
                    parse_time(now),
                    tenant_id,
                    job["live_query_id"],
                ),
            )
            row = db.execute(
                """SELECT * FROM sf_google_live_queries
                   WHERE tenant_id=%s AND live_query_id=%s""",
                (tenant_id, job["live_query_id"]),
            ).fetchone()
        return self._public(row, now=now)

    def recover_expired_leases(self, *, now, tenant_id=None):
        current = parse_time(now)
        recovered = 0
        with self.registry.connect() as db:
            params = [current]
            tenant_filter = ""
            if tenant_id is not None:
                text(tenant_id, "tenant_id")
                tenant_filter = " AND tenant_id=%s"
                params.append(tenant_id)
            rows = db.execute(
                f"""SELECT * FROM sf_google_live_queries
                    WHERE status='running' AND lease_expires_at<=%s{tenant_filter}
                    ORDER BY created_at FOR UPDATE SKIP LOCKED""",
                tuple(params),
            ).fetchall()
            for row in rows:
                if current >= parse_time(row["expires_at"]):
                    status, code, message = "expired", None, None
                elif row["cancel_requested"]:
                    status, code, message = "canceled", None, None
                elif row["attempts"] >= MAX_ATTEMPTS:
                    status = "failed"
                    code = "worker_lease_exhausted"
                    message = (
                        "Worker lease expired after the maximum attempts")
                else:
                    status, code, message = "queued", None, None
                if status == "queued":
                    db.execute(
                        """UPDATE sf_google_live_queries
                           SET status='queued',worker_ref=NULL,lease_token=NULL,
                               lease_expires_at=NULL
                           WHERE tenant_id=%s AND live_query_id=%s""",
                        (row["tenant_id"], row["live_query_id"]),
                    )
                else:
                    db.execute(
                        """UPDATE sf_google_live_queries
                           SET status=%s,result=NULL,error_code=%s,
                               error_message=%s,completed_at=%s,worker_ref=NULL,
                               lease_token=NULL,lease_expires_at=NULL
                           WHERE tenant_id=%s AND live_query_id=%s""",
                        (
                            status,
                            code,
                            message,
                            current,
                            row["tenant_id"],
                            row["live_query_id"],
                        ),
                    )
                recovered += 1
        return recovered

    def tenant_ids_with_work(self, *, now):
        with self.registry.connect() as db:
            rows = db.execute(
                """SELECT DISTINCT tenant_id FROM sf_google_live_queries
                   WHERE expires_at>%s
                     AND (status='queued' OR
                          (status='running' AND lease_expires_at<=%s))
                   ORDER BY tenant_id""",
                (parse_time(now), parse_time(now)),
            ).fetchall()
        return [row["tenant_id"] for row in rows]
