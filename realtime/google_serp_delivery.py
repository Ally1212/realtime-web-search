"""Pure builder for full saved Web SERP run envelopes, not an active uploader.

Uses documented whale.ingest.v1 content.platform_payload. Formal run/profile
admission and real persistence/readback remain separate acceptance steps.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .google_data import normalize_request, digest, timestamp
from .google_domain import DomainError
from .serp_fields import public_fields

RUN_SCHEMA = 'google-serp-run-envelope-1'
TEXT_PROJECTION = 'google-serp-snapshot-text-1'


def with_search_snapshot_text(message):
    """Add searchable saved-result text, never publisher body or score padding.

    Explicit opt-in correction: preserve the immutable source envelope and object
    identity, but use a separate deterministic event for this representation.
    """
    try:
        payload = message['content']['platform_payload']
        observation = payload['observation']
        run_id = str(UUID(payload['run_id']))
        items = observation['occurrences']
        if (payload['schema_version'] != RUN_SCHEMA
                or observation['request_id'] != run_id
                or observation['source'] != 'google_web'
                or observation['availability'] != 'available'
                or message['content']['external_content_id'] != 'serp-run:' + run_id
                or message['source']['source_platform'] != 'google_search'
                or not isinstance(items, list) or not items
                or message['source']['payload_hash'] != 'sha256:' + digest(payload)):
            raise ValueError()
        # Require a genuine useful saved field on every item. A URL alone is
        # sufficient for link collection; absent titles/snippets stay absent.
        if any(not isinstance(item, dict) or item.get('request_id') != run_id
               or not any(isinstance(value, str) and value.strip() for value in
                          (item.get('title'), item.get('snippet'),
                           (item.get('url') or {}).get('original_url')))
               for item in items):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DomainError('invalid_argument', 'A valid nonempty saved public Web envelope is required') from None
    result = deepcopy(message)
    # JSON strings keep query/result newlines visibly inside their own fields.
    def field(label, value):
        return label + ': ' + json.dumps(value, ensure_ascii=False, allow_nan=False)
    lines = ['Google Web search result snapshot',
             'Text projection of saved search results; not publisher article body.',
             field('Query', observation['query']), field('Run ID', run_id),
             field('Completed at', observation['completed_at']),
             field('Saved result count', len(items))]
    for index, item in enumerate(items, 1):
        lines.extend(['', 'Saved result ' + str(index)])
        for label, value in [('Title', item.get('title')),
                             ('URL', (item.get('url') or {}).get('original_url')),
                             ('Snippet', item.get('snippet')),
                             ('Organic position', item.get('organic_position'))]:
            if value is not None and value != '':
                lines.append(field(label, value))
    result['content']['body_text'] = '\n'.join(lines)
    result['content']['body_format'] = 'plain'
    metadata = result['discovery'].setdefault('metadata', {})
    metadata.update(text_projection_version=TEXT_PROJECTION,
                    text_role='saved_search_result_snapshot_not_publisher_body',
                    text_source_payload_hash=message['source']['payload_hash'])
    result['provided_capabilities'] = list(dict.fromkeys(
        [*message.get('provided_capabilities', []), 'body']))
    event = 'google_search:serp-run:' + run_id + ':' + TEXT_PROJECTION
    result['source']['source_record_key'] = event
    return result


def saved_web_run_message(row, *, dataset_id, source_name, collector_id):
    if row.get('source') != 'google_web':
        raise DomainError('not_covered', 'Full run envelopes currently support Google Web only')
    for value in (dataset_id, source_name, collector_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise DomainError('invalid_argument', 'Dataset/source/collector must be explicit')
    try:
        request_id = str(UUID(str(row['request_id'])))
    except (KeyError, ValueError, TypeError):
        raise DomainError('invalid_argument', 'A saved request UUID is required') from None
    if not isinstance(row.get('query'), str) or not row['query'].strip():
        raise DomainError('invalid_argument', 'Saved query text is required')
    completed = timestamp(row.get('completed_at'))
    if completed is None:
        raise DomainError('invalid_argument', 'Saved completion time must include timezone')
    observation = normalize_request(row)
    payload = dict(schema_version=RUN_SCHEMA, run_id=request_id,
                   observation=observation, source_response=public_fields(row['response']),
                   title_role='query_label_not_source_page_title',
                   original_source_observed_at=observation['source_observed_at'],
                   legacy_replay=True)
    event = f'google_search:serp-run:{request_id}'
    # A run is its own immutable object; publisher URLs remain item identities.
    return dict(schema_version='whale.ingest.v1', dataset_id=dataset_id,
        source=dict(source_platform='google_search', source_name=source_name+'.serp-runs.v1',
                    source_record_key=event, payload_hash='sha256:'+digest(payload), received_at=completed,
                    trace_id=request_id),
        discovery=dict(collector=collector_id, acquisition_type='backfill', discovered_at=completed,
                       metadata=dict(schema_version=RUN_SCHEMA, run_id=request_id,
                                     query_variant_id=observation['query_variant_id'],
                                     result_count=len(observation['occurrences']), legacy_replay=True)),
        content=dict(external_content_id='serp-run:'+request_id, content_type='search_result',
                     title=row['query'], platform_payload=payload, status='active'),
        provided_capabilities=['identity','title'])


def prepare_public_web_run(dsn, request_id, *, dataset_id, source_name, collector_id):
    try:
        request_id = str(UUID(str(request_id)))
    except (TypeError, ValueError):
        raise DomainError('invalid_argument', 'Invalid request ID') from None
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='5s'")
        row = db.execute("""SELECT r.request_id,r.source,r.query,r.page,r.classification,r.completed_at,r.response
            FROM serp_requests r WHERE r.request_id=%s AND r.source='google_web'
            AND NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id
                            AND o.options->>'customer_private'='true')""", (request_id,)).fetchone()
    if row is None:
        raise DomainError('not_found', 'Public Web request not found', 404)
    return saved_web_run_message(row, dataset_id=dataset_id, source_name=source_name, collector_id=collector_id)
