"""Bounded Google image metadata collection; never download result media."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit, parse_qsl

import requests
import psycopg

from .free_google import GoogleBlocked, GoogleTransport, SearchResult
from .serp_fields import public_fields, public_serp_url, field_status

VERSION = 'google-images-1'
LIMIT = 20


def image_result(item, *, page=1, rank=1):
    image = item.get('image') if isinstance(item.get('image'), dict) else {}
    source = item.get('source') if isinstance(item.get('source'), dict) else {}
    def positive(value):
        return value if type(value) is int and value > 0 else None
    def url(value):
        if not isinstance(value,str) or not public_serp_url(value):return None
        safe=public_fields(value)
        if not isinstance(safe,str):return None
        original,filtered=urlsplit(value),urlsplit(safe)
        if (parse_qsl(original.query,keep_blank_values=True)!=parse_qsl(filtered.query,keep_blank_values=True)
                or original.fragment!=filtered.fragment):return None
        return value
    original = url(image.get('url') or item.get('image_url') or item.get('original') or item.get('url'))
    row = dict(type='image', position=positive(item.get('rank')), rank_absolute=None,
        observed_order=rank, rank_basis='upstream_image_grid_order', page=page,
        title=item.get('title') if isinstance(item.get('title'), str) and item['title'] != 'No title' else None,
        link=original, image_url=original,
        thumbnail=url(image.get('thumbnail') or item.get('thumbnail') or item.get('thumbnail_url')),
        source_url=url(source.get('page_url') or item.get('source_url')),
        source_host=source.get('domain') or item.get('domain'),
        width=positive(image.get('width') or item.get('width')),
        height=positive(image.get('height') or item.get('height')),
        mime_type=None, snippet=item.get('snippet') if isinstance(item.get('snippet'), str) else None,
        upstream_result_id=item.get('id'), source_fields=public_fields(item),
        collection_method='openserp_google_images', media_downloaded=False)
    row['field_status'] = field_status(row, ('observed_order', 'rank_basis', 'media_downloaded'))
    return public_fields(row)


def fetch_page(transport, query, page, proxy_url, proxy_key):
    if page != 1:
        raise GoogleBlocked('images_depth_not_supported')
    transport.local.last_serp_response = {}
    transport.local.last_evidence = {}
    request_id = str(uuid4())
    params = dict(text=query, limit=LIMIT, start=0,
                  lang='ZH' if transport.language == 'zh' else 'EN',
                  region=transport.region, format='json')
    headers = {'Accept':'application/json','X-Request-ID':request_id}
    if proxy_url:
        headers.update({'X-Proxy-URL':proxy_url,
                        'X-Proxy-Session-ID':hashlib.sha256((proxy_key or proxy_url).encode()).hexdigest()})
    else:
        headers['X-Use-Proxy'] = 'direct'
    session = requests.Session()
    session.trust_env = False
    response = None
    raw = b''
    payload = None
    endpoint = transport.openserp_url.rstrip('/') + '/google/image'
    try:
        response = session.get(endpoint, params=params, headers=headers,
            timeout=min(90, transport.openserp_request_timeout_seconds), allow_redirects=False)
        raw = response.content
        if len(raw) > 64 * 1024 * 1024:
            raise GoogleBlocked('images_response_too_large')
        try:
            payload = response.json()
        except ValueError:
            raise GoogleBlocked('openserp_invalid_response') from None
        if response.status_code != 200:
            raise GoogleTransport._openserp_error(response.status_code, payload)
        if (not isinstance(payload, dict) or not isinstance(payload.get('query'), dict)
                or payload['query'].get('text') != query
                or payload['query'].get('engines_requested') != ['google']
                or not isinstance(payload.get('meta'), dict)
                or payload['meta'].get('engines_failed') != []
                or payload['meta'].get('request_id') != request_id
                or response.headers.get('X-Request-ID') != request_id
                or not payload['meta'].get('version')
                or not isinstance(payload.get('results'), list)
                or not isinstance(payload.get('pagination'), dict)
                or response.headers.get('X-Fallback-Engine')
                or str(response.headers.get('X-Cache','')).upper() == 'HIT'):
            raise GoogleBlocked('openserp_invalid_response')
        rows, invalid = [], []
        for ordinal, item in enumerate(payload['results'], 1):
            if not isinstance(item, dict) or item.get('engine') != 'google' or item.get('type') != 'image':
                raise GoogleBlocked('images_unexpected_result_type')
            row = image_result(item, rank=ordinal)
            if row['image_url']:
                rows.append(row)
            else:
                invalid.append(dict(observed_order=ordinal, reason='invalid_or_sensitive_image_url', source_fields=public_fields(item)))
        envelope = dict(schema_version='google-serp-v1', field_schema_version=VERSION,
            source='google_images', search_parameters=dict(engine='google',vertical='images',
                q=query,hl=params['lang'].lower(),gl=params['region'].lower(),start=0,num=LIMIT,
                device=None,location=None), image_results=rows, organic_results=[],
            invalid_image_results=invalid, upstream_response=public_fields(payload),
            pagination=public_fields(payload['pagination']),
            coverage=dict(requested_limit=LIMIT,returned=len(rows),invalid=len(invalid),
                partial=bool(invalid),depth='first_page_only'),
            capabilities=dict(image_results='supported',media_download='disabled'))
        transport.local.last_serp_response = envelope
        if invalid and not rows:
            raise GoogleBlocked('images_parse_failure')
        transport._openserp_evidence(response,raw,'results' if rows else 'empty',endpoint,payload)
        return [SearchResult(row['image_url'],row['title'] or '',('google_images',),row) for row in rows]
    except requests.RequestException:
        raise GoogleBlocked('openserp_unavailable') from None
    finally:
        if not transport.last_evidence:
            transport._openserp_evidence(response,raw,'error',endpoint,payload)
        if response is not None:
            response.close()
        session.close()


def collect_images(discovery, store, serp, campaign_id, queries, config):
    """One bounded page/query, isolated cache and global image concurrency=1."""
    results, errors = [], []
    started = time.monotonic()
    guard = psycopg.connect(config.database_url,autocommit=True)
    if not guard.execute('SELECT pg_try_advisory_lock(20261106,1)').fetchone()[0]:
        guard.close()
        return [], ['images_busy']
    try:
        for query in queries:
            budget=discovery.task_options.get('max_successful_searches')
            if budget:
                from .serp_console import ConsoleStore
                if ConsoleStore(config.database_url).task_usage(campaign_id)['successful'] >= budget:
                    break
            if discovery.should_continue and not discovery.should_continue():
                break
            key = hashlib.sha256(str((VERSION,query,discovery.transport.region,discovery.language,LIMIT,campaign_id if discovery.task_options.get('customer_private') else 'public')).encode()).hexdigest()
            cached = serp.cached_feed(key,campaign_id)
            if cached:
                results.extend(SearchResult(r['image_url'],r.get('title') or '',('google_images',),r)
                               for r in cached.get('image_results',[]))
                continue
            # Atomic source throttles/proxy scheduling and request persistence
            # remain owned by the existing discovery attempt implementation.
            discovery.transport.local.last_evidence = {}
            try:
                found = discovery._attempt('openserp',query,1,vertical='images')
                results.extend(found)
                rid = getattr(discovery._thread_state,'last_serp_request_id',None)
                if rid:
                    serp.cache_feed(key,rid,min(config.google_web_query_cache_seconds,3600))
            except GoogleBlocked as error:
                errors.append(error.reason)
            # Stop before the five-minute global lease could expire.
            if time.monotonic() - started > 180 and query != queries[-1]:
                errors.append('images_batch_time_limit')
                break
    finally:
        guard.close()  # Session-scoped advisory lock releases even on failure.
    return results, errors
