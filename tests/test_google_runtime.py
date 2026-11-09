import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from realtime.google_evidence import CursorCodec
from realtime.google_runtime import configure_google_service


class ReaderFixture:
    contract_ref = "fixture:reader:1"
    dataset_id = "fixture:dataset:1"

    def read_version(self, tenant_id, reference):
        raise AssertionError("Runtime wiring test must not read source data")


class MetricsFixture:
    contract_ref = "fixture:metrics:1"
    dataset_id = "fixture:metrics-dataset:1"

    def read_keyword_metrics(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")

    def read_trend_series(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")

    def read_trend_related(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")


class LiveExecutorFixture:
    contract_ref = "fixture:live:1"
    dataset_id = "fixture:live-dataset:1"
    source_adapter = "fixture"
    source_version = "1"
    source_api_version = "1"

    def execute(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not execute source data")


class SearchConsoleFixture:
    contract_ref = "fixture:gsc:1"
    dataset_id = "fixture:gsc-dataset:1"

    def read_report(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")

    def inspect_url(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not inspect source data")


class GA4Fixture:
    contract_ref = "fixture:ga4:1"
    dataset_id = "fixture:ga4-dataset:1"

    def read_report(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")

    def read_reconciliation(self, tenant_id, request):
        raise AssertionError("Runtime wiring test must not read source data")


class AdsFixture:
    contract_ref = "fixture:ads:1"
    dataset_id = "fixture:ads-dataset:1"

    def binding(self, tenant_id, access_id):
        raise AssertionError("Runtime wiring must not access provider bindings")

    def read_managed(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read managed Ads output")


class MerchantFixture:
    contract_ref = "fixture:merchant:1"
    dataset_id = "fixture:merchant-dataset:1"

    def read_report(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read Merchant output")


class LocalFixture:
    contract_ref = "fixture:local:1"
    dataset_id = "fixture:local-dataset:1"

    def grids(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read Local grids")

    def observations(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read Local observations")

    def place_reference(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read Local places")

    def review_insights(self, tenant_id, request):
        raise AssertionError("Runtime wiring must not read Local reviews")


class GoogleRuntimeTests(unittest.TestCase):
    def test_incomplete_or_unapproved_reader_configuration_fails_closed(self):
        service = SimpleNamespace(projection=None)
        with self.assertRaises(Exception) as error:
            configure_google_service(service, env={
                "SEARCHFLOW_GOOGLE_READER_MODULE": "fixture:reader",
            })
        self.assertEqual(error.exception.code, "not_covered")

        with self.assertRaises(Exception) as error:
            configure_google_service(service, env={
                "SEARCHFLOW_GOOGLE_READER_MODULE": "fixture:reader",
                "SEARCHFLOW_GOOGLE_READER_CONTRACT_REF": "fixture:reader:1",
                "SEARCHFLOW_GOOGLE_READER_DATASET_ID": "fixture:dataset:1",
                "SEARCHFLOW_GOOGLE_READER_APPROVAL_REF": "fixture:approval:1",
                "SEARCHFLOW_GOOGLE_READER_APPROVED": "false",
                "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED": "true",
            }, reader_factory=lambda _env: ReaderFixture())
        self.assertEqual(error.exception.code, "not_covered")

    def test_complete_approval_wires_local_services_but_starts_no_worker(self):
        service = SimpleNamespace(
            projection=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_READER_MODULE": "fixture:reader",
            "SEARCHFLOW_GOOGLE_READER_CONTRACT_REF": "fixture:reader:1",
            "SEARCHFLOW_GOOGLE_READER_DATASET_ID": "fixture:dataset:1",
            "SEARCHFLOW_GOOGLE_READER_APPROVAL_REF": "fixture:approval:1",
            "SEARCHFLOW_GOOGLE_READER_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED": "true",
        }
        configured = configure_google_service(
            service,
            env=env,
            reader_factory=lambda _env: ReaderFixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.projection)
        self.assertIsNotNone(service.exports)
        self.assertIsNotNone(service.jobs)
        self.assertIsNotNone(service.reports)
        self.assertIsNotNone(service.watchlists)
        self.assertIsNotNone(service.monitors)
        self.assertIsNotNone(service.analytics)
        self.assertEqual(service.initialize.call_count, 2)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))

    def test_merchant_adapter_has_independent_approval_gate(self):
        service = SimpleNamespace(
            projection=None,
            merchant=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_MERCHANT_MODULE": "fixture:merchant",
            "SEARCHFLOW_GOOGLE_MERCHANT_CONTRACT_REF": "fixture:merchant:1",
            "SEARCHFLOW_GOOGLE_MERCHANT_DATASET_ID": "fixture:merchant-dataset:1",
            "SEARCHFLOW_GOOGLE_MERCHANT_APPROVAL_REF": "fixture:approval:merchant",
            "SEARCHFLOW_GOOGLE_MERCHANT_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_MERCHANT_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_OAUTH_SCOPES":
                "https://www.googleapis.com/auth/content.readonly",
        }
        configured = configure_google_service(
            service,
            env=env,
            merchant_factory=lambda _env: MerchantFixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.merchant)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))
        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    merchant=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={
                    **env,
                    "SEARCHFLOW_GOOGLE_MERCHANT_PRODUCTION_APPROVED": "false",
                },
                merchant_factory=lambda _env: MerchantFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_parser_contract_is_optional_but_complete_when_configured(self):
        service = SimpleNamespace(
            projection=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_READER_MODULE": "fixture:reader",
            "SEARCHFLOW_GOOGLE_READER_CONTRACT_REF": "fixture:reader:1",
            "SEARCHFLOW_GOOGLE_READER_DATASET_ID": "fixture:dataset:1",
            "SEARCHFLOW_GOOGLE_READER_APPROVAL_REF": "fixture:approval:1",
            "SEARCHFLOW_GOOGLE_READER_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_PARSER_SOURCE": "fixture",
            "SEARCHFLOW_GOOGLE_PARSER_SOURCE_VERSION": "1",
            "SEARCHFLOW_GOOGLE_PARSER_VERSION": "p1",
            "SEARCHFLOW_GOOGLE_PARSER_DOM_SIGNATURE": "dom:v1",
            "SEARCHFLOW_GOOGLE_PARSER_CAPABILITIES": "organic,title",
            "SEARCHFLOW_GOOGLE_PARSER_RESULT_TYPES": "organic",
        }
        configured = configure_google_service(
            service,
            env=env,
            reader_factory=lambda _env: ReaderFixture(),
        )
        self.assertIsNotNone(configured.projection.parser_quality_gate)
        self.assertEqual(
            configured.projection.parser_quality_gate.contract.parser_version,
            "p1",
        )
        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={**env, "SEARCHFLOW_GOOGLE_PARSER_VERSION": ""},
                reader_factory=lambda _env: ReaderFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")
        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(projection=None),
                env={
                    "SEARCHFLOW_GOOGLE_PARSER_SOURCE": "fixture",
                },
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_local_adapter_has_independent_approval_gate(self):
        service = SimpleNamespace(
            projection=None,
            local=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_LOCAL_MODULE": "fixture:local",
            "SEARCHFLOW_GOOGLE_LOCAL_CONTRACT_REF": "fixture:local:1",
            "SEARCHFLOW_GOOGLE_LOCAL_DATASET_ID": "fixture:local-dataset:1",
            "SEARCHFLOW_GOOGLE_LOCAL_APPROVAL_REF": "fixture:approval:local",
            "SEARCHFLOW_GOOGLE_LOCAL_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED": "true",
        }
        configured = configure_google_service(
            service, env=env, local_factory=lambda _env: LocalFixture())
        self.assertIs(configured, service)
        self.assertIsNotNone(service.local)
        self.assertFalse(hasattr(service, "worker_started"))
        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    local=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={
                    **env,
                    "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED": "false",
                },
                local_factory=lambda _env: LocalFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_metrics_adapter_has_independent_approval_gate(self):
        service = SimpleNamespace(
            projection=None,
            metrics=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_METRICS_MODULE": "fixture:metrics",
            "SEARCHFLOW_GOOGLE_METRICS_CONTRACT_REF": "fixture:metrics:1",
            "SEARCHFLOW_GOOGLE_METRICS_DATASET_ID": "fixture:metrics-dataset:1",
            "SEARCHFLOW_GOOGLE_METRICS_APPROVAL_REF": "fixture:approval:2",
            "SEARCHFLOW_GOOGLE_METRICS_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_METRICS_PRODUCTION_APPROVED": "true",
        }
        configured = configure_google_service(
            service,
            env=env,
            metrics_factory=lambda _env: MetricsFixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.metrics)
        self.assertIsNone(service.projection)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))

        unapproved = {
            **env,
            "SEARCHFLOW_GOOGLE_METRICS_PRODUCTION_APPROVED": "false",
        }
        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    metrics=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env=unapproved,
                metrics_factory=lambda _env: MetricsFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_live_executor_requires_explicit_production_approval(self):
        service = SimpleNamespace(
            projection=None,
            metrics=None,
            live_queries=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_LIVE_MODULE": "fixture:live",
            "SEARCHFLOW_GOOGLE_LIVE_CONTRACT_REF": "fixture:live:1",
            "SEARCHFLOW_GOOGLE_LIVE_DATASET_ID": "fixture:live-dataset:1",
            "SEARCHFLOW_GOOGLE_LIVE_APPROVAL_REF": "fixture:approval:3",
            "SEARCHFLOW_GOOGLE_LIVE_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_LIVE_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_LIVE_SOURCE_ADAPTER": "fixture",
            "SEARCHFLOW_GOOGLE_LIVE_SOURCE_VERSION": "1",
        }
        configured = configure_google_service(
            service,
            env=env,
            live_factory=lambda _env: LiveExecutorFixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.live_queries)
        self.assertIsNone(service.projection)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))

        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    metrics=None,
                    live_queries=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={
                    **env,
                    "SEARCHFLOW_GOOGLE_LIVE_PRODUCTION_APPROVED": "false",
                },
                live_factory=lambda _env: LiveExecutorFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_search_console_adapter_has_independent_approval_gate(self):
        service = SimpleNamespace(
            projection=None,
            metrics=None,
            search_console=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(
                cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_GSC_MODULE": "fixture:gsc",
            "SEARCHFLOW_GOOGLE_GSC_CONTRACT_REF": "fixture:gsc:1",
            "SEARCHFLOW_GOOGLE_GSC_DATASET_ID": "fixture:gsc-dataset:1",
            "SEARCHFLOW_GOOGLE_GSC_APPROVAL_REF": "fixture:approval:gsc",
            "SEARCHFLOW_GOOGLE_GSC_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_OAUTH_SCOPES":
                "https://www.googleapis.com/auth/webmasters.readonly",
        }
        configured = configure_google_service(
            service,
            env=env,
            search_console_factory=lambda _env: SearchConsoleFixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.search_console)
        self.assertIsNone(service.projection)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))

        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    metrics=None,
                    search_console=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={
                    **env,
                    "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED": "false",
                },
                search_console_factory=lambda _env: SearchConsoleFixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_write_capable_oauth_scopes_cannot_wire_approved_adapters(self):
        service = SimpleNamespace(
            projection=None,
            metrics=None,
            search_console=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(
                cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_GSC_MODULE": "fixture:gsc",
            "SEARCHFLOW_GOOGLE_GSC_CONTRACT_REF": "fixture:gsc:1",
            "SEARCHFLOW_GOOGLE_GSC_DATASET_ID": "fixture:gsc-dataset:1",
            "SEARCHFLOW_GOOGLE_GSC_APPROVAL_REF": "fixture:approval:gsc",
            "SEARCHFLOW_GOOGLE_GSC_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED": "true",
        }
        for scope in (
                "https://www.googleapis.com/auth/indexing",
                "https://www.googleapis.com/auth/webmasters",
                "https://www.googleapis.com/auth/cloud-platform",
                "https://example.test/custom.readonly"):
            with self.subTest(scope=scope), self.assertRaises(Exception) as error:
                configure_google_service(
                    service,
                    env={**env, "SEARCHFLOW_GOOGLE_OAUTH_SCOPES": scope},
                    search_console_factory=lambda _env: SearchConsoleFixture(),
                )
            self.assertEqual(error.exception.code, "not_covered")

    def test_ga4_adapter_has_independent_approval_gate(self):
        service = SimpleNamespace(
            projection=None,
            metrics=None,
            search_console=None,
            authorized_analytics=None,
            registry=SimpleNamespace(dsn="postgresql://fixture"),
            limits=SimpleNamespace(
                cursor_codec=lambda: CursorCodec(b"x" * 32)),
            initialize=Mock(),
        )
        env = {
            "SEARCHFLOW_GOOGLE_GA4_MODULE": "fixture:ga4",
            "SEARCHFLOW_GOOGLE_GA4_CONTRACT_REF": "fixture:ga4:1",
            "SEARCHFLOW_GOOGLE_GA4_DATASET_ID": "fixture:ga4-dataset:1",
            "SEARCHFLOW_GOOGLE_GA4_APPROVAL_REF": "fixture:approval:ga4",
            "SEARCHFLOW_GOOGLE_GA4_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_GA4_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_OAUTH_SCOPES":
                "https://www.googleapis.com/auth/analytics.readonly",
        }
        configured = configure_google_service(
            service,
            env=env,
            ga4_factory=lambda _env: GA4Fixture(),
        )
        self.assertIs(configured, service)
        self.assertIsNotNone(service.authorized_analytics)
        self.assertIsNone(service.projection)
        self.assertFalse(hasattr(service, "worker_started"))
        self.assertFalse(hasattr(service, "scheduler_started"))

        with self.assertRaises(Exception) as error:
            configure_google_service(
                SimpleNamespace(
                    projection=None,
                    metrics=None,
                    search_console=None,
                    authorized_analytics=None,
                    registry=SimpleNamespace(dsn="postgresql://fixture"),
                    limits=SimpleNamespace(
                        cursor_codec=lambda: CursorCodec(b"x" * 32)),
                    initialize=Mock(),
                ),
                env={
                    **env,
                    "SEARCHFLOW_GOOGLE_GA4_PRODUCTION_APPROVED": "false",
                },
                ga4_factory=lambda _env: GA4Fixture(),
            )
        self.assertEqual(error.exception.code, "not_covered")

    def test_ads_independent_approval_and_contract_are_required_before_factory(self):
        env = {
            "SEARCHFLOW_GOOGLE_ADS_MODULE": "fixture:ads",
            "SEARCHFLOW_GOOGLE_ADS_CONTRACT_REF": "fixture:ads:1",
            "SEARCHFLOW_GOOGLE_ADS_DATASET_ID": "fixture:ads-dataset:1",
            "SEARCHFLOW_GOOGLE_ADS_APPROVAL_REF": "fixture:approval:ads",
            "SEARCHFLOW_GOOGLE_ADS_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_ADS_PRODUCTION_APPROVED": "true",
            "SEARCHFLOW_GOOGLE_OAUTH_SCOPES":
                "https://www.googleapis.com/auth/ads.readonly",
        }

        def service():
            return SimpleNamespace(
                ads=None, registry=SimpleNamespace(dsn="postgresql://fixture"),
                limits=SimpleNamespace(cursor_codec=lambda: CursorCodec(b"x" * 32)),
                initialize=Mock())

        factory = Mock(return_value=AdsFixture())
        for change in (
                {"SEARCHFLOW_GOOGLE_ADS_APPROVED": "false"},
                {"SEARCHFLOW_GOOGLE_ADS_PRODUCTION_APPROVED": "false"},
                {"SEARCHFLOW_GOOGLE_ADS_APPROVAL_REF": ""}):
            with self.subTest(change=change), self.assertRaises(Exception) as error:
                configure_google_service(service(), env={**env, **change}, ads_factory=factory)
            self.assertEqual(error.exception.code, "not_covered")
        factory.assert_not_called()
        value = service()
        configure_google_service(value, env=env, ads_factory=factory)
        self.assertIsNotNone(value.ads)
        self.assertFalse(hasattr(value, "worker_started"))
        self.assertFalse(hasattr(value, "scheduler_started"))
        with self.assertRaises(Exception) as error:
            configure_google_service(
                service(),
                env={**env, "SEARCHFLOW_GOOGLE_ADS_DATASET_ID": "other"},
                ads_factory=factory)
        self.assertEqual(error.exception.code, "not_covered")


if __name__ == "__main__":
    unittest.main()
