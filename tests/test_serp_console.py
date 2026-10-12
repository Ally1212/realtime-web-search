import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore
from realtime.config import Config
from realtime.serp_console import ConsoleStore, options, sources
from realtime.serp_page import PAGE
from realtime.serp_worker import make_discovery, run_serp_campaign, SerpSupply
from realtime.serp_fields import result_items
from realtime.discovery import SearchResult
from realtime.serp_console_http import get, post
from realtime.google_feeds import collect_sources
from http.server import BaseHTTPRequestHandler


class ConsoleValidationTests(unittest.TestCase):
    def test_workbench_keeps_guided_form_and_advanced_capabilities(self):
        for text in ('新建采集任务', '选择数据源', '采集一次', '持续采集',
                     '高级参数：页数、地区、语言、预算', '平台集成', '数据结果', '系统设置',
                     '导出 JSONL', '导出 CSV', '平台回执'):
            self.assertIn(text, PAGE)
        self.assertNotIn('id="taskMode"', PAGE)

    def test_source_and_region_validation(self):
        for value in ([], ['rss'], ['google_web', {}], 'google_web'):
            with self.assertRaises(ValueError):
                sources(value)
        for value in ({'web_region': 'XX'}, {'web_language': 'ja'}, {'news_locale': 'US:xx'}, {'unknown': 'x'}, {'web_pages': 12}):
            with self.assertRaises(ValueError):
                options(value)

    def test_region_reaches_transport_and_cache_identity(self):
        c = dict(id=str(uuid4()), query='q', proxy_profile='direct', serp_options={'web_region': 'GB', 'web_language': 'zh', 'news_locale': 'SG:zh-Hans'})
        d = make_discovery(Config(), Mock(), Mock(), c)
        self.assertEqual(d.transport.region, 'GB')
        self.assertEqual(d.transport.language, 'zh')
        self.assertIn(':GB', d.locale)
        self.assertEqual(d.task_options['news_locale'], 'SG:zh-Hans')
        d.close()

    def test_configured_source_rate_cap_reaches_shared_scheduler(self):
        c = dict(id=str(uuid4()), query='q', proxy_profile='direct')
        store = Mock()
        d = make_discovery(Config(google_web_max_rps=4),store,Mock(),c)
        d.source_slot_acquirer('google_openserp', 2)
        store.acquire_discovery_slot.assert_called_once_with('google_openserp',2,maximum_rps=4)
        d.close()

    def test_news_and_trends_use_task_editions(self):
        d = Mock(language='en', task_options={'news_locale': 'JP:ja', 'trends_geo': 'SG'}, should_continue=lambda: True)
        with patch('realtime.google_feeds.collect', return_value=([], [])) as fetch:
            collect_sources(d, Mock(), Mock(), str(uuid4()), ['a','b'], ['google_news','google_trends'], Config())
        self.assertEqual([(c.args[4], c.args[6]) for c in fetch.call_args_list], [('google_news','JP:ja'),('google_news','JP:ja'),('google_trends','SG')])
        d.should_continue = lambda: False
        with patch('realtime.google_feeds.collect') as fetch:
            collect_sources(d, Mock(), Mock(), str(uuid4()), ['a'], ['google_news'], Config())
        fetch.assert_not_called()

    def test_pause_prevents_web_request(self):
        c = dict(id=str(uuid4()), query='q', proxy_profile='direct')
        d = make_discovery(Config(), Mock(), Mock(), c)
        d.should_continue = lambda: False
        with patch.object(d.transport, 'fetch') as fetch:
            self.assertEqual(d.discover_many(('a','b'),1)[0], [])
        fetch.assert_not_called()
        d.close()


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class ConsoleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ['TEST_DATABASE_URL']
        self.schema = 'console_'+uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options=f'-c search_path={self.schema}')
        self.base = CampaignStore(self.dsn)
        self.config = Config(database_url=self.dsn, default_proxy_profile='direct', serp_continuous_enabled=True, serp_run_id='test-console', serp_keyword_offset=0, google_web_max_pages=1)
        self.store = ConsoleStore(self.dsn)
        self.store.initialize()
        self.store.initialize_console(self.config)
        self.cid = self.store.create_task({'keywords':['energy','research'], 'sources':['google_web','google_news','google_trends'], 'options':{'web_region':'GB','news_locale':'GB:en','trends_geo':'US'}, 'start':False}, self.config)['campaign_id']

    def tearDown(self):
        self.base.pool.close()
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def record(self, source, response, classification='results', cid=None):
        return self.store.record(cid or self.cid, source=source, provider='openserp' if source=='google_web' else 'google_rss', query='energy', page=1, language='en', response=response, elapsed_seconds=.1, classification=classification, http_status=200 if classification=='results' else 429, error_code=None if classification=='results' else 'blocked')

    def seed_results(self):
        self.web = self.record('google_web', {'organic_results':[dict(title='Solar "study"',link='https://example.org/a',snippet='clean energy',position=2)]})
        self.news = self.record('google_news', {'news_results':[dict(title='Energy news',link='https://example.org/a',publisher='Daily',feed_position=1)]})
        self.trends = self.record('google_trends', {'trending_searches':[dict(keyword='Energy topic',approx_traffic='20,000+',articles=[dict(title='New report',link='https://example.org/b')]),dict(keyword='No articles',articles=[])]})
        self.error = self.record('google_web', {}, 'captcha')

    def test_once_task_scope_budget_and_completion(self):
        cid = self.store.create_task({'keywords':['a','b'], 'sources':['google_web','google_news','google_trends'], 'options':{'web_pages':3,'news_locale':'US:en','trends_geo':'US'}, 'mode':'once', 'max_successful_searches':2, 'start':False}, self.config)['campaign_id']
        row = next(x for x in self.store.tasks()['tasks'] if str(x['id'])==cid)
        self.assertEqual((row['mode'],row['options']['web_pages'],row['max_successful_searches']),('once',3,2))
        self.store.action(cid,'start')
        self.seed_results()
        # Two successful persisted requests reach the task budget; reused cache is excluded.
        other = self.store.cache_feed('b'*64,self.news,600); self.store.cached_feed('b'*64,cid)
        from realtime.serp_worker import complete_once_task
        complete_once_task(self.store,cid,'max_successful_searches')
        row = next(x for x in self.store.tasks(status='completed')['tasks'] if str(x['id'])==cid)
        self.assertEqual((row['status'],row['stop_reason'],row['cached']),('completed','max_successful_searches',1))
        self.assertGreaterEqual(row['successful'],1)
        with self.assertRaises(ValueError): self.store.action(cid,'start')

    def test_once_task_scope_without_budget_completes_after_clean_run(self):
        cid = self.store.create_task({'query':'a','sources':['google_trends'], 'mode':'once'}, self.config)['campaign_id']
        self.store.record(cid, source='google_trends', provider='google_rss', query='a', page=1, language='en', response={'trending_searches':[]}, elapsed_seconds=.1, classification='empty', http_status=200)
        from realtime.serp_worker import complete_once_task
        complete_once_task(self.store,cid,'scope_completed')
        row = next(x for x in self.store.tasks()['tasks'] if str(x['id'])==cid)
        self.assertEqual((row['status'],row['stop_reason']),('completed','scope_completed'))

    def test_draft_start_pause_resume_stop_and_invalid_transition(self):
        self.assertFalse(self.store.runnable(self.cid))
        for op, state in [('start','active'),('pause','paused'),('resume','active'),('stop','stopped'),('start','active')]:
            self.assertEqual(self.store.action(self.cid,op)['status'],state)
            self.assertEqual(self.store.runnable(self.cid),state=='active')
        with self.assertRaises(ValueError):
            self.store.action(self.cid,'resume')
        self.assertIsNone(self.store.action(str(uuid4()),'start'))
        row = self.store.tasks()['tasks'][0]
        self.assertEqual(row['aliases'], ['research'])
        self.assertEqual(row['options']['web_region'],'GB')

    def test_legacy_article_tasks_cannot_be_reactivated_from_console(self):
        old = self.base.create_campaign('legacy body feed', [], 100, 'direct', ['rss'])
        self.assertEqual(self.store.tasks(kind='all')['total'], 1)
        self.assertIsNone(self.store.action(old, 'pause'))

    def test_settings_persist_seed_once_and_apply_to_worker(self):
        self.store.save_settings(dict(continuous_enabled=False, web_max_pages=3, news_interval_seconds=600, trends_interval_seconds=120, continuous_sources=['google_news'], web_region='SG'),self.config)
        self.store.initialize_console(replace(self.config,serp_continuous_enabled=True))
        s = ConsoleStore(self.dsn).settings(self.config)
        self.assertFalse(s['continuous_enabled'])
        self.assertFalse(s['content_fetch_enabled'])
        c = self.store.effective_config(self.config)
        self.assertEqual(c.google_web_max_pages,3)
        self.assertEqual(c.google_news_base_interval_seconds,600)
        self.assertEqual(c.serp_continuous_sources,('google_news',))
        self.assertEqual(self.store.task_options(self.cid)['web_region'],'GB')
        for invalid in ({'continuous_enabled':'false'},{'web_max_pages':0},{'news_interval_seconds':False},{'news_locales':[]},{'continuous_sources':['rss']},{'continuous_keywords':['']},{'content_fetch_enabled':1},{'junk':True}):
            with self.assertRaises(ValueError):
                self.store.save_settings(invalid,self.config)

    def test_global_continuous_switch_gates_supply_but_not_manual_tasks(self):
        self.store.save_settings({'continuous_keywords':['my energy'], 'continuous_sources':['google_news']},self.config)
        q = Mock()
        SerpSupply(self.config,self.base,q).tick()
        tasks = self.store.tasks(kind='automatic',limit=1)['tasks']
        self.assertEqual(tasks[0]['query'],'my energy')
        self.assertEqual(tasks[0]['sources'],['google_news'])
        auto = str(tasks[0]['id'])
        self.store.action(self.cid,'start')
        self.store.save_settings({'continuous_enabled':False,'continuous_sources':['google_trends']},self.config)
        self.assertFalse(self.store.runnable(auto))
        self.assertTrue(self.store.runnable(self.cid))
        q.reset_mock()
        SerpSupply(self.config,self.base,q).tick()
        q.enqueue.assert_not_called()
        self.store.save_settings({'continuous_enabled':True},self.config)
        self.assertTrue(self.store.runnable(auto))
        self.assertEqual(self.base.campaign(auto)['sources'],['google_trends'])

    def test_supervisor_compare_and_set_preserves_operator_stop_and_other_settings(self):
        from scripts.google_web_longrun import CONTROL
        self.store.save_settings({'continuous_enabled':False,'news_interval_seconds':600},self.config)
        prior=str(self.store.settings(self.config)['updated_at'])
        # A second website stop, even when already off, invalidates a pending resume.
        self.store.save_settings({'continuous_enabled':False},self.config)
        args=[sys.executable,'-c',CONTROL,json.dumps({'continuous_enabled':True}),prior]
        child=subprocess.run(args,env=dict(os.environ,DATABASE_URL=self.dsn),capture_output=True)
        self.assertNotEqual(child.returncode,0)
        self.assertFalse(self.store.settings(self.config)['continuous_enabled'])
        current=str(self.store.settings(self.config)['updated_at'])
        args[-1]=current
        child=subprocess.run(args,env=dict(os.environ,DATABASE_URL=self.dsn),capture_output=True)
        self.assertEqual(child.returncode,0)
        saved=self.store.settings(self.config)
        self.assertTrue(saved['continuous_enabled'])
        self.assertFalse(saved['content_fetch_enabled'])
        self.assertEqual(saved['news_interval_seconds'],600)

    def test_supervisor_snapshot_reads_existing_serp_schema_and_global_url_registry(self):
        from scripts.google_web_longrun import SNAPSHOT
        self.seed_results()
        with self.store.store.connect() as db:
            canonical=db.execute("SELECT u.* FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE r.source='google_web' AND NOT u.historical LIMIT 1").fetchone()
            alias=canonical['normalized_url']+('&' if '?' in canonical['normalized_url'] else '?')+'srsltid=legacy'
            db.execute('INSERT INTO serp_urls(url_hash,normalized_url,first_request_id,canonical_url_hash) VALUES(%s,%s,%s,%s)',(hashlib.sha256(alias.encode()).hexdigest(),alias,canonical['first_request_id'],canonical['url_hash']))
        start=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        child=subprocess.run([sys.executable,'-c',SNAPSHOT,start],env=dict(os.environ,DATABASE_URL=self.dsn,VALKEY_URL='redis://127.0.0.1:1/0'),capture_output=True,text=True)
        self.assertEqual(child.returncode,0,child.stderr)
        data=json.loads(child.stdout)
        self.assertEqual(data['new_links'],1)
        self.assertEqual(sum(x['n'] for x in data['hourly']),1)
        self.assertTrue(any(x['n']==0 for x in data['hourly']))
        self.assertGreaterEqual(sum(float(x['elapsed_seconds']) for x in data['hourly']),7200)
        self.assertEqual(sum(x['attempts'] for x in data['requests']),4)
        self.assertEqual(data['inflight'],0)
        # Retrospective repairs must use the original cutoff, not include a
        # neighbouring stage; this window ends before all fixture requests.
        end=(datetime.fromisoformat(start)+timedelta(hours=1)).isoformat()
        child=subprocess.run([sys.executable,'-c',SNAPSHOT,start,end],env=dict(os.environ,DATABASE_URL=self.dsn,VALKEY_URL='redis://127.0.0.1:1/0'),capture_output=True,text=True)
        self.assertEqual(child.returncode,0,child.stderr)
        bounded=json.loads(child.stdout)
        self.assertEqual(datetime.fromisoformat(bounded['at']),datetime.fromisoformat(end))
        self.assertEqual(bounded['new_links'],0)
        self.assertEqual(sum(x['attempts'] for x in bounded['requests']),0)
        self.assertAlmostEqual(sum(float(x['elapsed_seconds']) for x in bounded['hourly']),3600)

    def test_supervisor_page_decrease_completes_only_unleased_finished_supply_frontiers(self):
        from scripts.google_web_longrun import CONTROL
        self.store.save_settings({'web_max_pages':2},self.config)
        run='google-serp-20261010'
        with self.base.connect() as db:
            db.execute('INSERT INTO serp_supply_runs(run_id,keyword_offset) VALUES(%s,0)',(run,))
        ids={}
        for index,kind in enumerate(('finished','manual','unfinished','leased','stopped')):
            cid=self.base.create_campaign('page switch '+kind,[],1,'direct',['google_web'])
            ids[kind]=cid
            if kind!='manual':
                with self.base.connect() as db:
                    db.execute('INSERT INTO serp_supply_campaigns(run_id,campaign_id,sequence) VALUES(%s,%s,%s)',(run,cid,index))
            batch=self.base.acquire_google_page_batch(cid,'page switch','en:serp-v1',2,1,21600)
            if kind=='unfinished':
                self.base.record_google_page_result(batch,1,success=False,error='google_query_inflight')
            else:
                self.assertTrue(self.base.record_google_page_result(batch,1,success=True,result_count=7))
            if kind=='leased':
                self.base.acquire_google_page_batch(cid,'page switch','en:serp-v1',2,1,21600)
            if kind=='stopped':
                with self.base.connect() as db:
                    db.execute("UPDATE campaigns SET status='stopped' WHERE id=%s",(cid,))
        def rows():
            with self.base.connect() as db:
                return {str(r['campaign_id']):r for r in db.execute('SELECT * FROM google_page_frontier').fetchall()}
        before=rows()
        stale=str(self.store.settings(self.config)['updated_at'])
        self.store.save_settings({'continuous_enabled':False},self.config)
        args=[sys.executable,'-c',CONTROL,json.dumps({'web_max_pages':1}),stale]
        env=dict(os.environ,DATABASE_URL=self.dsn)
        child=subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertNotEqual(child.returncode,0)
        self.assertEqual(rows()[ids['finished']]['state'],'pending')
        args[-1]=str(self.store.settings(self.config)['updated_at'])
        child=subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertEqual(child.returncode,0,child.stderr)
        after=rows();finished=after[ids['finished']]
        self.assertEqual((finished['state'],finished['next_page'],finished['max_page']),('completed',2,1))
        self.assertEqual(finished['page_stats'],before[ids['finished']]['page_stats'])
        self.assertIsNone(self.base.acquire_google_page_batch(ids['finished'],'page switch','en:serp-v1',1,1,21600))
        for kind in ('manual','unfinished','leased','stopped'):
            self.assertEqual(after[ids[kind]],before[ids[kind]],kind)
        self.assertEqual(json.loads(child.stdout)['frontier_pages_reconciled'],1)
        self.assertFalse(self.store.settings(self.config)['continuous_enabled'])

    def test_results_filters_pagination_export_and_topic_without_links(self):
        self.seed_results()
        self.assertEqual(self.store.browse({})['total'],4)
        self.assertEqual(self.store.browse({'source':'google_news'})['total'],1)
        self.assertEqual(self.store.browse({'new_only':'true'})['total'],2)
        self.assertEqual(self.store.browse({'q':'clean energy'})['total'],1)
        self.assertEqual(self.store.browse({'campaign_id':self.cid,'q':'No articles'})['total'],1)
        self.assertEqual(self.store.browse({'since':'2099-01-01'})['total'],0)
        page = self.store.browse({'offset':1,'limit':1})
        self.assertEqual((len(page['rows']),page['total']),(1,4))
        exported = list(self.store.export({'source':'google_trends'}))
        self.assertEqual(len(exported),2)
        self.assertEqual(exported[1]['data']['articles'],[])
        self.assertEqual(self.store.browse({'status':'error'},'requests')['total'],1)
        self.assertEqual(len(list(self.store.export({'campaign_id':self.cid},'requests'))),4)

    def test_fast_pages_match_full_results_across_requests_and_empty_topics(self):
        self.seed_results()
        self.record('google_news', {'news_results': []}, 'empty')
        # A page boundary can fall inside a response, after empty/error requests.
        for filters in ({}, {'source':'google_trends'}, {'campaign_id':self.cid},
                        {'q':'clean energy'}, {'new_only':'true'}, {'since':'2099-01-01'}):
            expected = self.store.browse(filters)['rows']
            pages = []
            for offset in range(len(expected)+1):
                page = self.store.browse(dict(filters, count_total='false', limit=1, offset=offset))
                self.assertIsNone(page['total'])
                self.assertEqual(page['has_more'], offset+1 < len(expected))
                self.assertEqual(page['rows'], expected[offset:offset+1])
                pages.extend(page['rows'])
            self.assertEqual(pages, expected)
        page = self.store.browse({'count_total':'false', 'limit':2}, 'requests')
        self.assertEqual(len(page['rows']), 2)
        self.assertTrue(page['has_more'])
        self.assertIsNone(page['total'])

    def test_overview_and_cached_task_contributions_do_not_double_count(self):
        self.seed_results()
        second = self.store.create_task({'sources':['google_trends']},self.config)['campaign_id']
        self.store.cache_feed('a'*64,self.trends,600)
        self.store.cached_feed('a'*64,second)
        stats = self.store.overview()
        self.assertEqual(stats['requests']['attempts'],4)
        self.assertEqual(stats['requests']['failed'],1)
        self.assertEqual(stats['requests']['success_rate'],.75)
        self.assertEqual(sum(x['first_discovery_contribution'] for x in stats['sources'].values()),2)
        row = next(r for r in self.store.tasks()['tasks'] if str(r['id'])==second)
        self.assertEqual((row['cached'],row['new_links']),(1,0))
        self.assertEqual(stats['recent_errors'][0]['classification'],'captcha')

    def test_web_cache_reuse_remains_visible_on_each_task_without_new_request(self):
        q = 'shared web-cache query'
        first = self.store.create_task({'query':q,'sources':['google_web'],'options':{'web_region':'GB'}},self.config)['campaign_id']
        second = self.store.create_task({'query':q,'sources':['google_web'],'options':{'web_region':'GB'}},self.config)['campaign_id']
        item = dict(title='Shared result',link='https://example.org/shared',position=1)
        c = self.base.campaign(first);c['serp_options'] = self.store.task_options(first)
        d = make_discovery(self.config,self.base,self.store,c)
        d.transport.local.last_serp_response = {'organic_results':[item]}
        with patch.object(d,'_reserve_source'), patch.object(d.transport,'fetch',return_value=[SearchResult(item['link'],item['title'],('google_web',),item)]):
            self.assertEqual(len(d._discover_google_page(q,1)),1)
        d.close()
        c = self.base.campaign(second);c['serp_options'] = self.store.task_options(second)
        d = make_discovery(self.config,self.base,self.store,c)
        with patch.object(d.transport,'fetch',side_effect=AssertionError('must reuse persisted response')):
            self.assertEqual(len(d._discover_google_page(q,1)),1)
        d.close()
        self.assertEqual(self.store.browse({'campaign_id':second},'requests')['total'],1)
        row = next(x for x in self.store.tasks()['tasks'] if str(x['id'])==second)
        self.assertEqual((row['cached'],row['new_links']),(1,0))
        self.assertEqual(self.store.overview()['requests']['attempts'],1)

    def test_actual_http_task_settings_filters_json_and_export(self):
        self.seed_results()
        config, queue = self.config, Mock()
        class H(BaseHTTPRequestHandler):
            def send_json(self,d,status=200):
                b=json.dumps(d,default=str).encode();self.send_response(status);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            def do_GET(self):
                if not get(self): self.send_json({'error':'not found'},404)
            def do_POST(self):
                if not post(self): self.send_json({'error':'not found'},404)
            def log_message(self,*args): pass
        H.config,H.queue=config,queue
        server=ThreadingHTTPServer(('127.0.0.1',0),H)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def call(path,body=None):
            req=Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with urlopen(req) as r: return r.status,r.read()
        try:
            _, body=call('/api/collector-settings');self.assertFalse(json.loads(body)['content_fetch_enabled'])
            _, body=call('/api/collector-settings',{'continuous_enabled':False});self.assertFalse(json.loads(body)['continuous_enabled'])
            status, body=call('/api/serp/tasks',{'keywords':['http keyword'],'sources':['google_news'],'options':{'news_locale':'SG:en'},'start':False});self.assertEqual(status,201)
            cid=json.loads(body)['campaign_id'];queue.enqueue.assert_not_called()
            call('/api/serp/tasks/'+cid+'/start',{});queue.enqueue.assert_called_once_with(cid,priority=True)
            for op in ('pause','resume','stop'): call('/api/serp/tasks/'+cid+'/'+op,{})
            _, body=call('/api/serp/results?source=google_news');self.assertEqual(json.loads(body)['total'],1)
            _, body=call('/api/serp/results?count_total=false&limit=1')
            fast=json.loads(body);self.assertIsNone(fast['total']);self.assertTrue(fast['has_more']);self.assertEqual(len(fast['rows']),1)
            _, body=call('/api/serp/export?mode=requests&format=jsonl');self.assertEqual(len(body.splitlines()),4)
            _, body=call('/api/serp/export?source=google_trends&format=csv');self.assertIn(b'json',body);self.assertIn(b'20,000+',body)
            with self.assertRaises(HTTPError) as e: call('/api/collector-settings',{'web_max_pages':99})
            self.assertEqual(e.exception.code,400)
        finally:
            server.shutdown();server.server_close();thread.join()
