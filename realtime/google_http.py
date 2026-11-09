"""Authenticated Google namespace; no initialization, collection or writes."""
from __future__ import annotations

import os
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .google_api import PREFIX, GoogleDomainService, openapi
from .google_domain import CONTRACT_VERSION, DomainError, canonical_json, identity, utc_text
from .google_runtime import configure_google_service
from .product import ProductError, ProductStore


def now():
    return datetime.now(timezone.utc)


def _send(handler, payload, status, *, extra_headers=None):
    raw = canonical_json(payload).encode()
    handler.send_response(status)
    for key, value in {"Content-Type": "application/json; charset=utf-8",
                       "Content-Length": str(len(raw)), "Cache-Control": "no-store",
                       "X-Content-Type-Options": "nosniff",
                       **({"Retry-After": "1"} if status == 429 else {}),
                       **(extra_headers or {})}.items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(raw)


def handle(handler, method):
    from .product_http import secure
    parsed = urlparse(handler.path)
    path = parsed.path[len(PREFIX):]
    generated = now()
    meta = dict(request_id=str(uuid4()), query_id=None, data_as_of=None, source_observed_at=None,
                generated_at=utc_text(generated), quality="unknown",
                coverage={"status": "not_covered", "scope": "registered_tenant_metadata"},
                versions={"api": "1.0.0", "contract": CONTRACT_VERSION},
                input_manifest_id=None, non_reproducible_reason="metadata_only_no_observation_manifest",
                rights_expiry=None)
    try:
        if os.getenv("SEARCHFLOW_PRODUCT_ENABLED") != "true":
            raise DomainError("not_covered", "Product service is disabled", 503)
        if not secure(handler):
            raise DomainError("not_authorized", "HTTPS is required for customer credentials", 403)
        auth = handler.headers.get("Authorization", "")
        store = ProductStore(handler.config.database_url)
        account = store.authenticate(auth[7:] if auth.startswith("Bearer ") else "", rate_limit=False)
        if os.getenv("SEARCHFLOW_GOOGLE_DOMAIN_ENABLED") != "true":
            raise DomainError("not_covered", "Google domain service is not enabled", 503)
        values = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(v) != 1 for v in values.values()):
            raise DomainError("invalid_argument", "Duplicate query parameters are not accepted")
        params = {k: v[0] for k, v in values.items()}
        service = configure_google_service(
            GoogleDomainService(handler.config.database_url))
        principal = service.registry.principal(str(account["id"]), subject_ref=str(account["key_id"]), now=generated)
        meta["query_id"] = identity("api-query", dict(path=path, parameters=params,
                                    tenant_id=principal.tenant_id, rights_version=principal.rights_version))
        body = None
        if method in {"POST", "PATCH", "DELETE"}:
            length = int(handler.headers.get("Content-Length", "0"))
            if length > 65536:
                raise DomainError("invalid_argument", "Request body is too large")
            raw = handler.rfile.read(max(0, length))
            if method in {"DELETE", "PATCH"} and not raw:
                body = {}
            elif path.endswith("/cancel") and not raw:
                body = {}
            else:
                try:
                    body = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError):
                    raise DomainError("invalid_argument", "JSON request body is required") from None
                if not isinstance(body, dict):
                    raise DomainError("invalid_argument", "Request body must be an object")
        with service.limits.hold(principal.tenant_id, now=generated):
            if method == "POST" and path == "/reports":
                data = service.create_report(
                    principal, body,
                    idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": data["manifest_id"],
                    "rights_expiry": data["expires_at"],
                    "quality": "deterministic_report",
                    "coverage": {"status": "frozen_manifest",
                                 "scope": "deterministic_report"},
                    "non_reproducible_reason": None,
                    "versions": {**meta["versions"], **data["versions"]}},
                    errors=[]), 201,
                    extra_headers={
                        "Location": "/v1/google/reports/" + data["report_id"]})
                return True
            if method == "POST" and path == "/watchlists":
                data = service.create_watchlist(
                    principal, body,
                    idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None, "rights_expiry": None,
                    "quality": "watchlist_membership",
                    "coverage": {"status": "key_scoped_membership",
                                 "scope": "watchlist"},
                    "non_reproducible_reason": None}, errors=[]), 201,
                    extra_headers={
                        "Location": "/v1/google/watchlists/"
                                    + data["watchlist_id"]})
                return True
            if method == "POST" and path == "/monitors":
                data = service.create_monitor(
                    principal, body,
                    idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None, "rights_expiry": None,
                    "quality": "monitor_configuration",
                    "coverage": {"status": "paused_no_scheduler",
                                 "scope": "monitor_config"},
                    "non_reproducible_reason": None}, errors=[]), 201,
                    extra_headers={
                        "Location": "/v1/google/monitors/" + data["monitor_id"],
                        "ETag": data["etag"]})
                return True
            if method == "POST" and path == "/subscriptions":
                data = service.create_subscription(
                    principal,
                    body,
                    idempotency_key=handler.headers.get(
                        "Idempotency-Key", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None,
                    "rights_expiry": None,
                    "quality": "subscription_configuration",
                    "coverage": {"status": "configured_paused",
                                 "scope": "change_subscription"},
                    "non_reproducible_reason": None}, errors=[]), 201,
                    extra_headers={
                        "Location": "/v1/google/subscriptions/"
                                    + data["subscription_id"],
                        "ETag": data["etag"]})
                return True
            if method == "PATCH" and re.fullmatch(r"/monitors/[^/]+", path):
                monitor_id = path[len("/monitors/"):]
                data = service.patch_monitor(
                    principal, monitor_id, body,
                    if_match=handler.headers.get("If-Match", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None, "rights_expiry": None,
                    "quality": "monitor_configuration",
                    "coverage": {"status": "paused_no_scheduler",
                                 "scope": "monitor_config"},
                    "non_reproducible_reason": None}, errors=[]), 200,
                    extra_headers={"ETag": data["etag"]})
                return True
            if method == "DELETE" and re.fullmatch(
                    r"/watchlists/[^/]+/items/[^/]+", path):
                parts = path.split("/")
                data = service.remove_watchlist_item(
                    principal,
                    unquote(parts[2], errors="strict"),
                    unquote(parts[4], errors="strict"),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None, "rights_expiry": None,
                    "quality": "watchlist_membership",
                    "coverage": {"status": "key_scoped_membership",
                                 "scope": "watchlist"},
                    "non_reproducible_reason": None}, errors=[]), 200,
                    extra_headers={"ETag": data["watchlist"]["etag"]})
                return True
            if method == "DELETE" and re.fullmatch(
                    r"/subscriptions/[^/]+", path):
                subscription_id = path[len("/subscriptions/"):]
                data = service.delete_subscription(
                    principal,
                    unquote(subscription_id, errors="strict"),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None,
                    "rights_expiry": None,
                    "quality": "subscription_configuration",
                    "coverage": {"status": data["state"],
                                 "scope": "change_subscription"},
                    "non_reproducible_reason": None}, errors=[]), 200,
                    extra_headers={"ETag": data["etag"]})
                return True
            if method == "POST" and path == "/exports":
                data = service.create_export(
                    principal, body, idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    now=generated)
                finish = now()
                service.registry.assert_current(principal, now=finish)
                data = service.exports.detail(principal, data["export_id"], now=finish)
                queued = data["status"] in {"queued", "running"}
                location = data["download_path"] or "/v1/google/exports/" + data["export_id"]
                _send(handler, dict(data=data, meta={**meta, "input_manifest_id": data["manifest_id"],
                    "rights_expiry": data["expires_at"],
                    "quality": "export_job_" + data["status"] if queued else
                               "frozen_selected_observations",
                    "coverage": {"status": data["status"],
                                 "scope": "export_job" if queued else "frozen_export_manifest"},
                    "non_reproducible_reason": None}, errors=[]), 202 if queued else 200,
                    extra_headers={"Location": location})
                return True
            if method == "POST" and path.startswith("/exports/") and path.endswith("/cancel"):
                if body:
                    raise DomainError("invalid_argument", "Cancel request has no body fields")
                export_id = path[len("/exports/"):-len("/cancel")]
                data = service.cancel_export(principal, export_id, now=generated)
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": data["manifest_id"],
                    "rights_expiry": data["expires_at"], "quality": "export_job_metadata",
                    "coverage": {"status": data["status"], "scope": "export_job"},
                    "non_reproducible_reason": None}, errors=[]), 200)
                return True
            if method == "POST" and path == "/analysis-jobs":
                data = service.create_analysis_job(
                    principal, body, idempotency_key=handler.headers.get("Idempotency-Key", ""),
                    now=generated)
                finish = now()
                service.registry.assert_current(principal, now=finish)
                data = service.jobs.detail(principal, data["job_id"], now=finish)
                _send(handler, dict(data=data, meta={**meta, "input_manifest_id": data["manifest_id"],
                    "rights_expiry": data["expires_at"],
                    "quality": "analysis_job_" + data["status"],
                    "coverage": {"status": data["status"],
                                 "scope": "frozen_manifest"},
                    "non_reproducible_reason": None}, errors=[]), 202,
                    extra_headers={"Location": "/v1/google/analysis-jobs/" + data["job_id"]})
                return True
            if method == "POST" and path == "/live-queries":
                data = service.create_live_query(
                    principal,
                    body,
                    idempotency_key=handler.headers.get(
                        "Idempotency-Key", ""),
                    now=generated,
                )
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None,
                    "rights_expiry": data["expires_at"],
                    "quality": "live_query_job_" + data["status"],
                    "coverage": {"status": data["status"],
                                 "scope": "on_demand_job"},
                    "non_reproducible_reason": (
                        "provider_live_response_not_a_frozen_manifest")},
                    errors=[]), 202,
                    extra_headers={
                        "Location": "/v1/google/live-queries/"
                                    + data["live_query_id"]})
                return True
            if (method == "POST" and path.startswith("/live-queries/")
                    and path.endswith("/cancel")):
                if body:
                    raise DomainError(
                        "invalid_argument", "Cancel request has no body fields")
                live_id = path[len("/live-queries/"):-len("/cancel")]
                data = service.cancel_live_query(
                    principal, live_id, now=generated)
                _send(handler, dict(data=data, meta={**meta,
                    "input_manifest_id": None,
                    "rights_expiry": data["expires_at"],
                    "quality": "live_query_job_metadata",
                    "coverage": {"status": data["status"],
                                 "scope": "on_demand_job"},
                    "non_reproducible_reason": (
                        "provider_live_response_not_a_frozen_manifest")},
                    errors=[]), 200)
                return True
            if method == "POST" and path.startswith("/analysis-jobs/") and path.endswith("/cancel"):
                if body:
                    raise DomainError("invalid_argument", "Cancel request has no body fields")
                job_id = path[len("/analysis-jobs/"):-len("/cancel")]
                data = service.cancel_analysis_job(principal, job_id, now=generated)
                _send(handler, dict(data=data, meta={**meta, "input_manifest_id": data["manifest_id"],
                    "rights_expiry": data["expires_at"], "quality": "analysis_job_metadata",
                    "coverage": {"status": "job", "scope": "frozen_manifest"},
                    "non_reproducible_reason": None}, errors=[]), 200)
                return True
            if method != "GET":
                # No writes are silently forwarded to the old search/monitor APIs.
                raise DomainError("not_covered", "Domain write operation is not connected", 501)
            if path == "/openapi.json":
                _send(handler, openapi(), 200)
                return True
            if os.getenv("SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED") == "true" and path in {"/legacy-observations", "/legacy-data"}:
                from .google_legacy import read_public_requests
                data = read_public_requests(handler.config.database_url, params, normalized=path == "/legacy-data")
            elif (os.getenv("SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED") == "true"
                  and re.fullmatch(r"/(?:legacy-analysis/requests|legacy-data/requests)/[^/]+", path)):
                from .google_legacy import read_public_request
                normalized = path.startswith('/legacy-data/')
                if set(params) - ({'field_contract'} if normalized else set()):
                    raise DomainError("invalid_argument", "Request detail takes no filters")
                data = read_public_request(handler.config.database_url, path.rsplit('/', 1)[1],
                                           normalized=normalized, field_contract=params.get('field_contract', 'legacy'))
            elif (os.getenv("SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED") == "true"
                  and path in {"/legacy-analysis/news", "/legacy-analysis/trends"}):
                from .google_legacy import read_source_analysis
                source = "google_news" if path.endswith("/news") else "google_trends"
                data = read_source_analysis(handler.config.database_url, source, params)
            elif (os.getenv("SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED") == "true"
                  and path == "/legacy-delivery"):
                from .google_delivery import read_delivery_status
                if params:
                    raise DomainError('invalid_argument', 'Delivery summary takes no filters')
                data = read_delivery_status(handler.config.database_url)
            elif (os.getenv("SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED") == "true"
                  and path == "/legacy-analysis/quality"):
                from .google_legacy import read_legacy_quality
                if params:
                    raise DomainError("invalid_argument", "Quality summary takes no filters")
                data = read_legacy_quality(handler.config.database_url)
            else:
                data = service.read(principal, path, params, now=generated)
            # A read assembled across several registry queries must not escape a
            # revocation committed while those queries were in progress.
            service.registry.assert_current(principal, now=now())
            expiry = service.rights_expiry(principal)
            meta["rights_expiry"] = utc_text(expiry) if expiry else None
        if method == "GET" and path.endswith("/content") and isinstance(data, dict) and "content" in data:
            raw = data["content"]
            handler.send_response(200)
            for key, value in {"Content-Type": data["content_type"],
                               "Content-Length": str(len(raw)),
                               "Content-Disposition": 'attachment; filename="' + data["filename"] + '"',
                               "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}.items():
                handler.send_header(key, value)
            handler.end_headers()
            handler.wfile.write(raw)
            return True
        if path in {"/legacy-observations", "/legacy-data", "/legacy-delivery"} or path.startswith(("/legacy-analysis/", "/legacy-data/")):
            meta.update(
                quality="historical_legacy_serp_preview",
                coverage=data.get("coverage", {"status": "available", "scope": "public_legacy_history_only"}),
                non_reproducible_reason="live_read_of_saved_legacy_requests_not_frozen_whale_readback",
            )
            meta["versions"]["methodology"] = "legacy-serp-v3"
            if path.startswith("/legacy-data"):
                from .google_data import DATA_VERSION
                meta["versions"]["data_contract"] = DATA_VERSION
                if params.get('field_contract') == 'prd-v1':
                    from .google_field_adaptation import FIELD_VERSION
                    meta['versions']['field_adaptation'] = FIELD_VERSION
        elif isinstance(data, dict) and "aggregation_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality=data.get("quality_name", "deterministic_domain_aggregate"),
                coverage=data["coverage"],
                non_reproducible_reason="no_frozen_query_manifest_created",
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "projection": data["projection_version"],
                "aggregation": data["aggregation_version"],
            })
        elif isinstance(data, dict) and "search_console_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_search_console_source",
                coverage=data["coverage"],
                input_manifest_id=data["evidence_id"],
                non_reproducible_reason=(
                    "search_console_response_rechecked_on_each_request"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "search_console": data["search_console_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
            })
        elif isinstance(data, dict) and "ads_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_managed_ads_output",
                coverage=data["coverage"],
                input_manifest_id=data["evidence_id"],
                non_reproducible_reason="managed_ads_output_rechecked_on_each_request",
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "ads": data["ads_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
                "revision": data["revision"],
            })
        elif isinstance(data, dict) and "merchant_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_merchant_report",
                coverage=data["coverage"],
                input_manifest_id=data["evidence_id"],
                non_reproducible_reason=(
                    "merchant_report_rechecked_on_each_request"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "merchant": data["merchant_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
            })
        elif isinstance(data, dict) and "local_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_conditional_local_source",
                coverage=data["coverage"],
                input_manifest_id=data.get("evidence_id"),
                non_reproducible_reason=(
                    "conditional_local_evidence_rechecked_on_each_request"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "local": data["local_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
            })
        elif isinstance(data, dict) and "ga4_analytics_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_ga4_analytics_source",
                coverage=data["coverage"],
                input_manifest_id=data["evidence_id"],
                non_reproducible_reason=(
                    "ga4_source_evidence_rechecked_on_each_request"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "ga4_analytics": data["ga4_analytics_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
            })
        elif isinstance(data, dict) and "metric_source_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="approved_typed_metric_source",
                coverage=data["coverage"],
                input_manifest_id=data["evidence_id"],
                non_reproducible_reason=(
                    "source_evidence_rechecked_on_each_request"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "metric_source": data["metric_source_version"],
                "source_api": data["source_api_version"],
                "dataset": data["dataset_id"],
            })
        elif isinstance(data, dict) and "quality_version" in data:
            meta.update(
                data_as_of=data["data_as_of"],
                source_observed_at=data["source_observed_at"],
                quality="operational_quality_ledger",
                coverage=data["coverage"],
                non_reproducible_reason=(
                    "operation_metrics_are_not_a_frozen_query_manifest"),
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update({
                "quality": data["quality_version"],
                "projection": data["projection_version"],
            })
        elif isinstance(data, dict) and "live_query_id" in data:
            meta.update(
                quality="live_query_job_metadata",
                coverage={
                    "status": data["status"],
                    "scope": "on_demand_job",
                },
                non_reproducible_reason=(
                    "provider_live_response_not_a_frozen_manifest"),
                rights_expiry=data["expires_at"],
            )
            meta["versions"]["live_query"] = data["live_query_version"]
            if (data.get("result") or {}).get("evidence"):
                evidence = data["result"]["evidence"]
                meta.update(
                    source_observed_at=evidence["source_observed_at"],
                    data_as_of=evidence["source_observed_at"],
                    rights_expiry=evidence["evidence_expires_at"],
                )
        elif isinstance(data, dict) and "live_queries" in data:
            meta.update(
                quality="live_query_job_list",
                coverage={
                    "status": "tenant_key_queue",
                    "scope": "on_demand_jobs",
                },
                non_reproducible_reason=(
                    "provider_live_responses_not_frozen_manifests"),
            )
        elif isinstance(data, dict) and "subscriptions" in data:
            meta.update(
                quality="subscription_configuration",
                coverage={
                    "status": "configured_paused",
                    "scope": "change_subscriptions",
                },
                non_reproducible_reason=None,
            )
        elif isinstance(data, dict) and "subscription_id" in data:
            meta.update(
                quality="subscription_configuration",
                coverage={
                    "status": data["state"],
                    "scope": "change_subscription",
                },
                non_reproducible_reason=None,
            )
            meta["versions"]["subscription"] = data["subscription_version"]
        elif path == "/legacy-observations" and isinstance(data, dict) and "requests" in data:
            meta.update(
                data_as_of=data["requests"][0]["completed_at"] if data["requests"] else None,
                source_observed_at=None,
                quality="legacy_public_observations_unverified",
                coverage={"status": "observed_subset", "scope": "public_legacy_history_only"},
                non_reproducible_reason="no_whale_version_or_frozen_manifest",
                rights_expiry=None,
            )
            meta["versions"]["source_schema"] = "legacy_google_serp_v1_or_unknown"
        elif isinstance(data, dict) and "projection_version" in data:
            meta.update(data_as_of=data["data_as_of"], source_observed_at=data["source_observed_at"],
                        quality="selected_observations",
                        coverage={"status": "observed_subset", "scope": "authorized_pinned_versions"},
                        non_reproducible_reason="no_frozen_query_manifest_created",
                        rights_expiry=data["evidence_expires_at"])
            meta["versions"]["projection"] = data["projection_version"]
        elif isinstance(data, dict) and ("jobs" in data or "job_id" in data):
            meta.update(quality="analysis_job_metadata",
                        coverage={"status": "job_queue", "scope": "worker_not_deployed"},
                        non_reproducible_reason=None)
        elif isinstance(data, dict) and "evidence_id" in data and "evidence" in data:
            meta.update(
                quality="licensed_pinned_evidence",
                coverage={"status": "authorized_pinned_version",
                          "scope": "one_observation"},
                non_reproducible_reason=None,
                rights_expiry=data["evidence_expires_at"],
            )
            meta["versions"].update(data["versions"])
        elif path == "/usage":
            meta.update(
                quality="local_usage_only",
                coverage={"status": "local_control_plane",
                          "scope": "tenant_usage"},
                non_reproducible_reason=None,
            )
        elif isinstance(data, dict) and "report_id" in data:
            meta.update(
                input_manifest_id=data["manifest_id"],
                rights_expiry=data["expires_at"],
                quality="deterministic_report",
                coverage={"status": "frozen_manifest",
                          "scope": "deterministic_report"},
                non_reproducible_reason=None,
            )
            meta["versions"].update(data["versions"])
        elif isinstance(data, dict) and "watchlists" in data:
            meta.update(quality="watchlist_membership",
                        coverage={"status": "key_scoped_membership",
                                  "scope": "watchlist"},
                        non_reproducible_reason=None)
        elif isinstance(data, dict) and "watchlist_id" in data:
            meta.update(quality="watchlist_membership",
                        coverage={"status": "key_scoped_membership",
                                  "scope": "watchlist"},
                        non_reproducible_reason=None)
        elif isinstance(data, dict) and "monitors" in data:
            meta.update(quality="monitor_configuration",
                        coverage={"status": "paused_no_scheduler",
                                  "scope": "monitor_config"},
                        non_reproducible_reason=None)
        elif isinstance(data, dict) and "monitor_id" in data:
            meta.update(quality="monitor_configuration",
                        coverage={"status": "paused_no_scheduler",
                                  "scope": "monitor_config"},
                        non_reproducible_reason=None)
        elif path.startswith("/exports"):
            meta.update(quality="frozen_export_metadata",
                        coverage={"status": "frozen_scope", "scope": "export_manifest"},
                        non_reproducible_reason=None)
        else:
            meta["quality"] = "metadata_only"
            meta["coverage"]["status"] = "metadata_only"
        _send(handler, dict(data=data, meta=meta, errors=[]), 200)
    except (DomainError, ProductError) as exc:
        code = "not_authorized" if isinstance(exc, ProductError) else exc.code
        _send(handler, dict(data=None, meta=meta, errors=[dict(
            code=code, message=str(exc), retryable=exc.status in {429, 503},
            source=None)]), exc.status)
    except (ValueError, TypeError, UnicodeError):
        _send(handler, dict(data=None, meta=meta, errors=[dict(
            code="invalid_argument", message="Invalid domain request", retryable=False, source=None)]), 400)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        print("google domain API: " + type(exc).__name__, flush=True)
        _send(handler, dict(data=None, meta=meta, errors=[dict(
            code="source_error", message="Domain service is unavailable or not initialized",
            retryable=True, source=None)]), 503)
    return True
