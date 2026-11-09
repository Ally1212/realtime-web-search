"""Opt-in PRD field projection of saved SERP facts; no fetching or admission.

The legacy normalizer and immutable Whale envelopes remain byte-compatible.
Unknown source facts stay unknown; local observation time, feature links and
provider versions never masquerade as publication time, citations or Google API
versions. This projection can be recreated entirely from the saved response.
"""
from __future__ import annotations

import math
from urllib.parse import urlsplit

from .google_data import digest, normalize_request, timestamp, url_identity
from .serp_fields import public_fields, FEATURE_MODULES

FIELD_VERSION = 'google-prd-fields-1'
CONTRACT_CHOICES = ('legacy', 'prd-v1')


def validate_field_contract(value):
    if value not in CONTRACT_CHOICES:
        from .google_domain import DomainError
        raise DomainError('invalid_argument', 'Unsupported field_contract; use legacy or prd-v1')
    return value


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _number(value, minimum=0):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= minimum else None


def _integer(value, minimum=0):
    return value if type(value) is int and value >= minimum else None


def _boolean(value):
    return value if type(value) is bool else None


def _field(value, basis, *, missing='not_recorded', derived=False):
    return dict(value=value, availability='unknown' if value is None else 'available',
                basis=basis if value is not None else None,
                measurement='unknown' if value is None else 'derived' if derived else 'observed',
                missing_reason=missing if value is None else None)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _safe_url(value):
    return url_identity(value)['original_url']


def _redirect_evidence(value):
    safe = _safe_url(value)
    if safe:
        return safe
    # Google redirect URLs are invalid as deduplicated target objects, but
    # remain legitimate source evidence. This does not resolve/follow them.
    if not isinstance(value, str) or public_fields(value) != value:
        return None
    try:
        p = urlsplit(value)
        if p.scheme in {'http','https'} and p.hostname in {'google.com','www.google.com'} and p.path in {'/url','/goto'}:
            if p.port is None or 1 <= p.port <= 65535:
                return value
    except ValueError:
        pass
    return None


def _url_rows(value, role):
    if not isinstance(value, list):
        return []
    rows = []
    for order, item in enumerate(value, 1):
        raw = item.get('url') or item.get('link') if isinstance(item, dict) else item
        url = _safe_url(raw)
        if url:
            rows.append(dict(ordinal=order, role=role, url=url,
                             title=item.get('title') if isinstance(item, dict) else None))
    return rows


def _features(response, request_id):
    result, invalid, seen = [], [], set()

    def append(value, path, index, type_hint=None):
        if not isinstance(value, dict):
            invalid.append(dict(source_path=path, reason='non_object', source_value=public_fields(value)))
            return
        safe = public_fields(value)
        seen.add(digest(safe))
        position = _mapping(value.get('position'))
        # Array order is observable; it is not a measured SERP block position.
        block = _integer(value.get('block_order'))
        if block is None:
            block = _integer(position.get('absolute'))
        expanded = _text(value.get('expanded_state'))
        if expanded is None and type(value.get('expanded')) is bool:
            expanded = 'expanded' if value['expanded'] else 'collapsed'
        links = _url_rows(value.get('links'), 'target')
        citations = _url_rows(value.get('citations'), 'citation')
        citation_source = value.get('citations')
        citation_availability = ('unknown' if not isinstance(citation_source, list) else
            'true_zero' if not citation_source else 'available' if citations else 'source_error')
        children = []
        for child_order, child in enumerate(value.get('items') if isinstance(value.get('items'), list) else [], 1):
            if not isinstance(child, dict):
                invalid.append(dict(source_path=path + '.items', ordinal=child_order,
                                    reason='non_object', source_value=public_fields(child)))
                continue
            answer = _text(child.get('answer'))
            children.append(dict(child_id=digest([FIELD_VERSION, request_id, path, index, child_order]),
                ordinal=child_order, title=child.get('title'), text=child.get('text'),
                answer_text=answer, answer_availability='available' if answer else 'unknown',
                missing_reason=None if answer else 'no_separate_source_answer',
                target_url=_safe_url(child.get('url') or child.get('link')),
                source_fields=public_fields(child)))
        presence = _boolean(value.get('presence'))
        if presence is None:
            presence = True  # A saved block was actually returned, not inferred absent.
        result.append(dict(feature_id=digest([FIELD_VERSION, request_id, path, index]),
            request_id=request_id, native_id=_text(value.get('id')),
            feature_type=_text(value.get('type')) or type_hint or 'unknown',
            source_path=path, observed_order=index, presence=presence,
            block_order=block, expanded_state=expanded,
            parent_block_id=_text(value.get('parent_block_id')),
            channel=_text(value.get('channel')), title=value.get('title'), text=value.get('text'),
            extracted_at=timestamp(value.get('extracted_at')),
            target_urls=[r['url'] for r in links], citation_urls=[r['url'] for r in citations],
            citation_availability=citation_availability,
            citations_partial=len(citations) != len(citation_source) if isinstance(citation_source, list) else None,
            url_observations=links + citations,
            children=children,
            source_result_ids=value.get('source_result_ids') if isinstance(value.get('source_result_ids'), list) else None,
            source_record_hash=digest(safe), source_fields=safe,
            missing_reason={k:'not_provided' for k,v in dict(block_order=block,
                expanded_state=expanded, channel=_text(value.get('channel'))).items() if v is None}))

    features = response.get('serp_features')
    if isinstance(features, list):
        for index, value in enumerate(features, 1):
            append(value, 'serp_features', index)
    elif features is not None:
        invalid.append(dict(source_path='serp_features', reason='container_not_array', source_value=public_fields(features)))
    # Some providers expose only named modules. Do not double-count the same
    # block copied into both a named module and serp_features by our parser.
    passthrough_seen = set(seen)
    for name in dict.fromkeys(FEATURE_MODULES.values()):
        values = response.get(name)
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values, 1):
            if isinstance(value, dict) and digest(public_fields(value)) in passthrough_seen:
                continue
            append(value, name, index, name)
    return result, invalid


def adapt_request_fields(row):
    """Add a versioned supplemental projection without changing any legacy fact."""
    record = normalize_request(row)
    response = _mapping(public_fields(row.get('response')))
    params = _mapping(response.get('search_parameters'))
    metadata = _mapping(response.get('search_metadata'))
    upstream = _mapping(response.get('upstream_metadata'))
    upstream_response_meta = _mapping(_mapping(response.get('upstream_response')).get('meta'))
    feed = _mapping(response.get('feed_metadata'))
    coverage = _mapping(response.get('coverage'))
    rid = record['request_id']
    cache_hit = _boolean(metadata.get('cache_hit'))
    if cache_hit is None:
        status = metadata.get('cache_status')
        if isinstance(status, str) and status.upper() in {'HIT', 'MISS'}:
            cache_hit = status.upper() == 'HIT'
    received = record['received_at']
    observed = received or record['completed_at']
    partial = _boolean(coverage.get('partial'))
    if record['invalid_records'] or response.get('invalid_image_results'):
        partial = True
    features, feature_issues = _features(response, rid)
    methods = {i['source_fields'].get('collection_method') for i in record['occurrences']
               if isinstance(i['source_fields'].get('collection_method'), str)}
    methods.update(t['source_fields'].get('collection_method') for t in record['topics']
                   if isinstance(t['source_fields'].get('collection_method'), str))
    method = next(iter(methods)) if len(methods) == 1 else None
    method_derived = False
    provider = _text(row.get('provider')) or _text(metadata.get('provider'))
    if method is None and provider == 'openserp' and row['source'] == 'google_web':
        method, method_derived = 'openserp_google_web', True
    run = {
        'entity_id': _field('legacy-serp-run:' + rid, 'request_id namespace', derived=True),
        'entity_kind': _field('observation_run', 'saved request schema', derived=True),
        'provider_namespace': _field(provider, 'saved provider'),
        'native_request_id': _field(_text(upstream.get('request_id')) or _text(upstream_response_meta.get('request_id')), 'saved upstream metadata request_id'),
        'source_ref': _field('sha256:' + record['source_response_hash'], 'filtered saved response hash', derived=True),
        'source_api_version': _field(_text(response.get('source_api_version')), 'source_api_version'),
        'provider_version': _field(_text(metadata.get('upstream_version')) or _text(upstream.get('version')), 'saved upstream version'),
        'source_parser_version': _field(_text(response.get('parser_version')), 'parser_version'),
        'field_schema_version': _field(_text(response.get('field_schema_version')), 'field_schema_version'),
        'collection_method': _field(method, 'saved provider+source' if method_derived else 'consistent saved result collection_method', derived=method_derived),
        'observed_at': _field(observed, 'received_at' if received else 'completed_at proxy', derived=True),
        'source_observed_at': _field(record['source_observed_at'], 'collection_times.source_observed_at'),
        'scheduled_at': _field(record['scheduled_at'], 'collection_times.scheduled_at'),
        'requested_at': _field(record['requested_at'], 'collection_times.requested_at'),
        'received_at': _field(received, 'collection_times.received_at'),
        'ingested_at': _field(None, None, missing='platform_ingestion_not_verified'),
        'requested_country': _field(_text(params.get('gl')), 'search_parameters.gl'),
        'search_engine': _field(_text(params.get('engine')), 'search_parameters.engine'),
        'requested_surface': _field(_text(params.get('surface')) or _text(params.get('vertical')), 'explicit request surface/vertical'),
        'channel': _field(_text(metadata.get('channel')), 'explicit source channel'),
        'requested_language': _field(_text(params.get('hl')), 'search_parameters.hl'),
        'source_language': _field(_text(feed.get('language')) or _text(metadata.get('source_language')), 'explicit source language'),
        'query_language': _field(_text(params.get('query_language')), 'explicit query language'),
        'device': _field(_text(params.get('device')), 'search_parameters.device'),
        'location': _field(params.get('location'), 'search_parameters.location'),
        'location_precision': _field(_text(params.get('location_precision')), 'explicit location precision'),
        'login_state': _field(_text(params.get('login_state')), 'explicit login state'),
        'safe_search': _field(params.get('safe_search'), 'search_parameters.safe_search'),
        'observed_page': _field(_integer(row.get('page'), 1), 'saved request page'),
        'requested_offset': _field(_integer(params.get('start')), 'search_parameters.start'),
        'requested_item_limit': _field(_integer(params.get('num')), 'search_parameters.num'),
        'returned_item_count': _field(len(record['occurrences']) if record['availability'] != 'source_error' else None,
                                      'parsed successful response', missing='source_error', derived=True),
        'returned_topic_count': _field(len(record['topics']) if record['availability'] != 'source_error' else None,
                                       'parsed successful response', missing='source_error', derived=True),
        'stored_occurrence_count': _field(len(record['occurrences']), 'saved occurrence count', derived=True),
        'stored_topic_count': _field(len(record['topics']), 'saved topic count', derived=True),
        'cache_hit': _field(cache_hit, 'explicit upstream cache flag/status'),
        'cache_age_seconds': _field(_number(metadata.get('cache_age_seconds')), 'explicit cache age'),
        'cache_original_observed_at': _field(timestamp(metadata.get('cache_original_observed_at')), 'explicit original cache time'),
        'is_partial': _field(partial, 'source coverage or invalid record evidence', derived=bool(record['invalid_records'])),
        'is_truncated': _field(_boolean(coverage.get('truncated')), 'explicit source truncation'),
        'source_timezone': _field(_text(metadata.get('source_timezone')), 'explicit source timezone'),
        'billable_units': _field(_number(metadata.get('billable_units')), 'explicit provider billable units'),
        'cost': _field(None, None, missing='no_verified_cost_currency_unit_contract'),
    }
    items = []
    for occurrence in record['occurrences']:
        source = occurrence['source_fields']
        publication = occurrence['publication'] or {}
        items.append(dict(occurrence_id=occurrence['occurrence_id'],
            native_id=_text(source.get('upstream_result_id')) or _text(source.get('guid')),
            result_type=occurrence['result_type'],
            provider_rank_group=_integer(source.get('provider_rank_group'), 1),
            provider_rank_absolute=occurrence['source_absolute_rank'],
            organic_position=occurrence['organic_position'],
            page=_integer(source.get('page'), 1) or _integer(row.get('page'), 1),
            offset=_integer(source.get('offset')),
            parent_block_id=_text(source.get('parent_block_id')),
            display_url=_text(source.get('displayed_link')),
            target_url=occurrence['url']['original_url'],
            url_normalization_version=occurrence['url']['normalization_version'],
            registrable_domain=_text(source.get('registrable_domain')),
            subdomain=_text(source.get('subdomain')),
            canonical_url=_safe_url(source.get('canonical_url')),
            redirect_url=_redirect_evidence(source.get('redirect_link')),
            publisher_article_url=occurrence['article_url'] if occurrence['result_type']=='news_index' else None,
            sponsored_label=_text(source.get('sponsored_label')),
            sitelinks=source.get('sitelinks'),
            source_published_at=publication.get('published_at'),
            publication_basis=publication.get('basis') if publication.get('published_at') else None,
            author=_text(source.get('author')),
            source_language=_text(source.get('source_language')),
            result_language=_text(source.get('language')),
            detected_language=_text(source.get('detected_language')),
            content_semantics='image_metadata' if occurrence['result_type']=='image' else
                'serp_snippet' if occurrence['result_type']=='organic' else 'news_index',
            publisher_page_verified=False,
            missing_reason={k:'not_provided' for k in ('author','provider_rank_group','parent_block_id',
                'registrable_domain','subdomain','canonical_url','sponsored_label') if source.get(k) is None}))
    topics = [dict(topic_observation_id=topic['topic_observation_id'],
                   native_topic_id=_text(topic['source_fields'].get('provider_topic_id')),
                   label=topic['topic'], topic_kind='trending_now_label_not_provider_topic_id',
                   geo=topic['source_fields'].get('geo'),
                   traffic_value=_number(topic['source_fields'].get('traffic_value')),
                   traffic_unit=topic['source_fields'].get('traffic_unit'),
                   traffic_is_lower_bound=_boolean(topic['source_fields'].get('traffic_is_lower_bound')),
                   numeric_trend_series=None,
                   numeric_series_availability='not_covered') for topic in record['topics']]
    native_items = {}
    for item in items:
        if item['native_id']:
            native_items.setdefault(item['native_id'], []).append(item['occurrence_id'])
    for feature in features:
        matches = [oid for native in feature['source_result_ids'] or [] if isinstance(native, str)
                   for oid in native_items.get(native, [])]
        feature['matched_occurrence_ids'] = list(dict.fromkeys(matches))
    record['field_adaptation'] = dict(version=FIELD_VERSION,
        scope='saved_legacy_field_projection_not_formal_source_or_whale_admission',
        request_reuse_scope='origin_request_not_campaign_cache_associations',
        run=run, items=items, topics=topics, features=features, invalid_features=feature_issues,
        module_status=public_fields(response.get('module_status')),
        feature_absence_inference=False,
        rights=dict(status=record['rights_status'], source_admission_verified=False,
                    license_id=None, allowed_uses=None, retention_deadline=None),
        legacy_identity_preserved=True)
    return record
