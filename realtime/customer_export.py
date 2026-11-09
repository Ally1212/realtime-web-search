"""Lossless versioned customer CSV and bounded snapshot downloads."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import zipfile

from .google_domain import canonical_json
from .product import ProductError

FULL_COLUMNS = (
    'record_type', 'request_id', 'occurrence_id', 'topic_id', 'feature_id',
    'source', 'query', 'classification', 'availability', 'completed_at',
    'requested_at', 'received_at', 'source_observed_at', 'country', 'language',
    'page', 'title', 'link', 'normalized_url', 'url_id', 'snippet',
    'organic_position', 'source_absolute_rank', 'feed_position', 'ordinal',
    'child_ordinal', 'parent_topic_id', 'publisher', 'published_at', 'date_raw',
    'publication_basis', 'resolution_status', 'author', 'canonical_url',
    'display_url', 'redirect_url', 'image_url', 'source_page_url', 'thumbnail_url',
    'width', 'height', 'observed_order', 'media_downloaded', 'is_new_url',
    'topic', 'approx_traffic', 'traffic_value', 'traffic_is_lower_bound',
    'feature_type', 'target_urls_json', 'citation_urls_json', 'children_json',
    'source_fields_json', 'field_adaptation_json', 'record_json',
    'provider', 'source_schema_version', 'parser_version', 'source_response_hash',
    'query_conditions_json', 'source_metadata_json', 'module_status_json',
    'rating', 'reviews', 'sitelinks_json', 'rich_snippet_json', 'favicon',
    'publisher_site_url', 'field_status_json', 'collection_method',
    'picture', 'picture_source', 'geo', 'traffic_unit',
)
_exports = threading.BoundedSemaphore(2)


def _safe(value):
    if isinstance(value, (dict, list)):
        value = canonical_json(value)
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def full_rows(record):
    common = {k: record.get(k) for k in ('request_id', 'source', 'query', 'classification',
                                        'availability', 'completed_at', 'requested_at',
                                        'received_at', 'source_observed_at')}
    params = record.get('query_conditions') or {}
    common.update(country=params.get('gl'), language=params.get('hl'), page=params.get('page'))
    adaptation = record.get('field_adaptation') or {}
    common.update(provider=(record.get('source_metadata') or {}).get('provider'),
                  source_schema_version=record.get('source_schema_version'), parser_version=record.get('parser_version'),
                  source_response_hash=record.get('source_response_hash'))
    yield dict(common, record_type='request', record_json=canonical_json(record),
               field_adaptation_json=adaptation.get('run'), query_conditions_json=params,
               source_metadata_json=record.get('source_metadata'), module_status_json=record.get('module_status'))
    enhanced = {i['occurrence_id']: i for i in adaptation.get('items', [])}
    for item in record['occurrences']:
        source = item['source_fields']
        extra = enhanced.get(item['occurrence_id'], {})
        pub = item.get('publication') or {}
        values = {k: item.get(k) for k in FULL_COLUMNS}
        values.update({k: v for k, v in common.items()})
        values.update(record_type='result', link=item['url']['original_url'],
                      normalized_url=item['url']['normalized_url'], url_id=item['url']['url_id'],
                      publisher=source.get('publisher'), published_at=pub.get('published_at'),
                      date_raw=source.get('date_raw'), publication_basis=pub.get('basis'),
                      author=source.get('author'), canonical_url=source.get('canonical_url'),
                      rating=source.get('rating'), reviews=source.get('reviews'),
                      sitelinks_json=source.get('sitelinks'), rich_snippet_json=source.get('rich_snippet'),
                      favicon=source.get('favicon'), publisher_site_url=item.get('publisher_site_url'),
                      field_status_json=source.get('field_status'), collection_method=source.get('collection_method'),
                      display_url=source.get('displayed_link'), redirect_url=source.get('redirect_link'),
                      thumbnail_url=item.get('thumbnail_url') or source.get('thumbnail'),
                      is_new_url=source.get('is_new_url'), source_fields_json=source,
                      field_adaptation_json=extra, record_json=canonical_json(item))
        yield values
    enhanced_topics = {i['topic_observation_id']: i for i in adaptation.get('topics', [])}
    for topic in record['topics']:
        source = topic['source_fields']
        yield dict(common, record_type='topic', topic_id=topic['topic_observation_id'],
                   ordinal=topic['ordinal'], topic=topic['topic'],
                   approx_traffic=topic['approx_traffic_label'], traffic_value=source.get('traffic_value'),
                   traffic_is_lower_bound=source.get('traffic_is_lower_bound'),
                   picture=source.get('picture'), picture_source=source.get('picture_source'),
                   geo=source.get('geo'), traffic_unit=source.get('traffic_unit'),
                   source_fields_json=source, field_adaptation_json=enhanced_topics.get(topic['topic_observation_id']),
                   record_json=canonical_json(topic))
    for feature in adaptation.get('features', []):
        yield dict(common, record_type='feature', feature_id=feature['feature_id'],
                   feature_type=feature['feature_type'], title=feature.get('title'),
                   target_urls_json=feature['target_urls'], citation_urls_json=feature['citation_urls'],
                   children_json=feature['children'], field_adaptation_json=feature,
                   source_fields_json=feature['source_fields'], record_json=canonical_json(feature))


def write_csv(stream, records):
    writer = csv.DictWriter(stream, fieldnames=FULL_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for record in records:
        for row in full_rows(record):
            # JSON cells preserve exact values; ordinary display cells are formula-safe.
            writer.writerow({k: canonical_json(v) if isinstance(v, (dict, list)) else
                             v if k.endswith('_json') or k == 'record_json' else _safe(v)
                             for k, v in row.items()})


def export_record(handler, record, fmt):
    if fmt == 'jsonl':
        handler.send_body((canonical_json(record) + '\n').encode(), 'application/x-ndjson; charset=utf-8')
    elif fmt == 'csv':
        output = io.StringIO()
        write_csv(output, [record])
        handler.send_body(('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8')
    else:
        raise ProductError('invalid_format', '仅支持 csv、jsonl')


def snapshot_download(handler, store, account_id, parameters):
    from .customer_data import bounded_integer, selection
    from .google_data_export import export_public_data, verify_bundle
    p = dict(parameters)
    fmt = p.pop('format', 'zip')
    maximum = bounded_integer(p.pop('max_requests', None), 1000, 1, 10000, 'max_requests')
    if fmt not in ('zip', 'jsonl', 'csv') or set(p) & {'limit', 'cursor'}:
        raise ProductError('invalid_parameter', '快照支持 zip/jsonl/csv；使用筛选和 max_requests，不接受分页参数')
    p.setdefault('field_contract', 'prd-v1')
    selection(p, account_id)  # Validate before reserving resources.
    if not _exports.acquire(blocking=False):
        raise ProductError('export_busy', '导出正在处理，请稍后重试', 429)
    try:
        with tempfile.TemporaryDirectory(prefix='searchflow-export-') as temp:
            root = Path(temp)
            manifest = export_public_data(store.base.dsn, root / 'bundle', filters=p,
                                          max_requests=maximum, account_id=account_id,
                                          max_bytes=128 * 1024 * 1024)
            verify_bundle(root / 'bundle')
            # Add a usable wide CSV while retaining the independently verifiable
            # original three-file bundle and its checksum manifest unchanged.
            def records():
                with (root / 'bundle' / 'requests.jsonl').open(encoding='utf-8') as stream:
                    for line in stream:
                        yield json.loads(line)
            full = root / 'full.csv'
            with full.open('w', encoding='utf-8-sig', newline='') as stream:
                write_csv(stream, records())
            selected = root / 'bundle' / 'requests.jsonl' if fmt == 'jsonl' else full
            if fmt == 'zip':
                selected = root / 'data.zip'
                with zipfile.ZipFile(selected, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted((root / 'bundle').iterdir()):
                        archive.write(path, 'bundle/' + path.name)
                    archive.write(full, 'full.csv')
                    full_hash = hashlib.sha256()
                    with full.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            full_hash.update(chunk)
                    archive.writestr('export.json', canonical_json(dict(
                        version='searchflow-full-export-1', full_csv_sha256=full_hash.hexdigest(),
                        bundle_manifest_sha256=manifest['manifest_sha256'],
                        record_types=['request', 'result', 'topic', 'feature'],
                        counts=manifest['counts'], truncated=manifest['truncated'], scope=manifest['scope'])))
            handler.send_response(200)
            ctype = {'zip': 'application/zip', 'csv': 'text/csv; charset=utf-8',
                     'jsonl': 'application/x-ndjson; charset=utf-8'}[fmt]
            for key, value in {'Content-Type': ctype, 'Content-Length': str(selected.stat().st_size),
                               'Content-Disposition': 'attachment; filename="searchflow-data.' + fmt + '"',
                               'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                               'X-SearchFlow-Truncated': str(manifest['truncated']).lower(),
                               'X-SearchFlow-Requests': str(manifest['counts'].get('requests', 0)),
                               'X-SearchFlow-Manifest-SHA256': manifest['manifest_sha256']}.items():
                handler.send_header(key, value)
            handler.end_headers()
            with selected.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    handler.wfile.write(chunk)
    finally:
        _exports.release()
