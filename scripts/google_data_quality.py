#!/usr/bin/env python3
"""Read-only News timestamp audit. Prints counts, never queries, URLs or keys."""
import argparse
import json
import os
from collections import Counter

import psycopg

from realtime.google_data import news_time


def audit_news_times(dsn):
    counts = Counter()
    with psycopg.connect(dsn) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        db.execute("SET LOCAL statement_timeout='60s'")
        with db.cursor(name='news_time_quality') as cursor:
            cursor.itersize = 1000
            cursor.execute("""SELECT item->>'date',item->>'date_raw'
                FROM serp_requests r CROSS JOIN LATERAL jsonb_array_elements(
                  CASE WHEN jsonb_typeof(r.response->'news_results')='array'
                  THEN r.response->'news_results' ELSE '[]'::jsonb END) item
                WHERE r.source='google_news' AND NOT EXISTS (
                  SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id
                  AND o.options->>'customer_private'='true')""")
            for saved, raw in cursor:
                result = news_time({'date': saved, 'date_raw': raw})
                counts['records'] += 1
                counts['saved_field_provided'] += result['field_provided']
                counts[result['status']] += 1
    return dict(read_only=True, scope='public_legacy_news', counts=dict(counts),
                basis='raw_pubDate_matches_saved_utc_within_one_second', publisher_page_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-env', default='DATABASE_URL')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(audit_news_times(os.environ[args.database_url_env]), sort_keys=True))
    except Exception:
        print(json.dumps({'status': 'failed', 'reason': 'quality_audit_failed'}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
