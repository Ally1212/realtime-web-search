from dataclasses import replace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit,parse_qs

from tests import test_whale_serp as fixture
from realtime.google_web_once import web_plan
from realtime.google_web_once_source import SingleWebSource,OneBackendSession


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class SingleSourceTests(unittest.TestCase):
    setUpDatabase=fixture.IntegrationTests.setUp
    tearDown=fixture.IntegrationTests.tearDown
    record=fixture.IntegrationTests.record

    def setUp(self):
        self.setUpDatabase()
        self.calls=[];self.mode='results';owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.calls.append(self.path)
                params=parse_qs(urlsplit(self.path).query)
                rid=self.headers['X-Request-ID']
                payload=dict(query=dict(text=params['text'][0],engines_requested=['google']),
                    meta=dict(engines_failed=[],request_id=rid,version='test-fixture'),
                    pagination=dict(page=1,has_more=False,next_start=10),
                    results=[dict(engine='google',type='organic',url='https://example.org/once',title='Actual saved title',description='Saved snippet',rank=1)])
                if hasattr(owner,'result_items'):payload['results']=owner.result_items
                status=200
                if owner.mode=='failure':status=503;payload=dict(error='fixture unavailable')
                if owner.mode=='redirect':status=302
                raw=json.dumps(payload).encode()
                self.send_response(status);self.send_header('X-Request-ID',rid);self.send_header('X-Cache','MISS');self.send_header('X-Proxy-Attempts','1')
                self.send_header('Location','http://127.0.0.1:1/no-follow');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.config=replace(self.config,openserp_url=f'http://127.0.0.1:{self.server.server_port}',content_fetch_enabled=False)
        self.plan=web_plan(dataset_id='test',source_name='test',collector_id='test')
        self.source=SingleWebSource(self.config,idle_guard=self.idle)

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def idle(self,plan):
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM campaigns WHERE status='active'").fetchone()['n'],0)
        self.assertFalse(self.console.settings(self.config)['continuous_enabled'])
        self.assertFalse(self.store.settings()['uploads_enabled'])
        self.assertFalse(self.store.settings()['claims_enabled'])

    def test_one_http_request_saved_and_campaign_never_active(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td).resolve()/'source-proof.json'
            self.source=SingleWebSource(self.config,idle_guard=self.idle,evidence_path=path)
            rid=self.source.acquire_once(self.plan)
            self.assertEqual(json.loads(path.read_text())['saved_request_ids'],[rid])
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        row=self.source.saved_public_request(rid)
        self.assertEqual(row['classification'],'results')
        self.assertEqual(row['query'],self.plan['query'])
        self.assertEqual(row['response']['organic_results'][0]['title'],'Actual saved title')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.source.evidence['backend_http_calls'],1)
        self.assertEqual(self.base.campaign(self.source.campaign_id)['status'],'paused')
        with self.assertRaises(ValueError):self.source.acquire_once(self.plan)
        self.assertEqual(len(self.calls),1)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM google_page_frontier WHERE lease_token IS NOT NULL').fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM discovery_query_leases').fetchone()['n'],0)

    def test_failure_is_saved_and_does_not_retry(self):
        self.mode='failure'
        rid=self.source.acquire_once(self.plan)
        self.assertNotEqual(self.source.saved_public_request(rid)['classification'],'results')
        self.assertEqual(len(self.calls),1)

    def test_opt_in_failure_capture_binds_private_evidence_to_saved_request(self):
        from datetime import datetime,timezone,timedelta
        from realtime.google_raw_capture import PrivateResponseVault
        self.mode='failure'
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();vault=PrivateResponseVault(root/'vault',key=b'v'*32)
            expiry=datetime.now(timezone.utc)+timedelta(hours=1)
            def capture(request_id,body,http_status):
                return vault.put(body,tenant_id='fixture-operator',request_id=request_id,
                    http_status=http_status,expires_at=expiry,authorization_ref='fixture-only')
            source=SingleWebSource(self.config,idle_guard=self.idle,
                evidence_path=root/'proof.json',raw_response_recorder=capture)
            rid=source.acquire_once(self.plan)
            ref=source.evidence['private_response_capture']
            self.assertEqual(ref['request_id'],rid)
            self.assertEqual(ref['http_status'],503)
            self.assertEqual(json.loads(vault.read(tenant_id='fixture-operator',request_id=rid))['error'],'fixture unavailable')
            self.assertNotIn('private_response_capture',source.saved_public_request(rid)['response'])
            self.assertEqual(len(self.calls),1)

    def test_duplicate_url_occurrences_survive_database_save(self):
        from realtime.google_data import normalize_request
        self.result_items=[dict(engine='google',type='organic',url='https://example.org/repeated',title='First',rank=1),
                           dict(engine='google',type='organic',url='https://example.org/repeated',title='Second',rank=2)]
        rid=self.source.acquire_once(self.plan)
        row=self.source.saved_public_request(rid)
        observation=normalize_request(row)
        self.assertEqual([r['title'] for r in observation['occurrences']],['First','Second'])
        self.assertEqual(len({r['occurrence_id'] for r in observation['occurrences']}),2)
        self.assertEqual(len({r['url']['url_id'] for r in observation['occurrences']}),1)
        self.assertEqual(row['response']['new_unique_links'],1)
        self.assertEqual(len(row['response']['upstream_response']['results']),2)
        self.assertEqual(len(self.calls),1)

    def test_redirect_is_not_followed(self):
        self.mode='redirect'
        rid=self.source.acquire_once(self.plan)
        self.assertNotEqual(self.source.saved_public_request(rid)['classification'],'results')
        self.assertEqual(len(self.calls),1)

    def test_operator_stop_during_pre_request_check_prevents_http(self):
        checks=[]
        def guard(plan):
            checks.append(True)
            if len(checks)>1:raise ValueError('stopped')
        self.source.idle_guard=guard
        with self.assertRaises(ValueError):self.source.acquire_once(self.plan)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.base.campaign(self.source.campaign_id)['status'],'paused')

    def test_full_controller_with_real_database_and_two_http_services(self):
        from tests.test_google_web_once_platform import PlatformHTTPTests
        from realtime.google_web_once import WebOnce
        platform_fixture=PlatformHTTPTests('test_writer_and_independent_pinned_consumer')
        platform_fixture.setUp()
        source=self.source
        class Adapter:
            assert_idle=source.assert_idle
            acquire_once=source.acquire_once
            saved_public_request=source.saved_public_request
            def validate(self,message):
                platform_fixture.message=message
                return platform_fixture.client.validate(message)
            def bulk_once(self,message):return platform_fixture.client.bulk_once(message)
            def receipt(self,message,ack):return platform_fixture.client.receipt(message,ack)
            def readback(self,target,expected):return platform_fixture.client.readback(target,expected)
        try:
            with tempfile.TemporaryDirectory() as td:
                runner=WebOnce(Path(td).resolve()/'journal',self.plan,Adapter())
                state=runner.advance(authorized_plan_hash=runner.plan_hash)
                self.assertEqual(state['phase'],'verified')
                self.assertEqual(state['occurrence_count'],1)
                runner.advance(authorized_plan_hash=runner.plan_hash)
            self.assertEqual(len(self.calls),1)
            self.assertEqual(len(platform_fixture.calls),5)
            self.assertEqual(self.base.campaign(source.campaign_id)['status'],'paused')
        finally:platform_fixture.doCleanups()


class SessionBudgetTests(unittest.TestCase):
    def test_other_path_or_method_rejected_before_network(self):
        with OneBackendSession('http://127.0.0.1:1') as session:
            for method,url in [('GET','http://127.0.0.1:1/other'),('POST',session.endpoint)]:
                with self.assertRaises(ValueError):session.request(method,url)
            self.assertEqual(session.calls,0)
