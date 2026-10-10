import json
import os
import threading
import unittest
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
from realtime.serp_worker import make_discovery, run_serp_campaign, SerpSupply
from realtime.serp_fields import result_items
from realtime.discovery import SearchResult
from realtime.serp_console_http import get, post
from realtime.google_feeds import collect_sources
from http.server import BaseHTTPRequestHandler


class ConsoleValidationTests(unittest.TestCase):
    def test_source_and_region_validation(self):
        for value in ([], ['rss'], ['google_web', {}], 'google_web'):
            with self.assertRaises(ValueError):
                sources(value)
        for value in ({'web_region': 'XX'}, {'web_language': 'ja'}, {'news_locale': 'US:xx'}, {'unknown': 'x'}):
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
            _, body=call('/api/serp/export?mode=requests&format=jsonl');self.assertEqual(len(body.splitlines()),4)
            _, body=call('/api/serp/export?source=google_trends&format=csv');self.assertIn(b'json',body);self.assertIn(b'20,000+',body)
            with self.assertRaises(HTTPError) as e: call('/api/collector-settings',{'web_max_pages':99})
            self.assertEqual(e.exception.code,400)
        finally:
            server.shutdown();server.server_close();thread.join()
