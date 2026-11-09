#!/usr/bin/env python3
"""Capture read-only run evidence and summarize bounded acceptance sampling."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .searchflow_acceptance import browser, write_json
else:
    from searchflow_acceptance import browser, write_json


def summarize(journal, tasks):
    owned = {t['campaign_id'] for t in journal['tasks']}
    sources = defaultdict(Counter)
    selected = [t for t in tasks if t['id'] in owned]
    for task in selected:
        values = sources[task['sources'][0]]
        values['tasks'] += 1
        for key in ('requests', 'successful', 'cached', 'new_links'):
            values[key] += task[key]
        values['attempts_without_cache'] += task['requests'] - task['cached']
        values['successes_without_cache'] += task['successful'] - task['cached']
        values[task['status'] + '_tasks'] += 1
    for values in sources.values():
        n = values['attempts_without_cache']
        values['success_rate_without_cache'] = values['successes_without_cache']/n if n else None
    return dict(sources={s: dict(v) for s, v in sources.items()},
                totals=dict(tasks=len(selected), cached=sum(t['cached'] for t in selected),
                            attempts_without_cache=sum(t['requests']-t['cached'] for t in selected),
                            successes_without_cache=sum(t['successful']-t['cached'] for t in selected),
                            new_links=sum(t['new_links'] for t in selected),
                            active_tasks=sum(t['status']=='active' for t in selected)),
                elapsed_seconds=journal.get('elapsed_seconds'),
                started_at=journal['started_at'], ended_at=journal.get('ended_at'),
                aborted=journal.get('abort_reason'), backpressure_skips=journal.get('backpressure_skips', []))


def capture(space, directory):
    journal = json.loads((directory / 'journal.json').read_text())
    if journal['state'] != 'stopped':
        raise RuntimeError('Wait until the sampler has stopped')
    latest = browser(space, [{'path':'/api/serp/tasks?limit=100'}, {'path':'/api/collector-settings'},
                             {'path':'/api/whale/status'}, {'path':'/v1/usage', 'auth':'customer'},
                             {'path':'/v1/monitors', 'auth':'customer'}], directory / 'drained.json')
    details = directory / 'requests'
    details.mkdir(exist_ok=True)
    for task in journal['tasks']:
        browser(space, [dict(path='/api/serp/browse-requests?campaign_id='+task['campaign_id']+'&limit=100&count_total=false')],
                details / (task['campaign_id'] + '.json'))
    return latest


def build(directory):
    journal = json.loads((directory / 'journal.json').read_text())
    final = json.loads((directory / 'drained.json').read_text())
    tasks = final['results'][0]['body']['tasks']
    result = summarize(journal, tasks)
    at_stop = json.loads((directory / 'after.json').read_text())
    result['at_stop'] = summarize(journal, at_stop['results'][0]['body']['tasks'])
    result['after_stop_delta'] = {
        key: result['totals'][key] - result['at_stop']['totals'][key]
        for key in ('attempts_without_cache', 'successes_without_cache', 'new_links')}
    if result['totals']['tasks'] != len(journal['tasks']):
        raise RuntimeError('Missing owned tasks; paginate before generating report')
    raw = {}
    for path in sorted((directory / 'requests').glob('*.json')):
        response = json.loads(path.read_text())['results'][0]
        if response['status'] != 200 or response['body']['has_more']:
            raise RuntimeError('Incomplete request capture: ' + path.name)
        for row in response['body']['rows']:
            raw[row['id']] = row
    field = {'google_web':'organic_results', 'google_news':'news_results',
             'google_trends':'trending_searches', 'google_images':'image_results'}
    cutoff = datetime.fromisoformat(journal['started_at'])
    coverage = defaultdict(Counter)
    for row in raw.values():
        if datetime.fromisoformat(row['completed_at']) < cutoff:
            continue
        counts = coverage[row['source']]
        counts['distinct_requests'] += 1
        counts['status:' + row['status']] += 1
        response = row.get('response') or {}
        items = response.get(field[row['source']]) or []
        counts['result_items'] += len(items)
        counts['related_articles'] += sum(len(x.get('articles') or []) for x in items)
        for item in items:
            counts['missing_title'] += not bool(item.get('title') or item.get('keyword'))
            if row['source'] == 'google_images':
                counts['missing_image_url'] += not bool(item.get('image_url'))
                counts['missing_source_page_url'] += not bool(item.get('source_url'))
                counts['missing_dimensions'] += not bool(item.get('width') and item.get('height'))
                counts['missing_thumbnail'] += not bool(item.get('thumbnail'))
            elif row['source'] == 'google_news':
                counts['google_news_wrapped_links'] += urlsplit(item.get('link') or '').hostname == 'news.google.com'
                counts['missing_date'] += not bool(item.get('date'))
                counts['missing_publisher'] += not bool(item.get('publisher') or item.get('source'))
            elif row['source'] == 'google_web':
                counts['missing_link'] += not bool(item.get('link'))
                counts['missing_snippet'] += not bool(item.get('snippet'))
                counts['missing_rank'] += item.get('position') is None
        if row.get('error_code'):
            counts['error:' + row['error_code']] += 1
    result['visible_request_coverage'] = {s:dict(v) for s,v in coverage.items()}
    result['raw_request_rows'] = len(raw)
    settings = final['results'][1]['body']
    whale = final['results'][2]['body']['settings']
    monitors = final['results'][4]['body']['monitors']
    result['final_switches'] = dict(continuous=settings['continuous_enabled'],
        body=settings['content_fetch_enabled'], uploads=whale['uploads_enabled'], claims=whale['claims_enabled'],
        enabled_monitors=sum(bool(m['enabled']) for m in monitors))
    result['customer_usage'] = final['results'][3]['body']
    manifest = {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob('*.json')) if path.name not in ('summary.json', 'manifest.json')}
    write_json(directory / 'manifest.json', manifest)
    write_json(directory / 'summary.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--capture-space', type=int)
    args = p.parse_args()
    if args.capture_space:
        capture(args.capture_space, args.directory)
    print(json.dumps(build(args.directory), ensure_ascii=False, indent=2))
