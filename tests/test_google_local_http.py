import os
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from realtime.google_local import LocalService
from tests import test_google_api as api_tests
from tests import test_google_local as fixtures


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class LocalHTTPTests(unittest.TestCase):
    request = api_tests.DomainHTTPTests.request
    tearDown = api_tests.DomainHTTPTests.tearDown

    def setUp(self):
        api_tests.DomainHTTPTests.setUp(self)
        self.source = replace(
            fixtures.LocalRegistry().source_value, adapter_id="local")
        self.registry.save_source(
            self.tenant, self.source, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.grant = replace(
            fixtures.LocalRegistry().grant_value, tenant_id=self.tenant)
        self.registry.save_grant(
            self.grant, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={self.grant.grant_id},
            roles={"reader"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        self.adapter = fixtures.LocalAdapter()
        self.service.local = LocalService(
            self.registry, self.adapter,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock)

    def local_request(self, path, **changes):
        params = {
            "source": "local", "source_version": "1",
            "asset": "asset:local", **changes,
        }
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            return self.request(path + "?" + urlencode(params))

    def test_grid_observation_place_and_review_contracts_are_explicit(self):
        before = self.console.settings(self.config)
        status, grids, _ = self.local_request("local/grids")
        self.assertEqual(status, 200, grids)
        self.assertEqual(grids["data"]["grids"][0]["grid_id"], "grid:1")
        status, rows, _ = self.local_request(
            "local/grids/grid:1/observations")
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows["data"]["observations"][0]["status"], "observed")
        status, place, _ = self.local_request(
            "places/place:1/reference")
        self.assertEqual(status, 200, place)
        self.assertFalse(place["data"]["place"]["raw_store_allowed"])
        status, review, _ = self.local_request(
            "local/review-insights", place_id="place:1")
        self.assertEqual(status, 200, review)
        self.assertEqual(review["data"]["eligible_count"], 80)
        self.assertEqual(self.console.settings(self.config), before)

    def test_raw_proxy_fields_and_wrong_asset_fail_before_adapter(self):
        for path, changes in (
                ("local/grids", {"fields": "all"}),
                ("local/grids/grid:1/observations", {"query": "SELECT *"}),
                ("places/place:1/reference", {"method": "Search"}),
                ("local/grids", {"asset": "asset:other"})):
            self.adapter.calls.clear()
            status, out, _ = self.local_request(path, **changes)
            self.assertIn(status, (400, 403, 404), out)
            self.assertIsNone(out["data"])
            self.assertEqual(self.adapter.calls, [])

    def test_expired_evidence_returns_410_without_rows(self):
        self.adapter.grid_output = fixtures.GridBundle(
            fixtures.evidence(
                evidence_expires_at=(
                    fixtures.NOW + timedelta(milliseconds=500)).isoformat()),
            "asset:local",
            (fixtures.grid(),),
        )
        status, out, _ = self.local_request("local/grids")
        self.assertEqual(status, 410, out)
        self.assertIsNone(out["data"])


if __name__ == "__main__":
    unittest.main()
