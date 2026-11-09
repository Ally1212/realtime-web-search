"""Lossless News/Trending Now run candidates, without an uploader or schema admission.

No Dataset, content_type, article publication claim, quality bypass or production
registration is selected here. A platform-approved binding and separate operator
write authorization are required before a candidate can be submitted anywhere.
Pure builders expect a public saved row; prepare_public_feed_run enforces this
against the current database in a read-only transaction.
"""
from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .google_data import digest, normalize_request, timestamp
from .google_domain import DomainError
from .serp_fields import public_fields

# These are local candidate contract versions, not registered Whale schemas.
FEED_CONTRACTS = {
    'google_news': ('google-news-run-envelope-1', 'google-news-run'),
    'google_trends': ('google-trending-now-run-envelope-1', 'google-trending-now-run'),
}


def saved_feed_run_candidate(row):
    if not isinstance(row, dict):
        raise DomainError('invalid_argument', 'A saved feed request object is required')
    source = row.get('source')
    if not isinstance(source, str) or source not in FEED_CONTRACTS:
        raise DomainError('not_covered', 'Only saved News and Trending Now feeds are supported')
    try:
        request_id = str(UUID(str(row['request_id'])))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise DomainError('invalid_argument', 'A saved feed request UUID is required') from None
    if (not isinstance(row.get('query'), str) or not row['query'].strip()
            or type(row.get('page')) is not int or row['page'] < 1
            or not isinstance(row.get('classification'), str) or not row['classification']
            or not isinstance(row.get('response'), dict)
            or timestamp(row.get('completed_at')) is None):
        raise DomainError('invalid_argument', 'Saved feed query, page, status, response and zoned completion time are required')
    # Canonical UUID spelling must also bind every nested occurrence identity.
    observation = normalize_request({**row, 'request_id': request_id})
    schema, namespace = FEED_CONTRACTS[source]
    payload = dict(schema_version=schema, run_id=request_id,
                   observation=observation, source_response=public_fields(row['response']),
                   original_source_observed_at=observation['source_observed_at'], legacy_replay=True,
                   snapshot_role='saved_news_index_list_not_publisher_body' if source == 'google_news'
                       else 'saved_trending_now_topics_and_reports_not_numeric_history')
    return dict(admission_status='candidate_not_approved', source=source,
                object_key=namespace + ':' + request_id,
                event_key=source + ':' + namespace + ':' + request_id,
                payload_hash='sha256:' + digest(payload), payload=payload)


def validate_feed_payload(payload):
    """Rebuild from preserved source fields; reject lossy/altered derived data.

    This local semantic validation is not platform schema/rights admission.
    Unknown records and errors are preserved, rather than treated as absent.
    """
    try:
        observation = payload['observation']
        source = observation['source']
        row = dict(request_id=payload['run_id'], source=source,
                   query=observation['query'], page=observation['query_conditions']['page'],
                   classification=observation['classification'],
                   completed_at=observation['completed_at'], response=payload['source_response'])
        expected = saved_feed_run_candidate(row)['payload']
        if digest(expected) != digest(payload):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, DomainError):
        raise DomainError('source_error', 'Feed snapshot differs from its saved source contract', 503) from None
    return dict(source=source, run_id=payload['run_id'],
                occurrence_count=len(observation['occurrences']), topic_count=len(observation['topics']),
                invalid_record_count=len(observation['invalid_records']),
                availability=observation['availability'])


def validate_feed_candidate(candidate):
    """Bind proposed event/object identities and hash to the exact candidate."""
    try:
        payload = candidate['payload']
        summary = validate_feed_payload(payload)
        schema, namespace = FEED_CONTRACTS[summary['source']]
        expected = dict(admission_status='candidate_not_approved', source=summary['source'],
                        object_key=namespace + ':' + summary['run_id'],
                        event_key=summary['source'] + ':' + namespace + ':' + summary['run_id'],
                        payload_hash='sha256:' + digest(payload), payload=payload)
        if digest(candidate) != digest(expected):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DomainError('invalid_argument', 'Invalid saved feed candidate') from None
    return summary


def prepare_public_feed_run(dsn, request_id, *, source):
    if not isinstance(source, str) or source not in FEED_CONTRACTS:
        raise DomainError('not_covered', 'Only saved News and Trending Now feeds are supported')
    try:
        request_id = str(UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError):
        raise DomainError('invalid_argument', 'Invalid feed request ID') from None
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='5s'")
        row = db.execute("""SELECT r.request_id,r.source,r.query,r.page,r.classification,r.completed_at,r.response
            FROM serp_requests r WHERE r.request_id=%s AND r.source=%s
            AND NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id
                            AND o.options->>'customer_private'='true')""", (request_id, source)).fetchone()
    if row is None:
        raise DomainError('not_found', 'Public feed request not found', 404)
    return saved_feed_run_candidate(row)
