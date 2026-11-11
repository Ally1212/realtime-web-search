"""Google-shaped SYNTHETIC metrics and approvals; no OAuth/external Ads calls."""
import copy,json,os,time,unittest
from dataclasses import replace
from datetime import timedelta
from realtime.data_capability_google_ads import AdsScope,GoogleAdsVolumeAdapter,CurlGoogleAdsTransport,normalize,request_parameters,MONTHS
from realtime.data_capability_executor import SourceFailure,CapabilityExecutor
from realtime.data_capability_facts import validate_result
from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_serpapi import SourceBill,HTTPResult
from realtime.product import ProductError
from tests.test_data_capability_serpapi import NOW,context
from tests.test_data_capability_serpapi_aio import Sink
from tests.test_data_capability_facts import parameters
from tests import test_data_capability_http as http

ACCESS='fixture-oauth-never-real';DEVELOPER='fixture-developer-never-real'
def scope(now=NOW,**changes):
    return replace(AdsScope('1234567890',None,'fixture-subject','fixture-client','fixture-project','STANDARD','licensed research','US','geoTargetConstants/2840','en','languageConstants/1000','USD','fixture-scope-proof',now-timedelta(hours=1),now+timedelta(hours=1)),**changes)
def response():
    return dict(results=[dict(text='apple',closeVariants=['苹果'],keywordMetrics=dict(avgMonthlySearches='100',monthlySearchVolumes=[dict(year='2026',month='JANUARY',monthlySearches='0'),dict(year='2026',month='FEBRUARY',monthlySearches=None)],competition='LOW',competitionIndex='0',averageCpcMicros='1230000',lowTopOfPageBidMicros='10',highTopOfPageBidMicros='9000000'))])
def reply(value=None,status=200,**headers):
    return HTTPResult(status,{'content-type':'application/json','request-id':'fixture-request-id',**headers},json.dumps(response() if value is None else value).encode())

class GoogleAdsContractTests(unittest.TestCase):
    def setup_source(self,value=None,**overrides):
        self.sent=[];self.bills=[];self.delays=[];self.entered=[];self.sink=Sink();s=scope()
        def transport(body,deadline):
            self.sent.append(copy.deepcopy(body))
            if isinstance(value,Exception):raise value
            return value or reply()
        options=dict(enabled=True,scope=s,access_token=ACCESS,developer_token=DEVELOPER,token_expires_at=NOW+timedelta(minutes=30),admission_for=lambda:s,bill_for=lambda status,value,rid:SourceBill('0.1','USD','fixture-call','fixture-bill'),billing_unit='fixture-call',call_cost_ceiling='0.15',retain_raw=True,transport=transport,clock=lambda:NOW)
        options.update(overrides);adapter=GoogleAdsVolumeAdapter(**options)
        def bill(phase,value):
            self.assertEqual(len(self.sink.calls),1);self.bills.append((phase,value))
        ctx=replace(context(parameters('keyword_volume')),response=self.sink,before_call=lambda *a:self.entered.append(a),confirm_bill=bill,defer_calls=self.delays.append)
        return adapter,ctx
    def test_disabled_scope_admission_expiry_and_whole_budget_before_call(self):
        with self.assertRaises(SourceFailure):GoogleAdsVolumeAdapter()(context())
        with self.assertRaises(ValueError):GoogleAdsVolumeAdapter(enabled=True)
        a,c=self.setup_source()
        for mutation in [dict(maximum_cost='0.1'),dict(response=None),dict(before_call=None),dict(confirm_bill=None),dict(defer_calls=None),dict(canceled=lambda:True)]:
            with self.subTest(mutation=list(mutation)),self.assertRaises(SourceFailure) as e:a(replace(c,**mutation))
            self.assertEqual(str(e.exception.actual_cost),'0')
        self.assertEqual(self.sent,[]);self.assertNotIn(ACCESS,repr(a))
        for mutation in [dict(expires_at=NOW),dict(access_level='TEST_ACCOUNT'),dict(geo_target='https://evil.invalid'),dict(currency='usd'),dict(verified_at=NOW+timedelta(seconds=1))]:
            with self.subTest(scope=mutation),self.assertRaises(ValueError):scope(**mutation).validate(NOW)
        a._admission=lambda:scope(project_id='different-project')
        with self.assertRaises(SourceFailure):a(c)
        self.assertEqual(self.sent,[])
    def test_exact_dedup_source_params_network_and_full_month_limits(self):
        p=parameters('keyword_volume');b=request_parameters(p,scope(),NOW)
        self.assertEqual(b['keywords'],['苹果','apple']);self.assertEqual(p['keywords'],['苹果','apple','apple'])
        self.assertEqual(b['historicalMetricsOptions']['yearMonthRange']['start'],dict(year=2026,month='JANUARY'))
        self.assertEqual(request_parameters(dict(p,network='google_and_partners'),scope(),NOW)['keywordPlanNetwork'],'GOOGLE_SEARCH_AND_PARTNERS')
        for changes in [dict(country='GB'),dict(language='zh'),dict(start_month='2020-01'),dict(end_month='2026-11')]:
            with self.subTest(changes=changes),self.assertRaises(ProductError):request_parameters(dict(p,**changes),scope(),NOW)
        for network in ['google','google_and_partners']:
            for language in ['en','zh']:
                with self.subTest(network=network,language=language):
                    p=dict(p,language=language,network=network);s=scope(language=language,language_constant='languageConstants/1017' if language=='zh' else 'languageConstants/1000');self.assertEqual(request_parameters(p,s,NOW)['language'],s.language_constant)
    def test_merged_variants_approximate_zero_null_month_micros_and_unknown_average_window(self):
        a,c=self.setup_source();out=a(c);f=out.facts;g=f['data']['groups'][0]
        self.assertEqual([x['record_id'] for x in f['data']['input_mapping']],[g['id']]*3)
        self.assertEqual(g['source_fields']['native'],response()['results'][0]);self.assertEqual(g['statistic_window'],dict(start_month=None,end_month=None,reason='source_does_not_echo_effective_average_window'))
        self.assertEqual(g['monthly'][0]['metric']['value'],'0');self.assertEqual(g['monthly'][0]['metric']['status'],'available')
        self.assertIsNone(g['monthly'][1]['metric']['value']);self.assertTrue(f['is_partial'])
        self.assertEqual(g['commercial_fields']['cpc']['metric']['value'],'1230000');self.assertEqual(g['commercial_fields']['cpc']['unit'],'micros')
        self.assertIsNone(f['source']['source_observed_at']);self.assertEqual(str(out.actual_cost),'0.1');self.assertEqual(len(self.sent),1)
    def test_no_data_omitted_months_unreturned_inputs_and_unknown_window_validation(self):
        r=response();r['results'][0]['closeVariants']=[];r['results'][0]['keywordMetrics']['monthlySearchVolumes']=[]
        a,c=self.setup_source(reply(r));g=a(c).facts['data'];self.assertIsNone(g['input_mapping'][0]['record_id']);self.assertEqual(len(g['groups'][0]['monthly']),2)
        a,c=self.setup_source(reply({}));self.assertEqual(a(c).facts['data']['groups'],[])
        a,c=self.setup_source();f=a(c).facts
        for window in [dict(start_month=None,end_month='2026-01',reason='unknown'),dict(start_month=None,end_month=None),dict(start_month='2026-01',end_month='2026-02',reason='unknown')]:
            with self.subTest(window=window),self.assertRaises(ProductError):
                v=copy.deepcopy(f);v['data']['groups'][0]['statistic_window']=window;validate_result(c.parameters,v)
    def test_malformed_native_values_and_ambiguous_variants_fail_with_known_bill(self):
        mutations=[lambda r:r['results'][0]['keywordMetrics'].update(avgMonthlySearches=1.5),lambda r:r['results'][0]['keywordMetrics'].update(avgMonthlySearches=True),lambda r:r['results'][0]['keywordMetrics'].update(avgMonthlySearches='9223372036854775808'),lambda r:r['results'][0]['keywordMetrics'].update(competitionIndex='101'),lambda r:r['results'][0]['keywordMetrics'].update(competition='SEO_HARD'),lambda r:r['results'][0].update(closeVariants=['not-requested']),lambda r:r['results'].append(copy.deepcopy(r['results'][0])),lambda r:r['results'][0]['keywordMetrics']['monthlySearchVolumes'][0].update(month='UNSPECIFIED'),lambda r:r['results'][0]['keywordMetrics']['monthlySearchVolumes'][0].update(year='2025'),lambda r:r['results'][0]['keywordMetrics'].update(monthlySearchVolumes=[dict(year='2026',month='JANUARY',monthlySearches='1')]*2)]
        for m in mutations:
            with self.subTest(mutation=mutations.index(m)):
                r=response();m(r);a,c=self.setup_source(reply(r))
                with self.assertRaises(SourceFailure) as e:a(c)
                self.assertEqual(str(e.exception.actual_cost),'0.1');self.assertEqual(len(self.sent),1);self.assertEqual(len(self.sink.calls),1)
    def test_unknown_bill_invalid_response_transport_and_rate_limit_never_retry(self):
        for r in [RuntimeError(ACCESS),replace(reply(),body=b'{"a":1,"a":2}'),replace(reply(),headers={'content-type':'text/html','request-id':'fixture-id'})]:
            with self.subTest(kind=type(r).__name__):
                a,c=self.setup_source(r)
                with self.assertRaises(SourceFailure) as e:a(c)
                self.assertIsNone(e.exception.actual_cost);self.assertNotIn(ACCESS,str(e.exception));self.assertEqual(len(self.sent),1)
        a,c=self.setup_source(bill_for=lambda *a:None)
        with self.assertRaises(SourceFailure) as e:a(c)
        self.assertEqual(e.exception.code,'source_billing_unresolved');self.assertEqual(len(self.sink.calls),1)
        a,c=self.setup_source(reply(dict(error=dict(message=ACCESS+' '+DEVELOPER)),status=429,**{'retry-after':'70'}))
        with self.assertRaises(SourceFailure) as e:a(c)
        self.assertEqual((str(e.exception.actual_cost),e.exception.retry_after),('0.1',70));self.assertEqual(self.delays,[70]);self.assertNotIn(ACCESS,json.dumps(self.sink.calls,default=str));self.assertNotIn(DEVELOPER,json.dumps(self.sink.calls,default=str));self.assertEqual(len(self.sent),1)

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class GoogleAdsHTTPTests(unittest.TestCase):
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    wait=http.CapabilityHTTPTests.wait
    def work(self):self.stop_worker.wait()
    def submit(self,**kwargs):return http.CapabilityHTTPTests.submit(self,'keyword_volume',**kwargs)
    def setUp(self):
        http.CapabilityHTTPTests.setUp(self);self.calls=[];self.reply=reply();self.scope=scope(self.now)
        def transport(body,deadline):self.calls.append(copy.deepcopy(body));return self.reply
        self.source=GoogleAdsVolumeAdapter(enabled=True,scope=self.scope,access_token=ACCESS,developer_token=DEVELOPER,token_expires_at=self.now+timedelta(minutes=30),admission_for=lambda:self.scope,bill_for=lambda *a:SourceBill('0.1','USD','fixture-call','fixture-invoice'),billing_unit='fixture-call',call_cost_ceiling='0.15',retain_raw=True,transport=transport,clock=lambda:self.now)
        self.grants['keyword_volume'].update(provider='google_ads',billing_unit='fixture-call',validate_parameters=self.source.validate_parameters,raw_response_retention_allowed=True)
        self.spool=ResponseStore(self.data,lambda t,c:self.grants[c]);self.spool.initialize()
        self.executor=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='keyword_volume',provider='google_ads',scope=self.schema,execute=self.source,interval_seconds=0,poll_seconds=.01,response_store=self.spool)
        self.executor.initialize();self.runtime.worker_ready=self.executor.ready;self.runtime.response_store=self.spool;self.executor.start()
        end=time.monotonic()+3
        while not self.executor.ready(str(self.account['account_id']),'keyword_volume','google_ads') and time.monotonic()<end:time.sleep(.01)
    def tearDown(self):
        self.assertTrue(self.executor.stop());http.CapabilityHTTPTests.tearDown(self)
    def test_customer_merged_groups_months_fixed_exports_journal_and_tenant_rights(self):
        from realtime.data_capability_export import verify_bundle
        from realtime.data_capability_response_export import verify_bundle as verify_response
        job=self.submit();state=self.wait(job);self.assertEqual((state['status'],state['actual_cost']),('partial','0.1'))
        code,data=self.json(job['data_url']);self.assertEqual(code,200,data);o=data['observation'];g=o['payload']['data']['groups'][0]
        self.assertEqual(g['source_fields']['native'],response()['results'][0]);self.assertIsNone(g['statistic_window']['start_month'])
        code,roots=self.json('/v1/data/keyword-volumes?capability=keyword_volume&limit=1');self.assertEqual(code,200,roots)
        root='/v1/data/keyword-volumes/'+roots['records'][0]['object_id']
        code,page=self.json(root+'/months?limit=1');self.assertEqual(code,200,page);months=page['records']
        while page['has_more']:
            code,page=self.json(root+'/months?limit=1&cursor='+page['next_cursor']);self.assertEqual(code,200,page);months+=page['records']
        self.assertEqual(len(months),2)
        code,doc=self.json('/v1/openapi.json');self.assertEqual(code,200);self.assertEqual(doc['info']['version'],'1.9.0')
        window=doc['components']['schemas']['CapabilityVolume']['properties']['statistic_window']
        self.assertEqual(window['anyOf'][1]['properties']['start_month']['enum'],[None]);self.assertIn('reason',window['anyOf'][1]['required'])
        for f in ['json','jsonl','csv','zip','manifest']:
            with self.subTest(format=f):
                code,body,_=self.request(job['status_url']+'/export?format='+f+'&snapshot_hash='+o['snapshot_hash']);self.assertEqual(code,200)
                if f=='zip':verify_bundle(body,expected_payload_hash=o['payload_hash'],expected_observation_id=o['observation_id'],expected_snapshot_hash=o['snapshot_hash'])
        code,s=self.json(job['status_url']+'/response');self.assertEqual(code,200,s);self.assertEqual(s['call_count'],1)
        payload=s['payload']['calls'][0]['payload'];self.assertEqual(payload['request']['keywords'],['苹果','apple']);self.assertEqual(payload['response'],response())
        code,body,_=self.request(job['status_url']+'/response/export?format=zip');self.assertEqual(code,200);verify_response(body,expected_response_id=s['response_id'],expected_snapshot_hash=s['snapshot_hash'])
        for path in [job['data_url'],job['status_url']+'/response',job['status_url']+'/progress']:self.assertEqual(self.json(path,key=self.other['api_key'])[0],404)
        self.grants['keyword_volume']['status']='revoked';self.assertEqual(self.json(job['data_url'])[0],409);self.assertEqual(len(self.calls),1)
    def test_unknown_invoice_retains_response_and_quarantines_without_repeat(self):
        self.source._bill=lambda *a:None;job=self.submit();end=time.monotonic()+4
        while time.monotonic()<end:
            state=self.json(job['status_url'])[1]
            if state['status']=='reconciliation_required':break
            time.sleep(.01)
        self.assertEqual(state['status'],'reconciliation_required');self.assertIsNone(state['actual_cost']);self.assertEqual(state['reservation_status'],'held')
        self.assertEqual(self.json(job['status_url']+'/response')[0],200);self.assertEqual(self.json(job['data_url'])[0],409)
        self.assertEqual(self.json('/v1/data/jobs',method='POST',payload=dict(parameters('keyword_volume'),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='no-repeat')[0],503);self.assertEqual(len(self.calls),1)
    def test_search_volume_subscription_preserves_merged_native_group_and_fixed_ack(self):
        job=self.submit();self.wait(job)
        code,sub=self.json('/v1/data/subscriptions',method='POST',payload=dict(domain='data',name='Google Ads synthetic metrics',filters=dict(capability='keyword_volume'),start_mode='history'),idempotency='ads-sub')
        self.assertEqual(code,200,sub);self.assertFalse(sub['active']);root='/v1/data/subscriptions/'+sub['id']
        self.assertEqual(self.json(root+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(root+'/pull',method='POST',payload={});self.assertEqual(code,200,batch)
        self.assertEqual(batch['observation']['payload']['data']['groups'][0]['source_fields']['native'],response()['results'][0])
        path=root+'/batches/'+batch['batch_id'];code,before,_=self.request(path+'/export?format=zip');self.assertEqual(code,200)
        self.assertEqual(self.json(path+'/ack',method='POST',payload=dict(sha256=batch['sha256']))[0],200)
        self.assertEqual(self.request(path+'/export?format=zip')[1],before);self.assertEqual(len(self.calls),1)
    def test_known_http_rate_limit_settles_bill_and_persists_account_cooldown(self):
        self.reply=reply(dict(error=dict(code=429,message='fixture quota exceeded')),status=429,**{'retry-after':'70'})
        job=self.submit();state=self.wait(job);self.assertEqual((state['status'],state['actual_cost']),('failed','0.1'))
        with self.data.connect() as db:r=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()
        self.assertGreaterEqual(r['next_at'],self.now+timedelta(seconds=70));self.assertEqual(self.json(job['data_url'])[0],409);self.assertEqual(len(self.calls),1)

class GoogleAdsTransportTests(unittest.TestCase):
    def test_fixed_post_tls_account_headers_deadline_and_stream_bounds(self):
        from curl_cffi import CurlOpt
        from datetime import datetime,timezone
        class Fake:
            def __init__(self,mode):self.opts={};self.closed=False;self.mode=mode
            def setopt(self,k,v):self.opts[k]=v
            def getinfo(self,k):return 200
            def close(self):self.closed=True
            def perform(self):
                h=self.opts[CurlOpt.HEADERFUNCTION];w=self.opts[CurlOpt.WRITEFUNCTION]
                h(b'HTTP/1.1 200 OK\r\n');h(b'Content-Type: application/json\r\n');h(b'Request-Id: fixture-id\r\n');h(b'Set-Cookie: ignored\r\n')
                if self.mode=='body':
                    from realtime.data_capability_google_ads import MAX_BODY
                    if not w(b'x'*(MAX_BODY+1)):raise RuntimeError(ACCESS)
                elif self.mode=='header':
                    if not h(b'x'*65537):raise RuntimeError(DEVELOPER)
                else:w(b'{"results":[]}')
        for mode in ['ok','body','header']:
            with self.subTest(mode=mode):
                f=Fake(mode);s=scope(login_customer_id='9876543210');t=CurlGoogleAdsTransport(s,ACCESS,DEVELOPER,curl_factory=lambda:f)
                deadline=datetime.now(timezone.utc)+timedelta(seconds=1)
                if mode=='ok':
                    r=t({'keywords':['苹果']},deadline);self.assertEqual(r.body,b'{"results":[]}');self.assertNotIn('set-cookie',r.headers)
                else:
                    with self.assertRaises(SourceFailure) as e:t({},deadline)
                    self.assertEqual(e.exception.code,'source_response_too_large');self.assertNotIn(ACCESS,str(e.exception))
                self.assertTrue(f.closed);o=f.opts
                self.assertEqual(o[CurlOpt.URL],'https://googleads.googleapis.com/v26/customers/1234567890:generateKeywordHistoricalMetrics')
                self.assertNotIn(ACCESS,o[CurlOpt.URL]);self.assertEqual(o[CurlOpt.PROTOCOLS_STR],'https');self.assertEqual(o[CurlOpt.FOLLOWLOCATION],0);self.assertEqual(o[CurlOpt.SSL_VERIFYPEER],1);self.assertEqual(o[CurlOpt.SSL_VERIFYHOST],2);self.assertEqual(o[CurlOpt.PROXY],'');self.assertEqual(o[CurlOpt.POST],1)
                self.assertIn(b'login-customer-id: 9876543210',o[CurlOpt.HTTPHEADER]);self.assertLessEqual(o[CurlOpt.TIMEOUT_MS],1000);self.assertGreater(o[CurlOpt.TIMEOUT_MS],0)
