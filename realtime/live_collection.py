"""Safe, durable failure information for the two live collection entry points."""
from .product import ProductError
from .serp_fields import normalize_serp_url

PROXY_RETRY_CODES = {
    'proxy_pool_unavailable', 'proxy_transport_failed', 'google_web_circuit_open',
    'google_provider_cooling', 'openserp_unavailable',
    'google_web_rate_wait',
    'openserp_proxy_connect', 'openserp_proxy_timeout', 'google_transport_error',
    'google_timeout', 'trends_transport_error', 'google_query_inflight',
    'keyword_trends_busy', 'feed_query_inflight',
}
# A source 429 is transient too, but unlike transport failures it is cached for
# two minutes by the Trends collector.  Retry after that cache expires instead
# of polling every 15 seconds and repeatedly reading the same failed snapshot.
RETRYABLE_COLLECTION_CODES = PROXY_RETRY_CODES | {'upstream_rate_limited'}
RETRY_INTERVAL_SECONDS = 15
RATE_LIMIT_RETRY_SECONDS = 120


def retry_interval(code):
    return RATE_LIMIT_RETRY_SECONDS if code == 'upstream_rate_limited' else RETRY_INTERVAL_SECONDS
WEB_DELIVERY_RESERVE_SECONDS = 25
WEB_MAX_LIVE_SECONDS = 900
WEB_RECOVERY_ROUNDS = 3

_ALIASES = {
    'google_proxy_unavailable': 'proxy_pool_unavailable',
    'openserp_proxy_unavailable': 'proxy_pool_unavailable',
    'trends_proxy_unavailable': 'proxy_pool_unavailable',
    'ProxyError': 'proxy_transport_failed',
    'trends_rate_limited': 'upstream_rate_limited',
    'google_http_429': 'upstream_rate_limited',
    'trends_http_403': 'google_http_403',
    'ConnectionError': 'proxy_transport_failed',
    'ReadTimeout': 'proxy_transport_failed',
}
COLLECTION_ERROR_CODES = PROXY_RETRY_CODES | {
    'google_http_403', 'upstream_rate_limited', 'google_captcha', 'openserp_proxy_auth',
    'upstream_no_successful_result',
    'search_deadline_exceeded',
}


def collection_error(rows, errors):
    """Use only recognized codes. Discovery error strings can contain query text."""
    for error in reversed(errors or ()):
        suffix = error.rsplit(None, 1)[-1] if error.strip() else ''
        code = _ALIASES.get(suffix, suffix)
        if code in COLLECTION_ERROR_CODES:
            return code
    if rows:
        row = max(rows, key=lambda r: str(r.get('completed_at') or ''))
        if row.get('http_status') == 429:
            return 'upstream_rate_limited'
        if row.get('http_status') == 403:
            return 'google_http_403'
        raw = row.get('error_code')
        code = _ALIASES.get(raw, raw)
        if code in COLLECTION_ERROR_CODES:
            return code
    return 'upstream_no_successful_result'


def needs_collection(source, rows, page_budget):
    if not rows:
        return True
    if any(r['classification'] == 'empty' for r in rows):
        return False
    wait = collection_error(rows, ()) in RETRYABLE_COLLECTION_CODES
    if source == 'google_web':
        attempted = {r['page'] for r in rows}
        successful = {r['page'] for r in rows if r['classification'] == 'results'}
        return len(attempted) < page_budget or (wait and len(successful) < page_budget)
    return wait and not any(r['classification'] == 'results' for r in rows)


def can_deliver_web_results(rows, selected_count, requested, *, collection_finished):
    return selected_count > 0 and (
        collection_finished or selected_count >= requested
        or any(r.get('http_status') in (403, 429) for r in rows)
    )


def recover_web_pages(rows, errors, *, spec, state, save, run, read, can_start):
    """Use the remaining live budget without extending the requested page range."""
    page_budget, requested = spec['google_pages'], spec['max_items']
    pages = set(range(1, page_budget + 1))
    while state.get('web_recovery_rounds', 0) < WEB_RECOVERY_ROUNDS and rows and can_start():
        if errors and collection_error((), errors) in PROXY_RETRY_CODES:
            break
        page_urls = {}
        for row in rows:
            if row['classification'] not in ('results', 'empty'):
                continue
            urls = page_urls.setdefault(row['page'], set())
            for item in (row['response'] or {}).get('organic_results') or []:
                url = normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
                if url:
                    urls.add(url)
        if len(set().union(*page_urls.values())) >= requested:
            break
        missing = pages - page_urls.keys()
        sparse = {page for page, urls in page_urls.items() if 0 < len(urls) < spec['page_size']}
        # Give missing pages the first opportunity, then use any remaining
        # capacity to revisit pages with few usable results.
        rounds = state.get('web_recovery_rounds', 0)
        targets = missing if missing and rounds == 0 else missing | sparse
        if not targets:
            break
        state['web_recovery_rounds'] = rounds + 1
        save()
        errors = run(skip_web_pages=pages - targets, refresh_web_pages=targets & sparse)
        updated = read()
        if len(updated) <= len(rows):
            break
        rows = updated
    return rows, errors


def wait_for_web_capacity(errors, *, state, count, requested, remaining):
    """Defer an incomplete live query through the existing shared cooldown."""
    if (not errors or count >= requested or remaining <= RETRY_INTERVAL_SECONDS
            or state.get('web_recovery_rounds', 0) >= WEB_RECOVERY_ROUNDS):
        return
    code = collection_error((), errors)
    if code in PROXY_RETRY_CODES:
        raise ProductError(code, code, 503)


def check_collection(source, rows, errors, page_budget, *, deliver_saved=False):
    successful = [r for r in rows if r['classification'] in ('results', 'empty')]
    if not successful:
        code = collection_error(rows, errors)
        raise ProductError(code, code, 503)
    if (source == 'google_web' and not deliver_saved and len({r['page'] for r in successful}) < page_budget
            and not any(r['classification'] == 'empty' for r in successful)):
        code = collection_error(rows, errors)
        if code in PROXY_RETRY_CODES:
            raise ProductError(code, code, 503)
    return successful
