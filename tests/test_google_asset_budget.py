import unittest
from dataclasses import replace

from realtime.google_asset_budget import (
    AssetGrant,
    AssetRequest,
    DailyAssetBudget,
)
from realtime.google_domain import DomainError
from tests.test_google_domain import NOW


def grant(**changes):
    values = dict(
        grant_id="grant:asset",
        tenant_id="tenant:one",
        source="fixture",
        asset_kinds=frozenset({"viewport_screenshot"}),
        use="internal_processing",
        expires_at=(NOW.replace(hour=23, minute=59)).isoformat(),
        retention_days=7,
    )
    values.update(changes)
    return AssetGrant(**values)


def request(**changes):
    values = dict(
        request_id="request:asset",
        tenant_id="tenant:one",
        source="fixture",
        asset_kind="viewport_screenshot",
        requested_at=NOW.isoformat(),
        bytes_expected=1000,
        processing_seconds_expected=10,
    )
    values.update(changes)
    return AssetRequest(**values)


class DailyAssetBudgetTests(unittest.TestCase):
    def test_explicit_grant_and_budget_are_required(self):
        budget = DailyAssetBudget()
        result = budget.reserve(grant(), request())
        self.assertEqual(result["asset_kind"], "viewport_screenshot")
        self.assertFalse(result["fact_text_ingest_blocked"])
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(use="redistribution"),
                request(request_id="request:other"),
            )
        self.assertEqual(error.exception.code, "use_not_permitted")

    def test_original_media_and_arbitrary_assets_are_not_valid(self):
        for kind in ("original_video", "web_image", "full_page"):
            with self.subTest(kind=kind):
                with self.assertRaises(DomainError):
                    AssetRequest(
                        request_id="request:" + kind,
                        tenant_id="tenant:one",
                        source="fixture",
                        asset_kind=kind,
                        requested_at=NOW.isoformat(),
                        bytes_expected=1,
                        processing_seconds_expected=1,
                    )

    def test_full_page_requires_separate_current_approval(self):
        budget = DailyAssetBudget()
        html = request(
            asset_kind="required_html",
            full_page=True,
            bytes_expected=500,
        )
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(asset_kinds=frozenset({"required_html"})),
                html,
            )
        self.assertEqual(error.exception.code, "use_not_permitted")
        approved = grant(
            asset_kinds=frozenset({"required_html"}),
            separate_full_page_approval_ref="approval:html",
            separate_full_page_approval_expires_at=NOW.replace(
                hour=23, minute=59).isoformat(),
        )
        self.assertEqual(
            budget.reserve(approved, html)["full_page_approval_ref"],
            "approval:html",
        )

    def test_exhausted_budget_denies_asset_but_not_fact_text(self):
        budget = DailyAssetBudget(daily_bytes=1000, daily_processing_seconds=10)
        budget.reserve(grant(), request())
        status = budget.status("tenant:one", "fixture", at=NOW.isoformat())
        self.assertEqual(status["threshold"], "exhausted")
        self.assertTrue(status["asset_ingest_blocked_at_100_percent"])
        self.assertFalse(status["fact_text_ingest_blocked"])
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(),
                request(request_id="request:after-full", bytes_expected=1),
            )
        self.assertEqual(error.exception.code, "quota_exceeded")

    def test_eighty_percent_is_a_warning(self):
        budget = DailyAssetBudget(daily_bytes=1000, daily_processing_seconds=100)
        budget.reserve(grant(), request(bytes_expected=800, processing_seconds_expected=1))
        status = budget.status("tenant:one", "fixture", at=NOW.isoformat())
        self.assertEqual(status["threshold"], "warning_80")

    def test_request_is_atomic_and_idempotent(self):
        budget = DailyAssetBudget(daily_bytes=1000, daily_processing_seconds=10)
        first = budget.reserve(grant(), request(bytes_expected=900))
        self.assertEqual(budget.reserve(grant(), request(bytes_expected=900)), first)
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(),
                replace(request(bytes_expected=900), bytes_expected=800),
            )
        self.assertEqual(error.exception.code, "idempotency_conflict")
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(), request(request_id="request:partial", bytes_expected=200))
        self.assertEqual(error.exception.code, "quota_exceeded")
        status = budget.status("tenant:one", "fixture", at=NOW.isoformat())
        self.assertEqual(status["bytes_used"], 900)

    def test_scope_and_expiry_fail_closed(self):
        budget = DailyAssetBudget()
        with self.assertRaises(DomainError) as error:
            budget.reserve(grant(tenant_id="tenant:other"), request())
        self.assertEqual(error.exception.code, "not_authorized")
        with self.assertRaises(DomainError) as error:
            budget.reserve(
                grant(expires_at=NOW.replace(microsecond=0).isoformat()),
                request(),
            )
        self.assertEqual(error.exception.code, "expired")


if __name__ == "__main__":
    unittest.main()
