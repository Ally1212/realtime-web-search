"""Synthetic provider-shaped AI Mode, never real supplier calls or gold answers."""
import copy
import json
import os
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from realtime.data_capability_executor import CapabilityExecutor,SourceFailure
from realtime.data_capability_serpapi import SourceBill,_redact
from realtime.data_capability_serpapi_ai import SerpAPIAIModeAdapter,normalize,request_parameters
from realtime.data_capability_responses import ResponseStore
from realtime.product import ProductError
from tests.test_data_capability_serpapi import NOW,CREDENTIAL,context,http_result
from tests.test_data_capability_facts import parameters
from tests import test_data_capability_http as http


def response(p=None):
    p=p or parameters('ai_mode_answer')
    return dict(search_metadata=dict(id='synthetic-ai-mode-request',status='Success',created_at='2026-11-10 12:00:00 UTC'),
        search_parameters=request_parameters(p),
        text_blocks=[dict(type='heading',snippet='合成标题'),
            dict(type='paragraph',snippet='完整回答 苹果。',reference_indexes=[9],snippet_links=[dict(text='普通链接',link='https://example.org/ordinary')]),
            dict(type='list',list=[dict(title='嵌套条目',text_blocks=[dict(type='paragraph',snippet='深层文本',reference_indexes=[2]),
                dict(type='code_block',language='python',code='print("苹果")\n')])]),
            dict(type='table',table=[['月份','值'],['January','0']],formatted=[dict(month='January',value='0')],reference_indexes=[9,2])],
        reconstructed_markdown='这是刻意缩短的合成重构摘要，不是完整回答。',
        references=[dict(index=9,title='明确引用九',link='https://example.org/ref-nine'),
                    dict(index=2,title='明确引用二',link='https://example.org/ref-two'),
                    dict(index=50,title='无块映射引用',link='https://example.org/ref-unmapped')],
        quick_results=[dict(title='普通快结果',link='https://example.org/quick')],
        inline_images=['https://example.org/image.png'],related_questions=[])


def adapter(transport=None,**kwargs):
    options=dict(credential=CREDENTIAL,enabled=True,retain_raw=True,billing_unit='fixture-search',clock=lambda:NOW,
                 bill_for=lambda status,value:SourceBill('0.1','USD','fixture-search','fixture-ai-independent-invoice'),
                 transport=transport or (lambda params,deadline:http_result(response())))
    options.update(kwargs);return SerpAPIAIModeAdapter(**options)


def normalized(value=None,p=None,**kwargs):
    return normalize(p or parameters('ai_mode_answer'),value if value is not None else response(),requested_at=NOW,received_at=NOW,**kwargs)


class AIModeContractTests(unittest.TestCase):
    def test_candidate_scope_and_no_implicit_execution(self):
        source=SerpAPIAIModeAdapter()
        self.assertNotIn(CREDENTIAL,repr(source))
        with self.assertRaises(ProductError):source.validate_parameters(parameters('ai_mode_answer'))
        with self.assertRaises(SourceFailure) as failure:source(context(parameters('ai_mode_answer')))
        self.assertEqual(str(failure.exception.actual_cost),'0')
        for device in ['desktop','mobile']:
            for country,gl in [('US','us'),('GB','uk')]:
                with self.subTest(device=device,country=country):
                    p=dict(parameters('ai_mode_answer'),device=device,country=country)
                    sent=request_parameters(p)
                    self.assertEqual((sent['gl'],sent['device'],sent['no_cache'],sent['continuable']),(gl,device,'true','false'))
                    self.assertNotIn('api_key',sent)
                    self.assertEqual(normalized(response(p),p)['effective_parameters'],p)
        for p in [parameters('aio_answer'),parameters('trends_history'),dict(parameters('ai_mode_answer'),country='ZZ'),
                  dict(parameters('ai_mode_answer'),language='zh-CN'),dict(parameters('ai_mode_answer'),turn=2)]:
            with self.subTest(parameters=p),self.assertRaises(ProductError):request_parameters(p)
        self.assertNotIn(CREDENTIAL,repr(adapter()))

    def test_full_structure_and_explicit_native_citations_not_markdown_or_array_order(self):
        original=response();facts=normalized(original);a=facts['data']['answer']
        self.assertEqual(a['state'],'complete');self.assertFalse(facts['is_partial'])
        self.assertEqual(a['channel'],'ai_mode');self.assertIsNone(a['model'])
        self.assertEqual([b['structure'] for b in a['blocks']],original['text_blocks'])
        expected='合成标题\n\n完整回答 苹果。\n\n嵌套条目\n深层文本\nprint("苹果")\n\n\n月份\t值\nJanuary\t0'
        self.assertEqual(a['answer_text'],expected)
        self.assertNotEqual(a['answer_text'],original['reconstructed_markdown'])
        self.assertEqual(a['source_fields']['provider_reconstructed_markdown'],original['reconstructed_markdown'])
        self.assertEqual(a['citations'][0]['id'],'reference-9')
        self.assertEqual(a['citations'][0]['block_ids'],['block-1','block-3'])
        self.assertEqual(a['citations'][1]['block_ids'],['block-2','block-3'])
        self.assertIn('/text_blocks/2/list/0/text_blocks/0/reference_indexes',a['citations'][1]['source_fields']['mapping_paths'])
        self.assertEqual(a['citations'][2]['mapping_status'],'not_covered')
        self.assertEqual(a['citations'][2]['block_ids'],[])
        self.assertTrue(all(c['text_ranges']==[] for c in a['citations']))
        self.assertEqual([l['url'] for l in a['links']],['https://example.org/ordinary','https://example.org/quick','https://example.org/image.png'])
        self.assertIsNone(facts['source']['source_observed_at'])
        self.assertEqual(facts['raw']['status'],'not_retained')
        a['blocks'][0]['structure']['snippet']='changed'
        self.assertEqual(original['text_blocks'][0]['snippet'],'合成标题')

    def test_partial_unknown_truncated_missing_reference_list_and_followup_tokens(self):
        cases=[lambda r:r['text_blocks'].append(dict(type='chart_block',title='图表',data=[dict(x='0',y='1')])),
               lambda r:r['text_blocks'][0].update(new_answer_content='future field'),
               lambda r:r.update(is_partial=True),lambda r:r.update(has_more=True),
               lambda r:r['text_blocks'][0].update(is_truncated=True),lambda r:r.update(new_answer_module=dict(text='new'))]
        for mutate in cases:
            with self.subTest(mutation=mutate):
                r=response();mutate(r);f=normalized(r);self.assertTrue(f['is_partial']);self.assertEqual(f['data']['answer']['state'],'partial')
        f=normalized(response(),redacted_paths=['/next_page_token']);self.assertTrue(f['is_partial'])
        r=response();r['subsequent_request_token']='synthetic-short-lived-not-a-real-token'
        clean,paths=_redact(r,CREDENTIAL);f=normalized(clean,retain_raw=True,redacted_paths=paths)
        self.assertFalse(f['is_partial']);self.assertEqual(f['raw']['status'],'redacted')
        self.assertNotIn('synthetic-short-lived',json.dumps(f))
        r=response();r['references']=[]
        for b in r['text_blocks']:b.pop('reference_indexes',None)
        r['text_blocks'][2]['list'][0]['text_blocks'][0].pop('reference_indexes');r.pop('references')
        self.assertEqual(normalized(r)['data']['answer']['state'],'partial')

    def test_explicit_empty_response_not_refusal_or_missing_payload(self):
        r=response();r['text_blocks']=[];r['references']=[];r.pop('reconstructed_markdown')
        a=normalized(r)['data']['answer'];self.assertEqual(a['state'],'not_observed');self.assertIsNone(a['answer_text'])
        self.assertEqual(a['expansion']['end_reason'],'source_explicit_empty_text_blocks')
        for mutate in [lambda r:r.pop('text_blocks'),lambda r:r.update(has_more=True),
                       lambda r:r.update(reconstructed_markdown='cannot drop this text')]:
            with self.subTest(mutation=mutate),self.assertRaises((ValueError,KeyError)):normalized((lambda v:(mutate(v),v)[1])(copy.deepcopy(r)))
        r=response();r['text_blocks']=[dict(type='paragraph',snippet='I cannot answer that question.')];r['references']=[]
        self.assertEqual(normalized(r)['data']['answer']['state'],'complete') # No guessed formal refusal classification.

    def test_wrong_channel_scope_cache_time_indices_urls_and_structure_rejected(self):
        mutations=[lambda r:r['search_parameters'].update(engine='google'),lambda r:r['search_parameters'].update(q='different'),
            lambda r:r['search_parameters'].pop('device'),lambda r:r['search_parameters'].update(gl='gb'),
            lambda r:r['search_metadata'].update(status='Processing'),lambda r:r['search_metadata'].update(created_at='2026-11-10 11:59:59 UTC'),
            lambda r:r['search_metadata'].update(created_at='2026-11-10 12:00:01 UTC'),lambda r:r['search_metadata'].update(cached=True),
            lambda r:r.update(ai_overview=dict(text='different channel')),lambda r:r.update(organic_results=[]),
            lambda r:r['references'][1].update(index=9),lambda r:r['references'][0].update(index=True),
            lambda r:r['text_blocks'][1].update(reference_indexes=[0]),lambda r:r['text_blocks'][1].update(reference_indexes=[9,9]),
            lambda r:r['text_blocks'][1].update(reference_indexes=[True]),lambda r:r['references'][0].update(link='https://user:pass@example.org'),
            lambda r:r['text_blocks'][1]['snippet_links'][0].update(link='javascript:alert(1)'),
            lambda r:r['text_blocks'][3].update(table=[['x',1]]),lambda r:r.update(is_truncated='true'),
            lambda r:r['text_blocks'][0].pop('type'),lambda r:r['text_blocks'][0].update(is_partial=1)]
        for mutate in mutations:
            r=response();mutate(r)
            with self.subTest(mutation=mutate),self.assertRaises((ValueError,KeyError,TypeError,ProductError)):normalized(r)

    def test_paid_contract_failure_and_unknown_bill_use_shared_durable_path(self):
        r=response();r['references'][0]['index']=0
        ctx=context(parameters('ai_mode_answer'))
        with self.assertRaises(SourceFailure) as failure:adapter(lambda p,d:http_result(r))(ctx)
        self.assertEqual(failure.exception.code,'source_contract_unverified');self.assertEqual(str(failure.exception.actual_cost),'0.1')
        self.assertEqual(ctx.response.calls[0]['payload'],r)
        ctx=context(parameters('ai_mode_answer'))
        with self.assertRaises(SourceFailure) as failure:adapter(bill_for=lambda s,r:None)(ctx)
        self.assertEqual(failure.exception.code,'source_billing_unresolved');self.assertIsNone(failure.exception.actual_cost)
        self.assertTrue(ctx.response.calls)
        source=adapter();ctx=context(parameters('ai_mode_answer'))
        with patch.object(source,'_transport') as transport:
            missing=replace(ctx,response=None)
            with self.assertRaises(SourceFailure) as failure:source(missing)
            self.assertEqual(str(failure.exception.actual_cost),'0');transport.assert_not_called()

    def test_token_in_urls_fields_and_values_is_removed_from_facts(self):
        r=response();r.update(subsequent_request_token='synthetic-continuation-secret')
        r['related_questions']=[dict(question='more',serpapi_link='https://serpapi.com/search.json?engine=google_ai_mode&subsequent_request_token=synthetic-url-secret')]
        r['text_blocks'][1]['snippet']+=' '+CREDENTIAL+' synthetic-continuation-secret'
        ctx=context(parameters('ai_mode_answer'));facts=adapter(lambda p,d:http_result(r))(ctx).facts
        dumped=json.dumps(facts)
        for secret in [CREDENTIAL,'synthetic-continuation-secret','synthetic-url-secret']:
            self.assertNotIn(secret,dumped);self.assertNotIn(secret,json.dumps(ctx.response.calls,default=str))
        self.assertEqual(facts['raw']['status'],'redacted')
        self.assertIn('[redacted]',facts['data']['answer']['answer_text'])

    def test_very_long_answer_not_shortened_and_deep_structure_bounded(self):
        r=response();r['text_blocks'][1]['snippet']='长文本 🍎\n'*40000
        f=normalized(r);self.assertIn(r['text_blocks'][1]['snippet'],f['data']['answer']['answer_text'])
        self.assertEqual(f['data']['answer']['blocks'][1]['structure'],r['text_blocks'][1])
        leaf=dict(snippet='leaf')
        for _ in range(34):leaf=dict(type='list',list=[leaf])
        r['text_blocks']=[leaf]
        with self.assertRaises(ValueError):normalized(r)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AIModeHTTPTests(unittest.TestCase):
    request=http.CapabilityHTTPTests.request
    json=http.CapabilityHTTPTests.json
    wait=http.CapabilityHTTPTests.wait
    def work(self):self.stop_worker.wait()
    def submit(self,**kwargs):return http.CapabilityHTTPTests.submit(self,'ai_mode_answer',**kwargs)
    def setUp(self):
        http.CapabilityHTTPTests.setUp(self)
        self.source=adapter(clock=lambda:self.now)
        grant=self.grants['ai_mode_answer'];grant.update(provider='serpapi',billing_unit='fixture-search',validate_parameters=self.source.validate_parameters,
                                                       raw_response_retention_allowed=True)
        self.spool=ResponseStore(self.data,lambda t,c:self.grants[c]);self.spool.initialize()
        self.executor=CapabilityExecutor(self.data,lambda t,c:self.grants[c],capability='ai_mode_answer',provider='serpapi',scope=self.schema,
                                         execute=self.source,interval_seconds=0,poll_seconds=.01,response_store=self.spool)
        self.executor.initialize();self.runtime.worker_ready=self.executor.ready;self.runtime.response_store=self.spool;self.executor.start()
        limit=time.monotonic()+3
        while not self.executor.ready(str(self.account['account_id']),'ai_mode_answer','serpapi') and time.monotonic()<limit:time.sleep(.01)
    def tearDown(self):
        self.assertTrue(self.executor.stop());http.CapabilityHTTPTests.tearDown(self)

    def test_actual_customer_routes_children_complete_exports_and_response_identity(self):
        from realtime.data_capability_export import verify_bundle
        from realtime.data_capability_response_export import verify_bundle as verify_response
        r=response();r['text_blocks'][1]['snippet']='完整长文 🍎\n'*30000
        self.source._transport=lambda p,d:http_result(r)
        job=self.submit();state=self.wait(job);self.assertEqual(state['status'],'succeeded')
        self.assertEqual(state['actual_cost'],'0.1');code,data=self.json(job['data_url']);self.assertEqual(code,200,data)
        o=data['observation'];a=o['payload']['data']['answer'];self.assertIn(r['text_blocks'][1]['snippet'],a['answer_text'])
        self.assertEqual([b['structure'] for b in a['blocks']],r['text_blocks'])
        code,page=self.json('/v1/data/ai/answers?capability=ai_mode_answer');self.assertEqual(code,200,page)
        self.assertEqual(len(page['records']),1);root='/v1/data/ai/answers/'+page['records'][0]['object_id']
        for key,count in [('blocks',4),('citations',3),('links',3)]:
            with self.subTest(child=key):
                code,child=self.json(root+'/'+key+'?limit=1');self.assertEqual(code,200,child);self.assertTrue(child['has_more'])
                rows=child['records']
                while child['has_more']:
                    code,child=self.json(root+'/'+key+'?limit=1&cursor='+child['next_cursor']);self.assertEqual(code,200,child);rows+=child['records']
                self.assertEqual(len(rows),count);self.assertEqual(rows,a[key])
        for format in ['json','jsonl','csv','manifest','zip']:
            with self.subTest(format=format):
                code,body,_=self.request(job['status_url']+'/export?format='+format+'&snapshot_hash='+o['snapshot_hash']);self.assertEqual(code,200,body[:80])
                if format=='zip':verify_bundle(body,expected_payload_hash=o['payload_hash'],expected_observation_id=o['observation_id'],expected_snapshot_hash=o['snapshot_hash'])
        code,snapshot=self.json(job['status_url']+'/response');self.assertEqual(code,200,snapshot);self.assertEqual(snapshot['payload'],r)
        code,body,_=self.request(job['status_url']+'/response/export?format=zip');self.assertEqual(code,200)
        verify_response(body,expected_response_id=snapshot['response_id'],expected_snapshot_hash=snapshot['snapshot_hash'])
        for path in [job['data_url'],root,root+'/citations',job['status_url']+'/response']:
            self.assertEqual(self.json(path,key=self.other['api_key'])[0],404)
        self.grants['ai_mode_answer']['status']='revoked'
        self.assertEqual(self.json(job['data_url'])[0],409);self.assertEqual(self.request(job['status_url']+'/response/export?format=zip')[0],409)

    def test_partial_unknown_structure_and_paid_parser_failure_remain_distinct(self):
        r=response();r['text_blocks'].append(dict(type='chart_block',title='源图表',data=[dict(x='year',y='1')]))
        self.source._transport=lambda p,d:http_result(r)
        job=self.submit(idempotency='partial');self.assertEqual(self.wait(job)['status'],'partial')
        code,saved=self.json(job['data_url']);self.assertEqual(code,200);self.assertTrue(saved['observation']['payload']['is_partial'])
        r['text_blocks'][1]['reference_indexes']=[999]
        failed=self.submit(idempotency='paid-bad-parser');state=self.wait(failed)
        self.assertEqual(state['status'],'failed');self.assertEqual(state['actual_cost'],'0.1');self.assertEqual(state['error_code'],'source_contract_unverified')
        self.assertEqual(self.json(failed['data_url'])[0],409)
        self.assertEqual(self.json(failed['status_url']+'/response')[1]['payload'],r)
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],1)

    def test_unknown_bill_pauses_source_no_automatic_second_call(self):
        calls=[];self.source._transport=lambda p,d:(calls.append(copy.deepcopy(p)) or http_result(response()))
        self.source._bill_for=lambda status,value:None
        job=self.submit();limit=time.monotonic()+4
        while time.monotonic()<limit:
            state=self.json(job['status_url'])[1]
            if state['status']=='reconciliation_required':break
            time.sleep(.01)
        self.assertEqual(state['status'],'reconciliation_required');self.assertIsNone(state['actual_cost'])
        self.assertEqual(self.json(job['status_url']+'/response')[0],200)
        code,denied=self.json('/v1/data/jobs',method='POST',payload=dict(parameters('ai_mode_answer'),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='would-repeat')
        self.assertEqual(code,503,denied);self.assertEqual(len(calls),1)

    def test_subscription_complete_answer_fixed_delivery_and_ack(self):
        job=self.submit();job=self.wait(job)
        code,sub=self.json('/v1/data/subscriptions',method='POST',payload=dict(domain='data',name='AI Mode observations',filters=dict(capability='ai_mode_answer'),start_mode='history'),idempotency='ai-sub')
        self.assertEqual(code,200,sub);self.assertFalse(sub['active'])
        root='/v1/data/subscriptions/'+sub['id'];self.assertEqual(self.json(root+'/resume',method='POST',payload={})[0],200)
        code,batch=self.json(root+'/pull',method='POST',payload={});self.assertEqual(code,200,batch)
        self.assertEqual(batch['observation']['observation_id'],job['result_ref'])
        self.assertEqual(batch['observation']['payload']['data']['answer']['channel'],'ai_mode')
        path=root+'/batches/'+batch['batch_id'];code,before,_=self.request(path+'/export?format=zip');self.assertEqual(code,200)
        self.assertEqual(self.json(path+'/ack',method='POST',payload=dict(sha256=batch['sha256']))[0],200)
        self.assertEqual(self.request(path+'/export?format=zip')[1],before)
        self.assertEqual(self.json(root+'/status')[1]['pending_observations'],0)

    def test_aio_not_served_by_ai_mode_adapter_and_disabled_source_rejects_admission(self):
        with patch.object(self.source,'_transport') as transport:
            self.source.enabled=False
            code,value=self.json('/v1/data/jobs',method='POST',payload=dict(parameters('ai_mode_answer'),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='disabled')
            self.assertEqual(code,409,value);transport.assert_not_called()
            self.source.enabled=True
            code,value=self.json('/v1/data/jobs',method='POST',payload=dict(parameters('aio_answer'),budget=dict(maximum_cost='0.5',currency='USD')),idempotency='aio-is-separate')
            self.assertEqual(code,503,value);transport.assert_not_called()

    def test_wrong_scope_error_body_is_not_delivered_under_this_jobs_grant(self):
        r=dict(error='synthetic provider error',search_metadata=dict(status='Error'),search_parameters=dict(engine='google_ai_mode',q='another private prompt',gl='uk',hl='en',device='mobile'))
        self.source._transport=lambda p,d:http_result(r,status=403)
        job=self.submit();state=self.wait(job)
        self.assertEqual(state['status'],'failed');self.assertEqual(state['actual_cost'],'0.1')
        code,snapshot=self.json(job['status_url']+'/response')
        self.assertEqual(code,200,snapshot);self.assertEqual(snapshot['raw_status'],'not_retained')
        self.assertFalse(snapshot['scope_verified']);self.assertIsNone(snapshot['payload'])
        self.assertNotIn('another private prompt',json.dumps(snapshot))
