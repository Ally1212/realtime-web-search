import os
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from realtime.google_domain import DomainError, canonical_json
from realtime.google_merchant import MerchantService
from tests import test_google_api as api_tests
from tests import test_google_merchant as fixtures
from tests.test_google_domain import NOW


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class MerchantHTTPTests(unittest.TestCase):
    request = api_tests.DomainHTTPTests.request
    tearDown = api_tests.DomainHTTPTests.tearDown

    def setUp(self):
        api_tests.DomainHTTPTests.setUp(self)
        source = replace(
            fixtures.MerchantRegistry().source_value,
            adapter_id="merchant",
        )
        self.registry.save_source(
            self.tenant, source, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.grant = replace(
            fixtures.MerchantRegistry().grant_value, tenant_id=self.tenant)
        self.registry.save_grant(
            self.grant, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.policy = fixtures.policy(
            caller_subject_refs=(self.subject,))
        self.registry.save_merchant_access(
            self.tenant, self.policy, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={self.grant.grant_id},
            roles={"reader"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        self.adapter = fixtures.MerchantAdapter()
        self.service.merchant = MerchantService(
            self.registry, self.adapter,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock)

    def merchant_request(self, **changes):
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            return self.request(
                "merchant/reports?" + urlencode(fixtures.parameters(**changes)))

    def test_report_preserves_scope_micros_and_unknown_title(self):
        before = self.console.settings(self.config)
        status, out, _ = self.merchant_request()
        self.assertEqual(status, 200, out)
        self.assertEqual(out["meta"]["quality"], "approved_merchant_report")
        self.assertEqual(out["data"]["scope"]["merchant_id"], "1234567890")
        status, money, _ = self.merchant_request()
        self.assertEqual(status, 200, money)
        self.assertEqual(out["data"]["rows"][0]["product_title"], "Approved product title")
        self.assertEqual(self.console.settings(self.config), before)

    def test_proxy_fields_and_wrong_asset_fail_before_read(self):
        for changes in (
                {"query": "SELECT * FROM products"},
                {"method": "Search"},
                {"fields": "all"},
                {"asset": "merchant:9999999999"},
                {"template_version": "unapproved"}):
            self.adapter.calls.clear()
            status, out, _ = self.merchant_request(**changes)
            self.assertIn(status, (400, 403, 409), out)
            self.assertIsNone(out["data"])
            self.assertEqual(self.adapter.calls, [])

    def test_tenant_scope_revocation_and_cursor_snapshot_fail_closed(self):
        other_key = self.store.issue_key(self.tenant, "other merchant subject")
        other_subject = self.store.authenticate(
            other_key["api_key"], rate_limit=False)["key_id"]
        self.registry.save_subject(
            self.tenant, str(other_subject),
            grant_ids={self.grant.grant_id}, roles={"reader"},
            expected_revision=0, actor_ref="fixture:operator", now=self.clock)
        path = "merchant/reports?" + urlencode(fixtures.parameters())
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, other, _ = self.request(path, key=other_key["api_key"])
            self.assertEqual(status, 403, other)
            self.assertEqual(other["errors"][0]["code"], "use_not_permitted")
            status, tenant, _ = self.request(path, key=self.other["api_key"])
            self.assertEqual(status, 404, tenant)
        self.adapter.output = fixtures.report(rows=(
            fixtures.row(row_id="a"), fixtures.row(row_id="b")))
        status, first, _ = self.merchant_request(limit="1")
        self.assertEqual(status, 200, first)
        cursor = first["data"]["next_cursor"]
        self.adapter.output = fixtures.report(rows=(
            fixtures.row(row_id="a"),
            fixtures.row(row_id="b", value=99),
        ))
        status, changed, _ = self.merchant_request(limit="1", cursor=cursor)
        self.assertEqual(status, 400, changed)
        current = self.registry.grant(self.tenant, self.grant.grant_id)
        self.registry.save_grant(
            replace(current, revoked=True, version="2"),
            expected_revision=1, actor_ref="fixture:operator", now=self.clock)
        status, revoked, _ = self.merchant_request()
        self.assertEqual(status, 403, revoked)
        self.assertIsNone(revoked["data"])

    def test_provider_failure_never_becomes_empty_success(self):
        with patch.object(
                self.adapter, "read_report",
                side_effect=OSError("internal transport detail")) as reader:
            status, out, _ = self.merchant_request()
        self.assertEqual(status, 503, out)
        self.assertIsNone(out["data"])
        self.assertEqual(out["errors"][0]["code"], "source_error")
        self.assertNotIn("transport detail", canonical_json(out))
        reader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
