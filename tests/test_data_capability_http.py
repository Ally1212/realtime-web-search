"""Real local HTTP + PostgreSQL; source executor and credentials are fixtures."""
import copy
import hashlib
import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from realtime.access_gate import AccessGate
from realtime.data_capability_export import verify_bundle
from realtime.data_capability_queries import CapabilityRuntime
from realtime.data_capability_store import CapabilityData
from realtime.product_http import handle, openapi
from tests import test_product as product_tests
from tests.test_data_capability_facts import parameters, result as fixture_result


class CapabilitySchemaTests(unittest.TestCase):
    def test_main_discovery_keeps_old_sources_and_lists_new_unconfigured_contract(self):
        from realtime.product import capability_payload
        from realtime.customer_data import field_catalog
        info = capability_payload()
        self.assertEqual({s['id'] for s in info['sources']}, {'google_web', 'google_news', 'google_trends', 'google_images'})
        self.assertEqual(info['data_capabilities']['availability'], 'not_configured')
        self.assertFalse(info['data_capabilities']['source_verified'])
        self.assertEqual(info['data_capabilities']['subscriptions'], 'local_candidate_requires_runtime')
        self.assertIn('monthly', field_catalog()['data_capabilities']['models']['volume'])
        self.assertIn('citations', field_catalog()['data_capabilities']['models']['answer'])
        doc = openapi()
        self.assertEqual(doc['security'], [{'bearerAuth': [], 'siteAccess': []}])
        for path in ('/v1/data/jobs', '/v1/data/observations', '/v1/data/observations/{id}',
                     '/v1/data/trends/series', '/v1/data/trends/series/{id}/points',
                     '/v1/data/keyword-volumes', '/v1/data/keyword-volumes/{id}/months',
                     '/v1/data/ai/answers', '/v1/data/ai/answers/{id}/blocks',
                     '/v1/data/ai/answers/{id}/citations', '/v1/data/ai/answers/{id}/links'):
            self.assertIn(path, doc['paths'])
        create = doc['paths']['/v1/data/jobs']['post']
        self.assertIn('202', create['responses'])
        self.assertIn('409', create['responses'])
        self.assertTrue(create['parameters'][0]['required'])
        schemas = doc['components']['schemas']['CapabilityJobRequest']['oneOf']
        self.assertEqual(len(schemas), 4)
        for s in schemas:
            self.assertFalse(s['additionalProperties'])
            self.assertIn('budget', s['required'])
        # OpenAPI query names must not be duplicated when old/new contracts merge.
        for path, operations in doc['paths'].items():
            for operation in operations.values():
                names = [(p['in'], p['name']) for p in operation.get('parameters', [])]
                self.assertEqual(len(names), len(set(names)), path)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CapabilityHTTPTests(unittest.TestCase):
    def setUp(self):
        product_tests.ProductIntegrationTests.setUp(self)
        self.now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        self.data = CapabilityData(self.dsn, clock=lambda: self.now)
        self.data.initialize()
        self.provider = 'fixture-http-not-real-provider'
        self.grants = {c: dict(capability=c, status='approved', provider=self.provider,
                               license_id='fixture-only', allowed_use='isolated_test', retention_days=1,
                               billing_unit='fixture-call', currency='USD', maximum_cost='0.40', raw_response_retention_allowed=True,
                               expires_at='2027-01-01T00:00:00Z', validate_parameters=lambda p: None)
                       for c in ('trends_history', 'keyword_volume', 'aio_answer', 'ai_mode_answer')}
        for tenant in (self.account['account_id'], self.other['account_id']):
            self.data.configure_budget(str(tenant), 'USD', '100', '100')
        self.ready = True
        self.consume = True
        self.worker_errors = []
        self.stop_worker = threading.Event()
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.runtime = CapabilityRuntime(self.data, lambda tenant, c: self.grants.get(c),
                                          cursor_secret=b'fixture-api-cursor-secret-not-production' * 2,
                                          worker_ready=lambda t, c, p: self.ready and self.worker.is_alive())
        self.worker.start()
        gate = AccessGate(dict(key_sha256=hashlib.sha256(b'fixture-site-key').hexdigest(),
                               cookie_secret=('ab' * 32), origin='https://fixture.invalid'))
        class Handler(BaseHTTPRequestHandler):
            def send_body(self, payload, content_type, status=200):
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)
            def send_json(self, payload, status=200):
                self.send_body(json.dumps(payload, default=str).encode(), 'application/json', status)
            def route(self, method):
                # Explicit test proxy layer reproduces the production access
                # gate before the unchanged customer Bearer authentication.
                if not gate.authorized(self.headers.get('X-SearchFlow-Access-Key', ''), self.headers.get('Cookie', '')):
                    self.send_json({'code': 'site_access_required'}, 401)
                else:
                    handle(self, method)
            def do_GET(self): self.route('GET')
            def do_POST(self): self.route('POST')
            def log_message(self, *args): pass
        Handler.config = self.config
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.data_capability_runtime = self.runtime
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(3)
        self.stop_worker.set()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.worker_errors, [])
        product_tests.ProductIntegrationTests.tearDown(self)

    def work(self):
        while not self.stop_worker.wait(.01):
            if not self.consume:
                continue
            for cap in self.grants:
                try:
                    claim = self.data.claim(cap, self.provider, lambda tenant, c: self.grants[c])
                    if claim is None:
                        continue
                    job = claim['job']
                    value = fixture_result(job['parameters'], self.provider)
                    value['requested_at'] = value['received_at'] = self.now.isoformat()
                    self.data.complete(claim['tenant_id'], job['id'], claim['lease_token'], value, '0.1', self.grants[cap], event_key=job['id'])
                except Exception as exc:
                    self.worker_errors.append(type(exc).__name__ + ':' + str(exc))
                    self.stop_worker.set()
                    break

    def request(self, path, *, method='GET', payload=None, raw=None, key=None, site=True, bearer=True, idempotency=None):
        headers = {}
        if site:
            headers['X-SearchFlow-Access-Key'] = 'fixture-site-key'
        if bearer:
            headers['Authorization'] = 'Bearer ' + (key or self.account['api_key'])
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        request = Request('http://127.0.0.1:' + str(self.server.server_port) + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, response.read(), response.headers
        except HTTPError as response:
            return response.code, response.read(), response.headers

    def json(self, path, **kwargs):
        code, raw, _ = self.request(path, **kwargs)
        return code, json.loads(raw)

    def submit(self, cap='trends_history', *, key=None, idempotency='fixture-one'):
        code, job = self.json('/v1/data/jobs', method='POST', payload=dict(parameters(cap), budget=dict(maximum_cost='0.5', currency='USD')),
                              idempotency=idempotency, key=key)
        self.assertEqual(code, 202, job)
        return job

    def wait(self, job, *, key=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            code, state = self.json(job['status_url'], key=key)
            self.assertEqual(code, 200, state)
            if state['status'] in ('succeeded', 'partial', 'failed', 'canceled'):
                return state
            self.stop_worker.wait(.02)
        self.fail('live fixture worker did not finish the accepted job')

    def test_four_capabilities_real_http_to_fixture_executor_and_child_routes(self):
        for cap, prefix, children in (('trends_history', '/v1/data/trends/series', ['points']),
                                       ('keyword_volume', '/v1/data/keyword-volumes', ['months']),
                                       ('aio_answer', '/v1/data/ai/answers', ['blocks', 'citations', 'links']),
                                       ('ai_mode_answer', '/v1/data/ai/answers', ['blocks', 'citations', 'links'])):
            with self.subTest(cap=cap):
                job = self.submit(cap, idempotency='fixture-' + cap)
                state = self.wait(job)
                self.assertEqual(state['status'], 'succeeded')
                code, saved = self.json(job['data_url'])
                self.assertEqual(code, 200, saved)
                self.assertEqual(saved['observation']['capability'], cap)
                code, page = self.json(prefix + '?' + urlencode({'capability': cap, 'limit': 1}))
                self.assertEqual(code, 200, page)
                object_id = page['records'][0]['object_id']
                code, detail = self.json(prefix + '/' + object_id)
                self.assertEqual(code, 200, detail)
                for child in children:
                    code, records = self.json(prefix + '/' + object_id + '/' + child + '?limit=1')
                    self.assertEqual(code, 200, records)
                    all_rows = list(records['records'])
                    if records['has_more']:
                        code, rest = self.json(prefix + '/' + object_id + '/' + child + '?' + urlencode({'limit': 1, 'cursor': records['next_cursor']}))
                        self.assertEqual(code, 200, rest)
                        all_rows += rest['records']
                    expected = detail['record'][{'points': 'points', 'months': 'monthly'}.get(child, child)]
                    self.assertEqual(all_rows, expected)
        code, page = self.json('/v1/jobs?domain=data&limit=1')
        self.assertEqual(code, 200)
        self.assertTrue(page['has_more'])
        code, legacy = self.json('/v1/jobs?domain=search')
        self.assertEqual(code, 200)
        self.assertEqual(legacy['jobs'], [])
        code, data = self.json('/v1/data/observations?limit=1')
        self.assertEqual(code, 200)
        self.assertTrue(data['has_more'])
        self.assertNotIn('payload', data['records'][0])

    def test_full_downloads_and_pinned_version_match_the_saved_snapshot(self):
        job = self.submit('ai_mode_answer')
        self.wait(job)
        _, saved = self.json(job['data_url'])
        snapshot = saved['observation']
        for format in ('json', 'jsonl', 'csv', 'zip', 'manifest'):
            code, raw, headers = self.request(job['status_url'] + '/export?' + urlencode({'format': format, 'snapshot_hash': snapshot['snapshot_hash']}))
            self.assertEqual(code, 200, raw[:100])
            if format == 'zip':
                proof = verify_bundle(raw, expected_payload_hash=snapshot['payload_hash'], expected_snapshot_hash=snapshot['snapshot_hash'], expected_observation_id=snapshot['observation_id'])
                self.assertEqual(proof['record_counts']['citation'], 1)
            if format == 'json':
                self.assertEqual(json.loads(raw), snapshot)
        query = dict(capability='ai_mode_answer', observation_id=snapshot['observation_id'], snapshot_hash=snapshot['snapshot_hash'], format='json')
        code, data = self.json('/v1/data/export?' + urlencode(query))
        self.assertEqual(code, 200)
        self.assertEqual(data, snapshot)
        self.assertEqual(self.request(job['status_url'] + '/export?snapshot_hash=wrong')[0], 409)
        self.assertEqual(self.request('/v1/data/export?' + urlencode({**query, 'country': 'US'}))[0], 400)
        self.assertEqual(self.request('/v1/data/export?' + urlencode({**query, 'snapshot_hash': 'wrong'}))[0], 409)

    def test_two_auth_layers_and_owned_details_do_not_leak_foreign_facts(self):
        job = self.submit()
        done = self.wait(job)
        self.assertEqual(self.request('/v1/data/observations', site=False)[0], 401)
        self.assertEqual(self.request('/v1/data/observations', bearer=False)[0], 401)
        self.assertEqual(self.request('/v1/data/fields', key='wrong-bearer')[0], 401)
        for path in (job['status_url'], job['data_url'], job['status_url'] + '/export', '/v1/data/observations/' + done['result_ref']):
            self.assertEqual(self.request(path, key=self.other['api_key'])[0], 404)
        code, other = self.json('/v1/data/observations', key=self.other['api_key'])
        self.assertEqual(code, 200)
        self.assertEqual(other['records'], [])
        self.grants['trends_history']['status'] = 'revoked'
        self.assertEqual(self.request(job['data_url'])[0], 409)
        self.assertEqual(self.request(job['status_url'] + '/export')[0], 409)
        code, empty = self.json('/v1/data/trends/series')
        self.assertEqual(code, 200)
        self.assertEqual(empty['records'], [])
        self.assertFalse(empty['has_more'])

    def test_unconfigured_no_executor_parameters_duplicates_and_cancel(self):
        payload = dict(parameters(), budget=dict(maximum_cost='0.5', currency='USD'))
        del self.server.data_capability_runtime
        code, error = self.json('/v1/data/jobs', method='POST', payload=payload, idempotency='disabled')
        self.assertEqual((code, error['code']), (409, 'source_not_admitted'))
        self.assertEqual(self.request('/v1/data/observations')[0], 503)
        self.assertEqual(self.data.list(str(self.account['account_id']))['jobs'], [])
        self.server.data_capability_runtime = self.runtime
        self.ready = False
        code, error = self.json('/v1/data/jobs', method='POST', payload=payload, idempotency='no-executor')
        self.assertEqual((code, error['code']), (503, 'executor_unavailable'))
        for bad in ({**payload, 'unknown': True}, {**payload, 'device': 'desktop'},
                    {**payload, 'budget': {'maximum_cost': .5, 'currency': 'USD'}}, {**payload, 'budget': {'maximum_cost': 'NaN', 'currency': 'USD'}}):
            self.assertEqual(self.request('/v1/data/jobs', method='POST', payload=bad, idempotency='invalid')[0], 400)
        raw = json.dumps(payload)[:-1] + ',"budget":{"maximum_cost":"0.1","currency":"USD"}}'
        self.assertEqual(self.request('/v1/data/jobs', method='POST', raw=raw.encode(), idempotency='duplicate-json')[0], 400)
        self.assertEqual(self.request('/v1/data/jobs', method='POST', payload=payload)[0], 400)
        self.ready = True
        self.consume = False
        job = self.submit()
        code, canceled = self.json(job['status_url'] + '/cancel', method='POST', payload={})
        self.assertEqual(code, 200)
        self.assertEqual((canceled['status'], canceled['actual_cost']), ('canceled', '0'))
        self.assertEqual(self.request(job['status_url'] + '/pause', method='POST', payload={})[0], 405)
        self.assertEqual(self.request('/v1/data/jobs')[0], 405)
        self.assertEqual(self.request('/v1/data/observations?country=US&country=GB')[0], 400)
        self.assertEqual(self.request('/v1/data/ai/answers?capability=keyword_volume')[0], 400)

    def test_owner_can_stop_inflight_spending_after_source_revocation(self):
        self.consume = False
        job = self.submit()
        claim = self.data.claim('trends_history', self.provider, lambda tenant, c: self.grants[c])
        self.assertEqual(claim['job']['id'], job['id'])
        self.grants['trends_history']['status'] = 'revoked'
        code, response = self.json(job['status_url'] + '/cancel', method='POST', payload={})
        self.assertEqual(code, 200)
        self.assertTrue(response['cancel_requested'])
        self.assertEqual(response['reservation_status'], 'held')
        canceled = self.data.complete(claim['tenant_id'], job['id'], claim['lease_token'],
                                     {'content': 'must be discarded without persistence'}, '0.1', self.grants['trends_history'], event_key=job['id'])
        self.assertEqual((canceled['status'], canceled['actual_cost'], canceled['result_ref']), ('canceled', '0.1', None))
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'], 0)

    def test_wrong_filters_limits_ids_and_budget_denial_are_structured(self):
        for path in ('/v1/data/observations?device=desktop', '/v1/data/observations?limit=51',
                     '/v1/data/observations?from=not-a-time', '/v1/jobs?domain=unknown',
                     '/v1/data/ai/answers/bad-object/blocks', '/v1/data/export?capability=invalid'):
            code, error = self.json(path)
            self.assertEqual(code, 400, error)
            self.assertIn('code', error)
            self.assertIn('request_id', error)
        self.data.configure_budget(str(self.account['account_id']), 'USD', '0', '0')
        p = dict(parameters(), budget=dict(maximum_cost='0.5', currency='USD'))
        code, error = self.json('/v1/data/jobs', method='POST', payload=p, idempotency='budget-denied')
        self.assertEqual((code, error['code']), (429, 'budget_exceeded'))
        self.assertEqual(self.data.list(str(self.account['account_id']))['jobs'], [])

    def test_preview_is_read_only_explicit_money_ceiling_with_no_source_call(self):
        self.ready = False
        code, preview = self.json('/v1/data/jobs/preview', method='POST', payload=parameters('keyword_volume'))
        self.assertEqual(code, 200, preview)
        self.assertEqual(preview['maximum_cost'], '0.40')
        self.assertFalse(preview['reservation_created'])
        self.assertFalse(preview['executor_available'])
        self.assertFalse(preview['legacy_search_quota_debited'])
        self.assertEqual(preview['budget_snapshot']['daily_remaining'], '100')
        self.assertEqual(self.data.list(str(self.account['account_id']))['jobs'], [])
        with self.data.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'], 0)
        self.assertEqual(self.request('/v1/data/jobs/preview', method='POST', payload=parameters(), site=False)[0], 401)


    def test_new_domain_subscription_http_contract_and_complete_files(self):
        job = self.wait(self.submit())
        root = '/v1/data/subscriptions'
        request = dict(domain='data', name='new data fixture', filters=dict(capability='trends_history'), start_mode='history')
        for kwargs in (dict(site=False), dict(bearer=False)):
            self.assertEqual(self.json(root + '?domain=data', **kwargs)[0], 401)
        code, sub = self.json(root, method='POST', payload=request, idempotency='sub-fixture')
        self.assertEqual(code, 200, sub)
        self.assertFalse(sub['active'])
        sid = sub['id']; url = root + '/' + sid
        self.assertEqual(self.json(root + '?domain=data')[1]['subscriptions'][0]['id'], sid)
        self.assertEqual(self.json(root)[1]['subscriptions'], [])
        self.assertEqual(self.json(url + '/status', key=self.other['api_key'])[0], 404)
        self.assertEqual(self.json(url + '/resume', method='POST', payload={})[0], 200)
        code, batch = self.json(url + '/pull', method='POST', payload={})
        self.assertEqual(code, 200, batch)
        self.assertEqual(batch['observation']['observation_id'], job['result_ref'])
        b_url = url + '/batches/' + batch['batch_id']
        files = {}
        for fmt in ('json', 'jsonl', 'csv', 'zip', 'manifest'):
            code, raw, _ = self.request(b_url + '/export?format=' + fmt)
            self.assertEqual(code, 200, raw[:100]); files[fmt] = raw
        manifest = json.loads(files['manifest'])
        self.assertEqual(manifest['files']['batch.json']['sha256'], hashlib.sha256(files['json']).hexdigest())
        self.assertEqual(json.loads(files['json'])['payload']['event_id'], job['result_ref'])
        self.assertEqual(self.json(url + '/pull?limit=1', method='POST', payload={})[0], 400)
        self.assertEqual(self.json(b_url + '/ack', method='POST', payload=dict(sha256='wrong'))[0], 409)
        self.grants['trends_history']['status'] = 'revoked'
        self.assertEqual(self.request(b_url + '/export?format=zip')[0], 409)
        self.assertEqual(self.json(b_url + '/ack', method='POST', payload=dict(sha256=batch['sha256']))[0], 409)
        self.grants['trends_history']['status'] = 'approved'
        self.assertEqual(self.json(b_url + '/ack', method='POST', payload=dict(sha256=batch['sha256']))[0], 200)
        self.assertEqual(self.request(b_url + '/export?format=zip')[1], files['zip'])
        self.assertEqual(self.json(url + '/status')[1]['pending_observations'], 0)
        self.assertEqual(self.json(url + '/batches?limit=1')[1]['batches'][0]['id'], batch['batch_id'])
        self.assertEqual(self.json(root, method='POST', payload=dict(request, mode='new_urls'), idempotency='bad-sub')[0], 400)

    def test_unmounted_new_subscriptions_do_not_touch_legacy(self):
        del self.server.data_capability_runtime
        code, error = self.json('/v1/data/subscriptions?domain=data')
        self.assertEqual(code, 503, error)
        self.assertEqual(self.json('/v1/data/subscriptions')[0], 200)
        code, error = self.json('/v1/data/subscriptions', method='POST', payload=dict(domain='data', name='no runtime', filters=dict(capability='trends_history')), idempotency='disabled-sub')
        self.assertEqual(code, 503, error)
        self.assertEqual(self.json('/v1/data/subscriptions')[1]['subscriptions'], [])
