"""Provider-shaped SYNTHETIC responses, never real SerpApi requests/records."""
import copy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from realtime.data_capability_executor import CapabilityExecutor, SourceContext, SourceFailure
from realtime.data_capability_serpapi import (CurlSerpAPITransport, HTTPResult, SerpAPITrendsAdapter, SourceBill,
                                             normalize, request_parameters, retry_after, _redact)
from realtime.product import ProductError
from tests import test_data_capability_store as store_fixture
from tests.test_data_capability_facts import parameters

UTC = timezone.utc
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
CREDENTIAL = 'fixture-only-not-a-real-serpapi-credential'


def response(p=None, request_id='fixture-serpapi-native-request'):
    p = p or parameters()
    effective = request_parameters(p)
    return dict(search_metadata=dict(id=request_id, status='Success', created_at='2026-11-10 12:00:00 UTC'),
                search_parameters=effective,
                interest_over_time=dict(timeline_data=[
                    dict(date='Jan 1, 2026', timestamp='1767225600', values=[
                        dict(query=term, query_index=i, value='0' if i == 0 else '<1', extracted_value=0)
                        for i, term in enumerate(p['terms'])]),
                    dict(date='Jan 2, 2026', timestamp='1767312000', partial_data=True, values=[
                        dict(query=term, query_index=i, value='50', extracted_value=50)
                        for i, term in enumerate(p['terms'])])], averages=[{'query': p['terms'][0], 'value': 25}]))


class MemoryResponse:
    """Unit-test sink only; persistence is exercised by real PostgreSQL tests."""
    def __init__(self):self.calls=[]
    def save(self,**kwargs):self.calls.append(copy.deepcopy(kwargs))


def capture_store(fixture):
    from realtime.data_capability_responses import ResponseStore
    fixture.grants['trends_history']['raw_response_retention_allowed']=True
    store=ResponseStore(fixture.data,lambda t,c:fixture.grants[c]);store.initialize()
    return store


def context(p=None):
    return SourceContext('fixture-job-id', p or parameters(), '0.40', 'USD', NOW+timedelta(seconds=60), 'fixture-event', lambda: False, response=MemoryResponse())


def http_result(value=None, status=200, headers=None):
    return HTTPResult(status, headers or {'content-type': 'application/json'}, json.dumps(value if value is not None else response()).encode())


def adapter(transport=None, **kwargs):
    options = dict(credential=CREDENTIAL, enabled=True, retain_raw=True, billing_unit='fixture-search',
                   bill_for=lambda status, payload: SourceBill('0.1', 'USD', 'fixture-search', 'fixture-independent-bill'),
                   transport=transport or (lambda params, deadline: http_result()), clock=lambda: NOW)
    options.update(kwargs)
    return SerpAPITrendsAdapter(**options)


class SerpAPIContractTests(unittest.TestCase):
    def test_disabled_is_default_no_environment_or_network(self):
        e = SerpAPITrendsAdapter()
        self.assertFalse(e.enabled)
        with self.assertRaises(SourceFailure) as exc:
            e(context())
        self.assertEqual((exc.exception.code, str(exc.exception.actual_cost)), ('source_adapter_disabled', '0'))
        with self.assertRaises(ProductError):
            e.validate_parameters(parameters())
        with self.assertRaises(ValueError):
            SerpAPITrendsAdapter(enabled=True)
        self.assertNotIn(CREDENTIAL, repr(adapter()))

    def test_parameter_mapping_exact_no_silent_source_change(self):
        for mode, gprop in [('web',''),('images','images'),('news','news'),('shopping','froogle'),('youtube','youtube')]:
            with self.subTest(mode=mode):
                p = dict(parameters(), search_type=mode)
                sent = request_parameters(p)
                self.assertEqual(sent['gprop'], gprop)
                self.assertEqual(sent['geo'], p['country'])
                self.assertEqual(sent['q'], ','.join(p['terms']))
                self.assertEqual(sent['tz'], '0')
                self.assertEqual(sent['no_cache'], 'true')
                self.assertEqual(sent['async'], 'false')
        for change in [dict(language='zh'),dict(start_date='2003-01-01'),dict(terms=['a,b']),dict(terms=['same','same']),dict(terms=['a'*101])]:
            with self.subTest(change=change), self.assertRaises(ProductError):
                request_parameters(dict(parameters(), **change))
        with self.assertRaises(ProductError):
            request_parameters(parameters('keyword_volume'))

    def test_dates_scale_mapping_partial_and_censored_zero(self):
        p = parameters()
        facts = adapter()(context()).facts
        a,b = facts['data']['series']
        self.assertEqual(a['scale_id'], b['scale_id'])
        self.assertEqual(a['source_timezone'], 'UTC')
        self.assertEqual(a['points'][0]['period_end'], '2026-01-02T00:00:00+00:00')
        self.assertEqual(a['points'][0]['metric']['value'], '0')
        self.assertEqual(a['points'][0]['metric']['status'], 'available')
        self.assertTrue(a['points'][0]['metric']['censored'])
        self.assertEqual(b['points'][0]['metric']['status'], 'below_threshold')
        self.assertIsNone(b['points'][0]['metric']['value'])
        self.assertTrue(facts['is_partial'])
        self.assertFalse(facts['is_truncated'])
        self.assertIsNone(facts['source']['source_observed_at'])
        self.assertIn('request_time',facts['source']['unknown_time_reason'])
        self.assertEqual(facts['raw']['payload'], response())
        self.assertEqual(a['regional_interest']['status'], 'not_covered')
        different = normalize(p, response(request_id='another-source-observation'), requested_at=NOW, received_at=NOW, retain_raw=True)
        self.assertNotEqual(a['scale_id'], different['data']['series'][0]['scale_id'])

    def test_week_calendar_end_from_source_not_next_point_or_requested_end(self):
        p = dict(parameters(), start_date='2025-12-28', end_date='2026-01-10')
        r = response(p)
        r['interest_over_time']['timeline_data'][0].update(date='Dec 28, 2025 – Jan 3, 2026',timestamp='1766880000')
        r['interest_over_time']['timeline_data'][1].update(date='Jan 4 – 10, 2026',timestamp='1767484800')
        facts = normalize(p, r, requested_at=NOW,received_at=NOW,retain_raw=True)
        self.assertEqual(facts['data']['series'][0]['granularity'], 'week')
        self.assertEqual(facts['data']['series'][0]['points'][-1]['period_end'], '2026-01-11T00:00:00+00:00')

    def test_empty_is_no_data_not_zero_or_new_synthetic_series(self):
        r = response();r['interest_over_time']['timeline_data']=[]
        facts = adapter(lambda params, deadline: http_result(r))(context()).facts
        self.assertEqual(facts['data']['series'], [])
        self.assertEqual([m['status'] for m in facts['data']['input_mapping']], ['not_observed']*2)

    def test_bad_echo_time_query_numeric_or_period_is_paid_failure(self):
        mutations = [lambda r:r['search_parameters'].update(geo='GB'),
                     lambda r:r['search_parameters'].pop('tz'),
                     lambda r:r['search_metadata'].update(created_at='2026-11-09 12:00:00 UTC'),
                     lambda r:r['interest_over_time']['timeline_data'][0].update(date='1 janvier 2026'),
                     lambda r:r['interest_over_time']['timeline_data'][0].update(timestamp='1767312000'),
                     lambda r:r['interest_over_time']['timeline_data'][0]['values'][0].update(query_index=1),
                     lambda r:r['interest_over_time']['timeline_data'][0]['values'][0].update(query='wrong'),
                     lambda r:r['interest_over_time']['timeline_data'][0]['values'][0].update(value='101',extracted_value=101),
                     lambda r:r['interest_over_time']['timeline_data'][0]['values'][0].update(value='1',extracted_value=0),
                     lambda r:r['interest_over_time']['timeline_data'].reverse(),
                     lambda r:r['interest_over_time']['timeline_data'][0].update(partial_data='false')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                r=response();mutate(r)
                with self.assertRaises(SourceFailure) as exc:
                    adapter(lambda params, deadline:http_result(r))(context())
                self.assertEqual(exc.exception.code,'source_contract_unverified')
                self.assertEqual(str(exc.exception.actual_cost),'0.1')
        with self.assertRaises(SourceFailure):
            adapter(lambda params, deadline:http_result(response(dict(parameters(),start_date='2025-12-31'))))(context())

    def test_partial_query_omissions_keep_successes_and_explicit_gaps(self):
        r=response()
        r['interest_over_time']['timeline_data'][0]['values'].pop(1)
        facts=adapter(lambda p,d:http_result(r))(context()).facts
        self.assertEqual(len(facts['data']['series']),2)
        missing=facts['data']['series'][1]['points'][0]
        self.assertEqual(missing['metric']['status'],'not_observed')
        self.assertIsNone(missing['metric']['value'])
        self.assertTrue(missing['partial'])
        self.assertTrue(facts['is_partial'])
        r['interest_over_time']['timeline_data'][1]['values'].pop(1)
        facts=adapter(lambda p,d:http_result(r))(context()).facts
        self.assertEqual(len(facts['data']['series']),1)
        self.assertEqual(facts['data']['input_mapping'][1]['status'],'not_observed')
        self.assertTrue(facts['is_partial'])

    def test_credentials_and_short_tokens_removed_everywhere(self):
        r=response()
        r['search_parameters']['api_key']=CREDENTIAL
        r['search_metadata'].update(next_page_token='fixture-short-lived',json_endpoint='https://serpapi.com/search?api_key=other-secret')
        r[CREDENTIAL]='credential-as-field-name'
        r['extra']={'message':'echo '+CREDENTIAL,'nested':[{'accessToken':'shortsecret','link':'https://a.invalid/?token=shortsecret'}]}
        facts=adapter(lambda params, deadline:http_result(r))(context()).facts
        text=json.dumps(facts)
        for secret in [CREDENTIAL,'fixture-short-lived','other-secret','shortsecret']:
            self.assertNotIn(secret,text)
        self.assertEqual(facts['raw']['status'],'redacted')
        self.assertIn('/search_parameters/api_key',facts['raw']['redacted_paths'])
        clean=adapter(retain_raw=False)(context()).facts
        self.assertEqual(clean['raw']['status'],'not_retained')
        self.assertIsNone(clean['raw']['payload'])

    def test_billing_never_guessed_from_preview_or_http_status(self):
        for bill in [None,SourceBill('0.1','EUR','fixture-search','ref'),SourceBill('0.1','USD','other-unit','ref'),
                     SourceBill('NaN','USD','fixture-search','ref'),SourceBill('0.1','USD','fixture-search','')]:
            with self.subTest(bill=bill),self.assertRaises(SourceFailure) as exc:
                adapter(bill_for=lambda status,payload:bill)(context())
            self.assertIsNone(exc.exception.actual_cost)
        with self.assertRaises(SourceFailure) as exc:
            adapter(lambda p,d:http_result({'error':'quota'},429,{'content-type':'application/json','retry-after':'30'}))(context())
        self.assertEqual((str(exc.exception.actual_cost),exc.exception.retry_after),('0.1',30))

    def test_invalid_json_unknown_cost_and_exception_never_leaks_key(self):
        for body in [b'{"x":1,"x":2}',b'{"x":NaN}',b'[]',b'<html>error</html>']:
            with self.subTest(body=body),self.assertRaises(SourceFailure) as exc:
                adapter(lambda p,d:HTTPResult(200,{'content-type':'application/json'},body))(context())
            self.assertEqual(exc.exception.code,'source_response_invalid')
            self.assertIsNone(exc.exception.actual_cost)
        def broken(p,d):raise RuntimeError('request failed '+CREDENTIAL)
        with self.assertRaises(SourceFailure) as exc:adapter(broken)(context())
        self.assertNotIn(CREDENTIAL,str(exc.exception))

    def test_retry_after_seconds_date_and_unrepresentable_pause(self):
        self.assertEqual(retry_after('60',NOW),60)
        self.assertEqual(retry_after('Tue, 10 Nov 2026 12:01:00 GMT',NOW),60)
        self.assertEqual(retry_after('Tue, 10 Nov 2026 11:00:00 GMT',NOW),0)
        for value in ['86401','garbage','-1','Tue, 10 Nov 2026 12:01:00']:
            with self.subTest(value=value),self.assertRaises(SourceFailure):retry_after(value,NOW)

    def test_canceled_or_expired_before_request_is_confirmed_free(self):
        called=[]
        c=context();object.__setattr__(c,'canceled',lambda:True)
        for candidate in [c,SourceContext('job',parameters(),'0.4','USD',NOW-timedelta(seconds=1),'event',lambda:False)]:
            with self.subTest(candidate=candidate),self.assertRaises(SourceFailure) as exc:
                adapter(lambda p,d:called.append(p))(candidate)
            self.assertEqual(str(exc.exception.actual_cost),'0')
        self.assertEqual(called,[])


class CurlTransportTests(unittest.TestCase):
    def test_fixed_origin_tls_timeout_and_streaming_limits(self):
        from curl_cffi import CurlOpt
        class Fake:
            def __init__(self):self.opts={};self.closed=False
            def setopt(self,k,v):self.opts[k]=v
            def getinfo(self,k):return 200
            def close(self):self.closed=True
            def perform(self):
                self.opts[CurlOpt.HEADERFUNCTION](b'Content-Type: application/json\r\n')
                if not self.opts[CurlOpt.WRITEFUNCTION](b'x'*2048):raise RuntimeError('abort '+CREDENTIAL)
        fake=Fake()
        with self.assertRaises(SourceFailure) as exc:
            CurlSerpAPITransport(max_bytes=1024,curl_factory=lambda:fake)({'api_key':CREDENTIAL},datetime.now(UTC)+timedelta(seconds=1))
        self.assertEqual(exc.exception.code,'source_response_too_large')
        self.assertTrue(fake.closed)
        self.assertTrue(fake.opts[CurlOpt.URL].startswith('https://serpapi.com/search.json?'))
        self.assertEqual(fake.opts[CurlOpt.PROTOCOLS_STR],'https')
        self.assertEqual(fake.opts[CurlOpt.FOLLOWLOCATION],0)
        self.assertEqual(fake.opts[CurlOpt.PROXY],'')
        self.assertEqual(fake.opts[CurlOpt.SSL_VERIFYPEER],1)
        self.assertGreater(fake.opts[CurlOpt.TIMEOUT_MS],0)
        self.assertLessEqual(fake.opts[CurlOpt.TIMEOUT_MS],1000)

    def test_real_libcurl_total_timeout_on_local_drip_not_read_idle_only(self):
        from curl_cffi import Curl,CurlOpt
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b' ');self.wfile.flush();time.sleep(.03)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        class LocalCurl(Curl):
            def setopt(self,opt,value):
                if opt==CurlOpt.URL:value='http://127.0.0.1:'+str(server.server_port)+'/'
                if opt==CurlOpt.PROTOCOLS_STR:value='http'
                return super().setopt(opt,value)
        try:
            started=time.monotonic()
            with self.assertRaises(SourceFailure) as exc:
                CurlSerpAPITransport(curl_factory=LocalCurl)({},datetime.now(UTC)+timedelta(seconds=.25))
            self.assertEqual(exc.exception.code,'source_transport_uncertain')
            self.assertLess(time.monotonic()-started,1.5)
        finally:
            server.shutdown();server.server_close();worker.join(3)
            self.assertFalse(worker.is_alive())


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SerpAPIExecutorTests(unittest.TestCase):
    setUp=store_fixture.CapabilityStoreTests.setUp
    def tearDown(self):
        with self.data.connect() as db:
            db.execute('DELETE FROM sf_capability_responses WHERE tenant_id=%s',(self.tenant,))
            db.execute('DELETE FROM sf_capability_dispatches WHERE scope=%s',(self.tenant,))
            db.execute('DELETE FROM sf_capability_executor_scopes WHERE scope=%s',(self.tenant,))
        store_fixture.CapabilityStoreTests.tearDown(self)

    def test_provider_shape_through_executor_store_and_export_without_upstream(self):
        from realtime.data_capability_export import build_bundle,verify_bundle
        grants=self.grants
        grants['trends_history']['provider']='serpapi'
        grants['trends_history']['billing_unit']='fixture-search'
        source=adapter(clock=lambda:self.now)
        grants['trends_history']['validate_parameters']=source.validate_parameters
        e=CapabilityExecutor(self.data,lambda t,c:grants[c],capability='trends_history',provider='serpapi',scope=self.tenant,
                             execute=source,interval_seconds=0,response_store=capture_store(self))
        e.initialize()
        job=self.data.create(self.tenant,parameters(),'fixture-serpapi-test',grants['trends_history'],'0.5','USD')
        self.assertTrue(e.run_once())
        saved=self.data.get(self.tenant,job['id'])
        self.assertEqual(saved['status'],'partial')
        with self.data.connect() as db:
            bill=db.execute('SELECT billing_evidence FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()['billing_evidence']
        self.assertEqual(bill['reference'],'fixture-independent-bill')
        self.assertEqual(bill['currency'],'USD')
        self.assertEqual(bill['actual_cost'],'0.1')
        observation=self.data.job_data(self.tenant,job['id'],grants['trends_history'])['observation']
        self.assertEqual(observation['payload']['raw']['payload'],response())
        self.assertEqual(observation['payload']['data']['series'][0]['points'][0]['metric']['status'],'available')
        bundle=build_bundle(observation)
        verified=verify_bundle(bundle['files']['bundle.zip'],expected_payload_hash=observation['payload_hash'],
                               expected_observation_id=observation['observation_id'],expected_snapshot_hash=observation['snapshot_hash'])
        self.assertEqual(verified['observation_id'],observation['observation_id'])

    def test_confirmed_paid_error_retains_bill_and_currency_conflict_holds_reservation(self):
        grant=self.grants['trends_history']
        grant.update(provider='serpapi',billing_unit='fixture-search')
        source=adapter(lambda p,d:http_result({'error':'fixture provider failure'},429,{'content-type':'application/json','retry-after':'30'}))
        grant['validate_parameters']=source.validate_parameters
        e=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='trends_history',provider='serpapi',scope=self.tenant,
                             execute=source,interval_seconds=0,response_store=capture_store(self))
        e.initialize()
        first=self.data.create(self.tenant,parameters(),'paid-error',grant,'0.5','USD')
        self.assertTrue(e.run_once())
        saved=self.data.get(self.tenant,first['id'])
        self.assertEqual((saved['status'],saved['actual_cost']),('failed','0.1'))
        with self.data.connect() as db:
            d=db.execute('SELECT billing_evidence FROM sf_capability_dispatches WHERE job_id=%s',(first['id'],)).fetchone()
        self.assertEqual(d['billing_evidence']['reference'],'fixture-independent-bill')
        self.now+=timedelta(seconds=31)
        source._bill_for=lambda status,payload:SourceBill('0.1','EUR','fixture-search','fixture-eur-bill')
        second=self.data.create(self.tenant,parameters(),'currency-conflict',grant,'0.5','USD')
        self.assertTrue(e.run_once())
        saved=self.data.get(self.tenant,second['id'])
        self.assertEqual(saved['status'],'reconciliation_required')
        self.assertIsNone(saved['actual_cost'])
        self.assertEqual(saved['reservation_status'],'held')

    def test_bill_evidence_survives_failure_before_fact_commit(self):
        grant=self.grants['trends_history']
        grant.update(provider='serpapi',billing_unit='fixture-search')
        source=adapter(clock=lambda:self.now)
        grant['validate_parameters']=source.validate_parameters
        e=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='trends_history',provider='serpapi',scope=self.tenant,
                             execute=source,interval_seconds=0,response_store=capture_store(self))
        e.initialize()
        job=self.data.create(self.tenant,parameters(),'bill-before-commit',grant,'0.5','USD')
        with patch.object(self.data,'complete',side_effect=RuntimeError('fixture storage fault')):
            self.assertTrue(e.run_once())
        saved=self.data.get(self.tenant,job['id'])
        self.assertEqual(saved['status'],'reconciliation_required')
        self.assertIsNone(saved['actual_cost'])
        with self.data.connect() as db:
            d=db.execute('SELECT actual_cost,billing_evidence FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()
            n=db.execute('SELECT count(*) n FROM sf_capability_observations WHERE tenant_id=%s',(self.tenant,)).fetchone()['n']
        self.assertEqual(n,0)
        self.assertEqual(str(d['actual_cost']),'0.1')
        self.assertEqual(d['billing_evidence']['reference'],'fixture-independent-bill')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SerpAPIHTTPTests(unittest.TestCase):
    from tests import test_data_capability_http as _http
    request=_http.CapabilityHTTPTests.request
    json=_http.CapabilityHTTPTests.json
    submit=_http.CapabilityHTTPTests.submit
    wait=_http.CapabilityHTTPTests.wait
    def work(self):self.stop_worker.wait()
    def setUp(self):
        self._http.CapabilityHTTPTests.setUp(self)
        grant=self.grants['trends_history']
        self.source=adapter(clock=lambda:self.now)
        grant.update(provider='serpapi',billing_unit='fixture-search',validate_parameters=self.source.validate_parameters)
        self.executor=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='trends_history',provider='serpapi',scope=self.schema,
                                         execute=self.source,interval_seconds=0,poll_seconds=.01,response_store=capture_store(self))
        self.executor.initialize()
        self.runtime.worker_ready=self.executor.ready
        self.runtime.response_store=self.executor.response_store
        self.executor.start()
        limit=time.monotonic()+3
        while not self.executor.ready('', 'trends_history','serpapi') and time.monotonic()<limit:time.sleep(.01)
    def tearDown(self):
        self.assertTrue(self.executor.stop())
        self._http.CapabilityHTTPTests.tearDown(self)
    def test_customer_routes_preserve_provider_facts_and_independently_verifiable_zip(self):
        from realtime.data_capability_export import verify_bundle
        job=self.submit()
        state=self.wait(job)
        self.assertEqual(state['status'],'partial')
        self.assertEqual(state['provider'],'serpapi')
        self.assertEqual(state['actual_cost'],'0.1')
        code,data=self.json(job['data_url'])
        self.assertEqual(code,200,data)
        observed=data['observation']
        self.assertEqual(observed['payload']['raw']['payload'],response())
        code,page=self.json('/v1/data/trends/series?provider=serpapi')
        self.assertEqual(code,200,page)
        self.assertEqual(len(page['records']),2)
        code,child=self.json('/v1/data/trends/series/'+page['records'][0]['object_id']+'/points')
        self.assertEqual(code,200,child)
        self.assertEqual(len(child['records']),2)
        code,content,headers=self.request('/v1/jobs/'+job['id']+'/export?format=zip&snapshot_hash='+observed['snapshot_hash'])
        self.assertEqual(code,200,content[:100])
        manifest=verify_bundle(content,expected_payload_hash=observed['payload_hash'],expected_observation_id=observed['observation_id'],
                               expected_snapshot_hash=observed['snapshot_hash'])
        self.assertEqual(manifest['observation_id'],observed['observation_id'])
        code,_=self.json(job['data_url'],key=self.other['api_key'])
        self.assertEqual(code,404)

    def test_paid_parser_failure_has_full_response_but_no_normalized_observation(self):
        from realtime.data_capability_response_export import verify_bundle
        value=response();value['interest_over_time']['timeline_data'][0]['date']='unsupported fixture calendar'
        self.source._transport=lambda p,d:http_result(value)
        job=self.submit();state=self.wait(job)
        self.assertEqual(state['status'],'failed');self.assertEqual(state['actual_cost'],'0.1')
        self.assertEqual(state['error_code'],'source_contract_unverified')
        self.assertEqual(self.json(job['data_url'])[0],409)
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)
        code,snapshot=self.json(job['status_url']+'/response')
        self.assertEqual(code,200,snapshot)
        self.assertEqual(snapshot['payload'],value)
        self.assertEqual(snapshot['normalized_fact_status'],'not_asserted')
        for format in ['json','jsonl','csv','zip','manifest']:
            with self.subTest(format=format):
                code,content,headers=self.request(job['status_url']+'/response/export?format='+format+'&snapshot_hash='+snapshot['snapshot_hash'])
                self.assertEqual(code,200,content[:100])
                if format=='zip':
                    proof=verify_bundle(content,expected_response_id=snapshot['response_id'],expected_snapshot_hash=snapshot['snapshot_hash'])
                    self.assertEqual(proof['normalized_fact_count'],0)
        self.assertEqual(self.json(job['status_url']+'/response',key=self.other['api_key'])[0],404)
        self.assertEqual(self.json(job['status_url']+'/response?invalid=1')[0],400)
        self.assertEqual(self.json(job['status_url']+'/response',method='POST',payload={})[0],405)
        self.assertEqual(self.request(job['status_url']+'/response/export?format=bad')[0],400)
        self.assertEqual(self.request(job['status_url']+'/response/export?snapshot_hash=wrong')[0],409)
        self.grants['trends_history']['raw_response_retention_allowed']=False
        self.assertEqual(self.json(job['status_url']+'/response')[0],409)
        self.grants['trends_history']['raw_response_retention_allowed']=True
        self.assertEqual(self.json(job['status_url']+'/response')[0],200)
        self.executor.response_store.erase(str(self.account['account_id']),job['id'],reason='fixture_operator_erasure')
        self.assertEqual(self.json(job['status_url']+'/response')[0],410)

    def test_bill_unknown_preserves_response_and_disables_new_calls(self):
        self.source._bill_for=lambda status,payload:None
        job=self.submit()
        limit=time.monotonic()+3
        while time.monotonic()<limit:
            code,state=self.json(job['status_url'])
            if state['status']=='reconciliation_required':break
            time.sleep(.01)
        self.assertEqual(state['status'],'reconciliation_required')
        self.assertIsNone(state['actual_cost'])
        code,captured=self.json(job['status_url']+'/response')
        self.assertEqual(code,200,captured)
        self.assertEqual(captured['payload'],response())
        self.assertFalse(self.executor.ready(str(self.account['account_id']),'trends_history','serpapi'))

    def test_raw_rights_missing_rejects_before_acceptance_or_provider_call(self):
        called=[]
        self.source._transport=lambda p,d:called.append(p)
        self.grants['trends_history']['raw_response_retention_allowed']=False
        code,failure=self.json('/v1/data/jobs',method='POST',payload=dict(parameters(),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='no-raw-license')
        self.assertEqual(code,503,failure);self.assertEqual(called,[])
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_jobs').fetchone()['n'],0)

    def test_export_rights_rechecked_after_build_and_contract_is_discoverable(self):
        from realtime import data_capability_response_export as exports
        job=self.submit();self.wait(job)
        original=exports.build_bundle
        def revoke(snapshot):
            bundle=original(snapshot)
            self.grants['trends_history']['raw_response_retention_allowed']=False
            return bundle
        with patch.object(exports,'build_bundle',side_effect=revoke):
            code,raw,_=self.request(job['status_url']+'/response/export?format=zip')
        self.assertEqual(code,409);self.assertNotIn(b'interest_over_time',raw)
        self.grants['trends_history']['raw_response_retention_allowed']=True
        code,doc=self.json('/v1/openapi.json')
        self.assertEqual(code,200)
        self.assertEqual(doc['info']['version'],'1.9.0')
        self.assertIn('/v1/jobs/{id}/response',doc['paths'])
        self.assertIn('/v1/jobs/{id}/response/export',doc['paths'])
