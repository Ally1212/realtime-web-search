"""Summarize a bounded read-only legacy SERP sample without storing source rows."""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from realtime.google_domain import DomainError, Query, QueryVariant


def analyze(lines):
    source_counts = Counter()
    classifications = defaultdict(Counter)
    cache_statuses = defaultdict(Counter)
    schema_versions = defaultdict(Counter)
    result_fields = defaultdict(Counter)
    parameter_presence = defaultdict(Counter)
    conditions = Counter()
    failures = Counter()
    fingerprints = []
    timestamps = []

    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        source = row["source"]
        source_counts[source] += 1
        classifications[source][row["classification"]] += 1
        cache_statuses[source][row["cache_status"] or "unknown"] += 1
        schema_versions[source][row["response_schema_version"] or "unknown"] += 1
        fingerprints.append(row["request_fingerprint"])
        timestamps.append(row["completed_at"])
        for key, value in row["parameters"].items():
            if value is not None:
                parameter_presence[source][key] += 1
        for kind in ("organic", "news", "trends"):
            result_fields[kind].update(row[kind])
        if row["classification"] != "results":
            failures[row["classification"]] += 1
            continue
        if source != "google_web":
            continue
        parameters = row["parameters"]
        try:
            num = int(parameters["num"])
            location = {
                "precision": "unknown",
                "basis": "unknown",
            }
            if parameters["location"]:
                location["reported_label"] = parameters["location"]
            variant = QueryVariant(
                query_id=Query(row["query"]).query_id,
                country=parameters["gl"].upper(),
                language=parameters["hl"],
                location=location,
                device=parameters["device"] or "unknown",
                search_engine="google",
                surface="organic",
                channel="search",
                login_state="unknown",
                safe_search="unknown",
                depth=num,
                parameters_version="legacy-serp-read-only-1",
                parameters={"start": parameters["start"]},
            )
            if variant.variant_id:
                conditions["web_variant_constructed"] += 1
                if parameters["location"]:
                    conditions["location_label_not_precise_location"] += 1
                else:
                    conditions["location_unknown"] += 1
                if parameters["device"] is None:
                    conditions["device_unknown"] += 1
        except (DomainError, TypeError, ValueError, AttributeError) as exc:
            conditions["web_variant_unmapped"] += 1
            conditions["reason:" + type(exc).__name__] += 1

    if len(fingerprints) != 200 or len(set(fingerprints)) != 200:
        raise ValueError("Expected exactly 200 distinct public request fingerprints")
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "read_only_legacy_public_requests_not_independent_google_or_whale_validation",
        "sample_policy": {
            "google_web_results": 150,
            "google_web_nonresults": 20,
            "google_news_results": 20,
            "google_trends_results": 10,
            "order": "newest completed_at,request_id per source/class",
            "customer_private_excluded": True,
        },
        "count": len(fingerprints),
        "completed_at_min": min(timestamps),
        "completed_at_max": max(timestamps),
        "source_counts": dict(source_counts),
        "classifications": {key: dict(value) for key, value in classifications.items()},
        "cache_statuses": {key: dict(value) for key, value in cache_statuses.items()},
        "schema_versions": {key: dict(value) for key, value in schema_versions.items()},
        "parameter_presence": {key: dict(value) for key, value in parameter_presence.items()},
        "item_field_counts": {key: dict(value) for key, value in result_fields.items()},
        "condition_mapping": dict(conditions),
        "nonresult_classifications": dict(failures),
        "request_fingerprints": fingerprints,
        "limitations": [
            "existing_legacy_serp_requests_not_independent_source",
            "no_whale_document_version_or_terminal_readback",
            "no_manual_gold_labels_or_200_dual_source_comparison",
            "rss_feed_position_is_not_google_rank",
            "trending_rss_is_not_numeric_time_series",
            "unknown_location_login_state_and_cache_age_not_inferred",
            "no_raw_queries_urls_titles_or_responses_persisted_by_this_probe",
        ],
    }


if __name__ == "__main__":
    json.dump(analyze(sys.stdin), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
