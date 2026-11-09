import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError
from realtime.google_gsc_revisions import (
    BulkExportEvidence, GSCPartition, GSCRevision, GSCRevisionLedger,
)
from realtime.google_registry import RightsRegistry
from realtime.google_rights import Grant
from realtime.google_search_console import SearchConsoleService
from tests.test_google_domain import NOW, registration
from tests.test_google_search_console import evidence, report, row


def partition(**changes):
    values = dict(
        tenant_id="tenant:one", source_adapter="gsc", source_version="1",
        source_api_version="1", contract_ref="fixture:gsc:1",
        dataset_id="fixture:gsc-dataset:1", property_id="sc-domain:example.test",
        template_version="gsc-pages:1", grain=("date", "query", "page"),
        day="2026-11-02", source_timezone="America/Los_Angeles",
        aggregation="byPage", report_kind="top_rows", filters={},
    )
    values.update(changes)
    return GSCPartition(**values)


def bulk_partition(**changes):
    return partition(**{
        "report_kind": "bulk", "export_table": "searchdata_site_impression",
        **changes,
    })


def revision(scope=None, *, version=1, data_state="all", observed_at=NOW,
             received_at=NOW, expires_at=None, clicks=1, complete=True,
             proof=True, rows=None):
    scope = scope or partition()
    bulk = scope.report_kind == "bulk"
    dimensions = {
        "date": scope.day, "query": "alpha", "page": "https://example.test/a",
        "hour": scope.day + "T10:00:00-08:00",
    }
    value = report(
        evidence=evidence(
            adapter_id=scope.source_adapter, adapter_version=scope.source_version,
            source_api_version=scope.source_api_version, contract_ref=scope.contract_ref,
            dataset_id=scope.dataset_id, source_observed_at=observed_at,
            evidence_expires_at=expires_at or NOW + timedelta(hours=1)),
        asset_id=scope.property_id, property_id=scope.property_id,
        template_version=scope.template_version, grain=scope.grain,
        source_timezone=scope.source_timezone, aggregation=scope.aggregation,
        period_start=scope.day,
        period_end=(date.fromisoformat(scope.day) + timedelta(days=1)).isoformat(),
        data_state=data_state,
        report_kind=scope.report_kind, partition=scope.day if bulk else None,
        rows=rows if rows is not None else (row(
            day=scope.day, clicks=clicks,
            dimensions={key: dimensions[key] for key in scope.grain}),),
        response_complete=complete,
        statistical_completeness=(
            "bulk_partition_verified" if bulk and complete
            else "partial" if not complete else "top_rows_only"),
    )
    export_evidence = (
        BulkExportEvidence(
            scope.partition_id, version, "fixture:export-log:success", observed_at,
            "fixture:table-check", received_at, 1)
        if bulk and proof else None
    )
    return GSCRevision(
        scope, version, value, received_at, export_evidence,
        applied_filters=scope.filters,
    )


class GSCPartitionContractTests(unittest.TestCase):
    def test_scope_identity_separates_table_grain_asset_tenant_filters_and_hour(self):
        base = partition()
        variants = [
            replace(base, tenant_id="tenant:two"),
            replace(base, source_version="2"),
            replace(base, dataset_id="fixture:gsc:other"),
            replace(base, property_id="sc-domain:other.test"),
            replace(base, filters={"query": "beta"}),
            replace(base, grain=("date", "page")),
            bulk_partition(),
            bulk_partition(export_table="searchdata_url_impression"),
            replace(base, hour=10, grain=("date", "hour")),
        ]
        self.assertEqual(len({base.partition_id, *(v.partition_id for v in variants)}), 10)
        self.assertEqual(
            replace(base, grain=tuple(base.grain)).partition_id, base.partition_id)
        self.assertNotIn("data_state", base.to_dict())

    def test_partition_validation_is_fail_closed(self):
        for changes in (
                {"day": "20261102"}, {"hour": True}, {"hour": 24},
                {"hour": 10}, {"grain": ("hour",)}, {"source_timezone": "local"},
                {"export_table": "searchdata_url_impression"},
                {"report_kind": "bulk"}, {"filters": {"private": "x"}}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                partition(**changes)

    def test_revision_rejects_mismatched_report_or_filters(self):
        valid = revision()
        for changes in (
                {"source_revision": 0}, {"source_revision": True},
                {"scope": replace(valid.scope, dataset_id="fixture:wrong")},
                {"scope": replace(valid.scope, filters={"query": "different"})},
                {"report": replace(valid.report, period_start="2026-11-01")},
                {"received_at": NOW - timedelta(seconds=1)},
                {"received_at": NOW + timedelta(hours=2)}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                replace(valid, **changes)

    def test_hourly_rows_must_bind_actual_source_day_and_hour(self):
        scope = partition(grain=("date", "hour"), hour=10)
        self.assertEqual(revision(scope).scope.hour, 10)
        with self.assertRaises(DomainError):
            revision(replace(scope, hour=11))
        with self.assertRaises(DomainError):
            revision(scope, rows=(row(dimensions={
                "date": scope.day, "hour": scope.day + "T10:00:00Z"}),))
        with self.assertRaises(DomainError):
            revision(rows=(row(dimensions={
                "date": "2026-11-01", "query": "alpha", "page": "https://example.test/a",
            }),))

    def test_export_log_is_success_evidence_not_failure_status(self):
        scope = bulk_partition()
        with self.assertRaises(DomainError):
            BulkExportEvidence(scope.partition_id, 1, "fixture:log-without-time")
        with self.assertRaises(DomainError):
            BulkExportEvidence(
                scope.partition_id, 1, table_check_ref="fixture:check",
                table_checked_at=NOW)
        with self.assertRaises(DomainError):
            replace(revision(scope), export_evidence=BulkExportEvidence(
                scope.partition_id, 2))
        with self.assertRaises(DomainError):
            replace(revision(), export_evidence=BulkExportEvidence(
                scope.partition_id, 1))
        result = revision(scope, proof=False)
        self.assertIn("missing_successful_export_log", result.exclusions)
        self.assertIn("missing_table_partition_verification", result.exclusions)

    def test_log_without_table_check_or_partial_response_is_not_eligible(self):
        scope = bulk_partition()
        value = revision(scope)
        log_only = BulkExportEvidence(
            scope.partition_id, 1, "fixture:log", NOW)
        self.assertEqual(
            replace(value, export_evidence=log_only).exclusions,
            ["missing_table_partition_verification"])
        self.assertIn("incomplete_response", revision(scope, complete=False).exclusions)
        self.assertEqual(revision(scope).exclusions, [])

    def test_dst_repeated_hours_have_distinct_identity_and_nonexistent_hour_is_rejected(self):
        args = {"day": "2026-11-01", "grain": ("date", "hour"), "hour": 1}
        with self.assertRaises(DomainError):
            partition(**args)
        first = partition(**args, hour_start="2026-11-01T01:00:00-07:00")
        second = partition(**args, hour_start="2026-11-01T01:00:00-08:00")
        self.assertNotEqual(first.partition_id, second.partition_id)
        first_row = row(
            day="2026-11-01", dimensions={
                "date": "2026-11-01", "hour": "2026-11-01T01:00:00-07:00"})
        self.assertEqual(revision(first, rows=(first_row,)).scope.hour, 1)
        with self.assertRaises(DomainError):
            revision(second, rows=(first_row,))
        for start in (None, "2026-03-08T02:00:00-08:00",
                      "2026-03-08T02:00:00-07:00"):
            with self.subTest(start=start), self.assertRaises(DomainError):
                partition(day="2026-03-08", grain=("date", "hour"), hour=2, hour_start=start)

    def test_duplicate_grain_and_inconsistent_empty_partition_proof_are_rejected(self):
        with self.assertRaises(DomainError):
            revision(rows=(row(), row(row_id="row:duplicate-grain")))
        value = revision(bulk_partition())
        with self.assertRaises(DomainError):
            replace(value, export_evidence=replace(
                value.export_evidence, partition_row_count=0))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL required")
class GSCRevisionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ["TEST_DATABASE_URL"]
        self.schema = "gsc_revision_" + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options="-c search_path=" + self.schema)
        self.now = NOW
        self.ledger = GSCRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.initialize()

    def tearDown(self):
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_fresh_final_and_late_revisions_replace_without_summing(self):
        scope = partition()
        self.ledger.record(revision(scope, clicks=2))
        self.ledger.record(revision(scope, version=2, data_state="final", clicks=3))
        self.ledger.record(revision(scope, version=3, data_state="final", clicks=1))
        selected = self.ledger.current(scope)
        self.assertEqual(selected["source_revision"], 3)
        self.assertEqual(selected["metadata"]["report"]["report_hash"],
                         revision(scope, version=3, data_state="final", clicks=1)
                         .metadata()["report"]["report_hash"])
        history = self.ledger.history(scope)
        self.assertEqual(len(history), 3)
        self.assertEqual(len({r["metadata"]["report"]["report_hash"] for r in history}), 3)
        self.assertTrue(all("rows" not in r["metadata"]["report"] for r in history))
        self.assertEqual(sum(row["selected"] for row in history), 1)
        self.assertEqual(
            self.ledger.current(scope)["metadata"]["report"]["period"]["start"], scope.day)

    def test_preliminary_arriving_after_final_never_downgrades_it(self):
        scope = partition()
        self.ledger.record(revision(scope, version=2, data_state="final", clicks=3))
        self.ledger.record(revision(scope, version=1, clicks=9))
        self.ledger.record(revision(scope, version=3, data_state="all", clicks=10))
        self.assertEqual(self.ledger.current(scope)["source_revision"], 2)
        self.ledger.record(revision(scope, version=4, data_state="final", complete=False))
        self.assertEqual(self.ledger.current(scope)["source_revision"], 2)
        self.assertFalse(self.ledger.history(scope)[-1]["eligible"])

    def test_exact_retry_idempotent_and_payload_conflict_preserves_history(self):
        value = revision()
        first = self.ledger.record(value)
        self.assertEqual(first, self.ledger.record(value))
        with self.assertRaises(DomainError) as error:
            self.ledger.record(revision(clicks=2))
        self.assertEqual(error.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.ledger.history(value.scope)), 1)

    def test_concurrent_retries_and_conflicts_are_serialized(self):
        value = revision()
        with ThreadPoolExecutor(max_workers=4) as executor:
            outputs = list(executor.map(lambda _: self.ledger.record(value), range(8)))
        self.assertEqual(len({o["payload_id"] for o in outputs}), 1)
        self.assertEqual(len(self.ledger.history(value.scope)), 1)

        def conflicting(clicks):
            try:
                return self.ledger.record(revision(version=2, clicks=clicks))["source_revision"]
            except DomainError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outputs = list(executor.map(conflicting, [3, 4]))
        self.assertCountEqual(outputs, [2, "idempotency_conflict"])
        self.assertEqual(len(self.ledger.history(value.scope)), 2)

    def test_restart_preserves_versions_and_tenant_table_isolation(self):
        scopes = [partition(), partition(tenant_id="tenant:two"), bulk_partition(),
                  bulk_partition(export_table="searchdata_url_impression")]
        for i, scope in enumerate(scopes):
            self.ledger.record(revision(scope, clicks=i + 1, data_state="final"))
        self.ledger = GSCRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.initialize()
        for i, scope in enumerate(scopes):
            self.assertEqual(
                self.ledger.current(scope)["metadata"]["report"]["report_hash"],
                revision(scope, clicks=i + 1, data_state="final").metadata()["report"]["report_hash"])
        self.assertIsNone(self.ledger.current(partition(tenant_id="tenant:unknown")))

    def test_success_log_alone_does_not_complete_table_or_peer_table(self):
        scopes = [bulk_partition(), bulk_partition(export_table="searchdata_url_impression")]
        for scope in scopes:
            self.ledger.expect(scope, deadline_at=NOW - timedelta(minutes=1))
        self.ledger.record(revision(scopes[0], data_state="final"))
        url = revision(scopes[1], data_state="final")
        self.ledger.record(replace(url, export_evidence=BulkExportEvidence(
            scopes[1].partition_id, 1, "fixture:log:success", NOW)))
        audit = self.ledger.audit_bulk(scopes)
        self.assertFalse(audit["both_tables_final"])
        self.assertEqual([r["state"] for r in audit["tables"]], ["verified_final", "missing_overdue"])
        self.assertEqual(audit["tables"][1]["latest_exclusions"], ["missing_table_partition_verification"])
        self.assertFalse(audit["tables"][1]["export_log_failure_inferred"])
        self.ledger.record(revision(scopes[1], data_state="final"))
        self.assertTrue(self.ledger.audit_bulk(scopes)["both_tables_final"])
        self.assertEqual(len(self.ledger.history(scopes[1])), 1)
        self.assertEqual(self.ledger.current(scopes[1])["source_revision"], 1)
        self.assertIsNotNone(self.ledger.current(scopes[1])["verification"]["table_check_ref"])
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute(
                """SELECT count(*) FROM sf_google_gsc_partition_checks
                   WHERE partition_id=%s""", (scopes[1].partition_id,)).fetchone()[0], 2)

    def test_missed_day_retry_abandonment_and_recovery_are_explicit(self):
        scopes = [bulk_partition(), bulk_partition(export_table="searchdata_url_impression")]
        self.assertEqual(self.ledger.audit_bulk(scopes)["tables"][0]["state"], "unplanned")
        for scope in scopes:
            self.ledger.expect(scope, deadline_at=NOW + timedelta(minutes=1))
        self.assertEqual(self.ledger.audit_bulk(scopes)["tables"][0]["state"], "pending")
        self.now += timedelta(minutes=2)
        self.assertEqual(self.ledger.audit_bulk(scopes)["tables"][0]["state"], "missing_overdue")
        for i, state in enumerate(("failed", "retry_scheduled", "abandoned")):
            self.ledger.note_attempt(
                scopes[0], attempt_id=f"fixture:attempt:{i}", state=state,
                attempted_at=NOW + timedelta(seconds=i), reason_ref="fixture:reason")
        self.assertEqual(self.ledger.audit_bulk(scopes)["tables"][0]["state"], "abandoned")
        self.ledger.record(revision(scopes[0], data_state="final"))
        self.assertEqual(self.ledger.audit_bulk(scopes)["tables"][0]["state"], "verified_final")

    def test_plan_and_attempt_conflicts_cannot_rewrite_audit(self):
        scope = bulk_partition()
        self.ledger.expect(scope, deadline_at=NOW)
        self.ledger.expect(scope, deadline_at=NOW)
        with self.assertRaises(DomainError):
            self.ledger.expect(scope, deadline_at=NOW + timedelta(seconds=1))
        kwargs = dict(attempt_id="fixture:attempt", state="abandoned",
                      attempted_at=NOW, reason_ref="fixture:retry-exhausted")
        self.ledger.note_attempt(scope, **kwargs)
        self.ledger.note_attempt(scope, **kwargs)
        with self.assertRaises(DomainError):
            self.ledger.note_attempt(scope, **{**kwargs, "state": "failed"})
        with self.assertRaises(DomainError):
            self.ledger.note_attempt(
                replace(scope, tenant_id="tenant:two"), **kwargs)
        with self.assertRaises(DomainError):
            self.ledger.audit_bulk([scope, replace(scope, tenant_id="tenant:two")])
        with self.assertRaises(DomainError):
            self.ledger.audit_bulk([
                scope, bulk_partition(export_table="searchdata_url_impression",
                                      property_id="sc-domain:other.test")])

    def test_expired_final_does_not_resurrect_preliminary_and_details_are_purgeable(self):
        scope = partition()
        self.ledger.record(revision(scope, expires_at=NOW + timedelta(hours=2)))
        self.ledger.record(revision(
            scope, version=2, data_state="final", expires_at=NOW + timedelta(minutes=1)))
        self.now += timedelta(minutes=2)
        with self.assertRaises(DomainError) as error:
            self.ledger.current(scope)
        self.assertEqual(error.exception.code, "expired")
        self.assertEqual(self.ledger.purge_expired(), 1)
        self.assertEqual(self.ledger.purge_expired(), 0)
        history = self.ledger.history(scope)
        self.assertIsNone(history[1]["metadata"])
        self.assertTrue(history[1]["selected"])
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute(
                """SELECT metadata,payload_id FROM sf_google_gsc_partition_revisions
                   WHERE source_revision=2""").fetchone()[0], None)
        with self.assertRaises(DomainError):
            self.ledger.record(revision(scope, version=3,
                                        expires_at=NOW + timedelta(minutes=1)))

    def test_zero_rows_are_valid_only_with_explicit_completed_source_proof(self):
        scope = bulk_partition()
        empty = revision(scope, rows=(), data_state="final")
        empty = replace(empty, export_evidence=replace(
            empty.export_evidence, partition_row_count=0))
        self.ledger.record(empty)
        self.assertEqual(self.ledger.current(scope)["metadata"]["report"]["row_count"], 0)
        self.assertTrue(self.ledger.current(scope)["eligible"])

    def test_api_top_rows_never_becomes_bulk_completeness_through_revision(self):
        scope = partition()
        self.ledger.record(revision(scope, data_state="final"))
        self.assertEqual(
            self.ledger.current(scope)["metadata"]["report"]["statistical_completeness"],
            "top_rows_only")
        self.assertIsNone(self.ledger.current(bulk_partition()))

    def test_caller_transaction_rollback_leaves_no_source_revision_or_check(self):
        value = revision()
        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as db:
            self.ledger.record(value, db=db)
            db.rollback()
        self.assertEqual(self.ledger.history(value.scope), [])
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM sf_google_gsc_partition_checks").fetchone()[0], 0)

    def test_ledger_keeps_hash_and_audit_not_a_second_raw_report(self):
        marker = "private-row-marker-never-persisted"
        value = revision(rows=(row(dimensions={
            "date": "2026-11-02", "query": marker,
            "page": "https://example.test/" + marker,
        }),))
        self.ledger.record(value)
        with psycopg.connect(self.dsn) as db:
            stored = db.execute(
                """SELECT metadata::text FROM sf_google_gsc_partition_revisions""").fetchone()[0]
        self.assertNotIn(marker, stored)
        self.assertNotIn('"clicks"', stored)
        self.assertNotIn('"rows"', stored)
        meta = self.ledger.current(value.scope)["metadata"]
        self.assertFalse(meta["raw_report_retained"])
        self.assertFalse(meta["platform_readback_verified"])
        self.assertEqual(meta["report"]["row_count"], 1)

    def test_ingestion_requires_source_approval_asset_storage_rights_and_expiry(self):
        self.registry = RightsRegistry(self.dsn)
        self.registry.initialize()
        registered = replace(
            registration(), adapter_id="gsc", source_api_version="1",
            production_approved=True,
            field_rights=(replace(
                registration().field_rights[0], field="gsc_report",
                allowed_uses=frozenset({"internal_display", "derived_store"})),))
        self.registry.save_source(
            "tenant:one", registered, expected_revision=0, actor_ref="fixture:operator", now=NOW)
        allowed = Grant(
            tenant_id="tenant:one", asset_id="sc-domain:example.test",
            grant_id="grant:gsc", version="1",
            source_adapter="gsc", source_version="1",
            fields=frozenset({"gsc_report"}), uses=frozenset({"internal_display"}),
            expires_at=NOW + timedelta(hours=2))
        self.registry.save_grant(
            allowed, expected_revision=0, actor_ref="fixture:operator", now=NOW)
        principal = self.registry.principal("tenant:one", now=NOW)
        adapter = type("Fixture", (), {
            "contract_ref": "fixture:gsc:1", "dataset_id": "fixture:gsc-dataset:1",
        })()
        service = SearchConsoleService(
            self.registry, adapter, cursor_codec=None, clock=lambda: self.now,
            revision_ledger=self.ledger)
        with self.assertRaises(DomainError) as error:
            service.ingest_revision(principal, revision(), now=NOW)
        self.assertEqual(error.exception.code, "use_not_permitted")
        self.assertEqual(self.ledger.history(partition()), [])
        allowed = replace(allowed, uses=frozenset({"internal_display", "derived_store"}), version="2")
        self.registry.save_grant(
            allowed, expected_revision=1, actor_ref="fixture:operator", now=NOW)
        stale = principal
        principal = self.registry.principal("tenant:one", now=NOW)
        with self.assertRaises(DomainError):
            service.ingest_revision(stale, revision(), now=NOW)
        self.assertTrue(service.ingest_revision(principal, revision(), now=NOW)["eligible"])
        with self.assertRaises(DomainError):
            service.ingest_revision(
                replace(principal, tenant_id="tenant:two"), revision(version=2), now=NOW)
        with self.assertRaises(DomainError):
            service.ingest_revision(principal, revision(
                version=2, expires_at=NOW + timedelta(days=5)), now=NOW)
        self.registry.save_source(
            "tenant:one", replace(registered, production_approved=False),
            expected_revision=1, actor_ref="fixture:operator", now=NOW)
        with self.assertRaises(DomainError):
            service.ingest_revision(principal, revision(version=2), now=NOW)
        self.assertEqual(len(self.ledger.history(partition())), 1)


if __name__ == "__main__":
    unittest.main()
