import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError
from realtime.google_ingest_ledger import (
    Counter,
    HourlyIngestLedger,
    PlanPoint,
    RunRecord,
)
from tests.test_google_domain import NOW


HOUR = NOW.replace(minute=0, second=0, microsecond=0)


def counter(stage, unit, quantity):
    return Counter(stage, unit, quantity)


def run(run_id="run:one", *, point_id="point:one", cache_hit=False,
        retry_of=None, state="succeeded", received_at=None, scheduled_at=None,
        source_observed_at=None):
    schedule = scheduled_at or HOUR + timedelta(minutes=5)
    return RunRecord(
        run_id=run_id,
        tenant_id="tenant:one",
        source="fixture",
        point_id=point_id,
        scheduled_at=schedule,
        requested_at=schedule + timedelta(minutes=1),
        source_observed_at=source_observed_at or schedule + timedelta(minutes=2),
        received_at=received_at or schedule + timedelta(minutes=3),
        state=state,
        cache_hit=cache_hit,
        retry_of=retry_of,
        counters=(
            counter("source_attempts", "request", 1),
            counter("billable_units", "request", 1),
            counter("parsed", "result_item", 10),
            counter("sent", "stable_event", 10),
            counter("accepted", "platform_receipt", 9),
            counter("terminal", "platform_receipt", 8),
            counter("readable", "platform_document", 7),
        ),
    )


class HourlyIngestLedgerTests(unittest.TestCase):
    def setUp(self):
        self.now = HOUR + timedelta(hours=2)
        self.tenant = "tenant:one"
        self.source_name = "fixture"
        if type(self).__name__ == "HourlyIngestLedgerTests":
            self.ledger = HourlyIngestLedger(
                lateness_grace_seconds=900,
                clock=lambda: self.now,
            )
        else:
            self.root = os.environ["TEST_DATABASE_URL"]
            self.schema = "google_ingest_ledger_" + uuid4().hex
            with psycopg.connect(self.root) as db:
                db.execute(sql.SQL("CREATE SCHEMA {}").format(
                    sql.Identifier(self.schema)))
            self.dsn = make_conninfo(
                self.root, options="-c search_path=" + self.schema)
            self.ledger = HourlyIngestLedger.persistent(
                self.dsn, lateness_grace_seconds=900,
                clock=lambda: self.now)
        self.point = PlanPoint(
            "point:one", self.tenant, self.source_name,
            (HOUR + timedelta(minutes=5)).isoformat())
        self.ledger.plan(self.point)

        if type(self).__name__ == "HourlyIngestLedgerTests":
            self.close = lambda hour: self.ledger.close_hour(hour)
            self.summary = lambda hour: self.ledger.summary(hour)
        else:
            self.close = lambda hour: self.ledger.close_tenant_hour(
                self.tenant, self.source_name, hour)
            self.summary = lambda hour: self.ledger.tenant_hour_summary(
                self.tenant, self.source_name, hour)

    def tearDown(self):
        if type(self).__name__ != "HourlyIngestLedgerTests":
            with psycopg.connect(self.root) as db:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(self.schema)))

    def test_hourly_counts_keep_result_and_receipt_units_separate(self):
        self.ledger.record(run())
        summary = self.summary(HOUR)
        counts = {
            (row["stage"], row["unit"]): row
            for row in summary["counters"]
        }
        self.assertEqual(
            counts[("parsed", "result_item")]["quantity"], 10)
        self.assertEqual(
            counts[("accepted", "platform_receipt")]["quantity"], 9)
        self.assertEqual(
            counts[("sent", "stable_event")]["quantity"], 10)
        self.assertNotIn(("accepted", "result_item"), counts)

    def test_missing_counter_is_unknown_not_zero(self):
        base = run()
        incomplete = replace(
            base,
            counters=tuple(
                row for row in base.counters
                if row.stage != "rights_expired"
            ),
        )
        self.ledger.record(incomplete)
        summary = self.summary(HOUR)
        self.assertFalse(any(
            row["stage"] == "rights_expired"
            for row in summary["counters"]
        ))
        self.assertIn(
            "missing_stage_counts_remain_unknown",
            summary["counter_unit_policy"],
        )

    def test_cached_and_retry_runs_are_not_fresh_samples(self):
        self.ledger.record(run())
        self.ledger.record(run(
            "run:retry", cache_hit=True, retry_of="run:one"))
        summary = self.summary(HOUR)
        self.assertEqual(summary["runs"]["total"], 2)
        self.assertEqual(summary["runs"]["fresh_samples"], 1)
        self.assertEqual(
            summary["runs"]["cached_or_retried_not_fresh"], 1)

    def test_late_and_missed_are_explicit_at_close(self):
        self.ledger.plan(PlanPoint(
            "point:missed", self.tenant, self.source_name,
            (HOUR + timedelta(minutes=20)).isoformat(),
        ))
        self.ledger.record(run(received_at=HOUR + timedelta(minutes=40)))
        open_summary = self.summary(HOUR)
        self.assertFalse(open_summary["closed"])
        self.assertEqual(open_summary["planned"]["pending"], 1)
        self.assertEqual(open_summary["planned"]["missed"], 0)
        summary = self.close(HOUR)
        self.assertTrue(summary["closed"])
        self.assertEqual(summary["planned"]["points"], 2)
        self.assertEqual(summary["planned"]["completed"], 1)
        self.assertEqual(summary["planned"]["missed"], 1)
        self.assertEqual(summary["planned"]["pending"], 0)
        self.assertEqual(summary["runs"]["late"], 1)
        self.assertEqual(summary["runs"]["on_time"], 0)

    def test_closed_hour_cannot_be_changed(self):
        self.close(HOUR)
        with self.assertRaises(DomainError) as error:
            self.ledger.record(run("run:late", received_at=self.now))
        self.assertEqual(error.exception.code, "precondition_failed")

    def test_hour_cannot_close_before_grace(self):
        self.now = HOUR + timedelta(hours=1, seconds=899)
        with self.assertRaises(DomainError) as error:
            self.close(HOUR)
        self.assertEqual(error.exception.code, "precondition_failed")

    def test_run_identity_is_idempotent_and_changes_conflict(self):
        original = self.ledger.record(run())
        self.assertEqual(self.ledger.record(original), original)
        with self.assertRaises(DomainError) as error:
            self.ledger.record(replace(original, state="failed"))
        self.assertEqual(error.exception.code, "idempotency_conflict")

    def test_plan_scope_and_retry_reference_are_enforced(self):
        with self.assertRaises(DomainError) as error:
            self.ledger.record(run(point_id="point:missing"))
        self.assertEqual(error.exception.code, "incompatible_scope")
        with self.assertRaises(DomainError) as error:
            self.ledger.record(run(
                "run:orphan-retry", retry_of="run:missing"))
        self.assertEqual(error.exception.code, "incompatible_scope")

    def test_utc_boundary_keeps_runs_in_scheduled_hour(self):
        boundary = HOUR + timedelta(hours=1)
        self.ledger.record(run(
            "run:boundary",
            point_id=None,
            scheduled_at=boundary - timedelta(seconds=1),
            received_at=boundary + timedelta(minutes=1),
            source_observed_at=boundary + timedelta(seconds=30),
        ))
        self.assertEqual(
            self.summary(HOUR)["runs"]["total"], 1)
        self.assertEqual(
            self.summary(boundary)["runs"]["total"], 0)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class PersistentHourlyIngestLedgerTests(HourlyIngestLedgerTests):
    def setUp(self):
        super().setUp()

    def test_records_survive_restart_and_hour_close_is_immutable(self):
        self.ledger.record(run())
        reopened = HourlyIngestLedger.persistent(
            self.dsn, lateness_grace_seconds=900,
            clock=lambda: self.now,
        )
        open_summary = reopened.tenant_hour_summary(
            self.tenant, self.source_name, HOUR)
        self.assertEqual(open_summary["runs"]["total"], 1)
        closed = reopened.close_tenant_hour(
            self.tenant, self.source_name, HOUR)
        self.assertTrue(closed["closed"])
        self.assertEqual(
            reopened.tenant_hour_summary(
                self.tenant, self.source_name, HOUR),
            closed,
        )
        with self.assertRaises(DomainError) as error:
            reopened.plan(PlanPoint(
                "point:after-close", self.tenant, self.source_name,
                (HOUR + timedelta(minutes=50)).isoformat(),
            ))
        self.assertEqual(error.exception.code, "precondition_failed")
        with self.assertRaises(DomainError) as error:
            reopened.record(run("run:after-close"))
        self.assertEqual(error.exception.code, "precondition_failed")

    def test_tenant_and_source_scope_is_isolated(self):
        self.ledger.record(run())
        other = PlanPoint(
            "point:other", "tenant:two", self.source_name,
            (HOUR + timedelta(minutes=5)).isoformat(),
        )
        self.ledger.plan(other)
        cross = replace(run("run:cross"), tenant_id="tenant:two")
        with self.assertRaises(DomainError) as error:
            self.ledger.record(cross)
        self.assertEqual(error.exception.code, "incompatible_scope")
        self.ledger.record(replace(
            run("run:other", point_id="point:other"),
            tenant_id="tenant:two",
        ))
        one = self.ledger.tenant_hour_summary(
            self.tenant, self.source_name, HOUR)
        two = self.ledger.tenant_hour_summary(
            "tenant:two", self.source_name, HOUR)
        self.assertEqual(one["runs"]["total"], 1)
        self.assertEqual(two["runs"]["total"], 1)

    def test_cross_tenant_retry_is_rejected(self):
        original = run()
        self.ledger.record(original)
        self.ledger.plan(PlanPoint(
            "point:two", "tenant:two", self.source_name,
            (HOUR + timedelta(minutes=10)).isoformat(),
        ))
        with self.assertRaises(DomainError) as error:
            self.ledger.record(replace(
                run(
                    "run:cross-retry",
                    point_id="point:two",
                    retry_of=original.run_id,
                ),
                tenant_id="tenant:two",
            ))
        self.assertEqual(error.exception.code, "incompatible_scope")


if __name__ == "__main__":
    unittest.main()
