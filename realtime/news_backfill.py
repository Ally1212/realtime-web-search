"""Bounded, resumable News enrichment; original SERPs and URL contributions stay intact."""
from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta

from psycopg.types.json import Jsonb

from .customer_data import COLUMNS, PUBLIC, project
from .serp_fields import public_fields

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_news_link_enrichments (
 request_id uuid NOT NULL, occurrence_id text NOT NULL,
 wrapper text NOT NULL, evidence jsonb NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(request_id,occurrence_id)
);
CREATE TABLE IF NOT EXISTS sf_news_backfill_progress (
 request_id uuid PRIMARY KEY, completed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE sf_news_link_enrichments ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 1;
ALTER TABLE sf_news_link_enrichments ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;
ALTER TABLE sf_news_backfill_progress ADD COLUMN IF NOT EXISTS finished boolean NOT NULL DEFAULT true;
ALTER TABLE sf_news_backfill_progress ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;
"""


def run(store, discovery=None, *, max_requests=20, max_links=20, budget_seconds=60,
        pause_file=None, apply=False, retry_unresolved=False, retry_seconds=300):
    """Preview is offline; apply writes additive evidence only. Retry has bounded backoff.

    Resolved evidence is immutable. Deferred outcomes get a retry time instead
    of starving later requests. --retry-unresolved opts into revisiting failed
    terminal outcomes, including failures recorded by the previous version.
    """
    from .google_news_links import NewsLinkResolver, article_id, offline_target
    if not 1 <= max_requests <= 1000 or not 1 <= max_links <= 100 or not 1 <= budget_seconds <= 600 or not 60 <= retry_seconds <= 86400:
        raise ValueError('Invalid backfill bounds')
    if discovery is not None and not apply:
        raise ValueError('Online resolution requires explicit apply')
    started = time.monotonic()
    deadline = started + budget_seconds
    runnable = lambda: time.monotonic() < deadline and not (pause_file and pause_file.exists())
    if discovery is not None:
        discovery.should_continue = runnable
        discovery.task_options = {}  # SQL below selects public requests only.
    resolver = NewsLinkResolver(discovery, store, 'public-history-backfill', budget_seconds=budget_seconds,
                                max_links=max_links) if discovery is not None else None
    with store.connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='20s'")
        rows = db.execute('SELECT ' + COLUMNS + " FROM serp_requests r WHERE r.source='google_news' AND " + PUBLIC +
                          " AND (NOT EXISTS (SELECT 1 FROM sf_news_backfill_progress p WHERE p.request_id=r.request_id "
                          "AND (p.finished OR p.next_retry_at>now())) OR (%s AND EXISTS (SELECT 1 FROM sf_news_link_enrichments e "
                          "WHERE e.request_id=r.request_id AND e.evidence->>'status' IN ('unresolved','deferred') "
                          "AND coalesce(e.next_retry_at,'-infinity'::timestamptz)<=now()))) "
                          'ORDER BY r.completed_at,r.request_id LIMIT %s', (retry_unresolved, max_requests)).fetchall()
    outcomes, finished = [], []
    stopped = False
    for row in rows:
        if not runnable() or len(outcomes) >= max_links:
            stopped = True
            break
        record = project(row)
        with store.connect() as db:
            saved = {r['occurrence_id']: r for r in db.execute('SELECT * FROM sf_news_link_enrichments WHERE request_id=%s', (record['request_id'],)).fetchall()}
        for item in record['occurrences']:
            wrapper = item['source_fields'].get('link')
            if not article_id(wrapper):
                continue
            previous = saved.get(item['occurrence_id'])
            now = datetime.now(timezone.utc)
            if previous and (previous['evidence']['status'] == 'resolved' or
                             previous['evidence']['status'] == 'unresolved' and not retry_unresolved or
                             previous['next_retry_at'] and previous['next_retry_at'] > now):
                continue
            if not runnable() or len(outcomes) >= max_links:
                stopped = True
                break
            if resolver:
                proof = resolver.resolve(wrapper)
            else:
                target = offline_target(wrapper)
                proof = dict(wrapper=wrapper, target=target, status='resolved' if target else 'deferred',
                             reason=None if target else 'online_resolution_required', method='offline_protobuf' if target else None,
                             observed_at=now.isoformat(), publisher_fetched=False, http_request_count=0)
            attempt = (previous['attempts'] if previous else 0) + 1
            next_retry = now + timedelta(seconds=min(86400, retry_seconds * 2 ** min(attempt - 1, 8))) if proof['status'] != 'resolved' else None
            outcome = dict(request_id=record['request_id'], occurrence_id=item['occurrence_id'],
                           wrapper=wrapper, evidence=public_fields(proof), attempts=attempt,
                           next_retry_at=next_retry.isoformat() if next_retry else None)
            outcomes.append(outcome)
            if apply:
                with store.connect() as db:
                    db.execute('INSERT INTO sf_news_link_enrichments(request_id,occurrence_id,wrapper,evidence,attempts,next_retry_at) '
                               'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(request_id,occurrence_id) DO UPDATE SET '
                               'evidence=EXCLUDED.evidence,observed_at=clock_timestamp(),attempts=sf_news_link_enrichments.attempts+1,'
                               "next_retry_at=EXCLUDED.next_retry_at WHERE sf_news_link_enrichments.evidence->>'status'<>'resolved'",
                               (record['request_id'], item['occurrence_id'], wrapper, Jsonb(outcome['evidence']), attempt, next_retry))
        if apply:
            with store.connect() as db:
                current = {r['occurrence_id']: r for r in db.execute('SELECT * FROM sf_news_link_enrichments WHERE request_id=%s', (record['request_id'],)).fetchall()}
                pending = []
                for item in record['occurrences']:
                    if not article_id(item['source_fields'].get('link')):
                        continue
                    saved_item = current.get(item['occurrence_id'])
                    if not saved_item:
                        pending.append(datetime.now(timezone.utc))
                    elif saved_item['evidence']['status'] == 'deferred' or saved_item['evidence']['status'] == 'unresolved' and retry_unresolved:
                        pending.append(saved_item['next_retry_at'] or datetime.now(timezone.utc))
                done = not pending
                db.execute('INSERT INTO sf_news_backfill_progress(request_id,finished,next_retry_at) VALUES(%s,%s,%s) '
                           'ON CONFLICT(request_id) DO UPDATE SET completed_at=clock_timestamp(),finished=EXCLUDED.finished,next_retry_at=EXCLUDED.next_retry_at',
                           (record['request_id'], done, min(pending) if pending else None))
                if done:
                    finished.append(record['request_id'])
        if stopped:
            break
    counts = {state: sum(r['evidence']['status'] == state for r in outcomes) for state in ('resolved', 'unresolved', 'deferred')}
    return dict(version='news-backfill-2', applied=apply, online=resolver is not None,
                outcomes=outcomes, completed_requests=finished, stopped=stopped or not runnable(),
                **counts, attempted=len(outcomes), elapsed_seconds=time.monotonic() - started,
                http_requests=sum(r['evidence'].get('http_request_count', 0) for r in outcomes),
                cache_hits=sum(bool(r['evidence'].get('cache_hit')) for r in outcomes),
                publisher_fetches=0, original_requests_modified=0,
                note='Separate evidence; unresolved/deferred have bounded retry times. A resolved Google link is not independent publisher-page or publication-date verification.')
