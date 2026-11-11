"""Opt-in standalone backend. No collection coordinator or legacy migrations."""
from __future__ import annotations

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
from urllib.parse import urlparse

from .insights import InsightService
from .insights_core import InsightError, dumps
from .insights_http import handle
from .insights_source import SavedSearchSource
from .product import ProductError
from .google_domain import DomainError


class InsightHandler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'

    def log_message(self,*args):
        pass

    def send_bytes(self,raw,mime,filename=None,status=200):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"sandbox; default-src 'none'; style-src 'unsafe-inline'")
        if filename:
            self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(raw)

    def send_json(self,value,status=200):
        self.send_bytes(dumps(value).encode(),'application/json; charset=utf-8',status=status)

    def execute(self,method):
        try:
            self.connection.settimeout(30)
            if urlparse(self.path).path=='/health' and method=='GET':
                self.send_json({'status':'ok','worker_alive':self.server.insights.thread.is_alive()});return
            if self.client_address[0] not in ('127.0.0.1','::1'):
                raise InsightError('forbidden','仅接受本机反向代理',403)
            auth=self.headers.get('Authorization','')
            account=self.server.source.store.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
            handle(self,method,self.server.insights,str(account['id']))
        except (InsightError,ProductError,DomainError) as e:
            self.send_json({'error':str(e),'code':e.code},e.status)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            self.send_json({'error':'文本分析服务暂不可用，请刷新核对后重试','code':'service_unavailable'},503)

    def do_GET(self):self.execute('GET')
    def do_POST(self):self.execute('POST')
    def do_DELETE(self):self.execute('DELETE')


def serve():
    path=Path(os.environ['SEARCHFLOW_INSIGHTS_DB'])
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=open(str(path)+'.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    source=SavedSearchSource(os.environ['DATABASE_URL'])
    service=InsightService(path,source_loader=source.load,source_validator=source.validate)
    server=ThreadingHTTPServer(('127.0.0.1',int(os.getenv('SEARCHFLOW_INSIGHTS_PORT','18098'))),InsightHandler)
    server.source=source;server.insights=service
    def stop(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever(poll_interval=.5)
    finally:service.close();server.server_close();lock.close()


if __name__=='__main__':serve()
