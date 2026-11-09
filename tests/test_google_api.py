import json
import os
import threading
import unittest
import zlib
import csv
import io
from contextlib import ExitStack
from dataclasses import replace
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from realtime.google_analytics import (
    AnalyticsEvidence,
    AnalyticsReconciliation,
    AnalyticsReport,
    AnalyticsRow,
    AnalyticsSampling,
    AnalyticsService,
    ReconciliationMetric,
)
from realtime.google_api import ApiReadLimits, GoogleDomainService, openapi
from realtime.google_catalog import API_CATALOG, METRIC_CATALOG
from realtime.google_domain import (
    AVAILABILITY, DomainError, MetricValue, ObservationLimitations,
)
from realtime.google_domain_analytics import GoogleDomainAnalytics
from realtime.google_exports import ExportService, ExportWorker
from realtime.google_jobs import AnalysisJobService, AnalysisJobWorker
from realtime.google_live_queries import (
    LiveQueryEvidence,
    LiveQueryResult,
    LiveQueryService,
)
from realtime.google_monitors import MonitorService
from realtime.google_observations import SERPFeature, SERPItem
from realtime.google_quality import GoogleQualityLedger
from realtime.google_reports import ReportService
from realtime.google_rights import FieldRight, Grant, authorize
from realtime.google_search_console import (
    SearchConsoleEvidence,
    SearchConsoleInspection,
    SearchConsoleReport,
    SearchConsoleRow,
    SearchConsoleService,
)
from realtime.google_watchlists import WatchlistService
from realtime.product_http import handle
from tests.test_google_domain import NOW, registration
from tests import test_product as product_tests


class OpenAPITests(unittest.TestCase):
    def test_all_46_groups_are_catalogued_without_calling_planned_routes_implemented(self):
        spec = openapi()
        self.assertEqual(len(API_CATALOG), 46)
        self.assertEqual(len(METRIC_CATALOG), 22)
        ids = set()
        operation_ids = set()
        for path, methods in spec["paths"].items():
            self.assertTrue(path.startswith("/v1/google/"))
            for method, operation in methods.items():
                ids.add(operation["x-requirement-id"])
                self.assertNotIn(operation["operationId"], operation_ids)
                operation_ids.add(operation["operationId"])
                self.assertEqual(operation["security"], [{"bearerAuth": [], "siteAccess": []}])
                self.assertIn(operation["x-implementation-status"],
                              {"metadata", "local_projection", "local_export", "local_export_create",
                               "local_export_cancel", "local_job_foundation",
                               "local_job_create", "local_job_cancel", "local_report",
                               "local_report_create", "local_watchlist_list",
                               "local_watchlist_create", "local_watchlist_remove",
                               "local_monitor_list", "local_monitor_detail",
                               "local_monitor_create", "local_monitor_patch",
                               "local_evidence", "local_usage",
                               "local_domain_analytics",
                               "local_metrics_source",
                               "local_search_console",
                               "local_authorized_analytics",
                               "local_managed_ads",
                               "local_merchant",
                               "local_conditional_local",
                               "local_live_query_list", "local_live_query_detail",
                               "local_live_query_create", "local_live_query_cancel",
                               "local_subscription_list",
                               "local_subscription_detail",
                               "local_subscription_create",
                               "local_subscription_delete",
                               "local_quality",
                               "planned"})
                for name in ("id", "entity_id"):
                    if "{" + name + "}" in path:
                        self.assertTrue(any(p["name"] == name and p["in"] == "path"
                                            for p in operation["parameters"]))
        self.assertEqual(ids, {f"GO-API-{i:03}" for i in range(1, 47)})
        self.assertEqual(spec["paths"]["/v1/google/observations"]["get"]["x-implementation-status"],
                         "local_projection")
        for path in (
                "/v1/google/search-console/reports",
                "/v1/google/search-console/diagnostics"):
            operation = spec["paths"][path]["get"]
            self.assertEqual(
                operation["x-implementation-status"],
                "local_search_console",
            )
            names = [
                parameter.get("name")
                for parameter in operation["parameters"]
                if parameter.get("in") == "query"
            ]
            self.assertEqual(len(names), len(set(names)))
        report_operation = spec["paths"][
            "/v1/google/search-console/reports"]["get"]
        data_state = next(
            parameter for parameter in report_operation["parameters"]
            if parameter.get("name") == "data_state")
        self.assertEqual(
            data_state["schema"]["enum"],
            ["final", "all", "hourly_all"],
        )
        for path in (
                "/v1/google/analytics/reports",
                "/v1/google/analytics/reconciliations"):
            operation = spec["paths"][path]["get"]
            self.assertEqual(
                operation["x-implementation-status"],
                "local_authorized_analytics",
            )
        for path in ("/v1/google/ads/keyword-plans", "/v1/google/ads/reports"):
            operation = spec["paths"][path]["get"]
            self.assertEqual(operation["x-implementation-status"], "local_managed_ads")
            parameters = [
                spec["components"]["parameters"][item["$ref"].rsplit("/", 1)[-1]]
                if "$ref" in item else item for item in operation["parameters"]
            ]
            keys = [(item["name"], item["in"]) for item in parameters]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertNotIn(("gaql", "query"), keys)
        merchant = spec["paths"]["/v1/google/merchant/reports"]["get"]
        self.assertEqual(merchant["x-implementation-status"], "local_merchant")
        merchant_names = {
            item.get("name") for item in merchant["parameters"]
            if item.get("in") == "query"
        }
        self.assertIn("merchant_id", merchant_names)
        self.assertIn("report_version", merchant_names)
        self.assertNotIn("method", merchant_names)
        self.assertNotIn("query", merchant_names)
        for path in (
                "/v1/google/local/grids",
                "/v1/google/local/grids/{id}/observations",
                "/v1/google/places/{id}/reference",
                "/v1/google/local/review-insights"):
            operation = spec["paths"][path]["get"]
            self.assertEqual(
                operation["x-implementation-status"],
                "local_conditional_local",
            )
            names = {
                item.get("name") for item in operation["parameters"]
                if item.get("in") == "query"
            }
            self.assertTrue({"source", "source_version", "asset"} <= names)
        self.assertEqual(spec["components"]["schemas"]["MetricValue"]["properties"]["availability"]["enum"],
                         sorted(AVAILABILITY))

    def test_create_and_patch_contracts_include_idempotency_and_if_match(self):
        spec = openapi()
        for path in (
                "monitors", "watchlists", "analysis-jobs", "exports",
                "live-queries", "reports", "subscriptions"):
            operation = spec["paths"]["/v1/google/" + path]["post"]
            self.assertTrue(any(p["name"] == "Idempotency-Key" and p["required"]
                                for p in operation["parameters"]))
        operation = spec["paths"]["/v1/google/monitors/{id}"]["patch"]
        self.assertTrue(any(p["name"] == "If-Match" and p["required"]
                            for p in operation["parameters"]))
        self.assertIn("maximum", spec["components"]["parameters"]["Limit"]["schema"])
        self.assertEqual(
            spec["paths"]["/v1/google/exports"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/ExportRequest")
        self.assertEqual(
            spec["paths"]["/v1/google/analysis-jobs"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/AnalysisJobRequest")
        self.assertTrue(spec["paths"]["/v1/google/exports/{id}/cancel"]["post"]
                        ["x-local-extension"])
        self.assertTrue(spec["paths"]["/v1/google/analysis-jobs/{id}/cancel"]["post"]
                        ["x-local-extension"])
        self.assertTrue(spec["paths"]["/v1/google/live-queries/{id}/cancel"]["post"]
                        ["x-local-extension"])
        self.assertEqual(
            spec["paths"]["/v1/google/reports"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/ReportRequest")
        self.assertEqual(
            spec["paths"]["/v1/google/watchlists"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/WatchlistRequest")
        self.assertEqual(
            spec["paths"]["/v1/google/monitors"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/MonitorConfigRequest")
        self.assertEqual(
            spec["paths"]["/v1/google/live-queries"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/LiveQueryRequest")
        self.assertEqual(
            spec["paths"]["/v1/google/subscriptions"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/SubscriptionRequest")


class LiveQueryExecutorFixture:
    contract_ref = "fixture:live:1"
    dataset_id = "fixture:live-dataset:1"
    source_adapter = "fixture"
    source_version = "1"
    source_api_version = "1"

    def execute(self, tenant_id, request):
        return LiveQueryResult(
            evidence=LiveQueryEvidence(
                adapter_id="fixture",
                adapter_version="1",
                source_api_version="1",
                contract_ref=self.contract_ref,
                dataset_id=self.dataset_id,
                source_observed_at=NOW.isoformat(),
                evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
                provider_request_id="live:one",
            ),
            provider_namespace="fixture",
            native_id="native:one",
            returned_depth=10,
            complete=True,
            units_used=3,
            items=(SERPItem(
                "live:item:one",
                "organic",
                organic_position=1,
                target_url="https://live.example/result",
                title="Live result",
            ),),
            limitations=("fixture_executor_not_a_production_source",),
        )


class SearchConsoleAdapterFixture:
    contract_ref = "fixture:gsc:1"
    dataset_id = "fixture:gsc-dataset:1"

    def __init__(self, clock):
        self.clock = clock
        self.evidence_dataset = self.dataset_id
        self.source_observed_at = None
        self.evidence_expires_at = None

    def _evidence(self):
        observed = self.clock()
        return SearchConsoleEvidence(
            adapter_id="gsc",
            adapter_version="1",
            source_api_version="1",
            contract_ref=self.contract_ref,
            dataset_id=self.evidence_dataset,
            source_observed_at=(
                self.source_observed_at or observed.isoformat()),
            evidence_expires_at=(
                self.evidence_expires_at
                or (observed + timedelta(hours=1)).isoformat()),
            provider_request_id="gsc:request:1",
        )

    def read_report(self, tenant_id, request):
        rows = ()
        if request["grain"] == ["date", "query", "page"]:
            rows = (
                SearchConsoleRow(
                    "row-a",
                    "2026-11-02",
                    {
                        "date": "2026-11-02",
                        "query": "alpha",
                        "page": "https://example.test/a",
                    },
                    clicks=10,
                    impressions=100,
                    ctr="0.1",
                    position="2.5",
                ),
                SearchConsoleRow(
                    "row-b",
                    "2026-11-03",
                    {
                        "date": "2026-11-03",
                        "query": "beta",
                        "page": "https://example.test/b",
                    },
                    clicks=0,
                    impressions=0,
                    ctr=None,
                    position=None,
                ),
            )
        return SearchConsoleReport(
            evidence=self._evidence(),
            asset_id=request["asset_id"],
            property_id=request["property_id"],
            template_version=request["template_version"],
            grain=tuple(request["grain"]),
            period_start=request["period_start"],
            period_end=request["period_end"],
            source_timezone="America/Los_Angeles",
            data_state=request["data_state"],
            aggregation=request["aggregation"],
            report_kind=request["report_kind"],
            rows=rows,
            response_complete=True,
            statistical_completeness="top_rows_only",
            privacy={
                "anonymous_query_rows": "not_provided_by_source",
                "thresholding": "not_reported_by_source",
            },
            limitations=("top_rows_page_not_statistical_completeness",),
            partition=request["partition"],
        )

    def inspect_url(self, tenant_id, request):
        return SearchConsoleInspection(
            evidence=self._evidence(),
            asset_id=request["asset_id"],
            property_id=request["property_id"],
            inspection_url=request["inspection_url"],
            inspection_version="url-inspection-1",
            verdict="NEUTRAL",
            coverage_state="Submitted and indexed",
            indexing_state="INDEXING_ALLOWED",
            robots_state=None,
            page_fetch_state="SUCCESSFUL",
            canonical_url="https://example.test/a",
            last_crawl_time="2026-11-03T12:00:00Z",
            issues=({"severity": "info", "code": "fixture-known-issue"},),
            quota={
                "state": "source_reported",
                "remaining": 1999,
                "reset_at": "2026-11-04T08:00:00Z",
            },
        )


class GA4AdapterFixture:
    contract_ref = "fixture:ga4:1"
    dataset_id = "fixture:ga4-dataset:1"

    def __init__(self, clock):
        self.clock = clock
        self.evidence_dataset = self.dataset_id
        self.source_observed_at = NOW.isoformat()
        self.evidence_expires_at = (NOW + timedelta(hours=1)).isoformat()
        self.report_value = 10

    def _evidence(self):
        return AnalyticsEvidence(
            adapter_id="ga4",
            adapter_version="1",
            source_api_version="data-api-v1beta",
            contract_ref=self.contract_ref,
            dataset_id=self.evidence_dataset,
            source_observed_at=self.source_observed_at,
            evidence_expires_at=self.evidence_expires_at,
            provider_request_id="ga4-request:one",
        )

    @staticmethod
    def _metric(metric_id, value, row_id, request, dimensions):
        return MetricValue(
            value=value,
            unit="count",
            provenance_kind="authorized_reported",
            measurement_nature="exact_in_scope",
            availability=(
                "true_zero" if value == 0 else "available"),
            metric_scope={
                "asset_id": request["asset_id"],
                "property_id": request["property_id"],
                "template_version": request["template_version"],
                "period_start": request["period_start"],
                "period_end": request["period_end"],
                "report_timezone": "America/Los_Angeles",
                "row_id": row_id,
                "dimensions": dimensions,
                "metric": metric_id,
            },
            definition_version="ga4-data-api-1",
            additivity="semi_additive",
        )

    def read_report(self, tenant_id, request):
        rows = []
        for index, day in enumerate(("2026-11-02", "2026-11-03"), start=1):
            dimensions = {
                name: day if name == "date" else f"{name}:fixture"
                for name in request["dimensions"]
            }
            rows.append(AnalyticsRow(
                row_id=f"row-{index}",
                dimensions=dimensions,
                metrics=tuple(
                    self._metric(
                        metric_id,
                        self.report_value if metric_id == "sessions" else 7,
                        f"row-{index}",
                        request,
                        dimensions,
                    )
                    for metric_id in request["metrics"]
                ),
                sampling_metadata=(
                    AnalyticsSampling(
                        sample_size=90,
                        sampling_space=100,
                        sampling_rate="0.9",
                        reason="sampling_uniform",
                    ),
                ) if index == 1 else (),
                subject_to_thresholding=False,
            ))
        return AnalyticsReport(
            evidence=self._evidence(),
            asset_id=request["asset_id"],
            property_id=request["property_id"],
            template_version=request["template_version"],
            period_start=request["period_start"],
            period_end=request["period_end"],
            report_timezone="America/Los_Angeles",
            dimensions=request["dimensions"],
            metrics_requested=request["metrics"],
            rows=tuple(rows),
            row_count=2,
            source_response_complete=True,
            sampling_metadata=(
                AnalyticsSampling(
                    sample_size=90,
                    sampling_space=100,
                    sampling_rate="0.9",
                    reason="sampling_uniform",
                ),
            ),
            subject_to_thresholding=False,
            data_loss_from_other_row=False,
            schema_restrictions=(),
            property_quota={
                "state": "source_reported",
                "tokensPerDay": {"consumed": 1, "remaining": 199999},
            },
            limitations=("data_api_not_bigquery_event_export",),
        )

    def read_reconciliation(self, tenant_id, request):
        return AnalyticsReconciliation(
            evidence=self._evidence(),
            asset_id=request["asset_id"],
            property_id=request["property_id"],
            template_version=request["template_version"],
            period_start=request["period_start"],
            period_end=request["period_end"],
            report_timezone="America/Los_Angeles",
            dimensions=request["dimensions"],
            api_scope=request["api_scope"],
            bigquery_scope=request["bigquery_scope"],
            compatible=True,
            comparisons=tuple(
                ReconciliationMetric(
                    metric_id=metric_id,
                    api_value=10,
                    bigquery_value=9,
                    delta=1,
                    status="difference_explained",
                    reasons=("thresholded_rows",),
                )
                for metric_id in request["metrics"]
            ),
            limitations=("difference_not_forced_to_zero",),
        )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class DomainHTTPTests(unittest.TestCase):
    def setUp(self):
        product_tests.ProductIntegrationTests.setUp(self)
        self.service = GoogleDomainService(self.dsn)
        self.service.initialize()
        self.registry = self.service.registry
        self.tenant = str(self.owner["id"])
        self.source = registration(production_approved=True, coverage={
            "countries": ["US", "GB"], "languages": ["en"], "devices": ["desktop", "mobile"]})
        self.registry.save_source(self.tenant, self.source, expected_revision=0,
                                  actor_ref="fixture:operator", now=NOW)
        self.grant = Grant(self.tenant, "asset:fixture", "grant:fixture", "1",
                           frozenset({"title"}), frozenset({"raw_store", "internal_display", "export"}),
                           NOW + timedelta(hours=2), source_adapter="fixture", source_version="1")
        self.registry.save_grant(self.grant, expected_revision=0, actor_ref="fixture:operator", now=NOW)
        self.subject = str(self.owner["key_id"])
        self.registry.save_subject(self.tenant, self.subject, grant_ids={"grant:fixture"},
                                   roles={"reader"}, expected_revision=0,
                                   actor_ref="fixture:operator", now=NOW)
        self.clock = NOW
        self.flags = patch.dict(os.environ, {"SEARCHFLOW_GOOGLE_DOMAIN_ENABLED": "true"})
        self.flags.start()
        self.time_patch = patch("realtime.google_http.now", side_effect=lambda: self.clock)
        self.time_patch.start()
        config = self.config
        from realtime.web import Handler as WebHandler
        class Handler(BaseHTTPRequestHandler):
            do_PATCH = WebHandler.do_PATCH
            do_DELETE = WebHandler.do_DELETE
            _do_google_mutation = WebHandler._do_google_mutation
            def do_GET(h):
                if not handle(h, "GET"):
                    h.send_error(404)
            def do_POST(h):
                if not handle(h, "POST"):
                    h.send_error(404)
            def send_json(h, data, status=200):
                raw = json.dumps(data, default=str).encode()
                h.send_response(status)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(raw)))
                h.end_headers()
                h.wfile.write(raw)
            def log_message(h, *_):
                pass
        Handler.config = config
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.time_patch.stop()
        self.flags.stop()
        product_tests.ProductIntegrationTests.tearDown(self)

    def request(self, path, *, key=None, headers=None, payload=None, advance=True, method=None):
        if advance:
            self.clock += timedelta(seconds=1)
        h = {"Authorization": "Bearer " + (key if key is not None else self.account["api_key"]),
             **(headers or {})}
        req = Request(f"http://127.0.0.1:{self.server.server_port}/v1/google/" + path,
                      headers=h, data=json.dumps(payload).encode() if payload is not None else None,
                      method=method)
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.load(r), r.headers
        except HTTPError as exc:
            return exc.code, json.load(exc), exc.headers

    def test_normalized_legacy_data_is_authenticated_private_safe_and_matches_export_contract(self):
        from realtime.serp_store import SerpStore
        from realtime.google_data import DATA_VERSION
        from psycopg.types.json import Jsonb
        serp = SerpStore(self.dsn)
        serp.initialize()
        public = self.base.create_campaign('public data', [], 100, 'direct', ['google_web'])
        private = self.base.create_campaign('private data', [], 100, 'direct', ['google_web'])
        def save(cid, title):
            return serp.record(cid, provider='test', query=title, page=1, language='en',
                results=[], response={'organic_results': [{'title': title, 'link': 'https://example.com/a',
                    'position': 1, 'rank_absolute': 3}]}, elapsed_seconds=1,
                classification='results', http_status=200, error_code=None)
        pub = save(public, 'PUBLIC')
        priv = save(private, 'PRIVATE')
        with self.base.connect() as db:
            db.execute('INSERT INTO serp_task_options(campaign_id,options) VALUES(%s,%s)',
                       (private, Jsonb({'customer_private': True})))
        with patch.dict(os.environ, {'SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED': 'true'}), \
             patch('realtime.google_http.GoogleDomainService', return_value=self.service):
            status, out, _ = self.request('legacy-data', key='')
            self.assertEqual(status, 401)
            status, out, _ = self.request('legacy-data?source=google_web')
            self.assertEqual(status, 200)
            self.assertEqual(out['meta']['versions']['data_contract'], DATA_VERSION)
            self.assertEqual([r['request_id'] for r in out['data']['requests']], [pub])
            self.assertNotIn('PRIVATE', json.dumps(out))
            item = out['data']['requests'][0]['occurrences'][0]
            self.assertEqual((item['organic_position'], item['source_absolute_rank']), (1, 3))
            self.assertEqual(out['data']['whale_readback'], 'not_connected')
            status, detail, _ = self.request('legacy-data/requests/' + pub)
            self.assertEqual(status, 200)
            self.assertEqual(detail['data']['request'], out['data']['requests'][0])
            status, _, _ = self.request('legacy-data/requests/' + priv)
            self.assertEqual(status, 404)
            status, _, _ = self.request('legacy-data?limit=51')
            self.assertEqual(status, 400)
            status, delivery, _ = self.request('legacy-delivery')
            self.assertEqual(status, 200)
            self.assertFalse(delivery['data']['formal_observation_reader'])
            status, _, _ = self.request('legacy-delivery?source=google_web')
            self.assertEqual(status, 400)
            status, _, _ = self.request('legacy-delivery', key='')
            self.assertEqual(status, 401)
        with patch.dict(os.environ, {'SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED': 'false'}), \
             patch('realtime.google_http.GoogleDomainService', return_value=self.service):
            status, _, _ = self.request('legacy-data')
            self.assertNotEqual(status, 200)

    def test_explicit_prd_field_contract_list_detail_meta_and_private_boundary(self):
        from psycopg.types.json import Jsonb
        from realtime.google_field_adaptation import FIELD_VERSION
        public = self.base.create_campaign('public fields', [], 100, 'direct', ['google_web'])
        private = self.base.create_campaign('private fields', [], 100, 'direct', ['google_web'])
        ids = []
        for cid, title in ((public, 'PUBLIC'), (private, 'PRIVATE')):
            ids.append(self.console.record(cid, source='google_web', provider='openserp', query=title,
                page=1, language='en', response={'organic_results':[dict(title=title,link='https://example.com/')],
                    'serp_features':[dict(type='ai_summary',text=title)]},
                elapsed_seconds=.1,classification='results',http_status=200))
        with self.base.connect() as db:
            db.execute('INSERT INTO serp_task_options(campaign_id,options) VALUES(%s,%s)',
                       (private, Jsonb({'customer_private':True})))
        with patch.dict(os.environ, {'SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED':'true'}), \
                patch('realtime.google_http.GoogleDomainService', return_value=self.service):
            status, old, _ = self.request('legacy-data')
            self.assertEqual(status, 200)
            self.assertNotIn('field_adaptation', old['data']['requests'][0])
            status, new, _ = self.request('legacy-data?field_contract=prd-v1')
            self.assertEqual(status, 200)
            self.assertEqual(new['meta']['versions']['field_adaptation'], FIELD_VERSION)
            self.assertEqual(len(new['data']['requests']), 1)
            self.assertNotIn('PRIVATE', str(new))
            status, detail, _ = self.request('legacy-data/requests/'+ids[0]+'?field_contract=prd-v1')
            self.assertEqual(status, 200)
            self.assertEqual(detail['data']['request'], new['data']['requests'][0])
            for path, expected in [('legacy-data/requests/'+ids[1]+'?field_contract=prd-v1',404),
                                   ('legacy-data?field_contract=latest',400),
                                   ('legacy-observations?field_contract=prd-v1',400),
                                   ('legacy-data/requests/'+ids[0]+'?unknown=true',400)]:
                status, _, _ = self.request(path)
                self.assertEqual(status, expected, path)

    def test_auth_disabled_https_and_error_envelope(self):
        for headers, key, code in (({}, "", 401), ({}, "invalid", 401),
                                   ({"X-Forwarded-For": "203.0.113.1",
                                     "X-Forwarded-Proto": "http"}, None, 403)):
            status, out, response_headers = self.request("capabilities", headers=headers, key=key)
            self.assertEqual(status, code)
            self.assertEqual(response_headers["Cache-Control"], "no-store")
            self.assertEqual(response_headers["X-Content-Type-Options"], "nosniff")
            self.assertIsNone(out["data"])
            self.assertTrue(out["meta"]["request_id"])
            self.assertTrue(out["errors"][0]["code"])
        with patch.dict(os.environ, {"SEARCHFLOW_GOOGLE_DOMAIN_ENABLED": "false"}):
            status, out, _ = self.request("capabilities")
        self.assertEqual(status, 503)
        self.assertEqual(out["errors"][0]["code"], "not_covered")

    def test_capabilities_are_rights_metadata_not_claimed_real_google_data(self):
        status, out, _ = self.request("capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(out["meta"]["quality"], "metadata_only")
        self.assertIsNone(out["meta"]["source_observed_at"])
        self.assertIsNone(out["meta"]["data_as_of"])
        self.assertTrue(all(not x["collection_enabled"] for x in out["data"]["domains"]))
        self.assertEqual(out["data"]["sources"][0]["source_adapter"], "fixture")
        self.assertFalse(out["data"]["sources"][0]["adapter_connected"])

    def configure_projection_fixture(self):
        from tests import test_google_projection as fixtures
        from realtime.google_projection import ObservationProjection
        projection_fields = fixtures.FIELDS | {"citation_url", "publication"}
        source = replace(self.source, field_rights=tuple(FieldRight(
            f, frozenset({"derived_store", "internal_display", "export", "aggregate"}),
            NOW + timedelta(hours=1)) for f in sorted(projection_fields)))
        self.registry.save_source(self.tenant, source, expected_revision=1,
                                  actor_ref="fixture:operator", now=self.clock)
        grant = replace(self.grant, fields=projection_fields, version="2",
                        uses=frozenset({"derived_store", "internal_display", "export", "aggregate"}))
        self.registry.save_grant(grant, expected_revision=1, actor_ref="fixture:operator", now=self.clock)
        row = fixtures.observation()
        row = replace(row, reference=replace(row.reference, asset_id=grant.asset_id, grant_id=grant.grant_id))
        reader = fixtures.FixtureReader(row)
        projection = ObservationProjection(
            self.dsn, registry=self.registry, reader=reader, admission=fixtures.reader_admission(),
            cursor_codec=self.service.limits.cursor_codec(), production=False, clock=lambda: self.clock)
        projection.initialize()
        principal = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        projection.rebuild(principal, row.reference, now=self.clock)
        self.service.projection = projection
        self.exports = ExportService(self.dsn, registry=self.registry, projection=projection,
                                     clock=lambda: self.clock)
        self.exports.initialize()
        self.service.exports = self.exports
        self.jobs = AnalysisJobService(self.dsn, registry=self.registry, manifests=self.exports,
                                       clock=lambda: self.clock)
        self.jobs.initialize()
        self.service.jobs = self.jobs
        self.reports = ReportService(
            self.dsn, registry=self.registry, manifests=self.exports,
            clock=lambda: self.clock)
        self.reports.initialize()
        self.service.reports = self.reports
        self.watchlists = WatchlistService(
            self.dsn, registry=self.registry,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock)
        self.watchlists.initialize()
        self.service.watchlists = self.watchlists
        self.monitors = MonitorService(
            self.dsn, registry=self.registry,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock)
        self.monitors.initialize()
        self.service.monitors = self.monitors
        self.analytics = GoogleDomainAnalytics(
            projection, clock=lambda: self.clock, monitors=self.monitors)
        self.service.analytics = self.analytics
        return row, reader

    def configure_search_console_fixture(self):
        field_rights = tuple(
            FieldRight(
                field,
                frozenset({"internal_display"}),
                NOW + timedelta(hours=2),
            )
            for field in ("gsc_report", "gsc_diagnostics")
        )
        source = replace(
            self.source,
            adapter_id="gsc",
            source_api_version="1",
            coverage={
                "countries": ["US"],
                "languages": ["en"],
                "devices": ["desktop", "mobile"],
                "properties": ["sc-domain:example.test"],
            },
            field_rights=field_rights,
        )
        self.registry.save_source(
            self.tenant,
            source,
            expected_revision=0,
            actor_ref="fixture:operator",
            now=self.clock,
        )
        grant = Grant(
            self.tenant,
            "sc-domain:example.test",
            "grant:gsc",
            "1",
            frozenset({"gsc_report", "gsc_diagnostics"}),
            frozenset({"internal_display"}),
            NOW + timedelta(hours=2),
            source_adapter="gsc",
            source_version="1",
        )
        self.registry.save_grant(
            grant,
            expected_revision=0,
            actor_ref="fixture:operator",
            now=self.clock,
        )
        history = self.registry.history(
            self.tenant, "subject", self.subject)
        self.registry.save_subject(
            self.tenant,
            self.subject,
            grant_ids={"grant:fixture", "grant:gsc"},
            roles={"reader"},
            expected_revision=history[-1]["revision"],
            actor_ref="fixture:operator",
            now=self.clock,
        )
        self.search_console_adapter = SearchConsoleAdapterFixture(
            lambda: self.clock)
        self.service.search_console = SearchConsoleService(
            self.registry,
            self.search_console_adapter,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock,
        )
        return grant

    def configure_ga4_fixture(self):
        field_rights = tuple(
            FieldRight(
                field,
                frozenset({"internal_display"}),
                NOW + timedelta(hours=2),
            )
            for field in ("ga4_report", "ga4_reconciliation")
        )
        source = replace(
            self.source,
            adapter_id="ga4",
            source_api_version="data-api-v1beta",
            coverage={
                "countries": ["US"],
                "languages": ["en"],
                "properties": ["properties/123"],
            },
            field_rights=field_rights,
        )
        source_history = self.registry.history(
            self.tenant,
            "source",
            self.registry.source_key(source.adapter_id, source.adapter_version),
        )
        self.registry.save_source(
            self.tenant,
            source,
            expected_revision=len(source_history),
            actor_ref="fixture:operator",
            now=self.clock,
        )
        grant = Grant(
            self.tenant,
            "properties/123",
            "grant:ga4",
            "1",
            frozenset({"ga4_report", "ga4_reconciliation"}),
            frozenset({"internal_display"}),
            NOW + timedelta(hours=2),
            source_adapter="ga4",
            source_version="1",
        )
        grant_history = self.registry.history(
            self.tenant, "grant", grant.grant_id)
        self.registry.save_grant(
            grant,
            expected_revision=len(grant_history),
            actor_ref="fixture:operator",
            now=self.clock,
        )
        history = self.registry.history(
            self.tenant, "subject", self.subject)
        self.registry.save_subject(
            self.tenant,
            self.subject,
            grant_ids={"grant:fixture", "grant:ga4"},
            roles={"reader"},
            expected_revision=history[-1]["revision"],
            actor_ref="fixture:operator",
            now=self.clock,
        )
        self.ga4_adapter = GA4AdapterFixture(lambda: self.clock)
        self.service.authorized_analytics = AnalyticsService(
            self.registry,
            self.ga4_adapter,
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock,
        )
        return grant

    def test_approved_internal_reader_port_connects_real_query_code_not_fake_empty_success(self):
        row, reader = self.configure_projection_fixture()
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            for path, key in (("keywords", "keywords"), ("keywords/" + row.query.query_id, "keywords-detail"),
                              ("query-variants", "query_variants"), ("observations", "observations"),
                              ("observations/run:one", "observations-detail"),
                              ("observations/run:one/items", "items"), ("serp-features", "features"),
                              ("serp-history?variant_id=" + row.variant.variant_id +
                               "&target_url=https%3A%2F%2Fexample.com%2Fpage", "history")):
                with self.subTest(path=path):
                    status, out, _ = self.request(path)
                    self.assertEqual(status, 200, out)
                    self.assertTrue(out["data"][key])
                    self.assertEqual(out["meta"]["quality"], "selected_observations")
                    self.assertEqual(out["meta"]["coverage"]["status"], "observed_subset")
                    self.assertEqual(out["meta"]["data_as_of"], row.reference.observed_at.isoformat().replace("+00:00", "Z"))
                    self.assertIsNone(out["meta"]["source_observed_at"])
                    self.assertEqual(out["meta"]["non_reproducible_reason"], "no_frozen_query_manifest_created")
            reader.rows.clear()
            status, out, _ = self.request("observations")
        self.assertEqual(status, 503)
        self.assertIsNone(out["data"])
        self.assertEqual(out["errors"][0]["code"], "source_error")

    def test_readable_projection_does_not_auto_enable_whale_or_production_collection(self):
        self.configure_projection_fixture()
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("capabilities")
        self.assertEqual(status, 200)
        self.assertFalse(out["data"]["live_enabled"])
        self.assertTrue(all(not d["collection_enabled"] for d in out["data"]["domains"]))

    def test_export_freezes_manifest_validates_hash_and_is_idempotent(self):
        row, reader = self.configure_projection_fixture()
        payload = dict(
            source_path="/observations", filters={},
            fields=["run_id", "observed_at", "status",
                    "provider_namespace", "collection_version"],
            format="jsonl")
        headers = {"Idempotency-Key": "fixture-export-1"}
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, response_headers = self.request(
                "exports", payload=payload, headers=headers)
            self.assertEqual(status, 200, out)
            export = out["data"]
            self.assertEqual(export["status"], "succeeded")
            self.assertEqual(export["row_count"], 1)
            self.assertEqual(out["meta"]["input_manifest_id"], export["manifest_id"])
            self.assertTrue(export["content_sha256"].startswith("sha256:"))
            self.assertIn("content", response_headers.get("Location", ""))
            status, again, _ = self.request("exports", payload=payload, headers=headers)
            self.assertEqual(again["data"]["export_id"], export["export_id"])
            status, detail, _ = self.request("exports/" + export["export_id"])
            self.assertEqual(status, 200)
            self.assertEqual(detail["data"]["manifest_id"], export["manifest_id"])
            req = Request("http://127.0.0.1:%d/v1/google/exports/%s/content" %
                          (self.server.server_port, export["export_id"]),
                          headers={"Authorization": "Bearer " + self.account["api_key"]})
            self.clock += timedelta(seconds=1)
            with urlopen(req, timeout=5) as response:
                content = response.read()
                self.assertEqual(response.headers["Content-Disposition"].split("=")[0],
                                 "attachment; filename")
            self.assertIn(b'"run_id":"run:one"', content)
            self.assertIn(b'"provider_namespace":"fixture"', content)
            self.assertIn(b"\n", content)
            reader.rows.clear()
            with self.assertRaises(HTTPError) as err:
                urlopen(req, timeout=5)
            self.assertEqual(err.exception.code, 503)

    def test_export_csv_escapes_formula_and_rejects_partial_or_stale_scope(self):
        row, reader = self.configure_projection_fixture()
        payload = dict(source_path="/observations/{id}/items", filters={"id": "run:one"},
                       fields=["item_id", "title", "target_url"], format="csv")
        headers = {"Idempotency-Key": "fixture-export-csv"}
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("exports", payload=payload, headers=headers)
            self.assertEqual(status, 200, out)
            export = out["data"]
            req = Request("http://127.0.0.1:%d/v1/google/exports/%s/content" %
                          (self.server.server_port, export["export_id"]),
                          headers={"Authorization": "Bearer " + self.account["api_key"]})
            self.clock += timedelta(seconds=1)
            with urlopen(req, timeout=5) as response:
                text = response.read().decode()
            self.assertTrue(text.startswith("item_id,title,target_url\n"))
            self.assertEqual(len(text.splitlines()), 4)
            self.clock = NOW + timedelta(hours=10)
            with self.assertRaises(HTTPError) as err:
                urlopen(req, timeout=5)
            self.assertEqual(err.exception.code, 410)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("exports", payload=payload, headers=headers)
        self.assertEqual(status, 410)

    def test_export_csv_cells_are_safe_without_mutating_jsonl(self):
        from realtime.google_exports import _safe_cell
        self.assertEqual(_safe_cell("=1+1"), "'=1+1")
        self.assertEqual(_safe_cell("+A1"), "'+A1")
        self.assertEqual(_safe_cell("@cmd"), "'@cmd")
        self.assertEqual(_safe_cell("ordinary"), "ordinary")

    def test_export_requires_the_export_use_even_when_internal_display_is_licensed(self):
        row, reader = self.configure_projection_fixture()
        current = self.registry.grant(self.tenant, "grant:fixture")
        grant = replace(current, uses=current.uses - {"export"}, version="g3")
        self.registry.save_grant(grant, expected_revision=2, actor_ref="fixture:operator", now=NOW)
        self.p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        with self.assertRaises(DomainError) as err:
            self.exports.create(self.p, dict(source_path="/observations/{id}/items",
                                             filters={"id": "run:one"},
                                             fields=["item_id"], format="jsonl"),
                                idempotency_key="no-export-use", now=self.clock)
        self.assertEqual(err.exception.code, "use_not_permitted")

    def test_export_idempotency_conflict_and_frozen_payload_tamper_are_rejected(self):
        row, reader = self.configure_projection_fixture()
        base = dict(source_path="/observations", filters={},
                    fields=["run_id", "status"], format="jsonl")
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("exports", payload=base,
                                          headers={"Idempotency-Key": "same-key"})
            self.assertEqual(status, 200, out)
            status, conflict, _ = self.request(
                "exports", payload={**base, "format": "csv"},
                headers={"Idempotency-Key": "same-key"})
            self.assertEqual(status, 409)
            self.assertEqual(conflict["errors"][0]["code"], "idempotency_conflict")
            status, invalid, _ = self.request(
                "exports", payload={**base, "fields": ["item_id"]},
                headers={"Idempotency-Key": "wrong-field"})
            self.assertEqual(status, 400)
            self.assertEqual(invalid["errors"][0]["code"], "invalid_argument")
            export = out["data"]
            import psycopg
            with psycopg.connect(self.dsn) as db:
                db.execute("UPDATE sf_google_export_manifests SET rows='[]'::jsonb WHERE manifest_id=%s",
                           (export["manifest_id"],))
            req = Request("http://127.0.0.1:%d/v1/google/exports/%s/content" %
                          (self.server.server_port, export["export_id"]),
                          headers={"Authorization": "Bearer " + self.account["api_key"]})
            self.clock += timedelta(seconds=1)
            with self.assertRaises(HTTPError) as err:
                urlopen(req, timeout=5)
            self.assertEqual(err.exception.code, 503)

    def test_export_list_is_explicitly_synchronous_and_schema_is_isolated(self):
        row, reader = self.configure_projection_fixture()
        import psycopg
        with psycopg.connect(self.dsn) as db:
            tables = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s",
                                              (self.schema,))}
        self.assertIn("sf_google_export_manifests", tables)
        self.assertIn("sf_google_exports", tables)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            self.request("exports", payload=dict(source_path="/keywords", filters={},
                                                 fields=["query_id", "original_text"],
                                                 format="jsonl"),
                         headers={"Idempotency-Key": "list-one"})
            status, out, _ = self.request("exports")
        self.assertEqual(status, 200)
        self.assertEqual(len(out["data"]["exports"]), 1)
        self.assertEqual(out["data"]["status"],
                         "synchronous_and_nonblocking_async_export_jobs")
        self.assertEqual(out["data"]["worker_state"],
                         "external_worker_supported_not_deployed")

    def test_export_and_job_list_cursors_do_not_repeat_the_boundary_item(self):
        row, reader, first_export = self.job_manifest()
        p = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.clock += timedelta(seconds=1)
        second_export = self.exports.create(
            p,
            dict(source_path="/observations", filters={},
                 fields=["run_id", "status"], format="jsonl"),
            idempotency_key="list-cursor-second",
            now=self.clock,
        )
        first_page = self.exports.list(p, {"limit": "1"}, now=self.clock)
        self.assertEqual(
            first_page["exports"][0]["export_id"],
            second_export["export_id"],
        )
        self.assertTrue(first_page["next_cursor"])
        second_page = self.exports.list(
            p,
            {"limit": "1", "cursor": first_page["next_cursor"]},
            now=self.clock,
        )
        self.assertEqual(
            second_page["exports"][0]["export_id"],
            first_export["export_id"],
        )
        self.assertIsNone(second_page["next_cursor"])
        with self.assertRaises(DomainError):
            self.exports.list(
                p,
                {"limit": "2", "cursor": first_page["next_cursor"]},
                now=self.clock,
            )

        first_job = self.jobs.create(
            p,
            {"manifest_id": first_export["manifest_id"],
             "analysis_type": "item-coverage"},
            idempotency_key="job-list-cursor-one",
            now=self.clock,
        )
        self.clock += timedelta(seconds=1)
        second_job = self.jobs.create(
            p,
            {"manifest_id": first_export["manifest_id"],
             "analysis_type": "rank-summary"},
            idempotency_key="job-list-cursor-two",
            now=self.clock,
        )
        first_page = self.jobs.list(p, {"limit": "1"}, now=self.clock)
        self.assertEqual(first_page["jobs"][0]["job_id"], second_job["job_id"])
        self.assertTrue(first_page["next_cursor"])
        second_page = self.jobs.list(
            p,
            {"limit": "1", "cursor": first_page["next_cursor"]},
            now=self.clock,
        )
        self.assertEqual(second_page["jobs"][0]["job_id"], first_job["job_id"])
        self.assertIsNone(second_page["next_cursor"])
        with self.assertRaises(DomainError):
            self.jobs.list(
                p,
                {"limit": "2", "cursor": first_page["next_cursor"]},
                now=self.clock,
            )

    def test_domain_metric_matches_jsonl_and_csv_recomputation(self):
        row, reader = self.configure_projection_fixture()
        p = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        fields = [
            "run_id", "result_type", "target_url", "organic_position", "domain",
        ]
        exports = {}
        for fmt in ("jsonl", "csv"):
            exports[fmt] = self.exports.create(
                p,
                dict(source_path="/observations/{id}/items",
                     filters={"id": "run:one"},
                     fields=fields, format=fmt),
                idempotency_key="metric-consistency-" + fmt,
                now=self.clock,
            )
        window = {
            "observed_from": NOW.isoformat(),
            "observed_to": (NOW + timedelta(hours=1)).isoformat(),
            "domains": "example.com,other.test",
            "metric": "top10_share",
        }
        api = self.analytics.read(
            p, "/comparisons", window, now=self.clock)
        api_example = next(
            item for item in api["domains"] if item["domain"] == "example.com")
        self.assertEqual(
            (api_example["numerator"], api_example["denominator"]),
            (1, 1),
        )

        raw, _, _ = self.exports.content(
            p, exports["jsonl"]["export_id"], now=self.clock)
        jsonl_rows = [
            json.loads(line) for line in raw.decode().splitlines() if line
        ]
        raw, _, _ = self.exports.content(
            p, exports["csv"]["export_id"], now=self.clock)
        csv_rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))

        def recompute(rows):
            runs = {}
            for item in rows:
                if item.get("result_type") != "organic":
                    continue
                domain = (
                    item.get("domain")
                    or (urlparse(item.get("target_url") or "").hostname or "")
                )
                position = item.get("organic_position")
                if position in {"", None}:
                    position = None
                else:
                    position = int(position)
                runs.setdefault(item["run_id"], []).append(
                    (domain, position))
            values = list(runs.values())
            return (
                sum(
                    any(domain == "example.com"
                        and position is not None and position <= 10
                        for domain, position in items)
                    for items in values
                ),
                len(values),
            )

        self.assertEqual(
            recompute(jsonl_rows),
            (api_example["numerator"], api_example["denominator"]),
        )
        self.assertEqual(
            recompute(csv_rows),
            (api_example["numerator"], api_example["denominator"]),
        )

    def test_export_expiry_purge_removes_files_and_manifests(self):
        row, reader = self.configure_projection_fixture()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        export = self.exports.create(
            p, dict(source_path="/observations", filters={},
                    fields=["run_id", "status"], format="jsonl"),
            idempotency_key="purge-one", now=self.clock)
        self.assertEqual(self.exports.purge_expired(now=self.grant.expires_at), 1)
        import psycopg
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sf_google_exports").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM sf_google_export_manifests").fetchone()[0], 0)

    def test_download_rechecks_field_expiry_after_the_final_pinned_version_read(self):
        self.configure_projection_fixture()
        principal=self.registry.principal(self.tenant,subject_ref=self.subject,now=self.clock)
        export=self.exports.create(principal,dict(source_path='/observations',filters={},
            fields=['run_id','status'],format='jsonl'),idempotency_key='expiry-during-download',now=self.clock)
        read=self.exports.projection._read_version
        def expire_after_read(*args,**kwargs):
            result=read(*args,**kwargs)
            self.clock=NOW+timedelta(hours=1,seconds=1)
            return result
        req=Request('http://127.0.0.1:%d/v1/google/exports/%s/content' % (self.server.server_port,export['export_id']),
                    headers={'Authorization':'Bearer '+self.account['api_key']})
        with patch('realtime.google_http.GoogleDomainService',return_value=self.service), \
             patch.object(self.exports.projection,'_read_version',side_effect=expire_after_read):
            with self.assertRaises(HTTPError) as error:urlopen(req,timeout=5)
        self.assertEqual(error.exception.code,410)

    def test_direct_export_service_rechecks_revocation_after_final_version_read(self):
        self.configure_projection_fixture()
        principal=self.registry.principal(self.tenant,subject_ref=self.subject,now=self.clock)
        export=self.exports.create(principal,dict(source_path='/observations',filters={},
            fields=['run_id','status'],format='jsonl'),idempotency_key='revoke-during-download',now=self.clock)
        read=self.exports.projection._read_version;calls=0
        def revoke_after_read(*args,**kwargs):
            nonlocal calls
            result=read(*args,**kwargs);calls+=1
            grant=self.registry.grant(self.tenant,self.grant.grant_id)
            self.registry.save_grant(replace(grant,revoked=True,version='3'),expected_revision=2,
                actor_ref='fixture:operator',now=self.clock)
            return result
        with patch.object(self.exports.projection,'_read_version',side_effect=revoke_after_read):
            with self.assertRaises(DomainError) as error:
                self.exports.content(principal,export['export_id'],now=self.clock)
        self.assertEqual(calls,1)
        self.assertEqual(error.exception.code,'not_authorized')

    def test_async_export_chunks_recover_lease_and_download_frozen_content(self):
        row, reader = self.configure_projection_fixture()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        payload = dict(source_path="/observations/{id}/items", filters={"id": "run:one"},
                       fields=["item_id", "title", "target_url"], format="csv", **{"async": True})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=payload, headers={"Idempotency-Key": "async-export-one"})
        self.assertEqual(status, 202, out)
        queued = out["data"]
        self.assertEqual(queued["status"], "queued")
        self.assertIsNone(queued["manifest_id"])
        import psycopg
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_exports SET status='running',started_at=%s,
                          worker_ref='worker:dead',lease_token='lease:dead',
                          lease_expires_at=%s,heartbeat_at=%s,attempts=1
                          WHERE tenant_id=%s AND export_id=%s""",
                       (NOW, NOW, NOW, self.tenant, queued["export_id"]))
        with patch("realtime.google_exports.PAGE_SIZE", 2):
            result = ExportWorker(
                self.exports, worker_ref="worker:recovery", clock=lambda: self.clock
            ).run_once(now=self.clock)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["progress"],
                         {"rows_done": 3, "rows_total": 3, "percent": 100.0})
        with psycopg.connect(self.dsn) as db:
            chunks = db.execute("""SELECT content FROM sf_google_export_chunks
                                  WHERE tenant_id=%s AND export_id=%s
                                  ORDER BY chunk_index""",
                               (self.tenant, queued["export_id"])).fetchall()
        self.assertEqual(len(chunks), 2)
        rendered = b"".join(zlib.decompress(chunk[0]) for chunk in chunks).decode()
        self.assertEqual(rendered.count("item_id,title,target_url\n"), 1)
        self.assertEqual(len(rendered.splitlines()), 4)
        req = Request("http://127.0.0.1:%d/v1/google/exports/%s/content" %
                      (self.server.server_port, queued["export_id"]),
                      headers={"Authorization": "Bearer " + self.account["api_key"]})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            with urlopen(req, timeout=5) as response:
                self.assertEqual(response.read().decode(), rendered)
            status, job, _ = self.request(
                "analysis-jobs",
                payload={"manifest_id": result["manifest_id"],
                         "analysis_type": "rank-summary"},
                headers={"Idempotency-Key": "async-manifest-analysis"})
        self.assertEqual(status, 202, job)
        self.assertEqual(job["data"]["status"], "queued")

    def test_queued_export_cancel_is_terminal_and_worker_skips_it(self):
        row, reader = self.configure_projection_fixture()
        payload = dict(source_path="/observations", filters={},
                       fields=["run_id", "status"], format="jsonl", **{"async": True})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=payload, headers={"Idempotency-Key": "async-export-cancel"})
            self.assertEqual(status, 202, out)
            export_id = out["data"]["export_id"]
            status, canceled, _ = self.request(
                "exports/" + export_id + "/cancel", payload={})
        self.assertEqual(status, 200, canceled)
        self.assertEqual(canceled["data"]["status"], "canceled")
        self.assertIsNone(ExportWorker(
            self.exports, worker_ref="worker:test", clock=lambda: self.clock
        ).run_once(now=self.clock))
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, detail, _ = self.request("exports/" + export_id)
        self.assertEqual(status, 200)
        self.assertEqual(detail["data"]["status"], "canceled")

    def test_export_over_sync_page_limit_is_queued_automatically(self):
        row, reader = self.configure_projection_fixture()
        original_read = self.service.projection.read
        def read_with_more(principal, path, parameters, *, now):
            page = original_read(principal, path, parameters, now=now)
            if parameters.get("limit") == "100":
                page["next_cursor"] = "fixture-more-than-one-page"
            return page
        payload = dict(source_path="/observations", filters={},
                       fields=["run_id", "status"], format="jsonl")
        with patch.object(self.service.projection, "read", side_effect=read_with_more), \
                patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=payload,
                headers={"Idempotency-Key": "automatic-async-export"})
        self.assertEqual(status, 202, out)
        self.assertEqual(out["data"]["status"], "queued")
        self.assertIsNone(out["data"]["manifest_id"])
        self.assertTrue(out["data"]["cancel_path"].endswith("/cancel"))

    def test_running_export_cancel_stops_before_next_chunk(self):
        row, reader = self.configure_projection_fixture()
        payload = dict(source_path="/observations/{id}/items", filters={"id": "run:one"},
                       fields=["item_id", "target_url"], format="jsonl", **{"async": True})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=payload,
                headers={"Idempotency-Key": "running-cancel-export"})
        self.assertEqual(status, 202, out)
        export_id = out["data"]["export_id"]
        original_read = self.service.projection.read
        def read_then_cancel(principal, path, parameters, *, now):
            page = original_read(principal, path, parameters, now=now)
            import psycopg
            with psycopg.connect(self.dsn) as db:
                db.execute("""UPDATE sf_google_exports SET cancel_requested=true
                              WHERE tenant_id=%s AND export_id=%s""",
                           (self.tenant, export_id))
            return page
        with patch("realtime.google_exports.PAGE_SIZE", 2), \
                patch.object(self.service.projection, "read", side_effect=read_then_cancel):
            result = ExportWorker(
                self.exports, worker_ref="worker:cancel", clock=lambda: self.clock
            ).run_once(now=self.clock)
        self.assertEqual(result["status"], "canceled")
        import psycopg
        with psycopg.connect(self.dsn) as db:
            chunks = db.execute("""SELECT count(*) FROM sf_google_export_chunks
                                  WHERE tenant_id=%s AND export_id=%s""",
                               (self.tenant, export_id)).fetchone()[0]
        self.assertEqual(chunks, 0)

    def test_async_export_chunk_tamper_and_source_failure_fail_closed(self):
        row, reader = self.configure_projection_fixture()
        payload = dict(source_path="/observations/{id}/items", filters={"id": "run:one"},
                       fields=["item_id", "target_url"], format="jsonl", **{"async": True})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=payload,
                headers={"Idempotency-Key": "tamper-export"})
        self.assertEqual(status, 202, out)
        export_id = out["data"]["export_id"]
        with patch("realtime.google_exports.PAGE_SIZE", 2):
            result = ExportWorker(
                self.exports, worker_ref="worker:tamper", clock=lambda: self.clock
            ).run_once(now=self.clock)
        self.assertEqual(result["status"], "succeeded")
        import psycopg
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_export_chunks SET content=%s
                          WHERE tenant_id=%s AND export_id=%s AND chunk_index=0""",
                       (zlib.compress(b"tampered\n"), self.tenant, export_id))
        req = Request("http://127.0.0.1:%d/v1/google/exports/%s/content" %
                      (self.server.server_port, export_id),
                      headers={"Authorization": "Bearer " + self.account["api_key"]})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            with self.assertRaises(HTTPError) as err:
                urlopen(req, timeout=5)
        self.assertEqual(err.exception.code, 503)

        second = dict(source_path="/observations", filters={},
                      fields=["run_id", "status"], format="jsonl", **{"async": True})
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "exports", payload=second,
                headers={"Idempotency-Key": "source-failure-export"})
        self.assertEqual(status, 202, out)
        reader.rows.clear()
        failed = ExportWorker(
            self.exports, worker_ref="worker:source-failure", clock=lambda: self.clock
        ).run_once(now=self.clock)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "source_error")
        self.assertIsNone(failed["manifest_id"])

    def job_manifest(self):
        row, reader = self.configure_projection_fixture()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        export = self.exports.create(
            p, dict(source_path="/observations/{id}/items", filters={"id": "run:one"},
                    fields=["item_id", "target_url", "organic_position"],
                    format="jsonl"), idempotency_key="job-source", now=self.clock)
        return row, reader, export

    def test_analysis_job_is_queued_until_separate_worker_claims_it(self):
        row, reader, export = self.job_manifest()
        payload = {"manifest_id": export["manifest_id"], "analysis_type": "rank-summary"}
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("analysis-jobs", payload=payload,
                                          headers={"Idempotency-Key": "job-one"})
            self.assertEqual(status, 202, out)
            job = out["data"]
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["result"], None)
            self.assertEqual(out["meta"]["input_manifest_id"], export["manifest_id"])
            status, again, _ = self.request(
                "analysis-jobs", payload=payload, headers={"Idempotency-Key": "job-one"})
            self.assertEqual(again["data"]["job_id"], job["job_id"])
            status, listed, _ = self.request("analysis-jobs")
            self.assertEqual(status, 200)
            self.assertEqual(listed["data"]["worker_state"],
                             "external_worker_supported_not_deployed")
            self.assertTrue(listed["data"]["queued_is_not_success"])
        result = self.jobs.run_once(tenant_id=self.tenant, worker_ref="worker:test", now=self.clock)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["analysis_type"], "rank-summary")
        self.assertEqual(result["result"]["observed_items"], 3)
        self.assertEqual(result["result"]["organic_items"], 2)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, detail, _ = self.request("analysis-jobs/" + job["job_id"])
        self.assertEqual(status, 200)
        self.assertEqual(detail["data"]["status"], "succeeded")
        self.assertIn("unknown_positions_are_not_beyond_depth",
                      detail["data"]["result"]["limitations"])
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, replay, _ = self.request(
                "analysis-jobs", payload=payload, headers={"Idempotency-Key": "job-one"})
        self.assertEqual(status, 202)
        self.assertEqual(replay["data"]["job_id"], job["job_id"])
        self.assertEqual(replay["data"]["status"], "succeeded")

    def test_worker_recovers_expired_lease_and_reports_progress(self):
        row, reader, export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        job = self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                  "analysis_type": "item-coverage"},
                               idempotency_key="job-recover", now=self.clock)
        import psycopg
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_analysis_jobs SET status='running',started_at=%s,
                          worker_ref='worker:dead',lease_token='lease:dead',
                          lease_expires_at=%s,heartbeat_at=%s,attempts=1
                          WHERE tenant_id=%s AND job_id=%s""",
                       (NOW, NOW + timedelta(seconds=1), NOW,
                        self.tenant, job["job_id"]))
        self.clock += timedelta(seconds=10)
        result = AnalysisJobWorker(
            self.jobs, worker_ref="worker:recovery", clock=lambda: self.clock
        ).run_once(now=self.clock)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["progress"], {"done": 3, "total": 3, "percent": 100.0})
        self.assertIsNone(result["partial_result"])
        with psycopg.connect(self.dsn) as db:
            statuses = [r[0] for r in db.execute(
                """SELECT status FROM sf_google_analysis_job_events
                   WHERE tenant_id=%s AND job_id=%s ORDER BY seq""",
                (self.tenant, job["job_id"]))]
        self.assertEqual(statuses, ["queued", "lease_recovered", "running", "succeeded"])

    def test_worker_heartbeat_cas_and_exhausted_lease_are_terminal(self):
        row, reader, export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        job = self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                  "analysis_type": "rank-summary"},
                               idempotency_key="job-heartbeat", now=self.clock)
        import psycopg
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_analysis_jobs SET status='running',started_at=%s,
                          worker_ref='worker:live',lease_token='lease:live',
                          lease_expires_at=%s,heartbeat_at=%s,attempts=1
                          WHERE tenant_id=%s AND job_id=%s""",
                       (NOW, self.clock + timedelta(minutes=2), NOW,
                        self.tenant, job["job_id"]))
        self.assertFalse(self.jobs.heartbeat(
            self.tenant, job["job_id"], "lease:wrong", now=self.clock))
        self.assertTrue(self.jobs.heartbeat(
            self.tenant, job["job_id"], "lease:live", now=self.clock))
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_analysis_jobs SET attempts=3,
                          lease_expires_at=%s WHERE tenant_id=%s AND job_id=%s""",
                       (NOW, self.tenant, job["job_id"]))
        self.assertEqual(self.jobs.recover_expired_leases(
            now=self.clock, tenant_id=self.tenant), 1)
        detail = self.jobs.detail(p, job["job_id"], now=self.clock)
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["error"]["code"], "worker_lease_exhausted")
        self.assertIsNone(detail["result"])

    def test_canceled_crashed_worker_is_not_run_as_success(self):
        row, reader, export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        job = self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                  "analysis_type": "item-coverage"},
                               idempotency_key="job-canceled-crash", now=self.clock)
        import psycopg
        with psycopg.connect(self.dsn) as db:
            db.execute("""UPDATE sf_google_analysis_jobs SET status='running',started_at=%s,
                          cancel_requested=true,worker_ref='worker:dead',
                          lease_token='lease:dead',lease_expires_at=%s,attempts=1
                          WHERE tenant_id=%s AND job_id=%s""",
                       (NOW, NOW, self.tenant, job["job_id"]))
        self.assertIsNone(AnalysisJobWorker(
            self.jobs, worker_ref="worker:recovery", clock=lambda: self.clock
        ).run_once(now=self.clock))
        detail = self.jobs.detail(p, job["job_id"], now=self.clock)
        self.assertEqual(detail["status"], "canceled")
        self.assertIsNone(detail["result"])

    def test_analysis_job_rejects_non_item_manifest_and_cancel_is_terminal(self):
        row, reader, item_export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        observations = self.exports.create(
            p, dict(source_path="/observations", filters={}, fields=["run_id"],
                    format="jsonl"), idempotency_key="job-nonitems", now=self.clock)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "analysis-jobs", payload={"manifest_id": observations["manifest_id"],
                                         "analysis_type": "item-coverage"},
                headers={"Idempotency-Key": "job-invalid"})
            self.assertEqual(status, 400)
            status, out, _ = self.request(
                "analysis-jobs", payload={"manifest_id": item_export["manifest_id"],
                                         "analysis_type": "item-coverage"},
                headers={"Idempotency-Key": "job-cancel"})
            job = out["data"]
            status, canceled, _ = self.request(
                "analysis-jobs/" + job["job_id"] + "/cancel", payload={})
            self.assertEqual(status, 200)
            self.assertEqual(canceled["data"]["status"], "canceled")
            status, terminal, _ = self.request(
                "analysis-jobs/" + job["job_id"] + "/cancel", payload={})
            self.assertEqual(status, 409)

    def test_analysis_job_expiry_and_worker_absence_are_explicit(self):
        row, reader, export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        job = self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                  "analysis_type": "item-coverage"},
                               idempotency_key="job-expire", now=self.clock)
        self.clock = NOW + timedelta(hours=2)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, detail, _ = self.request("analysis-jobs/" + job["job_id"])
        self.assertEqual(status, 200)
        self.assertEqual(detail["data"]["status"], "expired")
        self.assertIsNone(detail["data"]["result"])

    def test_completed_job_result_is_blocked_after_current_grant_revocation(self):
        row, reader, export = self.job_manifest()
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        job = self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                  "analysis_type": "item-coverage"},
                               idempotency_key="job-revoke", now=self.clock)
        self.assertEqual(self.jobs.run_once(tenant_id=self.tenant, worker_ref="worker:test",
                                           now=self.clock)["status"], "succeeded")
        current = self.registry.grant(self.tenant, "grant:fixture")
        self.registry.save_grant(replace(current, revoked=True, version="g3"),
                                 expected_revision=2, actor_ref="fixture:operator", now=self.clock)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("analysis-jobs/" + job["job_id"])
        self.assertEqual(status, 403)
        self.assertIsNone(out["data"])

    def test_analysis_job_requires_aggregate_use_not_just_export_or_display(self):
        row, reader, export = self.job_manifest()
        current = self.registry.grant(self.tenant, "grant:fixture")
        grant = replace(current, uses=current.uses - {"aggregate"}, version="g3")
        self.registry.save_grant(grant, expected_revision=2, actor_ref="fixture:operator", now=self.clock)
        p = self.registry.principal(self.tenant, subject_ref=self.subject, now=self.clock)
        with self.assertRaises(DomainError) as err:
            self.jobs.create(p, {"manifest_id": export["manifest_id"],
                                "analysis_type": "rank-summary"},
                             idempotency_key="no-aggregate", now=self.clock)
        self.assertEqual(err.exception.code, "use_not_permitted")

    def test_deterministic_report_carries_evidence_versions_and_counterexamples(self):
        row, reader, export = self.job_manifest()
        payload = {
            "manifest_id": export["manifest_id"],
            "report_type": "deterministic",
            "research_question": "Which fixes are worth testing first?",
        }
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, headers = self.request(
                "reports", payload=payload,
                headers={"Idempotency-Key": "report-one"})
            self.assertEqual(status, 201, out)
            report = out["data"]
            self.assertTrue(report["deterministic"])
            self.assertFalse(report["ai_generated"])
            self.assertEqual(report["manifest_id"], export["manifest_id"])
            self.assertEqual(report["analysis"]["observed_items"], 3)
            self.assertEqual(report["analysis"]["organic_position_unknown"], 0)
            self.assertTrue(report["conclusions"])
            self.assertTrue(report["counterexamples"])
            self.assertIn("unknown_positions_are_not_beyond_depth",
                          report["limitations"])
            self.assertTrue(report["pending_verification_actions"])
            self.assertTrue(report["input_manifest_sha256"].startswith("sha256:"))
            principal = self.registry.principal(
                self.tenant, subject_ref=self.subject, now=self.clock)
            self.assertEqual(
                report["report_sha256"],
                self.reports.detail(
                    principal, report["report_id"], now=self.clock)["report_sha256"])
            self.assertTrue(report["rights"][0]["source_hash"].startswith("sha256:"))
            self.assertEqual(report["rights"][0]["parser_version"], "p1")
            self.assertIn("report", report["versions"])
            self.assertEqual(
                report["observation_limitations"]["run:one"]["limitations"],
                {},
            )
            self.assertIn("Location", headers)
            status, replay, _ = self.request(
                "reports", payload=payload,
                headers={"Idempotency-Key": "report-one"})
            self.assertEqual(status, 201)
            self.assertEqual(replay["data"]["report_id"], report["report_id"])
            status, second_report, _ = self.request(
                "reports",
                payload={**payload, "research_question": "Second fixed manifest"},
                headers={"Idempotency-Key": "report-two"},
            )
            self.assertEqual(status, 201, second_report)
            status, listed, _ = self.request("reports?limit=1")
            self.assertEqual(status, 200, listed)
            self.assertEqual(
                listed["data"]["reports"][0]["report_id"],
                second_report["data"]["report_id"])
            self.assertFalse(listed["data"]["result_payload_included"])
            self.assertNotIn("result", listed["data"]["reports"][0])
            cursor = listed["data"]["next_cursor"]
            self.assertTrue(cursor)
            status, second_page, _ = self.request(
                "reports?limit=1&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 200, second_page)
            self.assertEqual(
                second_page["data"]["reports"][0]["report_id"],
                report["report_id"])
            self.assertIsNone(second_page["data"]["next_cursor"])
            status, rebound, _ = self.request(
                "reports?limit=2&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 400)
            self.assertEqual(rebound["errors"][0]["code"], "invalid_argument")
            reader.reads.clear()
            for _ in range(50):
                replayed = self.reports.detail(
                    principal, report["report_id"], now=self.clock)
                self.assertEqual(
                    replayed["report_sha256"], report["report_sha256"])
            self.assertGreaterEqual(len(reader.reads), 50)
            status, conflict, _ = self.request(
                "reports", payload={**payload, "research_question": "Different"},
                headers={"Idempotency-Key": "report-one"})
            self.assertEqual(status, 409)
            self.assertEqual(conflict["errors"][0]["code"], "idempotency_conflict")

            import psycopg
            with psycopg.connect(self.dsn) as db:
                db.execute(
                    """UPDATE sf_google_reports
                       SET result=jsonb_set(result,'{analysis,observed_items}','999'::jsonb)
                       WHERE tenant_id=%s AND report_id=%s""",
                    (self.tenant, report["report_id"]),
                )
            status, tampered, _ = self.request("reports/" + report["report_id"])
            self.assertEqual(status, 503)
            self.assertIsNone(tampered["data"])

    def test_report_retains_pinned_run_limitations_without_inferred_queries(self):
        row, reader = self.configure_projection_fixture()
        limited = replace(
            row,
            run_id="run:limited",
            native_id="run:limited",
            reference=replace(
                row.reference, document_id="whale:fixture:run:limited",
                observation_id="run:limited"),
            cache_hit=True,
            cache_age_seconds=90,
            cache_original_observed_at=(
                NOW - timedelta(seconds=90)).isoformat(),
            truncated=True,
            complete=False,
            status="partial",
            limitations=ObservationLimitations({
                "anonymous": {"status": "source_applied"},
                "low_volume": {"status": "below_threshold"},
                "rounded": {"status": "source_applied"},
                "top_n": {"status": "source_applied"},
                "sampled": {"status": "source_applied"},
                "modeled": {"status": "not_observed"},
                "cache": {"status": "source_applied"},
                "truncated": {"status": "source_applied"},
            }),
        )
        reader.rows[limited.reference.document_id] = limited
        p = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.service.projection.rebuild(
            p, limited.reference, now=self.clock)
        export = self.exports.create(
            p, {
                "source_path": "/observations/{id}/items",
                "filters": {"id": limited.run_id},
                "fields": ["item_id", "result_type"],
                "format": "jsonl",
            },
            idempotency_key="limited-manifest",
            now=self.clock,
        )
        payload = {
            "manifest_id": export["manifest_id"],
            "report_type": "deterministic",
        }
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "reports", payload=payload,
                headers={"Idempotency-Key": "limited-report"})
            self.assertEqual(status, 201, out)
            reported = out["data"]["observation_limitations"][limited.run_id]
            self.assertTrue(reported["cache_hit"])
            self.assertTrue(reported["truncated"])
            self.assertEqual(
                reported["limitations"]["anonymous"]["status"],
                "source_applied",
            )
            self.assertEqual(
                reported["limitations"]["low_volume"]["status"],
                "below_threshold",
            )
            self.assertNotIn("reconstructed_query", out["data"])
            status, detail, _ = self.request(
                "reports/" + out["data"]["report_id"])
            self.assertEqual(status, 200, detail)
            self.assertEqual(
                detail["data"]["observation_limitations"],
                out["data"]["observation_limitations"],
            )

    def test_report_fails_closed_on_source_failure_revocation_and_expiry(self):
        row, reader, export = self.job_manifest()
        payload = {"manifest_id": export["manifest_id"],
                   "report_type": "deterministic"}
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "reports", payload=payload,
                headers={"Idempotency-Key": "report-close"})
        self.assertEqual(status, 201, out)
        report_id = out["data"]["report_id"]
        saved = dict(reader.rows)
        reader.rows.clear()
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, failed, _ = self.request("reports/" + report_id)
        self.assertEqual(status, 503)
        self.assertEqual(failed["errors"][0]["code"], "source_error")
        reader.rows.update(saved)

        current = self.registry.grant(self.tenant, "grant:fixture")
        self.registry.save_grant(
            replace(current, revoked=True, version="g3"),
            expected_revision=2, actor_ref="fixture:operator", now=self.clock)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, denied, _ = self.request("reports/" + report_id)
        self.assertEqual(status, 403)
        self.assertIsNone(denied["data"])

        self.clock = NOW + timedelta(hours=2)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, expired, _ = self.request("reports/" + report_id)
        self.assertEqual(status, 410)
        self.assertIsNone(expired["data"])

    def test_evidence_id_resolves_one_licensed_pinned_observation(self):
        row, reader = self.configure_projection_fixture()
        evidence_id = row.reference.evidence_id
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "evidence/" + quote(evidence_id, safe=""))
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["evidence_id"], evidence_id)
        self.assertEqual(out["data"]["versions"]["parser_version"], "p1")
        self.assertIn("GO-MET-001",
                      {metric["id"] for metric in out["data"]["metric_definitions"]})
        self.assertEqual(out["meta"]["quality"], "licensed_pinned_evidence")
        self.assertEqual(out["meta"]["coverage"]["scope"], "one_observation")
        reader.rows.clear()
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "evidence/" + quote(evidence_id, safe=""))
        self.assertEqual(status, 503)
        self.assertIsNone(out["data"])

    def test_search_console_report_preserves_source_state_privacy_and_cursor_scope(self):
        self.configure_search_console_fixture()
        query = urlencode({
            "source": "gsc",
            "source_version": "1",
            "asset": "sc-domain:example.test",
            "template": "gsc-pages:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "data_state": "all",
            "aggregation": "byPage",
            "dimensions": "date,query,page",
            "limit": "1",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, first, _ = self.request(
                "search-console/reports?" + query)
            self.assertEqual(status, 200, first)
            data = first["data"]
            self.assertEqual(data["data_state"], "all")
            self.assertEqual(data["aggregation"], "byPage")
            self.assertEqual(
                data["period"]["source_timezone"],
                "America/Los_Angeles",
            )
            self.assertEqual(
                data["statistical_completeness"], "top_rows_only")
            self.assertEqual(
                data["privacy"]["anonymous_query_rows"],
                "not_provided_by_source",
            )
            self.assertEqual(data["coverage"]["status"], "top_rows_page_only")
            self.assertEqual(data["rows"][0]["row_id"], "row-a")
            self.assertEqual(
                data["rows"][0]["metrics"]["position"]["availability"],
                "available",
            )
            self.assertEqual(
                data["rows"][0]["metrics"]["clicks"]["provenance_kind"],
                "authorized_reported",
            )
            self.assertEqual(first["meta"]["quality"],
                             "approved_search_console_source")
            self.assertEqual(first["meta"]["input_manifest_id"],
                             data["evidence_id"])
            cursor = data["next_cursor"]
            self.assertTrue(cursor)
            status, second, _ = self.request(
                "search-console/reports?" + urlencode({
                    **{key: value for key, value in {
                        "source": "gsc",
                        "source_version": "1",
                        "asset": "sc-domain:example.test",
                        "template": "gsc-pages:1",
                        "period_start": "2026-11-01",
                        "period_end": "2026-11-04",
                        "data_state": "all",
                        "aggregation": "byPage",
                        "dimensions": "date,query,page",
                        "limit": "1",
                    }.items()},
                    "cursor": cursor,
                }))
            self.assertEqual(status, 200, second)
            self.assertEqual(second["data"]["rows"][0]["row_id"], "row-b")
            self.assertIsNone(second["data"]["next_cursor"])
            status, rebound, _ = self.request(
                "search-console/reports?" + urlencode({
                    "source": "gsc",
                    "source_version": "1",
                    "asset": "sc-domain:example.test",
                    "template": "gsc-pages:1",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-04",
                    "data_state": "all",
                    "aggregation": "byPage",
                    "dimensions": "date,query,page",
                    "limit": "2",
                    "cursor": cursor,
                }))
            self.assertEqual(status, 400)
            self.assertEqual(rebound["errors"][0]["code"], "invalid_argument")

    def test_search_console_rejects_evidence_scope_expiry_and_revocation(self):
        self.configure_search_console_fixture()
        query = urlencode({
            "source": "gsc",
            "source_version": "1",
            "asset": "sc-domain:example.test",
            "template": "gsc-pages:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "data_state": "final",
            "aggregation": "byProperty",
            "dimensions": "date,query,page",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            self.search_console_adapter.evidence_dataset = "fixture:wrong"
            status, mismatch, _ = self.request(
                "search-console/reports?" + query)
            self.assertEqual(status, 409)
            self.assertEqual(mismatch["errors"][0]["code"],
                             "incompatible_scope")
            self.search_console_adapter.evidence_dataset = (
                self.search_console_adapter.dataset_id)
            self.search_console_adapter.source_observed_at = NOW.isoformat()
            self.search_console_adapter.evidence_expires_at = (
                NOW + timedelta(hours=1)).isoformat()
            self.clock = NOW + timedelta(minutes=90)
            status, expired, _ = self.request(
                "search-console/reports?" + query)
            self.assertEqual(status, 410, expired)
            self.assertEqual(expired["errors"][0]["code"], "expired")

            self.clock = NOW
            current = self.registry.grant(
                self.tenant, "grant:gsc")
            self.registry.save_grant(
                replace(current, revoked=True, version="2"),
                expected_revision=1,
                actor_ref="fixture:operator",
                now=self.clock,
            )
            status, revoked, _ = self.request(
                "search-console/reports?" + query)
            self.assertEqual(status, 403)
            self.assertIsNone(revoked["data"])

    def test_search_console_diagnostics_keep_unknowns_and_quota_explicit(self):
        self.configure_search_console_fixture()
        query = urlencode({
            "source": "gsc",
            "source_version": "1",
            "asset": "sc-domain:example.test",
            "url": "https://example.test/a",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "search-console/diagnostics?" + query)
        self.assertEqual(status, 200, out)
        inspection = out["data"]["inspection"]
        self.assertIsNone(inspection["robots_state"])
        self.assertEqual(inspection["quota"]["state"], "source_reported")
        self.assertEqual(inspection["quota"]["remaining"], 1999)
        self.assertEqual(out["data"]["coverage"]["status"], "source_reported")
        self.assertIn(
            "unknown_fields_are_not_false",
            out["data"]["not_claimed"],
        )

    def test_search_console_diagnostics_rejects_url_outside_property(self):
        self.configure_search_console_fixture()
        query = urlencode({
            "source": "gsc",
            "source_version": "1",
            "asset": "sc-domain:example.test",
            "url": "https://outside.test/a",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service), patch.object(
                    self.search_console_adapter,
                    "inspect_url",
                ) as inspect:
            status, out, _ = self.request(
                "search-console/diagnostics?" + query)
        self.assertEqual(status, 403, out)
        self.assertEqual(out["errors"][0]["code"], "use_not_permitted")
        inspect.assert_not_called()

    def test_ga4_report_preserves_metadata_quota_and_cursor_scope(self):
        self.configure_ga4_fixture()
        query = urlencode({
            "source": "ga4",
            "source_version": "1",
            "asset": "properties/123",
            "template": "ga4-core:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "dimensions": "date",
            "metrics": "sessions",
            "limit": "1",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, first, _ = self.request(
                "analytics/reports?" + query)
            self.assertEqual(status, 200, first)
            data = first["data"]
            self.assertEqual(
                data["period"]["report_timezone"],
                "America/Los_Angeles",
            )
            self.assertEqual(
                data["metadata"]["sampling_metadata"][0]["sample_size"],
                90,
            )
            self.assertEqual(
                data["metadata"]["property_quota"]["tokensPerDay"]["remaining"],
                199999,
            )
            self.assertEqual(
                data["coverage"]["statistical_completeness"],
                "not_claimed",
            )
            self.assertEqual(first["meta"]["quality"],
                             "approved_ga4_analytics_source")
            self.assertEqual(first["meta"]["input_manifest_id"],
                             data["evidence_id"])
            cursor = data["next_cursor"]
            self.assertTrue(cursor)
            status, second, _ = self.request(
                "analytics/reports?" + urlencode({
                    "source": "ga4",
                    "source_version": "1",
                    "asset": "properties/123",
                    "template": "ga4-core:1",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-04",
                    "dimensions": "date",
                    "metrics": "sessions",
                    "limit": "1",
                    "cursor": cursor,
                }))
            self.assertEqual(status, 200, second)
            self.assertEqual(second["data"]["rows"][0]["row_id"], "row-2")
            self.assertIsNone(second["data"]["next_cursor"])
            self.ga4_adapter.report_value = 11
            status, changed_snapshot, _ = self.request(
                "analytics/reports?" + urlencode({
                    "source": "ga4",
                    "source_version": "1",
                    "asset": "properties/123",
                    "template": "ga4-core:1",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-04",
                    "dimensions": "date",
                    "metrics": "sessions",
                    "limit": "1",
                    "cursor": cursor,
                }))
            self.assertEqual(status, 400)
            self.assertEqual(
                changed_snapshot["errors"][0]["code"], "invalid_argument")
            self.ga4_adapter.report_value = 10
            status, rebound, _ = self.request(
                "analytics/reports?" + urlencode({
                    "source": "ga4",
                    "source_version": "1",
                    "asset": "properties/123",
                    "template": "ga4-core:1",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-04",
                    "dimensions": "date",
                    "metrics": "sessions",
                    "limit": "2",
                    "cursor": cursor,
                }))
            self.assertEqual(status, 400)
            self.assertEqual(rebound["errors"][0]["code"], "invalid_argument")

    def test_ga4_reconciliation_preserves_nonzero_differences(self):
        self.configure_ga4_fixture()
        query = urlencode({
            "source": "ga4",
            "source_version": "1",
            "asset": "properties/123",
            "template": "ga4-core:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "dimensions": "date",
            "metrics": "sessions",
            "api_scope": json.dumps({
                "identity": "property",
                "attribution": "session",
                "timezone": "America/Los_Angeles",
            }),
            "bigquery_scope": json.dumps({
                "identity": "property",
                "attribution": "event",
                "timezone": "America/Los_Angeles",
            }),
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "analytics/reconciliations?" + query)
        self.assertEqual(status, 200, out)
        self.assertTrue(out["data"]["compatible"])
        comparison = out["data"]["comparisons"][0]
        self.assertEqual(comparison["api_value"], "10")
        self.assertEqual(comparison["bigquery_value"], "9")
        self.assertEqual(comparison["delta"], "1")
        self.assertEqual(comparison["status"], "difference_explained")
        self.assertFalse(
            out["data"]["coverage"]["non_zero_difference_required"])

    def test_ga4_rejects_evidence_scope_expiry_and_revocation(self):
        self.configure_ga4_fixture()
        query = urlencode({
            "source": "ga4",
            "source_version": "1",
            "asset": "properties/123",
            "template": "ga4-core:1",
            "period_start": "2026-11-01",
            "period_end": "2026-11-04",
            "dimensions": "date",
            "metrics": "sessions",
        })
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            self.ga4_adapter.evidence_dataset = "fixture:wrong"
            status, mismatch, _ = self.request(
                "analytics/reports?" + query)
            self.assertEqual(status, 409)
            self.assertEqual(
                mismatch["errors"][0]["code"], "incompatible_scope")
            self.ga4_adapter.evidence_dataset = self.ga4_adapter.dataset_id
            self.clock = NOW + timedelta(minutes=90)
            status, expired, _ = self.request(
                "analytics/reports?" + query)
            self.assertEqual(status, 410)
            self.assertEqual(expired["errors"][0]["code"], "expired")
            self.clock = NOW
            current = self.registry.grant(self.tenant, "grant:ga4")
            self.registry.save_grant(
                replace(current, revoked=True, version="2"),
                expected_revision=1,
                actor_ref="fixture:operator",
                now=self.clock,
            )
            status, revoked, _ = self.request(
                "analytics/reports?" + query)
            self.assertEqual(status, 403)
            self.assertEqual(revoked["errors"][0]["code"], "not_authorized")

    def test_manager_usage_is_local_and_never_invents_provider_cost(self):
        self.configure_projection_fixture()
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("usage")
        self.assertEqual(status, 403)
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={"grant:fixture"},
            roles={"reader", "manager"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("usage")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["scope"], "local_control_plane_usage")
        self.assertEqual(out["data"]["provider_usage"]["status"], "not_covered")
        self.assertEqual(out["data"]["fees"]["status"], "not_covered")
        self.assertEqual(out["data"]["api_limits"]["reads_per_second"], 5)
        self.assertIn("analysis_jobs", out["data"]["local"])
        self.assertIn("exports", out["data"]["local"])

    def test_quality_uses_verified_window_and_never_invents_cost_or_slo(self):
        self.configure_projection_fixture()
        self.service.quality = GoogleQualityLedger(
            self.registry,
            self.service.projection,
            clock=lambda: self.clock,
        )
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={"grant:fixture"},
            roles={"reader", "manager"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        window = (
            "observed_from=" + quote(NOW.isoformat(), safe="") +
            "&observed_to=" +
            quote((NOW + timedelta(hours=1)).isoformat(), safe="")
        )
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "quality?source=fixture&source_version=1&" + window)
        self.assertEqual(status, 200, out)
        self.assertEqual(out["meta"]["quality"], "operational_quality_ledger")
        self.assertEqual(out["data"]["quality_version"], "google-quality-1")
        self.assertEqual(out["data"]["observation_quality"]["runs"]["total"], 1)
        self.assertEqual(out["data"]["provider_usage"]["status"], "not_covered")
        self.assertEqual(out["data"]["fees"]["status"], "not_covered")
        self.assertEqual(out["data"]["source_quota"]["status"], "not_covered")
        self.assertFalse(
            out["data"]["observation_quality"]["latency_seconds"]["slo_claimed"])

    def test_live_queries_are_queued_until_an_external_worker_runs(self):
        source = replace(
            self.source,
            coverage={
                **dict(self.source.coverage),
                "live_queries": True,
            },
            field_rights=tuple(
                FieldRight(
                    field,
                    frozenset({"internal_display"}),
                    NOW + timedelta(hours=2),
                )
                for field in ("title", "live_query")
            ),
        )
        self.registry.save_source(
            self.tenant, source, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        grant = replace(
            self.grant,
            version="2",
            fields=frozenset({"title", "live_query"}),
            uses=frozenset({"internal_display"}),
        )
        self.registry.save_grant(
            grant, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={"grant:fixture"},
            roles={"reader"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        live = LiveQueryService(
            self.dsn,
            registry=self.registry,
            executor=LiveQueryExecutorFixture(),
            cursor_codec=self.service.limits.cursor_codec(),
            clock=lambda: self.clock,
        )
        live.initialize()
        self.service.live_queries = live
        body = {
            "source": "fixture",
            "source_version": "1",
            "query": "live fixture",
            "variant": {
                "country": "US",
                "language": "en",
                "location": {
                    "precision": "country",
                    "country": "US",
                },
                "device": "desktop",
                "search_engine": "google",
                "surface": "web",
                "channel": "consumer",
                "login_state": "logged_out",
                "safe_search": "off",
                "depth": 10,
                "parameters_version": "live-1",
                "parameters": {},
            },
            "max_units": 10,
        }
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, headers = self.request(
                "live-queries",
                payload=body,
                headers={"Idempotency-Key": "live-key-one"},
                method="POST",
            )
        self.assertEqual(status, 202, out)
        self.assertEqual(out["data"]["status"], "queued")
        self.assertIsNone(out["data"]["result"])
        self.assertIn("/v1/google/live-queries/", headers["Location"])
        live_id = out["data"]["live_query_id"]

        done = live.run_once(
            tenant_id=self.tenant,
            worker_ref="fixture-worker",
            now=self.clock,
        )
        self.assertEqual(done["status"], "succeeded", done)
        self.assertEqual(done["result"]["units_used"], 3)

        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request("live-queries/" + live_id)
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["status"], "succeeded")
        self.assertEqual(
            out["data"]["result"]["items"][0]["target_url"],
            "https://live.example/result",
        )
        self.assertEqual(
            out["data"]["provider_cost"]["status"], "not_covered")
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, second, _ = self.request(
                "live-queries",
                payload=body,
                headers={"Idempotency-Key": "live-key-two"},
                method="POST",
            )
            self.assertEqual(status, 202, second)
            status, first_page, _ = self.request("live-queries?limit=1")
            self.assertEqual(status, 200, first_page)
            self.assertEqual(
                first_page["data"]["live_queries"][0]["live_query_id"],
                second["data"]["live_query_id"],
            )
            cursor = first_page["data"]["next_cursor"]
            self.assertTrue(cursor)
            status, second_page, _ = self.request(
                "live-queries?limit=1&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 200, second_page)
            self.assertEqual(
                second_page["data"]["live_queries"][0]["live_query_id"],
                live_id,
            )
            self.assertIsNone(second_page["data"]["next_cursor"])

    def test_subscriptions_are_key_scoped_paused_and_do_not_send(self):
        source = replace(
            self.source,
            field_rights=tuple(
                FieldRight(
                    field,
                    frozenset({"internal_display"}),
                    NOW + timedelta(hours=2),
                )
                for field in ("title", "change_subscription")
            ),
        )
        self.registry.save_source(
            self.tenant, source, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        grant = replace(
            self.grant,
            version="2",
            fields=frozenset({"title", "change_subscription"}),
            uses=frozenset({"internal_display"}),
        )
        self.registry.save_grant(
            grant, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            self.tenant, self.subject, grant_ids={"grant:fixture"},
            roles={"reader"}, expected_revision=1,
            actor_ref="fixture:operator", now=self.clock)
        body = {
            "name": "Fixture changes",
            "target_ref": "approved-target:fixture",
            "event_types": ["rank_change", "news_event"],
            "filters": {
                "source": "fixture",
                "source_version": "1",
                "target_domains": ["example.com"],
            },
        }
        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, headers = self.request(
                "subscriptions",
                payload=body,
                headers={"Idempotency-Key": "subscription-key-one"},
                method="POST",
            )
        self.assertEqual(status, 201, out)
        self.assertEqual(out["data"]["state"], "configured_paused")
        self.assertFalse(out["data"]["active"])
        self.assertFalse(
            out["data"]["delivery"]["outbound_send_enabled"])
        self.assertEqual(
            out["data"]["delivery"]["status"], "not_connected")
        subscription_id = out["data"]["subscription_id"]

        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "subscriptions/" + subscription_id)
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["event_types"], ["rank_change", "news_event"])

        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, second, _ = self.request(
                "subscriptions",
                payload={**body, "name": "Second fixture changes"},
                headers={"Idempotency-Key": "subscription-key-two"},
                method="POST",
            )
            self.assertEqual(status, 201, second)
            status, first_page, _ = self.request("subscriptions?limit=1")
            self.assertEqual(status, 200, first_page)
            self.assertEqual(
                first_page["data"]["subscriptions"][0]["subscription_id"],
                second["data"]["subscription_id"],
            )
            cursor = first_page["data"]["next_cursor"]
            self.assertTrue(cursor)
            status, second_page, _ = self.request(
                "subscriptions?limit=1&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 200, second_page)
            self.assertEqual(
                second_page["data"]["subscriptions"][0]["subscription_id"],
                subscription_id,
            )
            self.assertIsNone(second_page["data"]["next_cursor"])

        with patch(
                "realtime.google_http.GoogleDomainService",
                return_value=self.service):
            status, out, _ = self.request(
                "subscriptions/" + subscription_id,
                payload={},
                method="DELETE",
            )
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["state"], "deleted")
        self.assertFalse(out["data"]["active"])

    def test_domain_rankings_comparisons_and_pages_use_one_fixed_verified_cohort(self):
        row, reader = self.configure_projection_fixture()
        from tests import test_google_projection as fixtures
        second = fixtures.observation(
            rid="run:two",
            when=NOW + timedelta(seconds=2),
            text="Python",
            items=(
                SERPItem(
                    "organic:other", "organic",
                    target_url="https://other.test/article",
                    title="Other", snippet="Fixture item",
                    organic_position=5, provider_rank_absolute=1,
                ),
                SERPItem(
                    "paid:other", "paid",
                    target_url="https://other.test/article",
                    provider_rank_absolute=2,
                ),
            ),
        )
        second = replace(
            second,
            reference=replace(
                second.reference,
                asset_id=self.grant.asset_id,
                grant_id=self.grant.grant_id,
            ),
            features=(SERPFeature("aio:other", "ai_overview", None),),
        )
        reader.rows[second.reference.document_id] = second
        self.clock = second.reference.observed_at
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.service.projection.rebuild(principal, second.reference, now=self.clock)
        window = (
            "observed_from=" + quote(NOW.isoformat(), safe="") +
            "&observed_to=" +
            quote((NOW + timedelta(hours=1)).isoformat(), safe="")
        )

        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, domains, _ = self.request("domains?" + window)
            self.assertEqual(status, 200, domains)
            self.assertEqual(domains["meta"]["quality"],
                             "deterministic_domain_aggregate")
            self.assertEqual(domains["meta"]["coverage"]["status"],
                             "fixed_observed_cohort")
            by_host = {entry["domain"]: entry for entry in domains["data"]["domains"]}
            self.assertEqual(set(by_host), {"example.com", "other.test"})
            self.assertEqual(by_host["example.com"]["top10_share"]["numerator"], 1)
            self.assertEqual(by_host["example.com"]["top10_share"]["denominator"], 2)
            self.assertEqual(by_host["other.test"]["top10_share"]["numerator"], 1)
            self.assertIsNone(by_host["example.com"]["top10_share"]["interval"])
            self.assertEqual(
                by_host["example.com"]["top10_share"]["interval_reason"],
                "clustered_query_runs_are_not_independent_samples",
            )

            status, rankings, _ = self.request(
                "rankings?domains=other.test,missing.test&metric=top10_share&" +
                window)
            self.assertEqual(status, 200, rankings)
            self.assertEqual(
                [entry["domain"] for entry in rankings["data"]["rankings"]],
                ["other.test", "missing.test"],
            )
            self.assertEqual(
                rankings["data"]["rankings"][1]["top10_share"]["numerator"], 0)

            status, comparison, _ = self.request(
                "comparisons?domains=example.com,other.test&" + window)
            self.assertEqual(status, 200, comparison)
            self.assertEqual(len(comparison["data"]["domains"]), 2)
            self.assertEqual(
                comparison["data"]["pairwise_differences"][0]["left_minus_right"],
                0.0,
            )
            self.assertFalse(comparison["data"]["causal_claim"])
            trace = comparison["data"]["domains"][0]["recent_evidence"]
            self.assertTrue(trace)
            self.assertTrue(trace[0]["evidence_id"].startswith(
                "google:evidence:"))
            self.assertEqual(
                comparison["data"]["domains"][0]["recent_evidence_total"],
                1,
            )

            status, opportunities, _ = self.request(
                "insights/opportunities?target=missing.test&" + window)
            self.assertEqual(status, 200, opportunities)
            self.assertTrue(opportunities["data"]["opportunities"])
            self.assertEqual(
                opportunities["data"]["opportunities"][0]["opportunity_score"],
                1.0,
            )
            self.assertEqual(
                opportunities["data"]["cost_status"], "not_covered")

            split_at = quote((NOW + timedelta(seconds=1)).isoformat(), safe="")
            status, anomaly, _ = self.request(
                "insights/anomalies?target=other.test&split_at=" +
                split_at + "&threshold=0.5&" + window)
            self.assertEqual(status, 200, anomaly)
            self.assertEqual(anomaly["data"]["anomaly"]["delta"], 1.0)
            self.assertTrue(anomaly["data"]["anomaly"]["detected"])
            self.assertEqual(
                anomaly["data"]["anomaly"]["business_cause"]["status"],
                "not_covered",
            )
            self.assertEqual(
                anomaly["data"]["anomaly"]["monitor_binding"],
                "explicit_target_not_saved_monitor",
            )

            status, changes, _ = self.request(
                "changes?target=other.test&split_at=" +
                split_at + "&threshold=0.5&" + window)
            self.assertEqual(status, 200, changes)
            self.assertEqual(len(changes["data"]["changes"]), 1)
            self.assertEqual(
                changes["data"]["changes"][0]["event_type"],
                "top10_share_change_v1",
            )
            self.assertEqual(changes["data"]["changes"][0]["delta"], 1.0)
            self.assertTrue(changes["data"]["changes"][0]["detected"])
            self.assertEqual(
                changes["data"]["changes"][0]["raw_content_change"],
                "not_covered_use_whale_changes",
            )

            status, brands, _ = self.request(
                "insights/brands?brands=example.com,other.test&" + window)
            self.assertEqual(status, 200, brands)
            self.assertEqual(brands["meta"]["quality"],
                             "separated_brand_observation_tracks")
            self.assertEqual(
                [row["brand_domain"] for row in brands["data"]["brands"]],
                ["example.com", "other.test"],
            )
            self.assertTrue(all(
                row["tracks"]["demand"]["status"] == "not_covered"
                for row in brands["data"]["brands"]))
            self.assertFalse(brands["data"]["combined_index_allowed"])

            status, article, _ = self.request(
                "articles/" + quote("organic:1", safe="") + "?" + window)
            self.assertEqual(status, 200, article)
            self.assertEqual(article["meta"]["quality"],
                             "licensed_article_summary_only")
            self.assertIsNone(article["data"]["article"]["body"])
            self.assertEqual(
                article["data"]["article"]["body_status"],
                "not_covered_by_typed_projection",
            )
            self.assertTrue(article["data"]["body_text_not_inferred"])

            example_id = by_host["example.com"]["domain_id"]
            status, pages, _ = self.request(
                "domains/" + quote(example_id, safe="") + "/pages?" + window)
            self.assertEqual(status, 200, pages)
            self.assertEqual(len(pages["data"]["pages"]), 1)
            self.assertEqual(
                pages["data"]["pages"][0]["target_url"],
                "https://example.com/page",
            )
            self.assertTrue(
                pages["data"]["pages"][0]["recent_evidence"][0][
                    "evidence_id"
                ].startswith("google:evidence:"))

            status, invalid, _ = self.request("domains")
            self.assertEqual(status, 400)
            self.assertEqual(invalid["errors"][0]["code"], "invalid_argument")

    def test_news_occurrences_and_event_candidates_do_not_invent_time_or_truth(self):
        row, reader = self.configure_projection_fixture()
        from tests import test_google_projection as fixtures
        news = fixtures.observation(
            rid="run:news",
            when=NOW + timedelta(seconds=3),
            items=(
                SERPItem(
                    "news:one", "top_stories",
                    target_url="https://publisher.test/story",
                    title="Same explicit story",
                    snippet="First feed occurrence",
                    provider_rank_absolute=1,
                ),
                SERPItem(
                    "news:two", "top_stories",
                    target_url="https://publisher.test/story",
                    title="Same explicit story",
                    snippet="Repeated feed occurrence",
                    provider_rank_absolute=2,
                ),
            ),
        )
        news = replace(
            news,
            reference=replace(
                news.reference,
                asset_id=self.grant.asset_id,
                grant_id=self.grant.grant_id,
            ),
        )
        reader.rows[news.reference.document_id] = news
        self.clock = news.reference.observed_at
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.service.projection.rebuild(principal, news.reference, now=self.clock)
        window = (
            "observed_from=" + quote((NOW + timedelta(seconds=2)).isoformat(), safe="") +
            "&observed_to=" +
            quote((NOW + timedelta(hours=1)).isoformat(), safe="")
        )

        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request(
                "news/observations?query=" + quote("C++", safe="") + "&" + window)
            self.assertEqual(status, 200, out)
            self.assertEqual(out["meta"]["quality"],
                             "deterministic_news_occurrences")
            self.assertEqual(len(out["data"]["news_observations"]), 2)
            self.assertTrue(all(
                row["source_published_at"] is None
                for row in out["data"]["news_observations"]))
            self.assertTrue(all(
                not row["publication_time_is_inferred"]
                for row in out["data"]["news_observations"]))

            status, events, _ = self.request(
                "news/events?topic=" + quote("C++", safe="") + "&" + window)
            self.assertEqual(status, 200, events)
            self.assertEqual(events["meta"]["quality"],
                             "deterministic_news_event_candidates")
            self.assertEqual(len(events["data"]["news_events"]), 1)
            event = events["data"]["news_events"][0]
            self.assertEqual(event["occurrence_count"], 2)
            self.assertEqual(event["independent_article_count"], 1)
            self.assertEqual(event["repost_count"], 1)
            self.assertEqual(event["source_published_from"], None)
            self.assertTrue(event["heuristic_not_gold"])
            self.assertTrue(events["data"]["event_truth_not_claimed"])
            self.assertEqual(
                events["data"]["topic_filter"]["basis"],
                "exact_normalized_observed_query_not_topic_taxonomy",
            )

    def test_query_topics_and_relations_are_evidence_backed_not_a_fake_taxonomy(self):
        row, reader = self.configure_projection_fixture()
        from tests import test_google_projection as fixtures
        second = fixtures.observation(
            rid="run:python",
            when=NOW + timedelta(seconds=2),
            text="Python",
        )
        second = replace(
            second,
            reference=replace(
                second.reference,
                asset_id=self.grant.asset_id,
                grant_id=self.grant.grant_id,
            ),
        )
        reader.rows[second.reference.document_id] = second
        self.clock = second.reference.observed_at
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.service.projection.rebuild(principal, second.reference, now=self.clock)
        window = (
            "observed_from=" + quote(NOW.isoformat(), safe="") +
            "&observed_to=" +
            quote((NOW + timedelta(hours=1)).isoformat(), safe="")
        )
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, _ = self.request("topics?" + window)
            self.assertEqual(status, 200, out)
            self.assertEqual(out["meta"]["quality"],
                             "observed_query_topic_candidates")
            self.assertFalse(out["data"]["approved_topic_taxonomy"])
            self.assertEqual(len(out["data"]["topics"]), 2)
            source = next(
                topic for topic in out["data"]["topics"]
                if topic["aliases"] == ["C++"])
            self.assertEqual(
                source["topic_basis"], "exact_normalized_observed_query")
            self.assertEqual(
                source["brand_relations"]["status"], "not_covered")

            status, relations, _ = self.request(
                "topics/" + quote(source["topic_id"], safe="") +
                "/relations?" + window)
            self.assertEqual(status, 200, relations)
            self.assertEqual(relations["meta"]["quality"],
                             "evidence_backed_query_topic_relations")
            self.assertEqual(len(relations["data"]["relations"]), 1)
            relation = relations["data"]["relations"][0]
            self.assertEqual(
                relation["related_aliases"], ["Python"])
            self.assertEqual(relation["confidence"], 1.0)
            self.assertFalse(relation["causal_claim"])
            self.assertTrue(relation["evidence_ids"])

    def test_ai_answers_keep_text_uncovered_and_citations_explicit(self):
        row, reader = self.configure_projection_fixture()
        from tests import test_google_projection as fixtures
        ai = fixtures.observation(
            rid="run:ai",
            when=NOW + timedelta(seconds=4),
            text="AI answer",
        )
        ai = replace(
            ai,
            reference=replace(
                ai.reference,
                asset_id=self.grant.asset_id,
                grant_id=self.grant.grant_id,
                fields=tuple(sorted(fixtures.FIELDS | {"citation_url"})),
            ),
            features=(SERPFeature(
                "aio:cited",
                "ai_overview",
                True,
                target_urls=("https://brand.test/page",),
                citation_urls=("https://brand.test/source",),
            ),),
        )
        reader.rows[ai.reference.document_id] = ai
        self.clock = ai.reference.observed_at
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        self.service.projection.rebuild(principal, ai.reference, now=self.clock)
        window = (
            "observed_from=" + quote(NOW.isoformat(), safe="") +
            "&observed_to=" +
            quote((NOW + timedelta(hours=1)).isoformat(), safe="")
        )
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, answers, _ = self.request(
                "ai/answers?channel=aio&" + window)
            self.assertEqual(status, 200, answers)
            self.assertEqual(answers["meta"]["quality"],
                             "licensed_ai_feature_observations")
            self.assertEqual(len(answers["data"]["answers"]), 2)
            self.assertTrue(all(
                answer["answer_text"] is None
                and answer["answer_text_status"] ==
                "not_covered_by_typed_projection"
                for answer in answers["data"]["answers"]))

            status, citations, _ = self.request(
                "ai/citations?channel=aio&" + window)
            self.assertEqual(status, 200, citations)
            self.assertEqual(len(citations["data"]["citations"]), 1)
            self.assertEqual(
                citations["data"]["citations"][0]["citation_url"],
                "https://brand.test/source",
            )

            status, visibility, _ = self.request(
                "ai/visibility?brands=brand.test&channel=aio&" + window)
            self.assertEqual(status, 200, visibility)
            cited = next(
                row for row in visibility["data"]["visibility"]
                if row["prompt"] == "AI answer")
            self.assertEqual(cited["presence"]["numerator"], 1)
            self.assertEqual(cited["presence"]["denominator"], 1)
            self.assertEqual(cited["brands"][0]["citation_count"], 1)
            self.assertEqual(
                cited["brands"][0]["citation_share_of_voice"]["value"], 1.0)
            self.assertEqual(
                cited["brands"][0]["mention_status"], "not_covered")

    def test_watchlists_are_key_scoped_idempotent_and_remove_only_membership(self):
        self.configure_projection_fixture()
        payload = {
            "name": "Research queue",
            "items": [
                {"entity_id": "query:one", "entity_type": "query",
                 "label": "Fixed query"},
                {"entity_id": "run:one", "entity_type": "run",
                 "metadata": {"note": "keep source facts"}},
            ],
        }
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, headers = self.request(
                "watchlists", payload=payload,
                headers={"Idempotency-Key": "watch-one"})
            self.assertEqual(status, 201, out)
            watchlist = out["data"]
            self.assertEqual(watchlist["item_count"], 2)
            self.assertFalse(watchlist["source_fact_deletion"])
            self.assertEqual(watchlist["etag"], '"1"')
            self.assertIn("Location", headers)
            status, replay, _ = self.request(
                "watchlists", payload=payload,
                headers={"Idempotency-Key": "watch-one"})
            self.assertEqual(status, 201)
            self.assertEqual(replay["data"]["watchlist_id"],
                             watchlist["watchlist_id"])
            status, conflict, _ = self.request(
                "watchlists", payload={**payload, "name": "Another"},
                headers={"Idempotency-Key": "watch-one"})
            self.assertEqual(status, 409)
            self.assertEqual(conflict["errors"][0]["code"], "idempotency_conflict")
            status, listed, _ = self.request("watchlists")
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["data"]["watchlists"]), 1)
            status, other, _ = self.request("watchlists", key=self.other["api_key"])
            self.assertEqual(status, 200)
            self.assertEqual(other["data"]["watchlists"], [])
            status, second_watchlist, _ = self.request(
                "watchlists",
                payload={**payload, "name": "Second research queue"},
                headers={"Idempotency-Key": "watch-two"},
            )
            self.assertEqual(status, 201, second_watchlist)
            status, first_page, _ = self.request("watchlists?limit=1")
            self.assertEqual(status, 200, first_page)
            self.assertEqual(
                first_page["data"]["watchlists"][0]["watchlist_id"],
                second_watchlist["data"]["watchlist_id"],
            )
            cursor = first_page["data"]["next_cursor"]
            self.assertTrue(cursor)
            status, second_page, _ = self.request(
                "watchlists?limit=1&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 200, second_page)
            self.assertEqual(
                second_page["data"]["watchlists"][0]["watchlist_id"],
                watchlist["watchlist_id"],
            )
            self.assertIsNone(second_page["data"]["next_cursor"])

            import psycopg
            with psycopg.connect(self.dsn) as db:
                before = db.execute(
                    """SELECT
                         (SELECT count(*) FROM sf_google_serp_runs WHERE tenant_id=%s),
                         (SELECT count(*) FROM sf_google_serp_items WHERE tenant_id=%s)""",
                    (self.tenant, self.tenant)).fetchone()
            status, removed, _ = self.request(
                "watchlists/%s/items/%s" % (
                    watchlist["watchlist_id"], quote("run:one", safe="")),
                method="DELETE")
            self.assertEqual(status, 200, removed)
            self.assertTrue(removed["data"]["removed"])
            self.assertFalse(removed["data"]["source_fact_deletion"])
            self.assertEqual(removed["data"]["watchlist"]["version"], 2)
            with psycopg.connect(self.dsn) as db:
                after = db.execute(
                    """SELECT
                         (SELECT count(*) FROM sf_google_serp_runs WHERE tenant_id=%s),
                         (SELECT count(*) FROM sf_google_serp_items WHERE tenant_id=%s)""",
                    (self.tenant, self.tenant)).fetchone()
            self.assertEqual(before, after)
            status, again, _ = self.request(
                "watchlists/%s/items/%s" % (
                    watchlist["watchlist_id"], quote("run:one", safe="")),
                method="DELETE")
            self.assertEqual(status, 200)
            self.assertFalse(again["data"]["removed"])
            self.assertEqual(again["data"]["watchlist"]["version"], 2)

    def test_monitor_configuration_is_versioned_paused_and_never_starts_a_scheduler(self):
        self.configure_projection_fixture()
        payload = {
            "name": "Brand watch",
            "queries": ["solar storage"],
            "prompts": ["best solar storage"],
            "domains": ["example.com"],
            "pools": [],
            "source_adapter": "fixture",
            "source_version": "1",
            "frequency_seconds": 3600,
            "budget": {"monthly_limit": "25.00", "currency": "USD"},
            "rules": {"rank_drop": 3},
        }
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, out, headers = self.request(
                "monitors", payload=payload,
                headers={"Idempotency-Key": "monitor-one"})
            self.assertEqual(status, 201, out)
            monitor = out["data"]
            self.assertFalse(monitor["enabled"])
            self.assertEqual(monitor["scheduler_state"],
                             "not_connected_no_scheduler")
            self.assertEqual(monitor["config"]["budget"],
                             {"monthly_limit": "25.00", "currency": "USD"})
            self.assertEqual(monitor["config"]["budget"]["monthly_limit"], "25.00")
            self.assertEqual(monitor["etag"], '"1"')
            self.assertIn("ETag", headers)
            status, replay, _ = self.request(
                "monitors", payload=payload,
                headers={"Idempotency-Key": "monitor-one"})
            self.assertEqual(status, 201)
            self.assertEqual(replay["data"]["monitor_id"], monitor["monitor_id"])
            status, listed, _ = self.request("monitors")
            self.assertEqual(status, 200)
            self.assertEqual(listed["data"]["execution_state"],
                             "paused_no_scheduler")
            self.assertEqual(len(listed["data"]["monitors"]), 1)
            status, second_monitor, _ = self.request(
                "monitors",
                payload={**payload, "name": "Second brand watch"},
                headers={"Idempotency-Key": "monitor-two"},
            )
            self.assertEqual(status, 201, second_monitor)
            status, first_page, _ = self.request("monitors?limit=1")
            self.assertEqual(status, 200, first_page)
            self.assertEqual(
                first_page["data"]["monitors"][0]["monitor_id"],
                second_monitor["data"]["monitor_id"],
            )
            cursor = first_page["data"]["next_cursor"]
            self.assertTrue(cursor)
            status, second_page, _ = self.request(
                "monitors?limit=1&cursor=" + quote(cursor, safe=""))
            self.assertEqual(status, 200, second_page)
            self.assertEqual(
                second_page["data"]["monitors"][0]["monitor_id"],
                monitor["monitor_id"],
            )
            self.assertIsNone(second_page["data"]["next_cursor"])
            status, mismatch, _ = self.request(
                "monitors/" + monitor["monitor_id"],
                method="PATCH", payload={"frequency_seconds": 7200},
                headers={"If-Match": '"9"'})
            self.assertEqual(status, 412)
            self.assertEqual(mismatch["errors"][0]["code"], "precondition_failed")
            status, updated, headers = self.request(
                "monitors/" + monitor["monitor_id"],
                method="PATCH", payload={"frequency_seconds": 7200},
                headers={"If-Match": monitor["etag"]})
            self.assertEqual(status, 200, updated)
            self.assertEqual(updated["data"]["version"], 2)
            self.assertEqual(updated["data"]["etag"], '"2"')
            self.assertEqual(updated["data"]["config"]["frequency_seconds"], 7200)
            self.assertFalse(updated["data"]["enabled"])
            self.assertIn("ETag", headers)
            status, closed, _ = self.request(
                "monitors/" + monitor["monitor_id"],
                method="PATCH", payload={"enabled": True},
                headers={"If-Match": updated["data"]["etag"]})
            self.assertEqual(status, 501)
            self.assertEqual(closed["errors"][0]["code"], "not_covered")

            import psycopg
            with psycopg.connect(self.dsn) as db:
                legacy_monitors = db.execute(
                    "SELECT count(*) FROM sf_monitors").fetchone()[0]
                legacy_campaigns = db.execute(
                    "SELECT count(*) FROM campaigns").fetchone()[0]
            self.assertEqual(legacy_monitors, 0)
            self.assertEqual(legacy_campaigns, 0)

    def test_markets_are_per_source_and_locations_are_not_invented_from_country(self):
        status, out, _ = self.request("markets?source=fixture&source_version=1")
        self.assertEqual(status, 200)
        self.assertEqual(out["data"]["countries"], ["US", "GB"])
        status, out, _ = self.request("locations?source=fixture&source_version=1")
        self.assertEqual(status, 501)
        self.assertEqual(out["errors"][0]["code"], "not_covered")
        status, out, _ = self.request("markets?source=unknown&source_version=1")
        self.assertEqual(status, 404)
        self.assertEqual(out["errors"][0]["code"], "not_covered")

    def test_metric_definitions_are_complete_but_do_not_claim_measured_values(self):
        status, out, _ = self.request("metric-definitions?limit=100")
        self.assertEqual(status, 200)
        self.assertEqual(len(out["data"]["definitions"]), 22)
        self.assertIn("GO-MET-011", {x["id"] for x in out["data"]["definitions"]})
        self.assertNotIn("values", out["data"])
        status, spec, _ = self.request("openapi.json")
        self.assertEqual(status, 200)
        self.assertIn("/v1/google/observations", spec["paths"])

    def test_metadata_pagination_cursor_is_tenant_and_filter_bound(self):
        status, out, _ = self.request("metric-definitions")
        self.assertEqual(status, 200)
        self.assertEqual(len(out["data"]["definitions"]), 20)
        cursor = out["data"]["next_cursor"]
        self.assertTrue(cursor)
        status, rest, _ = self.request("metric-definitions?cursor=" + cursor)
        self.assertEqual(status, 200)
        self.assertEqual(len(rest["data"]["definitions"]), 2)
        self.assertIsNone(rest["data"]["next_cursor"])
        status, _, _ = self.request("metric-definitions?cursor=" + cursor, key=self.other["api_key"])
        self.assertEqual(status, 400)
        status, _, _ = self.request("metric-definitions?limit=100&cursor=" + cursor)
        self.assertEqual(status, 400)

    def test_connections_and_capabilities_do_not_escape_tenant_or_key_asset_scope(self):
        status, out, _ = self.request("connections")
        self.assertEqual(status, 200)
        self.assertEqual(out["data"]["connections"][0]["asset_id"], "asset:fixture")
        raw = json.dumps(out)
        self.assertNotIn(self.account["api_key"], raw)
        self.assertNotIn("access_token", raw)
        status, out, _ = self.request("connections", key=self.other["api_key"])
        self.assertEqual(status, 200)
        self.assertEqual(out["data"]["connections"], [])
        other_key = self.store.issue_key(self.tenant, "unassigned")["api_key"]
        status, out, _ = self.request("connections", key=other_key)
        self.assertEqual(out["data"]["connections"], [])
        status, out, _ = self.request("capabilities", key=other_key)
        self.assertEqual(out["data"]["sources"], [])

    def test_capabilities_intersect_source_rights_with_asset_field_and_use_scope(self):
        broader = replace(self.source, field_rights=(*self.source.field_rights, FieldRight(
            "body_text", frozenset({"raw_store", "internal_display", "export"}),
            NOW + timedelta(days=1))))
        self.registry.save_source(self.tenant, broader, expected_revision=1,
                                  actor_ref="fixture:operator", now=NOW)
        narrower = replace(self.grant, version="2", uses=frozenset({"internal_display"}))
        self.registry.save_grant(narrower, expected_revision=1,
                                 actor_ref="fixture:operator", now=NOW)
        status, out, _ = self.request("capabilities")
        self.assertEqual(status, 200)
        fields = out["data"]["sources"][0]["fields"]
        self.assertEqual([x["field"] for x in fields], ["title"])
        self.assertEqual(fields[0]["allowed_uses"], ["internal_display"])
        self.assertEqual(out["meta"]["rights_expiry"], "2026-11-04T02:00:00Z")

    def test_unapproved_capability_is_absent_and_admission_fails_for_same_use(self):
        title = next(right for right in self.source.field_rights if right.field == "title")
        source = replace(
            self.source,
            field_rights=(replace(title, allowed_uses=frozenset({"internal_display"})),))
        self.registry.save_source(self.tenant, source, expected_revision=1,
                                  actor_ref="fixture:operator", now=NOW)
        status, out, _ = self.request("capabilities")
        self.assertEqual(status, 200)
        field = out["data"]["sources"][0]["fields"][0]
        self.assertEqual(field["field"], "title")
        self.assertEqual(field["allowed_uses"], ["internal_display"])
        self.assertNotIn("redistribute", field["allowed_uses"])
        principal = self.registry.principal(
            self.tenant, subject_ref=self.subject, now=self.clock)
        with self.assertRaises(DomainError) as error:
            authorize(principal, self.grant, source, "title", "redistribute", now=self.clock)
        self.assertEqual(error.exception.code, "use_not_permitted")

    def test_revocation_during_multi_query_response_discards_assembled_metadata(self):
        original = GoogleDomainService.read
        def race(service, principal, path, parameters, **kw):
            result = original(service, principal, path, parameters, **kw)
            self.registry.save_grant(replace(self.grant, revoked=True, version="2"),
                                     expected_revision=1, actor_ref="fixture:operator", now=NOW)
            return result
        with patch.object(GoogleDomainService, "read", race):
            status, out, _ = self.request("connections")
        self.assertEqual(status, 403)
        self.assertIsNone(out["data"])

    def test_envelope_expiry_never_exceeds_the_shortest_source_field_deadline(self):
        deadline = NOW + timedelta(minutes=30)
        source = replace(self.source, field_rights=(
            replace(self.source.field_rights[0], retention_deadline=deadline),))
        self.registry.save_source(self.tenant, source, expected_revision=1,
                                  actor_ref="fixture:operator", now=NOW)
        status, out, _ = self.request("capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(out["meta"]["rights_expiry"], "2026-11-04T00:30:00Z")

    def test_revoked_grant_stops_market_read_and_removes_connections(self):
        self.registry.save_grant(replace(self.grant, revoked=True, version="2"),
                                 expected_revision=1, actor_ref="fixture:operator", now=NOW)
        status, out, _ = self.request("markets?source=fixture&source_version=1")
        self.assertEqual(status, 403)
        self.assertEqual(out["errors"][0]["code"], "not_authorized")
        status, out, _ = self.request("connections")
        self.assertEqual(out["data"]["connections"], [])

    def test_uncovered_domains_and_unknown_or_duplicate_parameters_are_not_empty_success(self):
        status, out, _ = self.request("observations")
        self.assertEqual(status, 501)
        self.assertIsNone(out["data"])
        self.assertEqual(out["errors"][0]["code"], "not_covered")
        for path in ("capabilities?tenant_id=other", "capabilities?source=a&source=b",
                     "metric-definitions?limit=101"):
            status, out, _ = self.request(path)
            self.assertEqual(status, 400)
            self.assertEqual(out["errors"][0]["code"], "invalid_argument")
        status, out, _ = self.request("not-a-route")
        self.assertEqual(status, 404)

    def test_management_quality_is_not_readable_by_customer_reader(self):
        status, out, _ = self.request("quality")
        self.assertEqual(status, 403)
        self.registry.save_subject(self.tenant, self.subject, grant_ids={"grant:fixture"},
                                   roles={"reader", "manager"}, expected_revision=1,
                                   actor_ref="fixture:operator", now=NOW)
        status, out, _ = self.request("quality")
        self.assertEqual(status, 200)
        self.assertEqual(out["data"]["registry_entries"], 1)
        self.assertEqual(out["data"]["observation_quality"]["availability"], "not_covered")

    def test_read_limits_return_retry_after_and_bound_concurrency_across_instances(self):
        self.clock += timedelta(seconds=1)
        results = [self.request("metric-definitions", advance=False) for _ in range(6)]
        self.assertEqual([x[0] for x in results], [200, 200, 200, 200, 200, 429])
        self.assertEqual(results[-1][2]["Retry-After"], "1")
        first, second = ApiReadLimits(self.dsn), ApiReadLimits(self.dsn)
        future = self.clock + timedelta(seconds=1)
        with ExitStack() as stack:
            stack.enter_context(first.hold(self.tenant, now=future))
            stack.enter_context(second.hold(self.tenant, now=future))
            with self.assertRaises(DomainError):
                stack.enter_context(ApiReadLimits(self.dsn).hold(self.tenant, now=future))
        with first.hold(self.tenant, now=future + timedelta(seconds=1)):
            pass

    def test_new_domain_requests_do_not_create_collection_or_change_switches(self):
        with self.base.connect() as db:
            before = db.execute("SELECT count(*) n FROM campaigns").fetchone()["n"]
        self.request("capabilities")
        self.request("live-queries", payload={"query": "must not execute"})
        self.request("monitors", payload={"enabled": True})
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM campaigns").fetchone()["n"], before)
        self.assertFalse(self.console.settings(self.config)["continuous_enabled"])
        self.assertFalse(self.console.settings(self.config)["content_fetch_enabled"])

    def test_production_handler_patch_delete_use_the_domain_envelope_without_writes(self):
        status, out, _ = self.request("monitors/test", method="PATCH")
        self.assertEqual(status, 501)
        self.assertEqual(out["errors"][0]["code"], "not_covered")
        self.assertIsNone(out["data"])
        status, out, _ = self.request(
            "subscriptions/test", method="DELETE")
        self.assertEqual(status, 404)
        self.assertEqual(out["errors"][0]["code"], "not_found")
        self.assertIsNone(out["data"])
        status, out, _ = self.request("monitors/test", method="PATCH", key="invalid")
        self.assertEqual(status, 401)

    def test_cross_tenant_export_and_frozen_manifest_do_not_leak(self):
        self.configure_projection_fixture()
        payload = dict(source_path="/observations", filters={},
                       fields=["run_id"], format="jsonl")
        with patch("realtime.google_http.GoogleDomainService", return_value=self.service):
            status, export, _ = self.request(
                "exports", payload=payload, headers={"Idempotency-Key": "tenant-one"})
        self.assertEqual(status, 200, export)
        other_tenant = str(self.other["account_id"])
        self.other_key = self.store.issue_key(self.other["account_id"], "google-reader")
        self.registry.save_source(
            other_tenant, replace(self.source, production_approved=False),
            expected_revision=0, actor_ref="fixture:operator", now=self.clock)
        self.registry.save_grant(
            Grant(other_tenant, "asset:other", "grant:other", "1",
                  frozenset({"title"}), frozenset({"raw_store"}),
                  NOW + timedelta(hours=1), source_adapter="fixture",
                  source_version="1"),
            expected_revision=0, actor_ref="fixture:operator", now=self.clock)
        self.registry.save_subject(
            other_tenant, str(self.other_key["id"]), grant_ids={"grant:other"},
            roles={"reader"}, expected_revision=0,
            actor_ref="fixture:operator", now=self.clock)
        other_principal = self.registry.principal(
            other_tenant, subject_ref=str(self.other_key["id"]), now=self.clock)
        # The second tenant cannot see tenant one's export in list output.
        # Its valid but projection-incompatible reader must still receive an
        # authorization error rather than tenant one's export.
        with self.assertRaises(DomainError) as error:
            self.exports.list(other_principal, {}, now=self.clock)
        self.assertEqual(error.exception.code, "use_not_permitted")
        with self.assertRaises(DomainError) as error:
            self.exports.detail(
                other_principal, "export:cross-tenant-unseen", now=self.clock)
        self.assertEqual(error.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
