"""Safe, durable failure information for the two live collection entry points."""
import random
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

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
WEB_RECOVERY_ROUNDS = 10
WEB_RECOVERY_STALL_ROUNDS = 2
WEB_MIN_RESUME_SECONDS = 30
WEB_PERMANENT_BLOCK_CODES = {'google_http_403', 'google_captcha', 'google_consent'}
WEB_ACCESS_BLOCK_CODES = WEB_PERMANENT_BLOCK_CODES | {'google_http_429'}


def web_deadline_seconds(max_items):
    """One 100-item page-by-page Google collection measured ~10 minutes.

    The old fixed 300-second platform deadline expired mid-collection for large
    requests, which then looked like ``search_deadline_exceeded``. Scale the
    deadline with the requested item count and keep the 10-item fast path short.
    """
    if type(max_items) is not int or max_items <= 10:
        return 300
    return min(WEB_MAX_LIVE_SECONDS, 300 + 6 * max_items)


class WebRateWait(ProductError):
    def __init__(self, retry_at):
        super().__init__('upstream_rate_limited', 'Google Web 限流，等待冷却后续采', 429)
        self.retry_at = retry_at


def retry_after_timestamp(value, received_at):
    if not isinstance(value, str):
        return None
    try:
        if value.isascii() and value.isdecimal() and len(value) <= 10:
            return received_at + int(value)
        stamp = parsedate_to_datetime(value)
        return stamp.timestamp() if stamp.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None

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
        if row.get('classification') == 'captcha' or row.get('error_code') == 'google_captcha':
            return 'google_captcha'
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
    code = collection_error(rows, ())
    if source == 'google_web':
        attempted = {r['page'] for r in rows}
        retry_at = web_rate_retry_at(rows)
        return (code != 'upstream_rate_limited' and len(attempted) < page_budget
                and (retry_at is None or time.time() >= retry_at))
    return code in RETRYABLE_COLLECTION_CODES and not any(r['classification'] == 'results' for r in rows)


def can_deliver_web_results(rows, selected_count, requested, *, collection_finished):
    return selected_count > 0 and (
        collection_finished or selected_count >= requested
        or any(r.get('http_status') in (403, 429) for r in rows)
    )


def blocked_web_pages(rows):
    return {row['page'] for row in rows if row.get('http_status') in (400, 401, 403, 404, 410, 422)
            or row.get('classification') == 'captcha' or row.get('error_code') in WEB_PERMANENT_BLOCK_CODES}


def web_rate_retry_at(rows):
    limited = [row for row in rows if row.get('classification') != 'captcha'
               and row.get('error_code') not in WEB_PERMANENT_BLOCK_CODES
               and (row.get('http_status') == 429 or row.get('error_code') == 'google_http_429')]
    if not limited:
        return None
    latest = max(limited, key=lambda row: str(row.get('completed_at') or ''))
    completed = latest.get('completed_at')
    if completed is None:
        return None
    if isinstance(completed, str):
        completed = datetime.fromisoformat(completed)
    cooldown = 120 * 2 ** min(len(limited) - 1, 2)
    hints = [((row.get('response') or {}).get('web_rate_limit') or {}).get('retry_at') for row in limited]
    latest_hint = max((hint for hint in hints if type(hint) in (int, float)), default=0)
    return max(completed.timestamp() + cooldown, latest_hint)


def web_page_samples(rows):
    samples = {}
    for row in rows:
        if row['classification'] not in ('results', 'empty'):
            continue
        urls = set()
        for item in (row['response'] or {}).get('organic_results') or []:
            url = normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
            if url:
                urls.add(url)
        samples.setdefault(row['page'], []).append(urls)
    return samples


def recover_web_pages(rows, errors, *, spec, state, save, run, read, can_start, remaining):
    """Use the remaining live budget without extending the requested page range."""
    page_budget, requested = spec['google_pages'], spec['max_items']
    pages = set(range(1, page_budget + 1))
    state.pop('web_recovery_stop_reason', None)
    while rows and can_start():
        samples = web_page_samples(rows)
        page_urls = {page: set().union(*observations) for page, observations in samples.items()}
        previous_urls = set().union(*page_urls.values())
        if len(previous_urls) >= requested:
            break
        rounds = state.get('web_recovery_rounds', 0)
        if rounds >= WEB_RECOVERY_ROUNDS:
            state['web_recovery_stop_reason'] = 'round_limit'
            break
        if state.get('web_recovery_stalled', 0) >= WEB_RECOVERY_STALL_ROUNDS:
            state['web_recovery_stop_reason'] = 'no_new_links'
            break
        now = time.time()
        retry_at = web_rate_retry_at(rows)
        if retry_at is not None and retry_at > now:
            if remaining() <= retry_at - now + WEB_MIN_RESUME_SECONDS:
                state['web_recovery_stop_reason'] = 'upstream_rate_limited'
                break
            raise WebRateWait(retry_at)
        code = collection_error(rows, errors)
        if errors and code in PROXY_RETRY_CODES:
            break
        eligible = pages - blocked_web_pages(rows)
        missing = eligible - page_urls.keys()
        untried = eligible - {row['page'] for row in rows}
        refreshable = {page for page in eligible & page_urls.keys()
                       if 0 < len(page_urls[page]) < spec['page_size']
                       or len(page_urls[page]) > max(map(len, samples[page]))}
        targets = untried or (missing if missing and rounds == 0 else missing | refreshable)
        if not targets:
            state['web_recovery_stop_reason'] = 'no_eligible_pages'
            break
        delay = random.uniform(0.5, 1.0) * min(30, 2 ** min(rounds + 1, 5))
        if remaining() <= delay + 5:
            state['web_recovery_stop_reason'] = 'delivery_reserve'
            break
        time.sleep(delay)
        if not can_start():
            break
        state['web_recovery_rounds'] = rounds + 1
        save()
        errors = run(skip_web_pages=pages - targets, refresh_web_pages=targets & refreshable)
        updated = read()
        rows = updated
        if errors and collection_error((), errors) in PROXY_RETRY_CODES:
            break
        if any(urls - previous_urls for observations in web_page_samples(rows).values() for urls in observations):
            state['web_recovery_stalled'] = 0
        elif collection_error(rows, errors) != 'upstream_rate_limited':
            state['web_recovery_stalled'] = state.get('web_recovery_stalled', 0) + 1
        save()
    return rows, errors


def wait_for_web_capacity(errors, *, state, count, requested, remaining):
    """Defer an incomplete live query through the existing shared cooldown."""
    if (not errors or count >= requested or remaining <= RETRY_INTERVAL_SECONDS
            or state.get('web_recovery_stop_reason')
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
