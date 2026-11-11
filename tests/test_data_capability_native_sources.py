"""Native-shaped synthetic inputs only; these are not vendor approvals or gold."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import time
import traceback
import unittest
from uuid import uuid4
from unittest.mock import patch

from realtime.data_capability_native_sources import NativeSourceRoute,NativeSources
from realtime.data_capability_backoff import RetryPolicy
from realtime.data_capability_contract import admit_request
from realtime.data_capability_serpapi import SourceBill
from realtime.data_capability_serpapi_aio import SerpAPIAIOAdapter
from realtime.data_capability_google_ads import GoogleAdsVolumeAdapter
from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_jobs import digest
from realtime.data_capability_retention import write_checkpoint
from realtime.product import ProductError
from tests import test_data_capability_serpapi as trends
from tests import test_data_capability_serpapi_ai as ai
from tests import test_data_capability_serpapi_aio as aio
from tests import test_data_capability_google_ads as ads
from tests import test_data_capability_http as http
from tests.test_data_capability_facts import parameters


def configuration(tenant='fixture-tenant',sent=None):
    sent=[] if sent is None else sent
    def transport(cap,response):
        def send(p,d):
            sent.append((cap,copy.deepcopy(p)))
            if cap=='aio_answer':return trends.http_result(aio.expansion() if p['engine']=='google_ai_overview' else aio.initial())
            return response()
        return send
    sources=[trends.adapter(transport=transport('trends_history',lambda:trends.http_result())),
             ai.adapter(transport=transport('ai_mode_answer',lambda:trends.http_result(ai.response()))),
             SerpAPIAIOAdapter(enabled=True,credential=trends.CREDENTIAL,bill_for=lambda phase,s,r:SourceBill('0.1','USD','fixture-search','fixture-bill'),
                              billing_unit='fixture-search',retain_raw=True,call_cost_ceiling='0.15',clock=lambda:trends.NOW,
                              transport=transport('aio_answer',lambda:None))]
    scope=ads.scope()
    sources.append(GoogleAdsVolumeAdapter(enabled=True,scope=scope,access_token=ads.ACCESS,developer_token=ads.DEVELOPER,
        token_expires_at=trends.NOW+timedelta(minutes=30),admission_for=lambda:scope,bill_for=lambda s,r,i:SourceBill('0.1','USD','fixture-call','fixture-bill'),
        billing_unit='fixture-call',call_cost_ceiling='0.15',retain_raw=True,transport=transport('keyword_volume',ads.reply),clock=lambda:trends.NOW))
    routes=[NativeSourceRoute(a,'fixture-license','fixture-ads-account' if isinstance(a,GoogleAdsVolumeAdapter) else 'fixture-serpapi-account','USD',0,poll_seconds=.01) for a in sources]
    records={r.capability:dict(tenant_id=tenant,evidence_id=str(uuid4()),verified_at='2026-11-10T11:00:00Z',
        grant=dict(capability=r.capability,status='approved',provider=r.provider,license_id=r.license_id,allowed_use='licensed research',
                   retention_days=1,billing_unit=r.adapter.billing_unit,currency='USD',maximum_cost='0.40',expires_at='2026-11-10T13:00:00Z',raw_response_retention_allowed=True)) for r in routes}
    binding=NativeSources(routes,lambda t,c:records.get(c),clock=lambda:trends.NOW)
    return binding,records,routes,sent


class NativeBindingContractTests(unittest.TestCase):
    def error(self,call):
        with self.assertRaises(ProductError) as e:call()
        self.assertEqual(e.exception.code,'source_not_admitted')

    def test_native_route_parameter_validators_proof_refs_and_no_constructor_calls(self):
        binding,records,routes,sent=configuration()
        self.assertEqual(sent,[])
        for route in routes:
            with self.subTest(cap=route.capability):
                g=binding('fixture-tenant',route.capability)
                p=parameters(route.capability);admit_request(p,g,'0.5','USD',now=trends.NOW)
                self.assertEqual(g['validate_parameters'].__self__,route.adapter)
                self.assertEqual(g['admission_evidence_ref'],'approval:'+records[route.capability]['evidence_id'])
                self.assertNotIn('validate_parameters',records[route.capability]['grant'])
        self.assertNotIn(trends.CREDENTIAL,repr(routes));self.assertNotIn(ads.ACCESS,repr(binding));self.assertEqual(sent,[])
        self.assertIsNone(binding('fixture-tenant','not_registered'))

    def test_current_tenant_license_currency_units_retention_and_approval_time_are_enforced(self):
        for field,value in [('tenant_id','another-tenant'),('evidence_id','arbitrary text'),('verified_at','2027-01-01T00:00:00Z'),
                            ('provider','google_ads'),('capability','keyword_volume'),('license_id','other'),('currency','EUR'),
                            ('billing_unit','other'),('retention_days',0),('maximum_cost','NaN'),('status','revoked'),
                            ('expires_at','2026-11-10T12:00:00Z'),('raw_response_retention_allowed',False)]:
            with self.subTest(field=field):
                binding,records,_,sent=configuration();record=records['trends_history']
                (record if field in ('tenant_id','evidence_id','verified_at') else record['grant'])[field]=value
                self.error(lambda:binding('fixture-tenant','trends_history'));self.assertEqual(sent,[])
        binding,records,_,_=configuration();self.error(lambda:binding('wrong-tenant','keyword_volume'))
        records['keyword_volume']['grant']['allowed_use']='different use';self.error(lambda:binding('fixture-tenant','keyword_volume'))

    def test_no_executable_loader_metadata_unknown_fields_or_mismatched_retry_policy(self):
        for value in [dict(validate_parameters=lambda p:None),dict(access_token='synthetic secret'),dict(unknown='field'),
                      dict(source_retry_policy=RetryPolicy(maximum_attempts=2).public())]:
            with self.subTest(keys=list(value)):
                binding,records,_,_=configuration();records['trends_history']['grant'].update(value)
                self.error(lambda:binding('fixture-tenant','trends_history'))
        binding,records,routes,_=configuration();g=binding('fixture-tenant','trends_history');g['license_id']='tampered'
        self.assertEqual(binding('fixture-tenant','trends_history')['license_id'],'fixture-license')
        records['trends_history']['grant']['status']='revoked';self.error(lambda:binding('fixture-tenant','trends_history'))
        bad=NativeSources(routes,lambda t,c:(_ for _ in ()).throw(RuntimeError('synthetic private token')),clock=lambda:trends.NOW)
        with self.assertRaises(ProductError) as e:bad('fixture-tenant','trends_history')
        self.assertNotIn('synthetic private token',str(e.exception))
        self.assertNotIn('synthetic private token',''.join(traceback.format_exception(e.exception)))

    def test_route_mismatch_duplicates_and_disabled_adapter_rejected(self):
        _,_,routes,_=configuration()
        with self.assertRaises(ValueError):NativeSources([routes[0],routes[0]],lambda t,c:None)
        with self.assertRaises(ValueError):NativeSourceRoute(trends.SerpAPITrendsAdapter(),'license','scope','USD',0)
        with self.assertRaises(ValueError):NativeSourceRoute(routes[-1].adapter,'license','scope','EUR',0)
        with self.assertRaises(ValueError):NativeSourceRoute(lambda c:None,'license','scope','USD',0)
        invalid=copy.copy(routes[2].adapter);invalid._bill_for=lambda status,payload:None
        with self.assertRaises(ValueError):NativeSourceRoute(invalid,'license','scope','USD',0)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class NativeBindingHTTPTests(unittest.TestCase):
    setUp=http.CapabilityHTTPTests.setUp
    tearDown=http.CapabilityHTTPTests.tearDown
    work=http.CapabilityHTTPTests.work
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    submit=http.CapabilityHTTPTests.submit
    wait=http.CapabilityHTTPTests.wait

    def test_all_four_native_adapters_bind_to_approval_and_complete_api_spool_export_without_real_calls(self):
        self.stop_worker.set();self.worker.join(3);self.assertFalse(self.worker.is_alive())
        binding,records,routes,sent=configuration(str(self.account['account_id']))
        spool=ResponseStore(self.data,binding);spool.initialize();recovery=DataErasureCheckpoint(self.data,spool);recovery.initialize()
        key=b'fixture-native-binding-signing-key-0001';value=recovery.export(key)
        with tempfile.TemporaryDirectory(prefix='sf-native-binding-') as directory:
            path=Path(directory)/'v3.json';write_checkpoint(path,value)
            d=binding.deployment(self.data,cursor_secret=b'fixture-native-binding-cursor-secret'*2,
                                 checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
            try:
                self.assertEqual(sent,[]);d.initialize();d.recover_file(path,key);self.assertEqual(sent,[])
                d.start();del self.server.data_capability_runtime;d.mount(self.server)
                for route in routes:
                    cap=route.capability;deadline=time.monotonic()+3
                    while time.monotonic()<deadline and not d.ready(str(self.account['account_id']),cap,route.provider):time.sleep(.01)
                    self.assertTrue(d.ready(str(self.account['account_id']),cap,route.provider))
                    idempotency=str(uuid4());submitted=self.submit(cap,idempotency=idempotency)
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        row=self.data.get(str(self.account['account_id']),submitted['id'])
                        if row['status'] not in ('queued','running'):break
                        time.sleep(.05)
                    self.assertNotIn(row['status'],('queued','running'),dict(capability=cap,status=row['status'],error_code=row['error_code'],deployment=d.status()))
                    code,job=self.json('/v1/jobs/'+submitted['id']);self.assertEqual(code,200,job)
                    self.assertIn(job['status'],('succeeded','partial'),job)
                    self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],200)
                    self.assertEqual(self.request('/v1/jobs/'+job['id']+'/export?format=zip')[0],200)
                    self.assertEqual(self.json('/v1/jobs/'+job['id']+'/response')[0],200)
                    with self.data.connect() as db:
                        row=db.execute('SELECT admission,idempotency_key,actual_cost FROM sf_capability_jobs WHERE id=%s',(job['id'],)).fetchone()
                        self.assertEqual(row['idempotency_key'],idempotency)
                        self.assertEqual(row['admission']['admission_evidence_ref'],'approval:'+records[cap]['evidence_id'])
                        self.assertEqual(str(row['actual_cost']),'0.2' if cap=='aio_answer' else '0.1')
                    count=len(sent);records[cap]['grant']['status']='revoked'
                    self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],409)
                    self.assertEqual(self.request('/v1/jobs/'+job['id']+'/export?format=zip')[0],409)
                    self.assertEqual(len(sent),count)
                self.assertEqual(len(sent),5) # AIO search + expansion; three independent single-call domains.
                self.assertEqual(self.json('/v1/jobs')[0],200)
            finally:self.assertTrue(d.stop()['stopped'])
