"""Independent fixed-window auxiliary metrics; never count cache hits as calls."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .fetcher import normalize_url


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def summarize_auxiliary(events: list[dict[str, Any]], start: datetime, end: datetime,
                        baseline_hashes: set[str]) -> dict[str, Any]:
    def at(event):
        value = event['occurred_at']
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    def inside(event):
        return start <= at(event) < end

    grouped = defaultdict(list)
    for event in events:
        grouped[event['kind']].append(event)
    starts = {str(e['request_id']): e for e in grouped['request_started']}
    finishes = {str(e['request_id']): e for e in grouped['request_finished']}
    content = {str(e['request_id']): e for e in grouped['content_examined']}
    start_window = {key for key, e in starts.items() if inside(e)}
    unfinished = start_window - finishes.keys()
    calls = [e for e in grouped['request_finished'] if inside(e) and e['data']['attempted']]
    window_results = [e for e in grouped['discovery_result'] if inside(e)]
    sources = defaultdict(Counter)
    ids = set()
    orphan = set()
    mismatch = set()
    content_mismatch = set()
    orphan_content = set()
    for key, event in content.items():
        parent = starts.get(key)
        if parent is None:
            orphan_content.add(key)
        elif (str(parent['trace_id']), parent['source'], parent['operation']) != (
                str(event['trace_id']), event['source'], event['operation']):
            content_mismatch.add(key)
    for event in calls:
        key = str(event['request_id'])
        ids.add(key)
        parent = starts.get(key)
        if parent is None:
            orphan.add(key)
        elif (str(parent['trace_id']), parent['source'], parent['operation']) != (
                str(event['trace_id']), event['source'], event['operation']):
            mismatch.add(key)
        data = event['data']
        examined = content.get(key) if key not in content_mismatch | orphan_content else None
        captcha = data['url_captcha'] or bool(examined and examined['data']['explicit_captcha'])
        counter = sources[event['source']]
        counter['completed_client_calls'] += 1
        counter['explicit_captcha_calls'] += int(captcha)
        counter['http_successes'] += int(data['error_type'] is None and
                                       (data['http_status'] == 304 or isinstance(data['http_status'], int)
                                        and 200 <= data['http_status'] < 300))
        counter['http_errors'] += int(isinstance(data['http_status'], int) and data['http_status'] >= 400)
        counter['transport_errors'] += int(data['error_type'] is not None)
        counter['body_not_examined'] += int(examined is None)
        if isinstance(data['redirect_hops'], int):
            counter['reported_redirect_hops'] += data['redirect_hops']
    for event in window_results:
        counter = sources[event['source']]
        counter['discovery_batches'] += 1
        counter['cache_hit_batches'] += int(event['data']['cache_hit'])
        counter['offline_batches'] += int(event['data']['offline'])
        counter['discovery_error_batches'] += int(event['data']['error_type'] is not None)
    eligible = {url_hash(url) for event in window_results for url in event['data']['eligible_result_urls']}
    duplicate_kind_ids = sum(len(grouped[k]) - len({str(e['request_id']) for e in grouped[k]})
                             for k in ('request_started', 'request_finished', 'content_examined'))
    missing_batch = {str(e['trace_id']) for e in grouped['request_started'] if inside(e)} - {
        str(e['trace_id']) for e in grouped['discovery_result']}
    missing_batch_parents = {str(key) for e in window_results for key in e['data']['request_ids']} - starts.keys()
    batch_mismatch = {(str(e['trace_id']), str(key)) for e in window_results
                      for key in e['data']['request_ids'] if str(key) in starts
                      and (str(e['trace_id']), e['source'], e['operation']) != (
                          str(starts[str(key)]['trace_id']), starts[str(key)]['source'],
                          starts[str(key)]['operation'])}
    totals = Counter()
    for counter in sources.values():
        totals.update(counter)
    count = totals['completed_client_calls']
    return dict(
        started_records_in_window=len(start_window), completed_client_calls=count,
        proxy_or_preflight_failures=sum(inside(e) and not e['data']['attempted']
                                       for e in grouped['request_finished']),
        explicit_captcha_calls=totals['explicit_captcha_calls'],
        explicit_captcha_rate=totals['explicit_captcha_calls'] / count if count else None,
        unique_eligible_discovered_links=len(eligible), valid_new_discovered_links=len(eligible - baseline_hashes),
        cache_hit_batches=totals['cache_hit_batches'], offline_batches=totals['offline_batches'],
        unfinished_started_records=len(unfinished), orphan_completion_records=len(orphan),
        request_correlation_mismatches=len(mismatch), duplicate_request_kind_records=duplicate_kind_ids,
        orphan_content_records=len(orphan_content), content_correlation_mismatches=len(content_mismatch),
        started_here_finished_after_window=sum(at(finishes[key]) >= end for key in start_window & finishes.keys()),
        request_audit_complete=not (unfinished or orphan or mismatch or duplicate_kind_ids
                                    or orphan_content or content_mismatch),
        missing_discovery_batch_completions=len(missing_batch),
        missing_batch_request_parents=len(missing_batch_parents),
        batch_request_correlation_mismatches=len(batch_mismatch),
        discovery_batch_audit_complete=not (missing_batch or missing_batch_parents or batch_mismatch),
        body_not_examined=totals['body_not_examined'],
        sources={source: dict(counter) for source, counter in sorted(sources.items())},
        definition='Completed application HTTP client GET/POST calls whose completion event lies in the fixed window; '
                   'redirects are separately reported hops, not independently traced packets or retries. '
                   'Cache and offline-resolution batches are not calls. CAPTCHA counts only explicit observed markers; '
                   'unexamined bodies and unfinished starts remain visible. Links are normalized public URLs observed '
                   'by discovery batches, deduplicated against retained baseline links; they do not imply fetched bodies.',
    )


def read_auxiliary_window(db, campaign_ids: list[str], start: datetime, end: datetime,
                          baseline_hashes: set[str]) -> dict[str, Any]:
    """Read only the relevant traces; retain linked events beyond the hour boundary."""
    readonly = db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
    if readonly != 'on':
        raise ValueError('auxiliary audit metrics require a read-only transaction')
    events = db.execute(
        "WITH traces AS (SELECT DISTINCT trace_id FROM discovery_aux_audit_events "
        "WHERE campaign_id=ANY(%s::uuid[]) AND occurred_at>=%s AND occurred_at<%s) "
        "SELECT e.* FROM discovery_aux_audit_events e JOIN traces t USING(trace_id) "
        "WHERE e.campaign_id=ANY(%s::uuid[]) ORDER BY e.occurred_at,e.event_id",
        (campaign_ids, start, end, campaign_ids),
    ).fetchall()
    urls = list({url for e in events if e['kind'] == 'discovery_result'
                 for url in e['data']['eligible_result_urls']})
    historical = set(baseline_hashes)
    if urls:
        for table in ('discovery_known_result_urls', 'google_known_result_urls'):
            historical.update(url_hash(r['url']) for r in db.execute(
                f'SELECT url FROM {table} WHERE url=ANY(%s) AND first_seen<%s', (urls, start)))
    result = summarize_auxiliary(events, start, end, historical)
    result['database_read_only'] = readonly
    return result


def read_google_discovery_window(db, campaign_ids: list[str], start: datetime, end: datetime,
                                baseline_hashes: set[str]) -> dict[str, Any]:
    """Keep Web SERPs, News search feeds and other HTTP operations distinct."""
    aux = read_auxiliary_window(db, campaign_ids, start, end, baseline_hashes)
    web = db.execute(
        "SELECT provider,classification,error_code,result_urls FROM google_serp_attempts "
        "WHERE created_at>=%s AND created_at<%s", (start, end),
    ).fetchall()
    batches = db.execute(
        "SELECT data FROM discovery_aux_audit_events WHERE campaign_id=ANY(%s::uuid[]) "
        "AND kind='discovery_result' AND occurred_at>=%s AND occurred_at<%s",
        (campaign_ids, start, end),
    ).fetchall()
    web_urls = {url for row in web for url in row['result_urls']}
    auxiliary_urls = {url for row in batches for url in row['data']['eligible_result_urls']}
    all_urls = list(web_urls | auxiliary_urls)
    historical = set(baseline_hashes)
    if all_urls:
        for table in ('discovery_known_result_urls', 'google_known_result_urls'):
            historical.update(url_hash(r['url']) for r in db.execute(
                f'SELECT url FROM {table} WHERE url=ANY(%s) AND first_seen<%s', (all_urls, start)))
    web_hashes = {url_hash(url) for url in web_urls}
    auxiliary_hashes = {url_hash(url) for url in auxiliary_urls}
    web_captcha = sum(row['classification'] == 'captcha' or row['error_code'] == 'google_captcha' for row in web)
    news_search = aux['sources'].get('google_news', {}).get('completed_client_calls', 0)
    return dict(
        google_web_serp_calls=len(web), google_web_explicit_captcha_calls=web_captcha,
        google_web_explicit_captcha_rate=web_captcha / len(web) if web else None,
        google_news_search_feed_calls=news_search,
        completed_google_keyword_search_calls=len(web) + news_search,
        other_auxiliary_client_calls=aux['completed_client_calls'] - news_search,
        google_web_valid_new_links=len(web_hashes - historical),
        auxiliary_valid_new_links=len(auxiliary_hashes - historical),
        combined_unique_eligible_discovery_links=len(web_hashes | auxiliary_hashes),
        combined_valid_new_discovery_links=len((web_hashes | auxiliary_hashes) - historical),
        overlapping_web_auxiliary_links=len(web_hashes & auxiliary_hashes),
        auxiliary=aux,
        web_scope='All completed Web SERPs in the database window; requires an independently verified exclusive run.',
        definition='Web counts completed Google SERP client calls. News search counts keyword RSS-search calls; '
                   'resolver GET/POST, general RSS and Trends are separate auxiliary HTTP transactions. '
                   'The combined link count is a union, not a sum, and excludes retained historical links. '
                   'No multiplication by reported redirect hops or unverified Google upstream attempts.',
    )
