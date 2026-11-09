import io
import json
import os
import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore
from realtime.config import Config
from realtime.product import ProductError, ProductStore, ProductService, compare_snapshots, domain_name, parameters, provision_admin_key, admin_secret
from realtime.product_http import handle, openapi
from realtime.serp_console import ConsoleStore


class ProductUnitTests(unittest.TestCase):
    def test_admin_key_provision_is_private_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'SEARCHFLOW_ADMIN_KEY_FILE':tmp+'/private/key','SEARCHFLOW_ADMIN_KEY':''}):
            p=provision_admin_key();first=p.read_text()
            self.assertTrue(first.startswith('sf_admin_'))
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            provision_admin_key();self.assertEqual(p.read_text(),first)
            self.assertEqual(admin_secret(),first.strip())

    def test_domain_and_search_scope(self):
        self.assertEqual(domain_name('https://www.Example.com/path'),'example.com')
        for host in ('127.0.0.1','https://user:pass@example.com','localhost','bad_domain.com','example.com:80'):
            with self.assertRaises(ProductError):domain_name(host)
        for p in ({'query':'q','device':'mobile'}, {'query':'q','source':'google_news','region':'JP'}, {'query':''}):
            with self.assertRaises(ProductError):parameters(p)
        self.assertEqual(parameters({'query':' q '})['query'],'q')
        self.assertIn('/v1/search',openapi()['paths'])

    def test_changes_cache_dedup_and_unknown_ranks(self):
        def snap(i,rs):return dict(id=str(i),completed_at=f'2026-10-{i:02d}',provider='test',response={'organic_results':rs})
        latest=snap(2,[dict(link='https://a.com/?utm_source=x',position=2),dict(link='https://unknown.com')])
        old=snap(1,[dict(link='https://a.com/',position=5),dict(link='https://b.com/',position=1)])
        d=compare_snapshots([latest,latest,old],['a.com','b.com','unknown.com'])
        self.assertEqual(d['samples'],2)
        self.assertEqual(next(x for x in d['changes'] if x['kind']=='moved')['delta'],3)
        self.assertEqual(d['domains'][1]['eligible_samples'],1) # unknown ranks cannot prove absence
        self.assertEqual(d['domains'][2]['latest_rank'],None)
        self.assertEqual(d['result_overlap'],0.3333)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class ProductIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='product_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn=make_conninfo(self.root,options=f'-c search_path={self.schema}')
        self.base=CampaignStore(self.dsn)
        self.config=Config(database_url=self.dsn,default_proxy_profile='direct')
        self.console=ConsoleStore(self.dsn);self.console.initialize();self.console.initialize_console(self.config)
        self.store=ProductStore(self.dsn);self.store.initialize();self.store.initialize()
        self.env=patch.dict(os.environ,{'SEARCHFLOW_PRODUCT_ENABLED':'true','SEARCHFLOW_ADMIN_KEY':'test-admin'});self.env.start()
        self.account=self.store.create_account(dict(name='one',monthly_limit=2,concurrency=2))
        self.owner=self.store.authenticate(self.account['api_key'],rate_limit=False)
        self.other=self.store.create_account(dict(name='two',monthly_limit=5))

    def tearDown(self):
        self.env.stop();self.base.pool.close()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def submit(self,key='one',p=None):return self.store.submit(self.owner,p or {'query':'private query'},key,self.config)[0]

    def test_atomic_quota_and_idempotency(self):
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:self.submit(),range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.store.usage(self.owner['id'])['reserved'],1)
        with self.assertRaises(ProductError) as err:self.submit(p={'query':'different'})
        self.assertEqual(err.exception.code,'idempotency_conflict')
        second=self.submit('second')
        with self.assertRaises(ProductError):self.submit('third')
        self.store.finish(ids[0],'succeeded',{'data':{},'fetched_at':'2026-10-27','provider':'test'})
        self.assertFalse(self.store.finish(ids[0],'succeeded',{}))
        self.store.finish(second,'failed',error='upstream_unavailable')
        u=self.store.usage(self.owner['id']);self.assertEqual((u['used'],u['reserved'],u['remaining']),(1,0,1))
        self.submit('after-refund')

    def test_ownership_revocation_and_public_task_isolation(self):
        jid=self.submit()
        for operation in (lambda:self.store.job(self.other['account_id'],jid),lambda:self.store.cancel(self.other['account_id'],jid)):
            with self.assertRaises(ProductError) as err:operation()
            self.assertEqual(err.exception.status,404)
        self.assertEqual(self.console.tasks(kind='all')['tasks'],[])
        key=self.store.issue_key(self.owner['id'],'app');self.store.revoke(self.owner['id'],key['id'])
        with self.assertRaises(ProductError):self.store.authenticate(key['api_key'])
        self.assertEqual(self.store.job(self.owner['id'],jid)['status'],'queued')

    def test_monitor_paused_default_and_stop_pending(self):
        m=self.store.create_monitor(self.owner['id'],dict(query='test',domain='example.com',competitors=['other.com']))
        self.assertFalse(m['enabled'])
        service=ProductService(self.config,Mock());service.tick()
        self.assertEqual(self.store.jobs(self.owner['id']),[])
        self.store.monitor_action(self.owner['id'],m['id'],True);service.tick()
        self.assertEqual(len(self.store.jobs(self.owner['id'])),1)
        self.store.monitor_action(self.owner['id'],m['id'],False)
        self.assertEqual(self.store.jobs(self.owner['id'])[0]['status'],'cancelled')
        p=self.store.monitors(self.owner['id'])[0]['parameters']
        with self.assertRaises(ProductError):self.store.submit(self.owner,p,'stale-scheduler',self.config,m['id'])
        self.assertEqual(self.store.usage(self.owner['id'])['reserved'],0)

    def test_reconcile_success_freezes_result_and_hides_public_data(self):
        jid=self.submit()
        with self.base.connect() as db:cid=db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s',(jid,)).fetchone()['campaign_id']
        self.console.record(str(cid),source='google_web',provider='openserp',query='private query',page=1,language='en',response={'organic_results':[{'title':'Private','link':'https://example.com/','position':2}]},elapsed_seconds=.1,classification='results',http_status=200)
        ProductService(self.config,Mock()).tick()
        result=self.store.job(self.owner['id'],jid)
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['charged'],1)
        self.assertEqual(result['response']['data']['organic_results'][0]['position'],2)
        self.assertEqual(self.console.browse({},'requests')['rows'],[])
        self.assertEqual(self.store.history_analysis()['samples'],0)

    def test_finished_collector_failure_releases_quota_immediately(self):
        jid=self.submit()
        with self.base.connect() as db:
            cid=db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s',(jid,)).fetchone()['campaign_id']
            db.execute("UPDATE campaigns SET status='completed' WHERE id=%s",(cid,))
        ProductService(self.config,Mock()).tick()
        self.assertEqual(self.store.job(self.owner['id'],jid)['status'],'failed')
        self.assertEqual(self.store.usage(self.owner['id'])['reserved'],0)

    def test_platform_request_cap_counts_failures_but_not_idempotent_replay(self):
        jid=self.submit()
        self.store.finish(jid,'failed',error='test_failure')
        with self.base.connect() as db:
            db.execute('UPDATE sf_platform_months SET accepted=100000')
        self.assertEqual(self.submit(),jid)
        with self.assertRaises(ProductError) as err:self.submit('over-platform-cap')
        self.assertEqual(err.exception.code,'platform_request_budget')
        self.assertEqual(self.store.usage(self.owner['id'])['reserved'],0)

    def test_platform_budget_and_rate_limit(self):
        with self.assertRaises(ProductError):self.store.create_account(dict(name='over',monthly_limit=100000))
        # PostgreSQL now() stays fixed within this transaction, so a wall-clock
        # minute rollover cannot split the 61 requests into different windows.
        with self.store.base.connect() as db:
            db.execute("SELECT now()")

            @contextmanager
            def fixed_window():
                yield db

            with patch.object(self.store.base, "connect", fixed_window):
                for _ in range(60):
                    self.store.authenticate(self.account['api_key'])
                with self.assertRaises(ProductError) as err:
                    self.store.authenticate(self.account['api_key'])
                self.assertEqual(err.exception.status, 429)
            count = db.execute(
                "SELECT requests FROM sf_rate_windows WHERE account_id=%s",
                (self.owner['id'],)).fetchone()['requests']
            self.assertEqual(count, 61)
            db.execute(
                "UPDATE sf_rate_windows SET minute=minute-interval '2 minutes' "
                "WHERE account_id=%s", (self.owner['id'],))
        self.store.authenticate(self.account['api_key'])
        with self.store.base.connect() as db:
            windows = db.execute(
                "SELECT requests FROM sf_rate_windows WHERE account_id=%s ORDER BY minute DESC",
                (self.owner['id'],)).fetchall()
        self.assertEqual([row['requests'] for row in windows], [1, 61])

    def test_http_search_export_errors_and_transport(self):
        config=self.config
        class Handler(BaseHTTPRequestHandler):
            def send_body(self,b,ctype,status=200):
                self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            def send_json(self,p,status=200):self.send_body(json.dumps(p,default=str).encode(),'application/json',status)
            def do_GET(self):handle(self,'GET')
            def do_POST(self):handle(self,'POST')
            def log_message(self,*args):pass
        Handler.config=config
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def req(path,p=None,key=None,headers=None):
            h={'Authorization':'Bearer '+(key or self.account['api_key']),'Content-Type':'application/json',**(headers or {})}
            r=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(p).encode() if p is not None else None,headers=h)
            try:
                with urlopen(r,timeout=10) as response:return response.status,response.read()
            except HTTPError as e:return e.code,e.read()
        try:
            status,raw=req('/v1/search',{'query':'q'},headers={'Idempotency-Key':'http-one'})
            self.assertEqual(status,202);jid=json.loads(raw)['id']
            self.assertEqual(req('/v1/jobs/'+jid,key=self.other['api_key'])[0],404)
            self.assertEqual(req('/v1/jobs/'+jid+'/export')[0],409)
            self.assertEqual(req('/v1/usage',headers={'X-Forwarded-For':'1.2.3.4','X-Forwarded-Proto':'http'})[0],403)
            self.assertEqual(req('/v1/usage',headers={'X-Forwarded-For':'1.2.3.4','X-Forwarded-Proto':'https'})[0],200)
            self.assertEqual(req('/api/product/admin/accounts',{},key=self.account['api_key'])[0],401)
            self.assertEqual(req('/v1/search',{'query':'q'})[0],400)
            self.assertEqual(req('/api/collector-settings',{'continuous_enabled':True})[0],401)
            self.assertEqual(req('/api/stats')[0],410)
            self.store.finish(jid,'succeeded',{'data':{'organic_results':[dict(title='=1+1',link='https://example.com')]},'fetched_at':'2026-10-27','provider':'test'})
            status,raw=req('/v1/jobs/'+jid+'/export?format=csv');self.assertEqual(status,200);self.assertIn(b"'=1+1",raw)
            status,raw=req('/api/serp/requests');self.assertEqual(status,200)
        finally:server.shutdown();server.server_close();thread.join()
