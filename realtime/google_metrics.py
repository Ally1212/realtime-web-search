"""Deterministic metric oracles; never infer missing source data or permissions.

Inputs must already be authorized and fixed to a manifest. These functions do
not fetch data, recognize brands, calibrate uncertainty, or approve a source.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

from .google_domain import DomainError, MetricValue, canonical_json, decimal, parse_time, text


def _metric(value, unit, scope, *, status=None, flags=(), nature="exact_in_scope",
            provenance="research_derived", scale_id=None, cohort_version=None):
    return MetricValue(
        value=value, unit=unit, provenance_kind=provenance,
        measurement_nature=nature, availability=status or (
            "not_observed" if value is None else "true_zero" if value == 0 else "available"),
        metric_scope=scope, definition_version="1", quality_flags=tuple(flags),
        scale_id=scale_id, cohort_version=cohort_version,
    )


def _compatible(before, after, *, scale=False):
    if any(m.evidence_expires_at is not None
           and parse_time(m.evidence_expires_at) <= datetime.now(timezone.utc)
           for m in (before, after)):
        raise DomainError("expired", "Metric evidence has expired", 410)
    # Observation windows may differ, but the measurement definition may not.
    temporal = {"period_start", "period_end", "observed_at", "date_range"}
    scopes = [{k: v for k, v in m.metric_scope.items() if k not in temporal}
              for m in (before, after)]
    if (canonical_json(scopes[0]) != canonical_json(scopes[1])
            or before.unit != after.unit
            or before.definition_version != after.definition_version
            or before.cohort_version != after.cohort_version
            or before.provenance_kind != after.provenance_kind
            or before.measurement_nature != after.measurement_nature
            or scale and (not before.scale_id or before.scale_id != after.scale_id)):
        raise DomainError("incompatible_scope", "Source/scope/scale/definition is not comparable", 409)


def ratio(numerator, denominator, *, scope, reason="no_eligible_samples"):
    if type(numerator) is not int or type(denominator) is not int or not 0 <= numerator <= denominator:
        raise DomainError("invalid_argument", "Ratio requires integer 0 <= numerator <= denominator")
    value = Decimal(numerator) / Decimal(denominator) if denominator else None
    return {
        "metric": _metric(value, "ratio_0_1", scope, flags=() if denominator else (reason,)),
        "numerator": numerator, "denominator": denominator,
        "sample_size": denominator,
        "interval_method": None,
    }


def organic_position(items, target_url, *, scope, depth, complete):
    text(target_url, "target_url")
    if type(depth) is not int or depth < 0 or type(complete) is not bool:
        raise DomainError("invalid_argument", "Invalid observed depth/completeness")
    positions, rank_missing = [], False
    for item in items:
        if (item.get("result_type") != "organic" or item.get("target_url") != target_url
                or item.get("content_semantics") == "publisher_article"):
            continue
        rank = item.get("organic_position")
        rank_missing |= rank is None
        if rank is not None and (type(rank) is not int or rank < 1 or rank > depth):
            raise DomainError("invalid_argument", "Organic rank is outside the declared depth")
        if rank is not None:
            positions.append(rank)
    if positions:
        return _metric(min(positions), "rank", scope, provenance="public_observed")
    if rank_missing:
        return _metric(None, "rank", scope, flags=("rank_not_provided",), provenance="public_observed")
    return _metric(None, "rank", scope,
                   status="outside_observed_depth" if complete and depth > 0 else "not_observed",
                   provenance="public_observed")


def rank_change(before, after):
    _compatible(before, after)
    if before.value is None or after.value is None:
        return _metric(None, "rank_change", after.metric_scope, flags=("censored_transition",))
    for value in (before.value, after.value):
        if decimal(value) < 1 or decimal(value) != decimal(value).to_integral_value():
            raise DomainError("invalid_argument", "Positions must be positive integers")
    return _metric(decimal(before.value) - decimal(after.value), "rank_change", after.metric_scope)


def _unique(rows, key):
    unique = {}
    for row in rows:
        rid = text(row.get(key), key)
        if rid in unique and canonical_json(unique[rid]) != canonical_json(row):
            raise DomainError("invalid_argument", "Conflicting duplicate observation")
        unique[rid] = row
    return list(unique.values())


def _same_scope(row, scope):
    if canonical_json(row.get("scope")) != canonical_json(scope):
        raise DomainError("incompatible_scope", "Input observations have different scope", 409)


def top_k_share(runs, *, k, scope):
    if type(k) is not int or k < 1:
        raise DomainError("invalid_argument", "K must be a positive integer")
    runs = _unique(runs, "run_id")
    eligible, numerator = 0, 0
    for run in runs:
        _same_scope(run, scope)
        depth, position = run.get("depth"), run.get("position")
        if type(depth) is not int or depth < 0:
            raise DomainError("invalid_argument", "Missing observed depth")
        if position is not None and (type(position) is not int or not 1 <= position <= depth):
            raise DomainError("invalid_argument", "Invalid rank")
        if (run.get("status") == "succeeded" and run.get("complete") is True
                and run.get("cache_hit") is False and depth >= k):
            eligible += 1
            numerator += int(position is not None and position <= k)
    return {
        **ratio(numerator, eligible, scope=scope),
        "planned": len(runs),
        "completed": sum(r.get("status") == "succeeded" for r in runs),
        "failed": sum(r.get("status") == "failed" for r in runs),
        "cached": sum(r.get("cache_hit") is True for r in runs),
    }


def trend_change(before, after):
    _compatible(before, after, scale=True)
    if before.value is None or after.value is None:
        return _metric(None, "ratio_change", after.metric_scope, flags=("missing_period",))
    base, current = decimal(before.value), decimal(after.value)
    if base < 0 or current < 0:
        raise DomainError("invalid_argument", "Trend interest cannot be negative")
    if base <= 0:
        return _metric(None, "ratio_change", after.metric_scope, flags=("nonpositive_baseline",))
    return _metric((current - base) / base, "ratio_change", after.metric_scope,
                   scale_id=after.scale_id, nature="relative_index")


def search_demand(rows, *, scope, provenance_kind, measurement_nature):
    groups = {}
    for row in rows:
        gid = text(row.get("group_id"), "close variant group_id")
        value = None if row.get("value") is None else decimal(row["value"])
        if value is not None and value < 0:
            raise DomainError("invalid_argument", "Demand must be nonnegative")
        if gid in groups and groups[gid] != value:
            raise DomainError("incompatible_scope", "Close variants disagree on group demand", 409)
        groups[gid] = value
    complete = bool(groups) and all(x is not None for x in groups.values())
    return _metric(sum(groups.values(), Decimal(0)) if complete else None,
                   "searches", scope, flags=() if complete else ("incomplete_demand",),
                   provenance=provenance_kind, nature=measurement_nature)


def gsc_position(rows, *, source, grain, scope):
    if source not in {"api", "bulk"} or grain not in {"site", "url"}:
        raise DomainError("incompatible_scope", "GSC source and table grain must be explicit", 409)
    numerator, impressions = Decimal(0), Decimal(0)
    for row in rows:
        weight = decimal(row.get("impressions"))
        if weight < 0 or weight != weight.to_integral_value():
            raise DomainError("invalid_argument", "Invalid impressions")
        if weight == 0:
            continue
        value = decimal(row.get("position" if source == "api" else
                                "sum_top_position" if grain == "site" else "sum_position"))
        if value < (1 if source == "api" else 0):
            raise DomainError("invalid_argument", "Invalid source position")
        numerator += value * weight if source == "api" else value
        impressions += weight
    result = numerator / impressions + (1 if source == "bulk" else 0) if impressions else None
    return _metric(result, "rank", {**scope, "gsc_source": source, "gsc_grain": grain},
                   provenance="authorized_reported")


def ai_visibility(answers, *, target_brand, target_domain, tracked_brands,
                  scope, channel, brand_version):
    text(target_brand, "target_brand")
    text(target_domain, "target_domain")
    text(brand_version, "brand_version")
    if channel not in {"aio", "ai_mode"}:
        raise DomainError("incompatible_scope", "Only consumer AIO/AI Mode observations are eligible", 409)
    tracked = set(tracked_brands)
    if target_brand not in tracked or not tracked:
        raise DomainError("invalid_argument", "Target must belong to fixed tracked brand set")
    answers = _unique(answers, "answer_id")
    observed = {}
    for row in answers:
        _same_scope(row, scope)
        if row.get("channel") != channel:
            raise DomainError("incompatible_scope", "AI consumer channels cannot be combined", 409)
        if row.get("cache_hit") is not False:
            continue
        oid = text(row.get("observation_id"), "observation_id")
        if oid in observed and canonical_json(observed[oid]) != canonical_json(row):
            raise DomainError("invalid_argument", "Observation identity reused by different answers")
        observed[oid] = row
    successful = [r for r in observed.values() if r.get("status") == "succeeded"]
    detected = [r for r in successful if r.get("feature_detectable") is True
                and type(r.get("triggered")) is bool]
    triggered = [r for r in detected if r["triggered"]]
    full = [r for r in triggered if r.get("answer_complete") is True]
    mentions = [r for r in full if r.get("mention_complete") is True]
    citations = [r for r in full if r.get("citation_capable") is True
                 and r.get("citation_complete") is True]
    for row in mentions:
        if not isinstance(row.get("brands"), list) or any(not isinstance(x, str) or not x for x in row["brands"]):
            raise DomainError("invalid_argument", "Complete mentions require ordered disambiguated brand IDs")
    for row in citations:
        if not isinstance(row.get("citation_domains"), list):
            raise DomainError("invalid_argument", "Complete citations require an explicit domain list")
    ordered = [list(dict.fromkeys(r["brands"])) for r in mentions]
    total_mentions = sum(len(tracked.intersection(brands)) for brands in ordered)
    target_mentions = sum(target_brand in brands for brands in ordered)
    positions = [Decimal(brands.index(target_brand) + 1) for brands in ordered if target_brand in brands]
    scoped = {**scope, "channel": channel, "brand_version": brand_version,
              "tracked_brands": sorted(tracked)}
    return {
        "presence": ratio(len(triggered), len(detected), scope=scoped),
        "mention": ratio(target_mentions, len(mentions), scope=scoped, reason="no_eligible_answers"),
        "citation": ratio(sum(target_domain in r["citation_domains"] for r in citations),
                          len(citations), scope=scoped, reason="no_eligible_answers"),
        "sov": ratio(target_mentions, total_mentions, scope=scoped, reason="no_tracked_mentions"),
        "first_mention_position": _metric(sum(positions) / len(positions) if positions else None,
                                          "brand_order", scoped),
        "planned": len(answers), "successful_independent": len(successful),
        "cached": sum(r.get("cache_hit") is True for r in answers),
        "failed": sum(r.get("status") == "failed" for r in answers),
        "unexpanded": sum(r.get("triggered") is True and r.get("answer_complete") is not True
                         for r in successful),
        "sampling_claim": "observed_answers_only_not_user_exposure_or_stable_probability",
    }
