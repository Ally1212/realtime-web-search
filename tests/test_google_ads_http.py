import os
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from realtime.google_ads import AdsService
from realtime.google_domain import DomainError, canonical_json
from tests import test_google_ads as fixtures
from tests import test_google_api as api_tests
from tests.test_google_domain import NOW


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class AdsHTTPTests(unittest.TestCase):
    request = api_tests.DomainHTTPTests.request
    tearDown = api_tests.DomainHTTPTests.tearDown

    def setUp(self):
        api_tests.DomainHTTPTests.setUp(self)
        source = replace(
            fixtures.FakeRegistry().source_value,
            adapter_id="ads",
        )
        self.registry.save_source(
            self.tenant, source, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.ads_grant = replace(
            fixtures.FakeRegistry().grant_value, tenant_id=self.tenant)
        self.registry.save_grant(
            self.ads_grant, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.ads_policy = fixtures.policy(
            binding=fixtures.binding(tenant_id=self.tenant),
            caller_subject_refs=(self.subject,))
        self.registry.save_ads_access(
            self.ads_policy, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            self.tenant, self.subject,
            grant_ids={self.ads_grant.grant_id}, roles={"reader"},
            expected_revision=1, actor_ref="fixture:operator", now=self.clock)
        self.adapter = fixtures.AdsFixture()
        self.adapter.current_binding = self.ads_policy.binding
        self.adapter.output = fixtures.result(evidence=fixtures.evidence(
            binding=self.ads_policy.binding))
        self.service.ads = AdsService(
            self.registry, self.adapter,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock)

    def ads_request(self, kind="report", **changes):
        path = "ads/keyword-plans" if kind == "keyword_plan" else "ads/reports"
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            return self.request(path + "?" + urlencode(fixtures.parameters(kind, **changes)))

    def test_report_and_plan_http_preserve_currency_fractional_conversions_and_group(self):
        before = self.console.settings(self.config)
        status, out, _ = self.ads_request()
        self.assertEqual(status, 200, out)
        self.assertEqual(out["meta"]["quality"], "approved_managed_ads_output")
        self.assertEqual(out["meta"]["versions"]["revision"], "revision:1")
        self.assertEqual(out["data"]["rows"][0]["metrics"]["conversions"]["value"], "1.25")
        self.assertEqual(out["data"]["scope"]["currency"], "USD")
        self.adapter.output = fixtures.result(
            fixtures.scope("keyword_plan"),
            evidence=fixtures.evidence(binding=self.ads_policy.binding))
        status, plan, _ = self.ads_request("keyword_plan")
        self.assertEqual(status, 200, plan)
        self.assertEqual(len(plan["data"]["rows"]), 1)
        self.assertEqual(plan["data"]["rows"][0]["requested_queries"], ["alpha", "alpha variant"])
        self.assertEqual(plan["data"]["rows"][0]["metrics"]["search_volume"]["measurement_nature"],
                         "rounded")
        self.assertEqual(self.console.settings(self.config), before)

    def test_unapproved_project_scope_or_proxy_fails_before_managed_output_read(self):
        for changes in (
                {"gaql": "SELECT secret_field FROM customer"},
                {"mode": "proxy"},
                {"geo": "GB"},
                {"template_version": "unapproved"},
                {"cloud_project_ref": "projects/foreign"}):
            self.adapter.calls.clear()
            status, out, _ = self.ads_request(**changes)
            self.assertIn(status, (400, 403), out)
            self.assertIsNone(out["data"])
            self.assertFalse(any(call[0] == "read" for call in self.adapter.calls))
        self.adapter.current_binding = fixtures.binding(
            tenant_id=self.tenant, cloud_project_ref="projects/foreign")
        status, out, _ = self.ads_request()
        self.assertEqual(status, 403, out)
        self.assertEqual(out["errors"][0]["code"], "use_not_permitted")

    def test_customer_grant_does_not_authorize_other_subject_or_tenant(self):
        other_key = self.store.issue_key(self.tenant, "other subject")
        other_subject = self.store.authenticate(other_key["api_key"], rate_limit=False)["key_id"]
        self.registry.save_subject(
            self.tenant, str(other_subject),
            grant_ids={self.ads_grant.grant_id}, roles={"reader"},
            expected_revision=0, actor_ref="fixture:operator", now=self.clock)
        path = "ads/reports?" + urlencode(fixtures.parameters())
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, other, _ = self.request(path, key=other_key["api_key"])
            self.assertEqual(status, 403, other)
            self.assertEqual(other["errors"][0]["code"], "use_not_permitted")
            status, tenant, _ = self.request(path, key=self.other["api_key"])
            self.assertEqual(status, 404, tenant)
            self.assertIsNone(tenant["data"])
        with self.assertRaises(DomainError):
            self.registry.ads_access(str(self.other["id"]), self.ads_policy.access_id)
        raw = canonical_json(other)
        self.assertNotIn(self.account["api_key"], raw)
        self.assertNotIn(self.ads_policy.binding.oauth_subject_ref, raw)

    def test_policy_cas_and_revocation_invalidate_current_principal_and_response(self):
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        with self.assertRaises(DomainError) as error:
            self.registry.save_ads_access(
                self.ads_policy, expected_revision=0,
                actor_ref="fixture:operator", now=self.clock)
        self.assertEqual(error.exception.code, "precondition_failed")
        self.registry.save_ads_access(
            replace(self.ads_policy, revoked=True), expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        with self.assertRaises(DomainError):
            self.registry.assert_current(principal, now=self.clock)
        status, out, _ = self.ads_request()
        self.assertEqual(status, 403, out)
        self.assertIsNone(out["data"])

    def test_policy_revoked_during_managed_read_never_leaks_assembled_result(self):
        self.adapter.on_read = lambda: self.registry.save_ads_access(
            replace(self.ads_policy, revoked=True), expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        status, out, _ = self.ads_request()
        self.assertEqual(status, 403, out)
        self.assertIsNone(out["data"])

    def test_pagination_is_snapshot_bound_and_expired_results_are_not_returned(self):
        self.adapter.output = fixtures.result(
            evidence=fixtures.evidence(binding=self.ads_policy.binding),
            rows=(fixtures.row(row_id="a"), fixtures.row(row_id="b")))
        status, first, _ = self.ads_request(limit="1")
        self.assertEqual(status, 200, first)
        cursor = first["data"]["next_cursor"]
        status, second, _ = self.ads_request(limit="1", cursor=cursor)
        self.assertEqual(status, 200, second)
        self.assertEqual(second["data"]["rows"][0]["row_id"], "b")
        self.assertIsNone(second["data"]["next_cursor"])
        self.adapter.output = fixtures.result(
            evidence=fixtures.evidence(binding=self.ads_policy.binding),
            rows=(fixtures.row(row_id="a"), fixtures.row(row_id="b", value="2.5")))
        status, changed, _ = self.ads_request(limit="1", cursor=cursor)
        self.assertEqual(status, 400, changed)
        self.clock = NOW + timedelta(minutes=90)
        status, expired, _ = self.ads_request()
        self.assertEqual(status, 410, expired)
        self.assertEqual(expired["errors"][0]["code"], "expired")

    def test_provider_failure_returns_503_without_empty_success_or_automatic_replay(self):
        with patch.object(self.adapter, "read_managed", side_effect=OSError(
                "fixture internal transport detail")) as reader:
            status, out, _ = self.ads_request()
        self.assertEqual(status, 503, out)
        self.assertIsNone(out["data"])
        self.assertEqual(out["errors"][0]["code"], "source_error")
        self.assertNotIn("transport detail", canonical_json(out))
        reader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
