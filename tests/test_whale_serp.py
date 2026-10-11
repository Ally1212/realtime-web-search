import concurrent.futures
import json
import os
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.request import Request,urlopen
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore,_POOLS,_INITIALIZED_DSNS
from realtime.config import Config
from realtime.serp_console import ConsoleStore
from realtime.whale_serp import WhaleSerpStore,WhaleSerpRuntime,WhaleLinkClient,link_message,task_spec
from realtime.whale_serp_http import handle


class MessageTests(unittest.TestCase):
    def test_search_result_preserves_fields_without_invented_body_or_publication(self):
        row=dict(query='research',source='google_web',page=1,completed_at=datetime.now(timezone.utc),
                 request_id=str(uuid4()),campaign_id=str(uuid4()),response={'search_parameters':{'q':'research'}})
        item=dict(link='https://example.org/a?srsltid=x',title='Real title',snippet='Actual snippet',position=2)
        key,msg=link_message(row,item,Config(),'ctask_1')
        self.assertEqual(msg['content']['canonical_url'],'https://example.org/a')
        self.assertEqual(msg['content']['content_type'],'search_result')
        self.assertEqual(msg['source']['collection_task_id'],'ctask_1')
        self.assertNotIn('body_text',msg['content']);self.assertNotIn('published_at',msg['content'])
        self.assertEqual(msg['discovery']['metadata']['result']['snippet'],'Actual snippet')
        self.assertEqual(msg['provided_capabilities'],['identity','title'])
        row.update(request_id=str(uuid4()),campaign_id=str(uuid4()),completed_at=datetime.now(timezone.utc))
        self.assertEqual(link_message(row,item,Config(),'ctask_1')[0],key)
        self.assertNotEqual(link_message(row,item,Config(),'ctask_2')[0],key)
        item['date']='2026-10-01T00:00:00Z'
        self.assertEqual(link_message(row,item,Config())[1]['content']['published_at'],'2026-10-01T00:00:00+00:00')

    def test_task_validation_and_native_options(self):
        c=Config(whale_supported_task_types=('keyword_search','backfill','content_detail'))
        task=dict(id='ctask_1',dataset_id=c.whale_dataset_id,source_platform=c.whale_source_platform,
                  task_type='keyword_search',required_capabilities=['identity'],payload=dict(keywords=['one','two'],max_items=12,max_pages=3,region='GB',language='zh'))
        spec=task_spec(task,c);self.assertEqual(spec['keywords'],['one','two']);self.assertEqual(spec['max_pages'],3)
        self.assertEqual(spec['options'],dict(web_region='GB',web_language='zh'))
        for p in ({'keywords':4},{'keywords':['one'],'page_size':0},{'keywords':['one'],'max_items':False},{'keywords':['one'],'bogus':True}):
            with self.subTest(p=p),self.assertRaises(ValueError):task_spec(dict(task,payload=p),c)
        with self.assertRaises(ValueError):task_spec(dict(task,required_capabilities=['body']),c)
        logical=task_spec(dict(task,payload={'keyword':'one','max_pages':1,'page_size':25}),c)
        self.assertEqual(logical['google_pages'],3)
        detail=task_spec(dict(task,task_type='content_detail',payload={'urls':['https://example.org/a']}),c)
        self.assertEqual(detail['urls'],['https://example.org/a'])


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires real isolated PostgreSQL')
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='whale_serp_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn=make_conninfo(self.root,options='-c search_path='+self.schema)
        self.base=CampaignStore(self.dsn)
        self.config=Config(database_url=self.dsn,default_proxy_profile='direct',whale_serp_enabled=True,
                           whale_collector_api_key='test-only',whale_supported_task_types=('keyword_search','backfill','content_detail'))
        self.console=ConsoleStore(self.dsn);self.console.initialize();self.console.initialize_console(self.config)
        self.console.save_settings({'continuous_enabled':False},self.config)
        self.store=WhaleSerpStore(self.dsn);self.store.initialize()
        self.cid=self.console.create_task({'query':'energy','start':False},self.config)['campaign_id']
        self.record(self.cid,'google_web','https://example.org/a','Solar research')
        self.record(self.cid,'google_news','https://example.org/b','Energy news')
        self.record(self.cid,'google_trends','https://example.org/c','Trend report')
    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for k in list(_POOLS):
            if k[0]==self.dsn:_POOLS.pop(k).close()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
    def record(self,cid,source,url,title):
        item=dict(link=url,title=title,snippet='Real saved snippet',position=2)
        response={'organic_results':[item]} if source=='google_web' else {'news_results':[item]} if source=='google_news' else {'trending_searches':[dict(keyword='trend',articles=[item])]}
        return self.console.record(cid,source=source,provider='openserp' if source=='google_web' else 'google_rss',query='energy',page=1,language='en',response=response,elapsed_seconds=.1,classification='results',http_status=200)
    def job(self,**kw):return self.store.enqueue(dict(limit=10,**kw),self.config)
    def task(self,**kw):return dict(id='ctask_test',dataset_id=self.config.whale_dataset_id,source_platform=self.config.whale_source_platform,task_type='content_detail',payload={'urls':['https://example.org/a']},**kw)
    def test_default_off_preserves_collection_and_articles(self):
        self.assertFalse(self.store.settings()['uploads_enabled']);self.assertFalse(self.store.settings()['claims_enabled'])
        self.store.save_settings({'uploads_enabled':True,'claims_enabled':True})
        self.assertFalse(self.console.settings(self.config)['continuous_enabled'])
        self.assertFalse(self.console.settings(self.config)['content_fetch_enabled'])
        with self.base.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM pages').fetchone()['n'],0)
    def test_filtered_staging_bounded_deduplicated_and_transactional(self):
        j=self.job(q='Solar',source='google_web');self.assertEqual(j['collected'],1)
        payload=self.store.receipts(j['id'])[0]['payload']
        self.assertEqual(payload['content']['canonical_url'],'https://example.org/a')
        again=self.job(q='Solar');self.assertEqual(self.store.receipts(again['id'])[0]['id'],self.store.receipts(j['id'])[0]['id'])
        self.assertEqual(self.job(campaign_id=self.cid)['collected'],3)
        self.assertEqual(self.job(urls=['https://example.org/c'],new_only=False)['collected'],1)
        before=self.store.jobs()['total']
        with self.assertRaises(ValueError):self.job(q='missing value')
        self.assertEqual(self.store.jobs()['total'],before)
    def test_all_sources_and_existing_historical_url_observation_can_upload(self):
        other=self.console.create_task({'query':'new search','start':False},self.config)['campaign_id']
        self.record(other,'google_web','https://example.org/a','Again')
        j=self.job(campaign_id=other,new_only=False)
        self.assertEqual(j['collected'],1)
        with self.assertRaises(ValueError):self.job(campaign_id=other,new_only=True)
    def test_creation_subscription_stages_new_batches_without_overriding_upload_pause(self):
        cid=self.console.create_task({'query':'whale sync','start':False,'upload_to_whale':True},self.config)['campaign_id']
        self.record(cid,'google_web','https://example.org/sync-one','One')
        self.store.stage_subscription(cid,self.config)
        job=self.store.jobs()['jobs'][0];self.assertTrue(job['automatic']);self.assertEqual(job['collected'],1)
        self.store.stage_subscription(cid,self.config);self.assertEqual(self.store.jobs()['total'],1)
        self.store.action(str(job['id']),'pause')
        self.record(cid,'google_web','https://example.org/sync-two','Two')
        self.store.stage_subscription(cid,self.config)
        job=self.store.get_job(job['id']);self.assertEqual(job['collected'],2);self.assertEqual(job['status'],'paused')
        self.store.action(str(job['id']),'stop')
        self.record(cid,'google_web','https://example.org/sync-three','Three')
        self.store.stage_subscription(cid,self.config);self.assertEqual(self.store.get_job(job['id'])['collected'],2)

    def test_retry_after_and_invalid_batch_stay_pending_not_success(self):
        job=self.job(source='google_web');self.store.save_settings({'uploads_enabled':True})
        rows=self.store.reserve(job['id'],10);self.store.settle(rows,error='Whale HTTP 429',retry_after='120')
        r=self.store.receipts(job['id'])[0];self.assertEqual(r['state'],'pending')
        self.assertGreater((r['next_attempt_at']-datetime.now(timezone.utc)).total_seconds(),110)

    def test_pause_resume_stop_and_reservation_concurrency(self):
        j=self.job();self.store.save_settings({'uploads_enabled':True})
        self.store.action(str(j['id']),'pause');self.assertEqual(self.store.reserve(j['id'],50),[])
        self.store.action(str(j['id']),'resume')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:rows=list(pool.map(lambda _:self.store.reserve(j['id'],50),range(2)))
        ids=[r['id'] for batch in rows for r in batch];self.assertEqual(len(ids),len(set(ids)));self.assertEqual(len(ids),3)
        self.store.action(str(j['id']),'stop');self.assertEqual(self.store.reserve(j['id'],50),[])
        with self.assertRaises(ValueError):self.store.action(str(j['id']),'resume')
    def test_expired_lease_recovery_rejects_stale_ack_and_preserves_identity(self):
        j=self.job(source='google_web');self.store.save_settings({'uploads_enabled':True})
        old=self.store.reserve(j['id'],50)
        with self.base.connect() as db:db.execute("UPDATE whale_serp_outbox SET lease_expires_at=now()-interval '1 second'")
        new=self.store.reserve(j['id'],50);self.assertEqual(old[0]['payload'],new[0]['payload'])
        self.store.settle(old,[dict(receipt_status='queued',document_id='old')]);self.assertEqual(self.store.receipts(j['id'])[0]['state'],'pending')
        self.store.settle(new,[dict(receipt_status='queued',document_id='new')]);self.assertEqual(self.store.receipts(j['id'])[0]['receipt']['document_id'],'new')
    def test_queued_accepted_duplicate_rejected_counts_and_retry(self):
        j=self.job();self.store.save_settings({'uploads_enabled':True})
        rows=self.store.reserve(j['id'],50)
        self.store.settle(rows,[{'receipt_status':'queued'},{'receipt_status':'duplicate'},{'receipt_status':'rejected','message':'no'}])
        x=self.store.get_job(j['id']);self.assertEqual(x['received'],1);self.assertEqual(x['duplicates'],1);self.assertEqual(x['counts']['rejected'],1)
        self.store.finish(j['id'],'failed','platform_rejected');self.store.action(str(j['id']),'retry')
        rows=self.store.reserve(j['id'],50);self.assertEqual(len(rows),1)
        self.store.settle(rows,[dict(receipt_status='accepted')]);self.assertEqual(self.store.get_job(j['id'])['received'],2)
    def test_task_import_cas_pause_and_idempotent_resume(self):
        task=self.task();jid=self.store.import_task(task,self.config)
        self.assertEqual(self.store.get_job(jid)['status'],'paused')
        self.store.save_settings({'claims_enabled':True})
        self.assertEqual(self.store.import_task(task,self.config),jid)
        self.assertEqual(self.store.get_job(jid)['status'],'active')
        self.store.save_settings({'claims_enabled':False})
        j=self.store.get_job(jid);self.assertEqual(j['status'],'paused')
        self.assertFalse(self.base.is_local_campaign(str(j['campaign_id'])))
        self.assertEqual(self.base.campaign(str(j['campaign_id']))['status'],'paused')
    def test_platform_remote_pause_resume_cancel_and_no_body_request(self):
        self.store.save_settings({'claims_enabled':True,'uploads_enabled':False});jid=self.store.import_task(self.task(),self.config)
        client=Mock(agent_id='test-agent');client.claim.return_value=[];client.task_heartbeat.return_value={'control_signal':'pause'}
        runtime=WhaleSerpRuntime(self.config,self.store,client)
        try:
            runtime.tick();self.assertEqual(self.store.get_job(jid)['status'],'paused')
            client.task_heartbeat.return_value={'control_signal':'none'};runtime.tick()
            self.assertEqual(self.store.get_job(jid)['status'],'active')
            client.task_heartbeat.return_value={'control_signal':'cancel'};runtime.tick()
            self.assertEqual(self.store.get_job(jid)['status'],'stopped')
            self.assertFalse(self.console.settings(self.config)['continuous_enabled'])
        finally:runtime.pool.shutdown(wait=True)
    def test_keyword_and_backfill_collect_real_frontier_then_upload_without_body(self):
        for kind in ('keyword_search','backfill'):
            with self.subTest(kind=kind):
                self.store.save_settings({'claims_enabled':True,'uploads_enabled':True})
                task=dict(id='ctask_'+kind,dataset_id=self.config.whale_dataset_id,source_platform=self.config.whale_source_platform,
                          task_type=kind,payload={'keyword':kind,'max_items':3,'max_pages':1,'page_size':25})
                jid=self.store.import_task(task,self.config)
                client=Mock(agent_id='test-agent');client.claim.return_value=[];client.task_heartbeat.return_value={'control_signal':'none'};client.claim.return_value=[]
                client.post.side_effect=lambda path,rows:[{'receipt_status':'queued'} for _ in rows]
                runtime=WhaleSerpRuntime(self.config,self.store,client)
                def collect(cid,c):
                    self.assertFalse(c.content_fetch_enabled);self.assertEqual(c.google_web_max_pages,3)
                    self.record(cid,'google_web','https://example.org/'+kind,kind)
                    b=self.base.acquire_google_page_batch(cid,kind,'en',3,3,21600)
                    for page in range(1,4):self.base.record_google_page_result(b,page,success=True,result_count=1)
                try:
                    with patch('realtime.serp_worker.run_serp_campaign',side_effect=collect):
                        runtime.tick();runtime.searches[jid].result(timeout=5);runtime.tick()
                    self.assertEqual(self.store.get_job(jid)['status'],'succeeded');client.complete.assert_called_once()
                    self.assertNotIn('body_text',self.store.receipts(jid)[0]['payload']['content'])
                finally:runtime.pool.shutdown(wait=True)

    def test_local_pause_is_not_overridden_by_platform_reclaim_and_stop_is_reported(self):
        self.store.save_settings({'claims_enabled':True});jid=self.store.import_task(self.task(),self.config)
        self.store.action(jid,'pause');self.store.import_task(self.task(),self.config)
        self.assertEqual(self.store.get_job(jid)['status'],'paused')
        self.store.action(jid,'stop');client=Mock(agent_id='test-agent');client.claim.return_value=[]
        runtime=WhaleSerpRuntime(self.config,self.store,client)
        try:runtime.tick();client.fail.assert_called_once();self.assertTrue(self.store.get_job(jid)['reported'])
        finally:runtime.pool.shutdown(wait=True)

    def test_real_http_retry_upload_and_platform_detail_end_to_end(self):
        calls=[];task=self.task();claimed=False;fail_once=True
        class H(BaseHTTPRequestHandler):
            def do_POST(inner):
                nonlocal claimed,fail_once
                data=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])));calls.append((inner.path,data))
                result={};status=200
                if inner.path.endswith('/register'):result={'agent_id':'bound-agent'}
                elif inner.path.endswith('/claim'):
                    result={'tasks':[] if claimed else [task]};claimed=True
                elif inner.path.endswith('/heartbeat'):result={'control_signal':'none'}
                elif inner.path.endswith('/bulk'):
                    if fail_once:status=429;fail_once=False;result={'error':'rate_limit'}
                    else:result=[dict(receipt_status='queued',document_id='doc-real-http') for _ in data]
                b=json.dumps(result).encode();inner.send_response(status);inner.send_header('Content-Type','application/json');inner.send_header('Content-Length',str(len(b)));inner.end_headers();inner.wfile.write(b)
            def log_message(inner,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),H);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config=replace(self.config,whale_base_url='http://127.0.0.1:'+str(server.server_port))
        runtime=WhaleSerpRuntime(config,self.store,WhaleLinkClient(config))
        try:
            j=self.job(source='google_web');runtime.tick();self.assertFalse(calls)
            self.store.save_settings({'uploads_enabled':True});runtime.tick()
            self.assertEqual(self.store.get_job(j['id'])['counts']['pending'],1)
            with self.base.connect() as db:db.execute('UPDATE whale_serp_outbox SET next_attempt_at=now()')
            runtime.tick();self.assertEqual(self.store.get_job(j['id'])['status'],'succeeded')
            self.store.save_settings({'claims_enabled':True});runtime.tick();runtime.tick()
            job=next(j for j in self.store.jobs()['jobs'] if j['kind']=='platform')
            self.assertEqual(job['status'],'succeeded')
            self.assertTrue(any(p.endswith('/complete') for p,_ in calls))
            for p,body in calls:
                if p.endswith('/register') or p.endswith('/claim'):self.assertNotIn('body',body['declared_capabilities'])
                if p.endswith('/bulk'):
                    self.assertTrue(all('body_text' not in m['content'] for m in body))
                    if body[0]['source'].get('collection_task_id'):self.assertEqual(body[0]['source']['collection_task_id'],task['id'])
        finally:runtime.pool.shutdown(wait=True);server.shutdown();server.server_close();thread.join()
    def test_console_http_creates_controls_and_displays_receipts_without_credentials(self):
        config=self.config
        class H(BaseHTTPRequestHandler):
            def send_json(inner,d,status=200):
                b=json.dumps(d,default=str).encode();inner.send_response(status);inner.send_header('Content-Type','application/json');inner.send_header('Content-Length',str(len(b)));inner.end_headers();inner.wfile.write(b)
            def do_GET(inner):handle(inner,'GET')
            def do_POST(inner):handle(inner,'POST')
            def log_message(inner,*a):pass
        H.config=config;server=ThreadingHTTPServer(('127.0.0.1',0),H);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        def call(path,p=None):
            req=Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(p).encode() if p is not None else None,headers={'Content-Type':'application/json'})
            with urlopen(req) as r:return json.loads(r.read())
        try:
            status=call('/api/whale/status');self.assertNotIn('test-only',json.dumps(status))
            job=call('/api/whale/jobs',{'source':'google_news','limit':1});jid=str(job['id'])
            for op,state in [('pause','paused'),('resume','active'),('stop','stopped')]:self.assertEqual(call('/api/whale/jobs/'+jid+'/'+op,{})['status'],state)
            self.assertEqual(len(call('/api/whale/jobs/'+jid+'/receipts')['receipts']),1)
            call('/api/whale/settings',{'uploads_enabled':True});self.assertFalse(self.console.settings(self.config)['continuous_enabled'])
        finally:server.shutdown();server.server_close();t.join()
