import json
import io
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import unittest

from realtime.insights import InsightService
from realtime.insights_server import InsightHandler
from realtime.product import ProductError
from realtime.insights_schema import openapi


class Auth:
    def authenticate(self,key):
        if key not in ('owner','other'):raise ProductError('unauthorized','invalid',401)
        return {'id':key}


class InsightHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=InsightService(Path(self.tmp.name)/'http.db',start_worker=False)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),InsightHandler)
        self.server.source=SimpleNamespace(store=Auth());self.server.insights=self.service
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.service.close();self.tmp.cleanup()
    def req(self,path,body=None,key='owner',headers=None,raw=None):
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req=Request(self.base+'/v1/insights'+path,data=data,headers={'Authorization':'Bearer '+key,**({'Content-Type':'application/json'} if data is not None else {}),**(headers or {})})
        try:
            with urlopen(req) as r:return r.status,r.read(),r.headers
        except HTTPError as e:return e.code,e.read(),e.headers
    def create(self):
        status,body,_=self.req('/datasets',{'name':'one','mode':'voc','records':[{'text_raw':'real text'}]},headers={'Idempotency-Key':'one'})
        self.assertEqual(status,201);return json.loads(body)['id']
    def test_auth_and_tenant_details_exports_isolated(self):
        self.assertEqual(self.req('/datasets',key='bad')[0],401)
        did=self.create()
        for suffix in ('','/records','/export?format=zip','/runs'):
            self.assertEqual(self.req('/datasets/'+did+suffix,key='other')[0],404)
        self.assertEqual(json.loads(self.req('/datasets',key='other')[1])['datasets'],[])
    def test_invalid_bodies_and_parameters(self):
        for raw in (b'{"name":"a","name":"b"}',b'{"value":NaN}',b'[]',b'{'):
            self.assertEqual(self.req('/datasets',raw=raw)[0],400)
        self.assertEqual(self.req('/datasets?scope=a&scope=b')[0],400)
        self.assertEqual(self.req('/capabilities?unknown=x')[0],400)
        self.assertEqual(self.req('/datasets',{'name':'one','records':[{'text_raw':'ok'}]})[0],400)
    def test_complete_export_headers_and_schema(self):
        did=self.create();status,raw,headers=self.req('/datasets/'+did+'/export?format=zip')
        self.assertEqual(status,200);self.assertTrue(raw.startswith(b'PK'))
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(self.req('/datasets/'+did+'/records?limit=0')[0],400)
        doc=json.loads(self.req('/openapi.json')[1]);self.assertEqual(doc,openapi())
        for p in doc['paths']:
            self.assertTrue(p.startswith('/v1/insights'))
    def test_revision_control_delete_never_removes_foreign(self):
        did=self.create()
        self.assertEqual(self.req('/datasets/'+did+'/delete',{'revision':1},key='other')[0],404)
        self.assertEqual(self.req('/datasets/'+did+'/delete',{'revision':2})[0],409)
        self.assertEqual(self.req('/datasets/'+did+'/delete',{'revision':1})[0],200)
        self.assertEqual(self.req('/datasets/'+did)[0],404)
