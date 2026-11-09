"""Deterministic domain aggregates over fully reverified SERP projections."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import timedelta
from statistics import median
from urllib.parse import unquote, urlsplit

from .google_catalog import METRIC_CATALOG
from .google_domain import DomainError, decimal, identity, parse_time, text, utc_text
from .google_evidence import page_limit
from .google_observations import PROJECTION_VERSION

AGGREGATION_VERSION = "google-domain-analytics-1"
MAX_ANALYTICS_RUNS = 5000
MAX_NEWS_ITEMS = 10000
MAX_COMPARISON_DOMAINS = 10
ANALYTICS_ROUTES = frozenset({
    "/domains", "/domains/{id}", "/domains/{id}/pages", "/rankings", "/comparisons",
    "/news/observations", "/news/events",
    "/changes",
    "/insights/opportunities", "/insights/anomalies",
    "/topics", "/topics/{id}/relations",
    "/ai/answers", "/ai/answers/{id}", "/ai/citations", "/ai/visibility",
    "/insights/brands",
    "/articles/{id}",
})
COMMON_PARAMETERS = frozenset({
    "observed_from", "observed_to", "source", "source_version", "collection_version",
    "country", "device", "surface", "status", "query_id", "variant_id", "query",
})
RANKING_METRICS = frozenset({
    "top3_share", "top10_share", "median_position", "unique_urls",
})
INTERVAL_LIMITATION = "clustered_query_runs_are_not_independent_samples"
NEWS_CLUSTER_VERSION = "news-exact-v1"


def _host(value):
    if not value:
        return None
    parsed = urlsplit(value if "://" in value else "//" + value)
    host = parsed.hostname
    if not host:
        return None
    host = host.rstrip(".").lower()
    if len(host) > 253 or any(ch.isspace() for ch in host):
        return None
    return host


def _domain_id(host):
    return identity("domain", {"host": host, "aggregation_version": AGGREGATION_VERSION})


def _page_id(url):
    return identity("page", {"url": url, "aggregation_version": AGGREGATION_VERSION})


def _csv(name, value, *, required=False, maximum=100):
    if value is None:
        if required:
            raise DomainError("invalid_argument", f"{name} is required")
        return None
    text(value, name, maximum=16384)
    values = [part.strip() for part in value.split(",") if part.strip()]
    if len(values) > maximum or len(values) != len(set(values)):
        raise DomainError(
            "invalid_argument", f"{name} must contain at most {maximum} unique values")
    if required and not values:
        raise DomainError("invalid_argument", f"{name} is required")
    return values


def _ratio(numerator, denominator):
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _normalized_title(value):
    if not value:
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class GoogleDomainAnalytics:
    def __init__(self, projection, *, clock, monitors=None):
        self.projection = projection
        self.clock = clock
        self.monitors = monitors

    @staticmethod
    def route(path):
        if path in {
                "/domains", "/rankings", "/comparisons",
                "/news/observations", "/news/events", "/changes",
                "/insights/opportunities", "/insights/anomalies",
                "/topics", "/ai/answers", "/ai/citations", "/ai/visibility",
                "/insights/brands"}:
            return path[1:], None
        match = re.fullmatch(r"/articles/([^/]+)", path)
        if match:
            return "article-detail", unquote(match[1], errors="strict")
        match = re.fullmatch(r"/ai/answers/([^/]+)", path)
        if match:
            return "ai-answer-detail", unquote(match[1], errors="strict")
        match = re.fullmatch(r"/topics/([^/]+)/relations", path)
        if match:
            return "topic-relations", unquote(match[1], errors="strict")
        match = re.fullmatch(r"/domains/([^/]+)(/pages)?", path)
        if match:
            return ("pages" if match[2] else "domain-detail"), unquote(
                match[1], errors="strict")
        raise DomainError("not_found", "No domain analytics route matches", 404)

    def _filters(self, parameters):
        if "observed_from" not in parameters or "observed_to" not in parameters:
            raise DomainError(
                "invalid_argument",
                "A fixed [observed_from,observed_to) cohort is required",
            )
        start = parse_time(parameters["observed_from"])
        end = parse_time(parameters["observed_to"])
        if start >= end:
            raise DomainError("invalid_argument", "Observed window must be nonempty")
        filters = {
            name: parameters[name]
            for name in COMMON_PARAMETERS
            if name in parameters
        }
        topic = parameters.get("topic")
        if topic is not None:
            text(topic, "topic", maximum=4096)
            if "query" in filters and filters["query"] != topic:
                raise DomainError(
                    "invalid_argument",
                    "topic and query must name the same exact observed query",
                )
            filters["query"] = topic
        for name, value in filters.items():
            text(value, name, maximum=4096)
        return filters

    def _snapshot(self, principal, parameters, *, now):
        filters = self._filters(parameters)
        snap = self.projection.snapshot(
            principal, now=now, filters=filters, use="aggregate",
            max_runs=MAX_ANALYTICS_RUNS,
        )
        return snap, filters

    @staticmethod
    def _eligible(observation, k):
        return (
            observation.status == "succeeded"
            and observation.complete is True
            and observation.cache_hit is False
            and observation.returned_depth is not None
            and observation.returned_depth >= k
        )

    def _aggregate(self, observations):
        domains = defaultdict(lambda: {
            "organic_occurrences": 0,
            "unknown_position_occurrences": 0,
            "urls": set(),
            "best_by_run": {},
            "runs": set(),
            "feature_urls": set(),
            "evidence": {},
        })
        pages = defaultdict(lambda: {
            "domain_host": None,
            "organic_occurrences": 0,
            "unknown_position_occurrences": 0,
            "best_by_run": {},
            "runs": set(),
            "feature_urls": set(),
            "evidence": {},
        })
        for observation in observations:
            for item in observation.items:
                if item.result_type != "organic":
                    continue
                host = _host(item.domain) or _host(item.target_url)
                if not host:
                    continue
                bucket = domains[host]
                bucket["organic_occurrences"] += 1
                bucket["runs"].add(observation.run_id)
                bucket["evidence"][observation.reference.evidence_id] = (
                    observation.reference.observed_at)
                if item.target_url:
                    bucket["urls"].add(item.target_url)
                    page = pages[item.target_url]
                    page["domain_host"] = host
                    page["organic_occurrences"] += 1
                    page["runs"].add(observation.run_id)
                    page["evidence"][observation.reference.evidence_id] = (
                        observation.reference.observed_at)
                if item.organic_position is None:
                    bucket["unknown_position_occurrences"] += 1
                    if item.target_url:
                        pages[item.target_url]["unknown_position_occurrences"] += 1
                    continue
                previous = bucket["best_by_run"].get(observation.run_id)
                bucket["best_by_run"][observation.run_id] = (
                    item.organic_position
                    if previous is None else min(previous, item.organic_position)
                )
                if item.target_url:
                    previous = pages[item.target_url]["best_by_run"].get(
                        observation.run_id)
                    pages[item.target_url]["best_by_run"][observation.run_id] = (
                        item.organic_position
                        if previous is None else min(previous, item.organic_position)
                    )
            for feature in observation.features:
                urls = set(feature.target_urls) | set(feature.citation_urls)
                for url in urls:
                    host = _host(url)
                    if host:
                        domains[host]["feature_urls"].add(url)
                        domains[host]["evidence"][
                            observation.reference.evidence_id
                        ] = observation.reference.observed_at
                    if url in pages:
                        pages[url]["feature_urls"].add(url)
                        pages[url]["evidence"][
                            observation.reference.evidence_id
                        ] = observation.reference.observed_at
        return domains, pages

    @staticmethod
    def _coverage(observations):
        successful = [row for row in observations if row.status == "succeeded"]
        cached = [row for row in successful if row.cache_hit is True]
        incomplete = [
            row for row in successful
            if row.complete is not True or row.returned_depth is None
        ]
        return {
            "runs_total": len(observations),
            "succeeded_runs": len(successful),
            "failed_runs": sum(row.status == "failed" for row in observations),
            "partial_runs": sum(row.status == "partial" for row in observations),
            "cached_runs_excluded": len(cached),
            "incomplete_runs": len(incomplete),
            "run_cap": MAX_ANALYTICS_RUNS,
            "truncated": False,
        }

    @staticmethod
    def _is_news_item(observation, item):
        source = observation.reference.source_adapter.casefold()
        explicit_type = item.result_type.casefold() in {
            "news", "news_result", "top_stories",
        }
        return (
            "news" in source
            or explicit_type
            or item.publication_source is not None
            or item.source_published_at is not None
        )

    @staticmethod
    def _news_occurrences(observations):
        rows = []
        for observation in observations:
            for order, item in enumerate(observation.items):
                if not GoogleDomainAnalytics._is_news_item(observation, item):
                    continue
                host = _host(item.target_url)
                occurrence = {
                    "occurrence_id": identity("news-occurrence", {
                        "evidence_id": observation.reference.evidence_id,
                        "item_id": item.item_id,
                        "occurrence_order": order,
                    }),
                    "evidence_id": observation.reference.evidence_id,
                    "run_id": observation.run_id,
                    "item_id": item.item_id,
                    "query_id": observation.query.query_id,
                    "variant_id": observation.variant.variant_id,
                    "query": observation.query.original_text,
                    "observed_at": utc_text(observation.reference.observed_at),
                    "source_adapter": observation.reference.source_adapter,
                    "result_type": item.result_type,
                    "title": item.title,
                    "snippet": item.snippet,
                    "publisher": item.publication_source,
                    "source_published_at": item.source_published_at,
                    "target_url": item.target_url,
                    "domain": host,
                    "organic_position": item.organic_position,
                    "link_status": (
                        "not_observed" if item.target_url is None else
                        "google_news_redirect"
                        if (host or "").endswith("news.google.com") else
                        "publisher_url"
                    ),
                    "publication_time_is_inferred": False,
                }
                occurrence["_observed_datetime"] = observation.reference.observed_at
                occurrence["_publisher_key"] = (
                    item.publication_source.casefold()
                    if item.publication_source else None)
                occurrence["_title_key"] = _normalized_title(item.title)
                rows.append(occurrence)
        rows.sort(key=lambda row: (
            -row["_observed_datetime"].timestamp(),
            row["run_id"],
            row["item_id"],
        ))
        return rows

    @staticmethod
    def _public_news_row(row):
        return {key: value for key, value in row.items() if not key.startswith("_")}

    @staticmethod
    def _news_events(occurrences):
        grouped = defaultdict(list)
        for row in occurrences:
            if (row["target_url"] and row["link_status"] == "publisher_url"):
                key = ("target_url", row["target_url"])
            elif row["_publisher_key"] and row["_title_key"]:
                key = ("publisher_title", row["_publisher_key"], row["_title_key"])
            else:
                key = ("occurrence_only", row["occurrence_id"])
            grouped[key].append(row)
        events = []
        for key, rows in grouped.items():
            article_ids = {
                row["target_url"] for row in rows
                if row["target_url"] and row["link_status"] == "publisher_url"
            }
            unresolved = [
                row for row in rows if row["link_status"] == "google_news_redirect"]
            for row in unresolved:
                article_ids.add(identity("unresolved-news-link", {
                    "publisher": row["_publisher_key"],
                    "title": row["_title_key"],
                    "target_url": row["target_url"],
                }))
            if not article_ids:
                article_ids.add(identity("news-observation", {
                    "publisher": rows[0]["_publisher_key"],
                    "title": rows[0]["_title_key"],
                    "occurrence_id": rows[0]["occurrence_id"],
                }))
            observed = sorted(row["_observed_datetime"] for row in rows)
            publishers = sorted({
                row["publisher"] for row in rows if row["publisher"]})
            published = sorted(
                (parse_time(row["source_published_at"]), row["source_published_at"])
                for row in rows if row["source_published_at"]
            )
            events.append({
                "event_id": identity("news-event", {
                    "cluster_version": NEWS_CLUSTER_VERSION,
                    "key": list(key),
                }),
                "title": rows[0]["title"],
                "publishers": publishers,
                "first_observed_at": utc_text(observed[0]),
                "last_observed_at": utc_text(observed[-1]),
                "source_published_from": published[0][1] if published else None,
                "source_published_to": published[-1][1] if published else None,
                "occurrence_count": len(rows),
                "independent_article_count": len(article_ids),
                "repost_count": max(0, len(rows) - len(article_ids)),
                "unresolved_google_news_links": len(unresolved),
                "evidence_ids": sorted({row["evidence_id"] for row in rows}),
                "occurrences": [
                    GoogleDomainAnalytics._public_news_row(row) for row in rows],
                "cluster_method": "exact_publisher_url_or_explicit_publisher_title",
                "cluster_version": NEWS_CLUSTER_VERSION,
                "heuristic_not_gold": True,
            })
        events.sort(key=lambda row: (
            -parse_time(row["last_observed_at"]).timestamp(),
            row["event_id"],
        ))
        return events

    @staticmethod
    def _best_target_position(observation, target):
        positions = [
            item.organic_position for item in observation.items
            if item.result_type == "organic"
            and (_host(item.domain) or _host(item.target_url)) == target
            and item.organic_position is not None
        ]
        return min(positions) if positions else None

    def _opportunities(self, observations, target):
        grouped = defaultdict(list)
        for observation in observations:
            grouped[
                (observation.query.query_id,
                 observation.query.original_text,
                 observation.query.normalized_text)
            ].append(observation)
        rows = []
        for (query_id, original, _normalized), cohort in grouped.items():
            eligible = [row for row in cohort if self._eligible(row, 10)]
            hits = [
                row for row in eligible
                if (position := self._best_target_position(row, target)) is not None
                and position <= 10
            ]
            denominator = len(eligible)
            share = _ratio(len(hits), denominator)
            score = None if share is None else round(1 - share, 6)
            rows.append({
                "keyword": original,
                "query_id": query_id,
                "target_domain": target,
                "method": "observed-rank-gap-v1",
                "opportunity_score": score,
                "score_interval": None,
                "score_interval_reason": INTERVAL_LIMITATION,
                "current_top10_share": {
                    "numerator": len(hits),
                    "denominator": denominator,
                    "value": share,
                },
                "sample_size": denominator,
                "availability": (
                    "available" if score is not None else "not_observed"),
                "cost": {
                    "status": "not_covered",
                    "reason": "no_auction_cpc_or_paid_source_is_connected",
                },
                "undetermined": [
                    "actual_user_demand",
                    "click_value",
                    "causal_uplift_from_rank_change",
                ],
                "evidence_ids": sorted({
                    row.reference.evidence_id for row in cohort}),
            })
        rows.sort(key=lambda row: (
            row["opportunity_score"] is None,
            -(row["opportunity_score"] or 0),
            row["query_id"],
        ))
        return rows

    def _anomalies(self, observations, target, *, split_at, threshold):
        split = parse_time(split_at)
        early = [row for row in observations if row.reference.observed_at < split]
        late = [row for row in observations if row.reference.observed_at >= split]

        def measure(rows):
            eligible = [row for row in rows if self._eligible(row, 10)]
            hits = [
                row for row in eligible
                if (position := self._best_target_position(row, target)) is not None
                and position <= 10
            ]
            denominator = len(eligible)
            return {
                "numerator": len(hits),
                "denominator": denominator,
                "value": _ratio(len(hits), denominator),
                "evidence_ids": sorted({
                    row.reference.evidence_id for row in rows}),
            }

        baseline, current = measure(early), measure(late)
        delta = (
            None if baseline["value"] is None or current["value"] is None
            else round(current["value"] - baseline["value"], 6)
        )
        return {
            "target_domain": target,
            "rule": "top10_share_change_v1",
            "model": "none_deterministic",
            "split_at": utc_text(split),
            "baseline": baseline,
            "current": current,
            "delta": delta,
            "threshold": str(threshold),
            "detected": (
                None if delta is None else abs(delta) >= float(threshold)),
            "direction": (
                None if delta is None else
                "improved" if delta > 0 else
                "declined" if delta < 0 else "unchanged"),
            "category": "data_observation_change",
            "business_cause": {
                "status": "not_covered",
                "reason": "no_campaign_or_business_event_ledger_is_connected",
            },
            "interval": None,
            "interval_reason": INTERVAL_LIMITATION,
            "consistency_checks": {
                "eligible_depth": 10,
                "cached_runs_excluded": True,
                "repeated_queries_not_independent_samples": True,
            },
        }

    @staticmethod
    def _topics(observations):
        grouped = defaultdict(list)
        for observation in observations:
            grouped[observation.query.normalized_text].append(observation)
        rows = []
        for normalized, cohort in grouped.items():
            urls = {
                item.target_url for row in cohort for item in row.items
                if item.result_type == "organic" and item.target_url
            }
            hosts = {
                host for row in cohort for item in row.items
                if item.result_type == "organic"
                and (host := (_host(item.domain) or _host(item.target_url)))
            }
            rows.append({
                "topic_id": identity("query-topic", {
                    "normalized_text": normalized,
                    "collection_version": sorted({
                        row.collection_version for row in cohort}),
                }),
                "normalized_text": normalized,
                "aliases": sorted({row.query.original_text for row in cohort}),
                "query_ids": sorted({row.query.query_id for row in cohort}),
                "variant_ids": sorted({row.variant.variant_id for row in cohort}),
                "observation_count": len(cohort),
                "qualified_run_count": sum(
                    GoogleDomainAnalytics._eligible(row, 1) for row in cohort),
                "unique_urls": len(urls),
                "domains": sorted(hosts),
                "evidence_ids": sorted({
                    row.reference.evidence_id for row in cohort}),
                "topic_basis": "exact_normalized_observed_query",
                "approved_topic_taxonomy": False,
                "brand_relations": {
                    "status": "not_covered",
                    "reason": "no_approved_brand_taxonomy_or_relation_source",
                },
            })
        rows.sort(key=lambda row: (
            -row["qualified_run_count"],
            -row["observation_count"],
            row["topic_id"],
        ))
        return rows

    @staticmethod
    def _topic_relations(observations, source_topic_id):
        topics = GoogleDomainAnalytics._topics(observations)
        source = next(
            (row for row in topics if row["topic_id"] == source_topic_id), None)
        if source is None:
            raise DomainError("not_found", "No observed query topic matches", 404)

        def evidence(normalized):
            cohort = [
                row for row in observations
                if row.query.normalized_text == normalized
            ]
            urls = {
                item.target_url for row in cohort for item in row.items
                if item.result_type == "organic" and item.target_url
            }
            hosts = {
                host for row in cohort for item in row.items
                if item.result_type == "organic"
                and (host := (_host(item.domain) or _host(item.target_url)))
            }
            return cohort, urls, hosts

        source_runs, source_urls, source_hosts = evidence(
            source["normalized_text"])
        related = []
        for topic in topics:
            if topic["topic_id"] == source_topic_id:
                continue
            runs, urls, hosts = evidence(topic["normalized_text"])
            shared_urls = source_urls & urls
            shared_hosts = source_hosts & hosts
            if not shared_urls and not shared_hosts:
                continue
            url_union = source_urls | urls
            host_union = source_hosts | hosts
            url_jaccard = _ratio(len(shared_urls), len(url_union))
            domain_jaccard = _ratio(len(shared_hosts), len(host_union))
            confidence = (
                url_jaccard if url_jaccard is not None else domain_jaccard)
            related.append({
                "source_topic_id": source_topic_id,
                "related_topic_id": topic["topic_id"],
                "related_normalized_text": topic["normalized_text"],
                "related_aliases": topic["aliases"],
                "shared_result_urls": sorted(shared_urls)[:100],
                "shared_domains": sorted(shared_hosts)[:100],
                "url_jaccard": url_jaccard,
                "domain_jaccard": domain_jaccard,
                "confidence": confidence,
                "confidence_method": "observed_result_url_or_domain_jaccard",
                "source_run_count": sum(
                    GoogleDomainAnalytics._eligible(row, 1)
                    for row in source_runs),
                "related_run_count": sum(
                    GoogleDomainAnalytics._eligible(row, 1) for row in runs),
                "evidence_ids": sorted({
                    row.reference.evidence_id
                    for row in (*source_runs, *runs)}),
                "relation_method": "shared_observed_results_v1",
                "causal_claim": False,
                "statistical_significance_tested": False,
            })
        related.sort(key=lambda row: (
            -(row["confidence"] or 0),
            row["related_topic_id"],
        ))
        return source, related

    @staticmethod
    def _ai_answers(observations, channel):
        rows = []
        feature_type = "ai_overview" if channel == "aio" else "ai_mode"
        for observation in observations:
            for order, feature in enumerate(observation.features):
                if feature.feature_type != feature_type:
                    continue
                citations = list(feature.citation_urls)
                rows.append({
                    "answer_id": identity("ai-answer", {
                        "evidence_id": observation.reference.evidence_id,
                        "feature_id": feature.feature_id,
                        "occurrence_order": order,
                    }),
                    "evidence_id": observation.reference.evidence_id,
                    "run_id": observation.run_id,
                    "feature_id": feature.feature_id,
                    "query_id": observation.query.query_id,
                    "query": observation.query.original_text,
                    "observed_at": utc_text(observation.reference.observed_at),
                    "channel": channel,
                    "presence": feature.presence,
                    "answer_text": None,
                    "answer_text_status": "not_covered_by_typed_projection",
                    "citation_urls": citations,
                    "cited_domains": sorted({
                        host for url in citations if (host := _host(url))}),
                    "target_urls": list(feature.target_urls),
                    "expanded_state": feature.expanded_state,
                    "block_order": feature.block_order,
                })
        rows.sort(key=lambda row: (
            -parse_time(row["observed_at"]).timestamp(),
            row["answer_id"],
        ))
        return rows

    @staticmethod
    def _ai_citations(answers):
        rows = []
        for answer in answers:
            for order, url in enumerate(answer["citation_urls"]):
                rows.append({
                    "ai_citation_id": identity("ai-citation", {
                        "answer_id": answer["answer_id"],
                        "occurrence_order": order,
                        "url": url,
                    }),
                    "answer_id": answer["answer_id"],
                    "evidence_id": answer["evidence_id"],
                    "run_id": answer["run_id"],
                    "feature_id": answer["feature_id"],
                    "query_id": answer["query_id"],
                    "query": answer["query"],
                    "observed_at": answer["observed_at"],
                    "channel": answer["channel"],
                    "citation_url": url,
                    "domain": _host(url),
                    "citation_order": order,
                    "visibility_basis": "explicit_feature_citation_url",
                })
        rows.sort(key=lambda row: (
            -parse_time(row["observed_at"]).timestamp(),
            row["answer_id"],
            row["citation_order"],
        ))
        return rows

    def _ai_visibility(self, observations, brands, channel):
        feature_type = "ai_overview" if channel == "aio" else "ai_mode"
        grouped = defaultdict(list)
        for observation in observations:
            grouped[
                (observation.query.query_id, observation.query.original_text)
            ].append(observation)
        rows = []
        for (query_id, prompt), cohort in grouped.items():
            eligible = [row for row in cohort if self._eligible(row, 1)]
            detected = []
            all_citations = []
            for observation in eligible:
                matches = [
                    feature for feature in observation.features
                    if feature.feature_type == feature_type
                ]
                if matches:
                    detected.append(observation)
                for feature in matches:
                    all_citations.extend(feature.citation_urls)
            brand_stats = {}
            requested_citation_count = 0
            for brand in brands:
                citations = [
                    url for url in all_citations if _host(url) == brand]
                requested_citation_count += len(citations)
                brand_stats[brand] = {
                    "brand_domain": brand,
                    "citation_count": len(citations),
                    "citation_run_count": len({
                        row.run_id for row in detected
                        if any(_host(url) == brand for feature in row.features
                               if feature.feature_type == feature_type
                               for url in feature.citation_urls)
                    }),
                    "mention_status": "not_covered",
                    "mention_reason": "typed_projection_has_no_ai_answer_text",
                }
            for brand in brands:
                count = brand_stats[brand]["citation_count"]
                brand_stats[brand]["citation_share_of_voice"] = {
                    "numerator": count,
                    "denominator": requested_citation_count,
                    "value": _ratio(count, requested_citation_count),
                    "interval": None,
                    "interval_reason": INTERVAL_LIMITATION,
                    "brand_set_limited": True,
                }
            rows.append({
                "prompt": prompt,
                "query_id": query_id,
                "prompt_basis": "exact_normalized_observed_query_not_prompt_taxonomy",
                "channel": channel,
                "presence": {
                    "numerator": len(detected),
                    "denominator": len(eligible),
                    "value": _ratio(len(detected), len(eligible)),
                    "interval": None,
                    "interval_reason": INTERVAL_LIMITATION,
                },
                "brands": [brand_stats[brand] for brand in brands],
                "citations_total_in_requested_brand_set": requested_citation_count,
                "answer_text_status": "not_covered_by_typed_projection",
                "mention_not_citation": True,
            })
        rows.sort(key=lambda row: (-row["presence"]["numerator"], row["query_id"]))
        return rows

    def _brand_tracks(self, observations, brands, domains, news_occurrences):
        rows = []
        for brand in brands:
            bucket = domains.get(brand, {
                "organic_occurrences": 0,
                "unknown_position_occurrences": 0,
                "urls": set(),
                "best_by_run": {},
                "runs": set(),
                "feature_urls": set(),
            })
            domain = self._domain_row(brand, bucket, observations)
            news = [
                occurrence for occurrence in news_occurrences
                if occurrence["domain"] == brand
            ]
            all_ai_answers = (
                self._ai_answers(observations, "aio") +
                self._ai_answers(observations, "ai_mode")
            )
            ai_citations = [
                citation for citation in self._ai_citations(all_ai_answers)
                if citation["domain"] == brand
            ]
            rows.append({
                "brand_domain": brand,
                "brand_basis": "explicit_domain_not_approved_brand_taxonomy",
                "tracks": {
                    "search_observations": {
                        "top10_share": domain["top10_share"],
                        "median_best_position": domain["median_best_position"],
                        "unique_urls": domain["unique_urls"],
                        "runs_observed": domain["runs_observed"],
                    },
                    "news_observations": {
                        "occurrence_count": len(news),
                        "publishers": sorted({
                            row["publisher"] for row in news if row["publisher"]}),
                        "unresolved_google_news_links": sum(
                            row["link_status"] == "google_news_redirect"
                            for row in news),
                    },
                    "ai_citations": {
                        "citation_count": len(ai_citations),
                        "citation_runs": len({
                            row["run_id"] for row in ai_citations}),
                        "channels": sorted({
                            row["channel"] for row in ai_citations}),
                        "mention_status": "not_covered",
                    },
                    "demand": {
                        "status": "not_covered",
                        "reason": "no_approved_search_demand_or_keyword_volume_source",
                    },
                },
                "combined_index": None,
                "combining_reason": "search_news_and_ai_tracks_have_different_denominators",
            })
        rows.sort(key=lambda row: row["brand_domain"])
        return rows

    @staticmethod
    def _articles(observations):
        grouped = defaultdict(list)
        for observation in observations:
            for order, item in enumerate(observation.items):
                article_id = (
                    identity("article", {"target_url": item.target_url})
                    if item.target_url else
                    identity("article-observation", {
                        "evidence_id": observation.reference.evidence_id,
                        "item_id": item.item_id,
                        "occurrence_order": order,
                    })
                )
                grouped[article_id].append((observation, order, item))
        rows = []
        for article_id, occurrences in grouped.items():
            observation, order, item = sorted(
                occurrences,
                key=lambda value: value[0].reference.observed_at,
                reverse=True,
            )[0]
            rows.append({
                "article_id": article_id,
                "evidence_id": observation.reference.evidence_id,
                "item_id": item.item_id,
                "occurrence_order": order,
                "title": item.title,
                "snippet": item.snippet,
                "target_url": item.target_url,
                "display_url": item.display_url,
                "domain": _host(item.domain) or _host(item.target_url),
                "publisher": item.publication_source,
                "source_published_at": item.source_published_at,
                "body": None,
                "body_status": "not_covered_by_typed_projection",
                "body_reason": "typed_port_carries_licensed_search_summary_not_article_body",
                "observation_count": len(occurrences),
                "evidence_ids": sorted({
                    row.reference.evidence_id for row, _order, _item in occurrences}),
                "first_observed_at": utc_text(min(
                    row.reference.observed_at
                    for row, _order, _item in occurrences)),
                "last_observed_at": utc_text(max(
                    row.reference.observed_at
                    for row, _order, _item in occurrences)),
            })
        rows.sort(key=lambda row: row["article_id"])
        return rows

    def _domain_row(self, host, bucket, observations):
        def share(k):
            numerator = sum(
                1 for row in observations
                if self._eligible(row, k)
                and (position := bucket["best_by_run"].get(row.run_id)) is not None
                and position <= k
            )
            denominator = sum(self._eligible(row, k) for row in observations)
            return {
                "numerator": numerator,
                "denominator": denominator,
                "value": _ratio(numerator, denominator),
                "interval": None,
                "interval_reason": INTERVAL_LIMITATION,
            }

        positions = sorted(bucket["best_by_run"].values())
        evidence = [
            {
                "evidence_id": evidence_id,
                "observed_at": utc_text(observed_at),
            }
            for evidence_id, observed_at in sorted(
                bucket.get("evidence", {}).items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )[:20]
        ]
        return {
            "domain_id": _domain_id(host),
            "domain": host,
            "domain_basis": "selected_item_domain_or_target_url_hostname",
            "organic_occurrences": bucket["organic_occurrences"],
            "unknown_position_occurrences": bucket["unknown_position_occurrences"],
            "runs_observed": len(bucket["runs"]),
            "unique_urls": len(bucket["urls"]),
            "feature_urls": len(bucket["feature_urls"]),
            "median_best_position": (
                float(median(positions)) if positions else None),
            "top1_share": share(1),
            "top3_share": share(3),
            "top10_share": share(10),
            "recent_evidence": evidence,
            "recent_evidence_total": len(bucket.get("evidence", {})),
            "recent_evidence_truncated": (
                len(bucket.get("evidence", {})) > len(evidence)),
            "not_user_traffic": True,
        }

    def _page_row(self, url, bucket, observations):
        def share(k):
            numerator = sum(
                1 for row in observations
                if self._eligible(row, k)
                and (position := bucket["best_by_run"].get(row.run_id)) is not None
                and position <= k
            )
            denominator = sum(self._eligible(row, k) for row in observations)
            return {
                "numerator": numerator,
                "denominator": denominator,
                "value": _ratio(numerator, denominator),
                "interval": None,
                "interval_reason": INTERVAL_LIMITATION,
            }

        positions = sorted(bucket["best_by_run"].values())
        evidence = [
            {
                "evidence_id": evidence_id,
                "observed_at": utc_text(observed_at),
            }
            for evidence_id, observed_at in sorted(
                bucket.get("evidence", {}).items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )[:20]
        ]
        return {
            "page_id": _page_id(url),
            "target_url": url,
            "domain_id": _domain_id(bucket["domain_host"]),
            "domain": bucket["domain_host"],
            "organic_occurrences": bucket["organic_occurrences"],
            "unknown_position_occurrences": bucket["unknown_position_occurrences"],
            "runs_observed": len(bucket["runs"]),
            "feature_url_occurrences": len(bucket["feature_urls"]),
            "median_best_position": (
                float(median(positions)) if positions else None),
            "top3_share": share(3),
            "top10_share": share(10),
            "recent_evidence": evidence,
            "recent_evidence_total": len(bucket.get("evidence", {})),
            "recent_evidence_truncated": (
                len(bucket.get("evidence", {})) > len(evidence)),
            "page_basis": "exact_observed_target_url",
        }

    def _pagination(
            self, rows, parameters, *, principal, now, binding, evidence_expiry=None):
        limit = page_limit(parameters.get("limit"))
        codec = self.projection.cursor_codec
        if codec is None:
            raise DomainError(
                "not_covered", "Analytics pagination signing is not configured", 503)
        offset = 0
        if "cursor" in parameters:
            position = codec.decode(parameters["cursor"], binding=binding, now=now)
            if len(position) != 1 or type(position[0]) is not int or position[0] < 0:
                raise DomainError("invalid_argument", "Invalid analytics cursor")
            offset = position[0]
        page = rows[offset:offset + limit]
        more = offset + len(page) < len(rows)
        next_cursor = None
        if more:
            expiry = parse_time(now) + timedelta(minutes=10)
            if evidence_expiry is not None:
                expiry = min(expiry, evidence_expiry)
            next_cursor = codec.encode(
                binding=binding, position=[offset + len(page)],
                expires_at=expiry, now=now,
            )
        return page, next_cursor

    def read(self, principal, path, parameters, *, now):
        kind, detail_id = self.route(path)
        allowed = set(COMMON_PARAMETERS)
        if kind == "domains":
            allowed |= {"limit", "cursor", "cohort_version"}
        elif kind in {"rankings", "comparisons"}:
            allowed |= {"domains", "metric"}
        elif kind in {"news/observations", "news/events"}:
            allowed |= {"topic", "limit", "cursor"}
        elif kind == "insights/opportunities":
            allowed |= {
                "topic", "target", "method", "limit", "cursor",
            }
        elif kind == "insights/anomalies":
            allowed |= {
                "target", "monitor_id", "split_at", "threshold",
                "rule", "model",
            }
        elif kind == "changes":
            allowed |= {
                "target", "split_at", "threshold", "event_type",
                "limit", "cursor",
            }
        elif kind == "insights/brands":
            allowed |= {
                "brands", "brand_set_version", "limit", "cursor",
            }
        elif kind in {"topics", "topic-relations"}:
            allowed |= {"limit", "cursor"}
        elif kind in {"ai/answers", "ai/citations"}:
            allowed |= {"channel", "limit", "cursor"}
        elif kind == "ai-answer-detail":
            allowed.add("channel")
        elif kind == "ai/visibility":
            allowed |= {"channel", "brands", "limit", "cursor"}
        elif kind == "article-detail":
            allowed |= {"fields", "version"}
        if kind == "pages":
            allowed |= {"limit", "cursor"}
        if set(parameters) - allowed:
            raise DomainError(
                "invalid_argument", "Unknown query parameter for domain analytics")
        if kind in {"domain-detail", "pages"} and (
                "limit" in parameters or "cursor" in parameters) and kind == "domain-detail":
            raise DomainError("invalid_argument", "Domain detail is not paginated")
        snap, filters = self._snapshot(principal, parameters, now=now)
        observations = snap["observations"]
        domains, pages = self._aggregate(observations)
        coverage = self._coverage(observations)
        news_occurrences = self._news_occurrences(observations)
        if len(news_occurrences) > MAX_NEWS_ITEMS:
            raise DomainError(
                "partial_result",
                "News cohort exceeds the local verified-item cap; narrow the window",
                413,
            )
        coverage["news_runs"] = len({
            row["run_id"] for row in news_occurrences})
        coverage["news_occurrences"] = len(news_occurrences)
        universe = {
            "universe_id": identity("analytics-universe", {
                "aggregation_version": AGGREGATION_VERSION,
                "projection_revision": snap["projection_revision"],
                "filters": filters,
                "cohort_version": parameters.get("cohort_version"),
            }),
            "cohort_version": parameters.get("cohort_version"),
            "filters": filters,
            "fixed_observed_cohort": True,
        }
        evidence_expiry = snap["evidence_expires_at"]
        common = {
            "aggregation_version": AGGREGATION_VERSION,
            "projection_version": PROJECTION_VERSION,
            "projection_revision": snap["projection_revision"],
            "data_as_of": (
                utc_text(snap["data_as_of"]) if snap["data_as_of"] else None),
            "source_observed_at": (
                utc_text(snap["source_observed_at"])
                if snap["source_observed_at"] else None),
            "evidence_expires_at": (
                utc_text(evidence_expiry) if evidence_expiry else None),
            "reproducibility": "pinned_versions_rechecked_for_each_run",
            "coverage": {
                **coverage,
                "status": "fixed_observed_cohort",
                "scope": "verified_selected_serp_facts",
            },
            "metric_definitions": [
                dict(metric) for metric in METRIC_CATALOG
                if metric["id"] in {"GO-MET-001", "GO-MET-002", "GO-MET-004"}
            ],
            "topic_filter": (
                {
                    "value": parameters.get("topic"),
                    "basis": "exact_normalized_observed_query_not_topic_taxonomy",
                }
                if "topic" in parameters else None
            ),
        }
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                key: value for key, value in parameters.items() if key != "cursor"
            },
            "sort": ["analytics_rank"],
            "source": f"{AGGREGATION_VERSION}:{snap['projection_revision']}",
            "manifest_id": None,
        }
        if kind == "insights/brands":
            brand_values = _csv(
                "brands", parameters.get("brands"), required=True, maximum=20)
            brands = []
            for value in brand_values:
                brand = _host(value)
                if not brand:
                    raise DomainError("invalid_argument", "brands contains an invalid domain")
                brands.append(brand)
            brands = sorted(set(brands))
            version = parameters.get("brand_set_version", "exact-observed-domains-v1")
            rows = self._brand_tracks(
                observations, brands, domains, news_occurrences)
            page, cursor = self._pagination(
                rows, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="separated_brand_observation_tracks",
                brand_set_version=version,
                brand_set_id=identity("brand-set", {
                    "brands": brands,
                    "version": version,
                    "aggregation_version": AGGREGATION_VERSION,
                }),
                brands=page,
                next_cursor=cursor,
                universe=universe,
                total=len(rows),
                demand_status="not_covered",
                combined_index_allowed=False,
                combined_index_reason=(
                    "search_news_and_ai_tracks_have_different_denominators"),
            )
        if kind in {"insights/opportunities", "insights/anomalies"}:
            target = parameters.get("target")
            if target:
                target = next(
                    (host for host in domains if target in {host, _domain_id(host)}),
                    None,
                ) or _host(target)
            if not target:
                raise DomainError(
                    "invalid_argument", "A valid target domain is required")
            if kind == "insights/opportunities":
                method = parameters.get("method", "observed-rank-gap-v1")
                if method != "observed-rank-gap-v1":
                    raise DomainError("invalid_argument", "Unknown opportunity method")
                rows = self._opportunities(observations, target)
                page, cursor = self._pagination(
                    rows, parameters, principal=principal, now=now,
                    binding=binding, evidence_expiry=evidence_expiry)
                return dict(
                    common,
                    quality_name="deterministic_opportunity_scores",
                    method=method,
                    target_domain=target,
                    opportunities=page,
                    next_cursor=cursor,
                    universe=universe,
                    total=len(rows),
                    score_range={"minimum": 0, "maximum": 1},
                    cost_status="not_covered",
                    causal_claim=False,
                )
            split_at = parameters.get("split_at")
            if not split_at:
                raise DomainError(
                    "invalid_argument", "split_at is required for change analysis")
            split = parse_time(split_at)
            if not (
                    parse_time(parameters["observed_from"]) < split
                    < parse_time(parameters["observed_to"])):
                raise DomainError(
                    "invalid_argument", "split_at must define two nonempty windows")
            threshold = decimal(parameters.get("threshold", "0.1"))
            if not 0 <= threshold <= 1:
                raise DomainError("invalid_argument", "threshold must be between 0 and 1")
            rule = parameters.get("rule", "top10_share_change_v1")
            model = parameters.get("model", "none_deterministic")
            if rule != "top10_share_change_v1" or model != "none_deterministic":
                raise DomainError("invalid_argument", "Unknown anomaly rule or model")
            monitor_snapshot = None
            if "monitor_id" in parameters:
                if self.monitors is None:
                    raise DomainError("not_covered", "Monitor service is not connected", 501)
                monitor_snapshot = self.monitors.detail(
                    principal, parameters["monitor_id"], now=now)
                monitor_domains = monitor_snapshot["config"].get("domains", [])
                if monitor_domains and target not in {
                        _host(value) for value in monitor_domains
                }:
                    raise DomainError(
                        "incompatible_scope",
                        "Target domain is outside the saved monitor",
                        409,
                    )
                monitor_snapshot = {
                    "monitor_id": monitor_snapshot["monitor_id"],
                    "version": monitor_snapshot["version"],
                    "etag": monitor_snapshot["etag"],
                    "scheduler_state": monitor_snapshot["scheduler_state"],
                }
            anomaly = self._anomalies(
                observations, target, split_at=split_at, threshold=threshold)
            anomaly["monitor"] = monitor_snapshot
            anomaly["monitor_binding"] = (
                "saved_monitor_configuration_only_no_scheduler"
                if monitor_snapshot else "explicit_target_not_saved_monitor")
            return dict(
                common,
                quality_name="deterministic_change_candidate",
                anomaly=anomaly,
                universe=universe,
                business_causality_not_claimed=True,
            )
        if kind == "changes":
            split_at = parameters.get("split_at")
            if not split_at:
                raise DomainError(
                    "invalid_argument", "split_at is required for changes")
            split = parse_time(split_at)
            if not (
                    parse_time(parameters["observed_from"]) < split
                    < parse_time(parameters["observed_to"])):
                raise DomainError(
                    "invalid_argument",
                    "split_at must define two nonempty windows",
                )
            threshold = decimal(parameters.get("threshold", "0.1"))
            if not 0 <= threshold <= 1:
                raise DomainError(
                    "invalid_argument", "threshold must be between 0 and 1")
            event_type = parameters.get(
                "event_type", "top10_share_change_v1")
            if event_type != "top10_share_change_v1":
                raise DomainError("invalid_argument", "Unknown change event type")
            requested_target = parameters.get("target")
            if requested_target:
                requested_target = next(
                    (host for host in domains
                     if requested_target in {host, _domain_id(host)}),
                    None,
                ) or _host(requested_target)
                if not requested_target:
                    raise DomainError(
                        "invalid_argument", "A valid target domain is required")
                hosts = [requested_target]
            else:
                hosts = sorted(domains)
            rows = []
            before = [
                row for row in observations
                if row.reference.observed_at < split
            ]
            after = [
                row for row in observations
                if row.reference.observed_at >= split
            ]

            def measure(cohort, host):
                eligible = [
                    row for row in cohort if self._eligible(row, 10)
                ]
                hits = [
                    row for row in eligible
                    if (
                        position := self._best_target_position(row, host)
                    ) is not None and position <= 10
                ]
                return {
                    "numerator": len(hits),
                    "denominator": len(eligible),
                    "value": _ratio(len(hits), len(eligible)),
                    "evidence_ids": sorted({
                        row.reference.evidence_id for row in cohort
                    }),
                }

            for host in hosts:
                baseline, current = measure(before, host), measure(after, host)
                delta = (
                    None
                    if baseline["value"] is None or current["value"] is None
                    else round(current["value"] - baseline["value"], 6)
                )
                detected = (
                    None if delta is None
                    else abs(delta) >= float(threshold)
                )
                if requested_target is None and detected is not True:
                    continue
                rows.append({
                    "event_id": identity("change-event", {
                        "aggregation_version": AGGREGATION_VERSION,
                        "projection_revision": snap["projection_revision"],
                        "event_type": event_type,
                        "target_domain": host,
                        "split_at": utc_text(split),
                        "filters": filters,
                    }),
                    "event_type": event_type,
                    "target_domain": host,
                    "split_at": utc_text(split),
                    "baseline": baseline,
                    "current": current,
                    "delta": delta,
                    "threshold": str(threshold),
                    "detected": detected,
                    "direction": (
                        None if delta is None else
                        "improved" if delta > 0 else
                        "declined" if delta < 0 else "unchanged"
                    ),
                    "category": "data_observation_change",
                    "business_cause": {
                        "status": "not_covered",
                        "reason": (
                            "no_campaign_or_business_event_ledger_is_connected"),
                    },
                    "evidence_ids": sorted({
                        *baseline["evidence_ids"],
                        *current["evidence_ids"],
                    }),
                    "source_change_semantics": (
                        "fixed_window_observed_top10_share"),
                    "raw_content_change": "not_covered_use_whale_changes",
                })
            rows.sort(key=lambda row: (
                row["delta"] is None,
                -(abs(row["delta"]) if row["delta"] is not None else 0),
                row["event_id"],
            ))
            page, cursor = self._pagination(
                rows, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="deterministic_change_candidates",
                changes=page,
                next_cursor=cursor,
                universe=universe,
                total=len(rows),
                event_type=event_type,
                threshold=str(threshold),
                business_causality_not_claimed=True,
                raw_content_change="not_covered_use_whale_changes",
            )
        if kind == "topics":
            topics = self._topics(observations)
            page, cursor = self._pagination(
                topics, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="observed_query_topic_candidates",
                topics=page,
                next_cursor=cursor,
                universe=universe,
                total=len(topics),
                topic_basis="exact_normalized_observed_query",
                approved_topic_taxonomy=False,
                brand_relations_status="not_covered",
            )
        if kind == "topic-relations":
            topic, relations = self._topic_relations(observations, detail_id)
            page, cursor = self._pagination(
                relations, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="evidence_backed_query_topic_relations",
                topic=topic,
                relations=page,
                next_cursor=cursor,
                universe=universe,
                total=len(relations),
                relation_method="shared_observed_results_v1",
                causal_claim=False,
                brand_relations_status="not_covered",
            )
        if kind == "article-detail":
            requested_version = parameters.get("version", PROJECTION_VERSION)
            if requested_version != PROJECTION_VERSION:
                raise DomainError(
                    "incompatible_scope",
                    "Requested article fields do not match the selected projection version",
                    409,
                )
            articles = self._articles(observations)
            article = next(
                (row for row in articles
                 if detail_id in {row["article_id"], row["item_id"]}),
                None,
            )
            if article is None:
                raise DomainError("not_found", "No licensed article summary matches", 404)
            fields = _csv("fields", parameters.get("fields"), maximum=50)
            allowed_fields = {
                "title", "snippet", "target_url", "display_url", "domain",
                "publisher", "source_published_at", "body", "body_status",
                "body_reason", "evidence_id", "article_id",
            }
            if fields:
                if set(fields) - allowed_fields:
                    raise DomainError(
                        "invalid_argument", "fields contains an unsupported article field")
                selected = {
                    key: article[key] for key in fields
                }
                selected.setdefault("article_id", article["article_id"])
            else:
                selected = article
            return dict(
                common,
                quality_name="licensed_article_summary_only",
                article=selected,
                universe=universe,
                body_status="not_covered_by_typed_projection",
                body_text_not_inferred=True,
            )
        if kind in {
                "ai/answers", "ai-answer-detail", "ai/citations", "ai/visibility"}:
            channel = parameters.get("channel", "aio")
            if channel not in {"aio", "ai_mode"}:
                raise DomainError("invalid_argument", "channel must be aio or ai_mode")
            answers = self._ai_answers(observations, channel)
            if kind == "ai/answers":
                page, cursor = self._pagination(
                    answers, parameters, principal=principal, now=now,
                    binding=binding, evidence_expiry=evidence_expiry)
                return dict(
                    common,
                    quality_name="licensed_ai_feature_observations",
                    channel=channel,
                    answers=page,
                    next_cursor=cursor,
                    universe=universe,
                    total=len(answers),
                    answer_text_status="not_covered_by_typed_projection",
                )
            if kind == "ai-answer-detail":
                answer = next(
                    (row for row in answers if row["answer_id"] == detail_id),
                    None,
                )
                if answer is None:
                    raise DomainError("not_found", "No AI answer observation matches", 404)
                return dict(
                    common,
                    quality_name="licensed_ai_feature_observation",
                    answer=answer,
                    universe=universe,
                    answer_text_status="not_covered_by_typed_projection",
                )
            if kind == "ai/citations":
                citations = self._ai_citations(answers)
                page, cursor = self._pagination(
                    citations, parameters, principal=principal, now=now,
                    binding=binding, evidence_expiry=evidence_expiry)
                return dict(
                    common,
                    quality_name="explicit_ai_citation_occurrences",
                    channel=channel,
                    citations=page,
                    next_cursor=cursor,
                    universe=universe,
                    total=len(citations),
                    citation_visibility_only=True,
                    mention_status="not_covered",
                )
            brands = _csv(
                "brands", parameters.get("brands"), required=True, maximum=20)
            normalized_brands = []
            for brand in brands:
                host = _host(brand)
                if not host:
                    raise DomainError("invalid_argument", "brands contains an invalid domain")
                normalized_brands.append(host)
            visibility = self._ai_visibility(
                observations, sorted(set(normalized_brands)), channel)
            page, cursor = self._pagination(
                visibility, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="licensed_ai_visibility_components",
                channel=channel,
                visibility=page,
                next_cursor=cursor,
                universe=universe,
                total=len(visibility),
                brands=sorted(set(normalized_brands)),
                mention_status="not_covered",
                answer_text_status="not_covered_by_typed_projection",
                prompt_basis="exact_normalized_observed_query_not_prompt_taxonomy",
            )
        if kind == "news/observations":
            page, cursor = self._pagination(
                news_occurrences, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="deterministic_news_occurrences",
                news_observations=[
                    self._public_news_row(row) for row in page],
                next_cursor=cursor,
                universe=universe,
                total=len(news_occurrences),
                publication_time_policy=(
                    "only_explicit_source_published_at_never_inferred"),
            )
        if kind == "news/events":
            events = self._news_events(news_occurrences)
            page, cursor = self._pagination(
                events, parameters, principal=principal, now=now,
                binding=binding, evidence_expiry=evidence_expiry)
            return dict(
                common,
                quality_name="deterministic_news_event_candidates",
                news_events=page,
                next_cursor=cursor,
                universe=universe,
                total=len(events),
                cluster_method="exact_publisher_url_or_explicit_publisher_title",
                cluster_version=NEWS_CLUSTER_VERSION,
                heuristic_not_gold=True,
                event_truth_not_claimed=True,
            )
        if kind == "domains":
            rows = sorted(
                (self._domain_row(host, bucket, observations)
                 for host, bucket in domains.items()),
                key=lambda row: (
                    -(row["top10_share"]["value"] or 0),
                    row["domain_id"],
                ),
            )
            page, cursor = self._pagination(
                rows, parameters, principal=principal, now=now, binding=binding,
                evidence_expiry=evidence_expiry)
            return dict(common, domains=page, next_cursor=cursor,
                        universe=universe, total=len(rows),
                        domain_ranking_basis="observed_top10_share")
        if kind == "domain-detail":
            match = next(
                (row for row in (
                    self._domain_row(host, bucket, observations)
                    for host, bucket in domains.items()
                ) if detail_id in {row["domain"], row["domain_id"]}),
                None,
            )
            if match is None:
                raise DomainError("not_found", "No observed domain matches", 404)
            page_rows = [
                self._page_row(url, bucket, observations)
                for url, bucket in pages.items()
                if bucket["domain_host"] == match["domain"]
            ]
            return dict(common, domain=match, page_count=len(page_rows),
                        universe=universe)
        if kind == "pages":
            host = next(
                (domain for domain in domains
                 if detail_id in {domain, _domain_id(domain)}),
                None,
            )
            if host is None:
                raise DomainError("not_found", "No observed domain matches", 404)
            rows = sorted(
                (self._page_row(url, bucket, observations)
                 for url, bucket in pages.items()
                 if bucket["domain_host"] == host),
                key=lambda row: row["page_id"],
            )
            page, cursor = self._pagination(
                rows, parameters, principal=principal, now=now, binding=binding,
                evidence_expiry=evidence_expiry)
            return dict(common, domain=host, domain_id=_domain_id(host),
                        pages=page, next_cursor=cursor, universe=universe,
                        total=len(rows))
        if kind == "rankings":
            requested = _csv("domains", parameters.get("domains"), maximum=100)
            metric = parameters.get("metric", "top10_share")
            if metric not in RANKING_METRICS:
                raise DomainError("invalid_argument", "Unknown ranking metric")
            hosts = requested or sorted(domains)
            rows = []
            for host in hosts:
                host = _host(host)
                if not host:
                    raise DomainError("invalid_argument", "domains contains an invalid host")
                bucket = domains.get(host, {
                    "organic_occurrences": 0,
                    "unknown_position_occurrences": 0,
                    "urls": set(),
                    "best_by_run": {},
                    "runs": set(),
                    "feature_urls": set(),
                })
                item = self._domain_row(host, bucket, observations)
                value = (
                    item[metric]["value"] if metric.endswith("_share")
                    else item[metric]
                )
                item.update(rank_metric=metric, rank_value=value,
                            availability=(
                                "available" if value is not None else "not_observed"))
                rows.append(item)
            if metric == "median_position":
                rows.sort(key=lambda row: (
                    row["rank_value"] is None,
                    row["rank_value"] or 0,
                    row["domain_id"],
                ))
            else:
                rows.sort(key=lambda row: (
                    row["rank_value"] is None,
                    -(row["rank_value"] or 0),
                    row["domain_id"],
                ))
            page, cursor = self._pagination(
                rows, parameters, principal=principal, now=now, binding=binding,
                evidence_expiry=evidence_expiry)
            return dict(common, metric=metric, rankings=page,
                        next_cursor=cursor, universe=universe, total=len(rows),
                        weighting="equal_query_runs_no_traffic_weights",
                        not_search_volume=True)
        requested = _csv(
            "domains", parameters.get("domains"), required=True,
            maximum=MAX_COMPARISON_DOMAINS)
        metric = parameters.get("metric", "top10_share")
        if metric not in RANKING_METRICS:
            raise DomainError("invalid_argument", "Unknown comparison metric")
        if len(requested) < 2:
            raise DomainError("invalid_argument", "comparisons requires at least two domains")
        rows = []
        for raw_host in requested:
            host = _host(raw_host)
            if not host:
                raise DomainError("invalid_argument", "domains contains an invalid host")
            bucket = domains.get(host, {
                "organic_occurrences": 0,
                "unknown_position_occurrences": 0,
                "urls": set(),
                "best_by_run": {},
                "runs": set(),
                "feature_urls": set(),
            })
            item = self._domain_row(host, bucket, observations)
            value = (
                item[metric]["value"] if metric.endswith("_share")
                else item[metric]
            )
            rows.append({
                "domain_id": item["domain_id"],
                "domain": host,
                "metric": metric,
                "value": value,
                "numerator": (
                    item[metric]["numerator"] if metric.endswith("_share") else None),
                "denominator": (
                    item[metric]["denominator"] if metric.endswith("_share") else None),
                "interval": None,
                "interval_reason": (
                    INTERVAL_LIMITATION if metric.endswith("_share") else
                    "median_or_count_has_no_binomial_interval"),
                "recent_evidence": item["recent_evidence"],
                "recent_evidence_total": item["recent_evidence_total"],
                "recent_evidence_truncated": item[
                    "recent_evidence_truncated"],
            })
        differences = []
        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1:]:
                differences.append({
                    "left_domain_id": left["domain_id"],
                    "right_domain_id": right["domain_id"],
                    "metric": metric,
                    "left_minus_right": (
                        None if left["value"] is None or right["value"] is None
                        else round(left["value"] - right["value"], 6)),
                    "compatible_denominator": (
                        left["denominator"] == right["denominator"]),
                })
        return dict(common, metric=metric, domains=rows,
                    pairwise_differences=differences,
                    universe=universe, method="fixed_observed_set_descriptive",
                    causal_claim=False, not_user_traffic=True)
