"""Operator-only, atomic legacy dataset exports from one read-only DB snapshot.

No website route, new search, upload, migration or background task is installed.
A completed bundle has matching lossless JSONL/CSV plus flattened occurrences.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from .google_data import DATA_VERSION, digest, normalize_request
from .google_domain import canonical_json
from .google_legacy import _filters


MAX_RECORD_BYTES = 64 * 1024 * 1024

OCCURRENCE_FIELDS = ('request_id', 'occurrence_id', 'parent_topic_id', 'ordinal', 'child_ordinal',
    'result_type', 'original_url', 'normalized_url', 'url_id', 'title', 'snippet',
    'organic_position', 'source_absolute_rank', 'feed_position', 'record_json')


def _write_csv_value(value):
    # Empty and null remain distinct in record_json; prevent spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


def _occurrence_row(item):
    flattened = {k: item.get(k) for k in OCCURRENCE_FIELDS}
    flattened.update({k: item['url'].get(k) for k in ('original_url', 'normalized_url', 'url_id')})
    flattened['record_json'] = canonical_json(item)
    return {k: _write_csv_value(v) if k != 'record_json' else v
            for k, v in flattened.items()}


def export_public_data(dsn, destination, *, filters=None, max_requests=10000,
                       account_id=None, max_bytes=None):
    if type(max_requests) is not int or not 1 <= max_requests <= 1_000_000:
        raise ValueError('max_requests must be between 1 and 1000000')
    filters = dict(filters or {})
    if set(filters) & {'cursor', 'limit'}:
        raise ValueError('snapshot export does not accept page cursors or page limits')
    if account_id is None:
        where, args, _ = _filters(filters)
        scope = 'public_legacy_history_only'
        field_contract = filters.get('field_contract', 'legacy')
    else:
        from .customer_data import selection
        where, args, _, scope, field_contract = selection(filters, account_id)
    target = Path(destination).absolute()
    if target.exists():
        raise FileExistsError('export destination already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.google-data-', dir=target.parent))
    os.chmod(staging, 0o700)
    counts, publications, statuses, sources = Counter(), Counter(), Counter(), Counter()
    complete = True
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        # Disk-backed uniqueness avoids retaining a large URL set in process memory.
        index = sqlite3.connect(staging / 'working.sqlite')
        try:
            index.execute('CREATE TABLE urls (id TEXT PRIMARY KEY)')
            with (staging / 'requests.jsonl').open('w', encoding='utf-8') as jsonl, \
                 (staging / 'requests.csv').open('w', encoding='utf-8', newline='') as csvfile, \
                 (staging / 'occurrences.csv').open('w', encoding='utf-8', newline='') as flatfile:
                writer = csv.writer(csvfile)
                writer.writerow(('request_id', 'source', 'record_json'))
                flat = csv.DictWriter(flatfile, fieldnames=OCCURRENCE_FIELDS)
                flat.writeheader()
                with psycopg.connect(dsn, row_factory=dict_row) as db:
                    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    db.execute("SET LOCAL statement_timeout='120s'")
                    db.execute("SET LOCAL idle_in_transaction_session_timeout='120s'")
                    snapshot = db.execute('SELECT pg_current_snapshot()::text AS snapshot').fetchone()['snapshot']
                    with db.cursor(name='google_public_data_export') as cursor:
                        cursor.itersize = 100
                        cursor.execute('SELECT r.request_id,r.source,r.query,r.page,' + ('r.provider,' if account_id is not None else '') + 'r.classification,'
                            'r.completed_at,r.response FROM serp_requests r WHERE ' + ' AND '.join(where) +
                            ' ORDER BY r.completed_at,r.request_id LIMIT %s', args + [max_requests + 1])
                        for row in cursor:
                            if counts['requests'] >= max_requests:
                                complete = False
                                break
                            if field_contract == 'prd-v1':
                                from .google_field_adaptation import adapt_request_fields
                                record = adapt_request_fields(row)
                            else:
                                record = normalize_request(row)
                            encoded = canonical_json(record)
                            if len(encoded.encode('utf-8')) > MAX_RECORD_BYTES:
                                raise ValueError('request exceeds export record size limit')
                            jsonl.write(encoded + '\n')
                            writer.writerow((record['request_id'], record['source'], encoded))
                            counts['requests'] += 1
                            sources[record['source']] += 1
                            statuses[record['availability']] += 1
                            counts['topics'] += len(record['topics'])
                            counts['invalid_records'] += len(record['invalid_records'])
                            for item in record['occurrences']:
                                counts['occurrences'] += 1
                                if item['url']['url_id']:
                                    counts['valid_url_occurrences'] += 1
                                    index.execute('INSERT OR IGNORE INTO urls VALUES (?)', (item['url']['url_id'],))
                                if item['publication'] is not None:
                                    publications[item['publication']['status']] += 1
                                counts['unresolved_news_wrappers'] += item['resolution_status'] == 'unresolved_wrapper'
                                flat.writerow(_occurrence_row(item))
                            if max_bytes is not None and sum(s.tell() for s in (jsonl, csvfile, flatfile)) > max_bytes:
                                raise ValueError('export size limit exceeded; narrow the time/source filters')
                for stream in (jsonl, csvfile, flatfile):
                    stream.flush()
                    os.fsync(stream.fileno())
            counts['unique_urls'] = index.execute('SELECT count(*) FROM urls').fetchone()[0]
            counts['duplicate_url_occurrences'] = counts['valid_url_occurrences'] - counts['unique_urls']
        finally:
            index.close()
        (staging / 'working.sqlite').unlink()
        files = {}
        for path in sorted(staging.iterdir()):
            os.chmod(path, 0o600)
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            files[path.name] = dict(sha256=h.hexdigest(), bytes=path.stat().st_size)
        manifest = dict(contract_version=DATA_VERSION, generated_at=generated_at,
            snapshot=snapshot, filters=filters, max_requests=max_requests,
            complete_scope=complete, truncated=not complete, counts=dict(counts),
            sources=dict(sources), availability=dict(statuses), news_time_quality=dict(publications),
            scope=scope, source_read_only=True,
            whale_readback='not_connected', formal_whale_manifest=False,
            retention='operator must apply existing source and access retention rules',
            files=files, csv_formula_policy='display cells escaped; record_json lossless')
        if field_contract == 'prd-v1':
            from .google_field_adaptation import FIELD_VERSION
            manifest['field_adaptation_version'] = FIELD_VERSION
        manifest['manifest_sha256'] = digest(manifest)
        manifest_path = staging / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(manifest_path, 0o600)
        # Reserve an empty directory; replace our reservation in one rename.
        # Readers see either no manifest or the entire completed bundle.
        target.mkdir(mode=0o700)
        try:
            os.replace(staging, target)
        except BaseException:
            target.rmdir()
            raise
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_bundle(destination):
    """Validate copied/restored artifacts without a database or network connection."""
    # CSV defaults to 128 KiB, smaller than real SERP responses with features.
    # This is an operator tool; grow the process parser bound, never shrink it.
    if csv.field_size_limit() < MAX_RECORD_BYTES:
        csv.field_size_limit(MAX_RECORD_BYTES)
    target = Path(destination)
    manifest_file = target / 'manifest.json'
    if manifest_file.is_symlink():
        raise ValueError('manifest must be a regular file')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    expected = manifest.pop('manifest_sha256')
    if digest(manifest) != expected or manifest.get('contract_version') != DATA_VERSION:
        raise ValueError('manifest checksum or contract mismatch')
    names = {'requests.jsonl', 'requests.csv', 'occurrences.csv'}
    if set(manifest['files']) != names:
        raise ValueError('unexpected bundle files')
    for name in names:
        path = target / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('bundle file missing or invalid')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
        if h.hexdigest() != manifest['files'][name]['sha256'] or path.stat().st_size != manifest['files'][name]['bytes']:
            raise ValueError('bundle file checksum mismatch')
    counts, sources, statuses, publications = Counter(), Counter(), Counter(), Counter()
    # A copied bundle is verified without database access and without keeping
    # every URL in RAM. The temporary index never changes the supplied bundle.
    with tempfile.TemporaryDirectory(prefix='google-verify-') as work:
        with sqlite3.connect(Path(work) / 'urls.sqlite') as index:
            index.execute('CREATE TABLE urls (id TEXT PRIMARY KEY)')
            _verify_bundle_records(target, manifest, index, counts, sources, statuses, publications)
    return dict(status='verified', requests=counts['requests'], occurrences=counts['occurrences'],
                complete_scope=manifest['complete_scope'], manifest_sha256=expected)


def _verify_bundle_records(target, manifest, index, counts, sources, statuses, publications):
    from itertools import zip_longest
    count = occurrences = 0
    with (target / 'requests.jsonl').open(encoding='utf-8') as jsonl, \
         (target / 'requests.csv').open(encoding='utf-8', newline='') as csvfile, \
         (target / 'occurrences.csv').open(encoding='utf-8', newline='') as flatfile:
        rows = csv.DictReader(csvfile)
        flat = csv.DictReader(flatfile)
        if rows.fieldnames != ['request_id', 'source', 'record_json']:
            raise ValueError('request CSV columns differ')
        if flat.fieldnames != list(OCCURRENCE_FIELDS):
            raise ValueError('occurrence CSV columns differ')
        for line, row in zip_longest(jsonl, rows):
            if line is None or row is None or line.rstrip('\n') != row['record_json']:
                raise ValueError('JSONL and CSV differ')
            record = json.loads(line)
            if record['request_id'] != row['request_id'] or record['source'] != row['source']:
                raise ValueError('CSV identity differs')
            counts['topics'] += len(record['topics'])
            counts['invalid_records'] += len(record['invalid_records'])
            sources[record['source']] += 1
            statuses[record['availability']] += 1
            # Verify every occurrence against its parent request in streaming
            # order, including formula-safe display cells and lossless JSON.
            # File checksums alone cannot establish cross-file consistency.
            for item in record['occurrences']:
                actual = next(flat, None)
                expected_row = {k: '' if v is None else str(v)
                                for k, v in _occurrence_row(item).items()}
                if actual != expected_row:
                    raise ValueError('bundle occurrence content or order differs')
                occurrences += 1
                if item['url']['url_id']:
                    counts['valid_url_occurrences'] += 1
                    index.execute('INSERT OR IGNORE INTO urls VALUES (?)', (item['url']['url_id'],))
                if item['publication'] is not None:
                    publications[item['publication']['status']] += 1
                counts['unresolved_news_wrappers'] += item['resolution_status'] == 'unresolved_wrapper'
            count += 1
        if next(flat, None) is not None:
            raise ValueError('bundle occurrence has extra rows')
    if count != manifest['counts'].get('requests', 0) or occurrences != manifest['counts'].get('occurrences', 0):
        raise ValueError('bundle record count differs')
    counts['requests'] = count
    counts['occurrences'] = occurrences
    counts['unique_urls'] = index.execute('SELECT count(*) FROM urls').fetchone()[0]
    counts['duplicate_url_occurrences'] = counts['valid_url_occurrences'] - counts['unique_urls']
    if counts != Counter(manifest['counts']):
        raise ValueError('bundle aggregate counts differ')
    for name, actual in (('sources', sources), ('availability', statuses), ('news_time_quality', publications)):
        if actual != Counter(manifest[name]):
            raise ValueError('bundle aggregate ' + name + ' differs')


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', help='new private output directory')
    parser.add_argument('--verify', action='store_true', help='verify an existing bundle without database access')
    parser.add_argument('--source', choices=('google_web', 'google_news', 'google_trends', 'google_images'))
    parser.add_argument('--field-contract', choices=('legacy', 'prd-v1'), default='legacy')
    parser.add_argument('--query')
    parser.add_argument('--domain')
    parser.add_argument('--from', dest='start')
    parser.add_argument('--to', dest='end')
    parser.add_argument('--max-requests', type=int, default=10000)
    parser.add_argument('--database-url-env', default='DATABASE_URL')
    args = parser.parse_args(argv)
    parameters = {k: v for k, v in dict(source=args.source, query=args.query, domain=args.domain,
                                       **{'from': args.start, 'to': args.end}).items() if v}
    if args.field_contract != 'legacy':
        parameters['field_contract'] = args.field_contract
    try:
        if args.verify:
            print(json.dumps(verify_bundle(args.destination)))
            return 0
        result = export_public_data(os.environ[args.database_url_env], args.destination,
                                    filters=parameters, max_requests=args.max_requests)
    except Exception:
        # Connection errors can contain credentials/DSN; never echo their details.
        print(json.dumps({'status': 'failed', 'reason': 'export_failed_check_inputs_and_database'}))
        return 2
    print(json.dumps({'status': 'completed' if result['complete_scope'] else 'truncated',
                      'counts': result['counts'], 'manifest_sha256': result['manifest_sha256']}))
    return 0 if result['complete_scope'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
