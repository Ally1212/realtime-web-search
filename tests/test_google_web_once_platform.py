from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from realtime.google_data import digest
from realtime.google_domain import DomainError
from realtime.google_serp_delivery import saved_web_run_message,with_search_snapshot_text
from realtime.google_whale_readback import ReadbackTarget
from realtime.google_web_once_platform import SingleRunPlatform,PlatformCallError


class PlatformHTTPTests(unittest.TestCase):
    def setUp(self):
        row=dict(request_id=str(uuid4()),source='google_web',query='test',page=1,
            classification='results',completed_at=datetime.now(timezone.utc),
            response={'organic_results':[{'link':'https://example.org','title':'Saved'}]})
        self.message=with_search_snapshot_text(saved_web_run_message(row,dataset_id='dataset',source_name='source',collector_id='collector'))
        self.calls=[];self.mode='normal';owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.handle_request()
            def do_POST(self):self.handle_request()
            def handle_request(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
                owner.calls.append(dict(path=self.path,method=self.command,authorization=self.headers.get('Authorization'),body=body))
                if owner.mode in {'redirect','limited','server_error'}:
                    code={'redirect':302,'limited':429,'server_error':503}[owner.mode]
                    self.send_response(code);self.send_header('Location','http://127.0.0.1:1/stolen')
                    self.send_header('Retry-After','30');self.end_headers();return
                if self.path=='/v1/documents/validate':
                    result=dict(dry_run=True,valid=True,results=[dict(index=0,valid=True)])
                    if owner.mode=='invalid':result['results'][0]['valid']=False
                    if owner.mode=='malformed':result['results']=[None]
                elif self.path=='/v1/documents/bulk':
                    result=[dict(receipt_status='queued',document_id='doc_one',version_id='ver_one')]
                    if owner.mode=='partial':result.append(result[0])
                elif self.path=='/v1/ingest/receipts':
                    result=dict(receipts=[dict(**body[0],status='accepted',document_id='doc_one',version_id='ver_one')])
                    if owner.mode=='other_event':result['receipts'][0]['source_record_key']='other'
                elif self.path=='/v1/documents/doc_one':
                    result=dict(document=dict(id='doc_one',dataset_id=owner.message['dataset_id'],source_platform='google_search',external_content_id=owner.message['content']['external_content_id']))
                elif self.path=='/v1/versions/batch-get':
                    payload=owner.message['content']['platform_payload']
                    result=dict(versions=[dict(document_id='doc_one',version_id='ver_one',platform_payload=payload,
                        version=dict(document_id='doc_one',version_id='ver_one',raw_payload_hash=digest(payload)))])
                    if owner.mode=='other_version':result['versions'][0]['version_id']='other'
                else:self.send_error(404);return
                raw=json.dumps(result).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=SingleRunPlatform(f'http://127.0.0.1:{self.server.server_port}',writer_key='writer-local-test',consumer_key='consumer-local-test',allow_loopback=True)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()

    def test_writer_and_independent_pinned_consumer(self):
        self.assertTrue(self.client.validate(self.message))
        ack=self.client.bulk_once(self.message)
        receipt=self.client.receipt(self.message,ack)
        self.assertEqual(receipt['status'],'accepted')
        target=ReadbackTarget('doc_one','ver_one','dataset','google_search',expected_platform_payload_hash=self.message['source']['payload_hash'])
        proof=self.client.readback(target,self.message['content']['platform_payload'])
        self.assertTrue(proof['proof']['payload_roundtrip_verified'])
        self.assertEqual([c['authorization'] for c in self.calls],['Bearer writer-local-test']*3+['Bearer consumer-local-test']*2)
        self.assertEqual(self.calls[-1]['body'],{'items':[{'document_id':'doc_one','version_id':'ver_one'}],'include_body':False})
        self.assertEqual(self.calls[1]['body'],[self.message])

    def test_200_validation_is_not_automatically_valid(self):
        for mode in ('invalid','malformed'):
            self.mode=mode;self.assertFalse(self.client.validate(self.message))

    def test_redirect_or_rate_limit_or_503_never_retries(self):
        for mode in ('redirect','limited','server_error'):
            with self.subTest(mode=mode):
                self.mode=mode;before=len(self.calls)
                with self.assertRaises(PlatformCallError) as caught:self.client.bulk_once(self.message)
                self.assertEqual(len(self.calls),before+1)
                self.assertNotIn('writer-local-test',str(caught.exception))
                self.assertEqual(caught.exception.retry_after,'30')

    def test_partial_or_wrong_receipt_event_is_rejected(self):
        self.mode='partial'
        with self.assertRaises(PlatformCallError):self.client.bulk_once(self.message)
        self.mode='other_event'
        with self.assertRaises(ValueError):self.client.receipt(self.message,dict(document_id='doc_one',version_id='ver_one'))

    def test_wrong_fixed_version_is_rejected(self):
        self.mode='other_version'
        target=ReadbackTarget('doc_one','ver_one','dataset','google_search')
        with self.assertRaises(DomainError):self.client.readback(target,self.message['content']['platform_payload'])

    def test_invalid_origin_or_same_key_is_rejected_without_io(self):
        for origin in ('http://example.com','https://user:password@example.com','https://example.com/path','http://localhost'):
            with self.assertRaises(ValueError):SingleRunPlatform(origin,writer_key='w',consumer_key='r',allow_loopback=True)
        with self.assertRaises(ValueError):SingleRunPlatform('https://example.com',writer_key='same',consumer_key='same')
        self.assertEqual(self.calls,[])

    def test_controller_through_real_loopback_http_boundaries(self):
        from tests.test_google_web_once import FakeAdapter
        from realtime.google_web_once import WebOnce,web_plan
        owner=self
        class Adapter(FakeAdapter):
            def validate(self,message):
                owner.message=message
                return owner.client.validate(message)
            def bulk_once(self,message):return owner.client.bulk_once(message)
            def receipt(self,message,ack):return owner.client.receipt(message,ack)
            def readback(self,target,expected):return owner.client.readback(target,expected)
        adapter=Adapter()
        plan=web_plan(dataset_id='dataset-test',source_name='source',collector_id='collector')
        with tempfile.TemporaryDirectory() as td:
            runner=WebOnce(Path(td).resolve()/'journal',plan,adapter)
            state=runner.advance(authorized_plan_hash=runner.plan_hash)
            self.assertEqual(state['phase'],'verified')
            runner.advance(authorized_plan_hash=runner.plan_hash)
        self.assertEqual(adapter.calls,['source'])
        self.assertEqual(len(self.calls),5)


if __name__=='__main__':unittest.main()
