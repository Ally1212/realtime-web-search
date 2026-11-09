"""Standalone pre-access authentication for the private SearchFlow gateway."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

COOKIE = '__Host-searchflow_access'
TTL = 12 * 3600
LOGIN = '/_access/login'
PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>访问验证 · SearchFlow</title><style>*{box-sizing:border-box}body{margin:0;background:#f8fafd;color:#202124;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}main{width:100%;max-width:420px;background:white;border:1px solid #e3e6eb;border-radius:16px;padding:36px}small{color:#5f6368}.brand{font-size:22px;font-weight:650;color:#1967d2}h1{font-size:24px;margin:24px 0 8px}p{color:#5f6368;margin:0 0 26px}label{display:block;font-size:13px;margin-bottom:8px}input,button{width:100%;min-height:46px;border-radius:8px;font:inherit}input{border:1px solid #c4c7c5;padding:10px 12px}input:focus{outline:2px solid #1967d2;outline-offset:2px}button{border:0;color:white;background:#1967d2;margin-top:20px;cursor:pointer;font-weight:600}button:hover{background:#155bb7}small{display:block;margin-top:20px}.error{color:#b3261e;background:#fceeee;border-radius:8px;padding:10px;margin:0 0 18px;font-size:13px}</style><main><div class="brand">SearchFlow</div><h1>访问验证</h1><p>这是私人搜索数据工作台。请输入访问 Key 以继续。</p>ERROR<form method="post" action="/_access/login"><label for="accessKey">访问 Key</label><input id="accessKey" name="key" type="password" required maxlength="256" autocomplete="current-password" autofocus><button>进入工作台</button></form><small>验证后可访问 12 小时。请妥善保管 Key。</small></main></html>'''


class AccessGate:
    def __init__(self, config, clock=time.time):
        self.digest = config['key_sha256']
        if not re.fullmatch('[a-f0-9]{64}', self.digest):
            raise ValueError('invalid key digest')
        self.signer = bytes.fromhex(config['cookie_secret'])
        if len(self.signer) < 32:
            raise ValueError('cookie secret too short')
        self.origin = config['origin']
        self.clock = clock
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def valid_key(self, key):
        return isinstance(key, str) and len(key) <= 256 and hmac.compare_digest(
            hashlib.sha256(key.encode()).hexdigest(), self.digest)

    def issue_cookie(self):
        token = str(int(self.clock()) + TTL) + '.' + secrets.token_hex(16)
        return token + '.' + hmac.new(self.signer, token.encode(), hashlib.sha256).hexdigest()

    def authorized(self, key='', cookie=''):
        if key and self.valid_key(key):
            return True
        try:
            values = SimpleCookie()
            values.load(cookie)
            value = values[COOKIE].value
            if not re.fullmatch(r'\d{10}\.[a-f0-9]{32}\.[a-f0-9]{64}', value):
                return False
            stamp, nonce, signature = value.split('.')
            remaining = int(stamp) - self.clock()
            return 0 < remaining <= TTL and hmac.compare_digest(signature, hmac.new(
                self.signer, (stamp + '.' + nonce).encode(), hashlib.sha256).hexdigest())
        except (CookieError, KeyError, ValueError):
            return False

    def login(self, key, ip):
        """At most ten failed submissions per IP within five minutes."""
        with self.lock:
            now = self.clock()
            for address in list(self.attempts):
                q = self.attempts[address]
                while q and q[0] <= now - 300:
                    q.popleft()
                if not q:
                    del self.attempts[address]
            q = self.attempts[ip]
            if len(q) >= 10:
                return 429
            if self.valid_key(key):
                self.attempts.pop(ip, None)
                return 303
            q.append(now)
            return 401


def handler(gate):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Never log keys, cookies, or login request bodies.

        def respond(self, code, body=b'', content_type='application/json', headers=()):
            self.send_response(code)
            for k, v in [('Content-Type', content_type), ('Content-Length', str(len(body))),
                         ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                         ('Referrer-Policy', 'no-referrer'),
                         ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"), *headers]:
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def page(self, code=200, error=''):
            body = PAGE.replace('ERROR', '<p class="error" role="alert">'+error+'</p>' if error else '').encode()
            self.respond(code, body, 'text/html; charset=utf-8')

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/healthz':
                return self.respond(204)
            if path == '/check':
                return self.respond(204 if gate.authorized(self.headers.get('X-SearchFlow-Access-Key', ''), self.headers.get('Cookie', '')) else 401)
            if path == LOGIN:
                return self.page()
            if path == '/denied':
                if 'text/html' in self.headers.get('Accept', ''):
                    return self.respond(303, headers=[('Location', LOGIN)])
                return self.respond(401, b'{"error":"site_access_required","login_url":"/_access/login"}')
            return self.respond(404, b'{}')

        do_HEAD = do_GET

        def do_POST(self):
            path = self.path.split('?')[0]
            if self.headers.get('Origin') not in (None, gate.origin):
                return self.respond(403, b'{"error":"invalid_origin"}')
            if path == '/_access/logout':
                return self.respond(303, headers=[('Location', LOGIN), ('Set-Cookie', COOKIE+'=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict')])
            if path != LOGIN:
                return self.respond(404, b'{}')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                return self.respond(415, b'{}')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:
                    return self.respond(413, b'{}')
                fields = parse_qs(self.rfile.read(length).decode(), max_num_fields=4)
                key = fields.get('key', [''])[0]
            except (ValueError, UnicodeError):
                return self.respond(400, b'{}')
            ip = self.headers.get('X-Real-IP', self.client_address[0])
            code = gate.login(key, ip)
            if code != 303:
                if code == 429:
                    return self.page(429, '尝试次数过多，请在 5 分钟后重试。')
                return self.page(401, 'Key 不正确，请重新输入。')
            self.respond(303, headers=[('Location', '/'), ('Set-Cookie', COOKIE+'='+gate.issue_cookie()+'; Max-Age='+str(TTL)+'; Path=/; Secure; HttpOnly; SameSite=Strict')])
    return Handler


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--port', type=int, default=18095)
    args = p.parse_args()
    gate = AccessGate(json.loads(Path(args.config).read_text()))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(gate))
    server.daemon_threads = True
    server.serve_forever()
