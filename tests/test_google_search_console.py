import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_domain import DomainError
from realtime.google_search_console import (
    SearchConsoleEvidence,
    SearchConsoleInspection,
    SearchConsoleReport,
    SearchConsoleRow,
)
from tests.test_google_domain import NOW


def evidence(**changes):
    value = SearchConsoleEvidence(
        adapter_id="gsc",
        adapter_version="1",
        source_api_version="1",
        contract_ref="fixture:gsc:1",
        dataset_id="fixture:gsc-dataset:1",
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    return replace(value, **changes)


def row(row_id="row-a", **changes):
    values = dict(
        row_id=row_id,
        day="2026-11-02",
        dimensions={
            "date": "2026-11-02",
            "query": "alpha",
            "page": "https://example.test/a",
        },
        clicks=1,
        impressions=10,
        ctr="0.1",
        position="2",
    )
    values.update(changes)
    return SearchConsoleRow(**values)


def report(**changes):
    values = dict(
        evidence=evidence(),
        asset_id="sc-domain:example.test",
        property_id="sc-domain:example.test",
        template_version="gsc-pages:1",
        grain=("date", "query", "page"),
        period_start="2026-11-01",
        period_end="2026-11-04",
        source_timezone="America/Los_Angeles",
        data_state="final",
        aggregation="byPage",
        report_kind="top_rows",
        rows=(row(),),
        response_complete=True,
        statistical_completeness="top_rows_only",
        privacy={
            "anonymous_query_rows": "not_provided_by_source",
        },
        limitations=("top_rows_page_not_statistical_completeness",),
    )
    values.update(changes)
    return SearchConsoleReport(**values)


class SearchConsoleContractTests(unittest.TestCase):
    def test_top_rows_never_claim_statistical_completeness(self):
        with self.assertRaises(DomainError):
            report(statistical_completeness="bulk_partition_verified")
        bulk = report(
            report_kind="bulk",
            partition="2026-11-02",
            statistical_completeness="bulk_partition_verified",
        )
        self.assertEqual(
            bulk.to_dict()["statistical_completeness"],
            "bulk_partition_verified",
        )
        with self.assertRaises(DomainError):
            report(
                report_kind="bulk",
                partition="2026-11-02",
                response_complete=False,
                statistical_completeness="bulk_partition_verified",
            )

    def test_rows_must_match_period_and_dimensions(self):
        with self.assertRaises(DomainError):
            report(rows=(row(day="2026-11-04"),))
        with self.assertRaises(DomainError):
            report(rows=(replace(
                row(),
                dimensions={"date": "2026-11-02", "query": "alpha"},
            ),))
        with self.assertRaises(DomainError):
            report(rows=(row(), row(row_id="row-a")))

    def test_grain_and_source_timezone_are_explicit(self):
        with self.assertRaises(DomainError):
            report(source_timezone="local")
        with self.assertRaises(DomainError):
            report(grain=("date", "unsupported"))
        with self.assertRaises(DomainError):
            report(period_start="2026-11-04", period_end="2026-11-01")
        hourly = report(
            data_state="hourly_all",
            grain=("date", "hour"),
            rows=(row(dimensions={
                "date": "2026-11-02",
                "hour": "2026-11-02T10:00:00-08:00",
            }),),
        )
        self.assertEqual(hourly.grain, ("date", "hour"))
        self.assertFalse(hasattr(report(), "statistical_complete"))

    def test_censored_values_remain_null_not_zero(self):
        unavailable = row(
            clicks=0,
            impressions=0,
            ctr=None,
            position=None,
        )
        self.assertIsNone(unavailable.to_dict()["ctr"])
        self.assertIsNone(unavailable.to_dict()["position"])
        with self.assertRaises(DomainError):
            row(ctr="1.1")
        with self.assertRaises(DomainError):
            row(position="-1")

    def test_privacy_anonymous_and_top_n_states_remain_explicit(self):
        value = report(
            privacy={
                "anonymous_query_rows": "not_provided_by_source",
                "protected_queries_inferred": False,
            },
            rows=(row(clicks=0, impressions=0, ctr=None, position=None),),
        ).to_dict()
        self.assertEqual(
            value["privacy"]["anonymous_query_rows"],
            "not_provided_by_source",
        )
        self.assertFalse(value["privacy"]["protected_queries_inferred"])
        self.assertIn(
            "top_rows_page_not_statistical_completeness",
            value["limitations"],
        )
        self.assertIsNone(value["rows"][0]["ctr"])
        self.assertIsNone(value["rows"][0]["position"])

    def test_inspection_preserves_unknown_and_rejects_future_crawl(self):
        result = SearchConsoleInspection(
            evidence=evidence(),
            asset_id="sc-domain:example.test",
            property_id="sc-domain:example.test",
            inspection_url="https://example.test/a",
            inspection_version="url-inspection-1",
            verdict=None,
            coverage_state=None,
            indexing_state=None,
            robots_state=None,
            page_fetch_state=None,
            canonical_url=None,
            last_crawl_time="2026-11-03T00:00:00Z",
            issues=(),
            quota={
                "state": "not_reported_by_source",
                "remaining": None,
                "reset_at": None,
            },
        )
        self.assertIsNone(result.to_dict()["verdict"])
        self.assertEqual(
            result.to_dict()["quota"]["state"],
            "not_reported_by_source",
        )
        with self.assertRaises(DomainError):
            replace(result, last_crawl_time="2026-11-05T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
