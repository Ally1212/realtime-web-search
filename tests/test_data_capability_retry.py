"""Synthetic explicit failures, real isolated queue transactions/customer HTTP."""
import copy,os,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from realtime.data_capability_retry import plan,submit
from realtime.data_capability_jobs import digest
from realtime.product import ProductError
from tests.test_data_capability_facts import parameters,result
from tests import test_data_capability_http as http


def failed_fact(p,provider='fixture'):
    f=result(p,provider);g=f['data']['groups'][0];g['returned_text']=p['keywords'][0]
    f['data']['input_mapping']=[dict(input_index=i,record_id=g['id'] if i==0 else None,status='available' if i==0 else 'source_error',reason=None if i==0 else 'fixture_explicit_per_keyword_failure') for i in range(len(p['keywords']))]
    f['is_partial']=True;return f

def snapshot(p=None):
    p=p or parameters('keyword_volume');f=failed_fact(p)
    return dict(parameters=p,payload=f,observation_id='fixture-observation',payload_hash=digest(f),snapshot_hash='fixture-snapshot')

class RetryPlanTests(unittest.TestCase):
    def test_only_explicit_failed_inputs_preserve_duplicates_and_source_snapshot(self):
        s=snapshot();p=plan(s,'fixture-parent');self.assertEqual(p['parameters']['keywords'],['apple','apple']);self.assertEqual([m['parent_input_index'] for m in p['input_mapping']],[1,2]);self.assertEqual(s,snapshot())
        s['payload']['data']['input_mapping'].reverse();self.assertEqual(plan(s,'fixture-parent'),p)
    def test_missing_zero_low_volume_and_partial_success_are_not_retry_failures(self):
        for status in ['not_observed','below_threshold','unknown','not_covered','expired']:
            with self.subTest(status=status):
                s=snapshot()
                for m in s['payload']['data']['input_mapping'][1:]:m['status']=status
                p=plan(s,'parent');self.assertFalse(p['eligible']);self.assertIsNone(p['parameters'])
        s=snapshot();m=s['payload']['data']['input_mapping'][1];m.update(record_id='group-fixture',status='available',reason=None)
        p=plan(s,'parent');self.assertFalse(p['eligible']);self.assertEqual(p['excluded'][2]['reason'],'duplicate_input_has_nonfailed_copy')
    def test_returned_group_text_blocks_contradictory_failed_mapping(self):
        s=snapshot();s['payload']['data']['groups'][0]['returned_text']='apple'
        p=plan(s,'parent');self.assertFalse(p['eligible'])
        self.assertEqual(p['excluded'][1]['reason'],'returned_group_has_same_query_text')

    def test_wrong_capability_or_invalid_parent_fact_is_rejected(self):
        for cap in ['trends_history','aio_answer','ai_mode_answer']:
            with self.subTest(cap=cap),self.assertRaises(ProductError):plan(dict(parameters=parameters(cap)),'parent')
        s=snapshot();s['payload']['data']['input_mapping'][1]['reason']=None
        with self.assertRaises(ProductError):plan(s,'parent')

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class RetryHTTPTests(unittest.TestCase):
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    def work(self):self.stop_worker.wait()
    setUp=http.CapabilityHTTPTests.setUp
    tearDown=http.CapabilityHTTPTests.tearDown
    def parent(self,*,cap='keyword_volume',partial=True,key='fixture-parent'):
        p=parameters(cap);tenant=str(self.account['account_id']);g=self.grants[cap]
        job=self.data.create(tenant,p,key,g,'0.5','USD');claim=self.data.claim(cap,self.provider,lambda t,c:self.grants[c])
        f=failed_fact(p,self.provider) if partial and cap=='keyword_volume' else result(p,self.provider)
        self.data.complete(tenant,job['id'],claim['lease_token'],f,'0.1',g,event_key=job['id'])
        return job
    def preview(self,parent):
        code,p=self.json('/v1/jobs/'+parent['id']+'/retry');self.assertEqual(code,200,p);return p
    def retry(self,parent,p,*,key='retry-one',maximum='0.5',**kwargs):
        return self.json('/v1/jobs/'+parent['id']+'/retry',method='POST',payload=dict(plan_hash=p['plan_hash'],budget=dict(maximum_cost=maximum,currency='USD')),idempotency=key,**kwargs)
    def test_customer_retry_only_failed_subset_separate_facts_cost_and_fixed_parent_exports(self):
        parent=self.parent();p=self.preview(parent);self.assertEqual(p['parameters']['keywords'],['apple','apple']);self.assertTrue(p['creation_available'])
        root='/v1/jobs/'+parent['id'];code,before,_=self.request(root+'/export?format=zip');self.assertEqual(code,200)
        code,child=self.retry(parent,p);self.assertEqual(code,202,child);self.assertEqual(child['parameters']['keywords'],['apple','apple'])
        self.assertEqual(child['actual_cost'],None);self.assertEqual(child['reserved'],'0.40');self.assertEqual(child['retry']['parent_snapshot_hash'],p['parent_snapshot_hash'])
        claim=self.data.claim('keyword_volume',self.provider,lambda t,c:self.grants[c]);self.assertEqual(claim['job']['id'],child['id'])
        f=result(child['parameters'],self.provider);f['data']['input_mapping']=[dict(input_index=i,record_id='group-fixture',status='available',reason=None) for i in range(2)]
        self.data.complete(str(self.account['account_id']),child['id'],claim['lease_token'],f,'0.2',self.grants['keyword_volume'],event_key=child['id'])
        self.assertEqual(self.json('/v1/jobs/'+child['id'])[1]['actual_cost'],'0.2');self.assertEqual(self.json(root)[1]['actual_cost'],'0.1')
        self.assertEqual(self.request(root+'/export?format=zip')[1],before)
        lineage=self.json('/v1/jobs/'+child['id']+'/lineage')[1];self.assertTrue(lineage['is_retry']);self.assertEqual(lineage['input_mapping'],p['input_mapping'])
        again=self.preview(parent);self.assertFalse(again['creation_available']);self.assertEqual(again['existing_child_job_id'],child['id'])
    def test_concurrent_different_keys_one_child_and_replay_no_new_reservation(self):
        parent=self.parent();p=self.preview(parent)
        with ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(lambda k:self.retry(parent,p,key=k),['race-one','race-two']))
        self.assertEqual(sorted(c for c,_ in out),[202,409]);winner=next(v for c,v in out if c==202)
        with self.data.connect() as db:
            row=db.execute('SELECT idempotency_key FROM sf_capability_jobs WHERE id=%s',(winner['id'],)).fetchone()
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_retries WHERE tenant_id=%s',(str(self.account['account_id']),)).fetchone()['n'],1)
        self.ready=False;code,replay=self.retry(parent,p,key=row['idempotency_key']);self.assertEqual(code,202,replay);self.assertEqual(replay['id'],winner['id'])
        self.assertEqual(self.retry(parent,p,key=row['idempotency_key'],maximum='0.4')[0],409)
    def test_plan_conflict_no_data_wrong_domain_cancel_unknown_bill_and_no_executor(self):
        parent=self.parent();p=self.preview(parent);bad=dict(p,plan_hash='0'*64)
        self.assertEqual(self.retry(parent,bad)[0],409)
        self.ready=False;self.assertEqual(self.retry(parent,p)[0],503)
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_retries').fetchone()['n'],0)
        self.ready=True
        for status in ['reconciliation_required','failed','running','canceled']:
            with self.subTest(status=status):
                with self.data.connect() as db:db.execute('UPDATE sf_capability_jobs SET status=%s WHERE id=%s',(status,parent['id']))
                self.assertEqual(self.json('/v1/jobs/'+parent['id']+'/retry')[0],409)
        with self.data.connect() as db:db.execute("UPDATE sf_capability_jobs SET status='partial',actual_cost=NULL WHERE id=%s",(parent['id'],))
        self.assertEqual(self.json('/v1/jobs/'+parent['id']+'/retry')[0],409)
        other=self.parent(cap='aio_answer',partial=False,key='other-cap');self.assertEqual(self.json('/v1/jobs/'+other['id']+'/retry')[0],409)
    def test_two_layer_auth_tenant_revoke_expire_and_idempotency_collision(self):
        parent=self.parent();p=self.preview(parent);url='/v1/jobs/'+parent['id']+'/retry'
        for kw in [dict(site=False),dict(bearer=False)]:self.assertEqual(self.json(url,**kw)[0],401)
        self.assertEqual(self.json(url,key=self.other['api_key'])[0],404)
        # Same ordinary task parameters may not be attached as a retry by key.
        tenant=str(self.account['account_id']);self.data.create(tenant,p['parameters'],'already-ordinary',self.grants['keyword_volume'],'0.5','USD')
        self.assertEqual(self.retry(parent,p,key='already-ordinary')[0],409)
        code,child=self.retry(parent,p);self.assertEqual(code,202,child)
        line='/v1/jobs/'+child['id']+'/lineage';self.assertEqual(self.json(line,key=self.other['api_key'])[0],404)
        self.grants['keyword_volume']['status']='revoked';self.assertEqual(self.json(url)[0],409);self.assertEqual(self.json(line)[0],409)
    def test_lineage_insert_failure_rolls_back_child_and_reservation(self):
        parent=self.parent();p=self.preview(parent);tenant=str(self.account['account_id'])
        with patch('realtime.data_capability_retry.Jsonb',side_effect=RuntimeError('fixture transaction failure')):
            with self.assertRaises(RuntimeError):submit(self.runtime,tenant,parent['id'],dict(plan_hash=p['plan_hash'],budget=dict(maximum_cost='0.5',currency='USD')),'must-rollback')
        with self.data.connect() as db:
            self.assertIsNone(db.execute('SELECT id FROM sf_capability_jobs WHERE tenant_id=%s AND idempotency_key=%s',(tenant,'must-rollback')).fetchone())
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_retries WHERE tenant_id=%s',(tenant,)).fetchone()['n'],0)

    def test_cached_parent_alias_cannot_create_second_retry_for_same_observation(self):
        parent=self.parent();p=self.preview(parent);code,child=self.retry(parent,p);self.assertEqual(code,202,child)
        tenant=str(self.account['account_id']);g=self.grants['keyword_volume']
        alias=self.data.create(tenant,parameters('keyword_volume'),'parent-cache-alias',g,'0.5','USD')
        # Claim order has an earlier queued child; do not execute or bill it.
        with self.data.connect() as db:db.execute("UPDATE sf_capability_jobs SET created_at=created_at-interval '1 minute' WHERE id=%s",(alias['id'],))
        claim=self.data.claim('keyword_volume',self.provider,lambda t,c:self.grants[c]);self.assertEqual(claim['job']['id'],alias['id'])
        self.data.reuse(tenant,alias['id'],claim['lease_token'],p['parent_observation_id'],'0',g)
        alias_plan=self.preview(alias);self.assertFalse(alias_plan['creation_available']);self.assertEqual(alias_plan['existing_child_job_id'],child['id'])
        self.assertEqual(self.retry(alias,alias_plan,key='must-not-duplicate-cache')[0],409)
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_retries WHERE tenant_id=%s',(tenant,)).fetchone()['n'],1)
