"""Opt-in SerpApi AIO: native Google search, then at most one token expansion.

No credential discovery, initialization, provider call or service wiring on import.
Protocol/limits: docs/data-capability-serpapi-aio-2026-11-10.md.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime,timedelta
import json
import math
import re
import time

from .data_capability_backoff import SourceRetrySession,RetryNotEntered
from .data_capability_contract import validate_request
from .data_capability_executor import SourceCallDeferred,SourceFailure,SourceResult
from .data_capability_facts import VERSION,validate_result,http_url
from .data_capability_jobs import amount,digest
from .data_capability_serpapi import SerpAPITrendsAdapter,CurlSerpAPITransport,SourceBill,_redact,retry_after,retry_error_only,UTC
from .data_capability_serpapi_ai import normalize_scoped_answer,_array
from .product import ProductError

PARSER='serpapi-aio-1'
CALL_BYTES=7*1024*1024


def request_parameters(payload):
    p=validate_request(payload)
    if p['capability']!='aio_answer' or p['country'] not in ('US','GB') or p['language']!='en':
        raise ProductError('unsupported_parameter','AIO候选支持US/GB、en、桌面/手机的单轮查询；AI Mode独立',409)
    return dict(engine='google',q=p['prompt'],gl='uk' if p['country']=='GB' else 'us',hl=p['language'],
        device=p['device'],google_domain='google.com',no_cache='true',**{'async':'false','output':'json'})


def _scope(response,sent):
    echo=response.get('search_parameters')
    keys=('engine','page_token') if sent['engine']=='google_ai_overview' else ('engine','q','gl','hl','device','google_domain')
    return isinstance(echo,dict) and all(echo.get(k)==sent[k] for k in keys)


def _metadata(response,requested,received):
    m=response.get('search_metadata')
    if not isinstance(m,dict) or m.get('status')!='Success' or not isinstance(m.get('id'),str) or not m['id']:
        raise ValueError('native success identity missing')
    created=datetime.strptime(m['created_at'],'%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=UTC)
    if not requested.replace(microsecond=0)<=created<=received or m.get('cached',False) is not False or response.get('cached',False) is not False:
        raise ValueError('source creation time/cache disagrees')
    return created


@dataclass(repr=False)
class Phase:
    value: dict
    paths: list
    requested: object
    received: object
    scope: bool
    status: int
    delay: int
    continuation: str | None


class _NotEntered(Exception):
    def __init__(self,code):self.code=code


class SerpAPIAIOAdapter(SerpAPITrendsAdapter):
    requires_response_store=True
    request_parameters=staticmethod(request_parameters)

    def __init__(self,*,call_cost_ceiling=None,sleep=None,**kwargs):
        super().__init__(call_cost_ceiling=call_cost_ceiling,sleep=sleep,**kwargs)
        if self.enabled and (not self.retain_raw or call_cost_ceiling is None):
            raise ValueError('AIO requires explicit raw retention and approved per-call ceiling')
        self.call_ceiling=amount(call_cost_ceiling) if call_cost_ceiling is not None else None
        if kwargs.get('transport') is None:self._transport=CurlSerpAPITransport(max_bytes=CALL_BYTES)
        self._sleep=sleep or time.sleep

    def __repr__(self):return '<SerpAPIAIOAdapter credential=redacted enabled='+str(self.enabled)+'>'

    def validate_parameters(self,payload):
        if not self.enabled:raise ProductError('source_not_admitted','SerpApi AIO候选尚未启用',409)
        request_parameters(payload)

    def _enter(self,ctx,phase,cutoff):
        # Waiting retries only the local gate, NEVER an entered upstream call.
        while self._clock()<cutoff:
            if ctx.canceled():raise _NotEntered('source_canceled_before_call')
            try:
                ctx.before_call(phase,str(self.call_ceiling));return
            except SourceCallDeferred as exc:
                if self._clock()+timedelta(seconds=exc.retry_after)>=cutoff:raise _NotEntered('source_expansion_window_unavailable')
                self._sleep(min(.25,exc.retry_after))
            except ProductError as exc:raise _NotEntered(exc.code) from None
        raise _NotEntered('source_expansion_window_expired')

    def _call(self,ctx,phase,sent,cutoff,receipts,*,secret=None,session=None):
        if session is None:return self._call_once(ctx,phase,sent,cutoff,receipts,secret=secret)
        attempt=0
        while True:
            attempt+=1;native_phase=phase if attempt==1 else phase+'_retry_'+str(attempt)
            try:deadline=session.enter(native_phase,cutoff)
            except RetryNotEntered as exc:raise _NotEntered(exc.code) from None
            out=self._call_once(ctx,native_phase,sent,deadline,receipts,secret=secret,entered=True,billing_phase=phase)
            session.confirmed(receipts[-1]['actual_cost'])
            if out.status in (429,503) and retry_error_only(out.value,out.scope):
                if session.again(out.delay,cutoff):continue
            else:session.successful_leg()
            return out

    def _call_once(self,ctx,phase,sent,cutoff,receipts,*,secret=None,entered=False,billing_phase=None):
        if not entered:self._enter(ctx,phase,cutoff)
        requested=self._clock();params=dict(sent,api_key=self._credential)
        try:reply=self._transport(params,min(ctx.deadline,cutoff))
        except Exception:
            ctx.response.checkpoint(phase,http_status=None,payload=None,requested_at=requested,received_at=self._clock(),
                scope_verified=False,response_complete=False,reason='source_transport_uncertain')
            raise SourceFailure('source_transport_uncertain') from None
        received=self._clock()
        try:
            if len(reply.body)>CALL_BYTES or 'application/json' not in reply.headers.get('content-type','').lower():raise ValueError()
            def pairs(items):
                value={}
                for k,v in items:
                    if k in value:raise ValueError()
                    value[k]=v
                return value
            raw=json.loads(reply.body,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(raw,dict):raise ValueError()
            verified=_scope(raw,sent)
            # Contradictory optional echoes cannot override the bound initial query.
            if sent['engine']=='google_ai_overview':
                echo=raw.get('search_parameters',{})
                expected=request_parameters(ctx.parameters)
                verified=verified and all(k not in echo or echo[k]==expected[k] for k in ('q','gl','hl','device','google_domain'))
            continuation=raw.get('ai_overview',{}).get('page_token') if isinstance(raw.get('ai_overview'),dict) else None
            if continuation is not None and (not isinstance(continuation,str) or not 1<=len(continuation)<=8192 or any(ord(c)<33 for c in continuation)):
                raise ValueError()
            wrapped=dict(response=raw)
            if secret is not None:wrapped['transient_token']=secret
            clean,paths=_redact(wrapped,self._credential);clean=clean['response']
            paths=[p[len('/response'):] for p in paths if p.startswith('/response/')]
        except Exception:
            ctx.response.checkpoint(phase,http_status=reply.status,payload=None,requested_at=requested,received_at=received,
                scope_verified=False,response_complete=False,reason='source_response_invalid')
            raise SourceFailure('source_response_invalid') from None
        ctx.response.checkpoint(phase,http_status=reply.status,payload=clean,requested_at=requested,received_at=received,
            redacted_paths=paths,scope_verified=verified or retry_error_only(clean,verified),reason='source_scope_unverified')
        delay=retry_after(reply.headers.get('retry-after'),received)
        if delay:ctx.defer_calls(delay)
        try:
            bill=self._bill_for(billing_phase or phase,reply.status,clean)
            if (not isinstance(bill,SourceBill) or bill.currency!=ctx.currency or bill.billing_unit!=self.billing_unit
                    or not isinstance(bill.evidence_ref,str) or not re.fullmatch(r'[A-Za-z0-9:_.\-/]{1,200}',bill.evidence_ref)
                    or self._credential in bill.evidence_ref
                    or any(v and v in bill.evidence_ref for v in (secret,continuation))):raise ValueError()
            evidence=dict(reference=bill.evidence_ref,currency=bill.currency,billing_unit=bill.billing_unit,actual_cost=str(amount(bill.actual_cost)))
            ctx.confirm_bill(phase,evidence)
            receipts.append(evidence)
        except Exception:
            raise SourceFailure('source_billing_unresolved',retry_after=delay) from None
        return Phase(clean,paths,requested,received,verified,reply.status,delay,continuation)

    @staticmethod
    def _total(receipts):return sum((amount(v['actual_cost']) for v in receipts),amount('0'))

    def _delay(self,*phases):
        return max(0,*(math.ceil((p.received+timedelta(seconds=p.delay)-self._clock()).total_seconds()) for p in phases))

    def _result(self,ctx,initial,current,receipts,*,failure=None,expanded=False,continuation=False):
        """Normalize only scoped native AIO fields, never organic SERP snippets."""
        value=current.value;overview=value.get('ai_overview')
        valid_blocks=isinstance(overview,dict) and isinstance(overview.get('text_blocks'),list) and bool(overview['text_blocks'])
        if valid_blocks:
            flat=copy.deepcopy(overview)
            for key in ('thumbnail','header_images','serpapi_link'):flat.pop(key,None)
            flat.update(search_metadata=value['search_metadata'],search_parameters=value['search_parameters'])
            facts=normalize_scoped_answer(ctx.parameters,flat,requested_at=initial.requested,received_at=current.received,
                channel='aio',api_version='unversioned-google-ai-overview',parser=PARSER,
                termination='serpapi_google_aio_response',complete_reason='native_aio_text_blocks_no_continuation')
            a=facts['data']['answer']
            for b in a['blocks']:b['source_fields']['source_ref']='/ai_overview'+b['source_fields']['source_ref']
            for c in a['citations']:
                c['source_ref']='/ai_overview'+c['source_ref']
                c['source_fields']['mapping_paths']=['/ai_overview'+p for p in c['source_fields']['mapping_paths']]
            for link in a['links']:
                if 'source_ref' in link['source_fields']:link['source_fields']['source_ref']='/ai_overview'+link['source_fields']['source_ref']
            if failure or continuation:
                facts['is_partial']=True;a['state']='partial'
                a['expansion']=dict(state='expired' if failure=='source_expansion_window_expired' else 'failed' if failure else 'partial',
                    end_reason=failure or 'source_continuation_not_fetched')
            elif expanded:
                a['expansion']=dict(state='partial' if facts['is_partial'] else 'complete',end_reason='native_expansion_success_no_continuation')
        else:
            if not failure:
                if overview is None and 'ai_overview' not in value:state='not_observed'
                elif isinstance(overview,dict) and overview.get('error') is not None:state='source_error'
                elif continuation:state='expansion_failed';failure='source_continuation_not_fetched'
                elif (isinstance(overview,dict) and overview.get('text_blocks')==[] and not overview.get('references')
                      and set(overview)<={'text_blocks','references','thumbnail','header_images'}):state='not_observed'
                else:raise ValueError('missing or contradictory native AIO fields')
            else:state='expired' if failure=='source_expansion_window_expired' else 'expansion_failed'
            partial=state!='not_observed'
            a=dict(id=value['search_metadata']['id'],channel='aio',state=state,answer_text=None,blocks=[],citations=[],links=[],model=None,
                expansion=dict(state='expired' if state=='expired' else 'failed' if state=='expansion_failed' else 'partial' if partial else 'not_required',
                    end_reason=failure or 'source_aio_error' if partial else 'native_google_search_did_not_return_aio_text'),source_fields={})
            facts=dict(schema_version=VERSION,source=dict(provider='serpapi',api_version='unversioned-google-ai-overview',parser_version=PARSER,
                source_request_id=a['id'],source_observed_at=None,unknown_time_reason='provider_request_creation_is_not_an_independent_google_observation_time'),
                requested_at=initial.requested.isoformat(),received_at=current.received.isoformat(),effective_parameters=ctx.parameters,
                is_partial=partial,is_truncated=False,termination_reason='serpapi_google_aio_response',warnings=[],
                raw=dict(status='not_retained',payload=None,redacted_paths=[],reason='native_responses_in_independent_job_journal'),data=dict(answer=a))
        # Decorations and unmapped modules survive verbatim in this source field.
        a['source_fields'].update(initial_source_request_id=initial.value['search_metadata']['id'],native_overview=copy.deepcopy(overview),
            scope_basis='initial_query_echo_and_exact_expansion_token_echo' if expanded else 'native_google_query_echo',
            source_error_code=failure,model_unknown_reason='source_did_not_declare_a_verified_model',
            absence_basis='native_successful_google_search_no_aio_text' if a['state']=='not_observed' else None)
        if isinstance(overview,dict):
            for n,image in enumerate(_array(overview.get('header_images',[]))):
                if not isinstance(image,dict):raise ValueError('invalid native header image')
                for key in ('image','source'):
                    if key in image:
                        http_url(image[key]);a['links'].append(dict(url=image[key],title=None,source_fields=dict(source_ref='/ai_overview/header_images/'+str(n)+'/'+key)))
        facts['received_at']=self._clock().isoformat()
        facts['raw']=dict(status='not_retained',payload=None,redacted_paths=[],reason='native_responses_in_independent_job_journal')
        facts['warnings'].append('aio_country_localization_not_verified_physical_origin;source_error_text_is_not_formal_refusal')
        facts=validate_result(ctx.parameters,facts)
        cost=self._total(receipts)
        evidence=dict(reference='serpapi-aio-bills:'+digest(receipts),currency=ctx.currency,billing_unit=self.billing_unit,actual_cost=str(cost))
        return SourceResult(facts,cost,billing_evidence=evidence,retry_after=self._delay(initial,current))

    def __call__(self,ctx):
        if not self.enabled:raise SourceFailure('source_adapter_disabled',actual_cost='0')
        request_parameters(ctx.parameters)
        if (not self.retain_raw or ctx.response is None or not callable(getattr(ctx.response,'checkpoint',None))
                or not callable(ctx.before_call) or not callable(ctx.confirm_bill) or not callable(ctx.defer_calls)):
            raise SourceFailure('source_call_journal_unavailable',actual_cost='0')
        if self.call_ceiling*2>amount(ctx.maximum_cost):raise SourceFailure('source_call_budget_exceeded',actual_cost='0')
        session=None
        if self.retry_policy.maximum_attempts>1 or ctx.source_retry_policy is not None:
            try:session=SourceRetrySession(ctx,self.retry_policy,self.call_ceiling,self._clock,lambda:self.validate_parameters(ctx.parameters),sleep=self._sleep,monotonic=self._monotonic)
            except RetryNotEntered as exc:raise SourceFailure(exc.code,actual_cost='0') from None
        receipts=[]
        try:initial=self._call(ctx,'search',request_parameters(ctx.parameters),ctx.deadline,receipts,session=session)
        except _NotEntered as exc:
            cost=self._total(receipts)
            evidence=dict(reference='serpapi-aio-bills:'+digest(receipts),currency=ctx.currency,billing_unit=self.billing_unit,actual_cost=str(cost))
            raise SourceFailure(exc.code,actual_cost=cost,billing_evidence=evidence) from None
        def fail(code,delay=0):
            cost=self._total(receipts)
            evidence=dict(reference='serpapi-aio-bills:'+digest(receipts),currency=ctx.currency,billing_unit=self.billing_unit,actual_cost=str(cost))
            raise SourceFailure(code,actual_cost=cost,retry_after=delay,billing_evidence=evidence)
        try:
            if initial.status!=200 or initial.value.get('error') is not None:fail('source_http_error',self._delay(initial))
            if not initial.scope:fail('source_scope_unverified',self._delay(initial))
            created=_metadata(initial.value,initial.requested,initial.received)
            if initial.received>ctx.deadline:fail('source_deadline_exceeded')
            if not initial.continuation:return self._result(ctx,initial,initial,receipts)
            cutoff=min(ctx.deadline,created+timedelta(seconds=59))
            sent=dict(engine='google_ai_overview',page_token=initial.continuation,no_cache='true',**{'async':'false','output':'json'})
            try:expanded=self._call(ctx,'expand',sent,cutoff,receipts,secret=initial.continuation,session=session)
            except _NotEntered as exc:return self._result(ctx,initial,initial,receipts,failure=exc.code)
            if expanded.status!=200 or expanded.value.get('error') is not None or not expanded.scope:
                result=self._result(ctx,initial,initial,receipts,failure='source_expansion_failed')
                return SourceResult(result.facts,result.actual_cost,billing_evidence=result.billing_evidence,retry_after=self._delay(initial,expanded))
            try:_metadata(expanded.value,expanded.requested,expanded.received)
            except Exception:return self._result(ctx,initial,initial,receipts,failure='source_expansion_contract_unverified')
            if expanded.received>cutoff:return self._result(ctx,initial,initial,receipts,failure='source_expansion_window_expired')
            try:return self._result(ctx,initial,expanded,receipts,expanded=True,continuation=expanded.continuation is not None)
            except Exception:return self._result(ctx,initial,initial,receipts,failure='source_expansion_contract_unverified')
        except SourceFailure:raise
        except Exception:fail('source_contract_unverified')
