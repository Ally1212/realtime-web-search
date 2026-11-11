"""Explicit native adapter bindings and fresh, tenant-bound approval metadata.

No credential lookup, authorization claim, DB initialization or source call here.
Approval evidence is a reference supplied by the trusted operator, not verified
with an external licensing authority by this module.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import inspect
import re
from uuid import UUID

from .data_capability_deployment import CapabilityDeployment
from .data_capability_executor import CapabilityExecutor
from .data_capability_google_ads import GoogleAdsVolumeAdapter
from .data_capability_serpapi import SerpAPITrendsAdapter
from .data_capability_serpapi_ai import SerpAPIAIModeAdapter
from .data_capability_serpapi_aio import SerpAPIAIOAdapter
from .data_capability_facts import timestamp
from .data_capability_jobs import amount,required_text
from .data_capability_responses import ResponseStore
from .product import ProductError

NATIVE = {SerpAPITrendsAdapter:('trends_history','serpapi'),
          SerpAPIAIModeAdapter:('ai_mode_answer','serpapi'),
          SerpAPIAIOAdapter:('aio_answer','serpapi'),
          GoogleAdsVolumeAdapter:('keyword_volume','google_ads')}
GRANT_FIELDS = {'capability','status','provider','license_id','allowed_use','retention_days',
                'billing_unit','currency','maximum_cost','expires_at',
                'raw_response_retention_allowed','source_retry_policy'}


def refused():
    raise ProductError('source_not_admitted','原生来源授权记录缺失、过期或与绑定不符',409) from None


@dataclass(frozen=True,repr=False)
class NativeSourceRoute:
    adapter: object
    license_id: str
    scope: str
    currency: str
    interval_seconds: int
    lease_seconds: int = 300
    poll_seconds: float = 1

    def __post_init__(self):
        if type(self.adapter) not in NATIVE:raise ValueError('explicit supported native adapter required')
        required_text(self.license_id,'license_id');required_text(self.scope,'scope')
        if not isinstance(self.currency,str) or not re.fullmatch('[A-Z]{3}',self.currency):raise ValueError('currency required')
        if type(self.interval_seconds) is not int or not 0<=self.interval_seconds<=86400:raise ValueError('bounded account interval required')
        if type(self.lease_seconds) is not int or not 10<=self.lease_seconds<=900:raise ValueError('bounded lease required')
        if isinstance(self.poll_seconds,bool) or not isinstance(self.poll_seconds,(float,int)) or not .01<=self.poll_seconds<=10:raise ValueError('bounded polling required')
        if (not self.adapter.enabled or not isinstance(self.adapter.billing_unit,str) or not self.adapter.billing_unit):
            raise ValueError('explicitly enabled adapter and confirmed billing resolver required')
        # AIO and Ads invoices have a third phase/request-id argument. Check
        # wiring without invoking the billing resolver or assuming a zero bill.
        bill=self.adapter._bill if isinstance(self.adapter,GoogleAdsVolumeAdapter) else self.adapter._bill_for
        count=3 if type(self.adapter) in (GoogleAdsVolumeAdapter,SerpAPIAIOAdapter) else 2
        try:inspect.signature(bill).bind(*([None]*count))
        except Exception:raise ValueError('native billing callback signature differs; use an explicit trusted wrapper') from None
        if isinstance(self.adapter,GoogleAdsVolumeAdapter) and self.adapter.scope.currency!=self.currency:
            raise ValueError('Ads scope currency differs from binding')

    @property
    def capability(self):return NATIVE[type(self.adapter)][0]

    @property
    def provider(self):return NATIVE[type(self.adapter)][1]

    def __repr__(self):return '<NativeSourceRoute '+self.capability+' '+self.provider+' credentials=redacted>'


class NativeSources:
    def __init__(self, routes, approval_for, *, clock=None):
        if not isinstance(routes,(list,tuple)) or not 1<=len(routes)<=4 or not callable(approval_for):
            raise ValueError('explicit native routes and current tenant approval loader required')
        self._routes={}
        for route in routes:
            if not isinstance(route,NativeSourceRoute) or route.capability in self._routes:
                raise ValueError('exactly one explicit native route per capability; no fallback')
            self._routes[route.capability]=route
        self._approval_for=approval_for;self._clock=clock or (lambda:datetime.now(timezone.utc))

    def __repr__(self):return '<NativeSources explicit_bindings='+str(len(self._routes))+' credentials=redacted>'

    def __call__(self, tenant, capability):
        route=self._routes.get(capability)
        if route is None:return None
        try:
            # JSON copy rejects executable validators and avoids sharing mutable
            # grant dictionaries with the loader or with a previous request.
            record=self._approval_for(str(tenant),capability)
            if record is None:return None
            encoded=json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
            if len(encoded)>32768:refused()
            record=json.loads(encoded)
            if not isinstance(record,dict) or set(record)!={'tenant_id','evidence_id','verified_at','grant'}:refused()
            if record['tenant_id']!=str(tenant) or str(UUID(record['evidence_id']))!=record['evidence_id']:refused()
            now=self._clock();verified=timestamp(record['verified_at'])
            grant=record['grant']
            required=GRANT_FIELDS-{'raw_response_retention_allowed','source_retry_policy'}
            if not isinstance(grant,dict) or set(grant)-GRANT_FIELDS or required-set(grant):refused()
            if (grant['status']!='approved' or not route.adapter.enabled or verified>now or timestamp(grant['expires_at'])<=now
                    or (grant['capability'],grant['provider'],grant['license_id'],grant['currency'],grant['billing_unit'])
                    !=(capability,route.provider,route.license_id,route.currency,route.adapter.billing_unit)):
                refused()
            if (not isinstance(grant['allowed_use'],str) or not grant['allowed_use'].strip()
                    or type(grant['retention_days']) is not int or grant['retention_days']<=0):refused()
            if isinstance(route.adapter,GoogleAdsVolumeAdapter) and grant['allowed_use']!=route.adapter.scope.allowed_use:refused()
            amount(grant['maximum_cost'])
            if 'raw_response_retention_allowed' in grant and type(grant['raw_response_retention_allowed']) is not bool:refused()
            if route.adapter.retain_raw and grant.get('raw_response_retention_allowed') is not True:refused()
            policy=route.adapter.retry_policy
            if (policy.maximum_attempts>1 or 'source_retry_policy' in grant) and grant.get('source_retry_policy')!=policy.public():refused()
            grant['validate_parameters']=route.adapter.validate_parameters
            grant['admission_evidence_ref']='approval:'+record['evidence_id']
            grant['admission_verified_at']=verified.isoformat()
            return grant
        except Exception:
            # Never propagate loader/credential-bearing exception text.
            refused()

    def deployment(self, service, *, cursor_secret, checkpoint_id, checkpoint_hash,
                   response_policy=None, queue_timeout_seconds=300):
        if response_policy is not None and (not isinstance(response_policy,dict) or
                set(response_policy)-{'max_payload_bytes','max_total_bytes','max_active_records','ttl_seconds'}):
            raise ValueError('explicit bounded response policy required')
        store=ResponseStore(service,self,**(response_policy or {}))
        executors=[CapabilityExecutor(service,self,capability=r.capability,provider=r.provider,scope=r.scope,
                                      execute=r.adapter,interval_seconds=r.interval_seconds,lease_seconds=r.lease_seconds,
                                      poll_seconds=r.poll_seconds,response_store=store) for r in self._routes.values()]
        return CapabilityDeployment(service,self,executors=executors,response_store=store,cursor_secret=cursor_secret,
                                    checkpoint_id=checkpoint_id,checkpoint_hash=checkpoint_hash,
                                    queue_timeout_seconds=queue_timeout_seconds)
