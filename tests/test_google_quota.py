import os
import unittest
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from realtime.google_domain import DomainError
from realtime.google_quota import SourceQuotaScheduler
from tests.test_google_domain import NOW


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class SourceQuotaSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["TEST_DATABASE_URL"]
        self.scheduler = SourceQuotaScheduler(self.dsn)
        self.scheduler.initialize()
        self.tenant = "quota-test-" + os.urandom(8).hex()
        self.source = "ga4-fixture"
        for kind, ref, window, limit in (
                ("service", "analytics", 60, 10),
                ("cloud_project", "projects/one", 86400, 5),
                ("property", "properties/123", 86400, 3),
                ("cid", "1234567890", 1, 1),
                ("vendor_right", "rights:ga4", 86400, 2)):
            self.scheduler.set_limit(
                tenant_id=self.tenant, source_adapter=self.source,
                resource_kind=kind, resource_ref=ref,
                window_seconds=window, limit_units=limit, now=NOW)

    def reserve(self, request_id="request:1", account_ref="account:a",
                resources=None, now=NOW):
        return self.scheduler.reserve(
            tenant_id=self.tenant, source_adapter=self.source,
            request_id=request_id, account_ref=account_ref,
            resources=resources or [
                {"kind": "service", "ref": "analytics", "cost": 1},
                {"kind": "cloud_project", "ref": "projects/one", "cost": 1},
                {"kind": "property", "ref": "properties/123", "cost": 1},
            ],
            now=now)

    def test_all_resource_buckets_are_reserved_atomically(self):
        result = self.reserve()
        self.assertEqual(result["units"], 3)
        with self.assertRaises(DomainError) as error:
            self.reserve(
                request_id="request:2",
                now=NOW + timedelta(seconds=2),
                resources=[
                    {"kind": "service", "ref": "analytics", "cost": 1},
                    {"kind": "cid", "ref": "1234567890", "cost": 2},
                ])
        self.assertEqual(error.exception.code, "quota_exceeded")
        self.assertEqual(error.exception.status, 429)
        self.assertIn("retry_after", error.exception.__dict__)
        # The failed CID request must not consume the independent service bucket.
        retry = self.reserve(
            request_id="request:3", now=NOW + timedelta(seconds=1))
        self.assertEqual(retry["units"], 3)

    def test_shared_property_consumption_and_account_rotation_cannot_bypass(self):
        self.scheduler.consume_external(
            tenant_id=self.tenant, source_adapter=self.source,
            resource_kind="property", resource_ref="properties/123",
            units=2, now=NOW)
        first = self.reserve(
            request_id="request:one", account_ref="account:a",
            resources=[
                {"kind": "property", "ref": "properties/123", "cost": 1},
            ],
            now=NOW)
        self.assertEqual(first["units"], 1)
        with self.assertRaises(DomainError) as error:
            self.reserve(
                request_id="request:two", account_ref="account:b",
                resources=[
                    {"kind": "property", "ref": "properties/123", "cost": 1},
                ],
                now=NOW + timedelta(seconds=1))
        self.assertEqual(error.exception.code, "quota_exceeded")

    def test_request_id_is_idempotent_and_payload_changes_conflict(self):
        first = self.reserve()
        second = self.reserve()
        self.assertEqual(first, second)
        with self.assertRaises(DomainError) as error:
            self.reserve(account_ref="account:other")
        self.assertEqual(error.exception.code, "idempotency_conflict")

    def test_concurrent_reservations_cannot_oversubscribe_a_missing_bucket(self):
        self.scheduler.set_limit(
            tenant_id=self.tenant, source_adapter=self.source,
            resource_kind="property", resource_ref="properties/123",
            window_seconds=86400, limit_units=1, now=NOW)

        def attempt(index):
            scheduler = SourceQuotaScheduler(self.dsn)
            try:
                scheduler.reserve(
                    tenant_id=self.tenant, source_adapter=self.source,
                    request_id=f"concurrent:{index}",
                    account_ref=f"account:{index}",
                    resources=[{
                        "kind": "property",
                        "ref": "properties/123",
                        "cost": 1,
                    }],
                    now=NOW,
                )
                return "reserved"
            except DomainError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, (1, 2)))
        self.assertEqual(sorted(outcomes), ["quota_exceeded", "reserved"])

    def test_missing_limit_is_not_covered_and_retry_policy_is_explicit(self):
        with self.assertRaises(DomainError) as error:
            self.reserve(resources=[{
                "kind": "vendor_right", "ref": "missing", "cost": 1,
            }])
        self.assertEqual(error.exception.code, "not_covered")
        self.assertEqual(
            SourceQuotaScheduler.retry_delay(
                error_code="http_429", retry_after=10,
                attempt=3, jitter_seconds=2),
            12,
        )
        self.assertEqual(
            SourceQuotaScheduler.retry_delay(
                error_code="http_503", retry_after=0,
                attempt=10, jitter_seconds=3),
            515,
        )
        with self.assertRaises(DomainError):
            SourceQuotaScheduler.retry_delay(
                error_code="http_500", retry_after=1, attempt=1,
                jitter_seconds=0)


if __name__ == "__main__":
    unittest.main()
