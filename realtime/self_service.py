"""Google sign-in and workspace ownership for the public SearchFlow app."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import requests

from .product import ProductError, ProductStore

PREFIX = '/api/account/'
CALLBACK = PREFIX + 'google/callback'
SESSION = '__Host-searchflow_session'
LOGIN_STATE = '__Host-searchflow_google'
SESSION_TTL = 7 * 86400
STATE_TTL = 600
DAILY_LIMIT = 5
MONTHLY_LIMIT = 30
SEARCH_ITEMS = 10
MAPS_ITEMS = 20
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_identities (
 subject text PRIMARY KEY, account_id uuid NOT NULL UNIQUE REFERENCES sf_accounts(id)
);
CREATE TABLE IF NOT EXISTS sf_browser_sessions (
 token_hash char(64) PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id),
 expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sf_browser_sessions_expiry ON sf_browser_sessions(expires_at);
"""


@dataclass(frozen=True)
class Settings:
    origin: str
    client_id: str
    client_secret: str = field(repr=False)
    privacy_url: str = ''
    terms_url: str = ''

    @property
    def callback(self):
        return self.origin + CALLBACK


def settings():
    path = os.getenv('SEARCHFLOW_GOOGLE_LOGIN_CONFIG')
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
        origin, cid, secret, privacy, terms = (
            data[k] for k in ('origin', 'client_id', 'client_secret', 'privacy_url', 'terms_url')
        )
        url = urlsplit(origin)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or origin != 'https://' + url.netloc
                or not cid.endswith('.apps.googleusercontent.com') or not secret
                or any(urlsplit(link).scheme != 'https' or not urlsplit(link).hostname
                       for link in (privacy, terms))):
            raise ValueError('Invalid Google sign-in configuration')
        return Settings(origin, cid, secret, privacy, terms)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None


def digest(data):
    return hashlib.sha256(data.encode('ascii')).hexdigest()


def cookie(name, value, age, same_site):
    return f'{name}={value}; Max-Age={age}; Path=/; Secure; HttpOnly; SameSite={same_site}'


def cookie_token(handler, name):
    try:
        value = SimpleCookie(handler.headers.get('Cookie', ''))[name].value
    except (CookieError, KeyError):
        return ''
    return value if TOKEN.fullmatch(value) else ''


def google_start(config, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': config.client_id, 'redirect_uri': config.callback, 'response_type': 'code',
        'scope': 'openid', 'state': digest('state:' + verifier),
        'code_challenge': challenge, 'code_challenge_method': 'S256',
    })


def google_subject(config, code, verifier):
    token = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': config.client_id, 'client_secret': config.client_secret, 'code': code,
        'code_verifier': verifier, 'grant_type': 'authorization_code', 'redirect_uri': config.callback,
    }, timeout=8, allow_redirects=False)
    token.raise_for_status()
    access = token.json().get('access_token')
    if not isinstance(access, str) or not 1 <= len(access) <= 4096:
        raise ValueError('Invalid access token')
    user = requests.get('https://openidconnect.googleapis.com/v1/userinfo',
                        headers={'Authorization': 'Bearer ' + access}, timeout=8, allow_redirects=False)
    user.raise_for_status()
    subject = user.json().get('sub')
    if not isinstance(subject, str) or not re.fullmatch(r'[!-~]{1,255}', subject):
        raise ValueError('Invalid Google subject')
    return subject


def initialize(base):
    with base.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(20261123,1)')
        db.execute(SCHEMA)


def sign_in(store, subject, session):
    token = secrets.token_urlsafe(32)
    with store.base.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 20261123))', (subject,))
        row = db.execute('SELECT account_id FROM sf_google_identities WHERE subject=%s', (subject,)).fetchone()
        if row:
            account_id = row['account_id']
            if not db.execute('SELECT id FROM sf_accounts WHERE id=%s AND enabled', (account_id,)).fetchone():
                raise ProductError('account_disabled', 'This workspace has been disabled.', 403)
        else:
            account_id = uuid4()
            db.execute('INSERT INTO sf_accounts(id,name,monthly_limit,concurrency) VALUES(%s,%s,%s,1)',
                       (account_id, 'Personal workspace', MONTHLY_LIMIT))
            db.execute('INSERT INTO sf_google_identities(subject,account_id) VALUES(%s,%s)', (subject, account_id))
        db.execute('DELETE FROM sf_browser_sessions WHERE expires_at<=now()')
        if session:
            db.execute('DELETE FROM sf_browser_sessions WHERE token_hash=%s', (digest(session),))
        db.execute("INSERT INTO sf_browser_sessions(token_hash,account_id,expires_at) VALUES(%s,%s,now()+%s*interval '1 second')",
                   (digest(token), account_id, SESSION_TTL))
    return token


def daily_count(db, account_id):
    return db.execute("""
        SELECT
          (SELECT count(*) FROM sf_jobs WHERE account_id=%s AND created_at>=utc_day) +
          (SELECT count(*) FROM sf_google_local_jobs WHERE account_id=%s AND created_at>=utc_day) AS used
        FROM (SELECT date_trunc('day',now(),'UTC') AS utc_day) today
    """, (account_id, account_id)).fetchone()['used']


@dataclass
class Workspace:
    store: ProductStore
    account: dict

    def submit(self, create):
        # Both job stores lock the same workspace row until this transaction commits.
        with self.store.base.connect() as db:
            job_id, reused = create(db)
            if not reused and daily_count(db, self.account['id']) > DAILY_LIMIT:
                raise ProductError('daily_limit', 'Daily request limit reached. Try again tomorrow (UTC).', 429)
            return job_id, reused

    def usage(self):
        monthly = self.store.usage(self.account['id'])
        with self.store.base.connect() as db:
            daily = daily_count(db, self.account['id'])
        reset = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return {'authenticated': True, 'daily_limit': DAILY_LIMIT, 'daily_remaining': max(0, DAILY_LIMIT-daily),
                'monthly_limit': monthly['monthly_limit'], 'monthly_remaining': monthly['remaining'],
                'reserved': monthly['reserved'], 'daily_reset_at': reset.isoformat(), 'month': monthly['month']}


def workspace(store, token):
    if not token:
        raise ProductError('sign_in_required', 'Sign in with Google to continue.', 401)
    with store.base.connect() as db:
        account = db.execute("""
            SELECT a.* FROM sf_browser_sessions s JOIN sf_accounts a ON a.id=s.account_id
            WHERE s.token_hash=%s AND s.expires_at>now() AND a.enabled
        """, (digest(token),)).fetchone()
        if not account:
            raise ProductError('sign_in_required', 'Sign in with Google to continue.', 401)
        rate = db.execute("""
            INSERT INTO sf_rate_windows(account_id,minute,requests)
            VALUES(%s,date_trunc('minute',now()),1) ON CONFLICT(account_id,minute)
            DO UPDATE SET requests=sf_rate_windows.requests+1 RETURNING requests
        """, (account['id'],)).fetchone()['requests']
    if rate > 60:
        raise ProductError('rate_limited', 'Too many requests. Try again in a minute.', 429)
    return Workspace(store, account)


def respond(handler, status, *, location=None, cookies=(), page=None):
    headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
               'X-Content-Type-Options': 'nosniff'}
    if location:
        headers['Location'] = location
    if page is not None:
        headers['Referrer-Policy'] = 'same-origin'
        headers['Content-Type'] = 'text/html; charset=utf-8'
        headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
    content = (page or '').encode()
    headers['Content-Length'] = str(len(content))
    handler.send_response(status)
    for name, value in headers.items():
        handler.send_header(name, value)
    for value in cookies:
        handler.send_header('Set-Cookie', value)
    handler.end_headers()
    if content:
        handler.wfile.write(content)


def handle(handler, method, parsed):
    from .product_http import secure
    path = parsed.path
    config = settings()
    if not config or not secure(handler) or 'https://' + handler.headers.get('Host', '') != config.origin:
        handler.send_json({'error': 'Public access is unavailable.', 'code': 'public_disabled'}, 503)
        return True
    if method == 'POST' and handler.headers.get('Origin') != config.origin:
        handler.send_json({'error': 'Invalid origin.', 'code': 'invalid_origin'}, 403)
        return True
    if path in ('/app', '/app.js', '/app.css') and method == 'GET':
        from .public_workbench import asset, page
        if path == '/app':
            respond(handler, 200, page=page(config))
        else:
            content, mime = asset(path)
            handler.send_body(content, mime)
        return True
    if path == PREFIX + 'google/start' and method == 'GET':
        verifier = secrets.token_urlsafe(32)
        respond(handler, 303, location=google_start(config, verifier),
                cookies=[cookie(LOGIN_STATE, verifier, STATE_TTL, 'Lax')])
        return True

    store = ProductStore(handler.config.database_url)
    token = cookie_token(handler, SESSION)
    try:
        if path == CALLBACK and method == 'GET':
            verifier = cookie_token(handler, LOGIN_STATE)
            expired = cookie(LOGIN_STATE, '', 0, 'Lax')
            try:
                args = parse_qs(parsed.query, max_num_fields=12)
                code, state = args.get('code', []), args.get('state', [])
                if (not verifier or len(code) != 1 or len(state) != 1 or not 1 <= len(code[0]) <= 2048
                        or not hmac.compare_digest(state[0], digest('state:' + verifier))):
                    raise ValueError('Invalid request state')
                session = sign_in(store, google_subject(config, code[0], verifier), token)
            except (ValueError, TypeError, AttributeError, requests.RequestException):
                respond(handler, 303, location='/app?login=failed', cookies=[expired])
                return True
            respond(handler, 303, location='/app', cookies=[expired, cookie(SESSION, session, SESSION_TTL, 'Lax')])
        elif path == PREFIX + 'logout' and method == 'POST':
            if token:
                with store.base.connect() as db:
                    db.execute('DELETE FROM sf_browser_sessions WHERE token_hash=%s', (digest(token),))
            respond(handler, 303, location='/app', cookies=[cookie(SESSION, '', 0, 'Lax')])
        elif path == PREFIX + 'usage' and method == 'GET':
            handler.send_json(workspace(store, token).usage())
        elif path.startswith(PREFIX + 'maps/'):
            from .google_local_http import handle as maps
            if maps(handler, method, public=workspace(store, token)):
                return True
            handler.send_json({'error': 'Not found.'}, 404)
        elif (path == PREFIX + 'search' or re.fullmatch(
                PREFIX + r'search/[0-9a-f-]{36}(?:/(?:data|export|cancel))?', path)):
            from .quick_request_http import handle as search
            return search(handler, method, public=workspace(store, token))
        else:
            handler.send_json({'error': 'Not found.'}, 404)
    except ProductError as error:
        handler.send_json({'error': str(error), 'code': error.code}, error.status)
    except Exception as error:
        print('public workspace: ' + type(error).__name__, flush=True)
        handler.send_json({'error': 'The service is temporarily unavailable.', 'code': 'unavailable'}, 503)
    return True


if __name__ == '__main__':
    import argparse

    from .config import Config

    command = argparse.ArgumentParser(description='Initialize public SearchFlow account tables.')
    command.add_argument('--initialize', action='store_true', required=True)
    command.parse_args()
    initialize(ProductStore(Config().database_url).base)
