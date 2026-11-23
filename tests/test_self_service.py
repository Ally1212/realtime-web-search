import io
import os
import unittest
from contextlib import ExitStack
from http.cookies import SimpleCookie
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore
from realtime.config import Config
from realtime.maps_provider import availability
from realtime.product import ProductStore
from realtime.product_http import handle
from realtime.self_service import (
    CALLBACK, DAILY_LIMIT, LOGIN_STATE, PREFIX, SESSION, Settings, google_subject, initialize,
)
from realtime.serp_console import ConsoleStore
from tests.maps_job_fixture import process_pending
from tests.test_maps_provider import result


ORIGIN = 'https://example.test'
CONFIG = Settings(ORIGIN, 'fixture.apps.googleusercontent.com', 'fixture',
                  ORIGIN + '/privacy', ORIGIN + '/terms')


class Request:
    client_address = ('127.0.0.1', 12345)

    def __init__(self, config, path, headers, body):
        self.config, self.path, self.headers = config, path, headers
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.response_headers, self.payload = [], None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def send_json(self, payload, status=200):
        self.status, self.payload = status, payload

    def send_body(self, body, mime, status=200):
        self.status = status
        self.wfile.write(body)

    def get_cookie(self, name):
        jar = SimpleCookie()
        for key, value in self.response_headers:
            if key == 'Set-Cookie':
                jar.load(value)
        return jar[name].value


class GoogleProtocolTests(unittest.TestCase):
    def test_exchanges_code_server_side_then_reads_verified_provider_subject(self):
        with patch('realtime.self_service.requests.post') as exchange, patch(
                'realtime.self_service.requests.get') as identity:
            exchange.return_value.json.return_value = {'access_token': 'fixture-token'}
            identity.return_value.json.return_value = {'sub': 'identity-1'}
            self.assertEqual(google_subject(CONFIG, 'code', 'verifier'), 'identity-1')
        exchange.assert_called_once_with('https://oauth2.googleapis.com/token', data={
            'client_id': CONFIG.client_id, 'client_secret': CONFIG.client_secret, 'code': 'code',
            'code_verifier': 'verifier', 'grant_type': 'authorization_code', 'redirect_uri': CONFIG.callback,
        }, timeout=8, allow_redirects=False)
        identity.assert_called_once_with('https://openidconnect.googleapis.com/v1/userinfo',
                                         headers={'Authorization': 'Bearer fixture-token'},
                                         timeout=8, allow_redirects=False)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'isolated PostgreSQL required')
class PublicWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ['TEST_DATABASE_URL']
        self.schema = 'public_workspace_' + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        dsn = make_conninfo(self.root, options=f'-c search_path={self.schema}')
        self.config = Config(database_url=dsn)
        CampaignStore(dsn)
        console = ConsoleStore(dsn)
        console.initialize()
        console.initialize_console(self.config)
        self.store = ProductStore(dsn)
        self.store.initialize()
        initialize(self.store.base)
        self.context = ExitStack()
        self.addCleanup(self.cleanup)
        self.context.enter_context(patch.dict(os.environ, {
            'SEARCHFLOW_PRODUCT_ENABLED': 'true', 'SEARCHFLOW_MAPS_WORKER': 'true',
        }))
        self.context.enter_context(patch('realtime.self_service.settings', return_value=CONFIG))
        maps = dict(availability(), enabled=True)
        for module in ('realtime.google_local_jobs', 'realtime.google_local_http'):
            self.context.enter_context(patch(module + '.availability', return_value=maps))

    def cleanup(self):
        self.context.close()
        self.store.base.pool.close()
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def call(self, path, method='GET', *, session='', verifier='', payload=b'', idem='',
             origin=ORIGIN):
        cookies = '; '.join(f'{name}={value}' for name, value in (
            (SESSION, session), (LOGIN_STATE, verifier)) if value)
        headers = {'Host': 'example.test', 'Origin': origin, 'Cookie': cookies,
                   'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
                   'Idempotency-Key': idem,
                   'X-Forwarded-Proto': 'https'}
        request = Request(self.config, path, headers, payload)
        self.assertTrue(handle(request, method))
        return request

    def login(self, subject, session=''):
        start = self.call(PREFIX + 'google/start')
        self.assertEqual(start.status, 303)
        endpoint = urlsplit(dict(start.response_headers)['Location'])
        query = parse_qs(endpoint.query)
        self.assertEqual(endpoint.netloc, 'accounts.google.com')
        self.assertEqual(query['scope'], ['openid'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        verifier = start.get_cookie(LOGIN_STATE)
        with patch('realtime.self_service.google_subject', return_value=subject) as exchange:
            reject = self.call(CALLBACK + '?code=fixture&state=wrong', verifier=verifier)
            self.assertEqual(dict(reject.response_headers)['Location'], '/app?login=failed')
            exchange.assert_not_called()
            callback = self.call(CALLBACK + '?code=fixture&state=' + query['state'][0],
                                 session=session, verifier=verifier)
        exchange.assert_called_once_with(CONFIG, 'fixture', verifier)
        self.assertEqual(dict(callback.response_headers)['Location'], '/app')
        return callback.get_cookie(SESSION)

    def submit_map(self, session, idem):
        return self.call(PREFIX + 'maps/jobs', 'POST', session=session, idem=idem,
                         payload=b'{"query":"coffee","city":"Singapore","region":"SG"}')

    def test_google_identity_isolation_session_rotation_and_exports(self):
        self.assertEqual(self.call(PREFIX + 'usage').status, 401)
        self.assertEqual(self.submit_map('', 'public').status, 401)
        user_a, user_b = self.login('alice'), self.login('bob')
        self.assertEqual(self.call(PREFIX + 'usage', session=user_a).payload['monthly_limit'], 30)
        new = self.submit_map(user_a, 'first')
        self.assertEqual(new.status, 202)
        job = new.payload['status_url']
        self.assertEqual(self.call(job, session=user_b).status, 404)
        self.assertEqual(self.call(job + '/results', session=user_b).status, 404)
        self.assertEqual(self.call(job + '/export?format=csv', session=user_b).status, 404)
        with patch('tests.maps_job_fixture.fetch_search', return_value=result()):
            process_pending(self.store.base)
        export = self.call(job + '/export?format=csv', session=user_a)
        self.assertEqual(export.status, 200)
        self.assertIn(b'Cafe', export.wfile.getvalue())
        user_a2 = self.login('alice', session=user_a)
        self.assertEqual(self.call(job, session=user_a2).status, 200)
        self.assertEqual(self.call(job, session=user_a).status, 401)
        self.assertEqual(self.call(PREFIX + 'usage', session=user_b).payload['monthly_remaining'], 30)
        self.assertEqual(self.call(PREFIX + 'logout', 'POST', session=user_a2).status, 303)
        self.assertEqual(self.call(job, session=user_a2).status, 401)

    def test_daily_limit_atomic_across_sources_and_failed_job_releases_month(self):
        session = self.login('requester')
        first = self.submit_map(session, 'logical-retry')
        self.assertEqual(first.status, 202)
        self.assertEqual(self.submit_map(session, 'logical-retry').payload['id'], first.payload['id'])
        search = b'{"query":"test","source":"google_web","language":"en","region":"US"}'
        concurrent = self.call(PREFIX + 'search', 'POST', session=session, payload=search, idem='web')
        self.assertEqual(concurrent.payload['code'], 'concurrency_limit')
        with patch('tests.maps_job_fixture.fetch_search', side_effect=ValueError()):
            process_pending(self.store.base)
            for i in range(DAILY_LIMIT - 1):
                self.assertEqual(self.submit_map(session, 'new-' + str(i)).status, 202)
                process_pending(self.store.base)
        usage = self.call(PREFIX + 'usage', session=session).payload
        self.assertEqual(usage['daily_remaining'], 0)
        self.assertEqual(usage['monthly_remaining'], 30)
        for path, payload in ((PREFIX + 'maps/jobs', b'{"query":"coffee","city":"Singapore"}'),
                              (PREFIX + 'search', search)):
            rejected = self.call(path, 'POST', session=session, payload=payload, idem='over')
            self.assertEqual(rejected.status, 429)
            self.assertEqual(rejected.payload['code'], 'daily_limit')
        self.assertEqual(self.submit_map(session, 'logical-retry').payload['id'], first.payload['id'])
        self.assertEqual(self.call(PREFIX + 'usage', session=session).payload['reserved'], 0)
        self.assertEqual(self.call(PREFIX + 'logout', 'POST', session=session,
                                   origin='https://elsewhere.test').status, 403)

    def test_web_jobs_and_monthly_limit_keep_workspace_boundary(self):
        owner, stranger = self.login('owner'), self.login('stranger')
        search = b'{"query":"test","source":"google_web","language":"en","region":"US"}'
        accepted = self.call(PREFIX + 'search', 'POST', session=owner, payload=search, idem='web')
        self.assertEqual(accepted.status, 202)
        jid = accepted.payload['id']
        for suffix in ('', '/data', '/export?format=jsonl'):
            self.assertEqual(self.call(PREFIX + 'search/' + jid + suffix,
                                       session=stranger).status, 404)
        self.assertEqual(self.call(PREFIX + 'search/' + jid, session=owner).payload['status'], 'queued')
        record = {'request_id': jid, 'occurrences': [], 'topics': []}
        self.store.finish(jid, 'succeeded', response={'readback_verified': True, 'middle_record': record})
        self.assertEqual(self.call(PREFIX + 'search/' + jid + '/data', session=owner).payload, record)
        self.assertIn(jid.encode(), self.call(PREFIX + 'search/' + jid + '/export?format=csv',
                                             session=owner).wfile.getvalue())
        self.assertEqual(self.call(PREFIX + 'usage', session=owner).payload['monthly_remaining'], 29)
        with self.store.base.connect() as db:
            db.execute("""UPDATE sf_months SET used=30 WHERE account_id=(
                              SELECT account_id FROM sf_google_identities WHERE subject='owner')""")
        self.assertEqual(self.submit_map(owner, 'over-month').payload['code'], 'quota_exhausted')
        self.assertEqual(self.submit_map(stranger, 'separate').status, 202)
