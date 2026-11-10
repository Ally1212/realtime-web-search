"""Seven-day read-only delivery integrity watch; never pulls, acknowledges or searches.

Checks a fixed acknowledged archive, its request/URL ledgers, repeatable export
hashes, and storage growth. It does not claim seven days of new SERP collection
or live upstream availability. Deadline persists across service restarts.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import time

from realtime.customer_subscriptions import checksum, batch_file


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as file:
        os.chmod(temp, 0o600)
        json.dump(value, file, ensure_ascii=False, default=str, indent=2)
        file.flush(); os.fsync(file.fileno())
    os.replace(temp, path)


def probe(store, subscription_id, batch_id):
    from realtime.customer_data import selection
    from realtime.product import identifier
    with store.base.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        db.execute("SET LOCAL statement_timeout='15s'")
        row = db.execute('SELECT b.*,s.account_id,s.filters FROM sf_data_batches b JOIN sf_data_subscriptions s ON s.id=b.subscription_id '
                         'WHERE b.id=%s AND s.id=%s', (identifier(batch_id), identifier(subscription_id))).fetchone()
        if not row or not row['acknowledged_at']:
            raise ValueError('Watch requires a fixed acknowledged batch')
        if checksum(row['payload']) != row['checksum']:
            raise ValueError('Persisted payload checksum mismatch')
        rids = [r['request_id'] for r in row['payload']['requests']]
        uids = list({e['url_id'] for e in row['payload']['events'] if e['url_id']})
        where, args, *_ = selection(row['filters'], row['account_id'])
        visible = db.execute('SELECT count(*) n FROM serp_requests r WHERE r.request_id=ANY(%s::uuid[]) AND ' + ' AND '.join(where), [rids] + args).fetchone()['n']
        if visible != len(rids):
            raise ValueError('Source access changed')
        seen_requests = db.execute('SELECT count(*) n FROM sf_data_seen_requests WHERE subscription_id=%s AND request_id=ANY(%s::uuid[])', (row['subscription_id'], rids)).fetchone()['n']
        seen_urls = db.execute('SELECT count(*) n FROM sf_data_seen_urls WHERE subscription_id=%s AND url_id=ANY(%s::text[])', (row['subscription_id'], uids)).fetchone()['n']
        if seen_requests != len(rids) or seen_urls != len(uids):
            raise ValueError('Acknowledgement ledger mismatch')
        totals = db.execute('SELECT count(*) batches,coalesce(sum(payload_bytes),0)::bigint payload_bytes FROM sf_data_batches').fetchone()
        storage = db.execute("SELECT sum(pg_total_relation_size(t::regclass))::bigint bytes FROM unnest(ARRAY['sf_data_batches','sf_data_seen_requests','sf_data_seen_urls','sf_data_subscription_baselines']) t").fetchone()['bytes']
        source_requests = db.execute('SELECT count(*) n FROM serp_requests').fetchone()['n']
    envelope = dict(batch_id=str(row['id']), payload=row['payload'], sha256=row['checksum'])
    hashes = {}
    for fmt in ('jsonl', 'csv'):
        raw, _ = batch_file(envelope, fmt)
        hashes[fmt] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    return dict(batch_sha256=row['checksum'], exports=hashes, request_count=len(rids), unique_urls=len(uids),
                batches=totals['batches'], stored_payload_bytes=totals['payload_bytes'], storage_bytes=storage,
                source_requests=source_requests)


def step(state, sample, *, now, days, interval, error=None):
    """Pure state transition, also used for clock/restart/gap tests."""
    if state is None:
        state = dict(version='delivery-soak-1', started_at=now.isoformat(), deadline=(now + timedelta(days=days)).isoformat(),
                     interval_seconds=interval, checks=0, successes=0, failures=0, gaps=0, status='running')
    else:
        state = dict(state)
    if now < timestamp(state['started_at']):
        raise ValueError('Wall clock moved before start')
    deadline = timestamp(state['deadline'])
    if state['status'] in ('completed', 'completed_with_issues'):
        return state
    if now >= deadline:
        expected = math.ceil((deadline - timestamp(state['started_at'])).total_seconds() / state['interval_seconds'])
        state.update(finished_at=now.isoformat(), expected_checks=expected,
                     status='completed' if state['failures'] == 0 and state['gaps'] == 0 and state['successes'] >= expected else 'completed_with_issues')
        return state
    if state.get('last_at') and (now - timestamp(state['last_at'])).total_seconds() > state['interval_seconds'] * 2:
        state['gaps'] += 1
    state['checks'] += 1
    state['last_at'] = now.isoformat()
    state['last_error'] = error
    if sample is not None:
        if 'baseline' not in state:
            state['baseline'] = sample
        base = state['baseline']
        if sample['batch_sha256'] != base['batch_sha256'] or sample['exports'] != base['exports']:
            state['last_error'] = 'Archive exports changed'
        if sample['source_requests'] < base['source_requests']:
            state['last_error'] = 'Source request count decreased'
        state['latest'] = sample
        state['storage_growth_bytes'] = sample['storage_bytes'] - base['storage_bytes']
    elif error is None:
        state['last_error'] = 'No sample returned'
    if state['last_error']:
        state['failures'] += 1
    else:
        state['successes'] += 1
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--subscription-id', required=True)
    p.add_argument('--batch-id', required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--days', type=int, default=7, choices=range(1, 31))
    p.add_argument('--interval', type=int, default=900)
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    if not 60 <= args.interval <= 86400:
        p.error('interval must be 60–86400 seconds')
    import fcntl
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = args.state.with_suffix('.lock').open('a')
    os.chmod(lock.name, 0o600)
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    from realtime.config import Config
    from realtime.product import ProductStore
    store = ProductStore(Config().database_url)  # No initialization or migrations.
    state = json.loads(args.state.read_text()) if args.state.exists() else None
    if state and (state.get('subscription_id') != args.subscription_id or state.get('batch_id') != args.batch_id):
        p.error('Existing watch state belongs to another batch')
    while True:
        now = datetime.now(timezone.utc)
        due = timestamp(state['last_at']) + timedelta(seconds=state['interval_seconds']) if state and state.get('last_at') else now
        deadline = timestamp(state['deadline']) if state else now + timedelta(days=args.days)
        if now < min(due, deadline) and not args.once:
            time.sleep(min(60, (min(due, deadline) - now).total_seconds()))
            continue
        sample, error = None, None
        if now < deadline:
            try:
                sample = probe(store, args.subscription_id, args.batch_id)
            except Exception as exc:
                error = type(exc).__name__  # Never put connection strings or source payloads in logs.
        state = step(state, sample, now=now, days=args.days, interval=args.interval, error=error)
        state.update(subscription_id=args.subscription_id, batch_id=args.batch_id)
        atomic_json(args.state, state)
        journal = args.state.with_suffix('.jsonl')
        with journal.open('a') as file:
            os.chmod(journal, 0o600)
            file.write(json.dumps({k: state.get(k) for k in ('last_at', 'status', 'checks', 'successes', 'failures', 'gaps', 'last_error', 'storage_growth_bytes')}) + '\n')
        print(json.dumps({k: state.get(k) for k in ('status', 'deadline', 'checks', 'failures', 'gaps')}), flush=True)
        if args.once or state['status'] != 'running':
            return


if __name__ == '__main__':
    main()
