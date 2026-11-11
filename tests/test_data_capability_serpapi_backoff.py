"""Synthetic SerpApi shapes, real HTTP/queue/bills/journal; no supplier calls."""
import copy,json,os,time,unittest
from datetime import timedelta
from realtime.data_capability_backoff import RetryPolicy
from realtime.data_capability_executor import CapabilityExecutor
from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_serpapi import SerpAPITrendsAdapter,SourceBill,HTTPResult,retry_error_only
from realtime.data_capability_serpapi_ai import SerpAPIAIModeAdapter
from realtime.data_capability_serpapi_aio import SerpAPIAIOAdapter
from realtime.data_capability_export import verify_bundle
from realtime.data_capability_response_export import verify_bundle as verify_response
from tests import test_data_capability_http as http
from tests import test_data_capability_serpapi as trends
from tests import test_data_capability_serpapi_ai as ai
from tests import test_data_capability_serpapi_aio as aio
from tests.test_data_capability_facts import parameters


def error(status=503,delay=0,**extra):return trends.http_result(dict(error='fixture transient',**extra),status,{'content-type':'application/json','retry-after':str(delay)})


class NativeRetryFixture:
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    def work(self):self.stop_worker.wait()
    def setUp(self):
        http.CapabilityHTTPTests.setUp(self);self.policy=RetryPolicy(4,1,4,30);self.tick=0.;self.calls=[];self.waited=[];self.replies=[];self.costs=[];self.on_sleep=None
        def sleep(n):
            self.now+=timedelta(seconds=n);self.tick+=n;self.waited.append(n)
            if self.on_sleep:self.on_sleep()
        def transport(params,deadline):
            self.calls.append(dict(params=params,deadline=deadline));reply=self.replies[len(self.calls)-1]
            if isinstance(reply,Exception):raise reply
            if isinstance(reply,HTTPResult):return reply
            value=copy.deepcopy(reply)
            if value.get('search_metadata',{}).get('status')=='Success':
                value['search_metadata']['created_at']=self.now.strftime('%Y-%m-%d %H:%M:%S UTC');value['search_metadata']['id']+='-'+str(len(self.calls))
            return trends.http_result(value)
        def invoice(*args):
            n=len(self.calls)-1;cost=self.costs[n] if n<len(self.costs) else '0.01'
            return None if cost is None else SourceBill(cost,'USD','fixture-search','fixture-invoice-'+str(n+1))
        source={'trends_history':SerpAPITrendsAdapter,'ai_mode_answer':SerpAPIAIModeAdapter,'aio_answer':SerpAPIAIOAdapter}[self.cap]
        self.source=source(enabled=True,credential=trends.CREDENTIAL,retain_raw=True,billing_unit='fixture-search',call_cost_ceiling='0.05',retry_policy=self.policy,clock=lambda:self.now,transport=transport,bill_for=invoice,sleep=sleep,monotonic=lambda:self.tick)
        self.grants[self.cap].update(provider='serpapi',billing_unit='fixture-search',validate_parameters=self.source.validate_parameters,raw_response_retention_allowed=True,source_retry_policy=self.policy.public())
        self.spool=ResponseStore(self.data,lambda t,c:self.grants[c]);self.spool.initialize()
        self.executor=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability=self.cap,provider='serpapi',scope=self.schema,execute=self.source,interval_seconds=0,poll_seconds=.01,response_store=self.spool)
        self.executor.initialize();self.runtime.worker_ready=self.executor.ready;self.runtime.response_store=self.spool;self.executor.start()
        end=time.monotonic()+3
        while not self.executor.ready(str(self.account['account_id']),self.cap,'serpapi') and time.monotonic()<end:time.sleep(.01)
    def tearDown(self):self.assertTrue(self.executor.stop());http.CapabilityHTTPTests.tearDown(self)
    def submit(self,**kw):return http.CapabilityHTTPTests.submit(self,self.cap,**kw)
    def wait(self,j):
        end=time.monotonic()+5
        while time.monotonic()<end:
            s=self.data.get(str(self.account['account_id']),j['id'])
            if s['status'] in ('succeeded','partial','failed','canceled','reconciliation_required'):
                code,s=self.json(j['status_url']);self.assertEqual(code,200,s);return s
            time.sleep(.02)
        self.fail('isolated source did not finish')
    def proof(self,j,count):
        code,raw=self.json(j['status_url']+'/response');self.assertEqual(code,200,raw);self.assertEqual(raw['call_count'],count)
        code,binary,_=self.request(j['status_url']+'/response/export?format=zip');self.assertEqual(code,200);verify_response(binary,expected_response_id=raw['response_id'],expected_snapshot_hash=raw['snapshot_hash'])
        text=json.dumps(raw);self.assertNotIn(trends.CREDENTIAL,text);self.assertNotIn(aio.TOKEN,text)
        p=self.json(j['status_url']+'/progress')[1];self.assertEqual(len(p['confirmed_call_charges']),count);self.assertEqual(p['source_retry_policy'],self.policy.public())
        return raw,p


class RetryClassificationTests(unittest.TestCase):
    def test_no_async_or_ambiguous_error_or_useful_data_replay(self):
        self.assertTrue(retry_error_only({'error':'fixture failure'},False))
        self.assertTrue(retry_error_only({'error':'fixture failure','search_metadata':{'status':'Error'}},False))
        for value in [{'error':''},{'error':False},{'error':{}},{'error':'x','search_metadata':{'status':'Processing'}},{'error':'x','search_metadata':{'id':'pending'}},{'error':'x','search_parameters':{'q':'foreign'}},{'error':'x','ai_overview':{}},{'error':'x','interest_over_time':{}},{'error':'x','text_blocks':[]}]:
            with self.subTest(value=value):self.assertFalse(retry_error_only(value,False))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class TrendsRetryTests(NativeRetryFixture,unittest.TestCase):
    cap='trends_history'
    def success(self):return trends.response()
    def test_error_only_503_429_then_success_exact_data_bills_journal_and_exports(self):
        self.replies=[error(),error(429,3),self.success()];self.costs=['0.01','0.02','0.03'];j=self.submit();s=self.wait(j)
        self.assertEqual(s['status'],'partial' if self.cap=='trends_history' else 'succeeded');self.assertEqual(s['actual_cost'],'0.06');raw,p=self.proof(j,3)
        self.assertEqual([x['phase'] for x in p['source_call_attempts']],['query','query_retry_2','query_retry_3']);self.assertGreaterEqual(sum(self.waited),4)
        data=self.json(j['data_url'])[1]['observation'];self.assertEqual(data['payload']['raw']['payload'],raw['payload']['calls'][-1]['payload'])
        code,binary,_=self.request(j['status_url']+'/export?format=zip');self.assertEqual(code,200);verify_bundle(binary,expected_observation_id=data['observation_id'],expected_payload_hash=data['payload_hash'],expected_snapshot_hash=data['snapshot_hash'])
        self.assertEqual(self.json(j['status_url'],key=self.other['api_key'])[0],404)
    def test_mixed_success_error_and_wrong_echo_do_not_replay(self):
        for mode in ['mixed','echo','metadata']:
            with self.subTest(mode=mode):
                self.calls.clear();r=dict(self.success(),error='not safe to repeat') if mode=='mixed' else dict(error='fixture',search_parameters={'engine':'wrong'}) if mode=='echo' else dict(error='fixture',search_metadata={'status':'Success'})
                self.replies=[trends.http_result(r,503)];j=self.submit(idempotency=mode);s=self.wait(j);self.assertEqual(s['status'],'failed');self.assertEqual(len(self.calls),1);self.assertEqual(s['actual_cost'],'0.01');self.now+=timedelta(seconds=10)
    def test_unknown_retry_bill_keeps_prior_receipt_and_blocks_source(self):
        self.replies=[error(),error()];self.costs=['0.01',None];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'reconciliation_required');self.assertIsNone(s['actual_cost']);self.assertEqual(s['reservation_status'],'held');self.assertEqual(len(self.calls),2)
        p=self.json(j['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.01');self.assertEqual(p['source_call_attempts'][-1]['bill_status'],'unconfirmed');self.assertFalse(self.executor.run_once())
    def test_count_and_remaining_budget_preserve_shared_cooldown(self):
        for mode in ['count','budget']:
            with self.subTest(mode=mode):
                self.calls.clear();self.source.retry_policy=self.policy=RetryPolicy(3,1,4,30);self.grants[self.cap]['source_retry_policy']=self.policy.public()
                self.grants[self.cap]['maximum_cost']='0.12' if mode=='budget' else '0.40';self.replies=[error()]*3;self.costs=['0.04']*3 if mode=='budget' else ['0.01']*3
                j=self.submit(idempotency=mode);s=self.wait(j);self.assertEqual(s['status'],'failed');self.assertEqual(len(self.calls),2 if mode=='budget' else 3)
                self.assertEqual(s['actual_cost'],'0.08' if mode=='budget' else '0.03')
                with self.data.connect() as db:next_at=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()['next_at']
                self.assertGreaterEqual(next_at,self.now+timedelta(seconds=2 if mode=='budget' else 4));self.now+=timedelta(seconds=10)

    def test_cancel_while_waiting_known_charge_and_no_second_entry(self):
        def cancel():
            if not hasattr(self,'canceled'):
                self.canceled=True
                with self.data.connect() as db:jid=db.execute("SELECT id FROM sf_capability_jobs WHERE status='running'").fetchone()['id']
                self.data.cancel(str(self.account['account_id']),str(jid))
        self.on_sleep=cancel;self.replies=[error()];j=self.submit();s=self.wait(j);self.assertEqual((s['status'],s['actual_cost']),('canceled','0.01'));self.assertEqual(len(self.calls),1)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AIModeRetryTests(TrendsRetryTests):
    cap='ai_mode_answer'
    def success(self):return ai.response()


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AIORetryTests(NativeRetryFixture,unittest.TestCase):
    cap='aio_answer'
    def test_each_failed_leg_only_and_four_distinct_confirmed_phases(self):
        self.replies=[error(),aio.initial(inline=True),error(429),aio.expansion()];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'succeeded');self.assertEqual(s['actual_cost'],'0.04');raw,p=self.proof(j,4)
        self.assertEqual([x['phase'] for x in p['source_call_attempts']],['search','search_retry_2','expand','expand_retry_2'])
        self.assertEqual([c['params']['engine'] for c in self.calls],['google','google','google_ai_overview','google_ai_overview'])
        a=self.json(j['data_url'])[1]['observation']['payload']['data']['answer'];self.assertEqual(a['state'],'complete');self.assertEqual(a['expansion']['state'],'complete')
    def test_expansion_retry_does_not_repeat_successful_search(self):
        self.replies=[aio.initial(inline=True),error(),error(),aio.expansion()];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'succeeded');self.proof(j,4)
        self.assertEqual(sum(c['params']['engine']=='google' for c in self.calls),1)
    def test_native_token_expiry_preserves_inline_answer_and_shared_retry_after(self):
        self.replies=[aio.initial(inline=True),error(429,60)];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'partial');self.assertEqual(s['actual_cost'],'0.02');self.proof(j,2)
        a=self.json(j['data_url'])[1]['observation']['payload']['data']['answer'];self.assertTrue(a['answer_text']);self.assertEqual(a['expansion']['state'],'failed')
        with self.data.connect() as db:next_at=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()['next_at']
        self.assertGreaterEqual(next_at,self.now+timedelta(seconds=60));self.assertEqual(len(self.calls),2)
    def test_partial_answer_success_is_not_a_retry_reason(self):
        r=aio.expansion();r['ai_overview']['page_token']='fixture-more-continuation';self.replies=[aio.initial(),r];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'partial');self.assertEqual(len(self.calls),2)
        a=self.json(j['data_url'])[1]['observation']['payload']['data']['answer'];self.assertTrue(a['answer_text']);self.assertEqual(a['expansion']['state'],'partial')
    def test_unknown_expansion_retry_fee_is_quarantined_without_resending_search(self):
        self.replies=[aio.initial(inline=True),error(),error()];self.costs=['0.01','0.01',None];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'reconciliation_required');self.assertIsNone(s['actual_cost']);self.assertEqual(len(self.calls),3);self.assertEqual(sum(c['params']['engine']=='google' for c in self.calls),1)
        p=self.json(j['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.02');self.assertEqual(len(p['confirmed_call_charges']),2)
    def test_job_wide_count_cap_preserves_partial_instead_of_starting_expansion(self):
        self.source.retry_policy=self.policy=RetryPolicy(2,1,2,30);self.grants[self.cap]['source_retry_policy']=self.policy.public()
        self.replies=[error(),aio.initial(inline=True)];j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'partial');self.assertEqual(len(self.calls),2);self.proof(j,2)
        a=self.json(j['data_url'])[1]['observation']['payload']['data']['answer'];self.assertTrue(a['answer_text']);self.assertEqual(a['source_fields']['source_error_code'],'source_call_limit')

    def test_initial_search_retry_gate_changes_keep_prior_confirmed_fee(self):
        def change_policy():
            self.grants[self.cap]['source_retry_policy']=RetryPolicy(3).public()
        self.on_sleep=change_policy;self.replies=[error()];j=self.submit();s=self.wait(j)
        self.assertEqual((s['status'],s['actual_cost']),('failed','0.01'));self.assertEqual(len(self.calls),1)
        self.assertEqual(self.json(j['status_url']+'/progress')[1]['confirmed_call_cost'],'0.01')
