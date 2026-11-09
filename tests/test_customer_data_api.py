import copy
import csv
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from realtime.customer_data import CustomerDataStore, field_catalog, project, selection
from realtime.customer_export import FULL_COLUMNS, export_record, snapshot_download
from realtime.customer_jobs import list_jobs
from realtime.google_domain import DomainError
from realtime.google_data_export import export_public_data, verify_bundle
from realtime.product import ProductError, ProductService
from realtime.product_http import handle, openapi
from tests import test_product as product_tests


class CustomerDataUnitTests(unittest.TestCase):
    def test_full_csv_preserves_topics_features_raw_and_formula_safety(self):
        row = dict(request_id='00000000-0000-0000-0000-000000000001', source='google_trends',
                   query='=unsafe', page=1, classification='results', completed_at='2026-11-09T00:00:00Z',
                   response={'trending_searches': [dict(keyword='Topic', approx_traffic='100+', articles=[
                       dict(title='=1+1', link='https://example.com/a', author='Author', publisher='Media')])],
                       'serp_features': [dict(type='ai_summary', links=[dict(url='https://example.com/b')])]})
        record = project(row)
        before = copy.deepcopy(record)
        handler = Mock()
        export_record(handler, record, 'csv')
        rows = list(csv.DictReader(io.StringIO(handler.send_body.call_args.args[0].decode('utf-8-sig'))))
        self.assertEqual([r['record_type'] for r in rows], ['request', 'result', 'topic', 'feature'])
        self.assertEqual(json.loads(rows[0]['record_json']), record)
        self.assertEqual(rows[1]['title'], "'=1+1")
        self.assertEqual(json.loads(rows[1]['source_fields_json'])['title'], '=1+1')
        self.assertEqual(json.loads(rows[3]['target_urls_json']), ['https://example.com/b'])
        self.assertEqual(rows[1]['author'], 'Author')
        self.assertEqual(record, before)
        export_record(handler, record, 'jsonl')
        self.assertEqual(json.loads(handler.send_body.call_args.args[0]), record)

    def test_docs_cover_data_and_operator_features_without_mixing_permissions(self):
        document = openapi()
        paths = document['paths']
        for path in ('/v1/data/requests', '/v1/data/export', '/v1/data/fields', '/v1/data/quality',
                     '/v1/jobs/{id}/data', '/v1/jobs/{id}/progress', '/v1/jobs/{id}/pause',
                     '/v1/search/batch', '/v1/analytics/rankings', '/v1/google/legacy-data',
                     '/api/whale/jobs', '/api/collector-settings'):
            self.assertIn(path, paths)
        self.assertIn('siteAccess', document['components']['securitySchemes'])
        self.assertEqual(paths['/api/whale/settings']['post']['security'], [{'adminAuth': [], 'siteAccess': []}])
        self.assertNotIn('adminAuth', document['security'][0])
        fields = field_catalog()
        self.assertIn('source_observed_at', fields['enhanced_run_fields'])
        self.assertIn('citation_urls', fields['feature_fields'])
        self.assertIn('record_json', FULL_COLUMNS)

    def test_selection_defaults_and_bad_scope(self):
        aid = '00000000-0000-0000-0000-000000000002'
        where, args, _, scope, contract = selection({'source': 'google_news'}, aid)
        self.assertEqual((scope, contract), ('own', 'prd-v1'))
        self.assertEqual(args, [aid, 'google_news'])
        self.assertIn('sf_jobs', where[1])
        with self.assertRaises(ProductError):
            selection({'scope': 'all_tenants'}, aid)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CustomerDataDatabaseTests(unittest.TestCase):
    setUp = product_tests.ProductIntegrationTests.setUp
    tearDown = product_tests.ProductIntegrationTests.tearDown

    def allow_more(self):
        with self.base.connect() as db:
            db.execute('UPDATE sf_accounts SET monthly_limit=100,concurrency=10')

    def record(self, *, source='google_web', private=True, owner=None, query='query', response=None):
        owner = owner or self.owner
        if private:
            jid, _ = self.store.submit(owner, dict(query=query, source=source), 'record-' + query + '-' + source, self.config)
            with self.base.connect() as db:
                cid = db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s', (jid,)).fetchone()['campaign_id']
        else:
            task = self.console.create_task(dict(keywords=[query], sources=[source], start=False), self.config)
            cid, jid = task['campaign_id'], None
        fields = {'google_web': 'organic_results', 'google_news': 'news_results', 'google_images': 'image_results'}
        if response is None:
            response = {fields[source]: [dict(title='Saved', link='https://example.com/' + query,
                                             position=1, author='Author', publisher='Media')]}
        rid = self.console.record(str(cid), source=source, provider='openserp', query=query, page=1,
                                  language='en', response=response, elapsed_seconds=.1,
                                  classification='results', http_status=200)
        return jid, str(cid), rid

    def test_own_public_accessible_and_foreign_cache_origin_are_isolated(self):
        self.allow_more()
        _, _, mine = self.record(query='mine')
        _, _, public = self.record(query='public', private=False)
        other = self.store.authenticate(self.other['api_key'], rate_limit=False)
        _, _, foreign = self.record(query='foreign', owner=other)
        data = CustomerDataStore(self.store, self.owner['id'])
        ids = lambda p: {r['request_id'] for r in data.requests(p)['requests']}
        self.assertEqual(ids({}), {mine})
        self.assertEqual(ids({'scope': 'public'}), {public})
        self.assertEqual(ids({'scope': 'accessible'}), {mine, public})
        for scope in ('own', 'public', 'accessible'):
            with self.assertRaises(ProductError) as error:
                data.request(foreign, {'scope': scope})
            self.assertEqual(error.exception.status, 404)
        # Historical buggy cache associations must not become a read grant.
        jid, _ = self.store.submit(self.owner, {'query': 'cache'}, 'cache', self.config)
        with self.base.connect() as db:
            cid = db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s', (jid,)).fetchone()['campaign_id']
            db.execute('INSERT INTO serp_campaign_requests(campaign_id,request_id,reused) VALUES(%s,%s,true)', (cid, foreign))
        self.assertNotIn(foreign, ids({'scope': 'accessible'}))

    def test_private_job_full_data_sections_and_legacy_identity(self):
        jid, _, rid = self.record(response={'organic_results': [dict(link='https://example.com/', author='Author')],
                                          'serp_features': [dict(type='people_also_ask', items=[dict(title='Q', text='error')])]})
        ProductService(self.config, Mock()).tick()
        data = CustomerDataStore(self.store, self.owner['id'])
        result = data.job_data(jid)
        self.assertEqual(result['request_id'], rid)
        self.assertEqual(result['field_adaptation']['items'][0]['author'], 'Author')
        self.assertEqual(data.section(rid, 'features', {})['items'][0]['feature_type'], 'people_also_ask')
        self.assertEqual(data.section(rid, 'items', {'limit': '1', 'offset': '1'})['items'], [])
        old = data.request(rid, {'field_contract': 'legacy'})
        self.assertNotIn('field_adaptation', old)
        self.assertEqual(old['occurrences'], result['occurrences'])
        with self.assertRaises(ProductError):
            CustomerDataStore(self.store, self.other['account_id']).job_data(jid)

    def test_quality_counts_samples_and_snapshots_use_same_scope(self):
        self.allow_more()
        self.record(query='one')
        self.record(query='two')
        self.record(query='public', private=False)
        data = CustomerDataStore(self.store, self.owner['id'])
        quality = data.quality({'sample_limit': '1'})
        self.assertEqual(quality['totals']['requests'], 2)
        self.assertTrue(quality['truncated'])
        self.assertEqual(quality['sample']['requests'], 1)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'export'
            manifest = export_public_data(self.dsn, output, filters={'field_contract': 'prd-v1'}, account_id=self.owner['id'], max_requests=1)
            self.assertEqual(manifest['scope'], 'own')
            self.assertTrue(manifest['truncated'])
            self.assertEqual(verify_bundle(output)['requests'], 1)
            record = json.loads((output / 'requests.jsonl').read_text())
            detail = data.request(record['request_id'])
            self.assertEqual(record, detail)
            with self.assertRaises(ValueError):
                export_public_data(self.dsn, Path(temp) / 'too-big', account_id=self.owner['id'], max_bytes=1)
            self.assertFalse((Path(temp) / 'too-big').exists())

    def test_batch_atomic_rollback_replay_and_changed_payload_conflict(self):
        payload = {'searches': [{'query': 'one'}, {'query': 'two'}]}
        result = self.store.submit_batch(self.owner, payload, 'batch-one', self.config)
        self.assertEqual(len(result['jobs']), 2)
        replay = self.store.submit_batch(self.owner, payload, 'batch-one', self.config)
        self.assertEqual([j['id'] for j in result['jobs']], [j['id'] for j in replay['jobs']])
        self.assertEqual(self.store.usage(self.owner['id'])['reserved'], 2)
        with self.assertRaises(ProductError) as err:
            self.store.submit_batch(self.owner, {'searches': [{'query': 'changed'}]}, 'batch-one', self.config)
        self.assertEqual(err.exception.status, 409)
        # A fresh batch over quota rolls back even its first child.
        for job in result['jobs']:
            self.store.finish(job['id'], 'cancelled')
        with self.assertRaises(ProductError):
            self.store.submit_batch(self.owner, {'searches': [{'query': str(i)} for i in range(3)]}, 'too-many', self.config)
        self.assertEqual(self.store.usage(self.owner['id'])['reserved'], 0)
        self.assertEqual(len(self.store.jobs(self.owner['id'])), 2)

    def test_pause_progress_resume_deadline_and_foreign_control(self):
        jid, _ = self.store.submit(self.owner, {'query': 'pause'}, 'pause', self.config)
        paused = self.store.job_action(self.owner['id'], jid, 'pause')
        self.assertEqual(paused['execution_state'], 'paused')
        queue = Mock()
        ProductService(self.config, queue).tick()
        queue.enqueue.assert_not_called()
        progress = CustomerDataStore(self.store, self.owner['id']).progress(jid)
        self.assertIsNotNone(progress['progress'])
        resumed = self.store.job_action(self.owner['id'], jid, 'resume')
        self.assertEqual(resumed['deadline_at'], paused['deadline_at'])
        ProductService(self.config, queue).tick()
        queue.enqueue.assert_called()
        with self.assertRaises(ProductError):
            self.store.job_action(self.other['account_id'], jid, 'pause')
        with self.base.connect() as db:
            db.execute("UPDATE sf_jobs SET deadline_at=now()-interval '1 second' WHERE id=%s", (jid,))
        with self.assertRaises(ProductError):
            self.store.job_action(self.owner['id'], jid, 'resume')

    def test_jobs_keyset_filters_do_not_repeat_or_leak(self):
        self.allow_more()
        for i in range(3):
            self.record(query='page-' + str(i))
        first = list_jobs(self.store, self.owner['id'], {'limit': '2'})
        second = list_jobs(self.store, self.owner['id'], {'limit': '2', 'cursor': first['next_cursor']})
        self.assertEqual(len(first['jobs']), 2)
        self.assertEqual(len(second['jobs']), 1)
        self.assertFalse(set(str(j['id']) for j in first['jobs']) & set(str(j['id']) for j in second['jobs']))
        self.assertEqual(list_jobs(self.store, self.other['account_id'], {})['jobs'], [])
        self.assertEqual(len(list_jobs(self.store, self.owner['id'], {'query': 'page-1'})['jobs']), 1)

    def test_exact_keyword_new_link_filters_and_read_indexes(self):
        from scripts.customer_api_indexes import install
        self.allow_more()
        _, _, one = self.record(query='needle')
        self.record(query='needle longer')
        data = CustomerDataStore(self.store, self.owner['id'])
        self.assertEqual(len(data.requests({'query': 'needle'})['requests']), 2)
        self.assertEqual([r['request_id'] for r in data.requests({'query': 'needle', 'query_match': 'exact'})['requests']], [one])
        self.assertEqual(len(data.requests({'new_only': 'true'})['requests']), 2)
        result = install(self.dsn)
        self.assertEqual(len(result['indexes']), 3)
        self.assertTrue(all(result['indexes'].values()))
        self.assertEqual(install(self.dsn), result)

    def test_http_data_exports_customer_permissions_and_rejected_filters(self):
        jid, _, rid = self.record(response={'organic_results': [dict(title='Title', link='https://example.com/', author='Author')]})
        self.record(query='public', private=False)
        ProductService(self.config, Mock()).tick()
        config = self.config
        class Handler(BaseHTTPRequestHandler):
            queue = Mock()
            def send_body(self, body, content_type, status=200):
                self.send_response(status); self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def send_json(self, payload, status=200):
                self.send_body(json.dumps(payload, default=str).encode(), 'application/json', status)
            def do_GET(self): handle(self, 'GET')
            def do_POST(self): handle(self, 'POST')
            def log_message(self, *args): pass
        Handler.config = config
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def get(path, key=None):
            request = Request('http://127.0.0.1:' + str(server.server_port) + path,
                              headers={'Authorization': 'Bearer ' + (key or self.account['api_key'])})
            try:
                with urlopen(request, timeout=30) as response:
                    return response.status, response.read(), response.headers
            except HTTPError as error:
                return error.code, error.read(), error.headers
        try:
            status, raw, _ = get('/v1/jobs/' + jid + '?field_contract=prd-v1')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['data_record']['request_id'], rid)
            self.assertEqual(get('/v1/jobs/' + jid + '/data', self.other['api_key'])[0], 404)
            status, raw, _ = get('/v1/jobs/' + jid + '/export?profile=full&format=csv')
            self.assertEqual(status, 200)
            rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            self.assertEqual(rows[1]['author'], 'Author')
            status, raw, _ = get('/v1/data/export?format=zip')
            self.assertEqual(status, 200)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertIn('full.csv', archive.namelist())
                with tempfile.TemporaryDirectory() as temp:
                    archive.extractall(temp)
                    self.assertEqual(verify_bundle(Path(temp) / 'bundle')['requests'], 1)
            self.assertEqual(get('/v1/data/requests?scope=all')[0], 400)
            self.assertEqual(get('/v1/data/requests?limit=1&limit=2')[0], 400)
            self.assertEqual(get('/v1/analytics/summary?scope=own')[0], 400)
            status, raw, _ = get('/v1/analytics/summary')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['summary']['requests'], 1)
            status, raw, _ = get('/v1/analytics/overview?format=csv')
            self.assertEqual(status, 200)
            report_rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            self.assertIn('daily', {row['section'] for row in report_rows})
            self.assertEqual(get('/api/product/admin/accounts', self.account['api_key'])[0], 401)
        finally:
            server.shutdown(); server.server_close(); thread.join()
