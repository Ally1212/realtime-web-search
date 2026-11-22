import io
import json
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from realtime.quick_request_http import handle, receipt


class QuickRequestTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, SEARCHFLOW_PRODUCT_ENABLED='true')
        self.env.start(); self.addCleanup(self.env.stop)
        self.key = patch('realtime.quick_request_http.secret', return_value='server-only')
        self.key.start(); self.addCleanup(self.key.stop)
        self.secure = patch('realtime.product_http.secure', return_value=True)
        self.secure.start(); self.addCleanup(self.secure.stop)
        self.store_patch = patch('realtime.quick_request_http.ProductStore')
        self.store = self.store_patch.start().return_value
        self.addCleanup(self.store_patch.stop)
        self.store.authenticate.return_value = {'id': 'site-account'}

    def handler(self, path='/api/quick-request', payload=None, **headers):
        raw = json.dumps(payload or {}).encode()
        return SimpleNamespace(path=path, headers={
            'X-SearchFlow-Quick': '1', 'Host': 'searchflow.test',
            'Origin': 'https://searchflow.test', 'Content-Length': str(len(raw)),
            'Content-Type': 'application/json', 'Idempotency-Key': 'test', **headers},
            config=SimpleNamespace(database_url='unused'), rfile=io.BytesIO(raw), send_json=Mock())

    def test_submit_uses_server_account_and_returns_only_receipt(self):
        self.store.submit.return_value = ('12345678-1234-1234-1234-123456789abc', False)
        h = self.handler(payload={'query': 'battery', 'source': 'google_web'})
        handle(h, 'POST')
        result, status = h.send_json.call_args.args
        self.assertEqual(status, 202)
        self.assertNotIn('server-only', json.dumps(result))
        self.assertEqual(result['token'], receipt('server-only', result['id']))
        self.assertEqual(self.store.submit.call_args.args[1]['provider'], 'middle')

    def test_feed_sources_use_middle_without_local_fallback(self):
        self.store.submit.return_value=('12345678-1234-1234-1234-123456789abc',False)
        for source in ('google_news','google_trends'):
            handle(self.handler(payload={'query':'battery','source':source}),'POST')
            self.assertEqual(self.store.submit.call_args.args[1]['provider'],'middle')

    def test_cancel_lost_submission_uses_existing_account_job_only(self):
        jid = '12345678-1234-1234-1234-123456789abc'
        idem = 'quick-5298da1f-5059-4769-a698-1a89abfc115e'
        self.store.cancel_quick_submission.return_value = {'id': jid, 'status': 'cancelled'}
        h = self.handler('/api/quick-request/cancel-submission', **{'Idempotency-Key': idem})
        handle(h, 'POST')
        self.store.cancel_quick_submission.assert_called_once_with({'id': 'site-account'}, idem)
        self.store.submit.assert_not_called()
        h.send_json.assert_called_once_with({'id': jid, 'status': 'cancelled', 'token': receipt('server-only', jid)})
        invalid = self.handler('/api/quick-request/cancel-submission')
        handle(invalid, 'POST')
        self.assertEqual(invalid.send_json.call_args.args[1], 400)
        self.assertEqual(self.store.cancel_quick_submission.call_count, 1)

    def test_images_without_middle_contract_do_not_start_local_job(self):
        h = self.handler(payload={'query': 'battery', 'source': 'google_images'})
        handle(h, 'POST')
        self.assertEqual(h.send_json.call_args.args[1], 409)
        self.store.submit.assert_not_called()

    def test_reject_cross_origin_and_missing_flow_header_before_submit(self):
        for headers in ({'Origin': 'https://other.test'}, {'X-SearchFlow-Quick': ''}):
            h = self.handler(**headers); handle(h, 'POST')
            self.assertEqual(h.send_json.call_args.args[1], 403)
        self.store.submit.assert_not_called()

    def test_receipt_is_bound_to_job_and_server_account(self):
        job = '12345678-1234-1234-1234-123456789abc'
        h = self.handler('/api/quick-request/'+job, **{'X-Quick-Token': receipt('other', job)})
        handle(h, 'GET')
        self.assertEqual(h.send_json.call_args.args[1], 404)
        self.store.job.assert_not_called()
        h = self.handler('/api/quick-request/'+job, **{'X-Quick-Token': receipt('server-only', job)})
        handle(h, 'GET')
        self.store.job.assert_called_once_with('site-account', job)

    def test_extra_parameters_and_unbounded_routes_are_rejected(self):
        h = self.handler(payload={'query': 'test', 'provider': 'serper'})
        handle(h, 'POST')
        self.assertEqual(h.send_json.call_args.args[1], 400)
        for suffix in ('/keys', '/jobs', '/12345678-1234-1234-1234-123456789abc/retry'):
            h = self.handler('/api/quick-request'+suffix)
            handle(h, 'GET')
            self.assertEqual(h.send_json.call_args.args[1], 404)
        self.store.submit.assert_not_called()

    def test_history_always_reads_public_data_and_returns_export_receipts(self):
        rid = '12345678-1234-1234-1234-123456789abc'
        with patch('realtime.customer_data.CustomerDataStore') as cls:
            cls.return_value.requests.return_value = {'requests': [{'request_id': rid}], 'has_more': False}
            h = self.handler('/api/quick-request/history', payload={'source': 'google_web', 'limit': '5'})
            handle(h, 'POST')
            p = cls.return_value.requests.call_args.args[0]
            self.assertEqual(p['scope'], 'public')
            self.assertEqual(p['field_contract'], 'prd-v1')
            self.assertEqual(h.send_json.call_args.args[0]['receipts'][rid], receipt('server-only', 'public:'+rid))
        self.store.submit.assert_not_called()

    def test_history_rejects_scope_injection_and_invalid_types(self):
        for payload in ({'scope':'own'}, {'account_id':'other'}, {'limit':50}, {'limit':[]}, {'field_contract':'legacy'}):
            h = self.handler('/api/quick-request/history', payload=payload)
            handle(h, 'POST')
            self.assertEqual(h.send_json.call_args.args[1], 400)

    def test_record_and_export_recheck_public_visibility(self):
        rid = '12345678-1234-1234-1234-123456789abc'
        with patch('realtime.customer_data.CustomerDataStore') as cls, patch('realtime.customer_export.export_record') as export:
            cls.return_value.request.return_value = {'request_id':rid}
            h = self.handler('/api/quick-request/record', payload={'request_id':rid})
            handle(h, 'POST')
            cls.return_value.request.assert_called_with(rid, {'scope':'public', 'field_contract':'prd-v1'})
            cls.return_value.request.reset_mock()
            h = self.handler('/api/quick-request/records/'+rid+'/export?format=csv', **{'X-Quick-Token':receipt('server-only', rid)})
            handle(h, 'GET')
            self.assertEqual(h.send_json.call_args.args[1], 404)
            cls.return_value.request.assert_not_called()
            h = self.handler('/api/quick-request/records/'+rid+'/export?format=csv', **{'X-Quick-Token':receipt('server-only', 'public:'+rid)})
            handle(h, 'GET')
            cls.return_value.request.assert_called_with(rid, {'scope':'public', 'field_contract':'prd-v1'})
            export.assert_called_once()

    def test_signed_job_data_reads_only_owned_job(self):
        jid = '12345678-1234-1234-1234-123456789abc'
        with patch('realtime.customer_data.CustomerDataStore') as cls:
            cls.return_value.job_data.return_value = {'request_id':jid}
            h = self.handler('/api/quick-request/'+jid+'/data', **{'X-Quick-Token':receipt('server-only', jid)})
            handle(h, 'GET')
            cls.assert_called_once_with(self.store, 'site-account')
            cls.return_value.job_data.assert_called_once_with(jid, 'prd-v1')

    def test_private_record_is_unavailable_on_public_route(self):
        from realtime.product import ProductError
        with patch('realtime.customer_data.CustomerDataStore') as cls:
            cls.return_value.request.side_effect = ProductError('not_found', '记录不存在', 404)
            h = self.handler('/api/quick-request/record', payload={'request_id':'12345678-1234-1234-1234-123456789abc'})
            handle(h, 'POST')
            self.assertEqual(h.send_json.call_args.args[1], 404)

    def test_invalid_history_filter_reports_domain_error_as_400(self):
        from realtime.google_domain import DomainError
        with patch('realtime.customer_data.CustomerDataStore') as cls:
            cls.return_value.requests.side_effect = DomainError('invalid_argument','Invalid cursor')
            h = self.handler('/api/quick-request/history', payload={'cursor':'invalid'})
            handle(h,'POST')
            self.assertEqual(h.send_json.call_args.args[1],400)
