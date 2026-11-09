"""Isolated synthetic export load probe; never a real collection/delivery claim.

Requires an explicit loopback PostgreSQL DSN. Creates only a fresh UUID schema,
uses the production exporter and verifier, then drops only that schema. Does
not initialize/migrate application tables, use Redis, or contact any provider.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import itertools
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from realtime.google_data import digest
from realtime.google_data_export import export_public_data, verify_bundle
from realtime.google_domain import DomainError
from realtime.google_legacy import read_public_request

SOURCES = ('google_web', 'google_news', 'google_trends')
NAMESPACE = UUID('66b12c48-0571-4ed4-af06-f4e5abaf2be8')


def validate_local_dsn(dsn):
    info = conninfo_to_dict(dsn)
    if (info.get('host') not in {'127.0.0.1', '::1'} or info.get('hostaddr')
            or info.get('service') or not info.get('port')):
        raise ValueError('An explicit loopback PostgreSQL host and port are required')


def synthetic_row(index, count, pool):
    source = SOURCES[index % 3]
    items = [dict(link=f'https://scale-benchmark.invalid/item/{(index * 100 + n) % pool}',
                  title='SYNTHETIC export benchmark', snippet='Synthetic data; not a real Google observation.',
                  **({'position': n + 1} if source == 'google_web' else {'feed_position': n + 1}))
             for n in range(count)]
    response = dict(search_parameters=dict(gl='us', hl='en', device='desktop'),
                    search_metadata=dict(data_origin='synthetic_benchmark_not_real_observation'))
    if source == 'google_web': response['organic_results'] = items
    elif source == 'google_news': response['news_results'] = items
    else:
        response['trending_searches'] = [dict(keyword=f'SYNTHETIC topic {n // 10}',
            approx_traffic='unknown', articles=items[n:n + 10]) for n in range(0, count, 10)]
    return dict(request_id=str(uuid5(NAMESPACE, str(index))), source=source,
                query=f'SYNTHETIC query {index % 100}', page=1, classification='results',
                completed_at=datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(seconds=index), response=response)


def run_probe(dsn, destination, *, occurrences=1_000_000):
    validate_local_dsn(dsn)
    if type(occurrences) is not int or not 1 <= occurrences <= 1_000_000:
        raise ValueError('occurrences must be 1..1000000')
    target = Path(destination).absolute()
    if target.exists(): raise FileExistsError('Benchmark destination already exists')
    if shutil.disk_usage(target.parent).free < 16 * 1024**3:
        raise ValueError('Benchmark requires at least 16 GiB free disk')
    target.mkdir(mode=0o700)
    schema = 'google_scale_' + uuid4().hex
    scoped = make_conninfo(dsn, options=f'-c search_path={schema}')
    requests = math.ceil(occurrences / 100)
    pool = min(100000, occurrences)
    expected_path = target / 'synthetic-input-hashes.jsonl'
    expected_sources = {s: 0 for s in SOURCES}
    baseline = dict(platform=platform.platform(), python=sys.version.split()[0], cpus=os.cpu_count(),
                    free_disk_bytes=shutil.disk_usage(target).free)
    start = time.monotonic()
    schema_created = False
    try:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            schema_created = True
        with psycopg.connect(scoped) as db:
            db.execute('CREATE TABLE serp_requests(request_id uuid PRIMARY KEY,campaign_id uuid NOT NULL,source text NOT NULL,query text NOT NULL,page integer NOT NULL,classification text NOT NULL,completed_at timestamptz NOT NULL,response jsonb NOT NULL)')
            db.execute('CREATE TABLE serp_task_options(campaign_id uuid PRIMARY KEY,options jsonb NOT NULL)')
            with expected_path.open('x') as expected, db.cursor().copy('COPY serp_requests FROM STDIN') as copy:
                campaign = uuid4()
                for i in range(requests):
                    row = synthetic_row(i, min(100, occurrences - i * 100), pool)
                    copy.write_row((row['request_id'], campaign, row['source'], row['query'], 1, 'results', row['completed_at'], Jsonb(row['response'])))
                    expected_sources[row['source']] += 1
                    expected.write(json.dumps(dict(request_id=row['request_id'], source=row['source'],
                        response_hash=digest(row['response']), occurrences=min(100, occurrences - i * 100),
                        topics=len(row['response'].get('trending_searches', [])))) + '\n')
            # One private sentinel per source, in the same database and filters.
            for i in range(3):
                row = synthetic_row(requests + i, 1, pool)
                private = uuid4(); sentinel = {'private_sentinel': 'MUST_NOT_EXPORT', **row['response']}
                db.execute('INSERT INTO serp_task_options VALUES(%s,%s)', (private, Jsonb({'customer_private': True})))
                db.execute('INSERT INTO serp_requests VALUES(%s,%s,%s,%s,1,%s,%s,%s)',
                    (row['request_id'], private, row['source'], row['query'], 'results', row['completed_at'], Jsonb(sentinel)))
        expected_path.chmod(0o600)
        generation_seconds = time.monotonic() - start
        sample_ids = [str(uuid5(NAMESPACE, str(i))) for i in range(min(requests, 6))]
        api_records = {rid: read_public_request(scoped, rid, normalized=True)['request'] for rid in sample_ids}
        for i in range(3):
            try: read_public_request(scoped, str(uuid5(NAMESPACE, str(requests + i))), normalized=True)
            except DomainError as error:
                if error.code != 'not_found': raise
            else: raise AssertionError('private sentinel readable')
        latencies = []
        export_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(export_public_data, scoped, target / 'bundle', max_requests=requests + 1)
            while not future.done():
                t = time.monotonic()
                rid = sample_ids[len(latencies) % len(sample_ids)]
                record = read_public_request(scoped, rid, normalized=True)['request']
                if record != api_records[rid]: raise AssertionError('concurrent read differs')
                # Count samples begun and completed while the export was active.
                if not future.done(): latencies.append(time.monotonic() - t)
                time.sleep(0.1)
            manifest = future.result()
        export_seconds = time.monotonic() - export_start
        assert manifest['counts']['occurrences'] == occurrences
        assert manifest['counts']['requests'] == requests
        assert manifest['counts']['unique_urls'] == pool
        assert manifest['sources'] == {k: v for k, v in expected_sources.items() if v}
        assert manifest['complete_scope'] and not manifest['truncated']
        verify_start = time.monotonic()
        verification = verify_bundle(target / 'bundle')
        api_sample_matches = 0
        with expected_path.open() as expected, (target / 'bundle/requests.jsonl').open() as exported:
            for line, original in itertools.zip_longest(exported, expected):
                if line is None or original is None: raise AssertionError('input/output count mismatch')
                record, prepared = json.loads(line), json.loads(original)
                assert record['request_id'] == prepared['request_id'] and record['source'] == prepared['source']
                assert record['source_response_hash'] == prepared['response_hash']
                assert len(record['occurrences']) == prepared['occurrences'] and len(record['topics']) == prepared['topics']
                assert record['source_metadata']['data_origin'] == 'synthetic_benchmark_not_real_observation'
                assert 'private_sentinel' not in record['response_extensions']
                if record['request_id'] in api_records:
                    assert record == api_records[record['request_id']]
                    api_sample_matches += 1
        verify_seconds = time.monotonic() - verify_start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != 'darwin': peak *= 1024
        ordered = sorted(latencies)
        p95 = ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None
        result = dict(status='verified', synthetic_performance_only=True,
            scope='isolated synthetic database export; not real Google/Whale delivery or G1 acceptance',
            created_at=datetime.now(timezone.utc).isoformat(), baseline=baseline,
            occurrences=occurrences, requests=requests, unique_urls=pool, sources=manifest['sources'],
            private_sentinels_excluded=3, api_sample_matches=api_sample_matches,
            generation_seconds=generation_seconds, export_seconds=export_seconds,
            verify_seconds=verify_seconds, peak_process_rss_bytes=peak,
            bundle_bytes=sum(f['bytes'] for f in manifest['files'].values()),
            concurrent_reads=dict(samples=len(ordered),p95_seconds=p95,errors=0,
                                  suffices_for_200_sample_report=len(ordered)>=200,
                                  scope='one normalized request reader; not 5rps/concurrency2 HTTP benchmark'),
            bundle_verification=verification, new_searches=0, uploads=0,
            source_or_platform_admission=False, real_million_row_delivery=False)
        (target / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        (target / 'result.json').chmod(0o600)
        return result
    finally:
        if schema_created:
            with psycopg.connect(dsn, autocommit=True) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    parser.add_argument('--occurrences', type=int, default=10000)
    parser.add_argument('--database-url-env', default='GOOGLE_SCALE_TEST_DATABASE_URL')
    args = parser.parse_args(argv)
    try:
        result = run_probe(os.environ[args.database_url_env], args.destination, occurrences=args.occurrences)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps(dict(status='failed', reason='isolated_scale_probe_failed', new_searches=0, uploads=0)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
