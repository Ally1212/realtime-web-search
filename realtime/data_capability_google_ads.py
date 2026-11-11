"""Opt-in Google Ads historical keyword metrics, never Trends or SEO difficulty.

No credential discovery, OAuth refresh, initialization or service wiring on import.
Protocol/limits: docs/data-capability-google-ads-2026-11-10.md.
"""
from __future__ import annotations
from dataclasses import dataclass,replace
from datetime import date,datetime,timezone,timedelta
import copy
import json
import re
from .data_capability_contract import validate_request
from .data_capability_backoff import RetryPolicy,RetryWindow
from .data_capability_executor import SourceFailure,SourceResult,SourceCallDeferred
from .data_capability_facts import VERSION,validate_result
from .data_capability_jobs import amount,digest
from .data_capability_serpapi import HTTPResult,SourceBill,retry_after,_redact
from .product import ProductError

UTC=timezone.utc
API='v26'
MAX_BODY=16*1024*1024
MONTHS=('JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER')

@dataclass(frozen=True,repr=False)
class AdsScope:
    customer_id:str
    login_customer_id:str|None
    oauth_subject:str
    oauth_client_id:str
    project_id:str
    access_level:str
    allowed_use:str
    country:str
    geo_target:str
    language:str
    language_constant:str
    currency:str
    evidence_ref:str
    verified_at:datetime
    expires_at:datetime

    def validate(self,now):
        if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('aware clock required')
        for value in (self.customer_id,self.login_customer_id):
            if value is not None and (not isinstance(value,str) or not re.fullmatch(r'\d{10}',value)):raise ValueError('invalid customer binding')
        if self.customer_id is None:raise ValueError('customer required')
        for value in (self.oauth_subject,self.oauth_client_id,self.project_id,self.allowed_use):
            if not isinstance(value,str) or not value.strip() or len(value)>500:raise ValueError('identity and use required')
        if self.access_level not in ('EXPLORER','BASIC','STANDARD'):raise ValueError('production access approval required')
        for value,pattern in [(self.country,r'[A-Z]{2}'),(self.geo_target,r'geoTargetConstants/\d+'),(self.language,r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*'),(self.language_constant,r'languageConstants/\d+'),(self.currency,r'[A-Z]{3}'),(self.evidence_ref,r'[A-Za-z0-9:_.\-/]{1,200}')]:
            if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ValueError('invalid approved scope')
        if any(not isinstance(v,datetime) or v.tzinfo is None for v in (self.verified_at,self.expires_at)) or not self.verified_at<=now<self.expires_at:
            raise ValueError('current admission required')
        return self


def request_parameters(payload,scope,now):
    p=validate_request(payload);scope.validate(now)
    if p['capability']!='keyword_volume' or p['country']!=scope.country or p['language']!=scope.language:
        raise ProductError('unsupported_parameter','搜索量候选仅接受当前核验的地区和语言',409)
    # The source supports four years, not arbitrary years or current/future months.
    current=now.year*12+now.month-1
    first=int(p['start_month'][:4])*12+int(p['start_month'][5:])-1
    last=int(p['end_month'][:4])*12+int(p['end_month'][5:])-1
    if not current-48<=first<=last<current:
        raise ProductError('unsupported_parameter','搜索量候选限定最近48个完整月份，不保证每月有数据',409)
    def ym(s):return dict(year=int(s[:4]),month=MONTHS[int(s[5:])-1])
    return dict(keywords=list(dict.fromkeys(p['keywords'])),geoTargetConstants=[scope.geo_target],language=scope.language_constant,
        keywordPlanNetwork='GOOGLE_SEARCH' if p['network']=='google' else 'GOOGLE_SEARCH_AND_PARTNERS',includeAdultKeywords=False,
        historicalMetricsOptions=dict(yearMonthRange=dict(start=ym(p['start_month']),end=ym(p['end_month'])),includeAverageCpc=True))


class CurlGoogleAdsTransport:
    """Fixed TLS host, POST body, bounded stream, no redirects/proxy/retries."""
    def __init__(self,scope,access_token,developer_token,*,curl_factory=None):
        self._scope,self._access,self._developer,self._factory=scope,access_token,developer_token,curl_factory
    def __repr__(self):return '<CurlGoogleAdsTransport credentials=redacted>'
    def __call__(self,body,deadline):
        from curl_cffi import Curl,CurlInfo,CurlOpt
        remaining=(deadline-datetime.now(UTC)).total_seconds()
        if remaining<=0:raise SourceFailure('source_deadline_expired',actual_cost='0')
        out=bytearray();headers={};header_bytes=0;oversize=False
        def write(chunk):
            nonlocal oversize
            if len(out)+len(chunk)>MAX_BODY:oversize=True;return 0
            out.extend(chunk);return len(chunk)
        def header(chunk):
            nonlocal header_bytes,oversize
            header_bytes+=len(chunk)
            if header_bytes>65536:oversize=True;return 0
            if chunk.startswith(b'HTTP/'):headers.clear()
            elif b':' in chunk:
                k,v=chunk.split(b':',1);k=k.decode('ascii','ignore').lower()
                if k in ('content-type','retry-after','request-id'):headers[k]=v.decode('ascii','replace').strip()
            return len(chunk)
        curl=(self._factory or Curl)()
        try:
            curl.setopt(CurlOpt.URL,f'https://googleads.googleapis.com/{API}/customers/{self._scope.customer_id}:generateKeywordHistoricalMetrics')
            for opt,value in [(CurlOpt.PROTOCOLS_STR,'https'),(CurlOpt.FOLLOWLOCATION,0),(CurlOpt.SSL_VERIFYPEER,1),(CurlOpt.SSL_VERIFYHOST,2),(CurlOpt.PROXY,''),(CurlOpt.NOSIGNAL,1),(CurlOpt.TIMEOUT_MS,max(1,int(remaining*1000))),(CurlOpt.CONNECTTIMEOUT_MS,max(1,min(5000,int(remaining*1000))))]:curl.setopt(opt,value)
            h=['Content-Type: application/json','Accept: application/json','Accept-Encoding: identity','Authorization: Bearer '+self._access,'developer-token: '+self._developer]
            if self._scope.login_customer_id:h.append('login-customer-id: '+self._scope.login_customer_id)
            curl.setopt(CurlOpt.HTTPHEADER,[v.encode() for v in h]);curl.setopt(CurlOpt.POST,1)
            curl.setopt(CurlOpt.POSTFIELDS,json.dumps(body,ensure_ascii=False,separators=(',',':')).encode())
            curl.setopt(CurlOpt.WRITEFUNCTION,write);curl.setopt(CurlOpt.HEADERFUNCTION,header);curl.perform()
            return HTTPResult(int(curl.getinfo(CurlInfo.RESPONSE_CODE)),headers,bytes(out))
        except Exception:raise SourceFailure('source_response_too_large' if oversize else 'source_transport_uncertain') from None
        finally:curl.close()


def _integer(value):
    if type(value) is int:value=str(value)
    if not isinstance(value,str) or not re.fullmatch(r'0|[1-9]\d{0,18}',value) or int(value)>9223372036854775807:raise ValueError('invalid native int64')
    return value


def _metric(value,reason='source_null_or_absent'):
    if value is None:return dict(value=None,status='not_observed',reason=reason)
    return dict(value=_integer(value),status='available',source_fields=dict(approximate=True,rounded='unknown',zero_basis='source_approximate_zero_not_verified_true_zero' if str(value)=='0' else None))


def normalize(payload,response,*,scope,requested_at,received_at,request_id):
    p=validate_request(payload);sent=request_parameters(p,scope,requested_at)
    if not isinstance(response,dict) or set(response)-{'results','aggregateMetricResults'} or not isinstance(response.get('results',[]),list) or len(response.get('results',[]))>len(sent['keywords']):raise ValueError('native response contract mismatch')
    groups=[];owners={};partial=False
    for i,row in enumerate(response.get('results',[])):
        if not isinstance(row,dict) or set(row)-{'text','closeVariants','keywordMetrics'}:raise ValueError('native keyword contract mismatch')
        text=row.get('text');variants=row.get('closeVariants',[])
        if not isinstance(text,str) or not text.strip() or not isinstance(variants,list) or any(not isinstance(v,str) or not v.strip() for v in variants):raise ValueError('missing native variant identity')
        aliases=list(dict.fromkeys([text]+variants));matched=[v for v in aliases if v in sent['keywords']]
        if not matched or any(v not in sent['keywords'] for v in variants) or any(v in owners for v in aliases):raise ValueError('ambiguous/unbound native variants')
        gid='ads-group-'+str(i)+'-'+digest(dict(text=text,variants=variants))[:16]
        for v in aliases:owners[v]=gid
        metrics=row.get('keywordMetrics');missing=metrics is None
        if missing:metrics={};partial=True
        if not isinstance(metrics,dict):raise ValueError('invalid native metrics')
        allowed={'avgMonthlySearches','monthlySearchVolumes','competition','competitionIndex','lowTopOfPageBidMicros','highTopOfPageBidMicros','averageCpcMicros'}
        if set(metrics)-allowed:raise ValueError('uncovered native metric fields')
        points={};native=metrics.get('monthlySearchVolumes',[])
        if not isinstance(native,list) or len(native)>48:raise ValueError('invalid native months')
        for n,v in enumerate(native):
            if not isinstance(v,dict) or set(v)-{'year','month','monthlySearches'} or v.get('month') not in MONTHS:raise ValueError('invalid native month')
            year=int(_integer(v.get('year')));month=MONTHS.index(v['month'])+1;key=f'{year:04d}-{month:02d}'
            if not p['start_month']<=key<=p['end_month'] or key in points:raise ValueError('duplicate/out-of-window source month')
            points[key]=dict(year=year,month=month,metric=_metric(v.get('monthlySearches')),source_fields=dict(source_ref=f'/results/{i}/keywordMetrics/monthlySearchVolumes/{n}',native=copy.deepcopy(v)))
        start=date.fromisoformat(p['start_month']+'-01');end=date.fromisoformat(p['end_month']+'-01');monthly=[]
        while start<=end:
            key=start.strftime('%Y-%m');point=points.get(key)
            if point is None:point=dict(year=start.year,month=start.month,metric=_metric(None,'source_omitted_month'),source_fields={})
            if point['metric']['value'] is None:partial=True
            monthly.append(point);start=date(start.year+(start.month==12),start.month%12+1,1)
        commercial={}
        if 'competition' in metrics:
            c=metrics['competition']
            if c not in ('UNSPECIFIED','UNKNOWN','LOW','MEDIUM','HIGH'):raise ValueError('unknown competition enum')
            commercial['competition']=c
        if 'competitionIndex' in metrics:
            commercial['competition_index']=_metric(metrics['competitionIndex'])
            if commercial['competition_index']['value'] is not None and int(commercial['competition_index']['value'])>100:raise ValueError('competition index out of range')
        for src,dst in [('averageCpcMicros','cpc'),('lowTopOfPageBidMicros','bid_low'),('highTopOfPageBidMicros','bid_high')]:
            if src in metrics:commercial[dst]=dict(metric=_metric(metrics[src]),currency=scope.currency,unit='micros')
        groups.append(dict(id=gid,returned_text=text,close_variant_group=gid,measurement_type='estimated',unit='searches',
            statistic_window=dict(start_month=None,end_month=None,reason='source_does_not_echo_effective_average_window'),
            average=_metric(metrics.get('avgMonthlySearches')),monthly=monthly,commercial_fields=commercial,
            source_fields=dict(source_ref=f'/results/{i}',native=copy.deepcopy(row),close_variants=copy.deepcopy(variants),requested_window=dict(start_month=p['start_month'],end_month=p['end_month']),average_window_unknown=True,metrics_absent=missing)))
    mapping=[dict(input_index=i,record_id=owners.get(v),status='available' if v in owners else 'not_observed',reason=None if v in owners else 'source_omitted_keyword_no_per_keyword_reason') for i,v in enumerate(p['keywords'])]
    partial=partial or bool(groups) or any(m['record_id'] is None for m in mapping)
    return validate_result(p,dict(schema_version=VERSION,source=dict(provider='google_ads',api_version=API,parser_version='google-ads-volume-1',source_request_id=request_id,source_observed_at=None,unknown_time_reason='source_does_not_report_observation_time'),
        requested_at=requested_at.isoformat(),received_at=received_at.isoformat(),effective_parameters=p,is_partial=partial,is_truncated=False,termination_reason='native_unary_historical_metrics_response',
        warnings=['approximate_source_volume_not_exact_counts;average_window_unknown;adult_keywords_excluded;currency_from_verified_customer_scope;no_device_dimension'],
        raw=dict(status='not_retained',payload=None,redacted_paths=[],reason='native_response_in_independent_job_journal'),data=dict(groups=groups,input_mapping=mapping)))


class GoogleAdsVolumeAdapter:
    requires_response_store=True
    def __init__(self,*,enabled=False,scope=None,access_token=None,developer_token=None,token_expires_at=None,admission_for=None,bill_for=None,billing_unit=None,call_cost_ceiling=None,retain_raw=False,transport=None,clock=None,retry_policy=None,sleep=None,monotonic=None):
        self.retry_policy=RetryPolicy() if retry_policy is None else retry_policy
        if not isinstance(self.retry_policy,RetryPolicy):raise ValueError('trusted retry policy required')
        self._sleep,self._monotonic=sleep,monotonic
        self.enabled=enabled;self.scope=scope;self._access=access_token;self._developer=developer_token;self._expires=token_expires_at;self._admission=admission_for
        self._bill=bill_for;self.billing_unit=billing_unit;self._ceiling=amount(call_cost_ceiling) if call_cost_ceiling is not None else None;self.retain_raw=retain_raw;self._clock=clock or (lambda:datetime.now(UTC))
        if enabled:
            if not isinstance(scope,AdsScope) or not callable(admission_for) or not callable(bill_for) or not billing_unit or not retain_raw or self._ceiling is None:raise ValueError('explicit source admission, retention and billing required')
            for v in (access_token,developer_token):
                if not isinstance(v,str) or not 1<=len(v)<=8192 or any(ord(c)<33 or ord(c)>126 for c in v):raise ValueError('explicit safe credentials required')
            self._check()
            if any(v in scope.evidence_ref for v in (access_token,developer_token)):raise ValueError('opaque nonsecret admission reference required')
        self._transport=transport or (CurlGoogleAdsTransport(scope,access_token,developer_token) if enabled else None)
    def __repr__(self):return '<GoogleAdsVolumeAdapter credentials=redacted enabled='+str(self.enabled)+'>'
    def _check(self):
        now=self._clock();self.scope.validate(now)
        if self._admission()!=self.scope or not isinstance(self._expires,datetime) or self._expires.tzinfo is None or now>=self._expires:raise ValueError('source identity/authorization expired or changed')
    def validate_parameters(self,payload):
        if not self.enabled:raise ProductError('source_not_admitted','Google Ads搜索量候选尚未启用',409)
        try:self._check()
        except Exception:raise ProductError('source_not_admitted','搜索量来源授权待核验',409) from None
        request_parameters(payload,self.scope,self._clock())
    def __call__(self,ctx):
        # Default remains one attempt. Extra calls require a frozen operator grant;
        # customer budget alone cannot enable them.
        policy=self.retry_policy
        approved=ctx.source_retry_policy
        if ((policy.maximum_attempts>1 or approved is not None)
                and approved!=policy.public()):
            raise SourceFailure('source_retry_not_admitted',actual_cost='0')
        cutoff=min(ctx.deadline,self._expires,self.scope.expires_at) if self.enabled else ctx.deadline
        window=RetryWindow(policy,self._clock,cutoff,sleep=self._sleep,monotonic=self._monotonic)
        total=amount('0')
        def aggregate(evidence):
            return dict(reference='google-ads-retry:'+ctx.job_id,currency=ctx.currency,
                        billing_unit=self.billing_unit,actual_cost=str(total)) if attempt>1 else evidence
        for attempt in range(1,policy.maximum_attempts+1):
            phase='metrics' if attempt==1 else 'metrics_retry_'+str(attempt)
            try:
                call_ctx=ctx
                if approved is not None:
                    remaining=window.remaining()
                    if remaining<=0:raise SourceFailure('source_retry_window_expired',actual_cost='0')
                    call_ctx=replace(ctx,deadline=min(ctx.deadline,self._clock()+timedelta(seconds=remaining)))
                out=self._once(call_ctx,phase,window);total+=amount(out.actual_cost)
                return SourceResult(out.facts,total,billing_evidence=aggregate(out.billing_evidence),retry_after=out.retry_after)
            except SourceFailure as exc:
                if exc.actual_cost is None:raise
                total+=exc.actual_cost
                delay=exc.retry_after
                if exc.retryable and approved is not None:
                    delay=policy.delay(attempt,exc.retry_after)
                    ctx.defer_calls(delay)
                if (not exc.retryable or exc.actual_cost>self._ceiling or attempt>=policy.maximum_attempts
                        or total+self._ceiling>amount(ctx.maximum_cost)):
                    raise SourceFailure(exc.code,actual_cost=total,retry_after=delay,billing_evidence=aggregate(exc.billing_evidence)) from None
                # This cooldown survives worker death and applies to the shared
                # account even when retry window/permission rejects another call.
                try:allowed=window.wait(delay,ctx.canceled,self._check)
                except Exception:allowed=False
                if not allowed:
                    raise SourceFailure('source_retry_stopped',actual_cost=total,retry_after=delay,billing_evidence=aggregate(exc.billing_evidence)) from None

    def _once(self,ctx,phase,window):
        if not self.enabled:raise SourceFailure('source_adapter_disabled',actual_cost='0')
        try:
            self.validate_parameters(ctx.parameters)
            if ctx.canceled() or self._clock()>=ctx.deadline:raise ValueError()
            if not all(callable(v) for v in (ctx.before_call,ctx.confirm_bill,ctx.defer_calls,getattr(ctx.response,'checkpoint',None))) or amount(ctx.maximum_cost)<self._ceiling:raise ValueError()
            sent=request_parameters(ctx.parameters,self.scope,self._clock())
            while True:
                try:ctx.before_call(phase,str(self._ceiling));break
                except SourceCallDeferred as e:
                    if not window.wait(e.retry_after,ctx.canceled,self._check):raise
                    self.validate_parameters(ctx.parameters)
        except SourceCallDeferred as e:raise SourceFailure('source_account_deferred',actual_cost='0',retry_after=e.retry_after) from None
        except Exception:raise SourceFailure('source_not_admitted',actual_cost='0') from None
        requested=self._clock()
        try:reply=self._transport(sent,min(ctx.deadline,self._expires,self.scope.expires_at))
        except Exception:
            ctx.response.checkpoint(phase,http_status=None,payload=None,requested_at=requested,received_at=self._clock(),scope_verified=False,response_complete=False,reason='source_transport_uncertain')
            raise SourceFailure('source_transport_uncertain') from None
        received=self._clock()
        try:
            def pairs(items):
                d={}
                for k,v in items:
                    if k in d:raise ValueError()
                    d[k]=v
                return d
            if len(reply.body)>MAX_BODY or 'application/json' not in reply.headers.get('content-type','').lower():raise ValueError()
            raw=json.loads(reply.body,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(raw,dict):raise ValueError()
            clean,paths=_redact(raw,self._access);clean,more=_redact(clean,self._developer);paths=sorted(set(paths+more))
            rid=reply.headers.get('request-id')
            if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',rid) or any(v in rid for v in (self._access,self._developer)):raise ValueError()
        except Exception:
            ctx.response.checkpoint(phase,http_status=reply.status,payload=None,requested_at=requested,received_at=received,scope_verified=False,response_complete=False,reason='source_response_invalid')
            raise SourceFailure('source_response_invalid') from None
        # Native REST has no parameter echo: binding is the fixed TLS transport,
        # configured account, exact posted body and safe per-response request-id.
        envelope=dict(source_request_id=rid,request=sent,response=clean,scope_evidence=self.scope.evidence_ref,binding_basis='fixed_tls_account_endpoint_and_exact_request_body')
        ctx.response.checkpoint(phase,http_status=reply.status,payload=envelope,requested_at=requested,received_at=received,redacted_paths=['/response'+v for v in paths],scope_verified=True)
        delay=retry_after(reply.headers.get('retry-after'),received)
        if delay:ctx.defer_calls(delay)
        try:
            bill=self._bill(reply.status,clean,rid)
            if not isinstance(bill,SourceBill) or bill.currency!=ctx.currency or bill.billing_unit!=self.billing_unit or not isinstance(bill.evidence_ref,str) or not re.fullmatch(r'[A-Za-z0-9:_.\-/]{1,200}',bill.evidence_ref) or any(v in bill.evidence_ref for v in (self._access,self._developer)):raise ValueError()
            cost=amount(bill.actual_cost);evidence=dict(reference=bill.evidence_ref,currency=bill.currency,billing_unit=bill.billing_unit,actual_cost=str(cost));ctx.confirm_bill(phase,evidence)
        except Exception:raise SourceFailure('source_billing_unresolved',retry_after=delay) from None
        def fail(code,*,retryable=False):raise SourceFailure(code,actual_cost=cost,retry_after=delay,billing_evidence=evidence,retryable=retryable)
        if reply.status!=200 or 'error' in clean:fail('source_http_error',retryable=reply.status in (429,503) and set(clean)=={'error'} and isinstance(clean['error'],dict))
        try:
            self._check()
            if received>ctx.deadline:raise ValueError()
            facts=normalize(ctx.parameters,clean,scope=self.scope,requested_at=requested,received_at=received,request_id=rid)
        except Exception:fail('source_contract_unverified')
        return SourceResult(facts,cost,billing_evidence=evidence,retry_after=delay)
