"""Versioned, loss-aware export contract for existing public SERP records.

An operator-only legacy adapter, never a Whale reader or a collection trigger.
Objects may deduplicate; ordered occurrences and every request remain independent.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, parse_qsl

from .serp_fields import normalize_serp_url, public_serp_url, public_fields, URL_NORMALIZATION_VERSION

DATA_VERSION = 'google-public-data-1'
SOURCE_KEYS = {'google_web': 'organic_results', 'google_news': 'news_results',
               'google_trends': 'trending_searches', 'google_images': 'image_results'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def timestamp(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except (AttributeError, ValueError, TypeError, OverflowError):
        return None


def news_time(item):
    """RSS publication claim, not independently verified publisher page time."""
    raw, saved = item.get('date_raw'), item.get('date')
    provided = isinstance(saved, str) and bool(saved.strip())
    result = dict(raw=raw if isinstance(raw, str) else None,
                  saved=saved if isinstance(saved, str) else None,
                  field_provided=provided, published_at=None,
                  basis='rss_pubDate', publisher_page_verified=False)
    if not isinstance(raw, str) or not raw.strip():
        return {**result, 'status': 'unverified_missing_raw' if provided else 'missing'}
    try:
        parsed = parsedate_to_datetime(raw)
        rss = timestamp(parsed)
        normalized = timestamp(saved)
        if rss is None or normalized is None:
            return {**result, 'status': 'invalid'}
        delta = abs((datetime.fromisoformat(rss.replace('Z', '+00:00')) -
                     datetime.fromisoformat(normalized.replace('Z', '+00:00'))).total_seconds())
        if delta > 1:
            return {**result, 'status': 'mismatch'}
        return {**result, 'status': 'verified_rss', 'published_at': normalized}
    except (TypeError, ValueError, OverflowError):
        return {**result, 'status': 'invalid'}


def url_identity(value):
    if not isinstance(value, str) or not public_serp_url(value):
        return dict(original_url=None, normalized_url=None, url_id=None,
                    normalization_version=URL_NORMALIZATION_VERSION, status='invalid_or_missing')
    safe = public_fields(value)
    original_parts, safe_parts = urlsplit(value), urlsplit(safe or '')
    if (safe is None or
            parse_qsl(original_parts.query, keep_blank_values=True) != parse_qsl(safe_parts.query, keep_blank_values=True)
            or original_parts.fragment != safe_parts.fragment):
        return dict(original_url=None, normalized_url=None, url_id=None,
                    normalization_version=URL_NORMALIZATION_VERSION, status='redacted_sensitive_url')
    normalized = normalize_serp_url(value)
    return dict(original_url=value, normalized_url=normalized,
                url_id=hashlib.sha256(normalized.encode()).hexdigest(),
                normalization_version=URL_NORMALIZATION_VERSION, status='available')


def _positive(value):
    return value if type(value) is int and value > 0 else None


def normalize_request(row):
    """No source times, effective device/location, license or Whale proof are inferred."""
    source, rid = row['source'], str(row['request_id'])
    if source not in SOURCE_KEYS:
        raise ValueError('unsupported Google data source')
    response = row['response'] if isinstance(row.get('response'), dict) else {}
    parameters = response.get('search_parameters')
    parameters = parameters if isinstance(parameters, dict) else {}
    # Conditions are exported separately from search_parameters. Apply the
    # same nested redaction here, so a structured location cannot bypass it.
    parameters = public_fields(parameters)
    metadata = response.get('search_metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    times = metadata.get('collection_times')
    times = times if isinstance(times, dict) else {}
    conditions = {k: parameters.get(k) for k in ('gl', 'hl', 'device', 'location', 'start', 'num')}
    conditions.update(page=row['page'], source=source, login_state='unknown',
                      location_precision='unknown', effective_conditions_status='unverified')
    variant = digest(dict(query=row['query'], conditions=conditions,
                          parameters=public_fields(parameters), version=DATA_VERSION))
    values = response.get(SOURCE_KEYS[source])
    values = values if isinstance(values, list) else []
    occurrences, topics, invalid = [], [], []
    feed_positions=(source in ('google_news','google_trends') and
                    response.get('feed_observation_version')=='google-feed-observations-1')
    if feed_positions:
        issues=response.get('feed_parse_issues')
        if isinstance(issues,list):
            for issue in issues:
                if isinstance(issue,dict):
                    invalid.append(public_fields(issue))
    if response.get(SOURCE_KEYS[source]) is not None and not isinstance(response.get(SOURCE_KEYS[source]), list):
        invalid.append(dict(reason='result_container_not_array', source_value=public_fields(response[SOURCE_KEYS[source]])))

    def append(item, ordinal, child=0, parent=None):
        if not isinstance(item, dict):
            invalid.append(dict(ordinal=ordinal, child_ordinal=child, reason='non_object', source_value=public_fields(item)))
            return
        kind = 'organic' if source == 'google_web' else 'image' if source == 'google_images' else 'news_index'
        identity = digest([DATA_VERSION, source, rid, ordinal, child])
        link = url_identity(item.get('link'))
        if kind == 'image' and link['status'] == 'available':
            link.update(normalized_url=item['link'],url_id=hashlib.sha256(item['link'].encode()).hexdigest(),normalization_version='image-exact-url-1')
        wrapper = urlsplit(link['normalized_url'] or '').hostname == 'news.google.com'
        # Preserve fields outside the minimal vocabulary, excluding transport secrets.
        occurrences.append(dict(occurrence_id=identity, request_id=rid, parent_topic_id=parent,
            ordinal=ordinal, child_ordinal=child, result_type=kind, url=link,
            title=item.get('title'), snippet=item.get('snippet'),
            organic_position=_positive(item.get('position')) if kind == 'organic' else None,
            source_absolute_rank=_positive(item.get('rank_absolute')),
            feed_position=_positive(item.get('feed_position')),
            article_url=None if wrapper or kind == 'image' else link['original_url'],
            publisher_site_url=url_identity(item.get('publisher_url'))['original_url'],
            resolution_status='unresolved_wrapper' if wrapper else link['status'],
            publication=news_time(item) if kind == 'news_index' else None,
            source_fields=public_fields(item), source_record_hash=digest(public_fields(item)),
            **(dict(image_url=link['original_url'],source_page_url=url_identity(item.get('source_url'))['original_url'],
                    thumbnail_url=url_identity(item.get('thumbnail'))['original_url'],width=item.get('width'),height=item.get('height'),
                    upstream_rank=item.get('position'),observed_order=item.get('observed_order'),
                    rank_basis='upstream_image_grid_order',media_downloaded=False) if kind == 'image' else {})))

    for ordinal, item in enumerate(values, 1):
        if feed_positions and isinstance(item,dict):
            ordinal=_positive(item.get('feed_position')) or ordinal
        if source != 'google_trends':
            append(item, ordinal)
        elif isinstance(item, dict):
            tid = digest([DATA_VERSION, source, rid, 'topic', ordinal])
            topics.append(dict(topic_observation_id=tid, ordinal=ordinal,
                topic=item.get('keyword'), approx_traffic_label=item.get('approx_traffic'),
                measurement_nature='rounded_lower_bound' if str(item.get('approx_traffic') or '').endswith('+') else 'unknown',
                numeric_trend_series=None, source_fields=public_fields(item),
                source_record_hash=digest(public_fields(item))))
            articles = item.get('articles')
            if articles is not None and not isinstance(articles, list):
                invalid.append(dict(ordinal=ordinal, reason='articles_not_array', source_value=public_fields(articles)))
            for child, article in enumerate(articles if isinstance(articles, list) else [], 1):
                if feed_positions and isinstance(article,dict):
                    child=_positive(article.get('source_child_ordinal')) or child
                append(article, ordinal, child, tid)
        else:
            invalid.append(dict(ordinal=ordinal, child_ordinal=0, reason='non_object', source_value=public_fields(item)))
    classification = row['classification']
    availability = ('available' if classification == 'results' else
                    'true_zero' if classification == 'empty' else 'source_error')
    return dict(contract_version=DATA_VERSION, request_id=rid, source=source,
        query=row['query'], query_variant_id=variant, query_conditions=conditions,
        classification=classification, availability=availability,
        status_result_conflict=classification != 'results' and bool(values),
        completed_at=timestamp(row['completed_at']), source_observed_at=timestamp(times.get('source_observed_at')),
        scheduled_at=timestamp(times.get('scheduled_at')), requested_at=timestamp(times.get('requested_at')),
        received_at=timestamp(times.get('received_at')),
        cache_status=metadata.get('cache_status') or 'unknown',
        cache_original_observed_at=None, source_schema_version=response.get('schema_version') or 'unknown',
        parser_version=response.get('parser_version') or 'unknown',
        occurrences=occurrences, topics=topics, invalid_records=invalid,
        features=public_fields(response.get('serp_features')),
        module_status=public_fields(response.get('module_status')),
        search_parameters=public_fields(parameters), source_metadata=public_fields(metadata),
        response_extensions=public_fields({k: v for k, v in response.items() if k != SOURCE_KEYS[source]}),
        source_response_hash=digest(public_fields(response)),
        rights_status='legacy_scope_only_not_new_source_admission',
        provenance='legacy_serp_requests_not_whale_readback', whale_readback='not_connected')
