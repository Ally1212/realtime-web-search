"""Synthetic independent-reader fixtures; not a real Whale schema or POC."""
import json
import csv
import io
import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError, ObservationLimitations, Query
from realtime.google_evidence import CursorCodec, EvidenceReference
from realtime.google_observations import SERPFeature, SERPItem, SERPObservation
from realtime.google_projection import ObservationProjection, ReaderAdmission
from realtime.google_parser_quality import (
    ParserContract, ParserQualityGate, ParsedMessage,
)
from realtime.google_registry import RightsRegistry
from realtime.google_rights import FieldRight, Grant
from realtime.google_metrics import organic_position
from tests import test_google_domain as domain_tests
from tests.test_google_domain import NOW, registration

FIELDS = frozenset({"query", "variant", "run", "target_url", "title", "snippet", "ranks", "features"})


def observation(*, rid="run:one", when=NOW, text="C++", items=None,
                features=None, fields=FIELDS, **kw):
    query = Query(text)
    reference = EvidenceReference(
        document_id="whale:fixture:" + rid, document_version="v1", observation_id=rid,
        source_adapter="fixture", source_adapter_version="1", source_api_version="1",
        license_id="fixture-only", license_version="1", parser_version="p1",
        source_hash="sha256:" + "a" * 64, grant_id="grant:one", asset_id="asset:one",
        fields=tuple(sorted(fields)), source_observed_at=None, observed_at=when,
        evidence_expires_at=NOW + timedelta(days=1))
    return SERPObservation(
        reference=reference, query=query, variant=domain_tests.IdentityTests().variant(text),
        provider_namespace="fixture", native_id=rid, run_id=rid,
        collection_version="fixture-cohort:1", status="succeeded", returned_depth=10,
        complete=True, cache_hit=False, cache_age_seconds=None,
        items=tuple(items if items is not None else (
            SERPItem("organic:1", "organic", target_url="https://example.com/page", title="Title",
                     snippet="Index snippet, not an article body", organic_position=1,
                     provider_rank_absolute=3),
            SERPItem("ad:1", "paid", target_url="https://example.com/page",
                     provider_rank_absolute=1),
            SERPItem("organic:2", "organic", target_url="https://example.com/page",
                     organic_position=2, provider_rank_absolute=4))),
        features=(SERPFeature("aio:1", "ai_overview", None),) if features is None else tuple(features), **kw)


class FixtureReader:
    """Typed port fixture; never contacts a server or claims production admission."""
    contract_ref = "fixture:reader-contract:1"
    dataset_id = "fixture:dataset:1"
    def __init__(self, *rows):
        self.rows = {r.reference.document_id: r for r in rows}
        self.reads = []

    def read_version(self, tenant_id, reference):
        self.reads.append((tenant_id, reference.document_id, reference.document_version))
        try:
            return self.rows[reference.document_id]
        except KeyError:
            raise DomainError("source_error", "Fixture version is independently unavailable", 503)


def reader_admission(**kw):
    return ReaderAdmission(**dict(
        contract_ref="fixture:reader-contract:1", dataset_id="fixture:dataset:1",
        approval_ref="fixture:approval:1", approved=True, production_approved=False, **kw))


class ObservationContractTests(unittest.TestCase):
    def test_occurrences_are_not_deduplicated_by_url_or_absolute_rank(self):
        row = observation()
        self.assertEqual(len(row.items), 3)
        self.assertEqual(row.items[0].organic_position, 1)
        self.assertEqual(row.items[0].provider_rank_absolute, 3)
        self.assertIsNone(row.items[1].organic_position)
        self.assertEqual(row.fields_used(), FIELDS)
        self.assertIsNone(row.features[0].presence)

    def test_same_url_stays_separate_across_surfaces_and_no_google_total_rank(self):
        organic = observation()
        paid_reference = EvidenceReference(
            document_id="whale:fixture:run:paid", document_version="v1",
            observation_id="run:paid", source_adapter="fixture",
            source_adapter_version="1", source_api_version="1",
            license_id="fixture-only", license_version="1", parser_version="p1",
            source_hash="sha256:" + "a" * 64, grant_id="grant:one",
            asset_id="asset:one",
            fields=frozenset({"query", "variant", "run", "target_url", "ranks"}),
            source_observed_at=None, observed_at=NOW,
            evidence_expires_at=NOW + timedelta(days=1))
        paid = replace(
            observation(
                rid="run:paid",
                items=(SERPItem("paid:1", "paid",
                                target_url="https://example.com/page",
                                provider_rank_absolute=1),),
                features=(), fields=paid_reference.fields),
            reference=paid_reference)
        self.assertNotEqual(organic.variant.surface, "paid")
        self.assertEqual(organic.items[0].target_url, paid.items[0].target_url)
        self.assertNotIn("google_total_rank", organic.header())
        self.assertNotIn("google_total_rank", paid.header())
        self.assertIsNone(paid.items[0].organic_position)

    def test_snippet_and_news_index_never_claim_publisher_article_body(self):
        snippet = SERPItem(
            "news:1", "news", target_url="https://example.com/news",
            title="News index title", snippet="News index snippet",
            content_semantics="news_index")
        article = replace(
            snippet, item_id="article:1", content_semantics="publisher_article",
            publication_source="https://publisher.example")
        self.assertEqual(snippet.fields_used() | {"publication"}, article.fields_used())
        with self.assertRaises(DomainError):
            replace(snippet, content_semantics="article_full_text")
        with self.assertRaises(DomainError):
            replace(snippet, content_semantics="publisher_article")

    def test_rejects_wrong_query_time_field_scope_and_duplicate_occurrence_ids(self):
        row = observation()
        for patch in (
            dict(query=Query("C")), dict(run_id="wrong"),
            dict(items=(row.items[0], row.items[0])),
            dict(features=(row.features[0], row.features[0])),
            dict(returned_depth=True), dict(cache_age_seconds=-1),
            dict(reference=replace(row.reference, fields=("query",))),
        ):
            with self.subTest(patch=patch), self.assertRaises(DomainError):
                replace(row, **patch)

    def test_unknown_coordinates_cache_and_failure_are_not_invented(self):
        row = observation()
        failed = replace(row, status="failed", returned_depth=None, complete=False,
                         cache_hit=None, items=(),
                         reference=replace(row.reference, fields=("query", "variant", "run", "features")))
        self.assertIsNone(failed.returned_depth)
        for patch in (dict(cache_hit=True), dict(status="failed"), dict(returned_depth=1)):
            with self.subTest(patch=patch), self.assertRaises(DomainError):
                replace(row, **patch)

    def test_paid_rank_url_secrets_and_arbitrary_payload_are_rejected(self):
        for kw in (dict(organic_position=True), dict(result_type="paid", organic_position=1),
                   dict(target_url="https://user:secret@example.com/"),
                   dict(target_url="javascript:alert(1)")):
            with self.subTest(kw=kw), self.assertRaises(DomainError):
                SERPItem(**{"item_id": "one", "result_type": "organic", **kw})
        with self.assertRaises(TypeError):
            SERPItem("one", "organic", body_text="Not allowed")

    def test_known_url_with_unknown_position_is_not_outside_observed_depth(self):
        rank = organic_position([{"result_type": "organic", "target_url": "https://example.com/",
                                  "organic_position": None}], "https://example.com/",
                                scope={"fixture": True}, depth=10, complete=True)
        self.assertEqual(rank.availability, "not_observed")
        self.assertIn("rank_not_provided", rank.quality_flags)

    def test_cache_original_time_comparison_handles_fractional_seconds(self):
        row = observation(when=NOW + timedelta(microseconds=100000))
        valid = replace(row, cache_hit=True, cache_original_observed_at=NOW.isoformat())
        self.assertEqual(valid.cache_original_observed_at, NOW.isoformat().replace("+00:00", "Z"))
        self.assertEqual(valid.limitations.entries["cache"].status, "source_applied")
        truncated = replace(
            row, truncated=True, complete=False, status="partial")
        self.assertEqual(
            truncated.limitations.entries["truncated"].status,
            "source_applied",
        )
        with self.assertRaises(DomainError):
            replace(
                row,
                truncated=False,
                limitations=ObservationLimitations({
                    "truncated": {"status": "source_applied"},
                }),
            )


    def test_provider_overdelivery_preserves_requested_and_returned_depth_separately(self):
        row = replace(observation(), returned_depth=20)
        self.assertEqual(row.header()["requested_depth"], 10)
        self.assertEqual(row.header()["returned_depth"], 20)

    def test_secret_request_parameters_never_enter_the_selected_fact_projection(self):
        row = observation()
        for parameters in ({"apiKey": "fixture-secret"}, {"APIKey": "fixture-secret"},
                           {"headers": {"authorization": "fixture-secret"}},
                           {"aio": {"page_token": "fixture-secret"}}):
            with self.subTest(parameters=parameters), self.assertRaises(DomainError):
                replace(row, variant=replace(row.variant, parameters=parameters))


def parser_gate():
    return ParserQualityGate(ParserContract(
        source="fixture", source_version="1", parser_version="p1",
        dom_signature="dom:fixture:1",
        capabilities=frozenset({
            "query", "variant", "run", "target_url", "title", "snippet",
            "ranks", "features", "organic",
        }),
        known_result_types=frozenset({"organic", "paid"}),
    ))


def parser_message(**changes):
    values = dict(
        message_id="message:one", source="fixture", source_version="1",
        parser_version="p1", dom_signature="dom:fixture:1",
        provided_fields=frozenset({
            "query", "variant", "run", "target_url", "title", "snippet",
            "ranks", "features", "organic",
        }),
        result_types=("organic", "paid"), html_bytes=100, confidence=0.95,
    )
    values.update(changes)
    return ParsedMessage(**values)

@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class ProjectionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ["TEST_DATABASE_URL"]
        self.schema = "google_projection_" + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options="-c search_path=" + self.schema)
        self.registry = RightsRegistry(self.dsn)
        self.registry.initialize()
        self.source = registration(field_rights=tuple(FieldRight(
            f, frozenset({"derived_store", "internal_display", "export", "aggregate"}),
            NOW + timedelta(hours=8)) for f in sorted(FIELDS)))
        self.registry.save_source("one", self.source, expected_revision=0,
                                  actor_ref="fixture:operator", now=NOW)
        self.grant = Grant("one", "asset:one", "grant:one", "g1", FIELDS,
                           frozenset({"derived_store", "internal_display", "export", "aggregate"}),
                           NOW + timedelta(hours=4), source_adapter="fixture", source_version="1")
        self.registry.save_grant(self.grant, expected_revision=0,
                                 actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        self.row = observation()
        self.reader = FixtureReader(self.row)
        self.clock = NOW + timedelta(minutes=1)
        self.store = ObservationProjection(self.dsn, registry=self.registry, reader=self.reader,
                                          admission=reader_admission(), production=False,
                                          cursor_codec=CursorCodec(b"p" * 32), clock=lambda: self.clock)
        self.store.initialize()
        self.store.initialize()

    def tearDown(self):
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def project(self, row=None):
        row = row or self.row
        self.reader.rows[row.reference.document_id] = row
        return self.store.rebuild(self.p, row.reference, now=self.clock)

    def read(self, path="/observations", **params):
        return self.store.read(self.p, path, params, now=self.clock)

    def test_initialization_is_independent_and_unconfigured_reader_fails_closed(self):
        self.assertEqual(self.read()["observations"], [])
        with psycopg.connect(self.dsn) as db:
            tables = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s",
                                              (self.schema,))}
        self.assertEqual(tables, {"sf_google_registry", "sf_google_tenant_rights",
                                 "sf_google_projection_states", "sf_google_projection_tombstones",
                                 "sf_google_serp_runs", "sf_google_serp_items", "sf_google_serp_features"})
        self.store.reader = None
        with self.assertRaises(DomainError) as err:
            self.project()
        self.assertEqual(err.exception.code, "not_covered")
        with self.assertRaises(DomainError):
            self.read()

    def test_rebuild_requires_independent_pinned_version_and_is_idempotent(self):
        self.assertTrue(self.project()["created"])
        self.assertFalse(self.project()["created"])
        self.assertEqual(len(self.reader.reads), 2)
        self.assertEqual(len(self.read()["observations"]), 1)
        self.reader.rows[self.row.reference.document_id] = replace(
            self.row, reference=replace(self.row.reference, document_version="v2"))
        with self.assertRaises(DomainError):
            self.store.rebuild(self.p, self.row.reference, now=NOW)

    def test_parser_quality_is_bound_to_formal_projection_and_blocks_low_confidence_rank(self):
        self.store.parser_quality_gate = parser_gate()
        valid = replace(self.row, parser_message=parser_message())
        self.reader.rows[self.row.reference.document_id] = valid
        self.store.rebuild(self.p, valid.reference, now=self.clock)
        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as db:
            header = db.execute(
                "SELECT header FROM sf_google_serp_runs WHERE run_id=%s",
                (valid.run_id,)).fetchone()["header"]
        self.assertTrue(header["parser_quality"]["formal_ranking_eligible"])
        self.assertEqual(header["parser_quality"]["alerts"], [])
        low = replace(valid, parser_message=parser_message(confidence=0.5))
        self.reader.rows[valid.reference.document_id] = low
        with self.assertRaises(DomainError):
            self.store.rebuild(self.p, low.reference, now=self.clock)

    def test_parser_gate_requires_typed_message_and_rejects_empty_html(self):
        self.store.parser_quality_gate = parser_gate()
        with self.assertRaises(DomainError) as error:
            self.store.rebuild(self.p, self.row.reference, now=self.clock)
        self.assertEqual(error.exception.code, "source_error")
        empty = replace(self.row, parser_message=parser_message(html_bytes=0))
        self.reader.rows[self.row.reference.document_id] = empty
        with self.assertRaises(DomainError) as error:
            self.store.rebuild(self.p, empty.reference, now=self.clock)
        self.assertEqual(error.exception.code, "source_error")
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM sf_google_serp_runs").fetchone()[0], 0)

    def test_parser_metadata_without_gate_fails_closed(self):
        value = replace(self.row, parser_message=parser_message())
        self.reader.rows[self.row.reference.document_id] = value
        with self.assertRaises(DomainError) as error:
            self.store.rebuild(self.p, value.reference, now=self.clock)
        self.assertEqual(error.exception.code, "not_covered")
        self.reader.rows.clear()
        with self.assertRaises(DomainError):
            self.store.rebuild(self.p, self.row.reference, now=NOW)

    def test_parallel_rebuild_uses_one_transaction_connection_without_pool_deadlock(self):
        with ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(self.project) for _ in range(32)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(r["created"] for r in results), 1)
        self.assertEqual(len(self.read()["observations"]), 1)

    def test_missing_production_admission_never_becomes_a_real_reader_by_registration(self):
        self.store.production = True
        with self.assertRaises(DomainError):
            self.project()
        self.store.reader.contract_ref = self.store.admission.contract_ref
        self.store.reader.dataset_id = "wrong:dataset"
        with self.assertRaises(DomainError):
            self.project()
        self.store.production = False
        self.store.reader.contract_ref = "different:contract"
        with self.assertRaises(DomainError):
            self.project()

    def test_query_variant_run_items_features_separate_and_preserve_context(self):
        self.project()
        rows = self.read("/keywords")["keywords"]
        self.assertEqual(rows[0]["original_text"], "C++")
        self.assertEqual(self.read("/query-variants")["query_variants"][0]["variant_id"],
                         self.row.variant.variant_id)
        run = self.read()["observations"][0]
        self.assertIsNone(run["source_observed_at"])
        self.assertIsNone(run["cache_age_seconds"])
        self.assertEqual(run["evidence"]["document_version"], "v1")
        items = self.read("/observations/run:one/items")["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(len({i["target_url"] for i in items}), 1)
        self.assertEqual(self.read("/serp-features")["features"][0]["presence"], None)

    def test_condition_scope_and_limitations_survive_projection_and_exports(self):
        original_at = NOW - timedelta(seconds=1800)
        exact = replace(
            self.row,
            variant=replace(
                self.row.variant,
                location={"precision": "coordinates", "basis": "source_provided",
                          "coordinates": ["37.7749", "-122.4194"]},
                channel="organic"),
            cache_hit=True,
            cache_age_seconds=1800,
            cache_original_observed_at=original_at.isoformat(),
            truncated=False,
            limitations=ObservationLimitations({
                "anonymous": {"status": "not_applicable"},
                "low_volume": {"status": "not_observed"},
                "rounded": {"status": "not_applicable"},
                "top_n": {"status": "source_applied", "detail": "page depth 10"},
                "sampled": {"status": "not_applicable"},
                "modeled": {"status": "not_applicable"},
                "cache": {"status": "source_applied", "detail": "age 1800s"},
                "truncated": {"status": "not_applicable"},
            }),
        )
        exact = replace(
            exact, run_id="run:conditions", native_id="run:conditions",
            reference=replace(exact.reference,
                              observation_id="run:conditions",
                              document_id="whale:fixture:run:conditions"))
        projected = exact
        self.project(self.row)
        self.project(projected)
        variants = self.read("/query-variants")["query_variants"]
        self.assertTrue(any(
            v["location"] == projected.variant.to_dict()["location"]
            and v["channel"] == "organic"
            and v["login_state"] == projected.variant.login_state
            for v in variants))
        run = next(v for v in self.read()["observations"]
                   if v["run_id"] == projected.run_id)
        self.assertTrue(run["cache_hit"])
        self.assertEqual(run["cache_age_seconds"], 1800)
        self.assertEqual(run["cache_original_observed_at"],
                         original_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual(run["limitations"]["cache"]["detail"], "age 1800s")
        self.assertEqual(run["limitations"]["top_n"]["detail"], "page depth 10")

        from realtime.google_exports import ExportService
        exports = ExportService(self.dsn, registry=self.registry, projection=self.store,
                                clock=lambda: self.clock)
        exports.initialize()
        created = exports.create(
            self.p, {"source_path": "/observations", "filters": {},
                     "fields": ["run_id", "cache_age_seconds", "limitations"],
                     "format": "jsonl"},
            idempotency_key="condition-limitations", now=self.clock)
        raw, _, _ = exports.content(self.p, created["export_id"], now=self.clock)
        jsonl_rows = [json.loads(line) for line in raw.decode().splitlines()]
        row = next(item for item in jsonl_rows
                   if item["run_id"] == "run:conditions")
        self.assertIsNone(row["limitations"]["low_volume"]["detail"])
        self.assertEqual(row["limitations"]["top_n"]["detail"], "page depth 10")
        baseline = next(item for item in jsonl_rows if item["run_id"] == "run:one")
        self.assertIsNone(baseline["cache_age_seconds"])

        csv_export = exports.create(
            self.p, {"source_path": "/observations", "filters": {},
                     "fields": ["run_id", "cache_age_seconds", "limitations"],
                     "format": "csv"},
            idempotency_key="condition-limitations-csv", now=self.clock)
        raw, _, _ = exports.content(self.p, csv_export["export_id"], now=self.clock)
        csv_rows = list(csv.DictReader(io.StringIO(raw.decode())))
        csv_baseline = next(item for item in csv_rows if item["run_id"] == "run:one")
        self.assertEqual(csv_baseline["cache_age_seconds"], "")
        self.assertNotEqual(csv_baseline["cache_age_seconds"], "0")
        csv_exact = next(item for item in csv_rows if item["run_id"] == "run:conditions")
        self.assertIn("source_applied", csv_exact["limitations"])
        self.assertIn("null", csv_exact["limitations"])

    def test_retry_conflict_is_not_an_update_and_new_run_is_a_new_observation(self):
        self.project()
        changed = replace(self.row, items=(replace(self.row.items[0], title="Changed"),
                                          *self.row.items[1:]))
        with self.assertRaises(DomainError) as err:
            self.project(changed)
        self.assertEqual(err.exception.code, "idempotency_conflict")
        self.reader.rows[self.row.reference.document_id] = self.row
        second = observation(rid="run:two", when=NOW + timedelta(seconds=1))
        self.project(second)
        self.assertEqual(len(self.read()["observations"]), 2)
        self.assertEqual(len(self.read("/keywords")["keywords"]), 1)

    def test_current_authorization_read_back_and_shortest_expiry_are_rechecked(self):
        self.project()
        result = self.read()
        self.assertEqual(result["evidence_expires_at"], self.grant.expires_at.isoformat().replace("+00:00", "Z"))
        self.reader.rows.clear()
        with self.assertRaises(DomainError):
            self.read()
        self.reader.rows[self.row.reference.document_id] = self.row
        self.registry.save_grant(replace(self.grant, revoked=True, version="g2"), expected_revision=1,
                                 actor_ref="fixture:operator", now=NOW)
        with self.assertRaises(DomainError):
            self.read()

    def test_cannot_project_fields_without_derived_store_permission(self):
        self.registry.save_grant(replace(self.grant, uses=frozenset({"internal_display"}), version="g2"),
                                 expected_revision=1, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        with self.assertRaises(DomainError):
            self.project()
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sf_google_serp_runs").fetchone()[0], 0)

    def test_scope_filters_and_cursor_bind_revision_tenant_rights_and_page_size(self):
        self.project()
        self.project(observation(rid="run:two", when=NOW + timedelta(seconds=1), text="C"))
        first = self.read(limit="1")
        cursor = first["next_cursor"]
        self.assertTrue(cursor)
        second = self.read(limit="1", cursor=cursor)
        self.assertNotEqual(first["observations"][0]["run_id"], second["observations"][0]["run_id"])
        self.assertIsNone(second["next_cursor"])
        with self.assertRaises(DomainError):
            self.read(limit="2", cursor=cursor)
        self.assertEqual(len(self.read(query_id=self.row.query.query_id)["observations"]), 1)
        self.assertEqual(len(self.read(observed_from=NOW.isoformat(),
                                      observed_to=(NOW + timedelta(seconds=1)).isoformat())["observations"]), 1)
        self.project(observation(rid="run:three", when=NOW + timedelta(seconds=2)))
        with self.assertRaises(DomainError):
            self.read(limit="1", cursor=cursor)
        with self.assertRaises(DomainError):
            self.read(unsupported="silently ignored")

    def test_tenant_asset_and_hidden_detail_isolation(self):
        self.project()
        p2 = self.registry.principal("two", now=NOW)
        with self.assertRaises(DomainError):
            self.store.read(p2, "/observations", {}, now=NOW)
        hidden = replace(self.p, grant_ids=frozenset(), grant_versions={})
        with self.assertRaises(DomainError):
            self.store.read(hidden, "/observations/run:one/items", {}, now=NOW)
        with self.assertRaises(DomainError) as err:
            self.read("/observations/missing/items")
        self.assertEqual(err.exception.status, 404)

    def test_history_preserves_censoring_failures_and_exact_url_matching(self):
        self.project()
        missing = observation(rid="run:missing", when=NOW + timedelta(seconds=1))
        missing = replace(missing, items=tuple(replace(i, target_url="https://other.example/")
                                               for i in missing.items))
        self.project(missing)
        failed = observation(rid="run:failed", when=NOW + timedelta(seconds=2))
        failed = replace(failed, status="failed", items=(), complete=False, returned_depth=None,
                         reference=replace(failed.reference, fields=("query", "variant", "run", "features")))
        self.project(failed)
        history = self.read("/serp-history", variant_id=self.row.variant.variant_id,
                            target_url="https://example.com/page")["history"]
        self.assertEqual([r["rank"]["availability"] for r in history],
                         ["available", "outside_observed_depth", "source_error"])
        self.assertEqual(history[0]["rank"]["value"], 1)
        self.assertIsNone(history[2]["rank"]["value"])
        with self.assertRaises(DomainError):
            self.read("/serp-history", target_url="https://example.com/page")

    def test_display_license_does_not_implicitly_license_derived_rank_aggregation(self):
        grant = replace(self.grant, uses=self.grant.uses - {"aggregate"}, version="g2")
        self.registry.save_grant(grant, expected_revision=1, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        self.project()
        self.assertTrue(self.read()["observations"])
        with self.assertRaises(DomainError):
            self.read("/serp-history", variant_id=self.row.variant.variant_id,
                      target_url="https://example.com/page")

    def test_expiry_cascades_all_derived_copies_without_retaining_query_text(self):
        self.project()
        self.assertEqual(self.store.purge_expired(now=self.grant.expires_at), 1)
        with psycopg.connect(self.dsn) as db:
            for table in ("sf_google_serp_runs", "sf_google_serp_items", "sf_google_serp_features"):
                self.assertEqual(db.execute(sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier(table))).fetchone()[0], 0)

    def test_empty_items_and_filtered_empty_results_are_success_not_missing_run(self):
        self.project()
        self.assertEqual(self.read("/observations/run:one/items", result_type="nonexistent")["items"], [])
        failed = observation(rid="run:failed")
        failed = replace(failed, status="failed", items=(), complete=False, returned_depth=None,
                         reference=replace(failed.reference, fields=("query", "variant", "run", "features")))
        self.project(failed)
        out = self.read("/observations/run:failed/items")
        self.assertEqual(out["items"], [])
        self.assertIsNotNone(out["evidence_expires_at"])

    def test_revocation_janitor_erases_children_and_prevents_restoration(self):
        self.project()
        revoked = replace(self.grant, revoked=True, version="g2")
        self.registry.save_grant(revoked, expected_revision=1, actor_ref="fixture:operator", now=NOW)
        self.assertEqual(self.store.reconcile_rights("one", now=NOW), 1)
        self.assertEqual(self.store.reconcile_rights("one", now=NOW), 0)
        self.registry.save_grant(replace(self.grant, version="g3"), expected_revision=2,
                                 actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        self.assertEqual(self.read()["observations"], [])
        with self.assertRaises(DomainError):
            self.project()
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sf_google_serp_items").fetchone()[0], 0)

    def test_revoked_source_is_not_an_empty_covered_success_after_physical_purge(self):
        self.project()
        self.registry.save_source("one", replace(self.source, admission="revoked"),
                                  expected_revision=1, actor_ref="fixture:operator", now=NOW)
        self.assertEqual(self.store.reconcile_rights("one", now=NOW), 1)
        self.p = self.registry.principal("one", now=NOW)
        with self.assertRaises(DomainError) as err:
            self.read()
        self.assertEqual(err.exception.code, "use_not_permitted")

    def test_dropped_or_tampered_derived_data_cannot_be_reported_as_independent_readback(self):
        self.project()
        with psycopg.connect(self.dsn) as db:
            db.execute("UPDATE sf_google_serp_items SET item=jsonb_set(item,'{organic_position}','99')"
                       " WHERE item_id='organic:1'")
        for path, params in (
            ("/observations/run:one/items", {}),
            ("/serp-history", dict(variant_id=self.row.variant.variant_id, target_url="https://example.com/page")),
        ):
            with self.subTest(path=path), self.assertRaises(DomainError):
                self.read(path, **params)
        with psycopg.connect(self.dsn) as db:
            db.execute("DELETE FROM sf_google_serp_items")
        with self.assertRaises(DomainError):
            self.read("/observations/run:one/items")

    def test_item_cursor_pagination_uses_occurrence_order_not_source_rank(self):
        self.project()
        first = self.read("/observations/run:one/items", limit="1")
        second = self.read("/observations/run:one/items", limit="1", cursor=first["next_cursor"])
        self.assertEqual(first["items"][0]["item_id"], "organic:1")
        self.assertEqual(second["items"][0]["item_id"], "ad:1")
        self.assertEqual(self.read("/observations/run:one/items", depth="1")["items"][0]["item_id"], "organic:1")

    def test_expiry_during_independent_read_discards_response(self):
        self.project()
        original = self.reader.read_version
        def read(tenant, ref):
            result = original(tenant, ref)
            self.clock = self.grant.expires_at
            return result
        self.reader.read_version = read
        with self.assertRaises(DomainError):
            self.read()

    def test_concurrent_erasure_discards_already_assembled_response(self):
        self.registry.save_subject("one", "key:manager", grant_ids={"grant:one"}, roles={"reader", "manager"},
                                   expected_revision=0, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        self.project()
        manager = self.registry.principal("one", subject_ref="key:manager", now=NOW)
        original = self.reader.read_version
        def read(tenant, ref):
            result = original(tenant, ref)
            self.store.clear_document(manager, ref, reason="source_deleted", now=NOW)
            return result
        self.reader.read_version = read
        with self.assertRaises(DomainError) as err:
            self.read()
        self.assertEqual(err.exception.code, "precondition_failed")

    def test_tombstone_blocks_rebuild_even_if_reader_still_serves_deleted_version(self):
        self.project()
        with self.assertRaises(DomainError):
            self.store.clear_document(self.p, self.row.reference, reason="source_deleted", now=NOW)
        self.registry.save_subject("one", "key:manager", grant_ids={"grant:one"}, roles={"reader", "manager"},
                                   expected_revision=0, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        manager = self.registry.principal("one", subject_ref="key:manager", now=NOW)
        self.store.clear_document(manager, self.row.reference, reason="source_deleted", now=NOW)
        self.assertEqual(self.read()["observations"], [])
        with self.assertRaises(DomainError):
            self.project()

    def test_asset_scoped_manager_cannot_erase_another_assets_copy_of_same_whale_document(self):
        other = replace(self.grant, grant_id="grant:two", asset_id="asset:two")
        self.registry.save_grant(other, expected_revision=0, actor_ref="fixture:operator", now=NOW)
        self.registry.save_subject("one", "key:manager", grant_ids={"grant:one"}, roles={"reader", "manager"},
                                   expected_revision=0, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal("one", now=NOW)
        second = observation(rid="run:second")
        second = replace(second, reference=replace(
            second.reference, document_id=self.row.reference.document_id,
            grant_id=other.grant_id, asset_id=other.asset_id))
        by_run = {r.run_id: r for r in (self.row, second)}
        self.reader.read_version = lambda tenant, ref: by_run[ref.observation_id]
        for row in by_run.values():
            self.store.rebuild(self.p, row.reference, now=self.clock)
        manager = self.registry.principal("one", subject_ref="key:manager", now=NOW)
        self.store.clear_document(manager, self.row.reference, reason="operator_erasure", now=NOW)
        rows = self.read()["observations"]
        self.assertEqual([r["run_id"] for r in rows], ["run:second"])
        with self.assertRaises(DomainError):
            self.store.clear_document(manager, second.reference, reason="operator_erasure", now=NOW)


if __name__ == "__main__":
    unittest.main()
