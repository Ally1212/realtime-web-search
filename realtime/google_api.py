"""Google domain API catalog and live tenant-scoped control-plane reads.

Data endpoints without a qualified projection/adapter return not_covered, never
an empty success. This catalog describes all PRD groups, but labels unfinished
operations explicitly instead of treating the catalog as their implementation.
"""
from __future__ import annotations

import copy
import re
import secrets
from contextlib import contextmanager
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .google_catalog import API_CATALOG, METRIC_CATALOG, SOURCE_SHA256
from .google_ads import ADS_ROUTES
from .google_analytics import AUTHORIZED_ANALYTICS_ROUTES
from .google_domain import AVAILABILITY, NATURES, PROVENANCE, DomainError, parse_time, plain_json
from .google_domain_analytics import ANALYTICS_ROUTES
from .google_evidence import CursorCodec, page_limit
from .google_metrics_sources import METRICS_ROUTES
from .google_merchant import MERCHANT_ROUTES
from .google_local import LOCAL_ROUTES
from .google_quality import COMMON_PARAMETERS as QUALITY_PARAMETERS
from .google_registry import RightsRegistry
from .google_search_console import SEARCH_CONSOLE_ROUTES
from .google_subscriptions import SubscriptionService

PREFIX = "/v1/google"
METADATA_ROUTES = frozenset({
    "/capabilities", "/markets", "/locations", "/local/capabilities",
    "/metric-definitions", "/connections", "/quality", "/usage",
})
PROJECTION_ROUTES = frozenset({
    "/keywords", "/keywords/{id}", "/query-variants", "/observations", "/observations/{id}",
    "/observations/{id}/items", "/serp-history", "/serp-features",
})
EXPORT_ROUTES = frozenset({"/exports", "/exports/{id}", "/exports/{id}/content"})
ANALYSIS_ROUTES = frozenset({"/analysis-jobs", "/analysis-jobs/{id}"})
EVIDENCE_ROUTES = frozenset({"/evidence/{id}"})
REPORT_ROUTES = frozenset({"/reports", "/reports/{id}"})
WATCHLIST_ROUTES = frozenset({
    "/watchlists", "/watchlists/{id}", "/watchlists/{id}/items/{entity_id}",
})
MONITOR_ROUTES = frozenset({"/monitors", "/monitors/{id}"})
ERROR_CODES = (
    "invalid_argument", "not_authorized", "use_not_permitted", "not_covered", "unsupported",
    "source_error", "quota_exceeded", "privacy_suppressed", "incompatible_scope",
    "partial_result", "outside_observed_depth", "ingest_pending", "expired", "not_found",
    "precondition_failed", "idempotency_conflict",
)


def _parameters(path, method):
    result = [{"name": name, "in": "path", "required": True,
               "schema": {"type": "string", "minLength": 1}}
              for name in re.findall(r"\{([^}]+)\}", path)]
    detail = path in {
        "/keywords/{id}", "/observations/{id}", "/exports/{id}",
        "/exports/{id}/content", "/analysis-jobs/{id}", "/reports/{id}",
        "/watchlists/{id}", "/monitors/{id}", "/evidence/{id}", "/domains/{id}",
        "/ai/answers/{id}", "/articles/{id}", "/live-queries/{id}",
        "/subscriptions/{id}",
    }
    if (method == "GET" and not detail
            and path not in {
                "/comparisons", "/insights/anomalies",
                "/keyword-metrics", "/trends/series", "/trends/related",
            }
            and path not in AUTHORIZED_ANALYTICS_ROUTES
            and path not in MERCHANT_ROUTES
            and path not in LOCAL_ROUTES
            and (path not in METADATA_ROUTES
                 or path in {"/metric-definitions", "/connections"})):
        result += [{"$ref": "#/components/parameters/Limit"},
                   {"$ref": "#/components/parameters/Cursor"}]
    if path in {"/markets", "/locations"}:
        result += [{"name": name, "in": "query", "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 4096}}
                   for name in ("source", "source_version")]
    if path == "/locations":
        result += [{"name": "country", "in": "query", "schema": {"type": "string", "pattern": "^[A-Z]{2}$"}}]
    if path == "/metric-definitions":
        result += [{"name": "metric_id", "in": "query",
                    "schema": {"type": "string", "pattern": "^GO-MET-[0-9]{3}$"}}]
    if path in PROJECTION_ROUTES:
        names = ["query_id", "variant_id", "source", "source_version", "collection_version",
                 "observed_from", "observed_to", "status", "country", "device", "surface"]
        names += {"/keywords": ["text"], "/serp-history": ["target_url"],
                  "/serp-features": ["feature_type"], "/observations/{id}/items": ["result_type", "depth"]}.get(path, [])
        result += [{"name": name, "in": "query",
                    "required": path == "/serp-history" and name in {"variant_id", "target_url"},
                    "schema": ({"type": "integer", "minimum": 1, "maximum": 999999} if name == "depth" else
                               {"type": "string", "format": "date-time"} if name.startswith("observed_") else
                               {"type": "string", "minLength": 1})} for name in names]
    if path == "/exports" and method == "GET":
        result += [{"name": "limit", "in": "query", "schema": {
            "type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
            {"$ref": "#/components/parameters/Cursor"}]
    if path == "/analysis-jobs" and method == "GET":
        result += [{"$ref": "#/components/parameters/Limit"},
                   {"$ref": "#/components/parameters/Cursor"}]
    if path in ANALYTICS_ROUTES:
        result += [
            {"name": name, "in": "query",
             "required": name in {"observed_from", "observed_to"},
             "schema": ({"type": "string", "format": "date-time"}
                        if name.startswith("observed_") else
                        {"type": "string", "minLength": 1})}
            for name in (
                "observed_from", "observed_to", "source", "source_version",
                "collection_version", "country", "device", "surface", "status",
                "query_id", "variant_id", "query",
            )
        ]
        if path == "/domains":
            result.append({
                "name": "cohort_version", "in": "query",
                "schema": {"type": "string", "minLength": 1},
            })
        if path in {"/rankings", "/comparisons"}:
            result += [
                {"name": "domains", "in": "query",
                 "required": path == "/comparisons",
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "metric", "in": "query", "schema": {
                    "type": "string", "default": "top10_share",
                    "enum": [
                        "top3_share", "top10_share",
                        "median_position", "unique_urls",
                    ],
                }},
            ]
        if path.startswith("/news/"):
            result.append({
                "name": "topic", "in": "query",
                "description": "Exact observed query label, not a topic taxonomy ID.",
                "schema": {"type": "string", "minLength": 1},
            })
        if path == "/insights/opportunities":
            result += [
                {"name": "target", "in": "query", "required": True,
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "topic", "in": "query",
                 "description": "Exact observed query label, not a topic taxonomy ID.",
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "method", "in": "query", "schema": {
                    "type": "string", "default": "observed-rank-gap-v1",
                    "enum": ["observed-rank-gap-v1"],
                }},
            ]
        if path == "/insights/anomalies":
            result += [
                {"name": "target", "in": "query", "required": True,
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "monitor_id", "in": "query",
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "split_at", "in": "query", "required": True,
                 "schema": {"type": "string", "format": "date-time"}},
                {"name": "threshold", "in": "query", "schema": {
                    "type": "number", "default": 0.1,
                    "minimum": 0, "maximum": 1,
                }},
                {"name": "rule", "in": "query", "schema": {
                    "type": "string", "default": "top10_share_change_v1",
                    "enum": ["top10_share_change_v1"],
                }},
                {"name": "model", "in": "query", "schema": {
                    "type": "string", "default": "none_deterministic",
                    "enum": ["none_deterministic"],
                }},
            ]
        if path == "/changes":
            result += [
                {"name": "target", "in": "query",
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "split_at", "in": "query", "required": True,
                 "schema": {"type": "string", "format": "date-time"}},
                {"name": "threshold", "in": "query", "schema": {
                    "type": "number", "default": 0.1,
                    "minimum": 0, "maximum": 1,
                }},
                {"name": "event_type", "in": "query", "schema": {
                    "type": "string", "default": "top10_share_change_v1",
                    "enum": ["top10_share_change_v1"],
                }},
            ]
        if path in {
                "/ai/answers", "/ai/answers/{id}",
                "/ai/citations", "/ai/visibility",
        }:
            result.append({
                "name": "channel", "in": "query", "schema": {
                    "type": "string", "default": "aio",
                    "enum": ["aio", "ai_mode"],
                },
            })
        if path == "/ai/visibility":
            result.append({
                "name": "brands", "in": "query", "required": True,
                "schema": {"type": "string", "minLength": 1},
            })
        if path == "/insights/brands":
            result += [
                {"name": "brands", "in": "query", "required": True,
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "brand_set_version", "in": "query", "schema": {
                    "type": "string", "default": "exact-observed-domains-v1",
                }},
            ]
        if path == "/articles/{id}":
            result += [
                {"name": "fields", "in": "query",
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "version", "in": "query", "schema": {
                    "type": "string", "default": "serp-projection-1",
                }},
            ]
    if path in METRICS_ROUTES:
        result += [
            {"name": name, "in": "query",
             "required": name in {"source", "source_version"},
             "schema": {"type": "string", "minLength": 1}}
            for name in ("source", "source_version")
        ]
        if path == "/keyword-metrics":
            result += [
                {"name": "terms", "in": "query", "required": True,
                 "schema": {"type": "string", "minLength": 1}},
                {"name": "close_variant_group", "in": "query", "required": True,
                 "schema": {"type": "boolean"}},
                {"name": "geo", "in": "query", "required": True,
                 "schema": {"type": "string", "pattern": "^[A-Z]{2}$"}},
                {"name": "language", "in": "query",
                 "schema": {"type": "string", "default": "en"}},
                {"name": "network", "in": "query",
                 "schema": {"type": "string", "default": "google_search"}},
                {"name": "month", "in": "query", "required": True,
                 "schema": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}},
            ]
        elif path == "/trends/series":
            result += [
                {"name": name, "in": "query",
                 "schema": {"type": "string", "minLength": 1}}
                for name in ("term", "topic", "category", "timezone")
            ]
            result += [
                {"name": name, "in": "query",
                 "required": name in {"geo", "observed_from", "observed_to"},
                 "schema": (
                     {"type": "string", "format": "date-time"}
                     if name.startswith("observed_") else
                     {"type": "string", "minLength": 1}
                 )}
                for name in ("geo", "observed_from", "observed_to")
            ]
            result += [
                {"name": "granularity", "in": "query", "schema": {
                    "type": "string", "default": "day",
                    "enum": ["hour", "day", "week", "month"]}},
                {"name": "scale_policy", "in": "query", "schema": {
                    "type": "string", "default": "provider_native",
                    "enum": ["provider_native", "official_consistent"]}},
            ]
        else:
            result += [
                {"name": name, "in": "query",
                 "schema": {"type": "string", "minLength": 1}}
                for name in ("series", "query")
            ]
            result += [
                {"name": name, "in": "query",
                 "required": name in {"geo", "observed_from", "observed_to"},
                 "schema": (
                     {"type": "string", "format": "date-time"}
                     if name.startswith("observed_") else
                     {"type": "string", "minLength": 1}
                 )}
                for name in ("geo", "observed_from", "observed_to")
            ]
            result += [
                {"name": "mode", "in": "query", "schema": {
                    "type": "string", "default": "top",
                    "enum": ["top", "rising"]}},
                {"name": "limit", "in": "query", "schema": {
                    "type": "integer", "default": 20, "minimum": 1,
                    "maximum": 100}},
            ]
    if path in SEARCH_CONSOLE_ROUTES:
        result += [
            {"name": name, "in": "query", "required": True,
             "schema": {"type": "string", "minLength": 1}}
            for name in ("source", "source_version", "asset")
        ]
        if path == "/search-console/reports":
            result += [
                {"name": name, "in": "query", "required": True,
                 "schema": {"type": "string", "minLength": 1}}
                for name in (
                    "template", "period_start", "period_end", "dimensions",
                )
            ]
            result += [
                {"name": "data_state", "in": "query", "required": True,
                 "schema": {"type": "string",
                            "enum": ["final", "all", "hourly_all"]}},
                {"name": "aggregation", "in": "query", "required": True,
                 "schema": {"type": "string",
                            "enum": ["auto", "byPage", "byProperty",
                                     "byNewsShowcasePanel"]}},
                {"name": "report_kind", "in": "query",
                 "schema": {"type": "string", "default": "top_rows",
                            "enum": ["top_rows", "bulk"]}},
                {"name": "filters", "in": "query",
                 "description": "JSON object of explicit Search Console dimensions.",
                 "schema": {"type": "string", "maxLength": 16384}},
                {"name": "partition", "in": "query",
                 "schema": {"type": "string", "maxLength": 1024}},
            ]
        else:
            result.append({
                "name": "url", "in": "query", "required": True,
                "schema": {"type": "string", "format": "uri"},
            })
    if path in AUTHORIZED_ANALYTICS_ROUTES:
        result += [
            {"name": name, "in": "query", "required": True,
             "schema": {"type": "string", "minLength": 1}}
            for name in (
                "source", "source_version", "asset", "template",
                "period_start", "period_end", "dimensions", "metrics",
            )
        ]
        if path == "/analytics/reports":
            result += [{"$ref": "#/components/parameters/Limit"},
                       {"$ref": "#/components/parameters/Cursor"}]
        else:
            result += [
                {"name": name, "in": "query", "required": True,
                 "schema": {"type": "string", "maxLength": 16384}}
                for name in ("api_scope", "bigquery_scope")
            ]
    if path in ADS_ROUTES:
        result += [
            {"name": name, "in": "query", "required": True,
             "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
            for name in (
                "source", "source_version", "access_id", "template_version",
                "customer_id", "period_start", "period_end", "source_timezone",
                "network", "geo", "language", "currency", "attribution_version",
            )
        ]
        result.append({
            "name": "mode", "in": "query", "required": True,
            "schema": {"type": "string", "enum": ["internal_analysis", "report_service"]},
        })
    if path in MERCHANT_ROUTES:
        result += [
            {"name": name, "in": "query", "required": True,
             "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
            for name in (
                "source", "source_version", "asset", "access_id", "merchant_id",
                "template_version", "report_version", "period_start",
                "period_end", "source_timezone", "country", "currency",
            )
        ]
        result += [{"$ref": "#/components/parameters/Limit"},
                   {"$ref": "#/components/parameters/Cursor"}]
    if path in LOCAL_ROUTES:
        result += [
            {"name": name, "in": "query", "required": True,
             "schema": {"type": "string", "minLength": 1, "maxLength": 4096}}
            for name in ("source", "source_version", "asset")
        ]
        if path == "/local/review-insights":
            result.append({
                "name": "place_id", "in": "query", "required": True,
                "schema": {"type": "string", "minLength": 1},
            })
        if path.startswith("/local/grids"):
            result += [{"$ref": "#/components/parameters/Limit"},
                       {"$ref": "#/components/parameters/Cursor"}]
    if path == "/quality":
        result += [
            {"name": name, "in": "query",
             "schema": (
                 {"type": "string", "format": "date-time"}
                 if name.startswith("observed_") else
                 {"type": "string", "minLength": 1}
             )}
            for name in sorted(QUALITY_PARAMETERS)
        ]
    if method == "POST":
        result += [{"name": "Idempotency-Key", "in": "header", "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 120}}]
    if method == "PATCH":
        result += [{"name": "If-Match", "in": "header", "required": True,
                    "schema": {"type": "string", "minLength": 1}}]
    return result


def openapi():
    schemas = {
        "MetricValue": {
            "type": "object",
            "required": ["value", "unit", "provenance_kind", "measurement_nature",
                         "availability", "metric_scope", "definition_version", "interval"],
            "properties": {
                "value": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
                "unit": {"type": "string", "minLength": 1},
                "provenance_kind": {"type": "string", "enum": sorted(PROVENANCE)},
                "measurement_nature": {"type": "string", "enum": sorted(NATURES)},
                "availability": {"type": "string", "enum": sorted(AVAILABILITY)},
                "metric_scope": {"type": "object", "minProperties": 1},
                "definition_version": {"type": "string", "minLength": 1},
                "interval": {"anyOf": [{"type": "null"}, {
                    "type": "object", "required": ["lower", "upper", "level", "method", "calibration_population"],
                    "properties": {"lower": {"type": ["number", "string"]},
                                   "upper": {"type": ["number", "string"]},
                                   "level": {"type": ["number", "string"]},
                                   "method": {"type": "string"},
                                   "calibration_population": {"type": "string"}},
                }]},
                "quality_flags": {"type": "array", "items": {"type": "string"}},
                "evidence_expires_at": {"type": ["string", "null"], "format": "date-time"},
            },
        },
        "Error": {
            "type": "object", "required": ["code", "message", "retryable", "source"],
            "properties": {"code": {"type": "string", "enum": list(ERROR_CODES)},
                           "message": {"type": "string"}, "retryable": {"type": "boolean"},
                           "source": {"type": ["string", "null"]}},
        },
        "Envelope": {
            "type": "object", "required": ["data", "meta", "errors"],
            "properties": {
                "data": {},
                "meta": {
                    "type": "object", "required": ["request_id", "query_id", "data_as_of",
                        "source_observed_at", "generated_at", "quality", "coverage", "versions",
                        "input_manifest_id", "non_reproducible_reason", "rights_expiry"],
                },
                "errors": {"type": "array", "items": {"$ref": "#/components/schemas/Error"}},
            },
        },
        "JobProgress": {
            "type": "object",
            "required": ["rows_done", "percent"],
            "properties": {
                "rows_done": {"type": "integer", "minimum": 0},
                "rows_total": {"type": ["integer", "null"], "minimum": 0},
                "percent": {"type": "number", "minimum": 0, "maximum": 100},
            },
        },
        "AnalysisJobRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["manifest_id", "analysis_type"],
            "properties": {
                "manifest_id": {"type": "string", "minLength": 1},
                "analysis_type": {
                    "type": "string", "enum": ["item-coverage", "rank-summary"]},
            },
        },
        "ReportRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["manifest_id", "report_type"],
            "properties": {
                "manifest_id": {"type": "string", "minLength": 1},
                "report_type": {"type": "string", "enum": ["deterministic"]},
                "research_question": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
        },
        "WatchlistItem": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entity_id", "entity_type"],
            "properties": {
                "entity_id": {"type": "string", "minLength": 1, "maxLength": 4096},
                "entity_type": {
                    "type": "string",
                    "enum": ["query", "run", "item", "feature", "url", "domain",
                             "brand", "topic", "term", "report"],
                },
                "label": {"type": "string", "minLength": 1, "maxLength": 500},
                "metadata": {"type": "object"},
            },
        },
        "WatchlistRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 500},
                "items": {
                    "type": "array", "maxItems": 1000,
                    "items": {"$ref": "#/components/schemas/WatchlistItem"},
                },
            },
        },
        "MonitorConfig": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 500},
                "queries": {
                    "type": "array", "maxItems": 1000,
                    "items": {"type": "string", "minLength": 1},
                },
                "prompts": {
                    "type": "array", "maxItems": 1000,
                    "items": {"type": "string", "minLength": 1},
                },
                "domains": {
                    "type": "array", "maxItems": 1000,
                    "items": {"type": "string", "minLength": 1},
                },
                "pools": {
                    "type": "array", "maxItems": 1000,
                    "items": {"type": "string", "minLength": 1},
                },
                "source_adapter": {"type": "string", "minLength": 1},
                "source_version": {"type": "string", "minLength": 1},
                "frequency_seconds": {
                    "type": "integer", "minimum": 300, "maximum": 2592000,
                },
                "budget": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["monthly_limit", "currency"],
                    "properties": {
                        "monthly_limit": {"type": "string"},
                        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    },
                },
                "rules": {"type": "object"},
                "enabled": {
                    "type": "boolean", "default": False,
                    "description": "Configuration only. true is closed until a scheduler is connected.",
                },
            },
        },
        "MonitorConfigRequest": {
            "allOf": [
                {"$ref": "#/components/schemas/MonitorConfig"},
                {"required": [
                    "name", "source_adapter", "source_version",
                    "frequency_seconds", "budget",
                ]},
            ],
        },
        "MonitorPatchRequest": {
            "allOf": [{"$ref": "#/components/schemas/MonitorConfig"}],
        },
        "LiveQueryRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["source", "source_version", "query", "variant", "max_units"],
            "properties": {
                "source": {"type": "string", "minLength": 1},
                "source_version": {"type": "string", "minLength": 1},
                "query": {"type": "string", "minLength": 1, "maxLength": 4096},
                "variant": {
                    "type": "object",
                    "required": [
                        "country", "language", "location", "device",
                        "search_engine", "surface", "channel", "login_state",
                        "safe_search", "depth", "parameters_version",
                    ],
                },
                "max_units": {
                    "type": "integer", "minimum": 1, "maximum": 100,
                },
            },
        },
        "SubscriptionRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "target_ref", "event_types", "filters"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 500},
                "target_ref": {"type": "string", "minLength": 1, "maxLength": 4096},
                "event_types": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "string", "enum": [
                        "rank_change", "new_url", "news_event",
                        "ai_citation_change",
                    ]},
                },
                "filters": {"type": "object"},
                "active": {
                    "type": "boolean", "default": False,
                    "description": "Delivery is closed until an approved target and signing connector exist.",
                },
            },
        },
        "ExportRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["source_path", "filters", "fields", "format"],
            "properties": {
                "source_path": {
                    "type": "string",
                    "enum": ["/keywords", "/query-variants", "/observations",
                             "/observations/{id}/items", "/serp-features"],
                },
                "filters": {"type": "object"},
                "fields": {
                    "type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "format": {"type": "string", "enum": ["jsonl", "csv"]},
                "async": {
                    "type": "boolean", "default": False,
                    "description": "Force queued processing. Scopes over 100 rows are queued automatically.",
                },
            },
        },
    }
    result = {
        "openapi": "3.1.0", "info": {"title": "SearchFlow Google Domain API", "version": "1.0.0",
            "description": "Tenant/asset-scoped Google domain contract. Planned operations are closed with not_covered."},
        "servers": [{"url": "/"}], "x-prd-source-sha256": SOURCE_SHA256,
        "x-contract-status": "implementation_in_progress_not_approved_frozen_contract",
        "paths": {}, "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"},
                               "siteAccess": {"type": "apiKey", "in": "header", "name": "X-SearchFlow-Access-Key"}},
            "parameters": {"Limit": {"name": "limit", "in": "query", "schema": {
                "type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
                "Cursor": {"name": "cursor", "in": "query", "schema": {"type": "string", "maxLength": 8192}}},
            "schemas": schemas,
        },
    }
    for group in API_CATALOG:
        for op in group["operations"]:
            path, method = op["path"], op["method"]
            status = ("metadata" if method == "GET" and path in METADATA_ROUTES
                      and path not in {"/usage", "/quality"} else
                      "local_usage" if method == "GET" and path == "/usage" else
                      "local_quality" if method == "GET" and path == "/quality" else
                      "local_projection" if method == "GET" and path in PROJECTION_ROUTES else
                      "local_domain_analytics" if method == "GET" and path in ANALYTICS_ROUTES else
                      "local_evidence" if method == "GET" and path in EVIDENCE_ROUTES else
                      "local_metrics_source" if method == "GET" and path in METRICS_ROUTES else
                      "local_search_console" if method == "GET" and path in SEARCH_CONSOLE_ROUTES else
                      "local_authorized_analytics" if method == "GET" and path in AUTHORIZED_ANALYTICS_ROUTES else
                      "local_managed_ads" if method == "GET" and path in ADS_ROUTES else
                      "local_merchant" if method == "GET" and path in MERCHANT_ROUTES else
                      "local_conditional_local" if method == "GET" and path in LOCAL_ROUTES else
                      "local_live_query_list" if method == "GET" and path == "/live-queries" else
                      "local_live_query_detail" if method == "GET" and path == "/live-queries/{id}" else
                      "local_live_query_create" if method == "POST" and path == "/live-queries" else
                      "local_subscription_list" if method == "GET" and path == "/subscriptions" else
                      "local_subscription_detail" if method == "GET" and path == "/subscriptions/{id}" else
                      "local_subscription_create" if method == "POST" and path == "/subscriptions" else
                      "local_subscription_delete" if method == "DELETE" and path == "/subscriptions/{id}" else
                      "local_export" if method == "GET" and path in EXPORT_ROUTES else
                      "local_export_create" if method == "POST" and path == "/exports" else
                      "local_job_foundation" if method == "GET" and path in ANALYSIS_ROUTES else
                      "local_job_create" if method == "POST" and path == "/analysis-jobs" else
                      "local_report" if method == "GET" and path == "/reports/{id}" else
                      "local_report_list" if method == "GET" and path == "/reports" else
                      "local_report_create" if method == "POST" and path == "/reports" else
                      "local_watchlist_list" if method == "GET" and path == "/watchlists" else
                      "local_watchlist_create" if method == "POST" and path == "/watchlists" else
                      "local_watchlist_remove" if method == "DELETE" and path == "/watchlists/{id}/items/{entity_id}" else
                      "local_monitor_list" if method == "GET" and path == "/monitors" else
                      "local_monitor_detail" if method == "GET" and path == "/monitors/{id}" else
                      "local_monitor_create" if method == "POST" and path == "/monitors" else
                      "local_monitor_patch" if method == "PATCH" and path == "/monitors/{id}" else "planned")
            operation = {
                "operationId": group["id"].lower().replace("-", "_") + "_" + method.lower() + "_" +
                               re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_"),
                "summary": group["description"], "x-requirement-id": group["id"],
                "x-phase": group["phase"], "x-implementation-status": status,
                "security": [{"bearerAuth": [], "siteAccess": []}],
                "parameters": _parameters(path, method),
                "responses": {
                    str(code): {"description": description, "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Envelope"}}}}
                    for code, description in [(200, "Implemented response" if status != "planned" else "Target response, not implemented"),
                        (400, "Invalid argument"), (401, "Authentication required"), (403, "Use/asset not permitted"),
                        (404, "Not found/not covered"), (409, "Scope/idempotency conflict"),
                        (410, "Expired"), (412, "Revision changed"), (429, "Rate/concurrency/quota exceeded"),
                        (413, "Cohort exceeds the verified-row cap"),
                        (501, "Planned or not covered"), (503, "Disabled or temporarily unavailable")]
                },
            }
            if method in {"POST", "PATCH"}:
                request_schema = (
                    {"$ref": "#/components/schemas/ExportRequest"}
                    if method == "POST" and path == "/exports" else
                    {"$ref": "#/components/schemas/AnalysisJobRequest"}
                    if method == "POST" and path == "/analysis-jobs" else
                    {"$ref": "#/components/schemas/ReportRequest"}
                    if method == "POST" and path == "/reports" else
                    {"$ref": "#/components/schemas/WatchlistRequest"}
                    if method == "POST" and path == "/watchlists" else
                    {"$ref": "#/components/schemas/MonitorConfigRequest"}
                    if method == "POST" and path == "/monitors" else
                    {"$ref": "#/components/schemas/LiveQueryRequest"}
                    if method == "POST" and path == "/live-queries" else
                    {"$ref": "#/components/schemas/SubscriptionRequest"}
                    if method == "POST" and path == "/subscriptions" else
                    {"type": "object", "description":
                     "Planned domain request; operation remains closed until its schema and admission gates are implemented."})
                operation["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": request_schema}}}
            if method == "PATCH" and path == "/monitors/{id}":
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/MonitorPatchRequest"},
                    }},
                }
            if method == "GET" and path in PROJECTION_ROUTES:
                operation["x-local-projection-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved independent Whale version reader and dataset",
                    "current source/field/use/asset rights", "rebuildable selected-fact projection",
                ]
            if method == "GET" and path in ANALYTICS_ROUTES:
                operation["x-local-analytics-implemented"] = True
                operation["x-connection-requirements"] = operation.get(
                    "x-connection-requirements", [
                        "approved independent Whale version reader and dataset",
                        "current source/field/use/asset rights",
                        "rebuildable selected-fact projection",
                    ])
            if method == "GET" and path in METRICS_ROUTES:
                operation["x-local-metrics-source-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved metrics/Trends source adapter and API version",
                    "current source/field/use/asset rights",
                    "typed series with explicit scale, timezone and revisions",
                ]
            if method == "GET" and path in SEARCH_CONSOLE_ROUTES:
                operation["x-local-search-console-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved Search Console source/API version and property asset",
                    "current source/field/use/asset rights",
                    "typed report or URL-inspection evidence with source timezone and expiry",
                ]
            if method == "GET" and path in AUTHORIZED_ANALYTICS_ROUTES:
                operation["x-local-authorized-analytics-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved GA4 source/API version and property asset",
                    "current source/field/use/asset rights",
                    "typed report metadata, property quota and timezone",
                    "explicit API/BigQuery scope compatibility",
                ]
            if method == "GET" and path in ADS_ROUTES:
                operation["x-local-managed-ads-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved managed-output adapter, source/API version and dataset",
                    "current tenant/caller/business/project/OAuth/customer policy",
                    "Basic or Standard access and explicit combined-SERP permissible use",
                    "approved plan/template and exact output scope; no raw GAQL proxy",
                    "current asset/field/use and retention rights",
                ]
            if method == "GET" and path in MERCHANT_ROUTES:
                operation["x-local-merchant-implemented"] = True
                operation["x-connection-requirements"] = [
                    "approved read-only Merchant API source, method and report version",
                    "current merchant asset/field/use rights",
                    "typed report evidence with product/date/country/currency scope",
                    "no retired Content API compatibility or product writes",
                ]
            if method == "GET" and path in LOCAL_ROUTES:
                operation["x-local-local-implemented"] = True
                operation["x-connection-requirements"] = [
                    "independently approved Local/Places/review adapter and source",
                    "current location asset/field/use and retention rights",
                    "typed grid/observation/reference/insight evidence",
                    "no unlicensed map tile, raw review archive or full business profile",
                ]
            result["paths"].setdefault(PREFIX + path, {})[method.lower()] = operation
    local_extensions = {
        "/v1/google/analysis-jobs/{id}/cancel": (
            "GO-API-040", "local_job_cancel", "analysis_job_cancel"),
        "/v1/google/exports/{id}/cancel": (
            "GO-API-041", "local_export_cancel", "export_cancel"),
        "/v1/google/live-queries/{id}/cancel": (
            "GO-API-042", "local_live_query_cancel", "live_query_cancel"),
    }
    for path, (requirement, status, operation_id) in local_extensions.items():
        result["paths"][path] = {"post": {
            "operationId": operation_id,
            "summary": "Cancel a queued job; running work observes cancellation before its next chunk.",
            "x-requirement-id": requirement,
            "x-phase": "P0",
            "x-implementation-status": status,
            "x-local-extension": True,
            "security": [{"bearerAuth": [], "siteAccess": []}],
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "string", "minLength": 1}}],
            "requestBody": {"required": False, "content": {"application/json": {
                "schema": {"type": "object", "additionalProperties": False}}}},
            "responses": {
                str(code): {"description": description, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Envelope"}}}}
                for code, description in [
                    (200, "Cancellation recorded"), (400, "Invalid request"),
                    (401, "Authentication required"), (403, "Use/asset not permitted"),
                    (404, "Job not found"), (409, "Job is already terminal"),
                    (410, "Job expired"), (429, "Rate/concurrency/quota exceeded"),
                    (501, "Service not connected"), (503, "Temporarily unavailable"),
                ]
            },
        }}
    return result


LIMIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_api_windows (
 tenant_id text NOT NULL, second_at timestamptz NOT NULL, requests integer NOT NULL,
 PRIMARY KEY(tenant_id,second_at)
);
CREATE TABLE IF NOT EXISTS sf_google_api_leases (
 lease_id text PRIMARY KEY, tenant_id text NOT NULL, expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sf_google_api_leases_tenant ON sf_google_api_leases(tenant_id,expires_at);
CREATE TABLE IF NOT EXISTS sf_google_api_settings (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), cursor_secret bytea NOT NULL
);
"""


class ApiReadLimits:
    def __init__(self, dsn):
        self.registry = RightsRegistry(dsn)

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,4)")
            db.execute(LIMIT_SCHEMA)
            db.execute("INSERT INTO sf_google_api_settings(id,cursor_secret) VALUES(true,%s) ON CONFLICT DO NOTHING",
                       (secrets.token_bytes(32),))

    def cursor_codec(self):
        with self.registry.connect() as db:
            row = db.execute("SELECT cursor_secret FROM sf_google_api_settings WHERE id=true").fetchone()
        if not row:
            raise DomainError("not_covered", "Domain cursor signing is not initialized", 503)
        return CursorCodec(bytes(row["cursor_secret"]))

    @contextmanager
    def hold(self, tenant_id, *, now):
        current, lease = parse_time(now), str(uuid4())
        second = current.replace(microsecond=0)
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,20261104))", (tenant_id,))
            db.execute("DELETE FROM sf_google_api_leases WHERE tenant_id=%s AND expires_at<=%s",
                       (tenant_id, current))
            db.execute("DELETE FROM sf_google_api_windows WHERE tenant_id=%s AND second_at<%s",
                       (tenant_id, current - timedelta(minutes=1)))
            count = db.execute("SELECT count(*) n FROM sf_google_api_leases WHERE tenant_id=%s",
                               (tenant_id,)).fetchone()["n"]
            row = db.execute("SELECT requests FROM sf_google_api_windows WHERE tenant_id=%s AND second_at=%s",
                             (tenant_id, second)).fetchone()
            if count >= 2 or row and row["requests"] >= 5:
                raise DomainError("quota_exceeded", "Tenant read limit is 5 requests/second and 2 concurrent", 429)
            db.execute("""
                INSERT INTO sf_google_api_windows(tenant_id,second_at,requests) VALUES(%s,%s,1)
                ON CONFLICT(tenant_id,second_at) DO UPDATE SET requests=sf_google_api_windows.requests+1
            """, (tenant_id, second))
            db.execute("INSERT INTO sf_google_api_leases VALUES(%s,%s,%s)",
                       (lease, tenant_id, current + timedelta(seconds=30)))
        try:
            yield
        finally:
            with self.registry.connect() as db:
                db.execute("DELETE FROM sf_google_api_leases WHERE lease_id=%s AND tenant_id=%s",
                           (lease, tenant_id))


class GoogleDomainService:
    def __init__(
        self, dsn, *, projection=None, exports=None, jobs=None,
        reports=None, watchlists=None, monitors=None, analytics=None,
        metrics=None, search_console=None, authorized_analytics=None,
        ads=None, merchant=None, local=None, quality=None, live_queries=None,
        subscriptions=None,
    ):
        self.registry = RightsRegistry(dsn)
        self.limits = ApiReadLimits(dsn)
        self.projection = projection
        self.exports = exports
        self.jobs = jobs
        self.reports = reports
        self.watchlists = watchlists
        self.monitors = monitors
        self.analytics = analytics
        self.metrics = metrics
        self.search_console = search_console
        self.authorized_analytics = authorized_analytics
        self.ads = ads
        self.merchant = merchant
        self.local = local
        self.quality = quality
        self.live_queries = live_queries
        self.subscriptions = subscriptions or SubscriptionService(
            dsn,
            registry=self.registry,
            cursor_codec=None,
            clock=lambda: datetime.now(timezone.utc),
        )

    def initialize(self):
        self.registry.initialize()
        self.limits.initialize()
        if self.exports is not None:
            self.exports.initialize()
        if self.jobs is not None:
            self.jobs.initialize()
        if self.reports is not None:
            self.reports.initialize()
        if self.watchlists is not None:
            self.watchlists.initialize()
            if getattr(self.watchlists, "cursor_codec", None) is None:
                self.watchlists.cursor_codec = self.limits.cursor_codec()
        if self.monitors is not None:
            self.monitors.initialize()
            if getattr(self.monitors, "cursor_codec", None) is None:
                self.monitors.cursor_codec = self.limits.cursor_codec()
        if self.subscriptions is not None:
            self.subscriptions.initialize()
            if getattr(self.subscriptions, "cursor_codec", None) is None:
                self.subscriptions.cursor_codec = self.limits.cursor_codec()
        if self.live_queries is not None:
            self.live_queries.initialize()
            if getattr(self.live_queries, "cursor_codec", None) is None:
                self.live_queries.cursor_codec = self.limits.cursor_codec()

    def _connections(self, principal):
        rows = [self.registry.grant(principal.tenant_id, gid) for gid in sorted(principal.grant_ids)]
        return [dict(asset_id=g.asset_id, grant_id=g.grant_id, grant_version=g.version,
                     source_adapter=g.source_adapter, source_version=g.source_version,
                     fields=sorted(g.fields), allowed_uses=sorted(g.uses),
                     expires_at=g.expires_at.isoformat(), connection_state="registered_not_connected") for g in rows]

    def rights_expiry(self, principal):
        deadlines = []
        for gid in principal.grant_ids:
            grant = self.registry.grant(principal.tenant_id, gid)
            deadlines.append(grant.expires_at)
            source = self.registry.source(principal.tenant_id, grant.source_adapter, grant.source_version)
            deadlines.extend(r.retention_deadline for r in source.field_rights
                             if r.field in grant.fields and r.retention_deadline is not None)
        return min(deadlines) if deadlines else None

    def _usage(self, principal, *, now):
        """Local control-plane usage only; never invents provider units or fees."""
        self.registry.assert_current(principal, now=now)
        tables = {
            "analysis_jobs": "sf_google_analysis_jobs",
            "exports": "sf_google_exports",
            "reports": "sf_google_reports",
            "monitors": "sf_google_monitors",
            "watchlists": "sf_google_watchlists",
            "live_queries": "sf_google_live_queries",
            "subscriptions": "sf_google_subscriptions",
        }
        usage = {}
        with self.registry.connect() as db:
            for label, table in tables.items():
                exists = db.execute(
                    "SELECT to_regclass(%s) AS relation", (table,)
                ).fetchone()["relation"]
                if not exists:
                    usage[label] = {"state": "not_initialized", "count": None}
                    continue
                count = db.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=%s",
                    (principal.tenant_id,),
                ).fetchone()["n"]
                usage[label] = {"state": "local_table", "count": count}
            if db.execute("SELECT to_regclass('sf_google_analysis_jobs') AS relation").fetchone()["relation"]:
                usage["analysis_jobs"]["by_status"] = {
                    row["status"]: row["n"] for row in db.execute(
                        """SELECT status,count(*) AS n FROM sf_google_analysis_jobs
                           WHERE tenant_id=%s GROUP BY status""",
                        (principal.tenant_id,),
                    ).fetchall()
                }
            if db.execute("SELECT to_regclass('sf_google_exports') AS relation").fetchone()["relation"]:
                usage["exports"]["by_status"] = {
                    row["status"]: row["n"] for row in db.execute(
                        """SELECT status,count(*) AS n FROM sf_google_exports
                           WHERE tenant_id=%s GROUP BY status""",
                        (principal.tenant_id,),
                    ).fetchall()
                }
            if db.execute("SELECT to_regclass('sf_google_monitors') AS relation").fetchone()["relation"]:
                usage["monitors"]["enabled"] = db.execute(
                    """SELECT count(*) AS n FROM sf_google_monitors
                       WHERE tenant_id=%s AND enabled""",
                    (principal.tenant_id,),
                ).fetchone()["n"]
            if db.execute("SELECT to_regclass('sf_google_live_queries') AS relation").fetchone()["relation"]:
                usage["live_queries"]["by_status"] = {
                    row["status"]: row["n"] for row in db.execute(
                        """SELECT status,count(*) AS n
                           FROM sf_google_live_queries
                           WHERE tenant_id=%s GROUP BY status""",
                        (principal.tenant_id,),
                    ).fetchall()
                }
            if db.execute("SELECT to_regclass('sf_google_subscriptions') AS relation").fetchone()["relation"]:
                usage["subscriptions"]["active"] = db.execute(
                    """SELECT count(*) AS n FROM sf_google_subscriptions
                       WHERE tenant_id=%s AND active
                         AND deleted_at IS NULL""",
                    (principal.tenant_id,),
                ).fetchone()["n"]
            leases = db.execute(
                "SELECT count(*) AS n FROM sf_google_api_leases WHERE tenant_id=%s AND expires_at>%s",
                (principal.tenant_id, parse_time(now)),
            ).fetchone()["n"]
            requests = db.execute(
                """SELECT coalesce(sum(requests),0) AS n
                   FROM sf_google_api_windows WHERE tenant_id=%s AND second_at>=%s""",
                (principal.tenant_id, parse_time(now) - timedelta(minutes=1)),
            ).fetchone()["n"]
        return {
            "scope": "local_control_plane_usage",
            "as_of": now.isoformat().replace("+00:00", "Z"),
            "local": usage,
            "api_limits": {
                "reads_per_second": 5,
                "concurrent_reads": 2,
                "observed_active_leases": leases,
                "observed_requests_last_minute": requests,
            },
            "provider_usage": {
                "status": "not_covered",
                "reason": "billable_source_units_are_not_connected",
            },
            "fees": {
                "status": "not_covered",
                "reason": "no_fee_ledger_or_currency_settlement_is_connected",
            },
            "not_claimed": [
                "provider_budget",
                "source_quota",
                "invoice_reconciliation",
                "cost_per_task_or_page",
            ],
        }

    def _page(self, rows, *, principal, parameters, now, source, key):
        limit = page_limit(parameters.get("limit"))
        binding = dict(tenant_id=principal.tenant_id, rights_version=principal.rights_version,
                       filters={k: v for k, v in parameters.items() if k != "cursor"},
                       sort=[key], source=source, manifest_id=None)
        # Bind the effective page size even when omitted by the caller.
        binding["filters"]["limit"] = limit
        codec = self.limits.cursor_codec()
        after = None
        if "cursor" in parameters:
            position = codec.decode(parameters["cursor"], binding=binding, now=now)
            if len(position) != 1 or not isinstance(position[0], str):
                raise DomainError("invalid_argument", "Invalid metadata cursor position")
            after = position[0]
        ordered = sorted((r for r in rows if after is None or r[key] > after), key=lambda r: r[key])
        page, more = ordered[:limit], len(ordered) > limit
        cursor = (codec.encode(binding=binding, position=[page[-1][key]],
                               expires_at=parse_time(now) + timedelta(minutes=10), now=now)
                  if more else None)
        return page, cursor

    def read(self, principal, path, parameters, *, now):
        self.registry.assert_current(principal, now=now)
        allowed = {
            "/capabilities": set(), "/local/capabilities": set(),
            "/markets": {"source", "source_version"}, "/locations": {"source", "source_version", "country"},
            "/metric-definitions": {"limit", "cursor", "metric_id"},
            "/connections": {"limit", "cursor"},
            "/quality": set(QUALITY_PARAMETERS), "/usage": set(),
        }
        if path not in METADATA_ROUTES:
            evidence_match = re.fullmatch(r"/evidence/([^/]+)", path)
            if evidence_match:
                if self.projection is None:
                    raise DomainError(
                        "not_covered", "Evidence projection is not connected", 501)
                return self.projection.evidence(
                    principal,
                    unquote(evidence_match.group(1), errors="strict"),
                    now=now,
                )
            if self.reports is not None:
                try:
                    kind, report_id = self.reports.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.reports.list(
                            principal, parameters, now=now)
                    if kind == "detail":
                        return self.reports.detail(principal, report_id, now=now)
            if self.watchlists is not None:
                try:
                    kind, identifier = self.watchlists.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.watchlists.list(principal, parameters, now=now)
                    if kind == "detail":
                        return self.watchlists.detail(principal, identifier, now=now)
            if self.monitors is not None:
                try:
                    kind, monitor_id = self.monitors.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.monitors.list(principal, parameters, now=now)
                    if kind == "detail":
                        return self.monitors.detail(principal, monitor_id, now=now)
            if self.subscriptions is not None:
                try:
                    kind, subscription_id = self.subscriptions.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.subscriptions.list(
                            principal, parameters, now=now)
                    if kind == "detail":
                        return self.subscriptions.detail(
                            principal, subscription_id, now=now)
            if self.analytics is not None:
                try:
                    self.analytics.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.analytics.read(
                        principal, path, parameters, now=now)
            if self.metrics is not None:
                try:
                    self.metrics.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.metrics.read(
                        principal, path, parameters, now=now)
            if self.search_console is not None:
                try:
                    self.search_console.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.search_console.read(
                        principal, path, parameters, now=now)
            if self.authorized_analytics is not None:
                try:
                    self.authorized_analytics.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.authorized_analytics.read(
                        principal, path, parameters, now=now)
            if self.ads is not None and path in ADS_ROUTES:
                return self.ads.read(principal, path, parameters, now=now)
            if self.merchant is not None and path in MERCHANT_ROUTES:
                return self.merchant.read(principal, path, parameters, now=now)
            if self.local is not None:
                try:
                    self.local.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.local.read(principal, path, parameters, now=now)
            if self.live_queries is not None:
                try:
                    kind, live_id = self.live_queries.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.live_queries.list(
                            principal, parameters, now=now)
                    if kind == "detail":
                        return self.live_queries.detail(
                            principal, live_id, now=now)
            if self.jobs is not None:
                try:
                    kind, job_id = self.jobs.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.jobs.list(principal, parameters, now=now)
                    if kind == "detail":
                        return self.jobs.detail(principal, job_id, now=now)
            if self.exports is not None:
                try:
                    kind, export_id = self.exports.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    if kind == "list":
                        return self.exports.list(principal, parameters, now=now)
                    if kind == "detail":
                        return self.exports.detail(principal, export_id, now=now)
                    if kind == "content":
                        raw, content_type, filename = self.exports.content(principal, export_id, now=now)
                        return {"content": raw, "content_type": content_type, "filename": filename}
            if self.projection is not None:
                try:
                    self.projection.route(path)
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                else:
                    return self.projection.read(principal, path, parameters, now=now)
            if path == "/usage" and "manager" not in principal.roles:
                raise DomainError("not_authorized", "This endpoint requires the management role", 403)
            known = [op for group in API_CATALOG for op in group["operations"]
                     if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", op["path"]), path)]
            if known:
                raise DomainError("not_covered", "No qualified domain projection/adapter is connected for this operation", 501)
            raise DomainError("not_found", "Domain endpoint does not exist", 404)
        if set(parameters) - allowed[path]:
            raise DomainError("invalid_argument", "Unknown query parameter for this operation")
        if "country" in parameters and not re.fullmatch("[A-Z]{2}", parameters["country"]):
            raise DomainError("invalid_argument", "country must be a two-letter uppercase code")
        if "metric_id" in parameters and not re.fullmatch("GO-MET-[0-9]{3}", parameters["metric_id"]):
            raise DomainError("invalid_argument", "Invalid metric ID")
        if path in {"/capabilities", "/local/capabilities"}:
            if path == "/local/capabilities":
                return {"status": "not_covered", "collection_enabled": False,
                        "storage": "not_approved", "reviews": "not_covered", "reason": "local_source_not_admitted"}
            grants = self._connections(principal)
            permitted = {(g["source_adapter"], g["source_version"]) for g in grants}
            sources = [dict(s, adapter_connected=False)
                       for s in self.registry.sources(principal.tenant_id, now=now)
                       if (s["source_adapter"], s["source_version"]) in permitted]
            for source in sources:
                matching = [g for g in grants if (g["source_adapter"], g["source_version"]) ==
                            (source["source_adapter"], source["source_version"])]
                visible = []
                for right in source["fields"]:
                    # Capability disclosure and later admission use the same
                    # source rights object; a field absent here can never pass
                    # authorize() later.
                    scoped = [g for g in matching if right["field"] in g["fields"]]
                    uses = set().union(*(set(g["allowed_uses"]) for g in scoped)) if scoped else set()
                    uses.intersection_update(right["allowed_uses"])
                    if not uses:
                        continue
                    deadlines = [parse_time(g["expires_at"]) for g in scoped]
                    if right["retention_deadline"] is not None:
                        deadlines.append(parse_time(right["retention_deadline"]))
                    visible.append(dict(right, allowed_uses=sorted(uses),
                                        retention_deadline=min(deadlines).isoformat()))
                source["fields"] = visible
            return {"sources": sources, "live_enabled": False,
                    "domains": [dict(id=d, status="not_covered", collection_enabled=False) for d in
                                ("serp", "trends", "news", "aio", "ai_mode", "gsc", "ga4", "ads", "merchant", "local")],
                    "contract_status": "implementation_in_progress"}
        if path == "/usage":
            if "manager" not in principal.roles:
                raise DomainError(
                    "not_authorized", "This endpoint requires the management role", 403)
            return self._usage(principal, now=now)
        if path == "/quality":
            if "manager" not in principal.roles:
                raise DomainError(
                    "not_authorized", "This endpoint requires the management role", 403)
            if self.quality is not None:
                return self.quality.read(
                    principal, parameters, now=now)
            sources = self.registry.sources(principal.tenant_id, now=now)
            return {
                "registry_entries": len(sources),
                "unit": "registered_source_versions",
                "sources": sources,
                "observation_quality": {
                    "availability": "not_covered",
                    "reason": "operational_ledger_not_connected",
                },
            }
        if path == "/metric-definitions":
            rows = copy.deepcopy(METRIC_CATALOG)
            if "metric_id" in parameters:
                rows = [r for r in rows if r["id"] == parameters["metric_id"]]
            rows, cursor = self._page(rows, principal=principal, parameters=parameters, now=now,
                                      source="metric-catalog:1", key="id")
            return {"definitions": rows, "next_cursor": cursor, "measurement_status": "definitions_only"}
        if path == "/connections":
            rows, cursor = self._page(self._connections(principal), principal=principal,
                                      parameters=parameters, now=now, source="asset-registry:1", key="grant_id")
            return {"connections": rows, "next_cursor": cursor}
        source_id, version = parameters.get("source"), parameters.get("source_version")
        if not source_id or not version:
            raise DomainError("invalid_argument", "source and source_version are required")
        source = self.registry.source(principal.tenant_id, source_id, version)
        grants = [self.registry.grant(principal.tenant_id, gid) for gid in principal.grant_ids]
        if not any(g.source_adapter == source_id and g.source_version == version for g in grants):
            raise DomainError("not_authorized", "No asset grant covers this source version", 403)
        if "reader" not in principal.roles or not source.capability(now=now)["production_ready"]:
            raise DomainError("use_not_permitted", "Source is not admitted for production reads", 403)
        coverage = dict(source.coverage)
        if path == "/locations":
            if "locations" not in coverage:
                raise DomainError("not_covered", "Source has no validated precise location catalog", 501)
            locations = plain_json(coverage["locations"])
            if "country" in parameters:
                locations = [loc for loc in locations if loc.get("country") == parameters["country"]]
            return {"locations": locations, "source_adapter": source_id, "source_version": version}
        if "countries" not in coverage:
            raise DomainError("not_covered", "Source has no validated market catalog", 501)
        return {"countries": list(coverage["countries"]),
                "languages": list(coverage.get("languages", ())), "devices": list(coverage.get("devices", ())),
                "source_adapter": source_id, "source_version": version,
                "basis": "registered_source_coverage_not_observed_user_coverage"}

    def create_export(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.exports is None:
            raise DomainError("not_covered", "Export service is not connected", 501)
        return self.exports.create(principal, body, idempotency_key=idempotency_key, now=now)

    def create_analysis_job(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.jobs is None:
            raise DomainError("not_covered", "Analysis job service is not connected", 501)
        return self.jobs.create(principal, body, idempotency_key=idempotency_key, now=now)

    def create_live_query(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.live_queries is None:
            raise DomainError(
                "not_covered", "Live-query executor is not connected", 501)
        return self.live_queries.create(
            principal, body, idempotency_key=idempotency_key, now=now)

    def cancel_live_query(self, principal, live_id, *, now):
        self.registry.assert_current(principal, now=now)
        if self.live_queries is None:
            raise DomainError(
                "not_covered", "Live-query executor is not connected", 501)
        return self.live_queries.cancel(principal, live_id, now=now)

    def cancel_analysis_job(self, principal, job_id, *, now):
        self.registry.assert_current(principal, now=now)
        if self.jobs is None:
            raise DomainError("not_covered", "Analysis job service is not connected", 501)
        return self.jobs.cancel(principal, job_id, now=now)

    def cancel_export(self, principal, export_id, *, now):
        self.registry.assert_current(principal, now=now)
        if self.exports is None:
            raise DomainError("not_covered", "Export service is not connected", 501)
        return self.exports.cancel(principal, export_id, now=now)

    def create_report(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.reports is None:
            raise DomainError("not_covered", "Report service is not connected", 501)
        return self.reports.create(
            principal, body, idempotency_key=idempotency_key, now=now)

    def create_watchlist(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.watchlists is None:
            raise DomainError("not_covered", "Watchlist service is not connected", 501)
        return self.watchlists.create(
            principal, body, idempotency_key=idempotency_key, now=now)

    def remove_watchlist_item(self, principal, watchlist_id, entity_id, *, now):
        self.registry.assert_current(principal, now=now)
        if self.watchlists is None:
            raise DomainError("not_covered", "Watchlist service is not connected", 501)
        return self.watchlists.remove_item(
            principal, watchlist_id, entity_id, now=now)

    def create_monitor(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.monitors is None:
            raise DomainError("not_covered", "Monitor service is not connected", 501)
        return self.monitors.create(
            principal, body, idempotency_key=idempotency_key, now=now)

    def create_subscription(self, principal, body, *, idempotency_key, now):
        self.registry.assert_current(principal, now=now)
        if self.subscriptions is None:
            raise DomainError(
                "not_covered", "Subscription service is not connected", 501)
        return self.subscriptions.create(
            principal, body, idempotency_key=idempotency_key, now=now)

    def delete_subscription(self, principal, subscription_id, *, now):
        self.registry.assert_current(principal, now=now)
        if self.subscriptions is None:
            raise DomainError(
                "not_covered", "Subscription service is not connected", 501)
        return self.subscriptions.delete(
            principal, subscription_id, now=now)

    def patch_monitor(self, principal, monitor_id, body, *, if_match, now):
        self.registry.assert_current(principal, now=now)
        if self.monitors is None:
            raise DomainError("not_covered", "Monitor service is not connected", 501)
        return self.monitors.update(
            principal, monitor_id, body, if_match=if_match, now=now)
