"""Per-query outcomes, separating persisted attempts, cache reuse and waiting."""
from datetime import datetime, timezone


def summarize(task, requests, frontier, *, truncated=False, now=None):
    now = now or datetime.now(timezone.utc)
    queries = list(dict.fromkeys([task['query'], *(task.get('aliases') or [])]))
    sources = task['sources']
    options = task.get('options') or {}
    pages = options.get('web_pages', 1)
    groups = {}
    for source in sources:
        for query in (queries[:1] if source == 'google_trends' else queries):
            for page in range(1, pages+1 if source == 'google_web' else 2):
                groups[(source,query,page)] = []
    for r in requests:
        query = queries[0] if r['source']=='google_trends' else r['query']
        key = (r['source'],query,r['page'])
        if key in groups:
            groups[key].append(r)
    rows = []
    for (source,query,page), records in groups.items():
        fresh = [r for r in records if not r['reused']]
        success = [r for r in records if r['classification'] in ('results','empty')]
        good_fresh = [r for r in success if not r['reused']]
        recovered = bool(good_fresh and any(r['classification'] not in ('results','empty') and r['completed_at'] < good_fresh[0]['completed_at'] for r in fresh))
        f = next((f for f in frontier if source=='google_web' and f['query']==query), None)
        if success:
            state = 'succeeded' if good_fresh else 'cache_reused'
        elif task['status'] != 'active':
            state = 'paused' if task['status']=='paused' else 'ended_without_success'
        elif f and f['state']=='running' and f.get('lease_expires_at') and f['lease_expires_at']>now:
            state = 'inflight'
        elif f and f.get('next_run_at') and f['next_run_at']>now:
            state = 'waiting_retry'
        else:
            state = 'pending_retry' if fresh else 'pending'
        rows.append(dict(source=source,query=query,page=page,state=state,
            fresh_attempts=len(fresh),cached_associations=len(records)-len(fresh),
            successful_attempts=len(good_fresh),recovered=recovered,
            first_success_completed_at=success[0]['completed_at'] if success else None,
            recovery_seconds=(good_fresh[0]['completed_at']-fresh[0]['completed_at']).total_seconds() if recovered else None,
            latency_basis='persisted_completion_to_completion',
            next_retry_at=f['next_run_at'] if f and state=='waiting_retry' else None,
            last_error=f.get('last_error') if f and not success else None))
    attempted = [r for r in rows if r['fresh_attempts']]
    scope = bool(rows) and all(r['state'] in ('succeeded','cache_reused') for r in rows)
    # Frontier completion proves skipped pages ended naturally (short/empty SERP).
    # A pending/cooling frontier can never pass merely because a batch was empty.
    if 'google_web' in sources:
        web_done = all(any(f['query']==q and f['state']=='completed' for f in frontier) for q in queries)
        other_done = all(r['state'] in ('succeeded','cache_reused') for r in rows if r['source']!='google_web')
        scope = web_done and other_done
    return dict(campaign_id=str(task['id']),status=task['status'],stop_reason=task.get('stop_reason'),
        scope_complete=scope and not truncated,truncated=truncated,queries=rows,
        fresh_attempts=sum(r['fresh_attempts'] for r in rows),
        successful_attempts=sum(r['successful_attempts'] for r in rows),
        attempted_query_pages=len(attempted),
        query_page_success_rate=(sum(r['successful_attempts']>0 for r in attempted)/len(attempted)) if attempted and not truncated else None,
        recovered_query_pages=sum(r['recovered'] for r in rows),
        retry_window_seconds=options.get('retry_window_seconds'),
        retry_started_at=options.get('_retry_started_at'),
        note='Cache reuse is separate; active waits are not terminal failures. Latency excludes request duration before the first persisted completion.')
