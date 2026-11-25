"""Read-only task access shared by tabs of one authenticated browser session."""
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from .product import ProductError

PATH = '/api/quick-request'
LIST_PATH = PATH + '/browser-tasks'
PREFIX = '__Secure-sfq-'
# The access gate lives on a separate host and validates this cookie upstream.
LOGIN_COOKIE = '__Host-searchflow_access'
LIMIT = 20
MIGRATION_LIMIT = 8
SITE_COOKIE = re.compile(r'([0-9]{10})\.[0-9a-f]{32}\.[0-9a-f]{64}')
JOB_COOKIE = re.compile(r'([0-9]{10})\.([0-9a-f]{64})')
JOB_NAME = re.compile(r'[0-9a-f]{32}')


class BrowserTasks:
    def __init__(self, headers, key):
        self.key = key.encode()
        self.now = int(time.time())
        self.session = ''
        self.cookies = SimpleCookie()
        try:
            self.cookies.load(headers.get('Cookie', ''))
        except CookieError:
            return
        site = self.cookies.get(LOGIN_COOKIE)
        if site is not None and (match := SITE_COOKIE.fullmatch(site.value)) and int(match[1]) > self.now:
            self.session = site.value
            self.expires_at = int(match[1])

    def _signature(self, hex_id, issued):
        message = f'quick-browser-history:{self.session}:{hex_id}:{issued}'.encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def _issued(self, hex_id):
        if not self.session or not (cookie := self.cookies.get(PREFIX + hex_id)):
            return None
        match = JOB_COOKIE.fullmatch(cookie.value)
        if not match or int(match[1]) > self.now + 5:
            return None
        if hmac.compare_digest(match[2], self._signature(hex_id, match[1])):
            return int(match[1])
        return None

    def registered(self):
        if not self.session:
            return []
        pairs = []
        for name in self.cookies:
            if not name.startswith(PREFIX):
                continue
            suffix = name[len(PREFIX):]
            if JOB_NAME.fullmatch(suffix) and (issued := self._issued(suffix)) is not None:
                pairs.append((issued, suffix))
        return [suffix for _, suffix in sorted(pairs, reverse=True)]

    def allows(self, jid):
        try:
            return self._issued(UUID(str(jid)).hex) is not None
        except ValueError:
            return False

    def _header(self, hex_id, value, age):
        return f'{PREFIX}{hex_id}={value}; Path={PATH}; Max-Age={age}; Secure; HttpOnly; SameSite=Strict'

    def remember(self, jobs):
        if not self.session:
            return []
        requested = list(dict.fromkeys(UUID(str(job)).hex for job in jobs))[-LIMIT:]
        current = self.registered()
        available = [key for key in current if key not in requested]
        headers = [self._header(key, '', 0) for key in available[LIMIT - len(requested):]]
        for key in requested:
            stamp = str(self.now)
            value = stamp + '.' + self._signature(key, stamp)
            headers.append(self._header(key, value, self.expires_at - self.now))
        return headers

    def read(self, store, account_id):
        ids = [UUID(hex=value) for value in self.registered()[:LIMIT]]
        if not ids:
            return []
        with store.base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            result = db.execute("""
                SELECT id, parameters->>'query' AS query, parameters->>'source' AS source,
                       status,created_at,completed_at
                FROM sf_jobs WHERE account_id=%s AND id=ANY(%s)
                ORDER BY created_at DESC,id DESC
            """, (account_id, ids)).fetchall()
        now = datetime.now(timezone.utc)
        return [dict(id=row['id'], params={'query':row['query'], 'source':row['source']},
                     status=row['status'], server={'progress':{'elapsed_seconds':
                        max(0, int(((row['completed_at'] or now) - row['created_at']).total_seconds()))}})
                for row in result]


def send(handler, payload, status=200, cookies=()):
    if not cookies:
        handler.send_json(payload, status)
        return
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    for value in cookies:
        handler.send_header('Set-Cookie', value)
    handler.end_headers()
    handler.wfile.write(raw)


def list_request(handler, method, query, browser, store, account_id, body, receipt, key):
    if query or method not in ('GET', 'POST'):
        raise ProductError('invalid_parameter', '仅支持读取任务或迁移已有回执')
    if method == 'GET':
        handler.send_json({'tasks':browser.read(store, account_id)})
        return
    if not browser.session:
        raise ProductError('site_session_required', '请先登录工作台再恢复任务。', 401)
    rows = body(handler).get('receipts')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MIGRATION_LIMIT:
        raise ProductError('invalid_parameter', '回执数量无效')
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'token'}:
            raise ProductError('invalid_parameter', '回执格式无效')
        try:
            jid = str(UUID(row['id']))
        except (ValueError, TypeError, AttributeError):
            raise ProductError('invalid_parameter', '回执格式无效') from None
        token = row['token']
        if isinstance(token, str) and token.isascii() and hmac.compare_digest(token, receipt(key, jid)):
            ids.append(jid)
    sent = browser.remember(ids) if ids else []
    send(handler, {'remembered':ids}, cookies=sent)
