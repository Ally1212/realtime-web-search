"""Persistent hourly accounting for source runs and delivery outcomes.

The ledger never derives missing counts, treats a cached observation as a fresh
source sample, or marks an unrun plan point as anything other than missed at an
explicit close. Existing ingest rows are not backfilled: their hourly counts
become available only when explicitly admitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
import re
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import DomainError, identity, parse_time, text, utc_text

COUNTER_STAGES = frozenset({
    "source_attempts",
    "billable_units",
    "parsed",
    "sent",
    "accepted",
    "terminal",
    "readable",
    "rights_expired",
})
RUN_STATES = frozenset({"succeeded", "failed", "canceled"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_ingest_plan_points (
 point_id text PRIMARY KEY, tenant_id text NOT NULL, source_adapter text NOT NULL,
 scheduled_at timestamptz NOT NULL, hour_start timestamptz NOT NULL,
 UNIQUE(tenant_id, source_adapter, scheduled_at, point_id)
);
CREATE INDEX IF NOT EXISTS sf_google_ingest_plan_points_hour
 ON sf_google_ingest_plan_points(hour_start,tenant_id,source_adapter);
CREATE TABLE IF NOT EXISTS sf_google_ingest_runs (
 tenant_id text NOT NULL, run_id text NOT NULL, source_adapter text NOT NULL,
 point_id text, scheduled_at timestamptz NOT NULL,
 requested_at timestamptz NOT NULL, source_observed_at timestamptz,
 received_at timestamptz NOT NULL, state text NOT NULL,
 cache_hit boolean NOT NULL, retry_of text, counters jsonb NOT NULL,
 hour_start timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 record_kind text NOT NULL DEFAULT 'unknown_legacy'
  CHECK(record_kind IN ('source_run','delivery_transition','unknown_legacy')),
 PRIMARY KEY(tenant_id,run_id)
);
CREATE INDEX IF NOT EXISTS sf_google_ingest_runs_hour
 ON sf_google_ingest_runs(hour_start,tenant_id,source_adapter);
ALTER TABLE sf_google_ingest_runs ADD COLUMN IF NOT EXISTS record_kind text
 NOT NULL DEFAULT 'unknown_legacy'
 CHECK(record_kind IN ('source_run','delivery_transition','unknown_legacy'));
CREATE TABLE IF NOT EXISTS sf_google_ingest_closed_hours (
 tenant_id text NOT NULL, source_adapter text NOT NULL, hour_start timestamptz NOT NULL,
 lateness_grace_seconds integer NOT NULL, closed_at timestamptz NOT NULL,
 summary jsonb NOT NULL,
 PRIMARY KEY(tenant_id,source_adapter,hour_start)
);
CREATE TABLE IF NOT EXISTS sf_google_ingest_late_transitions (
 tenant_id text NOT NULL, transition_id text NOT NULL, event_id text NOT NULL,
 source_adapter text NOT NULL, origin_hour timestamptz NOT NULL,
 transition_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 state text NOT NULL, counters jsonb NOT NULL,
 reason text NOT NULL CHECK(reason='sealed_source_hour'),
 PRIMARY KEY(tenant_id,transition_id)
);
"""


def _hour(value):
    parsed = parse_time(value)
    return parsed.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0)


def _optional_time(value, name):
    if value is None:
        return None
    return parse_time(value)


@dataclass(frozen=True)
class Counter:
    stage: str
    unit: str
    quantity: int

    def __post_init__(self):
        if self.stage not in COUNTER_STAGES:
            raise DomainError("invalid_argument", "Unknown source counter stage")
        text(self.unit, "counter unit", maximum=128)
        if type(self.quantity) is not int or self.quantity < 0:
            raise DomainError(
                "invalid_argument", "Counter quantity must be a nonnegative integer")

    def to_dict(self):
        return {
            "stage": self.stage,
            "unit": self.unit,
            "quantity": self.quantity,
        }


@dataclass(frozen=True)
class PlanPoint:
    point_id: str
    tenant_id: str
    source: str
    scheduled_at: str

    def __post_init__(self):
        for name in ("point_id", "tenant_id", "source"):
            text(getattr(self, name), name)
        object.__setattr__(
            self, "scheduled_at", utc_text(parse_time(self.scheduled_at)))

    @property
    def hour(self):
        return _hour(self.scheduled_at)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tenant_id: str
    source: str
    scheduled_at: str
    requested_at: str
    received_at: str
    state: str
    cache_hit: bool
    point_id: str | None = None
    source_observed_at: str | None = None
    retry_of: str | None = None
    counters: tuple[Counter, ...] = ()
    record_kind: str = "source_run"

    def __post_init__(self):
        for name in ("run_id", "tenant_id", "source"):
            text(getattr(self, name), name)
        if self.state not in RUN_STATES:
            raise DomainError("invalid_argument", "Unknown run state")
        if self.record_kind not in {"source_run", "delivery_transition", "unknown_legacy"}:
            raise DomainError("invalid_argument", "Unknown hourly record kind")
        if type(self.cache_hit) is not bool:
            raise DomainError("invalid_argument", "cache_hit must be boolean")
        scheduled = parse_time(self.scheduled_at)
        requested = parse_time(self.requested_at)
        received = parse_time(self.received_at)
        if requested < scheduled:
            raise DomainError(
                "invalid_argument", "Run request cannot precede its scheduled point")
        if received < requested:
            raise DomainError(
                "invalid_argument", "Run receipt cannot precede its request")
        source_observed = _optional_time(
            self.source_observed_at, "source_observed_at")
        if source_observed is not None and source_observed > received:
            raise DomainError(
                "invalid_argument",
                "Source observation cannot follow receipt",
            )
        if self.retry_of is not None:
            text(self.retry_of, "retry_of")
            if self.retry_of == self.run_id:
                raise DomainError(
                    "invalid_argument", "A run cannot retry itself")
        if self.point_id is not None:
            text(self.point_id, "point_id")
        counters = tuple(self.counters)
        if any(not isinstance(row, Counter) for row in counters):
            raise DomainError("invalid_argument", "Invalid run counters")
        if len({row.stage for row in counters}) != len(counters):
            raise DomainError(
                "invalid_argument", "Each counter stage may appear once per run")
        object.__setattr__(self, "scheduled_at", utc_text(scheduled))
        object.__setattr__(self, "requested_at", utc_text(requested))
        object.__setattr__(self, "received_at", utc_text(received))
        if source_observed is not None:
            object.__setattr__(
                self, "source_observed_at", utc_text(source_observed))
        object.__setattr__(self, "counters", counters)

    @property
    def hour(self):
        return _hour(self.scheduled_at)

    @property
    def fresh_sample_eligible(self):
        return (
            self.state == "succeeded"
            and not self.cache_hit
            and self.retry_of is None
            and self.record_kind == "source_run"
        )


def _counter_json(counters):
    return Jsonb([row.to_dict() for row in counters])


def _point_values(point):
    return (
        point.point_id, point.tenant_id, point.source,
        parse_time(point.scheduled_at), _hour(point.scheduled_at),
    )


def _run_values(run, recorded_at):
    return (
        run.tenant_id, run.run_id, run.source, run.point_id,
        parse_time(run.scheduled_at), parse_time(run.requested_at),
        parse_time(run.source_observed_at) if run.source_observed_at else None,
        parse_time(run.received_at), run.state, run.cache_hit,
        run.retry_of, _counter_json(run.counters), run.hour, recorded_at, run.record_kind,
    )


def _point_from_row(row):
    return PlanPoint(
        row["point_id"], row["tenant_id"], row["source_adapter"],
        utc_text(row["scheduled_at"]),
    )


def _run_from_row(row):
    return RunRecord(
        row["run_id"], row["tenant_id"], row["source_adapter"],
        utc_text(row["scheduled_at"]), utc_text(row["requested_at"]),
        utc_text(row["received_at"]), row["state"], row["cache_hit"],
        row["point_id"], (
            utc_text(row["source_observed_at"])
            if row["source_observed_at"] else None
        ),
        row["retry_of"],
        tuple(Counter(**item) for item in row["counters"]),
        row.get('record_kind') or 'unknown_legacy',
    )


class HourlyIngestLedger:
    """Persistent explicit run facts by tenant/source/UTC scheduled hour."""

    def __init__(self, *, lateness_grace_seconds=900, clock=None):
        if (
                type(lateness_grace_seconds) is not int
                or not 0 <= lateness_grace_seconds <= 86400):
            raise DomainError(
                "invalid_argument",
                "Lateness grace must be between 0 and 86400 seconds",
            )
        self.lateness_grace_seconds = lateness_grace_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.dsn = None
        self._points = {}
        self._runs = {}
        self._closed_hours = {}

    @classmethod
    def persistent(cls, dsn, **kwargs):
        ledger = cls(**kwargs)
        ledger.dsn = dsn
        ledger.initialize()
        return ledger

    def initialize(self):
        if self.dsn is None:
            raise DomainError(
                "invalid_argument", "Persistent ledger requires a DSN")
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute("SELECT pg_advisory_xact_lock(20261110,1)")
            db.execute(SCHEMA)

    @staticmethod
    def _assert_persistent(ledger):
        if ledger.dsn is None:
            raise DomainError(
                "not_covered",
                "This operation requires the persistent ingest ledger",
                503,
            )

    def plan(self, point):
        if not isinstance(point, PlanPoint):
            raise DomainError("invalid_argument", "Expected typed plan point")
        if self.dsn is None:
            prior = self._points.get(point.point_id)
            if prior is not None:
                if prior != point:
                    raise DomainError(
                        "idempotency_conflict",
                        "Plan point identity changed its source or schedule",
                        409,
                    )
                return prior
            self._points[point.point_id] = point
            return point
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(20261110,2)")
            self._assert_closed_db(
                db, point.tenant_id, point.source, point.hour)
            prior = db.execute(
                """SELECT * FROM sf_google_ingest_plan_points
                   WHERE point_id=%s AND tenant_id=%s AND source_adapter=%s""",
                (point.point_id, point.tenant_id, point.source),
            ).fetchone()
            if prior:
                current = _point_from_row(prior)
                if current != point:
                    raise DomainError(
                        "idempotency_conflict",
                        "Plan point identity changed its source or schedule",
                        409,
                    )
                return current
            db.execute(
                """INSERT INTO sf_google_ingest_plan_points
                   (point_id,tenant_id,source_adapter,scheduled_at,hour_start)
                   VALUES(%s,%s,%s,%s,%s)""",
                _point_values(point),
            )
        return point

    def record(self, run, *, connection=None):
        if not isinstance(run, RunRecord):
            raise DomainError("invalid_argument", "Expected typed run record")
        if connection is not None and (self.dsn is None or connection.autocommit):
            raise DomainError("invalid_argument", "Shared ledger writes require a persistent transaction")
        if self.dsn is None:
            prior = self._runs.get(run.run_id)
            if prior is not None:
                if prior != run:
                    raise DomainError(
                        "idempotency_conflict",
                        "Run identity changed its timing or counters",
                        409,
                    )
                return run
            self._assert_open(run.hour)
            self._validate_scope(run, lambda point_id: self._points.get(point_id),
                                 lambda run_id: self._runs.get(run_id))
            self._runs[run.run_id] = run
            return run
        # A caller-owned connection keeps its transaction and commit boundary.
        # Standalone ledger writes retain the existing independent transaction.
        with (nullcontext(connection) if connection is not None else
              psycopg.connect(self.dsn, row_factory=dict_row)) as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(20261110,2)")
            prior = db.execute(
                """SELECT * FROM sf_google_ingest_runs
                   WHERE tenant_id=%s AND run_id=%s""",
                (run.tenant_id, run.run_id),
            ).fetchone()
            if prior:
                current = _run_from_row(prior)
                if current != run:
                    raise DomainError(
                        "idempotency_conflict",
                        "Run identity changed its timing or counters",
                        409,
                    )
                return current
            self._assert_closed_db(
                db, run.tenant_id, run.source, run.hour)
            self._validate_scope(
                run,
                lambda point_id: (
                    _point_from_row(row) if (row := db.execute(
                        """SELECT * FROM sf_google_ingest_plan_points
                           WHERE point_id=%s AND tenant_id=%s
                             AND source_adapter=%s""",
                        (point_id, run.tenant_id, run.source),
                    ).fetchone()) is not None else None
                ),
                lambda original_id: (
                    _run_from_row(row) if (row := db.execute(
                        """SELECT * FROM sf_google_ingest_runs
                           WHERE tenant_id=%s AND run_id=%s""",
                        (run.tenant_id, original_id),
                    ).fetchone()) is not None else None
                ),
            )
            db.execute(
                """INSERT INTO sf_google_ingest_runs
                   (tenant_id,run_id,source_adapter,point_id,scheduled_at,
                    requested_at,source_observed_at,received_at,state,cache_hit,
                    retry_of,counters,hour_start,recorded_at,record_kind)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                _run_values(run, parse_time(self.clock())),
            )
        return run

    def record_late_transition(self, run, *, event_id, transition_at, connection):
        """Append minimal delayed-delivery evidence without changing a sealed hour.

        This uses the caller's state transaction, stores no source fields, and
        never turns the delayed transition into another source observation.
        """
        if (self.dsn is None or connection.autocommit or not isinstance(run,RunRecord)
                or run.record_kind!='delivery_transition'
                or not re.fullmatch(r'google:ingest-event:[0-9a-f]{64}',event_id)):
            raise DomainError('invalid_argument','A persistent event transition transaction is required')
        current=parse_time(transition_at)
        if current<parse_time(run.received_at):
            raise DomainError('invalid_argument','Transition time cannot precede saved receipt time')
        db=connection
        db.execute('SELECT pg_advisory_xact_lock(20261110,2)')
        if not db.execute('SELECT 1 FROM sf_google_ingest_closed_hours WHERE tenant_id=%s AND source_adapter=%s AND hour_start=%s',
                          (run.tenant_id,run.source,run.hour)).fetchone():
            raise DomainError('invalid_argument','A late transition requires a sealed origin hour')
        key=identity('late-delivery-transition',dict(event_id=event_id,transition=run.run_id))
        counters=[c.to_dict() for c in run.counters]
        prior=db.execute('SELECT * FROM sf_google_ingest_late_transitions WHERE tenant_id=%s AND transition_id=%s',
                         (run.tenant_id,key)).fetchone()
        if prior:
            if (prior['event_id']!=event_id or prior['source_adapter']!=run.source
                    or prior['origin_hour']!=run.hour or prior['state']!=run.state or prior['counters']!=counters):
                raise DomainError('idempotency_conflict','Late transition identity changed',409)
            return key
        db.execute("""INSERT INTO sf_google_ingest_late_transitions
            (tenant_id,transition_id,event_id,source_adapter,origin_hour,transition_at,state,counters,reason)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'sealed_source_hour')""",
            (run.tenant_id,key,event_id,run.source,run.hour,current,run.state,Jsonb(counters)))
        return key

    @staticmethod
    def _validate_scope(run, find_point, find_run):
        if run.point_id is not None:
            point = find_point(run.point_id)
            if point is None:
                raise DomainError(
                    "incompatible_scope",
                    "Run references an unknown plan point",
                    409,
                )
            if point.tenant_id != run.tenant_id or point.source != run.source or point.hour != run.hour:
                raise DomainError(
                    "incompatible_scope",
                    "Run source or hour differs from its plan point",
                    409,
                )
        if run.retry_of is not None:
            original = find_run(run.retry_of)
            if original is None:
                raise DomainError(
                    "incompatible_scope",
                    "Retry run references an unknown original run",
                    409,
                )
            if original.tenant_id != run.tenant_id or original.source != run.source:
                raise DomainError(
                    "incompatible_scope",
                    "Retry run crosses its original run scope",
                    409,
                )

    @staticmethod
    def _assert_closed_db(db, tenant_id, source_adapter, hour):
        if db.execute(
                """SELECT 1 FROM sf_google_ingest_closed_hours
                   WHERE tenant_id=%s AND source_adapter=%s AND hour_start=%s""",
                (tenant_id, source_adapter, hour),
        ).fetchone():
            raise DomainError(
                "precondition_failed",
                "Closed UTC hour cannot be changed",
                412,
            )

    def close_hour(self, hour_start):
        hour = _hour(hour_start)
        now = parse_time(self.clock())
        earliest = hour + timedelta(
            hours=1, seconds=self.lateness_grace_seconds)
        if now < earliest:
            raise DomainError(
                "precondition_failed",
                "Hour cannot be closed before its lateness grace period",
                412,
            )
        if self.dsn is None:
            prior = self._closed_hours.get(hour)
            if prior is not None:
                return prior
            summary = self._summary(
                hour, now, closed=True, tenant_id=None, source=None)
            self._closed_hours[hour] = summary
            return summary
        # close_hour is cross-source only in this local contract API; callers
        # must provide tenant/source through close_tenant_hour in production.
        raise DomainError(
            "invalid_argument",
            "Persistent close requires tenant_id and source_adapter",
        )

    def close_tenant_hour(self, tenant_id, source_adapter, hour_start):
        text(tenant_id, "tenant_id")
        text(source_adapter, "source_adapter")
        hour = _hour(hour_start)
        now = parse_time(self.clock())
        earliest = hour + timedelta(
            hours=1, seconds=self.lateness_grace_seconds)
        if now < earliest:
            raise DomainError(
                "precondition_failed",
                "Hour cannot be closed before its lateness grace period",
                412,
            )
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute("SELECT pg_advisory_xact_lock(20261110,2)")
            prior = db.execute(
                """SELECT summary FROM sf_google_ingest_closed_hours
                   WHERE tenant_id=%s AND source_adapter=%s AND hour_start=%s""",
                (tenant_id, source_adapter, hour),
            ).fetchone()
            if prior:
                return prior["summary"]
            summary = self._summary(
                hour, now, closed=True, tenant_id=tenant_id,
                source=source_adapter)
            db.execute(
                """INSERT INTO sf_google_ingest_closed_hours
                   (tenant_id,source_adapter,hour_start,
                    lateness_grace_seconds,closed_at,summary)
                   VALUES(%s,%s,%s,%s,%s,%s)""",
                (tenant_id, source_adapter, hour,
                 self.lateness_grace_seconds, now, Jsonb(summary)),
            )
            return summary

    def summary(self, hour_start):
        hour = _hour(hour_start)
        if self.dsn is None:
            if hour in self._closed_hours:
                return self._closed_hours[hour]
            return self._summary(
                hour, parse_time(self.clock()), closed=False,
                tenant_id=None, source=None)
        raise DomainError(
            "invalid_argument",
            "Persistent summary requires tenant_id and source_adapter",
        )

    def tenant_hour_summary(self, tenant_id, source_adapter, hour_start):
        self._assert_persistent(self)
        text(tenant_id, "tenant_id")
        text(source_adapter, "source_adapter")
        hour = _hour(hour_start)
        now = parse_time(self.clock())
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            closed = db.execute(
                """SELECT summary FROM sf_google_ingest_closed_hours
                   WHERE tenant_id=%s AND source_adapter=%s AND hour_start=%s""",
                (tenant_id, source_adapter, hour),
            ).fetchone()
            if closed:
                return closed["summary"]
        return self._summary(
            hour, now, closed=False, tenant_id=tenant_id,
            source=source_adapter)

    def _assert_open(self, hour):
        if self.dsn is not None:
            raise DomainError(
                "invalid_argument",
                "Persistent ledger closure is tenant/source scoped",
            )
        if hour in self._closed_hours:
            raise DomainError(
                "precondition_failed",
                "Closed UTC hour cannot be changed",
                412,
            )

    def _summary(self, hour, now, *, closed, tenant_id, source):
        if self.dsn is None:
            points = {
                point.point_id: point
                for point in self._points.values()
                if point.hour == hour
            }
            runs = [
                run for run in self._runs.values()
                if run.hour == hour
            ]
        else:
            with psycopg.connect(self.dsn, row_factory=dict_row) as db:
                points = {
                    row["point_id"]: _point_from_row(row)
                    for row in db.execute(
                        """SELECT * FROM sf_google_ingest_plan_points
                           WHERE tenant_id=%s AND source_adapter=%s
                             AND hour_start=%s""",
                        (tenant_id, source, hour),
                    ).fetchall()
                }
                runs = [
                    _run_from_row(row) for row in db.execute(
                        """SELECT * FROM sf_google_ingest_runs
                           WHERE tenant_id=%s AND source_adapter=%s
                             AND hour_start=%s
                           ORDER BY recorded_at,run_id""",
                        (tenant_id, source, hour),
                    ).fetchall()
                ]
        source_runs = [
            run for run in runs if run.record_kind == "source_run"
        ]
        delivery_transitions = [
            run for run in runs
            if run.record_kind == "delivery_transition"
        ]
        by_point = {}
        for run in runs:
            if run.point_id is not None:
                by_point.setdefault(run.point_id, []).append(run)

        planned_completed = 0
        planned_failed = 0
        planned_unresolved = 0
        for point_id, point in points.items():
            matching = [
                run for run in by_point.get(point_id, ())
                if run.record_kind == "source_run"
            ]
            if any(run.state == "succeeded" for run in matching):
                planned_completed += 1
            elif any(run.state == "failed" for run in matching):
                planned_failed += 1
            else:
                planned_unresolved += 1

        late_runs = 0
        on_time_runs = 0
        for run in source_runs:
            deadline = (
                parse_time(run.scheduled_at)
                + timedelta(seconds=self.lateness_grace_seconds)
            )
            if parse_time(run.received_at) > deadline:
                late_runs += 1
            else:
                on_time_runs += 1

        counters = []
        for stage in sorted(COUNTER_STAGES):
            units = sorted({
                row.unit for run in runs for row in run.counters
                if row.stage == stage
            })
            for unit in units:
                rows = [
                    row for run in runs for row in run.counters
                    if row.stage == stage and row.unit == unit
                ]
                counters.append({
                    "stage": stage,
                    "unit": unit,
                    "quantity": sum(row.quantity for row in rows),
                    "known_runs": len(rows),
                    "unknown_runs": len(runs) - len(rows),
                    "coverage": (
                        "complete" if len(rows) == len(runs) else "partial"
                    ),
                })

        return {
            "hour_start": utc_text(hour),
            "tenant_id": tenant_id,
            "source_adapter": source,
            "hour_end": utc_text(hour + timedelta(hours=1)),
            "as_of": utc_text(now),
            "closed": closed,
            "planned": {
                "points": len(points),
                "completed": planned_completed,
                "failed": planned_failed,
                "pending": 0 if closed else planned_unresolved,
                "missed": planned_unresolved if closed else 0,
                "coverage": (
                    None if not points else
                    round(planned_completed / len(points), 6)
                ),
            },
            "runs": {
                "total": len(source_runs),
                "succeeded": sum(run.state == "succeeded" for run in source_runs),
                "failed": sum(run.state == "failed" for run in source_runs),
                "canceled": sum(run.state == "canceled" for run in source_runs),
                "on_time": on_time_runs,
                "late": late_runs,
                "unplanned": sum(run.point_id is None for run in source_runs),
                "fresh_samples": sum(
                    run.fresh_sample_eligible for run in source_runs),
                "cached_or_retried_not_fresh": sum(
                    run.state == "succeeded"
                    and (run.cache_hit or run.retry_of is not None)
                    for run in source_runs
                ),
            },
            "delivery_transitions": {
                "total": len(delivery_transitions),
                "succeeded": sum(
                    run.state == "succeeded"
                    for run in delivery_transitions),
                "failed": sum(
                    run.state == "failed"
                    for run in delivery_transitions),
                "canceled": sum(
                    run.state == "canceled"
                    for run in delivery_transitions),
            },
            "unclassified_records": {
                "total": sum(run.record_kind=='unknown_legacy' for run in runs),
                "not_counted_as_source_observations": True,
            },
            "timings": {
                "scheduled": True,
                "requested": all(run.requested_at for run in runs),
                "source_observed_known": sum(
                    run.source_observed_at is not None for run in runs),
                "received": True,
                "missing_times_are_not_inferred": True,
                "cache_and_retry_are_separate": True,
            },
            "counters": counters,
            "counter_unit_policy": (
                "counts_are_never_summed_across_units;"
                "missing_stage_counts_remain_unknown"
            ),
        }
