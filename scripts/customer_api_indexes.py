#!/usr/bin/env python3
"""Install only rebuildable indexes for customer data reads; no table migrations."""
import argparse
import json
import os

import psycopg

INDEXES = {
    'sf_jobs_customer_campaign': "CREATE INDEX CONCURRENTLY IF NOT EXISTS sf_jobs_customer_campaign ON sf_jobs(account_id,campaign_id) WHERE campaign_id IS NOT NULL",
    'sf_serp_request_campaign': "CREATE INDEX CONCURRENTLY IF NOT EXISTS sf_serp_request_campaign ON serp_campaign_requests(request_id,campaign_id)",
    'sf_serp_source_query_time': "CREATE INDEX CONCURRENTLY IF NOT EXISTS sf_serp_source_query_time ON serp_requests(source,query,completed_at DESC,request_id DESC)",
}


def install(dsn):
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute("SET lock_timeout='3s'")
        db.execute("SET statement_timeout='120s'")
        for name, statement in INDEXES.items():
            # Failed CONCURRENTLY builds can leave an invalid index. Do not
            # silently bless it, drop it, or block another active build.
            row = db.execute('SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(%s)', (name,)).fetchone()
            if row and not row[0]:
                raise RuntimeError('Invalid index requires operator review: ' + name)
            db.execute(statement)
        result = db.execute('SELECT c.relname,i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=ANY(%s)', (list(INDEXES),)).fetchall()
    return dict(indexes={name: valid for name, valid in result}, source_mutations=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Without this flag, only show index names.')
    args = parser.parse_args()
    print(json.dumps(install(os.environ['DATABASE_URL']) if args.apply else {'planned_indexes': list(INDEXES)}))
