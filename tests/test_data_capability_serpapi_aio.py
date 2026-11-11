"""Provider-shaped synthetic AIO; NEVER real Google/SerpApi calls or gold data."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
import os
import time
import unittest

from realtime.data_capability_executor import CapabilityExecutor,SourceFailure,SourceCallDeferred
from realtime.data_capability_serpapi_aio import SerpAPIAIOAdapter,request_parameters
from realtime.data_capability_serpapi import SourceBill
from realtime.data_capability_responses import ResponseStore
from realtime.product import ProductError
from tests.test_data_capability_serpapi import NOW,CREDENTIAL,context,http_result
from tests.test_data_capability_facts import parameters
from tests import test_data_capability_serpapi_ai as ai
from tests import test_data_capability_http as http

TOKEN='fixture-aio-short-lived-token-not-a-real-secret'


def initial(p=None,*,token=True,inline=False):
    p=p or parameters('aio_answer')
    value=dict(search_metadata=dict(id='fixture-google-search',status='Success',created_at='2026-11-10 12:00:00 UTC'),
        search_parameters=request_parameters(p),organic_results=[dict(link='https://example.org/serp-not-citation',snippet='普通SERP不冒充回答')])
    if token:value['ai_overview']=dict(page_token=TOKEN,serpapi_link='https://evil.invalid/search?page_token='+TOKEN)
    if inline or not token:value.setdefault('ai_overview',{}).update(answer())
    return value


def answer():
    r=ai.response()
    return dict(text_blocks=r['text_blocks']+[dict(type='expandable',title='可展开段落',subtitle='副标题',text_blocks=[
        dict(type='paragraph',snippet='无需额外访问的完整内容',reference_indexes=[9])]),
        dict(type='comparison',product_labels=['A','B'],comparison=[dict(feature='项目',values=['0','1'])]),
        dict(type='top_stories',top_stories=[dict(title='报道标题',link='https://example.org/ordinary-story',source='合成源',date='today')])],
        references=r['references'],header_images=[dict(image='https://example.org/image.png',source='https://example.org/image-source')])


def expansion():
    return dict(search_metadata=dict(id='fixture-aio-expansion',status='Success',created_at='2026-11-10 12:00:00 UTC'),
        search_parameters=dict(engine='google_ai_overview',page_token=TOKEN),ai_overview=answer())


class Sink:
    def __init__(self):self.calls=[]
    def checkpoint(self,phase,**kwargs):self.calls.append(dict(phase=phase,**copy.deepcopy(kwargs)))


class AIOContractTests(unittest.TestCase):
    def setup_source(self,values=None,**kwargs):
        self.now=NOW;self.sent=[];self.entered=[];self.bills=[];self.delays=[];self.sink=Sink()
        values=values if values is not None else [initial(),expansion()]
        def transport(params,deadline):
            self.sent.append(copy.deepcopy(params))
            item=values[len(self.sent)-1]
            if isinstance(item,Exception):raise item
            return item if hasattr(item,'body') else http_result(item)
        def before(phase,ceiling):self.entered.append((phase,ceiling))
        def confirmed(phase,bill):
            self.assertEqual(len(self.sink.calls),len(self.bills)+1) # Persist body before resolving each invoice.
            self.bills.append(dict(phase=phase,**bill))
        options=dict(credential=CREDENTIAL,enabled=True,retain_raw=True,call_cost_ceiling='0.15',billing_unit='fixture-search',
            clock=lambda:self.now,sleep=lambda seconds:setattr(self,'now',self.now+timedelta(seconds=seconds)),transport=transport,
            bill_for=lambda phase,status,value:SourceBill('0.1','USD','fixture-search','fixture-invoice-'+phase))
        options.update(kwargs);source=SerpAPIAIOAdapter(**options)
        ctx=replace(context(parameters('aio_answer')),response=self.sink,before_call=before,confirm_bill=confirmed,defer_calls=self.delays.append)
        return source,ctx

    def test_default_disabled_scope_matrix_and_mandatory_journal_bill_hooks(self):
        s=SerpAPIAIOAdapter()
        with self.assertRaises(SourceFailure) as e:s(context(parameters('aio_answer')))
        self.assertEqual(str(e.exception.actual_cost),'0')
        with self.assertRaises(ProductError):s.validate_parameters(parameters('aio_answer'))
        with self.assertRaises(ValueError):SerpAPIAIOAdapter(enabled=True)
        for country,gl in [('US','us'),('GB','uk')]:
            for device in ['desktop','mobile']:
                with self.subTest(country=country,device=device):
                    p=dict(parameters('aio_answer'),country=country,device=device)
                    self.assertEqual(request_parameters(p)['gl'],gl);self.assertEqual(request_parameters(p)['device'],device)
        for change in [dict(capability='ai_mode_answer'),dict(language='zh'),dict(country='ZZ')]:
            with self.subTest(change=change),self.assertRaises(ProductError):request_parameters(dict(parameters('aio_answer'),**change))
        source,ctx=self.setup_source()
        for change in [dict(response=None),dict(confirm_bill=None),dict(before_call=None),dict(defer_calls=None),dict(maximum_cost='0.2')]:
            with self.subTest(hook=list(change)),self.assertRaises(SourceFailure):source(replace(ctx,**change))
        self.assertEqual(self.sent,[]);self.assertNotIn(CREDENTIAL,repr(source))

    def test_native_two_call_flow_full_structure_explicit_citations_and_no_token_leak(self):
        source,ctx=self.setup_source();out=source(ctx);f=out.facts;a=f['data']['answer']
        self.assertEqual(str(out.actual_cost),'0.2');self.assertEqual([p['engine'] for p in self.sent],['google','google_ai_overview'])
        self.assertEqual(self.sent[1]['page_token'],TOKEN)
        self.assertNotIn('evil.invalid',json.dumps(self.sent));self.assertEqual([v[0] for v in self.entered],['search','expand'])
        self.assertEqual((a['channel'],a['state'],a['expansion']['state']),('aio','complete','complete'))
        self.assertEqual([b['structure'] for b in a['blocks']],answer()['text_blocks'])
        self.assertIn('可展开段落\n副标题\n无需额外访问的完整内容',a['answer_text']);self.assertIn('A\tB\n项目\t0\t1',a['answer_text'])
        self.assertIn('报道标题',a['answer_text']);self.assertNotIn('普通SERP',a['answer_text'])
        self.assertEqual(a['citations'][0]['id'],'reference-9');self.assertIn('block-4',a['citations'][0]['block_ids'])
        self.assertTrue(a['citations'][0]['source_ref'].startswith('/ai_overview/references'))
        self.assertNotIn('https://example.org/serp-not-citation',[v['url'] for v in a['citations']+a['links']])
        self.assertIsNone(a['model']);self.assertIsNone(f['source']['source_observed_at'])
        self.assertEqual(a['source_fields']['initial_source_request_id'],'fixture-google-search')
        self.assertNotIn(TOKEN,json.dumps(f)+json.dumps(self.sink.calls,default=str)+json.dumps(self.bills))
        self.assertEqual(f['raw']['reason'],'native_responses_in_independent_job_journal')

    def test_inline_answer_no_extra_call_absence_source_error_and_refusal_not_guessed(self):
        for mode in ['inline','absent','error','refusal','empty']:
            with self.subTest(mode=mode):
                r=initial(token=False)
                if mode=='absent':r.pop('ai_overview')
                if mode=='error':r['ai_overview']=dict(error="Can't generate an AI overview right now")
                if mode=='refusal':r['ai_overview']=dict(text_blocks=[dict(type='paragraph',snippet='I cannot answer that.')],references=[])
                if mode=='empty':r['ai_overview']=dict(text_blocks=[],references=[])
                source,ctx=self.setup_source([r]);out=source(ctx);self.assertEqual(len(self.sent),1)
                self.assertEqual(out.facts['data']['answer']['state'],{'absent':'not_observed','empty':'not_observed','error':'source_error'}.get(mode,'complete'))
        for malformed in [{},dict(text_blocks=[],products=[dict(title='cannot discard this')]),dict(text_blocks=None)]:
            with self.subTest(malformed=malformed):
                r=initial(token=False);r['ai_overview']=malformed;source,ctx=self.setup_source([r])
                with self.assertRaises(SourceFailure) as e:source(ctx)
                self.assertEqual(str(e.exception.actual_cost),'0.1')

    def test_expansion_expiry_and_known_error_preserve_first_partial_blocks_or_empty_state(self):
        for inline in [False,True]:
            source,ctx=self.setup_source([initial(inline=inline),http_result(dict(error='synthetic 429'),status=429,headers={'content-type':'application/json','retry-after':'70'})])
            out=source(ctx);a=out.facts['data']['answer'];self.assertTrue(out.facts['is_partial'])
            self.assertEqual(a['state'],'partial' if inline else 'expansion_failed');self.assertEqual(a['expansion']['state'],'failed')
            self.assertEqual(str(out.actual_cost),'0.2');self.assertEqual(out.retry_after,70);self.assertEqual(self.delays,[70])
            source,ctx=self.setup_source([initial(inline=inline)])
            ctx=replace(ctx,deadline=NOW+timedelta(seconds=1))
            def before(phase,ceiling):
                self.entered.append((phase,ceiling))
                if phase=='expand':raise SourceCallDeferred(2)
            ctx=replace(ctx,before_call=before);out=source(ctx);a=out.facts['data']['answer']
            self.assertEqual(len(self.sent),1);self.assertEqual(a['state'],'partial' if inline else 'expansion_failed')
            self.assertEqual(a['expansion']['state'],'failed');self.assertEqual(str(out.actual_cost),'0.1')

    def test_actual_elapsed_expiry_and_malformed_expansion_preserve_initial_answer(self):
        def late_bill(phase,status,value):
            if phase=='search':self.now=NOW+timedelta(seconds=60)
            return SourceBill('0.1','USD','fixture-search','fixture-invoice-'+phase)
        source,ctx=self.setup_source([initial()],bill_for=late_bill)
        out=source(ctx);a=out.facts['data']['answer']
        self.assertEqual((a['state'],a['expansion']['state']),('expired','expired'))
        self.assertEqual(len(self.sent),1)
        broken=expansion();broken['ai_overview']['references']=[dict(index=9,link='bad-url')]
        source,ctx=self.setup_source([initial(inline=True),broken]);out=source(ctx)
        self.assertEqual(out.facts['data']['answer']['state'],'partial')
        self.assertEqual(str(out.actual_cost),'0.2');self.assertEqual(len(self.sent),2)

    def test_wrong_scope_unknown_fee_transport_and_bad_json_never_trigger_retries(self):
        for phase in ['search','expand']:
            for kind in ['scope','invoice','transport','json']:
                with self.subTest(phase=phase,kind=kind):
                    items=[initial(),expansion()];index=0 if phase=='search' else 1
                    if kind=='scope':items[index]['search_parameters'].update(q='wrong') if phase=='search' else items[index]['search_parameters'].update(page_token='other-token')
                    if kind=='transport':items[index]=RuntimeError('fixture sensitive transport exception '+TOKEN)
                    if kind=='json':items[index]=http_result();items[index]=replace(items[index],body=b'{"a":1,"a":2}')
                    source,ctx=self.setup_source(items)
                    if kind=='invoice':source._bill_for=lambda p,s,v:None if p==phase else SourceBill('0.1','USD','fixture-search','fixture-search-invoice')
                    if kind=='scope' and phase=='expand':out=source(ctx);self.assertEqual(out.facts['data']['answer']['state'],'expansion_failed')
                    else:
                        with self.assertRaises(SourceFailure) as e:source(ctx)
                        self.assertNotIn(TOKEN,str(e.exception))
                    self.assertEqual(len(self.sent),index+1)
                    self.assertNotIn(TOKEN,json.dumps(self.sink.calls,default=str)+json.dumps(self.bills))

    def test_nested_links_unknown_content_truncation_and_unfinished_expansion(self):
        r=expansion();r['ai_overview']['text_blocks'][0]['snippet_links']=dict(text='inline',link='https://example.org/one-link')
        source,ctx=self.setup_source([initial(),r]);out=source(ctx)
        self.assertIn('https://example.org/one-link',[l['url'] for l in out.facts['data']['answer']['links']])
        for mutate in [lambda a:a.update(is_truncated=True),lambda a:a.update(page_token='fixture-unhandled-next-token'),
            lambda a:a['text_blocks'].append(dict(type='future_chart',title='unverified chart',data=[1]))]:
            with self.subTest(mutation=mutate):
                r=expansion();mutate(r['ai_overview']);source,ctx=self.setup_source([initial(),r]);out=source(ctx)
                self.assertTrue(out.facts['is_partial']);self.assertEqual(out.facts['data']['answer']['state'],'partial');self.assertEqual(len(self.sent),2)

    def test_bad_identity_time_cache_and_reference_mapping_fail_with_known_invoices(self):
        for mutate in [lambda r:r['search_metadata'].update(cached=True),lambda r:r['search_metadata'].update(created_at='2020-01-01 00:00:00 UTC'),
            lambda r:r['ai_overview']['text_blocks'][0].update(reference_indexes=[999]),lambda r:r['ai_overview']['references'][0].update(index=True)]:
            with self.subTest(mutation=mutate):
                r=initial(token=False);mutate(r);source,ctx=self.setup_source([r])
                with self.assertRaises(SourceFailure) as e:source(ctx)
                self.assertEqual(str(e.exception.actual_cost),'0.1');self.assertEqual(len(self.sent),1)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AIOHTTPTests(unittest.TestCase):
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    wait=http.CapabilityHTTPTests.wait
    def work(self):self.stop_worker.wait()
    def submit(self,**kwargs):return http.CapabilityHTTPTests.submit(self,'aio_answer',**kwargs)
    def setUp(self):
        http.CapabilityHTTPTests.setUp(self)
        self.calls=[];self.replies=[initial(),expansion()]
        def transport(params,deadline):
            self.calls.append(copy.deepcopy(params));value=self.replies[len(self.calls)-1]
            if isinstance(value,Exception):raise value
            return value if hasattr(value,'body') else http_result(value)
        self.source=SerpAPIAIOAdapter(enabled=True,credential=CREDENTIAL,retain_raw=True,call_cost_ceiling='0.15',billing_unit='fixture-search',
            clock=lambda:self.now,transport=transport,bill_for=lambda p,s,v:SourceBill('0.1','USD','fixture-search','fixture-invoice-'+p))
        self.grants['aio_answer'].update(provider='serpapi',billing_unit='fixture-search',validate_parameters=self.source.validate_parameters,raw_response_retention_allowed=True)
        self.spool=ResponseStore(self.data,lambda t,c:self.grants[c]);self.spool.initialize()
        self.executor=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='aio_answer',provider='serpapi',scope=self.schema,
            execute=self.source,interval_seconds=0,poll_seconds=.01,response_store=self.spool)
        self.executor.initialize();self.runtime.worker_ready=self.executor.ready;self.runtime.response_store=self.spool;self.executor.start()
        end=time.monotonic()+3
        while not self.executor.ready(str(self.account['account_id']),'aio_answer','serpapi') and time.monotonic()<end:time.sleep(.01)
    def tearDown(self):
        self.assertTrue(self.executor.stop());http.CapabilityHTTPTests.tearDown(self)

    def test_native_aio_long_answer_two_receipts_pagination_five_exports_and_private_rights(self):
        from realtime.data_capability_export import verify_bundle
        from realtime.data_capability_response_export import verify_bundle as verify_response
        self.replies[1]['ai_overview']['text_blocks'][1]['snippet']='完整 AIO 合成回答 🍎\n'*15000
        job=self.submit();state=self.wait(job);self.assertEqual((state['status'],state['actual_cost']),('succeeded','0.2'))
        code,data=self.json(job['data_url']);self.assertEqual(code,200,data);o=data['observation'];a=o['payload']['data']['answer']
        self.assertEqual(a['channel'],'aio');self.assertEqual(a['state'],'complete')
        self.assertEqual([b['structure'] for b in a['blocks']],self.replies[1]['ai_overview']['text_blocks'])
        self.assertIn(self.replies[1]['ai_overview']['text_blocks'][1]['snippet'],a['answer_text'])
        code,roots=self.json('/v1/data/ai/answers?capability=aio_answer');self.assertEqual(code,200,roots)
        root='/v1/data/ai/answers/'+roots['records'][0]['object_id']
        for child,expected in [('blocks',len(a['blocks'])),('citations',len(a['citations'])),('links',len(a['links']))]:
            code,page=self.json(root+'/'+child+'?limit=1');self.assertEqual(code,200,page);rows=page['records']
            while page['has_more']:
                code,page=self.json(root+'/'+child+'?limit=1&cursor='+page['next_cursor']);self.assertEqual(code,200,page);rows+=page['records']
            self.assertEqual(len(rows),expected)
        for f in ['json','jsonl','csv','zip','manifest']:
            with self.subTest(format=f):
                code,body,_=self.request(job['status_url']+'/export?format='+f+'&snapshot_hash='+o['snapshot_hash']);self.assertEqual(code,200)
                self.assertNotIn(TOKEN.encode(),body)
                if f=='zip':verify_bundle(body,expected_payload_hash=o['payload_hash'],expected_observation_id=o['observation_id'],expected_snapshot_hash=o['snapshot_hash'])
        code,snapshot=self.json(job['status_url']+'/response');self.assertEqual(code,200,snapshot);self.assertEqual(snapshot['call_count'],2)
        self.assertNotIn(TOKEN,json.dumps(snapshot));self.assertTrue(snapshot['payload']['calls'][1]['scope_verified'])
        code,body,_=self.request(job['status_url']+'/response/export?format=zip');self.assertEqual(code,200)
        verify_response(body,expected_response_id=snapshot['response_id'],expected_snapshot_hash=snapshot['snapshot_hash'])
        p=self.json(job['status_url']+'/progress')[1];self.assertEqual(p['confirmed_call_cost'],'0.2');self.assertEqual(len(p['source_call_attempts']),2)
        self.assertNotIn('evil.invalid',json.dumps(self.calls));self.assertEqual(len(self.calls),2)
        for path in [job['data_url'],root+'/citations',job['status_url']+'/response',job['status_url']+'/progress']:
            self.assertEqual(self.json(path,key=self.other['api_key'])[0],404)
        self.grants['aio_answer']['status']='revoked';self.assertEqual(self.json(job['data_url'])[0],409)

    def test_unknown_expansion_bill_quarantines_whole_account_and_keeps_scoped_responses(self):
        self.source._bill_for=lambda p,s,v:None if p=='expand' else SourceBill('0.1','USD','fixture-search','fixture-first-invoice')
        job=self.submit();end=time.monotonic()+4
        while time.monotonic()<end:
            state=self.json(job['status_url'])[1]
            if state['status']=='reconciliation_required':break
            time.sleep(.01)
        self.assertEqual(state['status'],'reconciliation_required');self.assertIsNone(state['actual_cost'])
        self.assertEqual(state['reservation_status'],'held');p=self.json(job['status_url']+'/progress')[1]
        self.assertEqual(p['confirmed_call_cost'],'0.1');self.assertEqual([v['bill_status'] for v in p['source_call_attempts']],['confirmed','unconfirmed'])
        code,snapshot=self.json(job['status_url']+'/response');self.assertEqual(code,200,snapshot);self.assertEqual(snapshot['call_count'],2)
        self.assertNotIn(TOKEN,json.dumps(snapshot));self.assertEqual(self.json(job['data_url'])[0],409)
        payload=dict(parameters('aio_answer'),budget=dict(maximum_cost='0.5',currency='USD'))
        self.assertEqual(self.json('/v1/data/jobs',method='POST',payload=payload,idempotency='must-not-repeat')[0],503);self.assertEqual(len(self.calls),2)

    def test_known_expansion_rate_limit_partial_result_and_durable_cooldown(self):
        self.replies=[initial(inline=True),http_result(dict(error='synthetic rate limited'),status=429,headers={'content-type':'application/json','retry-after':'70'})]
        job=self.submit();state=self.wait(job);self.assertEqual((state['status'],state['actual_cost']),('partial','0.2'))
        a=self.json(job['data_url'])[1]['observation']['payload']['data']['answer']
        self.assertEqual(a['state'],'partial');self.assertEqual(a['expansion']['state'],'failed');self.assertTrue(a['blocks'])
        with self.data.connect() as db:scope=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()
        self.assertGreaterEqual(scope['next_at'],self.now+timedelta(seconds=70))
        self.assertEqual(len(self.calls),2)

    def test_first_response_retry_after_blocks_expansion_without_claiming_expiry(self):
        self.replies=[http_result(initial(),headers={'content-type':'application/json','retry-after':'60'})]
        job=self.submit();state=self.wait(job);self.assertEqual((state['status'],state['actual_cost']),('partial','0.1'))
        a=self.json(job['data_url'])[1]['observation']['payload']['data']['answer']
        self.assertEqual((a['state'],a['expansion']['state']),('expansion_failed','failed'));self.assertEqual(len(self.calls),1)
        with self.data.connect() as db:scope=db.execute('SELECT next_at FROM sf_capability_executor_scopes WHERE scope=%s',(self.schema,)).fetchone()
        self.assertGreaterEqual(scope['next_at'],self.now+timedelta(seconds=60))

    def test_aio_observation_subscription_fixed_zip_and_ack(self):
        job=self.submit();state=self.wait(job);self.assertEqual(state['status'],'succeeded')
        code,sub=self.json('/v1/data/subscriptions',method='POST',payload=dict(domain='data',name='AIO synthetic observations',filters=dict(capability='aio_answer'),start_mode='history'),idempotency='aio-sub')
        self.assertEqual(code,200,sub);self.assertFalse(sub['active']);root='/v1/data/subscriptions/'+sub['id']
        self.assertEqual(self.json(root+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(root+'/pull',method='POST',payload={});self.assertEqual(code,200,batch)
        self.assertEqual(batch['observation']['payload']['data']['answer']['channel'],'aio')
        path=root+'/batches/'+batch['batch_id'];code,before,_=self.request(path+'/export?format=zip');self.assertEqual(code,200)
        self.assertEqual(self.json(path+'/ack',method='POST',payload=dict(sha256=batch['sha256']))[0],200)
        self.assertEqual(self.request(path+'/export?format=zip')[1],before);self.assertEqual(len(self.calls),2)

    def test_disabled_and_wrong_channel_never_enter_supplier(self):
        self.source.enabled=False
        code,value=self.json('/v1/data/jobs',method='POST',payload=dict(parameters('aio_answer'),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='disabled-aio')
        self.assertEqual(code,409,value);self.assertEqual(self.calls,[])
        with self.assertRaises(ProductError):self.source.validate_parameters(parameters('ai_mode_answer'))
