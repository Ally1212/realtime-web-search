import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_domain import DomainError, MetricValue, ObservationLimitations
from realtime.google_metrics_sources import (
    KeywordMetricObservation,
    KeywordMetricsResult,
    MetricSourceEvidence,
    MetricsSourceService,
    RelatedQueriesResult,
    RelatedQuery,
    TrendsSeriesResult,
    TrendPoint,
)
from realtime.google_rights import FieldRight, Grant, Principal
from tests.test_google_domain import NOW, registration


METRIC_FIELDS = frozenset({
    "keyword_metrics", "trend_series", "related_queries",
})


def evidence():
    return MetricSourceEvidence(
        adapter_id="fixture",
        adapter_version="1",
        source_api_version="metrics-api-1",
        contract_ref="fixture:metrics-contract:1",
        dataset_id="fixture:metrics-dataset:1",
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        provider_request_id="request:one",
    )


def metric_value(metric_id, value, *, unit, nature, provenance, limitations=None):
    return MetricValue(
        value=value,
        unit=unit,
        provenance_kind=provenance,
        measurement_nature=nature,
        availability="available",
        metric_scope={
            "metric": metric_id,
            "requested_query": "fixture query",
            "geo": "US",
            "language": "en",
            "network": "google_search",
            "year": 2026,
            "month": 10,
        },
        definition_version="keyword-metrics-1",
        additivity="non_additive",
        **({"limitations": limitations} if limitations is not None else {}),
    )


class FixtureMetricsAdapter:
    contract_ref = "fixture:metrics-contract:1"
    dataset_id = "fixture:metrics-dataset:1"

    def __init__(self):
        self.calls = []
        self.keyword_evidence = evidence()
        self.trend_evidence = evidence()
        self.related_evidence = evidence()

    def read_keyword_metrics(self, tenant_id, request):
        self.calls.append(("keyword", tenant_id, request))
        row = KeywordMetricObservation(
            evidence=self.keyword_evidence,
            provider_row_id="row:one",
            requested_query="fixture query",
            returned_text="fixture query",
            close_variant_group="group:one",
            geo="US",
            language="en",
            network="google_search",
            year=2026,
            month=10,
            metrics=(
                metric_value(
                    "search_volume", 1000,
                    unit="searches_per_month",
                    nature="rounded",
                    provenance="provider_estimated",
                    limitations=ObservationLimitations({
                        "rounded": {
                            "status": "source_applied",
                            "detail": "provider bucket",
                        },
                        "modeled": {
                            "status": "source_applied",
                            "detail": "provider estimate",
                        },
                        "anonymous": {"status": "not_applicable"},
                    }),
                ),
                metric_value(
                    "competition_index", 42,
                    unit="index_0_100",
                    nature="relative_index",
                    provenance="provider_estimated",
                ),
            ),
        )
        return KeywordMetricsResult(
            requested_terms=("fixture query",),
            close_variant_group=True,
            geo="US",
            language="en",
            network="google_search",
            year=2026,
            month=10,
            rows=(row,),
            complete=True,
            limitations=("rounded_provider_estimate_not_exact_demand",),
        )

    def read_trend_series(self, tenant_id, request):
        self.calls.append(("series", tenant_id, request))
        return TrendsSeriesResult(
            evidence=self.trend_evidence,
            series_id="series:one",
            subject_type="term",
            term_or_topic="fixture query",
            provider_topic_id=None,
            geo="US",
            category="all",
            requested_from=request["from"],
            requested_to=request["to"],
            granularity="day",
            timezone="UTC",
            scale_id="provider-native:one",
            scale_policy="provider_native",
            sample_version="sample:one",
            points=(
                TrendPoint(
                    period_start="2026-10-01T00:00:00Z",
                    period_end="2026-10-02T00:00:00Z",
                    value=12,
                    availability="available",
                    revision="one",
                ),
                TrendPoint(
                    period_start="2026-10-02T00:00:00Z",
                    period_end="2026-10-03T00:00:00Z",
                    value=None,
                    availability="below_threshold",
                    revision="one",
                    low_volume=True,
                    missing_reason="source_low_volume",
                ),
            ),
            complete=True,
            limitations=("relative_interest_not_search_volume",),
        )

    def read_trend_related(self, tenant_id, request):
        self.calls.append(("related", tenant_id, request))
        return RelatedQueriesResult(
            evidence=self.related_evidence,
            source_series_id="series:one",
            query=None,
            mode="rising",
            geo="US",
            observed_from=request["from"],
            observed_to=request["to"],
            scale_id="provider-native:one",
            rows=(
                RelatedQuery(
                    related_id="related:one",
                    label="related fixture",
                    rank=1,
                    provider_score=88,
                    breakout=True,
                    breakout_flag="Breakout",
                ),
            ),
            complete=True,
            limitations=("breakout_multiplier_not_inferred",),
        )


class FakeRegistry:
    def __init__(self, source, grant):
        self._source = source
        self._grant = grant

    def source(self, tenant_id, adapter_id, version):
        if (
                tenant_id != self._grant.tenant_id
                or adapter_id != self._source.adapter_id
                or version != self._source.adapter_version
        ):
            raise DomainError("not_authorized", "No source", 403)
        return self._source

    def grant(self, tenant_id, grant_id):
        if tenant_id != self._grant.tenant_id or grant_id != self._grant.grant_id:
            raise DomainError("not_authorized", "No grant", 403)
        return self._grant


class MetricsSourceTests(unittest.TestCase):
    def setUp(self):
        fields = tuple(
            FieldRight(
                name,
                frozenset({"internal_display"}),
                NOW + timedelta(hours=2),
            )
            for name in sorted(METRIC_FIELDS)
        )
        self.source = registration(
            field_rights=fields,
            coverage={"countries": ["US", "GB"], "languages": ["en"]},
            source_api_version="metrics-api-1",
            production_approved=True,
        )
        self.grant = Grant(
            tenant_id="tenant:one",
            asset_id="asset:one",
            grant_id="grant:one",
            version="1",
            fields=METRIC_FIELDS,
            uses=frozenset({"internal_display"}),
            expires_at=NOW + timedelta(hours=2),
            source_adapter="fixture",
            source_version="1",
        )
        self.principal = Principal(
            "tenant:one",
            frozenset({"grant:one"}),
            "1",
            roles=frozenset({"reader"}),
        )
        self.adapter = FixtureMetricsAdapter()
        self.service = MetricsSourceService(
            FakeRegistry(self.source, self.grant),
            self.adapter,
            clock=lambda: NOW + timedelta(minutes=1),
        )

    def test_keyword_metrics_preserve_provenance_and_fail_closed(self):
        out = self.service.read(
            self.principal,
            "/keyword-metrics",
            {
                "source": "fixture",
                "source_version": "1",
                "terms": "fixture query",
                "close_variant_group": "true",
                "geo": "US",
                "language": "en",
                "network": "google_search",
                "month": "2026-10",
            },
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(out["coverage"]["status"], "source_complete")
        self.assertEqual(
            out["rows"][0]["metrics"]["search_volume"]["measurement_nature"],
            "rounded",
        )
        self.assertIn(
            "rounded_provider_estimate_not_exact_demand", out["limitations"])
        metric = out["rows"][0]["metrics"]["search_volume"]
        self.assertEqual(metric["provenance_kind"], "provider_estimated")
        self.assertEqual(metric["measurement_nature"], "rounded")
        self.assertEqual(metric["additivity"], "non_additive")
        self.assertEqual(
            metric["limitations"]["rounded"]["detail"],
            "provider bucket",
        )
        self.assertEqual(
            metric["limitations"]["modeled"]["status"],
            "source_applied",
        )
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal,
                "/keyword-metrics",
                {
                    "terms": "fixture query",
                    "close_variant_group": "true",
                    "geo": "US",
                    "month": "2026-10",
                },
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(error.exception.code, "invalid_argument")

    def test_trends_keep_scale_timezone_revision_and_low_volume(self):
        out = self.service.read(
            self.principal,
            "/trends/series",
            {
                "source": "fixture",
                "source_version": "1",
                "term": "fixture query",
                "geo": "US",
                "category": "all",
                "observed_from": "2026-10-01T00:00:00Z",
                "observed_to": "2026-10-03T00:00:00Z",
                "granularity": "day",
                "timezone": "UTC",
                "scale_policy": "provider_native",
            },
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(out["series"]["scale_id"], "provider-native:one")
        self.assertIsNone(out["series"]["points"][1]["value"])
        self.assertTrue(out["series"]["points"][1]["low_volume"])
        self.assertEqual(
            out["series"]["points"][1]["availability"], "below_threshold")
        self.assertIn(
            "different_scale_ids_are_never_concatenated",
            out["scale_policy_limitations"],
        )

    def test_related_breakout_is_raw_not_multiplier(self):
        out = self.service.read(
            self.principal,
            "/trends/related",
            {
                "source": "fixture",
                "source_version": "1",
                "series": "series:one",
                "mode": "rising",
                "geo": "US",
                "observed_from": "2026-10-01T00:00:00Z",
                "observed_to": "2026-10-03T00:00:00Z",
                "limit": "20",
            },
            now=NOW + timedelta(minutes=1),
        )
        self.assertTrue(out["rows"][0]["breakout"])
        self.assertEqual(out["rows"][0]["breakout_flag"], "Breakout")
        self.assertFalse(out["rows"][0]["breakout_multiplier_inferred"])

    def test_adapter_contract_and_retention_mismatches_are_rejected(self):
        self.adapter.keyword_evidence = replace(
            self.adapter.keyword_evidence,
            dataset_id="other:dataset",
        )
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal,
                "/keyword-metrics",
                {
                    "source": "fixture",
                    "source_version": "1",
                    "terms": "fixture query",
                    "close_variant_group": "true",
                    "geo": "US",
                    "month": "2026-10",
                },
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(error.exception.code, "incompatible_scope")

        self.adapter.keyword_evidence = replace(
            evidence(),
            evidence_expires_at=(NOW + timedelta(hours=3)).isoformat(),
        )
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal,
                "/keyword-metrics",
                {
                    "source": "fixture",
                    "source_version": "1",
                    "terms": "fixture query",
                    "close_variant_group": "true",
                    "geo": "US",
                    "month": "2026-10",
                },
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(error.exception.code, "use_not_permitted")


if __name__ == "__main__":
    unittest.main()
