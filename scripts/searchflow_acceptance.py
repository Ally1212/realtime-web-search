#!/usr/bin/env python3
"""Bounded acceptance sampling through the user's ego-browser session.

Never enables global collection, article fetching, uploads or platform claims.
Every batch is a one-shot manual task. Ambiguous writes stop new submissions;
the intent journal allows read-only reconciliation instead of replaying POSTs.
"""
from __future__ import annotations

import argparse
import hashlib
from uuid import uuid4
import json
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ('state/fullqa-' + datetime.now().strftime('%Y%m%d'))


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def browser(space, requests, destination):
    """One bounded browser round; POSTs are deliberately never retried."""
    destination = Path(destination).resolve()
    config = dict(space=space, requests=requests, destination=str(destination),
                  keyDirectory=str(Path.home() / '.config/searchflow'))
    source = 'const command = ' + json.dumps(config) + ';\n' + (ROOT / 'scripts/searchflow_acceptance_browser.mjs').read_text()
    result = subprocess.run(['ego-browser', 'nodejs'], input=source, text=True,
                            capture_output=True, timeout=100)
    if result.returncode:
        # The CLI may have dispatched a POST before failing: preserve its intent.
        raise RuntimeError('Browser round failed; inspect intent/output, do not replay: ' + result.stderr[-600:])
    return json.loads(destination.read_text())


def batch_plan(minute):
    topics = ['solar energy', 'battery storage', 'ocean conservation', 'electric vehicles',
              'wind power', 'water treatment', 'climate adaptation', 'semiconductors',
              'robotics', 'public health', 'sustainable agriculture', 'satellite research',
              'heat pumps', 'recycling', 'green hydrogen']
    topic = topics[minute % len(topics)]
    if minute >= len(topics):
        topic += ' applications'
    plans = [('google_web', [f'{topic} research November 2026',
                             f'{topic} technology standards 2026'])]
    if minute % 5 == 0:
        plans += [('google_news', [f'{topic} research']),
                  ('google_images', [f'{topic} diagram'])]
    if minute % 15 == 0:
        plans += [('google_trends', ['当前热门趋势'])]
    return plans


def readiness(directory):
    """Gate on actual evidence; skipped tests and failed sources do not pass."""
    suites = list(ET.parse(directory / 'pytest.xml').getroot().iter('testsuite'))
    if not suites or any(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped')):
        raise RuntimeError('Full regression must finish without failure/error/skip')
    outcomes = json.loads((directory / 'small-outcomes.json').read_text())
    if {x['source'] for x in outcomes} != {'google_web', 'google_news', 'google_trends', 'google_images'}:
        raise RuntimeError('Four-source small sample evidence required')
    for result in outcomes:
        if result['body']['status'] != 'succeeded' or not result.get('replay', {}).get('reused'):
            raise RuntimeError('Source or idempotency gate failed')
        if {x['format'] for x in result['exports'] if x['status'] == 200} != {'csv', 'jsonl'}:
            raise RuntimeError('Both exports must succeed')
    validation = json.loads((directory / 'small-validation.json').read_text())
    if len(validation) != 4 or any(x['csv_rows'] < 1 for x in validation):
        raise RuntimeError('Export content validation is required')
    isolation = json.loads((directory / 'private-isolation.json').read_text())
    if len(isolation) != 4 or any(x['status'] != 404 for x in isolation):
        raise RuntimeError('Private sample isolation failed')
    return dict(at=utc(), passed=True, junit_case_records_including_subtests=sum(int(s.get('tests', 0)) for s in suites),
                sources=[x['source'] for x in outcomes],
                scope='local regression + four-source sample/export/isolation; external paid integrations not admitted')


def owned_task_ids(journal, tasks):
    """A random creation-key digest proves ownership after a lost POST response."""
    owned = {t['campaign_id'] for t in journal['tasks']}
    hashes = {hashlib.sha256(i['idempotency_key'].encode()).hexdigest() for i in journal.get('intents', []) if i.get('idempotency_key')}
    owned.update(str(t['id']) for t in tasks if t.get('options', {}).get('_creation_key') in hashes)
    return owned


def expired_owned_tasks(journal, tasks, now, timeout_seconds=180):
    owned = owned_task_ids(journal, tasks)
    return [t for t in tasks if t['id'] in owned and t['status'] == 'active'
            and (now - datetime.fromisoformat(t['created_at'].replace('Z', '+00:00'))).total_seconds() >= timeout_seconds]


def watchdog(space, journal_path, timeout_seconds=3600):
    """Independent batch deadline: retries cannot monopolize a source forever."""
    directory = journal_path.parent / 'watchdog'
    directory.mkdir(exist_ok=True)
    events_file = directory / 'events.json'
    events = json.loads(events_file.read_text()) if events_file.exists() else []
    while True:
        journal = json.loads(journal_path.read_text())
        if journal['state'] == 'stopped':
            write_json(directory / 'finished.json', dict(at=utc(), events=events))
            return
        run_expired = (datetime.now(timezone.utc)-datetime.fromisoformat(journal['started_at'])).total_seconds() >= journal['duration_minutes']*60+60
        if journal['state'] != 'running' and not run_expired:
            time.sleep(1)
            continue
        tasks = browser(space, [{'path': '/api/serp/tasks?limit=100'}], directory / 'latest.json')['results'][0]['body']['tasks']
        expired = expired_owned_tasks(journal, tasks, datetime.now(timezone.utc), 0 if run_expired else timeout_seconds)
        for task in expired:
            if json.loads(journal_path.read_text())['state'] != 'running' and not run_expired:
                break
            intent = dict(at=utc(), campaign_id=task['id'], source=task['sources'],
                          reason='run_deadline' if run_expired else 'batch_deadline', requests=task['requests'], successful=task['successful'], state='dispatching')
            events.append(intent)
            write_json(directory / 'events.json', events)
            result = browser(space, [dict(path=f"/api/serp/tasks/{task['id']}/stop", method='POST', auth='operator', payload={})],
                             directory / (task['id'] + '.json'))['results'][0]
            state = 'confirmed' if result['status'] == 200 else 'rejected'
            if result['status'] == 400:
                # The sampler can stop the same task between our GET and POST.
                # Reconcile its state once; never replay the stop request.
                current = browser(space, [{'path':'/api/serp/tasks?limit=100'}], directory / 'stop-reconciliation.json')
                if any(t['id'] == task['id'] and t['status'] == 'stopped' for t in current['results'][0]['body']['tasks']):
                    state = 'already_stopped'
            intent.update(state=state, http_status=result['status'])
            write_json(directory / 'events.json', events)
            print(json.dumps(intent), flush=True)
            if state == 'rejected':
                raise RuntimeError('Watchdog stop rejected; inspect evidence')
        if run_expired:
            write_json(directory / 'finished.json', dict(at=utc(), events=events, reason='run_deadline'))
            return
        time.sleep(20)


def collect(space, minutes, directory):
    if not 1 <= minutes <= 120:
        raise ValueError('Duration must be between 1 and 120 minutes')
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'journal.json').exists():
        raise RuntimeError('Existing journal must not be overwritten; choose a new output directory')
    journal = dict(started_at=utc(), duration_minutes=minutes, tasks=[], intents=[], checkpoints=[], state='preflight')
    journal_path = directory / 'journal.json'
    stop = False

    def interrupted(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    before = browser(space, [{'path': '/api/collector-settings'}, {'path': '/api/whale/status'}], directory / 'before.json')
    settings, whale = [x['body'] for x in before['results']]
    if settings['continuous_enabled'] or settings['content_fetch_enabled'] or whale['settings']['uploads_enabled'] or whale['settings']['claims_enabled']:
        raise RuntimeError('Expected paused baseline; do not override operator state')
    start = time.monotonic()
    deadline = start + minutes * 60
    journal['state'] = 'running'
    write_json(journal_path, journal)
    try:
        for minute in range(minutes):
            while time.monotonic() < start + minute * 60 and not stop:
                time.sleep(min(1, max(0, start + minute * 60 - time.monotonic())))
            if stop or time.monotonic() >= deadline:
                break
            # Bound each source separately: a slow Web request must not block RSS.
            checkpoint = browser(space, [{'path': '/api/serp/tasks?limit=100'},
                                         {'path': '/api/serp/stats'}], directory / f'checkpoint-{minute:02}.json')
            journal['checkpoints'].append(dict(minute=minute, at=utc()))
            owned = {t['campaign_id'] for t in journal['tasks']}
            active = [t for t in checkpoint['results'][0]['body']['tasks'] if t['id'] in owned and t['status'] == 'active']
            owned_rows = [t for t in checkpoint['results'][0]['body']['tasks'] if t['id'] in owned]
            if active:
                browser(space, [{'path':'/api/serp/tasks/'+t['id']+'/progress'} for t in active], directory / f'progress-{minute:02}.json')
            attempts = sum(t.get('requests', 0) for t in owned_rows)
            successes = sum(t.get('successful', 0) for t in owned_rows)
            if attempts >= 20 and (attempts - successes) / attempts > .5:
                journal['abort_reason'] = 'failure rate exceeds 50% after at least 20 attempts'
                stop = True
                break
            active_sources = {source for task in active for source in task['sources']}
            for source, words in batch_plan(minute):
                if stop or time.monotonic() >= deadline:
                    break
                if source in active_sources:
                    journal.setdefault('backpressure_skips', []).append(dict(minute=minute, source=source))
                    write_json(journal_path, journal)
                    continue
                payload = dict(keywords=words, sources=[source], start=True, mode='once',
                               max_successful_searches=len(words), upload_to_whale=False,
                               options=dict(web_region='US', web_language='en', web_pages=1))
                intent = dict(minute=minute, source=source, payload=payload, at=utc(), state='dispatching', idempotency_key='qa-'+uuid4().hex)
                journal['intents'].append(intent)
                write_json(journal_path, journal)
                out = browser(space, [dict(path='/api/serp/tasks', method='POST', auth='operator', payload=payload, idempotency=intent['idempotency_key'])],
                              directory / f'batch-{minute:02}-{source}.json')['results'][0]
                if out['status'] != 201:
                    intent['state'] = 'rejected'
                    raise RuntimeError(f"Task rejected: {out['status']}")
                intent.update(state='created', campaign_id=out['body']['campaign_id'])
                journal['tasks'].append(dict(out['body'], source=source, minute=minute))
                write_json(journal_path, journal)
            print(json.dumps(dict(minute=minute, tasks=len(journal['tasks']), at=utc())), flush=True)
        while time.monotonic() < deadline and not stop:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    finally:
        journal.update(state='stopping', stop_requested_at=utc(), elapsed_seconds=time.monotonic()-start)
        write_json(journal_path, journal)
        tasks = browser(space, [{'path': '/api/serp/tasks?limit=100'}], directory / 'stop-before.json')['results'][0]['body']['tasks']
        owned = owned_task_ids(journal, tasks)
        known = {t['campaign_id'] for t in journal['tasks']}
        for t in tasks:
            if str(t['id']) in owned-known:
                journal['tasks'].append(dict(campaign_id=str(t['id']),source=t['sources'][0],reconciled_from_creation_key=True))
        write_json(journal_path,journal)
        actions = [dict(path=f"/api/serp/tasks/{t['id']}/stop", method='POST', auth='operator', payload={})
                   for t in tasks if t['id'] in owned and t['status'] in ('active', 'paused', 'failed', 'completed')]
        if actions:
            browser(space, actions, directory / 'stop-actions.json')
        final = browser(space, [{'path': '/api/serp/tasks?limit=100'}, {'path': '/api/serp/stats'},
                                {'path': '/api/collector-settings'}, {'path': '/api/whale/status'}], directory / 'after.json')
        if any(t['status']=='active' for t in final['results'][0]['body']['tasks'] if t['id'] in owned):
            raise RuntimeError('Owned task still active after stop')
        journal.update(state='stopped', ended_at=utc(), interrupted=stop)
        write_json(journal_path, journal)
    return journal


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--space', type=int, required=True)
    p.add_argument('--minutes', type=int, default=30)
    p.add_argument('--output', type=Path, default=ARTIFACTS / 'longrun')
    p.add_argument('--evidence', type=Path, default=ARTIFACTS)
    p.add_argument('--watchdog-journal', type=Path)
    p.add_argument('--task-timeout-seconds', type=int, default=3600)
    args = p.parse_args()
    if args.watchdog_journal:
        watchdog(args.space, args.watchdog_journal.resolve(), args.task_timeout_seconds)
    else:
        write_json(args.evidence / 'readiness.json', readiness(args.evidence))
        collect(args.space, args.minutes, args.output.resolve())
