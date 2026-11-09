"""Internal GSC partition revisions; no scheduler or Google calls.

Only an explicitly authorized ingestion caller may write audit metadata here.
API top rows and the two bulk tables stay in separate partition namespaces.
Source revision numbers are explicit adapter evidence, not invented run counts.
Report rows are hashed in memory and never persisted as a second raw truth.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, frozen_json, identity, parse_time,
    plain_json, text, utc_text,
)
from .google_search_console import (
    AGGREGATIONS, REPORT_DIMENSIONS, SearchConsoleReport,
)

BULK_TABLES = frozenset({
    "searchdata_site_impression", "searchdata_url_impression",
})
ATTEMPT_STATES = frozenset({"failed", "retry_scheduled", "abandoned"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_gsc_partition_plans (
 tenant_id text NOT NULL, partition_id text NOT NULL,
 scope jsonb NOT NULL, deadline_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,partition_id)
);
CREATE TABLE IF NOT EXISTS sf_google_gsc_partition_revisions (
 tenant_id text NOT NULL, partition_id text NOT NULL, source_revision bigint NOT NULL,
 scope jsonb NOT NULL, metadata jsonb, payload_id text NOT NULL,
 source_observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, is_final boolean NOT NULL,
 PRIMARY KEY(tenant_id,partition_id,source_revision)
);
CREATE TABLE IF NOT EXISTS sf_google_gsc_partition_checks (
 tenant_id text NOT NULL, partition_id text NOT NULL, source_revision bigint NOT NULL,
 check_id text NOT NULL, proof jsonb, received_at timestamptz NOT NULL,
 eligible boolean NOT NULL, exclusions jsonb NOT NULL,
 PRIMARY KEY(tenant_id,partition_id,source_revision,check_id),
 FOREIGN KEY(tenant_id,partition_id,source_revision)
 REFERENCES sf_google_gsc_partition_revisions(tenant_id,partition_id,source_revision)
);
CREATE TABLE IF NOT EXISTS sf_google_gsc_partition_attempts (
 tenant_id text NOT NULL, partition_id text NOT NULL, attempt_id text NOT NULL,
 state text NOT NULL, attempted_at timestamptz NOT NULL, reason_ref text NOT NULL,
 PRIMARY KEY(tenant_id,partition_id,attempt_id)
);
"""

REVISION_SELECT = """
SELECT r.*,c.eligible,c.exclusions,c.proof AS verification,c.check_id
FROM sf_google_gsc_partition_revisions r
JOIN LATERAL (
 SELECT eligible,exclusions,proof,check_id FROM sf_google_gsc_partition_checks
 WHERE tenant_id=r.tenant_id AND partition_id=r.partition_id
   AND source_revision=r.source_revision
 ORDER BY eligible DESC,received_at DESC,check_id DESC LIMIT 1
) c ON true
WHERE r.tenant_id=%s AND r.partition_id=%s
"""


def _day(value):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DomainError("invalid_argument", "Partition day must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise DomainError("invalid_argument", "Partition day must be YYYY-MM-DD")
    return parsed


def _positive(value, name, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= 2**63 - 1:
        raise DomainError("invalid_argument", f"Invalid {name}")


@dataclass(frozen=True)
class GSCPartition:
    tenant_id: str
    source_adapter: str
    source_version: str
    source_api_version: str
    contract_ref: str
    dataset_id: str
    property_id: str
    template_version: str
    grain: tuple[str, ...]
    day: str
    source_timezone: str
    aggregation: str
    report_kind: str
    filters: dict
    hour: int | None = None
    export_table: str | None = None
    hour_start: str | None = None

    def __post_init__(self):
        for name in (
                "tenant_id", "source_adapter", "source_version",
                "source_api_version", "contract_ref", "dataset_id",
                "property_id", "template_version"):
            text(getattr(self, name), name)
        _day(self.day)
        try:
            ZoneInfo(self.source_timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise DomainError("invalid_argument", "Expected IANA source timezone") from None
        grain = tuple(self.grain)
        if (
                not grain or len(set(grain)) != len(grain)
                or set(grain) - REPORT_DIMENSIONS):
            raise DomainError("invalid_argument", "Invalid partition grain")
        if self.hour is not None and (
                type(self.hour) is not int or not 0 <= self.hour < 24):
            raise DomainError("invalid_argument", "Invalid partition hour")
        if ("hour" in grain) != (self.hour is not None):
            raise DomainError(
                "invalid_argument", "Hourly grain must bind an explicit partition hour")
        if self.hour is None and self.hour_start is not None:
            raise DomainError("invalid_argument", "Daily partitions cannot bind an hour start")
        if self.hour is not None:
            zone = ZoneInfo(self.source_timezone)
            if self.hour_start is None:
                local = datetime.fromisoformat(self.day).replace(
                    hour=self.hour, tzinfo=zone)
                if local.utcoffset() != local.replace(fold=1).utcoffset():
                    raise DomainError(
                        "invalid_argument",
                        "Ambiguous or nonexistent local hour requires an explicit valid hour_start",
                    )
                start = local.astimezone(timezone.utc)
            else:
                start = parse_time(self.hour_start)
            local = start.astimezone(zone)
            if (
                    local.date().isoformat() != self.day or local.hour != self.hour
                    or local.minute or local.second or local.microsecond):
                raise DomainError("invalid_argument", "hour_start differs from the local partition hour")
            object.__setattr__(self, "hour_start", utc_text(start))
        if self.aggregation not in AGGREGATIONS:
            raise DomainError("invalid_argument", "Invalid partition aggregation")
        if self.report_kind not in {"top_rows", "bulk"}:
            raise DomainError("invalid_argument", "Invalid partition report kind")
        if self.report_kind == "bulk":
            if self.export_table not in BULK_TABLES or self.hour is not None:
                raise DomainError(
                    "invalid_argument", "Bulk partitions require an explicit daily source table")
        elif self.export_table is not None:
            raise DomainError("invalid_argument", "API rows cannot name a bulk table")
        if not isinstance(self.filters, Mapping) or set(self.filters) - REPORT_DIMENSIONS:
            raise DomainError("invalid_argument", "Invalid partition filters")
        for name, value in self.filters.items():
            text(value, name)
        object.__setattr__(self, "grain", grain)
        object.__setattr__(self, "filters", frozen_json(self.filters))

    def to_dict(self):
        return {
            name: plain_json(getattr(self, name))
            for name in self.__dataclass_fields__
        }

    @property
    def partition_id(self):
        # data_state and revision are deliberately not part of replacement identity.
        return identity("gsc-partition", self.to_dict())


@dataclass(frozen=True)
class BulkExportEvidence:
    partition_id: str
    source_revision: int
    log_ref: str | None = None
    logged_at: str | None = None
    table_check_ref: str | None = None
    table_checked_at: str | None = None
    partition_row_count: int | None = None

    def __post_init__(self):
        text(self.partition_id, "partition_id")
        _positive(self.source_revision, "source revision")
        for ref, timestamp in (
                ("log_ref", "logged_at"), ("table_check_ref", "table_checked_at")):
            value, at = getattr(self, ref), getattr(self, timestamp)
            if (value is None) != (at is None):
                raise DomainError("invalid_argument", "Export evidence reference/time must pair")
            if value is not None:
                text(value, ref)
                object.__setattr__(self, timestamp, utc_text(parse_time(at)))
        if (self.table_check_ref is None) != (self.partition_row_count is None):
            raise DomainError("invalid_argument", "Table verification requires explicit row count")
        if self.partition_row_count is not None:
            _positive(self.partition_row_count, "partition row count", zero=True)
        if self.logged_at and self.table_checked_at:
            if parse_time(self.table_checked_at) < parse_time(self.logged_at):
                raise DomainError(
                    "invalid_argument", "Table verification must follow the successful export log")

    def to_dict(self):
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class GSCRevision:
    scope: GSCPartition
    source_revision: int
    report: SearchConsoleReport
    received_at: str
    export_evidence: BulkExportEvidence | None = None
    applied_filters: dict | None = None

    def __post_init__(self):
        if not isinstance(self.scope, GSCPartition) or not isinstance(
                self.report, SearchConsoleReport):
            raise DomainError("invalid_argument", "Expected typed GSC scope and report")
        _positive(self.source_revision, "source revision")
        scope, report = self.scope, self.report
        filters = {} if self.applied_filters is None else self.applied_filters
        if not isinstance(filters, Mapping) or canonical_json(filters) != canonical_json(scope.filters):
            raise DomainError("incompatible_scope", "Report filters differ from its partition", 409)
        object.__setattr__(self, "applied_filters", frozen_json(filters))
        expected = {
            "property_id": scope.property_id,
            "template_version": scope.template_version,
            "grain": scope.grain,
            "source_timezone": scope.source_timezone,
            "aggregation": scope.aggregation,
            "report_kind": scope.report_kind,
            "period_start": scope.day,
            "period_end": (_day(scope.day) + timedelta(days=1)).isoformat(),
            "partition": scope.day if scope.report_kind == "bulk" else None,
        }
        evidence_scope = {
            "adapter_id": scope.source_adapter,
            "adapter_version": scope.source_version,
            "source_api_version": scope.source_api_version,
            "contract_ref": scope.contract_ref,
            "dataset_id": scope.dataset_id,
        }
        if (
                any(getattr(report, key) != value for key, value in expected.items())
                or any(getattr(report.evidence, key) != value
                       for key, value in evidence_scope.items())):
            raise DomainError("incompatible_scope", "Report differs from its partition scope", 409)
        received = parse_time(self.received_at)
        if not (
                parse_time(report.evidence.source_observed_at) <= received
                < parse_time(report.evidence.evidence_expires_at)):
            raise DomainError("expired", "Partition report is future-dated or expired", 410)
        object.__setattr__(self, "received_at", utc_text(received))
        seen = set()
        for row in report.rows:
            grain_key = canonical_json(row.dimensions)
            if grain_key in seen:
                raise DomainError("incompatible_scope", "Report repeats a partition grain", 409)
            seen.add(grain_key)
            if row.day != scope.day or (
                    "date" in row.dimensions and row.dimensions["date"] != scope.day):
                raise DomainError("incompatible_scope", "Row is outside its daily partition", 409)
            if scope.hour is not None:
                value = parse_time(row.dimensions["hour"])
                start = parse_time(scope.hour_start)
                if not start <= value < start + timedelta(hours=1):
                    raise DomainError("incompatible_scope", "Row is outside its hourly partition", 409)
        proof = self.export_evidence
        if proof is not None:
            if (
                    not isinstance(proof, BulkExportEvidence)
                    or scope.report_kind != "bulk"
                    or proof.partition_id != scope.partition_id
                    or proof.source_revision != self.source_revision):
                raise DomainError("incompatible_scope", "Export evidence differs from revision", 409)
            for at in (proof.logged_at, proof.table_checked_at):
                if at and parse_time(at) > received:
                    raise DomainError("invalid_argument", "Export evidence follows receipt")
            if proof.partition_row_count == 0 and report.rows:
                raise DomainError(
                    "incompatible_scope", "Empty verified source partition cannot have report rows", 409)

    @property
    def exclusions(self):
        result = []
        if not self.report.response_complete:
            result.append("incomplete_response")
        if self.scope.report_kind == "bulk":
            proof = self.export_evidence
            if self.report.statistical_completeness != "bulk_partition_verified":
                result.append("unverified_statistical_completeness")
            if proof is None or proof.log_ref is None:
                result.append("missing_successful_export_log")
            if proof is None or proof.table_check_ref is None:
                result.append("missing_table_partition_verification")
        return result

    def to_dict(self):
        return {
            "scope": self.scope.to_dict(),
            "source_revision": self.source_revision,
            "report": self.report.to_dict(),
            "evidence": self.report.evidence.to_dict(),
            "received_at": self.received_at,
            "export_evidence": (
                self.export_evidence.to_dict() if self.export_evidence else None),
            "applied_filters": plain_json(self.applied_filters),
        }

    def metadata(self):
        report = self.report.to_dict()
        return {
            "scope": self.scope.to_dict(), "source_revision": self.source_revision,
            "received_at": self.received_at, "evidence": self.report.evidence.to_dict(),
            "report": {
                "period": report["period"], "data_state": report["data_state"],
                "response_complete": report["response_complete"],
                "statistical_completeness": report["statistical_completeness"],
                "row_count": len(report["rows"]),
                "report_hash": identity("gsc-report-audit", report),
            },
            "raw_report_retained": False,
            "platform_readback_verified": False,
        }


class GSCRevisionLedger:
    """Internal durable revision store; readers must separately authorize rights."""

    def __init__(self, dsn, *, clock=None):
        self.dsn = dsn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        with psycopg.connect(self.dsn) as db:
            db.execute("SELECT pg_advisory_xact_lock(20261111,1)")
            db.execute(SCHEMA)

    @staticmethod
    def _lock(db, scope):
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                   (scope.partition_id,))

    def expect(self, scope, *, deadline_at):
        if not isinstance(scope, GSCPartition) or scope.report_kind != "bulk":
            raise DomainError("invalid_argument", "Only explicit bulk partitions may be planned")
        deadline = parse_time(deadline_at)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            self._lock(db, scope)
            prior = db.execute(
                """SELECT * FROM sf_google_gsc_partition_plans
                   WHERE tenant_id=%s AND partition_id=%s""",
                (scope.tenant_id, scope.partition_id),
            ).fetchone()
            if prior and prior["deadline_at"] != deadline:
                raise DomainError("idempotency_conflict", "Partition deadline changed", 409)
            if not prior:
                db.execute(
                    """INSERT INTO sf_google_gsc_partition_plans
                       (tenant_id,partition_id,scope,deadline_at) VALUES(%s,%s,%s,%s)""",
                    (scope.tenant_id, scope.partition_id, Jsonb(scope.to_dict()), deadline),
                )
        return scope.partition_id

    def record(self, revision, *, db=None):
        if not isinstance(revision, GSCRevision):
            raise DomainError("invalid_argument", "Expected typed GSC revision")
        if db is None:
            with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
                return self.record(revision, db=connection)
        scope, report = revision.scope, revision.report
        now = parse_time(self.clock())
        if parse_time(revision.received_at) > now:
            raise DomainError("invalid_argument", "Receipt is in the future")
        if parse_time(report.evidence.evidence_expires_at) <= now:
            raise DomainError("expired", "Partition evidence has expired", 410)
        payload = revision.to_dict()
        # Re-reading export verification may add proof, but cannot rewrite the
        # source report or invent a new source revision for that same export.
        core = {key: value for key, value in payload.items()
                if key not in {"export_evidence", "received_at"}}
        payload_id = identity("gsc-partition-revision", core)
        proof = payload["export_evidence"]
        check_id = identity("gsc-partition-check", {
            "payload_id": payload_id, "export_evidence": proof,
        })
        self._lock(db, scope)
        prior = db.execute(
            """SELECT payload_id FROM sf_google_gsc_partition_revisions
               WHERE tenant_id=%s AND partition_id=%s AND source_revision=%s""",
            (scope.tenant_id, scope.partition_id, revision.source_revision),
        ).fetchone()
        if prior:
            if prior["payload_id"] != payload_id:
                raise DomainError("idempotency_conflict", "Source revision payload changed", 409)
        else:
            db.execute(
                """INSERT INTO sf_google_gsc_partition_revisions
                       (tenant_id,partition_id,source_revision,scope,metadata,payload_id,
                    source_observed_at,received_at,expires_at,is_final)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (scope.tenant_id, scope.partition_id, revision.source_revision,
                 Jsonb(scope.to_dict()), Jsonb(revision.metadata()), payload_id,
                 parse_time(report.evidence.source_observed_at),
                 parse_time(revision.received_at),
                 parse_time(report.evidence.evidence_expires_at),
                 report.data_state == "final"),
            )
        db.execute(
            """INSERT INTO sf_google_gsc_partition_checks
               (tenant_id,partition_id,source_revision,check_id,proof,
                received_at,eligible,exclusions) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (scope.tenant_id, scope.partition_id, revision.source_revision,
             check_id, Jsonb(proof), parse_time(revision.received_at),
             not revision.exclusions, Jsonb(revision.exclusions)),
        )
        return {"partition_id": scope.partition_id,
                "source_revision": revision.source_revision, "payload_id": payload_id,
                "check_id": check_id, "eligible": not revision.exclusions,
                "exclusions": revision.exclusions}

    def note_attempt(self, scope, *, attempt_id, state, attempted_at, reason_ref):
        if not isinstance(scope, GSCPartition) or scope.report_kind != "bulk":
            raise DomainError("invalid_argument", "Expected bulk partition scope")
        text(attempt_id, "attempt_id")
        text(reason_ref, "reason_ref")
        if state not in ATTEMPT_STATES:
            raise DomainError("invalid_argument", "Unknown local export attempt state")
        at = parse_time(attempted_at)
        if at > parse_time(self.clock()):
            raise DomainError("invalid_argument", "Attempt is in the future")
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            self._lock(db, scope)
            plan = db.execute(
                """SELECT 1 FROM sf_google_gsc_partition_plans
                   WHERE tenant_id=%s AND partition_id=%s""",
                (scope.tenant_id, scope.partition_id),
            ).fetchone()
            if not plan:
                raise DomainError("not_covered", "No explicit partition plan", 409)
            prior = db.execute(
                """SELECT state,attempted_at,reason_ref FROM sf_google_gsc_partition_attempts
                   WHERE tenant_id=%s AND partition_id=%s AND attempt_id=%s""",
                (scope.tenant_id, scope.partition_id, attempt_id),
            ).fetchone()
            expected = {"state": state, "attempted_at": at, "reason_ref": reason_ref}
            if prior and prior != expected:
                raise DomainError("idempotency_conflict", "Attempt identity changed", 409)
            if not prior:
                db.execute(
                    """INSERT INTO sf_google_gsc_partition_attempts
                       (tenant_id,partition_id,attempt_id,state,attempted_at,reason_ref)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (scope.tenant_id, scope.partition_id, attempt_id, state, at, reason_ref),
                )

    def history(self, scope):
        if not isinstance(scope, GSCPartition):
            raise DomainError("invalid_argument", "Expected partition scope")
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            rows = db.execute(
                REVISION_SELECT + " ORDER BY r.source_revision",
                (scope.tenant_id, scope.partition_id),
            ).fetchall()
        now = parse_time(self.clock())
        # Selection precedes expiry filtering: an expired final must not resurrect
        # a still-retained preliminary or older revision.
        eligible = [row for row in rows if row["eligible"]]
        selected = max(
            eligible, key=lambda row: (row["is_final"], row["source_revision"]),
            default=None,
        )
        return [{
            "partition_id": scope.partition_id,
            "source_revision": row["source_revision"],
            "is_final": row["is_final"],
            "eligible": row["eligible"],
            "exclusions": row["exclusions"],
            "selected": selected is not None and row["source_revision"] == selected["source_revision"],
            "expired": row["expires_at"] <= now,
            "metadata": row["metadata"] if row["expires_at"] > now else None,
            "verification": row["verification"] if row["expires_at"] > now else None,
            "check_id": row["check_id"],
        } for row in rows]

    def current(self, scope):
        selected = next((row for row in self.history(scope) if row["selected"]), None)
        if selected and selected["expired"]:
            raise DomainError("expired", "Selected partition revision has expired", 410)
        return selected

    def purge_expired(self):
        """Remove expired audit details while keeping version/hash tombstones."""
        with psycopg.connect(self.dsn) as db:
            db.execute(
                """UPDATE sf_google_gsc_partition_checks c SET proof=NULL
                   FROM sf_google_gsc_partition_revisions r
                   WHERE c.tenant_id=r.tenant_id AND c.partition_id=r.partition_id
                     AND c.source_revision=r.source_revision AND r.expires_at<=%s
                     AND c.proof IS NOT NULL""", (parse_time(self.clock()),),
            )
            result = db.execute(
                """UPDATE sf_google_gsc_partition_revisions SET metadata=NULL
                   WHERE expires_at<=%s AND metadata IS NOT NULL""",
                (parse_time(self.clock()),),
            )
        return result.rowcount

    def audit_bulk(self, scopes):
        scopes = tuple(scopes)
        if (
                len(scopes) != 2 or any(not isinstance(s, GSCPartition) for s in scopes)
                or {s.export_table for s in scopes} != BULK_TABLES):
            raise DomainError("invalid_argument", "Audit requires both explicit bulk tables")
        bindings = [{
            name: getattr(scope, name) for name in (
                "tenant_id", "source_adapter", "source_version", "source_api_version",
                "contract_ref", "dataset_id", "property_id", "day", "source_timezone",
            )
        } for scope in scopes]
        if canonical_json(bindings[0]) != canonical_json(bindings[1]):
            raise DomainError("incompatible_scope", "Bulk table audit crosses source/day scope", 409)
        result = []
        now = parse_time(self.clock())
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            # One repeatable snapshot covers both tables and their revisions.
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for scope in scopes:
                params = (scope.tenant_id, scope.partition_id)
                plan = db.execute(
                    """SELECT deadline_at FROM sf_google_gsc_partition_plans
                       WHERE tenant_id=%s AND partition_id=%s""", params,
                ).fetchone()
                current = db.execute(
                    "SELECT * FROM (" + REVISION_SELECT + """) candidates WHERE eligible
                       ORDER BY is_final DESC,source_revision DESC LIMIT 1""", params,
                ).fetchone()
                latest = db.execute(
                    REVISION_SELECT + " ORDER BY r.source_revision DESC LIMIT 1", params,
                ).fetchone()
                attempt = db.execute(
                    """SELECT state FROM sf_google_gsc_partition_attempts
                       WHERE tenant_id=%s AND partition_id=%s
                       ORDER BY attempted_at DESC,attempt_id DESC LIMIT 1""", params,
                ).fetchone()
                state = (
                    "verified_final" if current and current["is_final"] and current["expires_at"] > now
                    else "expired" if current and current["expires_at"] <= now
                    else "preliminary" if current
                    else "unplanned" if not plan
                    else "abandoned" if attempt and attempt["state"] == "abandoned"
                    else "missing_overdue" if now >= plan["deadline_at"]
                    else "pending"
                )
                result.append({
                    "export_table": scope.export_table,
                    "partition_id": scope.partition_id, "state": state,
                    "deadline_at": utc_text(plan["deadline_at"]) if plan else None,
                    "last_local_attempt_state": attempt["state"] if attempt else None,
                    "selected_source_revision": current["source_revision"] if current else None,
                    "latest_source_revision": latest["source_revision"] if latest else None,
                    "latest_exclusions": latest["exclusions"] if latest else [],
                    "export_log_failure_inferred": False,
                })
        return {
            "day": scopes[0].day, "tables": result,
            "both_tables_final": all(row["state"] == "verified_final" for row in result),
        }
