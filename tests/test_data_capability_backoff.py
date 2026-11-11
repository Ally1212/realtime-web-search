"""Synthetic confirmed invoices; real queue/HTTP/journal for bounded source retries."""
import copy,os,unittest,time
from datetime import datetime,timedelta,timezone
from realtime.data_capability_backoff import RetryPolicy,RetryWindow
from realtime.data_capability_executor import SourceFailure
from realtime.data_capability_serpapi import SourceBill
from realtime.data_capability_contract import admit_request
from realtime.product import ProductError
from realtime.data_capability_jobs import amount
from tests import test_data_capability_google_ads as ads


class BackoffPolicyTests(unittest.TestCase):
    def test_strict_policy_and_retry_after_is_never_capped_below_source(self):
        p=RetryPolicy(4,2,5,120)
        self.assertEqual([p.delay(i) for i in (1,2,3)],[2,4,5]);self.assertEqual(p.delay(3,70),70)
        self.assertEqual(RetryPolicy.parse(p.public()),p)
        for changes in [dict(maximum_attempts=True),dict(maximum_attempts=9),dict(initial_delay_seconds=0),dict(initial_delay_seconds=31),dict(maximum_window_seconds=86401),dict(maximum_delay_seconds=1.0)]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):RetryPolicy(**changes)
        for value in [None,{},dict(p.public(),unknown=1)]:
            with self.subTest(value=value),self.assertRaises(ValueError):RetryPolicy.parse(value)
    def test_window_cancellation_authorization_and_monotonic_bound(self):
        now=[datetime(2026,11,10,tzinfo=timezone.utc)];tick=[0.];calls=[]
        def sleep(n):now[0]+=timedelta(seconds=n);tick[0]+=n
        w=RetryWindow(RetryPolicy(3,1,2,4),lambda:now[0],now[0]+timedelta(seconds=10),sleep=sleep,monotonic=lambda:tick[0])
        self.assertTrue(w.wait(1,lambda:False,lambda:calls.append(1)));self.assertTrue(calls)
        self.assertFalse(w.wait(3,lambda:False,lambda:None));self.assertFalse(w.wait(1,lambda:True,lambda:None))
        def revoked():raise ValueError('revoked')
        with self.assertRaises(ValueError):w.wait(1,lambda:False,revoked)
        # Backwards wall time cannot enlarge the monotonic retry window.
        def rollback(n):now[0]-=timedelta(seconds=n);tick[0]+=n
        w=RetryWindow(RetryPolicy(3,1,2,2),lambda:now[0],now[0]+timedelta(seconds=10),sleep=rollback,monotonic=lambda:tick[0])
        self.assertFalse(w.wait(1,lambda:False,lambda:None));self.assertLessEqual(tick[0],3.1)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class BackoffHTTPTests(unittest.TestCase):
    setUp=ads.GoogleAdsHTTPTests.setUp
    tearDown=ads.GoogleAdsHTTPTests.tearDown
    work=ads.GoogleAdsHTTPTests.work
    submit=ads.GoogleAdsHTTPTests.submit
    def wait(self,job):
        end=time.monotonic()+5
        while time.monotonic()<end:
            state=self.data.get(str(self.account['account_id']),job['id'])
            if state['status'] in ('partial','succeeded','failed','canceled','reconciliation_required'):
                code,value=self.json(job['status_url']);self.assertEqual(code,200,value);return value
            time.sleep(.02)
        self.fail('fixture executor did not finish')
    request=ads.GoogleAdsHTTPTests.request
    json=ads.GoogleAdsHTTPTests.json

    def configure(self,replies,costs,policy=None,on_sleep=None):
        policy=policy or RetryPolicy(3,1,8,30);self.source.retry_policy=policy;self.grants['keyword_volume']['source_retry_policy']=policy.public()
        self.tick=0.;self.waited=[];self.source._monotonic=lambda:self.tick
        def sleep(n):
            self.tick+=n;self.now+=timedelta(seconds=n);self.waited.append(n)
            if on_sleep:on_sleep()
        self.source._sleep=sleep
        sequence=iter(replies);invoices=iter(costs)
        def transport(body,deadline):
            self.calls.append(copy.deepcopy(body));r=next(sequence)
            if isinstance(r,Exception):raise r
            return r
        self.source._transport=transport
        def bill(*args):
            cost=next(invoices)
            return None if cost is None else SourceBill(cost,'USD','fixture-call','fixture-invoice-'+str(len(self.calls)))
        self.source._bill=bill

    def test_paid_503_429_then_success_preserves_each_receipt_raw_phase_and_total(self):
        self.configure([ads.reply({'error':{'code':503}},503),ads.reply({'error':{'code':429}},429,**{'retry-after':'3'}),ads.reply()],['0.05','0.07','0.1'])
        code,preview=self.json('/v1/data/jobs/preview',method='POST',payload=ads.parameters('keyword_volume'));self.assertEqual(code,200);self.assertEqual(preview['source_retry_policy'],self.source.retry_policy.public())
        j=self.submit();state=self.wait(j);self.assertEqual((state['status'],state['actual_cost']),('partial','0.22'));self.assertEqual(len(self.calls),3);self.assertGreaterEqual(sum(self.waited),4)
        p=self.json(j['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.22');self.assertEqual([v['phase'] for v in p['source_call_attempts']],['metrics','metrics_retry_2','metrics_retry_3']);self.assertEqual(len(p['confirmed_call_charges']),3)
        self.assertEqual(p['source_retry_policy'],self.source.retry_policy.public())
        raw=self.json(j['status_url']+'/response')[1];self.assertEqual(raw['call_count'],3)
        facts=self.json(j['data_url'])[1]['observation']['payload'];self.assertEqual(len(facts['data']['groups']),1);self.assertNotIn('error',facts['data'])
        for fmt in ['json','jsonl','csv','zip','manifest']:self.assertEqual(self.request(j['status_url']+'/export?format='+fmt)[0],200)
        self.assertEqual(self.json(j['status_url'],key=self.other['api_key'])[0],404)

    def test_attempt_cap_budget_cap_and_retry_after_window(self):
        for label,policy,cost,delay,expected in [('attempts',RetryPolicy(3,1,2,30),'0.01',0,3),('budget',RetryPolicy(3,1,2,30),'0.3',0,1),('remaining',RetryPolicy(3,1,2,30),'0.14',0,2),('window',RetryPolicy(3,1,2,2),'0.01',10,1)]:
            with self.subTest(label=label):
                self.calls.clear();self.configure([ads.reply({'error':{}},503,**{'retry-after':str(delay)})]*3,[cost]*3,policy)
                j=self.submit(idempotency=label);s=self.wait(j);self.assertEqual(s['status'],'failed');self.assertEqual(len(self.calls),expected);self.assertEqual(s['actual_cost'],str(amount(cost)*expected))
                # Shared durable cooldown, not just an in-process sleep.
                with self.data.connect() as db:r=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()
                expected_delay=10 if label=='window' else 2 if label in ('attempts','remaining') else 1
                self.assertGreaterEqual(r['next_at'],self.now+timedelta(seconds=expected_delay))
                self.now+=timedelta(seconds=20)

    def test_unknown_later_bill_or_transport_quarantines_without_another_call(self):
        for mode in ['bill','transport']:
            with self.subTest(mode=mode):
                self.calls.clear();self.configure([ads.reply({'error':{}},503),ads.reply({'error':{}},503) if mode=='bill' else RuntimeError('fixture-secret')],['0.02',None])
                j=self.submit(idempotency=mode);s=self.wait(j);self.assertEqual(s['status'],'reconciliation_required');self.assertIsNone(s['actual_cost']);self.assertEqual(s['reservation_status'],'held');self.assertEqual(len(self.calls),2)
                p=self.json(j['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.02');self.assertEqual(p['source_call_attempts'][-1]['bill_status'],'unconfirmed')
                self.assertFalse(self.executor.run_once());self.data.reconcile(str(self.account['account_id']),j['id'],'failed','0.03',reason='fixture independent bill only');self.now+=timedelta(seconds=20)

    def test_mixed_data_permanent_error_and_invalid_facts_never_replayed(self):
        for label,reply in [('mixed',ads.reply(dict(ads.response(),error={}),503)),('permanent',ads.reply({'error':{}},400)),('partial',ads.reply()),('invalid',ads.reply({'results':'bad'}))]:
            with self.subTest(label=label):
                self.calls.clear();self.configure([reply],['0.1']);j=self.submit(idempotency=label);s=self.wait(j);self.assertEqual(len(self.calls),1);self.assertEqual(s['actual_cost'],'0.1');self.now+=timedelta(seconds=10)

    def test_cancel_or_revoke_policy_while_waiting_keeps_prior_bill_no_new_call(self):
        for mode in ['cancel','policy']:
            with self.subTest(mode=mode):
                self.calls.clear();done=[]
                def stop():
                    if done:return
                    done.append(1)
                    if mode=='policy':self.grants['keyword_volume']['source_retry_policy']=RetryPolicy(2).public()
                    else:
                        with self.data.connect() as db:jid=db.execute("SELECT id FROM sf_capability_jobs WHERE status='running'").fetchone()['id']
                        self.data.cancel(str(self.account['account_id']),str(jid))
                self.configure([ads.reply({'error':{}},503)],['0.05'],on_sleep=stop)
                j=self.submit(idempotency=mode);s=self.wait(j);self.assertEqual(s['status'],'canceled' if mode=='cancel' else 'failed');self.assertEqual(s['actual_cost'],'0.05');self.assertEqual(len(self.calls),1);self.now+=timedelta(seconds=20)

    def test_policy_not_implicitly_enabled_and_invalid_customer_field_rejected(self):
        self.source.retry_policy=RetryPolicy(3)
        p=dict(ads.parameters('keyword_volume'),budget=dict(maximum_cost='0.5',currency='USD'))
        self.assertEqual(self.json('/v1/data/jobs',method='POST',payload=p,idempotency='not-approved')[0],503)
        # A legacy already-queued job also fails closed without a source call.
        j=self.data.create(str(self.account['account_id']),ads.parameters('keyword_volume'),'legacy-queued',self.grants['keyword_volume'],'0.5','USD');j['status_url']='/v1/jobs/'+j['id']
        s=self.wait(j);self.assertEqual(s['status'],'failed');self.assertEqual(s['actual_cost'],'0');self.assertFalse(self.calls)
        p=dict(ads.parameters('keyword_volume'),source_retry_policy=RetryPolicy(3).public(),budget=dict(maximum_cost='0.5',currency='USD'))
        self.assertEqual(self.json('/v1/data/jobs',method='POST',payload=p,idempotency='customer-policy')[0],400)
        self.grants['keyword_volume']['source_retry_policy']=dict(maximum_attempts=True)
        self.assertEqual(self.json('/v1/data/jobs/preview',method='POST',payload=ads.parameters('keyword_volume'))[0],409)

    def test_worker_stop_during_wait_keeps_confirmed_charge_and_never_calls_again(self):
        self.configure([ads.reply({'error':{}},503)],['0.05'],on_sleep=self.executor._stop.set)
        j=self.submit();s=self.wait(j);self.assertEqual((s['status'],s['actual_cost']),('failed','0.05'));self.assertEqual(len(self.calls),1)
        self.assertFalse(self.executor.ready(str(self.account['account_id']),'keyword_volume','google_ads'))

    def test_restart_after_entered_retry_does_not_replay_uncertain_job(self):
        self.configure([ads.reply({'error':{}},503),RuntimeError('simulated interrupted retry')],['0.02'])
        j=self.submit();s=self.wait(j);self.assertEqual(s['status'],'reconciliation_required');self.assertEqual(len(self.calls),2)
        self.assertTrue(self.executor.stop());self.executor.start()
        self.assertFalse(self.executor.run_once());self.assertEqual(len(self.calls),2)
        p=self.json(j['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.02');self.assertEqual(p['source_call_attempts'][-1]['bill_status'],'unconfirmed')

    def test_queued_policy_is_frozen_and_changed_grant_blocks_before_transport(self):
        policy=RetryPolicy(3);self.configure([ads.reply()],['0.1'],policy)
        with self.data.connect() as db:db.execute('UPDATE sf_capability_executor_scopes SET next_at=%s WHERE scope=%s',(self.now+timedelta(seconds=60),self.schema))
        j=self.submit();self.assertEqual(j['source_retry_policy'],policy.public())
        self.grants['keyword_volume']['source_retry_policy']=RetryPolicy(2).public();self.now+=timedelta(seconds=60)
        s=self.wait(j);self.assertEqual((s['status'],s['actual_cost']),('failed','0'));self.assertEqual(s['source_retry_policy'],policy.public());self.assertFalse(self.calls)

    def test_transport_deadline_is_bounded_by_retry_window_and_late_success_not_replayed(self):
        self.configure([ads.reply()],['0.1'],RetryPolicy(3,1,2,2));start=self.now;deadlines=[]
        def transport(body,deadline):
            self.calls.append(copy.deepcopy(body));deadlines.append(deadline);self.now+=timedelta(seconds=3);self.tick+=3;return ads.reply()
        self.source._transport=transport;j=self.submit();s=self.wait(j)
        self.assertEqual((s['status'],s['actual_cost']),('failed','0.1'));self.assertEqual(len(self.calls),1);self.assertLessEqual(deadlines[0],start+timedelta(seconds=2))
