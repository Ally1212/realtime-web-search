"""Manager-only operational quality ledger for verified SERP projections."""
from __future__ import annotations

from collections import Counter

from .google_domain import DomainError, parse_time, utc_text
from .google_observations import PROJECTION_VERSION

QUALITY_VERSION = "google-quality-1"
COMMON_PARAMETERS = frozenset({
    "observed_from", "observed_to", "source", "source_version",
    "collection_version", "country", "device", "surface", "status",
    "query_id", "variant_id",
})


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 6)


def _seconds(start, end):
    if start is None or end is None:
        return None
    value = (parse_time(end) - parse_time(start)).total_seconds()
    return value if value >= 0 else None


class GoogleQualityLedger:
    def __init__(self, registry, projection, *, clock):
        self.registry = registry
        self.projection = projection
        self.clock = clock

    def read(self, principal, parameters, *, now):
        if "manager" not in principal.roles:
            raise DomainError(
                "not_authorized",
                "This endpoint requires the management role",
                403,
            )
        if set(parameters) - COMMON_PARAMETERS:
            raise DomainError(
                "invalid_argument", "Unknown quality query parameter")
        for name, value in parameters.items():
            if not isinstance(value, str) or not value or len(value) > 4096:
                raise DomainError(
                    "invalid_argument", f"Invalid {name} filter")
        if "status" in parameters and parameters["status"] not in {
                "succeeded", "partial", "failed"}:
            raise DomainError(
                "invalid_argument", "Unknown observation status")
        has_from = "observed_from" in parameters
        has_to = "observed_to" in parameters
        if has_from != has_to:
            raise DomainError(
                "invalid_argument",
                "observed_from and observed_to must be supplied together",
            )
        bounds = None
        if has_from:
            start = parse_time(parameters["observed_from"])
            end = parse_time(parameters["observed_to"])
            if start >= end:
                raise DomainError(
                    "invalid_argument", "Observed window must be nonempty")
            bounds = (start, end)
        elif any(name in parameters for name in COMMON_PARAMETERS):
            raise DomainError(
                "invalid_argument",
                "A fixed observed window is required with cohort filters",
            )
        registry_rows = self.registry.sources(
            principal.tenant_id, now=now)
        monitor_rows = self._monitor_rows(principal, now=now)
        base = {
            "quality_version": QUALITY_VERSION,
            "projection_version": PROJECTION_VERSION,
            "data_as_of": utc_text(now),
            "source_observed_at": None,
            "evidence_expires_at": None,
            "coverage": {
                "status": "registry_only",
                "scope": "approved_source_metadata",
            },
            "filters": dict(parameters),
            "source_registry": {
                "count": len(registry_rows),
                "sources": registry_rows,
            },
            "plan_coverage": {
                "monitor_configurations": monitor_rows,
                "scheduler_state": "not_connected_no_scheduler",
                "execution_coverage": "not_covered",
                "reason": "monitor_scheduler_and_planned_run_ledger_not_connected",
            },
            "resources": {
                "api_limits": {
                    "reads_per_second": 5,
                    "concurrent_reads": 2,
                },
                "database_storage": "not_measured",
                "worker_cpu_memory": "not_covered",
            },
            "provider_usage": {
                "status": "not_covered",
                "reason": "billable_source_units_are_not_connected",
            },
            "fees": {
                "status": "not_covered",
                "reason": "no_fee_ledger_or_currency_settlement_is_connected",
            },
            "source_quota": {
                "status": "not_covered",
                "reason": "no_current_provider_quota_ledger_is_connected",
            },
        }
        if not bounds:
            base["observation_quality"] = {
                "status": "not_covered",
                "reason": "fixed_observed_window_required",
            }
            return base
        if self.projection is None:
            raise DomainError(
                "not_covered",
                "Observation quality projection is not connected",
                501,
            )
        snapshot = self.projection.snapshot(
            principal,
            now=now,
            filters={
                name: value for name, value in parameters.items()
                if name in COMMON_PARAMETERS
            },
            use="aggregate",
            max_runs=5000,
        )
        observations = snapshot["observations"]
        status_counts = Counter(row.status for row in observations)
        cache_counts = Counter(
            "hit" if row.cache_hit is True else
            "miss" if row.cache_hit is False else "unknown"
            for row in observations
        )
        error_counts = Counter(
            row.missing_reason or "unspecified_failure"
            for row in observations if row.status == "failed"
        )
        incomplete = sum(
            row.status == "succeeded"
            and (row.complete is not True or row.returned_depth is None)
            for row in observations
        )
        truncated = sum(row.truncated is True for row in observations)
        unknown_depth = sum(
            row.returned_depth is None for row in observations)
        ages = [
            (parse_time(now) - row.reference.observed_at).total_seconds()
            for row in observations
        ]
        completion_lags = [
            lag for row in observations
            if (lag := _seconds(row.requested_at, row.completed_at)) is not None
        ]
        ingest_lags = [
            lag for row in observations
            if (lag := _seconds(
                row.reference.source_observed_at,
                row.ingested_at,
            )) is not None
        ]
        source_to_received_lags = [
            lag for row in observations
            if (lag := _seconds(
                row.reference.source_observed_at,
                row.received_at,
            )) is not None
        ]
        total = len(observations)
        return {
            **base,
            "coverage": {
                "status": "fixed_verified_window",
                "scope": "selected_serp_runs",
                "observed_from": utc_text(bounds[0]),
                "observed_to": utc_text(bounds[1]),
                "run_cap": 5000,
                "truncated": False,
            },
            "data_as_of": (
                utc_text(snapshot["data_as_of"])
                if snapshot["data_as_of"] else None),
            "source_observed_at": (
                utc_text(snapshot["source_observed_at"])
                if snapshot["source_observed_at"] else None),
            "evidence_expires_at": (
                utc_text(snapshot["evidence_expires_at"])
                if snapshot["evidence_expires_at"] else None),
            "projection_revision": snapshot["projection_revision"],
            "observation_quality": {
                "status": "fixed_verified_window",
                "runs": {
                    "total": total,
                    "succeeded": status_counts["succeeded"],
                    "partial": status_counts["partial"],
                    "failed": status_counts["failed"],
                    "incomplete_success": incomplete,
                    "truncated": truncated,
                    "unknown_returned_depth": unknown_depth,
                },
                "cache": {
                    "hit": cache_counts["hit"],
                    "miss": cache_counts["miss"],
                    "unknown": cache_counts["unknown"],
                    "hit_rate_of_known": (
                        None if cache_counts["hit"] + cache_counts["miss"] == 0
                        else round(
                            cache_counts["hit"]
                            / (cache_counts["hit"] + cache_counts["miss"]),
                            6,
                        )
                    ),
                },
                "errors": {
                    "counts": dict(sorted(error_counts.items())),
                    "pass_through_success": status_counts["failed"] == 0,
                },
                "latency_seconds": {
                    "observation_age": {
                        "p50": _percentile(ages, 0.50),
                        "p95": _percentile(ages, 0.95),
                        "samples": len(ages),
                    },
                    "request_to_completion": {
                        "p50": _percentile(completion_lags, 0.50),
                        "p95": _percentile(completion_lags, 0.95),
                        "samples": len(completion_lags),
                    },
                    "source_to_received": {
                        "p50": _percentile(source_to_received_lags, 0.50),
                        "p95": _percentile(source_to_received_lags, 0.95),
                        "samples": len(source_to_received_lags),
                    },
                    "source_to_ingest": {
                        "p50": _percentile(ingest_lags, 0.50),
                        "p95": _percentile(ingest_lags, 0.95),
                        "samples": len(ingest_lags),
                    },
                    "method": "nearest_rank_empirical_percentile",
                    "slo_claimed": False,
                },
                "publication_time": {
                    "explicit_values": sum(
                        any(item.source_published_at is not None
                            for item in row.items)
                        for row in observations
                    ),
                    "unknown_values": sum(
                        not any(item.source_published_at is not None
                                for item in row.items)
                        for row in observations
                    ),
                    "inferred": False,
                },
                "limitations": [
                    "not_a_seven_day_slo",
                    "not_a_provider_usage_or_invoice_ledger",
                    "run_cap_returns_partial_result_instead_of_truncating",
                ],
            },
        }

    def _monitor_rows(self, principal, *, now):
        with self.registry.connect() as db:
            exists = db.execute(
                "SELECT to_regclass('sf_google_monitors') AS relation"
            ).fetchone()["relation"]
            if not exists:
                return {
                    "status": "not_initialized",
                    "total": None,
                    "enabled": None,
                }
            rows = db.execute(
                """SELECT enabled,count(*) AS n
                   FROM sf_google_monitors
                   WHERE tenant_id=%s
                   GROUP BY enabled
                   ORDER BY enabled""",
                (principal.tenant_id,),
            ).fetchall()
        counts = {
            "enabled" if row["enabled"] else "paused": row["n"]
            for row in rows
        }
        return {
            "status": "local_configuration_only",
            "total": sum(counts.values()),
            "enabled": counts.get("enabled", 0),
            "paused": counts.get("paused", 0),
        }
