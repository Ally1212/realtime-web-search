import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError
from realtime.google_metrics_revisions import (
    MetricPartition, MetricRevisionLedger, ReportRevision, RereadPolicy,
    SourceAccounting,
)
from tests.test_google_domain import NOW


def partition(**changes):
    values = dict(
        tenant_id="tenant:one", family="ga4", source_adapter="ga4",
        source_version="1", source_api_version="1", contract_ref="fixture:ga4:1",
        dataset_id="fixture:ga4-dataset:1", asset_id="properties/123",
        template_version="ga4-pages:1", report_version="1",
        period_start="2026-11-01", period_end="2026-11-04",
        source_timezone="America/Los_Angeles", dimensions=("date", "page"),
        metrics=("sessions", "conversions"), scope={"property": "fixture"},
    )
    values.update(changes)
    return MetricPartition(**values)


def policy(**changes):
    values = dict(
        family="ga4", policy_version="1", preliminary_window_seconds=86400,
        final_after_seconds=3600, minimum_reread_seconds=60, max_rereads=2,
        lag_reread_allowed=False, note="fixture source timing",
    )
    values.update(changes)
    return RereadPolicy(**values)


def accounting(**changes):
    values = dict(
        reread_condition="initial_read", revision_fee_micros=None,
        query_scans=None, tokens_consumed=None, rows_scanned=None,
            bytes_scanned=None, accounting_basis=None, fee_currency=None,
    )
    values.update(changes)
    return SourceAccounting(**values)


def revision(scope=None, *, oid="observation:1", stage="intraday",
             source_revision="r1", observed_at=NOW, received_at=NOW,
             expires_at=None, payload=None, accounting_value=None,
             reread_of=None, preliminary=True, complete=True):
    scope = scope or partition()
    return ReportRevision(
        scope, oid, stage, source_revision, observed_at, received_at,
        expires_at or NOW + timedelta(hours=2),
        payload or {"source_revision": source_revision, "rows": [{"m": 1}]},
        accounting_value or accounting(), complete, preliminary, reread_of,
    )


class MetricRevisionContractTests(unittest.TestCase):
    def test_partition_identity_covers_asset_template_window_dimensions_metrics_and_scope(self):
        base = partition()
        variants = [
            base,
            replace(base, tenant_id="tenant:two"),
            replace(base, asset_id="properties/456"),
            replace(base, template_version="ga4-pages:2"),
            replace(base, period_end="2026-11-05"),
            replace(base, dimensions=("date",)),
            replace(base, metrics=("sessions",)),
            replace(base, scope={"property": "other"}),
        ]
        self.assertEqual(len({value.partition_id for value in variants}), len(variants))
        self.assertEqual(
            replace(base, dimensions=tuple(reversed(base.dimensions))).partition_id,
            base.partition_id)

    def test_unknown_timing_is_not_invented_and_metric_limited_lag_needs_approval(self):
        self.assertIsNone(policy(preliminary_window_seconds=None).preliminary_window_seconds)
        for changes in (
                {"preliminary_window_seconds": -1}, {"max_rereads": True},
                {"lag_reread_allowed": True},
                {"lag_only_metrics": ("conversions",)},
                {"family": "unknown"}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                policy(**changes)
        approved = policy(lag_reread_allowed=True, lag_only_metrics=("conversions",))
        self.assertEqual(approved.lag_only_metrics, ("conversions",))

    def test_preliminary_final_stages_are_family_specific(self):
        with self.assertRaises(DomainError):
            revision(partition(family="ads"), stage="intraday")
        with self.assertRaises(DomainError):
            revision(stage="final", preliminary=True)
        final = revision(stage="final", preliminary=False)
        self.assertEqual(final.eligibility(now=NOW), [])
        unknown = revision(
            partition(family="merchant"), stage="current", preliminary=None)
        self.assertIsNone(unknown.preliminary)
        with self.assertRaises(DomainError):
            revision(observed_at=NOW + timedelta(seconds=1), received_at=NOW)
        with self.assertRaises(DomainError):
            revision(expires_at=NOW)

    def test_source_accounting_unknown_is_distinct_from_true_zero(self):
        unknown = accounting(reread_condition="manual_reread")
        self.assertIsNone(unknown.query_scans)
        zero = accounting(query_scans=0, bytes_scanned=0, accounting_basis="source receipt")
        self.assertEqual(zero.query_scans, 0)
        with self.assertRaises(DomainError):
            accounting(query_scans=-1)
        with self.assertRaises(DomainError):
            accounting(reread_condition="unapproved")
        with self.assertRaises(DomainError):
            accounting(fee_currency="usd")

    def test_hash_payload_is_explicit_and_immutable_revision_is_required(self):
        with self.assertRaises(DomainError):
            revision(payload={"rows": [{"m": 1}]})
        with self.assertRaises(DomainError):
            revision(source_revision="")
        first = revision()
        self.assertEqual(first.payload_id, revision().payload_id)
        self.assertNotEqual(
            first.payload_id,
            revision(payload={"source_revision": "r1", "rows": [{"m": 2}]}).payload_id)
        self.assertFalse(hasattr(first, "rows"))
        self.assertEqual(
            first.payload_id,
            revision(received_at=NOW + timedelta(seconds=1)).payload_id)

    def test_source_revision_payload_mismatch_is_ineligible(self):
        self.assertIn(
            "payload_source_revision_mismatch",
            revision(
                source_revision="r2",
                payload={"source_revision": "r1", "rows": [{"m": 1}]},
            ).eligibility(now=NOW))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL required")
class MetricRevisionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ["TEST_DATABASE_URL"]
        self.schema = "metric_revisions_" + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options="-c search_path=" + self.schema)
        self.now = NOW + timedelta(minutes=5)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.initialize()
        self.ga4 = partition()
        self.ledger.policy(self.ga4, policy=policy())

    def tearDown(self):
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_intraday_and_final_are_separate_and_counts_are_not_summed(self):
        first = revision(accounting_value=accounting(
            reread_condition="scheduled_reread", query_scans=1))
        self.ledger.record(first)
        final = revision(
            oid="observation:2", stage="final", source_revision="r2",
            observed_at=NOW + timedelta(hours=1), received_at=NOW + timedelta(hours=1),
            preliminary=False, reread_of="observation:1")
        self.now = NOW + timedelta(hours=1)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        result = self.ledger.record(final)
        self.assertTrue(result["eligible"])
        history = self.ledger.history(self.ga4)
        self.assertEqual([row["accounting"]["preliminary"] for row in history], [True, False])
        self.assertEqual(len({row["payload_id"] for row in history}), 2)
        self.assertTrue(all(not row["payload_retained"] for row in history))
        account = self.ledger.account_partition(self.ga4)
        self.assertEqual(account["preliminary_observations"], 1)
        self.assertEqual(account["final_observations"], 1)
        self.assertEqual(account["rereads"], 1)
        self.assertFalse(account["unknown_is_zero"])

    def test_exact_retry_idempotent_and_changed_payload_conflicts(self):
        value = revision()
        first = self.ledger.record(value)
        self.assertEqual(first, self.ledger.record(value))
        with self.assertRaises(DomainError) as error:
            self.ledger.record(revision(payload={"source_revision": "r1", "rows": [{"m": 2}]}))
        self.assertEqual(error.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.ledger.history(self.ga4)), 1)

    def test_same_source_revision_cannot_be_a_new_observation(self):
        self.ledger.record(revision())
        with self.assertRaises(DomainError) as error:
            self.ledger.record(revision(oid="observation:2"))
        self.assertEqual(error.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.ledger.history(self.ga4)), 1)
        self.now = NOW + timedelta(hours=2)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.record(revision(
            oid="observation:3", stage="final", preliminary=False,
            source_revision="r2", observed_at=NOW + timedelta(hours=1),
            received_at=NOW + timedelta(hours=1),
            expires_at=NOW + timedelta(hours=3), reread_of="observation:1"))
        self.assertEqual(len(self.ledger.history(self.ga4)), 2)

    def test_ads_lag_scope_and_source_window_are_enforced(self):
        ads = partition(family="ads", metrics=("conversions",))
        ads_policy = policy(
            family="ads", lag_reread_allowed=True,
            lag_only_metrics=("conversions",))
        self.ledger.policy(ads, policy=ads_policy)
        self.ledger.record(revision(ads, stage="current", preliminary=None))
        with self.assertRaises(DomainError) as error:
            self.ledger.record(revision(
                ads, oid="observation:2", stage="conversion_adjusted",
                source_revision="r2", observed_at=NOW + timedelta(seconds=10),
                received_at=NOW + timedelta(seconds=10), preliminary=None,
                accounting_value=accounting(reread_condition="lag_reread"),
                reread_of="observation:1"))
        self.assertIn("reread_too_early", str(error.exception))

    def test_final_before_source_window_is_rejected_and_metric_scope_is_explicit(self):
        ads = partition(family="ads", metrics=("conversions", "clicks"))
        self.ledger.policy(ads, policy=policy(
            family="ads", lag_reread_allowed=True,
            lag_only_metrics=("conversions",)))
        self.ledger.record(revision(ads, stage="current", preliminary=None))
        with self.assertRaises(DomainError):
            self.ledger.record(revision(
                ads, oid="observation:2", stage="conversion_adjusted",
                source_revision="r2", preliminary=None,
                accounting_value=accounting(reread_condition="lag_reread"),
                reread_of="observation:1"))
        with self.assertRaises(DomainError):
            self.ledger.record(revision(
                ads, oid="observation:3", stage="conversion_adjusted",
                source_revision="r3", observed_at=NOW + timedelta(hours=2),
                received_at=NOW + timedelta(hours=2), preliminary=None,
                accounting_value=accounting(reread_condition="lag_reread"),
                reread_of="observation:1"))

    def test_merchant_revision_stage_and_missing_accounting_stay_unknown(self):
        merchant = partition(
            family="merchant", metrics=("clicks", "sales_micros"),
            asset_id="merchant:1234567890")
        self.ledger.policy(merchant, policy=policy(family="merchant"))
        self.ledger.record(revision(
            merchant, stage="current", preliminary=None,
            accounting_value=accounting(reread_condition="initial_read")))
        with self.assertRaises(DomainError):
            self.ledger.record(revision(
                merchant, oid="observation:2", stage="revised",
                source_revision="r2", observed_at=NOW + timedelta(seconds=1),
                received_at=NOW + timedelta(seconds=1), preliminary=None,
                reread_of="observation:1"))
        account = self.ledger.account_partition(merchant)
        self.assertIsNone(account["source_accounting"]["query_scans"]["value"])
        self.assertEqual(account["source_accounting"]["query_scans"]["availability"], "unknown")
        self.assertEqual(account["source_accounting"]["query_scans"]["missing_observations"], 1)
        self.now = NOW + timedelta(hours=3)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.record(revision(
            merchant, oid="observation:3", stage="revised", source_revision="r2",
            observed_at=NOW + timedelta(hours=2), received_at=NOW + timedelta(hours=2),
            expires_at=NOW + timedelta(hours=3, minutes=1),
            preliminary=None, reread_of="observation:1"))
        self.assertEqual(len(self.ledger.history(merchant)), 2)

    def test_reread_budget_and_policy_scope_are_fail_closed(self):
        self.ledger.record(revision())
        for index in (2, 3, 4):
            try:
                self.now = NOW + timedelta(hours=index)
                self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
                self.ledger.record(revision(
                    oid=f"observation:{index}", source_revision=f"r{index}",
                    observed_at=NOW + timedelta(hours=index),
                    received_at=NOW + timedelta(hours=index),
                    expires_at=NOW + timedelta(hours=5),
                    stage="final" if index == 2 else "intraday",
                    preliminary=index != 2, reread_of="observation:1"))
            except DomainError as error:
                self.assertEqual(error.code, "quota_exceeded")
                break
        else:
            self.fail("Configured reread budget was not enforced")
        with self.assertRaises(DomainError):
            self.ledger.policy(self.ga4, policy=policy(max_rereads=99))

    def test_restart_keeps_history_and_expiry_does_not_return_payload(self):
        self.ledger.record(revision(expires_at=NOW + timedelta(minutes=8)))
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.initialize()
        self.assertEqual(len(self.ledger.history(self.ga4)), 1)
        self.now += timedelta(minutes=10)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        history = self.ledger.history(self.ga4)
        self.assertTrue(history[0]["expired"])
        self.assertIsNone(history[0]["payload"])
        self.assertFalse(history[0]["platform_readback_verified"])

    def test_false_zero_estimate_is_never_substituted_for_missing_cost_or_scans(self):
        self.ledger.record(revision())
        accounting_result = self.ledger.account_partition(self.ga4)
        for name in (
                "revision_fee_micros", "query_scans", "tokens_consumed",
                "rows_scanned", "bytes_scanned"):
            value = accounting_result["source_accounting"][name]
            self.assertIsNone(value["value"])
            self.assertEqual(value["availability"], "unknown")
            self.assertEqual(value["missing_observations"], 1)
            self.assertNotEqual(value["value"], 0)

    def test_costs_from_different_bases_or_currencies_are_not_summed(self):
        self.ledger.record(revision(
            accounting_value=accounting(
                query_scans=2, accounting_basis="API quota", fee_currency="USD")))
        self.now = NOW + timedelta(hours=2)
        self.ledger = MetricRevisionLedger(self.dsn, clock=lambda: self.now)
        self.ledger.record(revision(
            oid="observation:2", stage="final", preliminary=False,
            source_revision="r2", observed_at=NOW + timedelta(hours=1),
            received_at=NOW + timedelta(hours=1),
            expires_at=NOW + timedelta(hours=3),
            accounting_value=accounting(
                query_scans=3, accounting_basis="BQ scan", fee_currency="USD")))
        account = self.ledger.account_partition(self.ga4)
        scans = account["source_accounting"]["query_scans"]
        self.assertIsNone(scans["value"])
        self.assertEqual(scans["availability"], "mixed_basis")
        self.assertEqual(
            [group["value"] for group in scans["basis_groups"]], [2, 3])
        self.assertEqual(
            [group["accounting_basis"] for group in scans["basis_groups"]],
            ["API quota", "BQ scan"])


if __name__ == "__main__":
    unittest.main()
