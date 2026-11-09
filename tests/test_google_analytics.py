import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_analytics import (
    AnalyticsEvidence,
    AnalyticsReconciliation,
    AnalyticsReport,
    AnalyticsRow,
    AnalyticsSampling,
    ReconciliationMetric,
)
from realtime.google_domain import DomainError, MetricValue, ObservationLimitations
from tests.test_google_domain import NOW


def evidence(**changes):
    value = AnalyticsEvidence(
        adapter_id="ga4",
        adapter_version="1",
        source_api_version="data-api-v1beta",
        contract_ref="fixture:ga4:1",
        dataset_id="fixture:ga4-dataset:1",
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    return replace(value, **changes)


def metric(
        metric_id="sessions",
        value=10,
        *,
        availability="available",
        nature="exact_in_scope",
        row_id="row-a",
        limitations=None,
):
    return MetricValue(
        value=value,
        unit="count",
        provenance_kind="authorized_reported",
        measurement_nature=nature,
        availability=availability,
        metric_scope={
            "asset_id": "properties/123",
            "property_id": "properties/123",
            "template_version": "ga4-core:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "report_timezone": "America/Los_Angeles",
            "row_id": row_id,
            "dimensions": {"date": "2026-11-02"},
            "metric": metric_id,
        },
        definition_version="ga4-data-api-1",
        additivity="semi_additive",
        **({"limitations": limitations} if limitations is not None else {}),
    )


def row(**changes):
    values = dict(
        row_id="row-a",
        dimensions={"date": "2026-11-02"},
        metrics=(metric(),),
        sampling_metadata=(),
        subject_to_thresholding=False,
    )
    values.update(changes)
    return AnalyticsRow(**values)


def report(**changes):
    values = dict(
        evidence=evidence(),
        asset_id="properties/123",
        property_id="properties/123",
        template_version="ga4-core:1",
        period_start="2026-11-01",
        period_end="2026-11-04",
        report_timezone="America/Los_Angeles",
        dimensions=("date",),
        metrics_requested=("sessions",),
        rows=(row(),),
        row_count=1,
        source_response_complete=True,
        sampling_metadata=(),
        subject_to_thresholding=False,
        data_loss_from_other_row=False,
        schema_restrictions=(),
        property_quota={
            "state": "source_reported",
            "tokensPerDay": {"consumed": 1, "remaining": 199999},
        },
        limitations=("data_api_rows_not_bigquery_event_export",),
    )
    values.update(changes)
    return AnalyticsReport(**values)


class AnalyticsContractTests(unittest.TestCase):
    def test_report_preserves_sampling_thresholding_quota_and_unknowns(self):
        report_value = report(
            sampling_metadata=(AnalyticsSampling(
                sample_size=90,
                sampling_space=100,
                sampling_rate="0.9",
                reason="sampling_uniform",
            ),),
            subject_to_thresholding=None,
            data_loss_from_other_row=None,
        )
        payload = report_value.to_dict()
        self.assertEqual(
            payload["sampling_metadata"][0]["sampling_rate"], "0.9")
        self.assertIsNone(payload["subject_to_thresholding"])
        self.assertIsNone(payload["data_loss_from_other_row"])
        self.assertEqual(
            payload["property_quota"]["tokensPerDay"]["remaining"], 199999)
        with self.assertRaises(DomainError):
            AnalyticsSampling(
                sample_size=90,
                sampling_space=100,
                sampling_rate="0.8",
            )

    def test_thresholded_values_remain_null_and_rows_match_scope(self):
        thresholded = metric(
            availability="privacy_suppressed",
            nature="thresholded",
            value=None,
            limitations=ObservationLimitations({
                "anonymous": {"status": "source_applied"},
                "sampled": {"status": "source_applied"},
                "modeled": {"status": "not_applicable"},
                "rounded": {"status": "not_applicable"},
            }),
        )
        payload = row(metrics=(thresholded,)).to_dict()
        self.assertIsNone(payload["metrics"]["sessions"]["value"])
        self.assertEqual(
            payload["metrics"]["sessions"]["availability"],
            "privacy_suppressed",
        )
        self.assertEqual(
            set(payload["metrics"]["sessions"]["limitations"]),
            {"anonymous", "sampled", "modeled", "rounded"},
        )
        with self.assertRaises(DomainError):
            report(rows=(row(row_id="row-a", dimensions={
                "date": "2026-11-02",
                "country": "US",
            }),))
        with self.assertRaises(DomainError):
            report(metrics_requested=("activeUsers",))
        with self.assertRaises(DomainError):
            report(row_count=0)

    def test_property_timezone_period_and_quota_are_explicit(self):
        with self.assertRaises(DomainError):
            report(report_timezone="local")
        with self.assertRaises(DomainError):
            report(period_start="2026-11-04", period_end="2026-11-01")
        with self.assertRaises(DomainError):
            report(property_quota={
                "state": "source_reported",
                "unsupportedQuota": {"remaining": 1},
            })
        with self.assertRaises(DomainError):
            report(property_quota={
                "state": "source_reported",
                "tokensPerDay": {"remaining": -1},
            })
        with self.assertRaises(DomainError):
            report(property_quota={
                "state": "source_reported",
            })
        with self.assertRaises(DomainError):
            report(property_quota=None)
        unknown = report(property_quota={
            "state": "not_reported_by_source",
        })
        self.assertEqual(
            unknown.to_dict()["property_quota"],
            {"state": "not_reported_by_source"},
        )

    def test_nonzero_reconciliation_delta_is_preserved(self):
        comparison = ReconciliationMetric(
            metric_id="sessions",
            api_value=10,
            bigquery_value=9,
            delta=1,
            status="difference_explained",
            reasons=("thresholded_rows",),
        )
        self.assertEqual(comparison.to_dict()["delta"], "1")
        self.assertEqual(
            comparison.to_dict()["status"], "difference_explained")
        with self.assertRaises(DomainError):
            ReconciliationMetric(
                metric_id="sessions",
                api_value=10,
                bigquery_value=9,
                delta=0,
                status="difference_explained",
                reasons=("thresholded_rows",),
            )

    def test_incompatible_scopes_cannot_claim_comparability(self):
        not_comparable = ReconciliationMetric(
            metric_id="sessions",
            api_value=None,
            bigquery_value=9,
            delta=None,
            status="not_comparable",
            reasons=("attribution_scope_not_compatible",),
        )
        result = AnalyticsReconciliation(
            evidence=evidence(),
            asset_id="properties/123",
            property_id="properties/123",
            template_version="ga4-core:1",
            period_start="2026-11-01",
            period_end="2026-11-04",
            report_timezone="America/Los_Angeles",
            dimensions=("date",),
            api_scope={"identity": "property", "attribution": "session"},
            bigquery_scope={"identity": "event", "attribution": "event"},
            compatible=False,
            comparisons=(not_comparable,),
            limitations=("different_identity_scope",),
        )
        self.assertFalse(result.to_dict()["compatible"])
        self.assertIsNone(result.to_dict()["comparisons"][0]["delta"])
        with self.assertRaises(DomainError):
            AnalyticsReconciliation(
                evidence=evidence(),
                asset_id="properties/123",
                property_id="properties/123",
                template_version="ga4-core:1",
                period_start="2026-11-01",
                period_end="2026-11-04",
                report_timezone="America/Los_Angeles",
                dimensions=("date",),
                api_scope={"identity": "property"},
                bigquery_scope={"identity": "event"},
                compatible=False,
                comparisons=(
                ReconciliationMetric(
                    metric_id="sessions",
                    api_value=10,
                    bigquery_value=9,
                    delta=1,
                    status="difference_explained",
                    reasons=("late_rows",),
                ),
                ),
                limitations=(),
            )


if __name__ == "__main__":
    unittest.main()
