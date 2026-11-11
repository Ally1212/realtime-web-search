"""Opt-in SerpApi historical Trends adapter, not Trending Now or search volume.

Protocol references and supported subset: docs/data-capability-serpapi-2026-11-10.md.
No environment, credentials, approval, initialization or requests on import.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import re
import unicodedata
from urllib.parse import urlencode, quote

from .data_capability_backoff import RetryPolicy,SourceRetrySession,RetryNotEntered
from .data_capability_contract import validate_request
from .data_capability_executor import SourceFailure, SourceResult
from .data_capability_facts import VERSION, validate_result
from .data_capability_jobs import amount, digest, required_text
from .product import ProductError

PROVIDER = 'serpapi'
PARSER = 'serpapi-trends-1'
ENDPOINT = 'https://serpapi.com/search.json'
UTC = timezone.utc
MAX_BODY = 16 * 1024 * 1024


@dataclass(frozen=True)
class SourceBill:
    actual_cost: object
    currency: str
    billing_unit: str
    evidence_ref: str


@dataclass(frozen=True, repr=False)
class HTTPResult:
    status: int
    headers: dict
    body: bytes


def retry_after(value, now):
    """Seconds/date Retry-After; unbounded/invalid values require a pause."""
    if value is None:
        return 0
    try:
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError()
        if re.fullmatch(r'\d+', value.strip()):
            seconds = int(value.strip())
        else:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                raise ValueError()
            delta = (target - now).total_seconds()
            seconds = max(0, int(delta) + (delta > int(delta)))
        if not 0 <= seconds <= 86400:
            raise ValueError()
        return seconds
    except (ValueError, TypeError, OverflowError):
        raise SourceFailure('source_retry_after_unresolved') from None


class CurlSerpAPITransport:
    """Fixed HTTPS origin, verified TLS, no redirects or implicit env proxy.

    libcurl's total transfer deadline covers DNS/connect/read, including drip
    responses. Both body and aggregate headers have streaming bounds. This is
    the backend adapter transport, not a substitute for browser operations.
    """
    def __init__(self, *, max_bytes=MAX_BODY, curl_factory=None):
        if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_BODY:
            raise ValueError('response bound must be 1KiB–16MiB')
        self.max_bytes, self._factory = max_bytes, curl_factory

    def __call__(self, parameters, deadline):
        from curl_cffi import Curl, CurlInfo, CurlOpt
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise SourceFailure('source_deadline_expired', actual_cost='0')
        body, headers, sizes = bytearray(), {}, [0]
        exceeded = [False]
        def write(chunk):
            if len(body) + len(chunk) > self.max_bytes:
                exceeded[0] = True
                return 0
            body.extend(chunk)
            return len(chunk)
        def header(chunk):
            sizes[0] += len(chunk)
            if sizes[0] > 65536:
                exceeded[0] = True
                return 0
            if chunk.startswith(b'HTTP/'):
                headers.clear()
            elif b':' in chunk:
                k, v = chunk.split(b':', 1)
                name = k.decode('ascii', 'ignore').lower()
                # No cookies, auth headers or arbitrary source text retained.
                if name in ('content-type', 'retry-after', 'content-encoding'):
                    headers[name] = v.decode('ascii', 'replace').strip()
            return len(chunk)
        curl = (self._factory or Curl)()
        try:
            curl.setopt(CurlOpt.URL, ENDPOINT + '?' + urlencode(parameters))
            curl.setopt(CurlOpt.PROTOCOLS_STR, 'https')
            curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
            curl.setopt(CurlOpt.SSL_VERIFYPEER, 1)
            curl.setopt(CurlOpt.SSL_VERIFYHOST, 2)
            curl.setopt(CurlOpt.PROXY, '')
            curl.setopt(CurlOpt.NOSIGNAL, 1)
            curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(remaining * 1000)))
            curl.setopt(CurlOpt.CONNECTTIMEOUT_MS, max(1, min(5000, int(remaining * 1000))))
            curl.setopt(CurlOpt.HTTPHEADER, [b'Accept: application/json', b'Accept-Encoding: identity'])
            curl.setopt(CurlOpt.WRITEFUNCTION, write)
            curl.setopt(CurlOpt.HEADERFUNCTION, header)
            curl.perform()
            return HTTPResult(int(curl.getinfo(CurlInfo.RESPONSE_CODE)), headers, bytes(body))
        except Exception:
            raise SourceFailure('source_response_too_large' if exceeded[0] else 'source_transport_uncertain') from None
        finally:
            curl.close()


def request_parameters(payload):
    p = validate_request(payload)
    if (p['capability'] != 'trends_history' or p['language'] != 'en'
            or p['start_date'] < '2004-01-01' or len(set(p['terms'])) != len(p['terms'])
            or any(',' in term or len(term) > 100 for term in p['terms'])):
        raise ProductError('unsupported_parameter', '此适配器支持2004年起、en、最多5个不含逗号且不重复的100字查询', 409)
    return dict(engine='google_trends', data_type='TIMESERIES', q=','.join(p['terms']), geo=p['country'],
                hl=p['language'], date=p['start_date'] + ' ' + p['end_date'], cat=str(p['category']),
                gprop={'web': '', 'images': 'images', 'news': 'news', 'shopping': 'froogle', 'youtube': 'youtube'}[p['search_type']],
                tz='0', no_cache='true', **{'async': 'false', 'output': 'json'})


def _redact(value, credential):
    paths = []
    # Tokens can be repeated in free text or another URL, not just their
    # original sensitive field. Learn bounded secret literals before removal.
    secrets = {credential}
    pending = [(value, 0)]
    seen = 0
    sensitive = {'key','apikey','secret','clientsecret','password','authorization','cookie','token'}
    while pending:
        node, depth = pending.pop(); seen += 1
        if depth > 64 or seen > 1000000:
            raise ValueError('source redaction structure exceeds bound')
        if isinstance(node, dict):
            for key, child in node.items():
                name = re.sub(r'[^a-z0-9]', '', key.lower())
                if (name in sensitive or name.endswith('token')) and isinstance(child, str) and child:
                    secrets.add(child)
                pending.append((child, depth + 1))
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)
    if len(secrets) > 1024 or sum(map(len,secrets)) > 262144:
        raise ValueError('source secret material exceeds redaction bound')
    variants = sorted({v for secret in secrets for v in (secret,quote(secret,safe=''),quote(secret,safe='').lower())},key=lambda v:(-len(v),v))
    def visit(node, path):
        if isinstance(node, dict):
            result = {}
            for key, child in node.items():
                name = re.sub(r'[^a-z0-9]', '', key.lower())
                if any(secret in key for secret in variants):
                    paths.append(path + '/[redacted-field-name]')
                    continue
                sub = path + '/' + key.replace('~', '~0').replace('/', '~1')
                if name in {'key', 'apikey', 'secret', 'clientsecret', 'password', 'authorization', 'cookie', 'token'} or name.endswith('token'):
                    paths.append(sub)
                    continue
                result[key] = visit(child, sub)
            return result
        if isinstance(node, list):
            return [visit(child, path + '/' + str(i)) for i, child in enumerate(node)]
        if isinstance(node, str):
            cleaned = node
            for secret in variants:
                cleaned = cleaned.replace(secret, '[redacted]')
            # Remove entire credential-bearing URLs, including unknown token
            # values. Ordinary source links remain intact.
            if re.search(r'(?i)(?:[?&](?:[a-z0-9_-]*token|api_key|key|secret|client_secret)=|https?://[^/\s]*@)', cleaned):
                cleaned = '[redacted credential-bearing URL]'
            if cleaned != node:
                paths.append(path)
            return cleaned
        return node
    return visit(value, ''), paths


def _period(row):
    """Parse explicit English calendar labels, cross-check UTC epoch start.

    Never infer the last bucket's end from the next point or requested window.
    Unknown/localized/hourly labels fail as an unsupported source format.
    """
    label = unicodedata.normalize('NFKC', row['date']).replace('–', '-').replace('—', '-')
    parts = [v.strip() for v in label.split('-')]
    if len(parts) == 1:
        a = datetime.strptime(parts[0], '%b %d, %Y').replace(tzinfo=UTC)
        b = a + timedelta(days=1)
    elif len(parts) == 2:
        right = parts[1]
        if re.fullmatch(r'\d{1,2}, \d{4}', right):
            right = parts[0].split()[0] + ' ' + right
        b = datetime.strptime(right, '%b %d, %Y').replace(tzinfo=UTC)
        left = parts[0]
        if ',' not in left:
            left += ', ' + str(b.year)
        a = datetime.strptime(left, '%b %d, %Y').replace(tzinfo=UTC)
        if a > b and ',' not in parts[0]:
            a = a.replace(year=a.year - 1)
        b += timedelta(days=1)
    else:
        raise ValueError('unsupported source calendar period')
    stamp = row['timestamp']
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{1,12}', stamp) or datetime.fromtimestamp(int(stamp), UTC) != a or b <= a:
        raise ValueError('timestamp and calendar label disagree')
    return a, b


def normalize(payload, response, *, requested_at, received_at, retain_raw, redacted_paths=()):
    """Normalize one licensed, already redacted synchronous response."""
    p, sent = validate_request(payload), request_parameters(payload)
    meta, effective = response.get('search_metadata', {}), response.get('search_parameters', {})
    if meta.get('status') != 'Success' or not isinstance(meta.get('id'), str) or not meta['id']:
        raise ValueError('source did not confirm a successful observation identity')
    # Echoes bind actual response to requested conditions. Omitted critical
    # parameters are not silently assumed to have taken effect.
    for key in ('engine', 'q', 'geo', 'hl', 'date', 'cat', 'tz', 'data_type'):
        if str(effective.get(key, object())) != sent[key]:
            raise ValueError('source effective parameters disagree')
    if effective.get('gprop', '') != sent['gprop']:
        raise ValueError('source search type disagrees')
    created = datetime.strptime(meta['created_at'], '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=UTC)
    # no_cache was explicit, but a reused/old request identity is not a new
    # historical observation. Second-resolution source timestamps are allowed.
    if not requested_at.replace(microsecond=0) <= created <= received_at:
        raise ValueError('source observation is stale or future')
    timeline = response['interest_over_time']['timeline_data']
    if not isinstance(timeline, list) or len(timeline) > 100000:
        raise ValueError('unsupported source timeline')
    series, mappings = [], []
    periods = [_period(row) for row in timeline]
    if any(periods[i][0] < periods[i-1][1] for i in range(1, len(periods))):
        raise ValueError('source periods overlap or are reordered')
    rows_by_index = []
    for row in timeline:
        values = row['values']
        if not isinstance(values, list) or len(values) > len(p['terms']):
            raise ValueError('source comparison has extra query values')
        indexed = {}
        for position, item in enumerate(values):
            index = item.get('query_index')
            if (type(index) is not int or not 0 <= index < len(p['terms']) or index in indexed
                    or item.get('query') != p['terms'][index]):
                raise ValueError('source query mapping is ambiguous')
            indexed[index] = (position, item)
        rows_by_index.append(indexed)
    source_scale = 'serpapi:' + meta['id'] + ':' + digest(sent)
    for index, term in enumerate(p['terms']):
        sid, points = 'query-' + str(index), []
        for n, (row, (a, b)) in enumerate(zip(timeline, periods)):
            found = rows_by_index[n].get(index)
            if found is None:
                position, item = None, None
                metric = dict(value=None, status='not_observed', reason='source_omitted_query_in_period')
            else:
                position, item = found
                value, raw_value = item.get('extracted_value'), item.get('value')
                if raw_value == '<1':
                    metric = dict(value=None, status='below_threshold', reason='source_display_below_one', censored=True)
                elif type(value) is int and 0 <= value <= 100 and raw_value == str(value):
                    metric = dict(value=str(value), status='available')
                    if value == 0:
                        # Google's own FAQ says low-volume terms appear as 0.
                        # Preserve the relative index, never call this true_zero.
                        metric.update(censored=True, reason='source_zero_index_does_not_establish_zero_searches')
                else:
                    raise ValueError('source numeric/display value disagree')
            partial = row.get('partial_data', False)
            if type(partial) is not bool:
                raise ValueError('source partial flag is invalid')
            points.append(dict(id='period-' + str(n), period_start=a.isoformat(), period_end=b.isoformat(),
                               metric=metric, partial=partial or found is None, revision=meta['id'],
                               source_ref='/interest_over_time/timeline_data/' + str(n) + '/values' + ('/' + str(position) if position is not None else ''),
                               source_fields=dict(date=row['date'], timestamp=row['timestamp'], value=item,
                                                  partial_flag_reported='partial_data' in row)))
        if not points or all(index not in items for items in rows_by_index):
            mappings.append(dict(input_index=index, record_id=None, status='not_observed', reason='source_query_not_observed' if points else 'source_empty_timeline'))
            continue
        durations = {b-a for a,b in periods}
        granularity = 'day' if durations == {timedelta(days=1)} else 'week' if durations == {timedelta(days=7)} else 'source_periods'
        extensions = dict(status='not_covered', rows=[], reason='this_request_only_fetched_TIMESERIES')
        series.append(dict(id=sid, term_or_topic=term, resolved_subject=None,
                           effective_window=dict(start=periods[0][0].isoformat(), end=periods[-1][1].isoformat()),
                           granularity=granularity, source_timezone='UTC', scale_id=source_scale,
                           measurement_type='relative_index', normalization='google_trends_within_response_comparison',
                           comparison_group=source_scale, revision=meta['id'], points=points,
                           source_fields=dict(search_parameters=effective, provider_request_created_at=created.isoformat(), averages=response['interest_over_time'].get('averages')),
                           regional_interest=extensions, related_top=extensions, related_rising=extensions))
        mappings.append(dict(input_index=index, record_id=sid, status='available', reason=None))
    raw = dict(status='not_retained', payload=None, redacted_paths=[], reason='adapter_raw_retention_not_enabled')
    if retain_raw:
        raw = dict(status='redacted' if redacted_paths else 'stored', payload=response,
                   redacted_paths=list(redacted_paths), reason='source_credentials_removed' if redacted_paths else None)
    facts = dict(schema_version=VERSION, source=dict(provider=PROVIDER, api_version='unversioned-serpapi-search',
        parser_version=PARSER, source_request_id=meta['id'], source_observed_at=None,
        unknown_time_reason='provider_created_at_is_request_time_not_independent_observation_time'),
        requested_at=requested_at.isoformat(), received_at=received_at.isoformat(), effective_parameters=p,
        is_partial=any(point['partial'] for s in series for point in s['points']) or bool(timeline) and any(m['record_id'] is None for m in mappings), is_truncated=False,
        termination_reason='serpapi_synchronous_timeseries_response', warnings=['relative_index_not_absolute_search_volume'],
        raw=raw, data=dict(series=series, input_mapping=mappings))
    return validate_result(p, facts)


class SerpAPITrendsAdapter:
    # Source-specific hooks share the audited transport, response capture and billing path.
    request_parameters = staticmethod(request_parameters)
    normalize = staticmethod(normalize)

    @staticmethod
    def scope_verified(response, expected):
        echo=response.get('search_parameters',{})
        return (isinstance(echo,dict) and all(str(echo.get(k,object()))==expected[k]
                for k in ('engine','q','geo','hl','date','cat','tz','data_type')) and echo.get('gprop','')==expected['gprop'])

    def __init__(self, *, credential=None, bill_for=None, billing_unit=None, retain_raw=False, enabled=False, transport=None, clock=None,retry_policy=None,call_cost_ceiling=None,sleep=None,monotonic=None):
        self.retry_policy=RetryPolicy() if retry_policy is None else retry_policy
        if not isinstance(self.retry_policy,RetryPolicy):raise ValueError('trusted retry policy required')
        self.call_ceiling=amount(call_cost_ceiling) if call_cost_ceiling is not None else None
        self._sleep,self._monotonic=sleep,monotonic
        if enabled and self.retry_policy.maximum_attempts>1 and (not retain_raw or self.call_ceiling is None):raise ValueError('retry requires raw journal and explicit per-call ceiling')
        if type(enabled) is not bool or type(retain_raw) is not bool:
            raise ValueError('explicit boolean source/retention policy required')
        if enabled and (not isinstance(credential, str) or not credential or len(credential) > 4096
                        or not callable(bill_for) or not isinstance(billing_unit, str) or not billing_unit):
            raise ValueError('enabled adapter requires credential and confirmed billing resolver/unit')
        self._credential, self._bill_for = credential, bill_for
        self.billing_unit, self.retain_raw, self.enabled = billing_unit, retain_raw, enabled
        self._transport = transport if transport is not None else CurlSerpAPITransport()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def requires_response_store(self):
        return self.retain_raw

    def __repr__(self):
        return '<SerpAPITrendsAdapter credential=redacted enabled=' + str(self.enabled) + '>'

    def validate_parameters(self, payload):
        if not self.enabled:
            raise ProductError('source_not_admitted', 'SerpApi历史曲线适配器尚未启用', 409)
        self.request_parameters(payload)

    def __call__(self,context):
        if self.retry_policy.maximum_attempts>1 or context.source_retry_policy is not None:
            return self._with_retries(context)
        return self._once(context)

    def _with_retries(self,context):
        if not self.retain_raw:raise SourceFailure('source_call_journal_unavailable',actual_cost='0')
        try:session=SourceRetrySession(context,self.retry_policy,self.call_ceiling,self._clock,lambda:self.validate_parameters(context.parameters),sleep=self._sleep,monotonic=self._monotonic)
        except RetryNotEntered as exc:raise SourceFailure(exc.code,actual_cost='0') from None
        def evidence():return dict(reference='serpapi-retry:'+context.job_id,currency=context.currency,billing_unit=self.billing_unit,actual_cost=str(session.spent))
        attempt=0
        while True:
            attempt+=1;phase='query' if attempt==1 else 'query_retry_'+str(attempt)
            try:deadline=session.enter(phase,context.deadline)
            except RetryNotEntered as exc:raise SourceFailure(exc.code,actual_cost=session.spent,billing_evidence=evidence()) from None
            try:
                outcome=self._once(replace(context,deadline=deadline),phase=phase)
                session.confirmed(outcome.actual_cost)
                return SourceResult(outcome.facts,session.spent,billing_evidence=evidence(),retry_after=outcome.retry_after)
            except SourceFailure as exc:
                if exc.actual_cost is None:raise
                session.confirmed(exc.actual_cost)
                if exc.retryable and session.again(exc.retry_after,context.deadline):continue
                raise SourceFailure(exc.code,actual_cost=session.spent,retry_after=exc.retry_after,billing_evidence=evidence()) from None

    def _once(self, context, *, phase=None):
        if not self.enabled:
            raise SourceFailure('source_adapter_disabled', actual_cost='0')
        if self.retain_raw and context.response is None:
            raise SourceFailure('response_capture_unavailable',actual_cost='0')
        params = self.request_parameters(context.parameters)
        if context.canceled():
            raise SourceFailure('source_canceled_before_call', actual_cost='0')
        requested_at = self._clock()
        if requested_at >= context.deadline:
            raise SourceFailure('source_deadline_expired', actual_cost='0')
        def capture(**kwargs):
            if context.response is not None:
                if phase is None:context.response.save(**kwargs)
                else:context.response.checkpoint(phase,**kwargs)
        params['api_key'] = self._credential
        try:
            reply = self._transport(params, context.deadline)
        except SourceFailure:
            if phase is None:raise
            capture(http_status=None,payload=None,requested_at=requested_at,received_at=self._clock(),scope_verified=False,response_complete=False,reason='source_transport_uncertain')
            raise SourceFailure('source_transport_uncertain') from None
        except Exception:
            if phase is not None:capture(http_status=None,payload=None,requested_at=requested_at,received_at=self._clock(),scope_verified=False,response_complete=False,reason='source_transport_uncertain')
            raise SourceFailure('source_transport_uncertain') from None
        received_at = self._clock()
        try:
            if len(reply.body) > MAX_BODY or 'application/json' not in reply.headers.get('content-type', '').lower():
                raise ValueError()
            def pairs(items):
                result = {}
                for key, val in items:
                    if key in result:
                        raise ValueError()
                    result[key] = val
                return result
            response = json.loads(reply.body, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(response, dict):
                raise ValueError()
            response, paths = _redact(response, self._credential)
        except Exception:
            if context.response is not None:
                capture(http_status=reply.status,payload=None,requested_at=requested_at,received_at=received_at,
                                      reason='source_response_invalid',scope_verified=False,response_complete=False)
            raise SourceFailure('source_response_invalid') from None
        if context.response is not None:
            expected=self.request_parameters(context.parameters)
            verified=self.scope_verified(response,expected)
            error_only=retry_error_only(response,self.scope_verified(response,expected))
            capture(http_status=reply.status,payload=response,requested_at=requested_at,received_at=received_at,
                                  redacted_paths=paths,reason='source_scope_unverified',scope_verified=verified or error_only)
        delay = retry_after(reply.headers.get('retry-after'), received_at)
        try:
            bill = self._bill_for(reply.status, response)
            if not isinstance(bill, SourceBill) or bill.currency != context.currency or bill.billing_unit != self.billing_unit:
                raise ValueError()
            if (not isinstance(bill.evidence_ref, str) or not re.fullmatch(r'[A-Za-z0-9:_.\-/]{1,200}', bill.evidence_ref)
                    or self._credential in bill.evidence_ref):
                raise ValueError()
            cost = amount(bill.actual_cost)
        except Exception:
            raise SourceFailure('source_billing_unresolved', retry_after=delay) from None
        evidence = dict(reference=bill.evidence_ref, currency=bill.currency, billing_unit=bill.billing_unit, actual_cost=str(cost))
        if phase is not None:
            try:context.confirm_bill(phase,evidence)
            except Exception:raise SourceFailure('source_billing_unresolved',retry_after=delay) from None
        if delay and callable(context.defer_calls):context.defer_calls(delay)
        if received_at > context.deadline:
            raise SourceFailure('source_deadline_exceeded', actual_cost=cost, retry_after=delay, billing_evidence=evidence)
        if reply.status != 200 or response.get('error') is not None:
            raise SourceFailure('source_http_error', actual_cost=cost, retry_after=delay, billing_evidence=evidence,retryable=reply.status in (429,503) and retry_error_only(response,self.scope_verified(response,self.request_parameters(context.parameters))))
        try:
            facts = self.normalize(context.parameters, response, requested_at=requested_at, received_at=received_at,
                              retain_raw=self.retain_raw, redacted_paths=paths)
        except Exception:
            # A known invoice is not evidence that the parser understood the
            # response. No values, timestamps or series are fabricated.
            raise SourceFailure('source_contract_unverified', actual_cost=cost, retry_after=delay, billing_evidence=evidence) from None
        return SourceResult(facts, cost, billing_evidence=evidence,retry_after=delay)


def retry_error_only(value,scope_verified):
    if not isinstance(value,dict) or not isinstance(value.get('error'),str) or not value['error'].strip() or set(value)-{'error','search_metadata','search_parameters'}:return False
    meta=value.get('search_metadata')
    if meta is not None and (not isinstance(meta,dict) or meta.get('status')!='Error'):return False
    return 'search_parameters' not in value or scope_verified
