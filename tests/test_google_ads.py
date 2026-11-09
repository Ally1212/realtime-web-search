"""Synthetic policies/results prove local gates, not Google project approval."""
import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_ads import (
    AdsAccessPolicy, AdsBinding, AdsEvidence, AdsResult, AdsRow,
    AdsScope, AdsService, METRIC_UNITS,
)
from realtime.google_domain import DomainError, MetricValue
from realtime.google_evidence import CursorCodec
from realtime.google_rights import FieldRight, Grant, Principal
from tests.test_google_domain import NOW, registration


def binding(**changes):
    value = AdsBinding(
        tenant_id="tenant:ads",
        business_subject_ref="business:one",
        cloud_project_ref="projects/one",
        oauth_subject_ref="oauth:one",
        customer_id="1234567890",
        access_level="Basic",
        source_api_version="fixture-ads-api",
    )
    return replace(value, **changes)


def policy(**changes):
    value = AdsAccessPolicy(
        access_id="access:one",
        source_adapter="ads",
        source_version="1",
        binding=binding(),
        caller_subject_refs=("caller:one",),
        modes=("internal_analysis", "report_service"),
        approved_plans=("plan:1",),
        approved_templates=("report:1",),
        approved_scopes=(scope().to_dict(), scope("keyword_plan").to_dict()),
        permissible_use_ref="fixture:permissible-use",
        combined_serp_approval_ref="fixture:combined-serp-policy",
        expires_at=(NOW + timedelta(hours=2)).isoformat(),
        approved=True,
        production_approved=True,
    )
    return replace(value, **changes)


def parameters(kind="report", **changes):
    value = {
        "source": "ads",
        "source_version": "1",
        "access_id": "access:one",
        "template_version": "plan:1" if kind == "keyword_plan" else "report:1",
        "customer_id": "1234567890",
        "period_start": "2026-10-01",
        "period_end": "2026-11-01",
        "source_timezone": "America/Los_Angeles",
        "network": "google_search",
        "geo": "US",
        "language": "en",
        "currency": "USD",
        "attribution_version": "not_applicable" if kind == "keyword_plan" else "session:1",
        "mode": "internal_analysis",
    }
    value.update(changes)
    return value


def scope(kind="report", **changes):
    params = parameters(kind)
    value = AdsScope(
        kind=kind,
        **{key: params[key] for key in AdsScope.__dataclass_fields__ if key != "kind"},
    )
    return replace(value, **changes)


def evidence(**changes):
    value = AdsEvidence(
        adapter_id="ads",
        adapter_version="1",
        source_api_version="fixture-ads-api",
        contract_ref="fixture:ads-contract",
        dataset_id="fixture:ads-dataset",
        binding=binding(),
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        revision="revision:1",
        provider_request_id="fixture:ads-request",
    )
    return replace(value, **changes)


def row(scope_value=None, *, row_id="row:1", metric_id="conversions",
        value="1.25", row_role="snapshot", availability=None):
    scope_value = scope_value or scope()
    plan = scope_value.kind == "keyword_plan"
    dimensions = {"month": "2026-10"} if plan else {
        "date": "2026-10-15", "campaign_id": "campaign:" + row_id}
    return AdsRow(
        row_id=row_id,
        dimensions=dimensions,
        metrics=(MetricValue(
            value=value,
            unit=METRIC_UNITS[metric_id],
            provenance_kind="authorized_reported",
            measurement_nature="rounded" if plan else "exact_in_scope",
            availability=availability or ("true_zero" if value == 0 else "available"),
            metric_scope={
                **scope_value.to_dict(),
                "metric": metric_id,
                "row_id": row_id,
                "row_role": row_role,
                "dimensions": dimensions,
                "revision": "revision:1",
            },
            definition_version="fixture:metric-definition",
            additivity="non_additive" if plan else "additive_in_scope",
        ),),
        row_role=row_role,
        requested_queries=("alpha", "alpha variant") if plan else (),
        returned_text="alpha" if plan else None,
        close_variant_group="variants:one" if plan else None,
    )


def result(scope_value=None, **changes):
    scope_value = scope_value or scope()
    value = dict(
        evidence=evidence(),
        scope=scope_value,
        rows=(
            row(scope_value, metric_id="search_volume", value=1000)
            if scope_value.kind == "keyword_plan" else row(scope_value),
        ),
        response_complete=True,
        limitations=("fixture_not_a_real_ads_asset",),
    )
    value.update(changes)
    return AdsResult(**value)


class AdsFixture:
    contract_ref = "fixture:ads-contract"
    dataset_id = "fixture:ads-dataset"

    def __init__(self):
        self.current_binding = binding()
        self.output = result()
        self.calls = []
        self.on_read = None

    def binding(self, tenant_id, access_id):
        self.calls.append(("binding", tenant_id, access_id))
        return self.current_binding

    def read_managed(self, tenant_id, request):
        self.calls.append(("read", tenant_id, request))
        if self.on_read is not None:
            self.on_read()
        return self.output


class FakeRegistry:
    def __init__(self):
        self.source_value = registration(
            adapter_id="ads",
            source_api_version="fixture-ads-api",
            production_approved=True,
            field_rights=tuple(
                FieldRight(
                    field, frozenset({"internal_display"}),
                    NOW + timedelta(hours=2))
                for field in ("ads_keyword_plan", "ads_report")
            ),
        )
        self.grant_value = Grant(
            tenant_id="tenant:ads", asset_id="customers/1234567890",
            grant_id="grant:ads", version="1",
            fields=frozenset({"ads_keyword_plan", "ads_report"}),
            uses=frozenset({"internal_display"}),
            expires_at=NOW + timedelta(hours=2),
            source_adapter="ads", source_version="1",
        )
        self.policy = policy()
        self.current = True

    def source(self, tenant_id, source_id, version):
        if (tenant_id, source_id, version) != ("tenant:ads", "ads", "1"):
            raise DomainError("not_authorized", "No source", 403)
        return self.source_value

    def grant(self, tenant_id, grant_id):
        if (tenant_id, grant_id) != ("tenant:ads", "grant:ads"):
            raise DomainError("not_authorized", "No grant", 403)
        return self.grant_value

    def ads_access(self, tenant_id, access_id):
        if (tenant_id, access_id) != ("tenant:ads", "access:one"):
            raise DomainError("not_authorized", "No Ads policy", 403)
        return self.policy

    def assert_current(self, principal, *, now):
        if not self.current:
            raise DomainError("not_authorized", "Rights changed", 403)


class AdsContractTests(unittest.TestCase):
    def test_policy_detaches_caller_binding_and_requires_explicit_use(self):
        original = policy().to_dict()
        copy = AdsAccessPolicy.from_record(original)
        original["caller_subject_refs"].append("caller:two")
        self.assertEqual(copy.caller_subject_refs, ("caller:one",))
        for changes in (
                {"permissible_use_ref": ""},
                {"combined_serp_approval_ref": ""},
                {"modes": ("programmatic_proxy",)},
                {"approved": "true"},
                {"caller_subject_refs": "caller:one"}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                policy(**changes)

    def test_micros_decimal_conversions_and_negative_revision_are_not_lost(self):
        money = row(metric_id="cost_micros", value="1000001")
        data = result(rows=(money,)).to_dict()
        self.assertEqual(data["rows"][0]["metrics"]["cost_micros"]["value"], "1000001")
        self.assertEqual(
            result().to_dict()["rows"][0]["metrics"]["conversions"]["value"], "1.25")
        delta = row(metric_id="conversions", value="-0.25", row_role="revision_delta")
        self.assertEqual(result(rows=(delta,)).rows[0].row_role, "revision_delta")
        for bad in (
                row(metric_id="cost_micros", value="1.25"),
                row(metric_id="clicks", value=-1),
                row(metric_id="conversions", value="-0.25")):
            with self.assertRaises(DomainError):
                result(rows=(bad,))

    def test_money_currency_grain_and_revision_must_match(self):
        money = row(metric_id="cost_micros", value=100)
        metric = money.metrics[0]
        for changes in (
                {"currency": "EUR"}, {"revision": "revision:other"},
                {"attribution_version": "other"}, {"network": "other"},
                {"unexpected_identifier": "not_permitted"}):
            altered = replace(metric, metric_scope={**dict(metric.metric_scope), **changes})
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                result(rows=(replace(money, metrics=(altered,)),))
        with self.assertRaises(DomainError):
            result(rows=(replace(money, metrics=(replace(metric, unit="currency"),)),))

    def test_shared_variant_groups_are_one_contribution_per_month(self):
        plan_scope = scope("keyword_plan")
        planned = result(plan_scope)
        self.assertEqual(planned.rows[0].requested_queries, ("alpha", "alpha variant"))
        duplicate = row(plan_scope, row_id="row:2", metric_id="search_volume", value=1000)
        with self.assertRaises(DomainError):
            result(plan_scope, rows=(*planned.rows, duplicate))
        suppressed = row(
            plan_scope, metric_id="search_volume",
            value=None, availability="below_threshold")
        data = result(plan_scope, rows=(suppressed,)).to_dict()
        self.assertIsNone(data["rows"][0]["metrics"]["search_volume"]["value"])

    def test_unknown_dimensions_and_duplicate_report_grain_are_rejected(self):
        first = row()
        with self.assertRaises(DomainError):
            result(rows=(replace(first, dimensions={
                **dict(first.dimensions), "user_identifier": "not-permitted",
            }),))
        second = row(row_id="other")
        second = replace(
            second, dimensions=first.dimensions,
            metrics=(replace(
                second.metrics[0], metric_scope={
                    **dict(second.metrics[0].metric_scope),
                    "dimensions": dict(first.dimensions),
                }),))
        with self.assertRaises(DomainError) as error:
            result(rows=(first, second))
        self.assertEqual(error.exception.code, "incompatible_scope")
        with self.assertRaises(DomainError):
            result(rows=(replace(first, metrics=(
                replace(first.metrics[0], value="NaN"),)),))


class AdsServiceTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.adapter = AdsFixture()
        self.principal = Principal(
            "tenant:ads", frozenset({"grant:ads"}), "1",
            subject_ref="caller:one")
        self.clock = NOW + timedelta(seconds=1)
        self.service = AdsService(
            self.registry, self.adapter,
            cursor_codec=CursorCodec(b"a" * 32), clock=lambda: self.clock)

    def read(self, kind="report", **changes):
        return self.service.read(
            self.principal,
            "/ads/keyword-plans" if kind == "keyword_plan" else "/ads/reports",
            parameters(kind, **changes), now=self.clock)

    def assert_denied_without_read(self, **changes):
        self.adapter.calls.clear()
        with self.assertRaises(DomainError) as error:
            self.read(**changes)
        self.assertEqual(error.exception.code, "use_not_permitted")
        self.assertFalse(any(call[0] == "read" for call in self.adapter.calls))

    def test_managed_output_is_revalidated_and_bound_to_current_customer(self):
        data = self.read()
        self.assertEqual(data["rows"][0]["metrics"]["conversions"]["value"], "1.25")
        self.assertEqual(data["scope"]["currency"], "USD")
        self.assertEqual(data["mode"], "internal_analysis")
        self.assertEqual([call[0] for call in self.adapter.calls], ["binding", "read", "binding"])
        self.assertNotIn("oauth_subject_ref", data)
        self.assertEqual(self.read(mode="report_service")["mode"], "report_service")

    def test_standard_project_admission_is_supported_with_same_bound_subject(self):
        value = binding(access_level="Standard")
        self.registry.policy = policy(binding=value)
        self.adapter.current_binding = value
        self.adapter.output = result(evidence=evidence(binding=value))
        self.assertEqual(self.read()["coverage"]["status"], "managed_output")

    def test_subject_project_oauth_customer_and_level_mismatch_are_denied(self):
        for changes in (
                {"tenant_id": "tenant:other"},
                {"business_subject_ref": "business:other"},
                {"cloud_project_ref": "projects/other"},
                {"oauth_subject_ref": "oauth:other"},
                {"customer_id": "9999999999"},
                {"access_level": "Explorer"}):
            with self.subTest(changes=changes):
                self.adapter.current_binding = binding(**changes)
                self.assert_denied_without_read()
        self.adapter.current_binding = binding()
        self.principal = replace(self.principal, subject_ref="caller:two")
        self.assert_denied_without_read()

    def test_explorer_and_test_access_fail_even_if_policy_matches(self):
        for level in ("Explorer", "Test", "unknown"):
            with self.subTest(level=level):
                value = binding(access_level=level)
                self.registry.policy = policy(binding=value)
                self.adapter.current_binding = value
                self.assert_denied_without_read()

    def test_missing_combination_approval_unapproved_and_revoked_fail_closed(self):
        for changes in (
                {"approved": False}, {"production_approved": False},
                {"revoked": True},
                {"expires_at": NOW.isoformat()},
                {"approved_templates": ("other:1",)},
                {"modes": ("report_service",)}):
            with self.subTest(changes=changes):
                self.registry.policy = policy(**changes)
                self.assert_denied_without_read()

    def test_arbitrary_queries_methods_fields_and_proxy_modes_are_not_forwarded(self):
        self.assert_denied_without_read(mode="programmatic_proxy")
        for key in (
                "gaql", "query", "method", "fields", "cloud_project_ref",
                "oauth_subject_ref", "approved", "production_approved"):
            with self.subTest(key=key), self.assertRaises(DomainError) as error:
                self.read(**{key: "caller-supplied"})
            self.assertEqual(error.exception.code, "invalid_argument")
        self.assertFalse(any(call[0] == "read" for call in self.adapter.calls))

    def test_template_does_not_authorize_arbitrary_geo_network_currency_or_window(self):
        for changes in (
                {"geo": "GB"}, {"network": "search_partners"},
                {"currency": "EUR"}, {"period_start": "2026-09-01"},
                {"attribution_version": "other:1"}):
            with self.subTest(changes=changes):
                self.assert_denied_without_read(**changes)

    def test_keyword_history_cannot_escape_four_year_month_window(self):
        self.adapter.output = result(scope("keyword_plan"))
        self.assertEqual(self.read("keyword_plan")["scope"]["kind"], "keyword_plan")
        for changes in (
                {"period_start": "2022-10-01"},
                {"period_start": "2026-10-02"},
                {"period_end": "2027-01-01"}):
            with self.subTest(changes=changes), self.assertRaises(DomainError) as error:
                self.read("keyword_plan", **changes)
            self.assertEqual(error.exception.code, "invalid_argument")

    def test_wrong_dataset_contract_asset_future_and_expired_evidence_fail_closed(self):
        for changes, code in (
                ({"dataset_id": "other"}, "incompatible_scope"),
                ({"contract_ref": "other"}, "incompatible_scope"),
                ({"source_observed_at": (NOW + timedelta(minutes=1)).isoformat()},
                 "incompatible_scope"),
                ({"evidence_expires_at": (NOW + timedelta(milliseconds=500)).isoformat()},
                 "expired"),
                ({"evidence_expires_at": (NOW + timedelta(hours=3)).isoformat()},
                 "use_not_permitted")):
            with self.subTest(changes=changes):
                self.adapter.output = result(evidence=evidence(**changes))
                with self.assertRaises(DomainError) as error:
                    self.read()
                self.assertEqual(error.exception.code, code)

    def test_expiry_during_adapter_read_is_not_returned_as_success(self):
        self.adapter.on_read = lambda: setattr(
            self, "clock", NOW + timedelta(minutes=90))
        with self.assertRaises(DomainError) as error:
            self.read()
        self.assertEqual(error.exception.code, "expired")

    def test_revocation_binding_or_policy_change_during_read_discards_result(self):
        self.adapter.on_read = lambda: setattr(self.registry, "current", False)
        with self.assertRaises(DomainError) as error:
            self.read()
        self.assertEqual(error.exception.code, "not_authorized")
        self.registry.current = True
        self.adapter.on_read = lambda: setattr(
            self.adapter, "current_binding", binding(cloud_project_ref="projects/other"))
        with self.assertRaises(DomainError) as error:
            self.read()
        self.assertEqual(error.exception.code, "use_not_permitted")

    def test_stale_principal_is_blocked_before_adapter_access(self):
        self.registry.current = False
        with self.assertRaises(DomainError) as error:
            self.read()
        self.assertEqual(error.exception.code, "not_authorized")
        self.assertEqual(self.adapter.calls, [])

    def test_partial_outputs_do_not_claim_full_coverage(self):
        self.adapter.output = result(response_complete=False)
        data = self.read()
        self.assertFalse(data["response_complete"])
        self.assertEqual(data["coverage"]["status"], "partial_managed_output")

    def test_cursor_binds_snapshot_and_does_not_repeat_boundary_row(self):
        first = row(row_id="row:1")
        second = row(row_id="row:2")
        self.adapter.output = result(rows=(first, second))
        data = self.read(limit="1")
        cursor = data["next_cursor"]
        self.assertTrue(cursor)
        next_page = self.read(limit="1", cursor=cursor)
        self.assertEqual(next_page["rows"][0]["row_id"], "row:2")
        self.assertIsNone(next_page["next_cursor"])
        self.adapter.output = result(rows=(first, row(row_id="row:2", value="2.5")))
        with self.assertRaises(DomainError) as error:
            self.read(limit="1", cursor=cursor)
        self.assertEqual(error.exception.code, "invalid_argument")


if __name__ == "__main__":
    unittest.main()
