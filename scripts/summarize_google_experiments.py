#!/usr/bin/env python3
"""Unified fixed-window comparison for one or more isolated experiment ledgers."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def metrics(path: Path) -> dict:
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.row_factory = sqlite3.Row
        settings = {row['key']: json.loads(row['value']) for row in db.execute('SELECT * FROM settings')}
        start = float(settings.get('started_at') or 0)
        deadline = float(settings.get('deadline') or start)
        end = min(deadline, float(settings.get('finished_at') or deadline))
        search = db.execute(
            "SELECT count(*) pages, sum(status='success') successful, sum(status='failed') failed, "
            "sum(cache_hit) cached FROM searches WHERE finished>=? AND finished<=?",
            (start, end),
        ).fetchone()
        attempts = []
        for row in db.execute('SELECT attempts FROM searches WHERE finished>=? AND finished<=?', (start, end)):
            attempts.extend(json.loads(row['attempts']))
        documents = list(db.execute(
            "SELECT d.hash,d.canonical,d.classification,d.finished,o.status receipt,o.finished receipt_finished "
            'FROM documents d LEFT JOIN outbox o ON o.document_id=d.id '
            'WHERE d.finished>=? AND d.finished<=? AND d.quality=\'[]\'', (start, end),
        ))
        strict = {}
        for row in documents:
            if row['classification'] == 'new' and row['hash']:
                strict[row['hash']] = row
        accepted = {row['canonical'] for row in documents
                    if row['receipt'] in {'accepted', 'duplicate'} and row['receipt_finished'] and start <= row['receipt_finished'] <= end}
        return {
            'path': str(path), 'id': settings.get('id'), 'state': settings.get('state'),
            'shard_count': settings.get('shard_count', 1), 'shard_index': settings.get('shard_index', 0),
            'elapsed_seconds': round(max(0, end-start), 3), 'search_pages': search['pages'],
            'successful_searches': search['successful'], 'failed_searches': search['failed'],
            'cache_hits': search['cached'], 'google_requests': len(attempts),
            'google_successes': sum(bool(a.get('success')) for a in attempts),
            'google_captchas': sum(a.get('error') == 'google_captcha' for a in attempts),
            'transport_errors': sum(str(a.get('error', '')).startswith(('google_timeout', 'google_transport')) for a in attempts),
            'attempt_errors': {code: sum(a.get('error') == code for a in attempts) for code in sorted({a.get('error') for a in attempts if a.get('error')})},
            'unique_urls': db.execute('SELECT count(*) FROM urls WHERE first_seen>=? AND first_seen<=?', (start, end)).fetchone()[0],
            'strict_new_local': len(strict), 'accepted_local': len(accepted),
            'settings': {
                'search_rps': settings.get('search_rps'), 'search_workers': settings.get('search_workers'),
                'body_workers': settings.get('body_workers'), 'providers': settings.get('google_providers'),
                'proxy_profile': settings.get('proxy_profile'), 'query_plan': settings.get('query_plan'),
            },
        }
    finally:
        db.close()


def global_dedup(paths: list[Path]) -> dict:
    urls, hashes, accepted, query_pages = set(), set(), set(), set()
    for path in paths:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            settings = {row['key']: json.loads(row['value']) for row in db.execute('SELECT * FROM settings')}
            start, deadline = float(settings.get('started_at') or 0), float(settings.get('deadline') or start)
            end = min(deadline, float(settings.get('finished_at') or deadline))
            urls.update(row[0] for row in db.execute(
                'SELECT url FROM urls WHERE first_seen>=? AND first_seen<=?', (start, end)))
            hashes.update(row[0] for row in db.execute(
                "SELECT hash FROM documents WHERE classification='new' AND quality='[]' "
                'AND finished>=? AND finished<=? AND hash<>\'\'', (start, end)))
            accepted.update(row[0] for row in db.execute(
                "SELECT d.hash FROM documents d JOIN outbox o ON o.document_id=d.id "
                "WHERE o.status IN ('accepted','duplicate') AND d.quality='[]' "
                'AND o.finished>=? AND o.finished<=? AND d.canonical<>\'\'', (start, end)))
            query_pages.update((row[0], row[1]) for row in db.execute(
                'SELECT query_id,page FROM searches WHERE finished>=? AND finished<=?', (start, end)))
        finally:
            db.close()
    return {'global_unique_urls': len(urls), 'global_deduped_strict_new': len(hashes),
            'global_deduped_accepted': len(accepted), 'global_searches': len(query_pages)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('experiment_sqlite', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = {'runs': [metrics(path) for path in args.experiment_sqlite],
              'global_dedup': global_dedup(args.experiment_sqlite)}
    report['totals_local'] = {key: sum(int(run.get(key) or 0) for run in report['runs'])
                              for key in ('strict_new_local', 'accepted_local', 'search_pages', 'successful_searches')}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8') as handle:
            handle.write(text+'\n')
        print(f'wrote report to {args.output}', file=sys.stderr)
    print(text)


if __name__ == '__main':
    main()
