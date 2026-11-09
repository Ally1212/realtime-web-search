"""Synthetic Merchant policies prove local fail-closed contracts only."""
import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_domain import DomainError, MetricValue
from realtime.google_evidence import CursorCodec
from realtime.google_merchant import (
    MerchantAccessPolicy,
    MerchantEvidence,
    MerchantResult,
    MerchantRow,
    MerchantScope,
    MerchantService,
)
from realtime.google_rights import FieldRight, Grant, Principal
from tests.test_google_domain import NOW, registration


def scope(**changes):
    value = MerchantScope(
        kind="report",
        merchant_id="1234567890",
        template_version="merchant-products:1",
        report_version="report:1",
        period_start="2026-10-01",
        period_end="2026-11-01",
        source_timezone="America/Los_Angeles",
        country="US",
        currency="USD",
    )
    return replace(value, **changes)


def parameters(**changes):
    value = {
        "source": "merchant",
        "source_version": "1",
        "asset": "merchant:1234567890",
        "merchant_id": "1234567890",
        "template_version": "merchant-products:1",
        "report_version": "report:1",
        "period_start": "2026-10-01",
        "period_end": "2026-11-01",
        "source_timezone": "America/Los_Angeles",
        "country": "US",
        "currency": "USD",
        "access_id": "merchant-access:1",
    }
    value.update(changes)
    return value


def evidence(**changes):
    value = MerchantEvidence(
        adapter_id="merchant",
        adapter_version="1",
        source_api_version="merchant-api-v1",
        contract_ref="fixture:merchant-contract",
        dataset_id="fixture:merchant-dataset",
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        revision="revision:1",
    )
    return replace(value, **changes)


def policy(**changes):
    value = MerchantAccessPolicy(
        access_id="merchant-access:1",
        source_adapter="merchant",
        source_version="1",
        merchant_id="1234567890",
        caller_subject_refs=("caller:merchant",),
        approved_templates=("merchant-products:1",),
        approved_report_versions=("report:1",),
        approved_scopes=(scope().to_dict(),),
        approval_ref="fixture:approval:merchant",
        expires_at=(NOW + timedelta(hours=2)).isoformat(),
        approved=True,
        production_approved=True,
    )
    return replace(value, **changes)


def row(report_scope=None, *, row_id="row:1", metric_id="impressions",
        value=10, row_role="snapshot"):
    report_scope = report_scope or scope()
    dimensions = {
        "date": "2026-10-15",
        "product_id": "product:" + row_id,
        "country": "US",
    }
    return MerchantRow(
        row_id=row_id,
        dimensions=dimensions,
        metrics=(MetricValue(
            value=value,
            unit=(
                "money_micros" if metric_id in {"cost_micros", "sales_micros"}
                else "count"),
            provenance_kind="authorized_reported",
            measurement_nature="exact_in_scope",
            availability="true_zero" if value == 0 else "available",
            metric_scope={
                **report_scope.to_dict(),
                "row_id": row_id,
                "row_role": row_role,
                "dimensions": dimensions,
                "revision": "revision:1",
                "metric": metric_id,
            },
            definition_version="fixture:merchant-metric",
            additivity="additive_in_scope",
        ),),
        row_role=row_role,
        product_title="Approved product title",
    )


def report(report_scope=None, **changes):
    report_scope = report_scope or scope()
    value = dict(
        evidence=evidence(),
        scope=report_scope,
        rows=(row(report_scope),),
        response_complete=True,
        limitations=("fixture_not_a_real_merchant_asset",),
    )
    value.update(changes)
    return MerchantResult(**value)


class MerchantAdapter:
    contract_ref = "fixture:merchant-contract"
    dataset_id = "fixture:merchant-dataset"

    def __init__(self):
        self.output = report()
        self.calls = []
        self.on_read = None

    def read_report(self, tenant_id, request):
        self.calls.append(("read", tenant_id, request))
        if self.on_read is not None:
            self.on_read()
        return self.output


class MerchantRegistry:
    def __init__(self):
        self.source_value = registration(
            adapter_id="merchant",
            source_api_version="merchant-api-v1",
            production_approved=True,
            field_rights=(
                FieldRight(
                    "merchant_report",
                    frozenset({"internal_display"}),
                    NOW + timedelta(hours=2),
                ),
            ),
        )
        self.grant_value = Grant(
            tenant_id="tenant:merchant",
            asset_id="merchant:1234567890",
            grant_id="grant:merchant",
            version="1",
            fields=frozenset({"merchant_report"}),
            uses=frozenset({"internal_display"}),
            expires_at=NOW + timedelta(hours=2),
            source_adapter="merchant",
            source_version="1",
        )
        self.policy = policy()
        self.current = True

    def source(self, tenant_id, source_id, version):
        if (tenant_id, source_id, version) != (
                "tenant:merchant", "merchant", "1"):
            raise DomainError("not_authorized", "No source", 403)
        return self.source_value

    def grant(self, tenant_id, grant_id):
        if (tenant_id, grant_id) != ("tenant:merchant", "grant:merchant"):
            raise DomainError("not_authorized", "No grant", 403)
        return self.grant_value

    def merchant_access(self, tenant_id, access_id):
        if (tenant_id, access_id) != (
                "tenant:merchant", "merchant-access:1"):
            raise DomainError("not_authorized", "No Merchant policy", 403)
        return self.policy

    def assert_current(self, principal, *, now):
        if not self.current:
            raise DomainError("not_authorized", "Rights changed", 403)


class MerchantContractTests(unittest.TestCase):
    def test_rows_preserve_micros_revision_and_unknown_title(self):
        money = row(metric_id="cost_micros", value=1000001)
        payload = report(rows=(money,)).to_dict()
        self.assertEqual(
            payload["rows"][0]["metrics"]["cost_micros"]["value"], 1000001)
        unknown = row()
        unknown = replace(unknown, product_title=None)
        self.assertIsNone(report(rows=(unknown,)).to_dict()["rows"][0]["product_title"])
        delta = row(metric_id="cost_micros", value=-1, row_role="revision_delta")
        self.assertEqual(
            report(rows=(delta,)).rows[0].row_role, "revision_delta")

    def test_wrong_content_style_dimensions_units_and_duplicate_grain_fail(self):
        with self.assertRaises(DomainError):
            report(rows=(replace(
                row(), dimensions={
                    "date": "2026-10-15",
                    "legacy_content_id": "not-current-api",
                }),))
        first = row()
        second = row(row_id="row:2")
        second = replace(second, dimensions=first.dimensions)
        with self.assertRaises(DomainError):
            report(rows=(first, second))
        metric = first.metrics[0]
        with self.assertRaises(DomainError):
            report(rows=(replace(
                first, metrics=(replace(metric, unit="currency"),)),))

    def test_period_timezone_and_nonnegative_snapshot_are_enforced(self):
        with self.assertRaises(DomainError):
            scope(source_timezone="local")
        with self.assertRaises(DomainError):
            scope(period_start="2026-11-01", period_end="2026-10-01")
        with self.assertRaises(DomainError):
            report(rows=(row(value=-1),))


class MerchantServiceTests(unittest.TestCase):
    def setUp(self):
        self.registry = MerchantRegistry()
        self.adapter = MerchantAdapter()
        self.principal = Principal(
            "tenant:merchant", frozenset({"grant:merchant"}), "1",
            subject_ref="caller:merchant")
        self.clock = NOW + timedelta(seconds=1)
        self.service = MerchantService(
            self.registry, self.adapter,
            cursor_codec=CursorCodec(b"m" * 32), clock=lambda: self.clock)

    def read(self, **changes):
        return self.service.read(
            self.principal, "/merchant/reports",
            parameters(**changes), now=self.clock)

    def test_authorized_read_and_cursor_are_snapshot_bound(self):
        self.adapter.output = report(rows=(row(row_id="a"), row(row_id="b")))
        first = self.read(limit="1")
        self.assertEqual(first["rows"][0]["row_id"], "a")
        second = self.read(limit="1", cursor=first["next_cursor"])
        self.assertEqual(second["rows"][0]["row_id"], "b")
        self.assertEqual(
            self.adapter.calls[0][2]["scope"]["merchant_id"], "1234567890")
        self.adapter.output = report(rows=(
            row(row_id="a"), row(row_id="b", value=99)))
        with self.assertRaises(DomainError):
            self.read(limit="1", cursor=first["next_cursor"])

    def test_raw_source_proxy_and_scope_switches_fail_before_read(self):
        for changes in (
                {"method": "Search"},
                {"query": "SELECT * FROM product"},
                {"fields": "all"},
                {"template_version": "other"},
                {"asset": "merchant:9999999999"}):
            self.adapter.calls.clear()
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.read(**changes)
            self.assertEqual(self.adapter.calls, [])
        for changes in ({"access_id": "other"}, {"report_version": "other"}):
            self.adapter.calls.clear()
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.read(**changes)
            self.assertEqual(self.adapter.calls, [])

    def test_future_expired_wrong_dataset_and_untyped_results_fail_closed(self):
        for value, code in (
                (report(evidence=evidence(
                    source_observed_at=(NOW + timedelta(minutes=1)).isoformat())),
                 "incompatible_scope"),
                (report(evidence=evidence(
                    evidence_expires_at=(NOW + timedelta(milliseconds=500)).isoformat())),
                 "expired"),
                (report(evidence=evidence(dataset_id="other")), "incompatible_scope"),
                (object(), "source_error")):
            self.adapter.output = value
            with self.subTest(value=value), self.assertRaises(DomainError) as error:
                self.read()
            self.assertEqual(error.exception.code, code)

    def test_revocation_during_read_discards_result(self):
        self.adapter.on_read = lambda: setattr(self.registry, "current", False)
        with self.assertRaises(DomainError) as error:
            self.read()
        self.assertEqual(error.exception.code, "not_authorized")
        self.assertEqual(len(self.adapter.calls), 1)


if __name__ == "__main__":
    unittest.main()
