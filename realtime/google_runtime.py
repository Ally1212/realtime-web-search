"""Optional dependency injection for an approved independent Google source reader."""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone

from .google_domain import DomainError
from .google_metrics_sources import MetricsSourceService
from .google_parser_quality import ParserContract, ParserQualityGate
from .google_projection import ObservationProjection, ReaderAdmission

READER_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_READER_MODULE",
    "SEARCHFLOW_GOOGLE_READER_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_READER_DATASET_ID",
    "SEARCHFLOW_GOOGLE_READER_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_READER_APPROVED",
    "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED",
})
METRICS_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_METRICS_MODULE",
    "SEARCHFLOW_GOOGLE_METRICS_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_METRICS_DATASET_ID",
    "SEARCHFLOW_GOOGLE_METRICS_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_METRICS_APPROVED",
    "SEARCHFLOW_GOOGLE_METRICS_PRODUCTION_APPROVED",
})
LIVE_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_LIVE_MODULE",
    "SEARCHFLOW_GOOGLE_LIVE_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_LIVE_DATASET_ID",
    "SEARCHFLOW_GOOGLE_LIVE_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_LIVE_APPROVED",
    "SEARCHFLOW_GOOGLE_LIVE_PRODUCTION_APPROVED",
    "SEARCHFLOW_GOOGLE_LIVE_SOURCE_ADAPTER",
    "SEARCHFLOW_GOOGLE_LIVE_SOURCE_VERSION",
})
SEARCH_CONSOLE_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_GSC_MODULE",
    "SEARCHFLOW_GOOGLE_GSC_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_GSC_DATASET_ID",
    "SEARCHFLOW_GOOGLE_GSC_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_GSC_APPROVED",
    "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED",
})
GA4_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_GA4_MODULE",
    "SEARCHFLOW_GOOGLE_GA4_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_GA4_DATASET_ID",
    "SEARCHFLOW_GOOGLE_GA4_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_GA4_APPROVED",
    "SEARCHFLOW_GOOGLE_GA4_PRODUCTION_APPROVED",
})
ADS_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_ADS_MODULE",
    "SEARCHFLOW_GOOGLE_ADS_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_ADS_DATASET_ID",
    "SEARCHFLOW_GOOGLE_ADS_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_ADS_APPROVED",
    "SEARCHFLOW_GOOGLE_ADS_PRODUCTION_APPROVED",
})
MERCHANT_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_MERCHANT_MODULE",
    "SEARCHFLOW_GOOGLE_MERCHANT_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_MERCHANT_DATASET_ID",
    "SEARCHFLOW_GOOGLE_MERCHANT_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_MERCHANT_APPROVED",
    "SEARCHFLOW_GOOGLE_MERCHANT_PRODUCTION_APPROVED",
})
OAUTH_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_OAUTH_SCOPES",
})
LOCAL_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_LOCAL_MODULE",
    "SEARCHFLOW_GOOGLE_LOCAL_CONTRACT_REF",
    "SEARCHFLOW_GOOGLE_LOCAL_DATASET_ID",
    "SEARCHFLOW_GOOGLE_LOCAL_APPROVAL_REF",
    "SEARCHFLOW_GOOGLE_LOCAL_APPROVED",
    "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED",
})
PARSER_ENV_KEYS = frozenset({
    "SEARCHFLOW_GOOGLE_PARSER_SOURCE",
    "SEARCHFLOW_GOOGLE_PARSER_SOURCE_VERSION",
    "SEARCHFLOW_GOOGLE_PARSER_VERSION",
    "SEARCHFLOW_GOOGLE_PARSER_DOM_SIGNATURE",
    "SEARCHFLOW_GOOGLE_PARSER_CAPABILITIES",
    "SEARCHFLOW_GOOGLE_PARSER_RESULT_TYPES",
})
FORBIDDEN_OAUTH_SCOPES = frozenset({
    "https://www.googleapis.com/auth/indexing",
    "https://www.googleapis.com/auth/webmasters",
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/content",
    "https://www.googleapis.com/auth/business.manage",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/cloud-platform",
})
READ_ONLY_OAUTH_HINTS = frozenset({
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/ads.readonly",
    "https://www.googleapis.com/auth/content.readonly",
})


def _bool_env(env, name):
    value = env.get(name)
    if value is None:
        return False
    if value not in {"true", "false"}:
        raise DomainError(
            "not_covered", f"{name} must be explicitly true or false", 503)
    return value == "true"


def _load_component(env, factory, *, prefix):
    if factory is not None:
        component = factory(env)
    else:
        module_path = env[f"SEARCHFLOW_GOOGLE_{prefix}_MODULE"]
        if ":" not in module_path:
            raise DomainError(
                "not_covered",
                f"Google {prefix.lower()} module must use module:callable syntax",
                503,
            )
        module_name, attribute = module_path.rsplit(":", 1)
        try:
            factory = getattr(importlib.import_module(module_name), attribute)
            component = factory(env)
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "not_covered",
                f"Approved Google {prefix.lower()} factory could not be loaded",
                503,
            ) from exc
    return component


def _load_reader(env, reader_factory=None):
    reader = _load_component(env, reader_factory, prefix="READER")
    if (
        getattr(reader, "contract_ref", None) != env["SEARCHFLOW_GOOGLE_READER_CONTRACT_REF"]
        or getattr(reader, "dataset_id", None) != env["SEARCHFLOW_GOOGLE_READER_DATASET_ID"]
        or not callable(getattr(reader, "read_version", None))
    ):
        raise DomainError(
            "not_covered",
            "Google reader does not match the explicitly approved contract and dataset",
            503,
        )
    return reader


def _load_parser_gate(env):
    values = {}
    for name in (
            "SOURCE", "SOURCE_VERSION", "VERSION", "DOM_SIGNATURE",
            "CAPABILITIES", "RESULT_TYPES"):
        value = env.get("SEARCHFLOW_GOOGLE_PARSER_" + name)
        if value is None or not value.strip():
            raise DomainError(
                "not_covered", "Parser quality configuration is incomplete", 503)
        values[name] = value.strip()

    def items(name):
        return frozenset(
            item.strip() for item in values[name].split(",") if item.strip())

    try:
        return ParserQualityGate(ParserContract(
            source=values["SOURCE"],
            source_version=values["SOURCE_VERSION"],
            parser_version=values["VERSION"],
            dom_signature=values["DOM_SIGNATURE"],
            capabilities=items("CAPABILITIES"),
            known_result_types=items("RESULT_TYPES"),
        ))
    except (DomainError, TypeError):
        raise DomainError(
            "not_covered", "Parser quality contract is invalid", 503) from None


def _oauth_scope_contract(env):
    raw = env.get("SEARCHFLOW_GOOGLE_OAUTH_SCOPES")
    if raw is None or not raw.strip():
        raise DomainError(
            "not_covered", "Approved Google adapters require OAuth scope declaration", 503)
    scopes = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if not scopes or len(scopes) != len([x for x in raw.split(",") if x.strip()]):
        raise DomainError(
            "not_covered", "OAuth scope declaration is empty or malformed", 503)
    if scopes & FORBIDDEN_OAUTH_SCOPES or not scopes <= READ_ONLY_OAUTH_HINTS:
        raise DomainError(
            "not_covered", "Google OAuth scope is write-capable or unapproved", 503)
    return scopes


def _load_metrics(env, metrics_factory=None):
    metrics = _load_component(env, metrics_factory, prefix="METRICS")
    if (
            getattr(metrics, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_METRICS_CONTRACT_REF"]
            or getattr(metrics, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_METRICS_DATASET_ID"]
            or not all(callable(getattr(metrics, name, None)) for name in (
                "read_keyword_metrics",
                "read_trend_series",
                "read_trend_related",
            ))
    ):
        raise DomainError(
            "not_covered",
            "Google metrics adapter does not match the explicitly approved "
            "contract and dataset",
            503,
        )
    return metrics


def _load_live_executor(env, live_factory=None):
    executor = _load_component(env, live_factory, prefix="LIVE")
    if (
            getattr(executor, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_LIVE_CONTRACT_REF"]
            or getattr(executor, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_LIVE_DATASET_ID"]
            or getattr(executor, "source_adapter", None)
            != env["SEARCHFLOW_GOOGLE_LIVE_SOURCE_ADAPTER"]
            or getattr(executor, "source_version", None)
            != env["SEARCHFLOW_GOOGLE_LIVE_SOURCE_VERSION"]
            or not callable(getattr(executor, "execute", None))
    ):
        raise DomainError(
            "not_covered",
            "Google live executor does not match the explicitly approved "
            "contract, dataset and source version",
            503,
        )
    return executor


def _load_search_console(env, search_console_factory=None):
    adapter = _load_component(
        env, search_console_factory, prefix="GSC")
    if (
            getattr(adapter, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_GSC_CONTRACT_REF"]
            or getattr(adapter, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_GSC_DATASET_ID"]
            or not callable(getattr(adapter, "read_report", None))
            or not callable(getattr(adapter, "inspect_url", None))
    ):
        raise DomainError(
            "not_covered",
            "Search Console adapter does not match the explicitly approved "
            "contract and dataset",
            503,
        )
    return adapter


def _load_ga4(env, ga4_factory=None):
    adapter = _load_component(env, ga4_factory, prefix="GA4")
    if (
            getattr(adapter, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_GA4_CONTRACT_REF"]
            or getattr(adapter, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_GA4_DATASET_ID"]
            or not callable(getattr(adapter, "read_report", None))
            or not callable(getattr(adapter, "read_reconciliation", None))
    ):
        raise DomainError(
            "not_covered",
            "GA4 adapter does not match the explicitly approved contract "
            "and dataset",
            503,
        )
    return adapter


def _load_ads(env, ads_factory=None):
    adapter = _load_component(env, ads_factory, prefix="ADS")
    if (
            getattr(adapter, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_ADS_CONTRACT_REF"]
            or getattr(adapter, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_ADS_DATASET_ID"]
            or not callable(getattr(adapter, "binding", None))
            or not callable(getattr(adapter, "read_managed", None))
    ):
        raise DomainError(
            "not_covered",
            "Managed Ads adapter does not match the approved contract and dataset",
            503,
        )
    return adapter


def _load_merchant(env, merchant_factory=None):
    adapter = _load_component(env, merchant_factory, prefix="MERCHANT")
    if (
            getattr(adapter, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_MERCHANT_CONTRACT_REF"]
            or getattr(adapter, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_MERCHANT_DATASET_ID"]
            or not callable(getattr(adapter, "read_report", None))):
        raise DomainError(
            "not_covered",
            "Merchant adapter does not match the approved contract and dataset",
            503,
        )
    return adapter


def _load_local(env, local_factory=None):
    adapter = _load_component(env, local_factory, prefix="LOCAL")
    if (
            getattr(adapter, "contract_ref", None)
            != env["SEARCHFLOW_GOOGLE_LOCAL_CONTRACT_REF"]
            or getattr(adapter, "dataset_id", None)
            != env["SEARCHFLOW_GOOGLE_LOCAL_DATASET_ID"]
            or not all(callable(getattr(adapter, name, None)) for name in (
                "grids", "observations", "place_reference", "review_insights"))):
        raise DomainError(
            "not_covered",
            "Local adapter does not match the approved contract and dataset",
            503,
        )
    return adapter


def configure_google_service(
        service, *, env=None, reader_factory=None, metrics_factory=None,
        live_factory=None, search_console_factory=None, ga4_factory=None,
        ads_factory=None, merchant_factory=None, local_factory=None, clock=None):
    """Attach local services only when a complete approved reader is configured."""
    env = os.environ if env is None else env
    configured_reader = {
        key for key in READER_ENV_KEYS if env.get(key) not in {None, ""}}
    configured_metrics = {
        key for key in METRICS_ENV_KEYS if env.get(key) not in {None, ""}}
    configured_live = {
        key for key in LIVE_ENV_KEYS if env.get(key) not in {None, ""}}
    configured_search_console = {
        key for key in SEARCH_CONSOLE_ENV_KEYS
        if env.get(key) not in {None, ""}}
    configured_ga4 = {
        key for key in GA4_ENV_KEYS
        if env.get(key) not in {None, ""}}
    configured_ads = {
        key for key in ADS_ENV_KEYS
        if env.get(key) not in {None, ""}}
    configured_merchant = {
        key for key in MERCHANT_ENV_KEYS
        if env.get(key) not in {None, ""}}
    configured_local = {
        key for key in LOCAL_ENV_KEYS
        if env.get(key) not in {None, ""}}
    configured_parser = {
        key for key in PARSER_ENV_KEYS
        if env.get(key) not in {None, ""}}
    oauth_scopes = None
    requires_oauth = any((
        configured_search_console, configured_ga4, configured_ads,
        configured_merchant,
    ))
    if requires_oauth:
        oauth_scopes = _oauth_scope_contract(env)
    if (
            not configured_reader and not configured_metrics
            and not configured_live and not configured_search_console
            and not configured_ga4 and not configured_ads
            and not configured_merchant and not configured_local
            and not configured_parser and "SEARCHFLOW_GOOGLE_OAUTH_SCOPES" not in env):
        return service
    if configured_parser and not configured_reader:
        raise DomainError(
            "not_covered", "Parser quality requires an approved reader configuration", 503)
    # Explicitly supplying only an OAuth declaration is not an adapter and has
    # no allowed code path.
    if not any((
            configured_reader, configured_metrics, configured_live,
            configured_search_console, configured_ga4, configured_ads,
            configured_merchant, configured_local, configured_parser,
    )):
        raise DomainError(
            "not_covered", "OAuth declaration requires an approved adapter", 503)
    optional = {
        "SEARCHFLOW_GOOGLE_READER_APPROVED",
        "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_METRICS_APPROVED",
        "SEARCHFLOW_GOOGLE_METRICS_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_LIVE_APPROVED",
        "SEARCHFLOW_GOOGLE_LIVE_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_GSC_APPROVED",
        "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_GA4_APPROVED",
        "SEARCHFLOW_GOOGLE_GA4_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_ADS_APPROVED",
        "SEARCHFLOW_GOOGLE_ADS_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_MERCHANT_APPROVED",
        "SEARCHFLOW_GOOGLE_MERCHANT_PRODUCTION_APPROVED",
        "SEARCHFLOW_GOOGLE_LOCAL_APPROVED",
        "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED",
    }
    clock = clock or (lambda: datetime.now(timezone.utc))
    if configured_reader:
        required = READER_ENV_KEYS - optional
        if required - configured_reader:
            raise DomainError(
                "not_covered",
                "Google reader configuration is incomplete; no projection was connected",
                503,
            )
        if not _bool_env(env, "SEARCHFLOW_GOOGLE_READER_APPROVED") or not _bool_env(
                env, "SEARCHFLOW_GOOGLE_READER_PRODUCTION_APPROVED"):
            raise DomainError(
                "not_covered",
                "Google reader lacks explicit approval and production approval",
                503,
            )
        if getattr(service, "projection", None) is None:
            reader = _load_reader(env, reader_factory=reader_factory)
            admission = ReaderAdmission(
                contract_ref=env["SEARCHFLOW_GOOGLE_READER_CONTRACT_REF"],
                dataset_id=env["SEARCHFLOW_GOOGLE_READER_DATASET_ID"],
                approval_ref=env["SEARCHFLOW_GOOGLE_READER_APPROVAL_REF"],
                approved=True,
                production_approved=True,
            )
            from .google_domain_analytics import GoogleDomainAnalytics
            from .google_exports import ExportService
            from .google_jobs import AnalysisJobService
            from .google_monitors import MonitorService
            from .google_quality import GoogleQualityLedger
            from .google_reports import ReportService
            from .google_watchlists import WatchlistService

            service.initialize()
            projection = ObservationProjection(
                service.registry.dsn,
                registry=service.registry,
                reader=reader,
                admission=admission,
                cursor_codec=service.limits.cursor_codec(),
                production=True,
                clock=clock,
                parser_quality_gate=(
                    _load_parser_gate(env) if configured_parser else None),
            )
            service.projection = projection
            service.exports = ExportService(
                service.registry.dsn,
                registry=service.registry,
                projection=projection,
                clock=clock,
            )
            service.jobs = AnalysisJobService(
                service.registry.dsn,
                registry=service.registry,
                manifests=service.exports,
                clock=clock,
            )
            service.reports = ReportService(
                service.registry.dsn,
                registry=service.registry,
                manifests=service.exports,
                clock=clock,
            )
            service.watchlists = WatchlistService(
                service.registry.dsn,
                registry=service.registry,
                clock=clock,
            )
            service.monitors = MonitorService(
                service.registry.dsn,
                registry=service.registry,
                clock=clock,
            )
            service.analytics = GoogleDomainAnalytics(
                projection, clock=clock, monitors=service.monitors)
            service.quality = GoogleQualityLedger(
                service.registry, projection, clock=clock)
    if configured_metrics:
        required = METRICS_ENV_KEYS - optional
        if required - configured_metrics:
            raise DomainError(
                "not_covered",
                "Google metrics configuration is incomplete; no adapter was connected",
                503,
            )
        if not _bool_env(env, "SEARCHFLOW_GOOGLE_METRICS_APPROVED") or not _bool_env(
                env, "SEARCHFLOW_GOOGLE_METRICS_PRODUCTION_APPROVED"):
            raise DomainError(
                "not_covered",
                "Google metrics lacks explicit approval and production approval",
                503,
            )
        if getattr(service, "metrics", None) is None:
            metrics_adapter = _load_metrics(
                env, metrics_factory=metrics_factory)
            service.metrics = MetricsSourceService(
                service.registry, metrics_adapter, clock=clock)
    if configured_live:
        required = LIVE_ENV_KEYS - optional
        if required - configured_live:
            raise DomainError(
                "not_covered",
                "Google live configuration is incomplete; no executor was connected",
                503,
            )
        if not _bool_env(env, "SEARCHFLOW_GOOGLE_LIVE_APPROVED") or not _bool_env(
                env, "SEARCHFLOW_GOOGLE_LIVE_PRODUCTION_APPROVED"):
            raise DomainError(
                "not_covered",
                "Google live querying lacks explicit approval and production approval",
                503,
            )
        if getattr(service, "live_queries", None) is None:
            from .google_live_queries import LiveQueryService

            executor = _load_live_executor(
                env, live_factory=live_factory)
            service.live_queries = LiveQueryService(
                service.registry.dsn,
                registry=service.registry,
                executor=executor,
                cursor_codec=service.limits.cursor_codec(),
                clock=clock,
            )
    if configured_search_console:
        required = SEARCH_CONSOLE_ENV_KEYS - optional
        if required - configured_search_console:
            raise DomainError(
                "not_covered",
                "Search Console configuration is incomplete; no adapter was connected",
                503,
            )
        if (
                not _bool_env(env, "SEARCHFLOW_GOOGLE_GSC_APPROVED")
                or not _bool_env(
                    env,
                    "SEARCHFLOW_GOOGLE_GSC_PRODUCTION_APPROVED",
                )):
            raise DomainError(
                "not_covered",
                "Search Console lacks explicit approval and production approval",
                503,
            )
        if getattr(service, "search_console", None) is None:
            from .google_search_console import SearchConsoleService

            adapter = _load_search_console(
                env, search_console_factory=search_console_factory)
            service.search_console = SearchConsoleService(
                service.registry,
                adapter,
                cursor_codec=service.limits.cursor_codec(),
                clock=clock,
            )
            service.search_console.oauth_scopes = sorted(oauth_scopes)
    if configured_ga4:
        required = GA4_ENV_KEYS - optional
        if required - configured_ga4:
            raise DomainError(
                "not_covered",
                "GA4 configuration is incomplete; no adapter was connected",
                503,
            )
        if (
                not _bool_env(env, "SEARCHFLOW_GOOGLE_GA4_APPROVED")
                or not _bool_env(
                    env,
                    "SEARCHFLOW_GOOGLE_GA4_PRODUCTION_APPROVED",
                )):
            raise DomainError(
                "not_covered",
                "GA4 lacks explicit approval and production approval",
                503,
            )
        if getattr(service, "authorized_analytics", None) is None:
            from .google_analytics import AnalyticsService

            adapter = _load_ga4(env, ga4_factory=ga4_factory)
            service.authorized_analytics = AnalyticsService(
                service.registry,
                adapter,
                cursor_codec=service.limits.cursor_codec(),
                clock=clock,
            )
            service.authorized_analytics.oauth_scopes = sorted(oauth_scopes)
    if configured_ads:
        if (ADS_ENV_KEYS - optional) - configured_ads:
            raise DomainError(
                "not_covered", "Managed Ads configuration is incomplete", 503)
        if (
                not _bool_env(env, "SEARCHFLOW_GOOGLE_ADS_APPROVED")
                or not _bool_env(env, "SEARCHFLOW_GOOGLE_ADS_PRODUCTION_APPROVED")):
            raise DomainError(
                "not_covered",
                "Managed Ads lacks explicit source and production approval",
                503,
            )
        if getattr(service, "ads", None) is None:
            from .google_ads import AdsService

            adapter = _load_ads(env, ads_factory=ads_factory)
            service.ads = AdsService(
                service.registry, adapter,
                cursor_codec=service.limits.cursor_codec(), clock=clock)
            service.ads.oauth_scopes = sorted(oauth_scopes)
    if configured_merchant:
        if (MERCHANT_ENV_KEYS - optional) - configured_merchant:
            raise DomainError(
                "not_covered", "Merchant configuration is incomplete", 503)
        if (
                not _bool_env(env, "SEARCHFLOW_GOOGLE_MERCHANT_APPROVED")
                or not _bool_env(
                    env, "SEARCHFLOW_GOOGLE_MERCHANT_PRODUCTION_APPROVED")):
            raise DomainError(
                "not_covered",
                "Merchant lacks explicit source and production approval",
                503,
            )
        if getattr(service, "merchant", None) is None:
            from .google_merchant import MerchantService

            adapter = _load_merchant(
                env, merchant_factory=merchant_factory)
            service.merchant = MerchantService(
                service.registry, adapter,
                cursor_codec=service.limits.cursor_codec(), clock=clock)
            service.merchant.oauth_scopes = sorted(oauth_scopes)
    if configured_local:
        if (LOCAL_ENV_KEYS - optional) - configured_local:
            raise DomainError(
                "not_covered", "Local configuration is incomplete", 503)
        if (
                not _bool_env(env, "SEARCHFLOW_GOOGLE_LOCAL_APPROVED")
                or not _bool_env(
                    env, "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED")):
            raise DomainError(
                "not_covered",
                "Local lacks explicit source and production approval",
                503,
            )
        if getattr(service, "local", None) is None:
            from .google_local import LocalService

            adapter = _load_local(env, local_factory=local_factory)
            service.local = LocalService(
                service.registry, adapter,
                cursor_codec=service.limits.cursor_codec(), clock=clock)
    service.initialize()
    return service
