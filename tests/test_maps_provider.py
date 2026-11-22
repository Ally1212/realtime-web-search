import copy
import csv
import io
import json
import os
from pathlib import Path
from uuid import uuid4
import unittest
from unittest.mock import Mock, patch
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from realtime.maps_provider import prepare, normalize, VERSION
from realtime.maps_export import content
from realtime.google_local_jobs import LocalJobStore, parameters
from tests.maps_job_fixture import process_pending
from realtime.product import ProductStore, ProductError
from realtime.campaign_store import CampaignStore
from realtime.config import Config
from realtime.serp_console import ConsoleStore


def result():
    raw=[dict(title='Cafe',address='address',data_id='id:1',latitude=1.29,longtitude=103.85,review_rating=4.5,review_count=10,phone='+123',web_site='https://example.com',categories=['Cafe'],open_hours={'Monday':['9–5']})]
    p=prepare(parameters(dict(query='coffee',city='Singapore',region='SG')))
    return dict(places=normalize(raw,p),provider='gosom',provider_version=VERSION,ordering='distance',middle_verified=False,notice='fixture',provider_request_id=None,response_hash='a'*64,field_mask='gosom-fast-v1',sku='self_hosted')


class MapsTests(unittest.TestCase):
    def test_browser_scan_keeps_in_radius_places_when_upstream_returns_outside_rows(self):
        self.check_browser_result(partial_timeout=False)

    def test_browser_timeout_preserves_complete_rows_and_reports_shortfall(self):
        self.check_browser_result(partial_timeout=True)

    def test_unconfirmed_browser_timeout_still_fails(self):
        with self.assertRaises(ProductError) as caught:
            self.check_browser_result(partial_timeout=True, confirmed=False)
        self.assertEqual(caught.exception.code, 'maps_timeout')

    def test_deep_scan_reserves_delivery_time_and_uses_remaining_budget(self):
        from datetime import datetime, timedelta, timezone

        self.check_browser_result(partial_timeout=False,
                                  deadline_at=datetime.now(timezone.utc) + timedelta(seconds=480))

    def check_browser_result(self, *, partial_timeout, confirmed=True, deadline_at=None):
        import hashlib
        import subprocess
        from tempfile import TemporaryDirectory
        from realtime import maps_provider

        center = {'lat': 47.6062, 'lng': -122.3321}
        p = parameters(dict(query='coffee', location=center, radius_m=1500, max_places=100))
        inside = dict(title='Inside', data_id='id:inside', latitude=47.6062, longitude=-122.3321)
        outside = dict(title='Outside', data_id='id:outside', latitude=47.6262, longitude=-122.3321)

        wait = Mock(return_value=0)

        def provider(args, *, stdout, **kwargs):
            response = json.dumps([inside, outside, inside]) + '\n'
            if partial_timeout:
                response += '{"truncated":'
            Path(args[args.index('-results') + 1]).write_text(response)
            if confirmed:
                stdout.write(b'{"status":"success"}\n')
            if partial_timeout:
                wait.side_effect = [subprocess.TimeoutExpired(args, 360), 0]
            return Mock(wait=wait, pid=234)

        with TemporaryDirectory() as folder:
            binary = Path(folder) / 'scraper'
            binary.write_bytes(b'fixture')
            with patch.object(maps_provider, 'config', return_value={'enabled': True, 'binary': str(binary)}), \
                 patch.object(maps_provider, 'BINARY_SHA256', hashlib.sha256(b'fixture').hexdigest()), \
                 patch.object(maps_provider.subprocess, 'Popen', side_effect=provider) as start, \
                 patch.object(maps_provider.os, 'killpg') as kill:
                observed = maps_provider.search(p, deadline_at=deadline_at)
        self.assertEqual(start.call_args.args[0][start.call_args.args[0].index('-depth') + 1], '17')
        timeout = wait.call_args_list[0].kwargs['timeout']
        self.assertTrue(418 < timeout <= 420 if deadline_at is not None else timeout == 360)
        self.assertEqual([place['data_id'] for place in observed['places']], ['id:inside'])
        self.assertEqual(observed['raw_response'], [inside, inside])
        self.assertEqual(observed['coverage']['provider_occurrence_count'], 3)
        self.assertEqual(observed['coverage']['out_of_radius_occurrence_count'], 1)
        self.assertEqual(observed['coverage']['duplicate_occurrence_count'], 1)
        expected = 'provider_timeout' if partial_timeout else 'provider_scan_ended'
        self.assertEqual(observed['coverage']['stop_reason'], expected)
        self.assertEqual(kill.call_count, int(partial_timeout))

    def test_normalize_does_not_invent_place_id_rating_or_safe_website(self):
        p=prepare(parameters(dict(query='coffee',city='Singapore')))
        r=result()['places'][0]['source_fields'];r.update(review_rating=0,review_count=0,web_site='javascript:alert(1)')
        place=normalize([r],p)[0]
        self.assertIsNone(place['rating']);self.assertIsNone(place['user_rating_count'])
        self.assertIsNone(place['place_id']);self.assertIsNone(place['website_uri'])
        self.assertEqual(place['source_fields']['data_id'],'id:1')

    def test_hundred_place_contract_and_duplicate_coverage_are_explicit(self):
        from realtime.maps_provider import availability
        self.assertEqual(availability()['max_results'],100)
        self.assertEqual(parameters(dict(query='coffee',city='Singapore',max_places=100))['max_places'],100)
        with self.assertRaises(ProductError):parameters(dict(query='coffee',city='Singapore',max_places=101))
        p=prepare(parameters(dict(query='coffee',city='Singapore',max_places=100)))
        raw=result()['places'][0]['source_fields']
        self.assertEqual(len(normalize([raw,raw],p)),1)

    def test_preparation_is_idempotent_and_does_not_repeat_type_hint(self):
        p=parameters(dict(query='coffee',included_type='cafe',city='Singapore'))
        self.assertEqual(prepare(prepare(p)),prepare(p))
        self.assertEqual(prepare(p)['query'],'coffee')

    def test_nearby_city_and_location_precedence(self):
        p=prepare(parameters(dict(mode='nearby',included_type='dentist',city='London')))
        self.assertEqual(p['query'],'dentist')
        self.assertEqual(p['location'],dict(lat=51.5074,lng=-0.1278))
        p=prepare(parameters(dict(mode='nearby',included_type='cafe',city='London',location=dict(lat=1,lng=2))))
        self.assertEqual(p['location'],dict(lat=1,lng=2))

    def test_bad_optional_fields_and_parameter_types_are_rejected(self):
        p=prepare(parameters(dict(query='coffee',city='Singapore')))
        raw=result()['places'][0]['source_fields']
        for field,value in [('categories','Cafe'),('categories',[1]),('phone',{}),('address',42),('open_hours',[])]:
            with self.subTest(field=field,value=value),self.assertRaises(ProductError):
                normalize([dict(raw,**{field:value})],p)
        for payload in [dict(query='coffee',city='Singapore',region=[]),
                        dict(query='coffee',location=dict(lat=float('nan'),lng=1)),
                        dict(query='coffee',city='Singapore',radius_m=True)]:
            with self.subTest(payload=payload),self.assertRaises(ProductError):parameters(payload)

    def test_invalid_or_outside_results_and_multiline_queries_fail(self):
        p=prepare(parameters(dict(query='coffee',city='Singapore')))
        raw=result()['places'][0]['source_fields']
        for value in [[dict(raw,latitude=90)],[dict(raw,data_id='')],[dict(raw,latitude=float('nan'))]]:
            with self.assertRaises(ProductError):normalize(value,p)
        self.assertEqual(len(normalize([raw,raw],p)),1)
        for payload in [dict(query='a\nb',city='Singapore')]:
            with self.assertRaises(ProductError):prepare(parameters(payload))

    def test_csv_and_jsonl_preserve_raw_fields_and_formula_safe_phone(self):
        job=dict(id='1',parameters={'query':'coffee'},completed_at='now',result_cache=result())
        parsed=list(csv.DictReader(io.StringIO(content(job,'csv'))))
        self.assertEqual(parsed[0]['national_phone_number'],"'+123")
        self.assertEqual(json.loads(parsed[0]['source_fields_json']),job['result_cache']['places'][0]['source_fields'])
        self.assertEqual(json.loads(parsed[0]['record_json']),job['result_cache']['places'][0])
        self.assertEqual(json.loads(content(job,'jsonl')),job)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'isolated PostgreSQL required')
class MapsDBTests(unittest.TestCase):
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='maps_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn=make_conninfo(self.root,options=f'-c search_path={self.schema}')
        self.base=CampaignStore(self.dsn)
        self.console=ConsoleStore(self.dsn);self.console.initialize();self.console.initialize_console(Config(database_url=self.dsn))
        self.product=ProductStore(self.dsn);self.product.initialize()
        self.account=self.product.create_account(dict(name='maps',monthly_limit=3,concurrency=2))
        self.owner=self.product.authenticate(self.account['api_key'],rate_limit=False)
        self.jobs=LocalJobStore(self.product.base)
        self.env=patch.dict(os.environ,{'SEARCHFLOW_MAPS_WORKER':'true'});self.env.start()
        self.available=patch('realtime.google_local_jobs.availability',return_value={'enabled':True});self.available.start()
    def tearDown(self):
        self.env.stop();self.available.stop();self.product.base.pool.close();self.base.pool.close()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
    def submit(self,idem='one'):
        return self.jobs.submit(self.owner,dict(query='coffee',city='Singapore'),idem)[0]
    def test_idempotency_success_snapshot_tenant_and_expiry(self):
        jid=self.submit();self.assertEqual(self.submit(),jid)
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'],1)
        with patch('tests.maps_job_fixture.fetch_search',return_value=result()) as call:process_pending(self.product.base);call.assert_called_once()
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'],0)
        self.assertEqual(self.product.usage(self.owner['id'])['used'],1)
        job=self.jobs.job(self.owner['id'],jid,include_results=True)
        self.assertEqual(job['result_cache']['places'],result()['places'])
        self.assertIn('location',job['parameters'])
        with self.assertRaises(ProductError):self.jobs.job(str(uuid4()),jid)
        with self.product.base.connect() as db:db.execute("UPDATE sf_google_local_jobs SET result_expires_at=now()-interval '1 second' WHERE id=%s",(jid,))
        with self.assertRaises(ProductError) as e:self.jobs.job(self.owner['id'],jid,include_results=True)
        self.assertEqual(e.exception.status,410)
        process_pending(self.product.base)
        with self.product.base.connect() as db:self.assertIsNone(db.execute('SELECT result_cache FROM sf_google_local_jobs WHERE id=%s',(jid,)).fetchone()['result_cache'])
    def test_failure_and_restart_release_quota_once(self):
        jid=self.submit()
        with patch('tests.maps_job_fixture.fetch_search',side_effect=ProductError('maps_timeout','timeout')):process_pending(self.product.base)
        self.assertEqual(self.jobs.job(self.owner['id'],jid)['status'],'failed')
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'],0)
        self.assertEqual(self.product.usage(self.owner['id'])['used'],0)
        nextid=self.submit('two')
        with self.product.base.connect() as db:db.execute("UPDATE sf_google_local_jobs SET status='running',started_at=now()-interval '4 minutes' WHERE id=%s",(nextid,))
        process_pending(self.product.base);process_pending(self.product.base)
        self.assertEqual(self.jobs.job(self.owner['id'],nextid)['error_code'],'worker_interrupted')
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'],0)
    def test_worker_disabled_never_claims_and_idem_conflict(self):
        jid=self.submit()
        with patch.dict(os.environ,{'SEARCHFLOW_MAPS_WORKER':'false'}),patch('tests.maps_job_fixture.fetch_search') as call:process_pending(self.product.base);call.assert_not_called()
        self.assertEqual(self.jobs.job(self.owner['id'],jid)['status'],'queued')
        with self.assertRaises(ProductError):self.jobs.submit(self.owner,dict(query='dentist',city='Singapore'),'one')

    def test_site_receipt_and_export_auth_without_customer_key(self):
        from types import SimpleNamespace
        from realtime.google_local_http import handle
        from realtime.quick_request_http import receipt
        jid=self.submit()
        with patch('tests.maps_job_fixture.fetch_search',return_value=result()):process_pending(self.product.base)
        owner=self.owner
        class Handler:
            def __init__(self,path,headers):
                self.path=path;self.headers=headers;self.config=SimpleNamespace(database_url='unused');self.wfile=io.BytesIO();self.status=None;self.value=None
            def send_json(self,value,status=200):self.value=value;self.status=status
            def send_response(self,status):self.status=status
            def send_header(self,*args):pass
            def end_headers(self):pass
        original=self.product.authenticate
        self.product.authenticate=lambda key: original(key,rate_limit=False)
        with patch('realtime.google_local_http.ProductStore',return_value=self.product),patch('realtime.product_http.secure',return_value=True),patch('realtime.quick_request_http.secret',return_value=self.account['api_key']),patch.dict(os.environ,{'SEARCHFLOW_PRODUCT_ENABLED':'true'}):
            h=Handler('/api/maps/jobs/'+jid,{});handle(h,'GET');self.assertEqual(h.status,403)
            headers={'X-SearchFlow-Quick':'1','Host':'fixture','Origin':'https://fixture','X-Quick-Token':'wrong'}
            h=Handler('/api/maps/jobs/'+jid,headers);handle(h,'GET');self.assertEqual(h.status,404)
            headers['X-Quick-Token']=receipt(self.account['api_key'],'maps:'+jid)
            h=Handler('/api/maps/jobs/'+jid+'/export?format=jsonl',headers);handle(h,'GET');self.assertEqual(h.status,200)
            self.assertEqual(json.loads(h.wfile.getvalue())['result_cache']['places'],result()['places'])
            headers['Origin']='https://elsewhere'
            h=Handler('/api/maps/jobs/'+jid,headers);handle(h,'GET');self.assertEqual(h.status,403)

    def test_maps_and_web_share_concurrency(self):
        self.submit('map-one');self.submit('map-two')
        with self.assertRaises(ProductError) as e:self.product.submit(self.owner,{'query':'web'},'web',Config(database_url=self.dsn))
        self.assertEqual(e.exception.code,'concurrency_limit')

    def test_external_submit_retry_reports_actual_status_and_strict_body(self):
        from types import SimpleNamespace
        from realtime.google_local_http import handle
        owner=self.owner
        class Handler:
            def __init__(self,path,raw):
                self.path=path;self.config=SimpleNamespace(database_url='unused');self.rfile=io.BytesIO(raw)
                self.headers={'Authorization':'Bearer fixture','Content-Type':'application/json',
                              'Content-Length':str(len(raw)),'Idempotency-Key':'http-retry'}
            def send_json(self,value,status=200):self.value=value;self.status=status
        raw=b'{"query":"coffee","city":"Singapore"}'
        with patch('realtime.google_local_http.ProductStore',return_value=self.product),patch.object(self.product,'authenticate',return_value=owner),patch('realtime.product_http.secure',return_value=True),patch.dict(os.environ,{'SEARCHFLOW_PRODUCT_ENABLED':'true'}):
            h=Handler('/v1/google/local/jobs',raw);handle(h,'POST')
            self.assertEqual(h.status,202);jid=h.value['id']
            self.assertEqual(h.value['results_url'],'/v1/google/local/jobs/'+jid+'/results')
            with patch('tests.maps_job_fixture.fetch_search',return_value=result()):process_pending(self.product.base)
            h=Handler('/v1/google/local/jobs',raw);handle(h,'POST')
            self.assertTrue(h.value['reused']);self.assertEqual(h.value['status'],'succeeded')
            self.assertEqual(self.product.usage(owner['id'])['used'],1)
            h=Handler('/v1/google/local/jobs?ignored=1',raw);handle(h,'POST');self.assertEqual(h.status,400)
            h=Handler('/v1/google/local/jobs',b'{"query":"a","query":"b"}');handle(h,'POST');self.assertEqual(h.status,400)

    def test_global_queue_backpressure_preserves_quota_and_idempotency(self):
        self.submit('one');self.submit('two')
        second=self.product.create_account(dict(name='second',monthly_limit=10,concurrency=2))
        owner=self.product.authenticate(second['api_key'],rate_limit=False)
        for key in ('third','fourth'):self.jobs.submit(owner,dict(query='coffee',city='Singapore'),key)
        third=self.product.create_account(dict(name='third',monthly_limit=10,concurrency=2))
        account=self.product.authenticate(third['api_key'],rate_limit=False)
        with self.assertRaises(ProductError) as e:self.jobs.submit(account,dict(query='coffee',city='Singapore'),'fifth')
        self.assertEqual(e.exception.code,'maps_queue_full')
        self.assertEqual(self.product.usage(account['id'])['reserved'],0)
        self.assertEqual(self.submit('one'),self.submit('one'))

    def test_concurrent_accounts_cannot_overbook_the_last_queue_slot(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.submit('one');self.submit('two')
        owners=[]
        for name in ('race-a','race-b'):
            account=self.product.create_account(dict(name=name,monthly_limit=10,concurrency=2))
            owners.append(self.product.authenticate(account['api_key'],rate_limit=False))
        self.jobs.submit(owners[0],dict(query='coffee',city='Singapore'),'third')
        barrier=threading.Barrier(2)
        def submit(owner):
            barrier.wait(timeout=10)
            try:self.jobs.submit(owner,dict(query='coffee',city='Singapore'),'last-slot');return 'accepted'
            except ProductError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,owners))
        self.assertCountEqual(results,['accepted','maps_queue_full'])
        with self.product.base.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM sf_google_local_jobs WHERE status='queued'").fetchone()['n'],4)

    def test_middle_worker_persists_private_state_and_charges_only_after_readback(self):
        from unittest.mock import Mock
        from realtime.middle_maps_runtime import process_middle_pending
        jid = self.submit()
        phases = Mock()
        phases.advance.return_value = None
        process_middle_pending(self.product.base, phases)
        job = phases.advance.call_args.args[0]
        self.jobs.save_middle_state(job, dict(middle_query_id='lq_test', stage='ingest_pending'))
        self.assertNotIn('middle_state', self.jobs.job(self.owner['id'], jid))
        self.assertEqual(self.product.usage(self.owner['id'])['used'], 0)
        phases.advance.return_value = dict(middle_verified=True, middle_query_id='lq_test',
            payload=dict(places=[], provider=dict(name='gosom', version='1.18.2'), ordering='distance'))
        process_middle_pending(self.product.base, phases)
        self.assertEqual(phases.advance.call_args.args[1]['middle_query_id'], 'lq_test')
        stored = self.jobs.job(self.owner['id'], jid, include_results=True)
        self.assertTrue(stored['result_cache']['middle_verified'])
        self.assertEqual(self.product.usage(self.owner['id'])['used'], 1)
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'], 0)

    def test_middle_rate_limit_cooldown_survives_next_iteration(self):
        from unittest.mock import Mock
        from realtime.middle_maps_runtime import process_middle_pending
        jid = self.submit()
        phases = Mock()
        error = ProductError('middle_rate_limited', 'limited', 429)
        error.retry_after = 45
        phases.advance.side_effect = error
        process_middle_pending(self.product.base, phases)
        process_middle_pending(self.product.base, phases)
        self.assertEqual(phases.advance.call_count, 1)
        with self.product.base.connect() as db:
            state = db.execute('SELECT middle_state FROM sf_google_local_jobs WHERE id=%s', (jid,)).fetchone()['middle_state']
        self.assertIn('retry_not_before', state)
        self.assertEqual(self.product.usage(self.owner['id'])['reserved'], 1)
        self.assertEqual(self.product.usage(self.owner['id'])['used'], 0)
