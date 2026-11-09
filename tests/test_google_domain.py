"""PRD v1.0 contract oracles; synthetic cases are not production acceptance."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from realtime.google_domain import (
    DomainError, MetricValue, ObservationLimitations, Query, QueryVariant,
    canonical_json,
    identity, observation_event, parse_time, report_identity,
)
from realtime.google_rights import (
    FieldRight, Grant, Principal, SourceRegistration, authorize, inherit_rights,
)
from realtime.google_metrics import (
    ai_visibility, gsc_position, organic_position, rank_change, ratio,
    search_demand, top_k_share, trend_change,
)

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
SCOPE = dict(country="US", device="desktop", surface="organic", source="fixture-v1",
             cohort_version="cohort-1", definition_version="1")


def metric(value, **overrides):
    return MetricValue(value=value, unit="index_points",
                       provenance_kind="public_observed",
                       measurement_nature="relative_index",
                       availability="available", metric_scope=SCOPE,
                       definition_version="1", scale_id="request-a", **overrides)


class IdentityTests(unittest.TestCase):
    def variant(self, text="C++", **kw):
        args = dict(query_id=Query(text).query_id, country="US", language="en",
                    location={"code": "US", "precision": "country"},
                    device="desktop", search_engine="google", surface="organic",
                    channel="search", login_state="unknown", safe_search="unknown",
                    depth=10, parameters_version="1")
        return QueryVariant(**{**args, **kw})

    def test_query_preserves_original_and_meaning(self):
        values = ['C', 'C++', 'C#', '"foo bar"', 'foo bar', 'Apple', 'apple']
        self.assertEqual(len({Query(x).query_id for x in values}), len(values))
        self.assertEqual(Query('e\u0301').query_id, Query('\u00e9').query_id)
        self.assertEqual(Query('  C++  ').original_text, '  C++  ')
        self.assertNotEqual(Query('Ａ').query_id, Query('A').query_id)
        for text in ("", "   ", "x" * 4097, None):
            with self.assertRaises(DomainError):
                Query(text)

    def test_variant_scope_changes_identity(self):
        base = self.variant()
        for kw in (dict(country="GB"), dict(device="mobile"),
                   dict(surface="paid"), dict(depth=20), dict(login_state="signed_out"),
                   dict(location={"code": "London", "precision": "city",
                                  "basis": "source_provided"}),
                   dict(parameters_version="2")):
            self.assertNotEqual(base.variant_id, self.variant(**kw).variant_id)
        self.assertEqual(base.variant_id, self.variant().variant_id)
        for location in (
            {"precision": "unknown", "basis": "unknown"},
            {"precision": "country", "basis": "ip_country", "code": "US"},
            {"precision": "coordinates", "basis": "source_provided",
             "coordinates": ["37.7749", "-122.4194"]},
        ):
            self.assertNotEqual(base.variant_id, self.variant(location=location).variant_id)
        coordinate = self.variant(location={
            "precision": "coordinates", "basis": "source_provided",
            "coordinates": ["37.7749", "-122.4194"]})
        coordinate_pair = self.variant(location={
            "precision": "coordinates", "basis": "source_provided",
            "latitude": "37.7749", "longitude": "-122.4194"})
        self.assertEqual(coordinate_pair.location["latitude"], "37.7749")
        self.assertNotEqual(
            coordinate.variant_id,
            self.variant(location={
                "precision": "coordinates", "basis": "source_provided",
                "coordinates": ["37.7749", "-122.4193"]}).variant_id)
        for location in (
            {"precision": "planet"},
            {"precision": "city", "basis": "ip_country"},
            {"precision": "city", "code": "London"},
            {"precision": "coordinates", "basis": "source_provided"},
            {"precision": "country", "coordinates": [1, 2]},
            {"precision": "country", "latitude": 1},
            {"precision": "coordinates", "latitude": 1},
            {"precision": "coordinates", "basis": "ip_country",
             "coordinates": [1, 2]},
        ):
            with self.subTest(location=location):
                with self.assertRaises(DomainError):
                    self.variant(location=location)
        with self.assertRaises(DomainError):
            self.variant(depth=True)
        with self.assertRaises(DomainError):
            self.variant(device="")

    def test_legacy_sample_conditions_map_without_inventing_location(self):
        # Mirror of the read-only legacy serp profile (google-serp-v1 rows):
        # gl/hl/num/start present, location/device empty, cache status unknown.
        legacy = self.variant(
            country="US", language="en",
            location={"precision": "unknown", "basis": "unknown"},
            device="unknown", login_state="unknown", safe_search="unknown",
            depth=10, parameters_version="legacy-serp-read-only-1",
            parameters={"start": 0})
        self.assertTrue(legacy.variant_id)
        self.assertEqual(legacy.location["precision"], "unknown")
        self.assertEqual(legacy.location["basis"], "unknown")
        self.assertNotIn("code", legacy.location)
        self.assertNotIn("coordinates", legacy.location)
        self.assertEqual(legacy.device, "unknown")
        # Unknown condition is not the same as a source-provided precise location.
        precise = replace(
            legacy,
            location={"code": "US", "precision": "country", "basis": "source_provided"})
        self.assertNotEqual(legacy.variant_id, precise.variant_id)

    def test_identity_canonical_namespace_and_numeric_precision(self):
        self.assertEqual(identity("query", {"a": 1, "b": 2}),
                         identity("query", {"b": 2, "a": 1}))
        self.assertNotEqual(identity("query", "123"), identity("topic", "123"))
        self.assertNotEqual(identity("query", "123"), identity("query", 123))
        self.assertEqual(json.loads(canonical_json({"n": Decimal("0.1234567890123456789")}))["n"],
                         "0.1234567890123456789")
        for x in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")):
            with self.assertRaises(DomainError):
                canonical_json({"value": x})
        with self.assertRaises(DomainError):
            canonical_json({1: "non-string key"})

    def test_retry_event_is_immutable_but_new_run_is_new_fact(self):
        payload = {"value": 4}
        event = observation_event("run:one", NOW, payload)
        for _ in range(100):
            self.assertEqual(event, observation_event("run:one", NOW, payload))
        self.assertNotEqual(event["event_id"],
                            observation_event("run:two", NOW, payload)["event_id"])
        # A retry with changed content is a conflict, not a new event to accept.
        changed = observation_event("run:one", NOW, {"value": 5})
        self.assertEqual(event["event_id"], changed["event_id"])
        self.assertNotEqual(event["payload_hash"], changed["payload_hash"])

    def test_report_grain_period_asset_and_revision(self):
        args = dict(asset_id="asset:one", template_version="gsc-site:1",
                    grain=["day", "query"], period_start="2026-11-01",
                    period_end="2026-11-02", source_timezone="America/Los_Angeles",
                    scope={"type": "web"})
        report = report_identity(**args)
        for key, value in (("asset_id", "asset:two"), ("grain", ["day", "page"]),
                           ("scope", {"type": "image"}), ("period_end", "2026-11-03")):
            self.assertNotEqual(report, report_identity(**{**args, key: value}))
        with self.assertRaises(DomainError):
            report_identity(**{**args, "period_end": "2026-11-01"})

    def test_time_must_be_aware_and_is_normalized_to_utc(self):
        self.assertEqual(parse_time("2026-11-04T08:00:00+08:00"), NOW)
        for value in ("2026-11-04", "2026-11-04T00:00:00", NOW.replace(tzinfo=None),
                      "2026-11-04T00:00:00+08:99"):
            with self.assertRaises(DomainError):
                parse_time(value)

    def test_variant_cannot_be_mutated_through_nested_scope(self):
        v = self.variant(location={
            "precision": "coordinates", "basis": "source_provided",
            "coordinates": [1, 2]})
        vid = v.variant_id
        with self.assertRaises(TypeError):
            v.location["precision"] = "country"
        with self.assertRaises(TypeError):
            v.location["coordinates"][0] = 10
        self.assertEqual(v.variant_id, vid)


class MetricContractTests(unittest.TestCase):
    def test_metric_limitations_remain_separate_typed_states(self):
        limitations = ObservationLimitations({
            "anonymous": {"status": "source_applied"},
            "rounded": {"status": "source_applied", "detail": "provider bucket"},
            "modeled": {"status": "not_applicable"},
        })
        m = metric(1200, limitations=limitations)
        raw = m.to_dict()["limitations"]
        self.assertEqual(set(raw), {"anonymous", "rounded", "modeled"})
        self.assertEqual(raw["rounded"]["detail"], "provider bucket")
        with self.assertRaises(TypeError):
            m.limitations.entries["sampled"] = {"status": "source_applied"}
        for value in ({"sampled": {"status": "ok", "secret": True}},
                      {"unexpected": {"status": "ok"}},
                      {"sampled": {"status": ""}},
                      {"sampled": {"status": "invented"}}):
            with self.assertRaises(DomainError):
                ObservationLimitations(value)

    def test_roundtrip_zero_missing_rounded_and_precision(self):
        for value, status, nature in (
            (0, "true_zero", "exact_in_scope"),
            (None, "outside_observed_depth", "unknown"),
            (Decimal("1200.000000001"), "available", "rounded"),
        ):
            m = MetricValue(value=value, unit="searches", provenance_kind="authorized_reported",
                            measurement_nature=nature, availability=status,
                            metric_scope=SCOPE, definition_version="1")
            raw = json.loads(canonical_json(m.to_dict()))
            self.assertEqual(raw["availability"], status)
            self.assertEqual(raw["value"], str(value) if isinstance(value, Decimal) else value)
            self.assertIsNone(raw["interval"])
            self.assertIn("uncertainty_not_provided", raw["quality_flags"])

    def test_inconsistent_values_and_statuses_are_rejected(self):
        for value, status in ((None, "available"), (1, "true_zero"), (False, "available"),
                              (4, "not_authorized"), (0, "source_error")):
            with self.assertRaises(DomainError):
                MetricValue(value=value, unit="rank", provenance_kind="public_observed",
                            measurement_nature="exact_in_scope", availability=status,
                            metric_scope=SCOPE, definition_version="1")
        with self.assertRaises(DomainError):
            metric(4, interval={"lower": 3, "upper": 5, "level": .95})
        with self.assertRaises(DomainError):
            metric(float("nan"))
        with self.assertRaises(DomainError):
            metric(4, interval={"lower": 5, "upper": 3, "level": .95,
                                "method": "fixture", "calibration_population": "fixture"})

    def test_input_mutation_cannot_change_scope_or_identity(self):
        scope = dict(SCOPE)
        m = MetricValue(value=1, unit="count", provenance_kind="public_observed",
                        measurement_nature="exact_in_scope", availability="available",
                        metric_scope=scope, definition_version="1")
        scope["country"] = "GB"
        exported = m.to_dict()
        exported["metric_scope"]["country"] = "CA"
        self.assertEqual(m.to_dict()["metric_scope"]["country"], "US")
        with self.assertRaises(TypeError):
            m.metric_scope["country"] = "CA"

    def test_metric_rejects_containers_and_expired_evidence_is_not_comparable(self):
        for value in ({}, [], (), object()):
            with self.assertRaises(DomainError):
                metric(value)
        old = replace(metric(10), evidence_expires_at="2020-01-01T00:00:00Z")
        with self.assertRaises(DomainError) as ctx:
            trend_change(old, metric(12))
        self.assertEqual(ctx.exception.code, "expired")


def registration(**kw):
    fields = (FieldRight("title", frozenset({"raw_store", "internal_display", "export"}),
                         NOW + timedelta(days=2)),)
    args = dict(adapter_id="fixture", adapter_version="1", source_api_version="1",
                endpoint="https://fixture.example/serp", coverage={"countries": ["US"]},
                admission="approved", approval_ref="fixture-approval",
                license_id="fixture-only", license_version="1",
                refresh_seconds=3600, cache_seconds=60, billing_unit="request",
                field_rights=fields, retention_policy="per_field_deadline",
                production_approved=False)
    return SourceRegistration(**{**args, **kw})


class RightsTests(unittest.TestCase):
    def test_missing_registration_is_planned_and_never_admitted(self):
        for kw in (dict(endpoint=None), dict(coverage={}), dict(cache_seconds=None),
                   dict(license_id=None), dict(billing_unit="unknown"),
                   dict(approval_ref=None), dict(retention_policy=None),
                   dict(field_rights=())):
            source = registration(**kw)
            self.assertEqual(source.capability(now=NOW)["status"], "planned")
            with self.assertRaises(DomainError):
                source.admit("title", "raw_store", now=NOW)
        with self.assertRaises(DomainError):
            registration().admit("title", "raw_store", now=NOW, production=True)
        self.assertEqual(registration().capability(now=NOW)["production_ready"], False)

    def test_source_inventory_never_embeds_credentials_or_mutable_rights(self):
        for endpoint in ("https://user:password@fixture.example/api",
                         "https://fixture.example/api?api_key=secret",
                         "http://fixture.example/api", "not-an-endpoint"):
            with self.assertRaises(DomainError):
                registration(endpoint=endpoint)
        source = registration()
        with self.assertRaises(TypeError):
            source.coverage["countries"][0] = "GB"
        self.assertEqual(source.capability(now=NOW)["status"], "available")

    def test_revocation_and_expiry_are_not_erased_by_missing_metadata(self):
        source = registration(admission="revoked", endpoint=None)
        self.assertEqual(source.capability(now=NOW)["status"], "revoked")
        with self.assertRaises(DomainError):
            source.admit("title", "export", now=NOW)

    def test_field_use_expiry_and_unknown_are_fail_closed(self):
        source = registration()
        self.assertEqual(source.admit("title", "export", now=NOW).field, "title")
        for field, use in (("body", "export"), ("title", "embedding"),
                           ("title", "model_input"), ("title", "redistribute")):
            with self.assertRaises(DomainError):
                source.admit(field, use, now=NOW)
        with self.assertRaises(DomainError) as ctx:
            source.admit("title", "export", now=NOW + timedelta(days=2))
        self.assertEqual(ctx.exception.code, "expired")
        with self.assertRaises(DomainError):
            FieldRight("title", frozenset({"export"}), None)

    def test_tenant_asset_grant_and_rights_version_are_all_required(self):
        source = registration()
        grant = Grant(tenant_id="one", asset_id="gsc:one", grant_id="grant:one",
                      version="1", fields=frozenset({"title"}), uses=frozenset({"export"}),
                      expires_at=NOW + timedelta(hours=1))
        principal = Principal("one", frozenset({"grant:one"}), "1")
        authorize(principal, grant, source, "title", "export", now=NOW)
        for p, g in (
            (replace(principal, tenant_id="two"), grant),
            (replace(principal, grant_ids=frozenset()), grant),
            (replace(principal, rights_version="2"), grant),
            (principal, replace(grant, revoked=True)),
            (principal, replace(grant, asset_id="")),
        ):
            with self.assertRaises(DomainError):
                authorize(p, g, source, "title", "export", now=NOW)
        with self.assertRaises(DomainError):
            authorize(principal, grant, source, "title", "export",
                      now=NOW + timedelta(hours=1))

    def test_derived_rights_are_intersection_not_union(self):
        a = FieldRight("value", frozenset({"internal_display", "export"}),
                       NOW + timedelta(days=3))
        b = FieldRight("value", frozenset({"internal_display"}),
                       NOW + timedelta(days=1))
        result = inherit_rights("score", [a, b])
        self.assertEqual(result.allowed_uses, frozenset({"internal_display"}))
        self.assertEqual(result.retention_deadline, NOW + timedelta(days=1))
        with self.assertRaises(DomainError):
            inherit_rights("score", [])


class MetricOracleTests(unittest.TestCase):
    def test_rank_and_censored_transitions(self):
        items = [dict(result_type="paid", organic_position=None, target_url="https://a"),
                 dict(result_type="organic", organic_position=3, target_url="https://a"),
                 dict(result_type="organic", organic_position=7, target_url="https://a")]
        rank = organic_position(items, "https://a", scope=SCOPE, depth=10, complete=True)
        self.assertEqual(rank.value, 3)
        absent = organic_position(items, "https://b", scope=SCOPE, depth=10, complete=True)
        self.assertIsNone(absent.value)
        self.assertEqual(absent.availability, "outside_observed_depth")
        failed = organic_position([], "https://a", scope=SCOPE, depth=10, complete=False)
        self.assertEqual(failed.availability, "not_observed")
        before = replace(rank, value=8)
        self.assertEqual(rank_change(before, rank).value, 5)
        self.assertIsNone(rank_change(rank, absent).value)
        with self.assertRaises(DomainError):
            rank_change(before, replace(rank, metric_scope={**SCOPE, "device": "mobile"}))

    def test_publisher_article_occurrences_do_not_enter_organic_rank(self):
        items = [
            dict(result_type="organic", target_url="https://a",
                 organic_position=3, content_semantics="serp_snippet"),
            dict(result_type="organic", target_url="https://a",
                 organic_position=1, content_semantics="publisher_article"),
        ]
        rank = organic_position(items, "https://a", scope=SCOPE, depth=10, complete=True)
        self.assertEqual(rank.value, 3)

    def test_ratios_keep_numerator_denominator_and_no_sample_is_not_zero(self):
        out = ratio(0, 0, scope=SCOPE)
        self.assertIsNone(out["metric"].value)
        self.assertEqual(out["denominator"], 0)
        self.assertEqual(ratio(0, 5, scope=SCOPE)["metric"].availability, "true_zero")
        for args in ((3, 2), (-1, 2), (1, -2), (True, 1)):
            with self.assertRaises(DomainError):
                ratio(*args, scope=SCOPE)

    def test_metric_export_retains_provenance_nature_scale_and_additivity(self):
        value = metric(15)
        exported = value.to_dict()
        for name in ("provenance_kind", "measurement_nature", "scale_id",
                     "additivity", "quality_flags", "metric_scope"):
            self.assertIn(name, exported)
        self.assertEqual(exported["provenance_kind"], "public_observed")
        self.assertEqual(exported["measurement_nature"], "relative_index")
        self.assertEqual(exported["scale_id"], "request-a")
        self.assertEqual(exported["additivity"], "non_additive")
        self.assertIn("uncertainty_not_provided", exported["quality_flags"])

    def test_top_k_requires_real_depth_and_no_cache_pseudoreplication(self):
        runs = [
            dict(run_id="one", status="succeeded", complete=True, depth=10,
                 position=2, cache_hit=False, scope=SCOPE),
            dict(run_id="one", status="succeeded", complete=True, depth=10,
                 position=2, cache_hit=False, scope=SCOPE),
            dict(run_id="cache", status="succeeded", complete=True, depth=10,
                 position=1, cache_hit=True, scope=SCOPE),
            dict(run_id="two", status="succeeded", complete=True, depth=10,
                 position=None, cache_hit=False, scope=SCOPE),
            dict(run_id="bad", status="failed", complete=False, depth=0,
                 position=None, cache_hit=False, scope=SCOPE),
        ]
        out = top_k_share(runs, k=3, scope=SCOPE)
        self.assertEqual((out["numerator"], out["denominator"]), (1, 2))
        self.assertEqual((out["planned"], out["completed"], out["failed"], out["cached"]),
                         (4, 3, 1, 1))
        self.assertEqual(out["metric"].value, Decimal("0.5"))

    def test_trend_scale_window_and_zero_baseline(self):
        self.assertEqual(trend_change(metric(10), metric(15)).value, Decimal("0.5"))
        for after in (replace(metric(15), scale_id="request-b"),
                      replace(metric(15), metric_scope={**SCOPE, "country": "GB"})):
            with self.assertRaises(DomainError):
                trend_change(metric(10), after)
        result = trend_change(replace(metric(0), availability="true_zero"), metric(1))
        self.assertIsNone(result.value)
        self.assertIn("nonpositive_baseline", result.quality_flags)
        with self.assertRaises(DomainError):
            trend_change(metric(-10), metric(1))
        with self.assertRaises(DomainError):
            trend_change(metric(10), metric(-1))

    def test_close_variants_are_not_counted_per_term(self):
        rows = [dict(group_id="g1", value=1200), dict(group_id="g1", value=1200),
                dict(group_id="g2", value=200)]
        result = search_demand(rows, scope=SCOPE, provenance_kind="authorized_reported",
                               measurement_nature="rounded")
        self.assertEqual(result.value, 1400)
        with self.assertRaises(DomainError):
            search_demand(rows + [dict(group_id="g1", value=1300)], scope=SCOPE,
                          provenance_kind="authorized_reported", measurement_nature="rounded")

    def test_gsc_position_api_and_bq_grains(self):
        rows = [dict(impressions=10, position=2, sum_top_position=10, sum_position=30),
                dict(impressions=30, position=4, sum_top_position=90, sum_position=90)]
        self.assertEqual(gsc_position(rows, source="api", grain="site", scope=SCOPE).value,
                         Decimal("3.5"))
        self.assertEqual(gsc_position(rows, source="bulk", grain="site", scope=SCOPE).value,
                         Decimal("3.5"))
        self.assertEqual(gsc_position(rows, source="bulk", grain="url", scope=SCOPE).value,
                         Decimal("4"))
        self.assertIsNone(gsc_position([], source="api", grain="site", scope=SCOPE).value)
        with self.assertRaises(DomainError):
            gsc_position(rows, source="bulk", grain="mixed", scope=SCOPE)

    def answer(self, aid, **kw):
        return dict(answer_id=aid, observation_id=aid, channel="aio",
                    status="succeeded", feature_detectable=True, triggered=True,
                    answer_complete=True, mention_complete=True, citation_complete=True,
                    citation_capable=True, cache_hit=False, brands=["other", "target"],
                    citation_domains=["example.com"], scope=SCOPE, **kw)

    def test_ai_presence_mention_citation_and_sov_have_distinct_denominators(self):
        one = self.answer("1")
        absent = [{**self.answer(str(i)), "triggered": False} for i in range(2, 11)]
        result = ai_visibility([one, *absent], target_brand="target",
                               target_domain="example.com", tracked_brands=["target", "other"],
                               scope=SCOPE, channel="aio", brand_version="brands:1")
        self.assertEqual(result["presence"]["metric"].value, Decimal("0.1"))
        self.assertEqual(result["mention"]["metric"].value, Decimal(1))
        self.assertEqual(result["citation"]["metric"].value, Decimal(1))
        self.assertEqual(result["sov"]["metric"].value, Decimal("0.5"))
        self.assertEqual(result["first_mention_position"].value, Decimal(2))
        self.assertIsNone(result["mention"]["metric"].interval)

    def test_ai_no_trigger_no_tracked_mentions_failed_and_cached(self):
        a = self.answer("a")
        result = ai_visibility([{**a, "triggered": False}], target_brand="target",
                               target_domain="example.com", tracked_brands=["target"],
                               scope=SCOPE, channel="aio", brand_version="brands:1")
        self.assertEqual(result["presence"]["metric"].availability, "true_zero")
        self.assertIsNone(result["mention"]["metric"].value)
        self.assertIsNone(result["citation"]["metric"].value)
        result = ai_visibility([{**a, "brands": [], "citation_domains": []},
                                {**self.answer("b"), "cache_hit": True},
                                {**self.answer("c"), "status": "failed"}],
                               target_brand="target", target_domain="example.com",
                               tracked_brands=["target"], scope=SCOPE,
                               channel="aio", brand_version="brands:1")
        self.assertEqual(result["mention"]["metric"].availability, "true_zero")
        self.assertEqual(result["citation"]["metric"].availability, "true_zero")
        self.assertIsNone(result["sov"]["metric"].value)
        self.assertIn("no_tracked_mentions", result["sov"]["metric"].quality_flags)
        self.assertEqual(result["mention"]["denominator"], 1)

    def test_ai_mixed_channels_and_repeated_observations_do_not_inflate(self):
        a = self.answer("a")
        result = ai_visibility([a, a], target_brand="target", target_domain="example.com",
                               tracked_brands=["target"], scope=SCOPE,
                               channel="aio", brand_version="brands:1")
        self.assertEqual(result["mention"]["denominator"], 1)
        with self.assertRaises(DomainError):
            ai_visibility([a, {**self.answer("b"), "channel": "generation_api"}],
                          target_brand="target", target_domain="example.com",
                          tracked_brands=["target"], scope=SCOPE,
                          channel="aio", brand_version="brands:1")


if __name__ == "__main__":
    unittest.main()
