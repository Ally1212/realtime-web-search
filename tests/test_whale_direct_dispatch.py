import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

from realtime.middle_live_runtime import LiveRunner


class IdleDispatchTests(unittest.TestCase):
    def test_whale_dispatch_is_polled_without_website_jobs(self):
        runner = object.__new__(LiveRunner)
        runner.source = 'google_news'
        runner.store, runner.collector = MagicMock(), Mock()
        runner.config = Mock()
        leader, db = MagicMock(), MagicMock()
        leader.__enter__.return_value = leader
        db.__enter__.return_value = db
        leader.execute.return_value.fetchone.return_value = {'acquired': True}
        db.execute.return_value.fetchall.return_value = []
        runner.store.base.connect.side_effect = [leader, db]
        direct = Mock()
        direct.poll.return_value = False
        with patch('realtime.middle_live_runtime.time.sleep', side_effect=KeyboardInterrupt), \
             patch('realtime.middle_direct.DirectTasks', return_value=direct, create=True):
            with self.assertRaises(KeyboardInterrupt):
                runner.run()
        direct.poll.assert_called_once_with(allow_claim=True)

from realtime.middle_direct import DirectTasks, maps_parameters
from realtime.product import ProductError


class DirectStateTests(unittest.TestCase):
    def test_collector_transports_preserve_documented_conflict_and_retry_after(self):
        from realtime.config import Config
        from realtime.middle_ingest import WhaleLinkClient
        from realtime.middle_maps_collector import MapsCollector

        session = Mock()
        clients = [
            WhaleLinkClient(Config(whale_collector_api_key='test'), session=session),
            MapsCollector('https://middle.test', 'test', session=session),
        ]
        for client in clients:
            for body, expected in [
                ({'error': 'live_results_pending'}, 'live_results_pending'),
                ({'error': {'code': 'task_lease_lost'}}, 'task_lease_lost'),
                ({'code': 'some_internal_detail'}, ''),
            ]:
                with self.subTest(client=type(client).__name__, expected=expected):
                    session.post.return_value = Mock(status_code=409, headers={'Retry-After': '12'},
                                                     json=Mock(return_value=body))
                    with self.assertRaises((RuntimeError, ProductError)) as raised:
                        client.post('/v1/collection/tasks/ctask_abc/complete', {})
                    self.assertEqual(raised.exception.status_code, 409)
                    self.assertEqual(raised.exception.whale_code, expected)
                    self.assertEqual(raised.exception.whale_retry_after, '12')

    def runner(self):
        r = object.__new__(DirectTasks)
        r.source = 'google_maps'
        r.platform = 'google_maps'
        r.collector = Mock(agent_id='agent')
        r.collector.post.return_value = {}
        r.save, r.stop, r.collect = Mock(), Mock(), Mock()
        return r

    def state(self):
        return dict(task=dict(id='ctask_abc', payload={'live_query_id':'lq_abc'}), messages=[dict(dataset_id='google_maps_raw', source=dict(source_platform='google_maps', source_name='test',source_record_key='event'),content={})])

    def receipt(self, status):
        return dict(dataset_id='google_maps_raw',source_platform='google_maps',source_name='test',source_record_key='event',status=status,document_id='doc',version_id='ver')

    def test_web_completion_reports_why_a_hundred_item_query_returned_less(self):
        runtime = self.runner()
        runtime.platform = 'google_search'
        state = self.state()
        state['web_partial'] = True
        state['messages'][0]['content']['platform_payload'] = {'record': {'collection_coverage': {
            'requested_max_items': 100, 'entity_unit': 'links', 'delivered_entities': 1,
            'stop_reason': 'partial_failure', 'interruption_code': 'google_captcha',
            'page_budget': 11, 'failed_pages': [3, 7, 11], 'missing_pages': [3, 7, 11],
        }}}
        with patch('realtime.middle_direct.processing_receipts', return_value=[self.receipt('accepted')]):
            runtime.advance(state)
        result = next(c.args[1]['result'] for c in runtime.collector.post.call_args_list
                      if c.args[0].endswith('/complete'))
        self.assertEqual(result['document_ids'], ['doc'])
        self.assertEqual(result['returned_count'], 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['collection_coverage']['requested_max_items'], 100)
        self.assertEqual(result['collection_coverage']['failed_pages'], [3, 7, 11])
        self.assertEqual(result['collection_coverage']['interruption_code'], 'google_captcha')

    def test_maps_direct_reports_the_provider_error_code(self):
        runtime = self.runner()
        runtime.base = MagicMock()
        state = self.state()
        state.pop('messages')
        db = runtime.base.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.return_value = {'state': state}
        with patch.object(runtime, 'advance', side_effect=ProductError('maps_invalid_response', 'provider detail', 502)):
            runtime.poll(allow_claim=False)
        report = next(c.args[1] for c in runtime.collector.post.call_args_list if c.args[0].endswith('/fail'))
        self.assertEqual(report['message'], 'maps_invalid_response')
        self.assertEqual(state['failure_code'], 'maps_invalid_response')

    def test_keyword_trends_completion_keeps_document_and_point_counts_distinct(self):
        runtime = self.runner()
        runtime.platform = 'google_trends'
        state = self.state()
        coverage = dict(requested_max_items=100, entity_unit='trend_series', delivered_entities=1,
                        point_count=93, stop_reason='trend_series_collected')
        state['messages'][0]['content']['platform_payload'] = {'record': {'collection_coverage': coverage}}
        with patch('realtime.middle_direct.processing_receipts', return_value=[self.receipt('accepted')]):
            runtime.advance(state)
        completion = next(c.args[1] for c in runtime.collector.post.call_args_list if c.args[0].endswith('/complete'))
        self.assertEqual(completion['collected_count'], 1)
        self.assertEqual(completion['result']['document_ids'], ['doc'])
        self.assertEqual(completion['result']['collection_coverage'], coverage)

    def test_invalid_trends_conditions_are_reported_without_contacting_google(self):
        from realtime.config import Config

        runtime = self.runner()
        runtime.source = runtime.platform = 'google_trends'
        runtime.config = Config(whale_supported_task_types=('live_query',),
                                whale_source_platform='google_trends', whale_dataset_id='social_media_raw')
        runtime.base = MagicMock()
        state = {'task': dict(id='ctask_abc', task_type='live_query', dataset_id='social_media_raw',
                              source_platform='google_trends', payload=dict(resource_kind='content',
                              live_query_id='lq_abc', query='OpenAI', conditions=[dict(name='unknown')]))}
        db = runtime.base.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.return_value = {'state': state}
        with patch.object(runtime, 'collect', wraps=DirectTasks.collect.__get__(runtime)), \
             patch('realtime.serp_worker.run_serp_campaign') as collect:
            runtime.poll(allow_claim=False)
        collect.assert_not_called()
        report = next(c.args[1] for c in runtime.collector.post.call_args_list if c.args[0].endswith('/fail'))
        self.assertEqual(report['message'], 'invalid_trends_conditions')
        self.assertEqual(state['failure_code'], 'invalid_trends_conditions')

    def test_queued_receipts_never_complete_and_restart_does_not_recollect(self):
        r=self.runner();state=self.state()
        r.collector.post.side_effect=[{},dict(receipts=[self.receipt('queued')])]
        r.advance(state)
        r.collect.assert_not_called()
        self.assertFalse(any(call.args[0].endswith('/complete') for call in r.collector.post.call_args_list))
        r.collector.post.side_effect=[{},dict(receipts=[self.receipt('accepted')]),{},{}]
        r.advance(state)
        r.collect.assert_not_called()
        self.assertEqual(state['stage'],'succeeded')
        self.assertEqual(r.save.call_args.args[1],'succeeded')
        self.assertEqual(r.collector.post.call_args.args[1]['result']['document_ids'],['doc'])

    def test_collection_is_saved_before_upload_and_uses_realtime(self):
        r=self.runner(); state=self.state(); messages=state.pop('messages')
        r.collect.return_value=messages
        r.collector.post.side_effect=[{},dict(receipts=[self.receipt('unknown')])]
        r.advance(state)
        r.collector.upload_realtime.assert_called_once_with(messages)
        self.assertEqual(r.save.call_args.args[0]['messages'],messages)

    def test_pending_completion_keeps_heartbeating_until_retry_after(self):
        r = self.runner()
        state = self.state()
        r.base = MagicMock()
        r.base.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'state': state}
        completed = []

        def post(path, payload):
            if path.endswith('/receipts'):
                return {'receipts': [self.receipt('accepted')]}
            if path.endswith('/complete'):
                completed.append(payload)
                if len(completed) == 1:
                    error = RuntimeError('Whale HTTP 409')
                    error.status_code = 409
                    error.whale_code = 'live_results_pending'
                    error.whale_retry_after = '30'
                    raise error
            return {}

        r.collector.post.side_effect = post
        started = datetime(2026, 11, 18, tzinfo=timezone.utc)
        with patch('realtime.middle_direct.datetime', wraps=datetime) as clock:
            clock.now.return_value = started
            r.poll(allow_claim=False)
            self.assertEqual(state['stage'], 'completion_pending')
            heartbeats = r.collector.post.call_count
            clock.now.return_value = started + timedelta(seconds=10)
            r.poll(allow_claim=False)
            self.assertEqual(r.collector.post.call_count, heartbeats + 1)
            self.assertTrue(r.collector.post.call_args.args[0].endswith('/heartbeat'))
            self.assertEqual(len(completed), 1)
            clock.now.return_value = started + timedelta(seconds=34)
            r.poll(allow_claim=False)

        self.assertEqual(completed[0], completed[1])
        self.assertEqual(r.save.call_args.args[1], 'succeeded')
        r.collect.assert_not_called()
        r.collector.upload_realtime.assert_not_called()

    def test_explicit_lease_loss_stops_completion(self):
        r = self.runner()
        state = self.state()
        r.base = MagicMock()
        r.base.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'state': state}
        error = RuntimeError('Whale HTTP 409')
        error.status_code = 409
        error.whale_code = 'task_lease_lost'
        r.collector.post.side_effect = [{}, {'receipts': [self.receipt('accepted')]}, {}, error]
        r.poll(allow_claim=False)
        self.assertEqual(r.save.call_args.args[1], 'lease_lost')

    def test_cancel_prevents_collection_and_upload(self):
        r=self.runner();state=self.state();state.pop('messages')
        r.collector.post.return_value={'control_signal':'cancel'}
        with self.assertRaises(ProductError):r.advance(state)
        r.collect.assert_not_called()
        r.collector.upload_realtime.assert_not_called()

    def test_maps_started_without_saved_response_is_not_repeated(self):
        r=self.runner();r.search=Mock()
        from realtime.middle_maps_contract import maps_request
        p=dict(mode='text',query='coffee',city='Singapore',region='SG',language='en',radius_m=1500)
        request=maps_request(p)
        task=dict(id='ctask_abc',task_type='live_query',dataset_id='google_maps_raw',source_platform='google_maps',payload={k:v for k,v in request.items() if k not in ('dataset_id','source_platforms','deadline_seconds')})
        task['payload']['live_query_id']='lq_abc'
        state=dict(task=task,collection_started=True)
        self.assertEqual(maps_parameters(task)['radius_m'],1500)
        with self.assertRaises(ValueError):DirectTasks.collect(r,state)
        r.search.assert_not_called()
        task['payload']['conditions'].append(dict(fact_type='other',name='ignored',op='eq',value='x'))
        with self.assertRaises((ProductError,KeyError,ValueError)):maps_parameters(task)

import os
from dataclasses import replace
from tests.search_database_fixture import SearchDatabaseFixture


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'isolated PostgreSQL required')
class DirectDatabaseTests(SearchDatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from realtime.product import ProductStore
        from realtime.google_local_jobs import SCHEMA
        ProductStore(self.dsn).initialize()
        with self.base.connect() as db:db.execute(SCHEMA)
        self.collector=Mock(agent_id='agent',base_url='https://middle.test')
        self.collector.post.return_value={}
        self.runtime=DirectTasks(self.base,self.collector,source='google_web',config=replace(self.config,whale_supported_task_types=('live_query',),whale_dataset_id='social_media_raw',whale_source_platform='google_search'))

    def task(self):
        return dict(id='ctask_direct',task_type='live_query',dataset_id='social_media_raw',source_platform='google_search',required_capabilities=['identity','title'],payload=dict(resource_kind='content',live_query_id='lq_direct',query='energy',region='US',language='en',max_items=2))

    def test_generic_trends_claim_completes_with_one_keyword_series(self):
        config = replace(self.runtime.config, whale_source_platform='google_trends')
        runtime = DirectTasks(self.base, self.collector, source='google_trends', config=config)
        task = dict(self.task(), source_platform='google_trends')
        task['payload']['max_items'] = 5
        runtime.accept(task)

        def record(cid, config, **kwargs):
            with self.base.connect() as db:
                options = db.execute('SELECT options FROM serp_task_options WHERE campaign_id=%s',
                                     (cid,)).fetchone()['options']
            self.assertEqual(options['trends_mode'], 'keyword')
            self.assertEqual(options['trends_time_range'], 'today 3-m')
            series = dict(point_count=1, points=[dict(timestamp='2026-11-20T00:00:00Z', value=42)])
            item = dict(link='https://trends.google.com/trends/explore?q=energy', title='Energy trend')
            self.console.record(cid, source='google_trends', provider='google_trends_explore',
                                query='energy', page=1, language='en', elapsed_seconds=.1,
                                classification='results', response=dict(keyword_trends=series,
                                trending_searches=[dict(keyword='energy', numeric_trend_series=series,
                                                        articles=[item])]))
            return ()

        def post(path, payload):
            if path.endswith('/receipts'):
                return {'receipts': [{**key, 'status':'accepted', 'document_id':'doc_trends',
                                      'version_id':'ver_trends'} for key in payload]}
            return {}

        self.collector.post.side_effect = post
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=record):
            self.assertTrue(runtime.poll(allow_claim=False))
        result = next(c.args[1]['result'] for c in self.collector.post.call_args_list
                      if c.args[0].endswith('/complete'))
        self.assertEqual(result['document_ids'], ['doc_trends'])
        self.assertEqual(result['collection_coverage']['entity_unit'], 'trend_series')
        self.assertEqual(result['collection_coverage']['point_count'], 1)
        with self.base.connect() as db:
            status = db.execute('SELECT status FROM sf_middle_direct_tasks').fetchone()['status']
        self.assertEqual(status, 'succeeded')

    def test_proxy_recovery_resumes_all_eleven_pages_under_same_direct_task(self):
        task = self.task()
        task['payload']['max_items'] = 11
        self.runtime.accept(task)

        def save_pages(cid, pages):
            for page in pages:
                self.console.record(cid, source='google_web', provider='openserp', query='energy',
                    page=page, language='en', elapsed_seconds=.1, classification='results', http_status=200,
                    response=dict(organic_results=[dict(link=f'https://example.org/{page}',
                                                        title=f'Page {page}', position=1)]))

        def fail_after_page_one(cid, config, **kwargs):
            self.assertEqual(config.google_web_max_pages, 11)
            save_pages(cid, [1])
            return ('energy: page 2: google web google_proxy_unavailable',)

        with patch('realtime.serp_worker.run_serp_campaign', side_effect=fail_after_page_one) as run:
            self.assertFalse(self.runtime.poll(allow_claim=False))
            self.assertFalse(self.runtime.poll(allow_claim=True))
            run.assert_called_once()
        with self.base.connect() as db:
            row = db.execute('SELECT state,status FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(row['status'], 'running')
        state = row['state']
        self.assertEqual(state['stage'], 'waiting_for_collection')
        self.assertEqual(state['collection_retry_code'], 'proxy_pool_unavailable')
        self.assertFalse(any(c.args[0].endswith('/fail') for c in self.collector.post.call_args_list))
        self.assertFalse(any(c.args[0].endswith('/claim') for c in self.collector.post.call_args_list))

        state['retry_at'] = '2000-01-01T00:00:00+00:00'
        self.runtime.save(state)
        restarted = DirectTasks(self.base, self.collector, source='google_web', config=self.runtime.config)
        def recover(cid, config, *, should_continue, skip_web_pages):
            self.assertEqual(cid, state['campaign_id'])
            self.assertEqual(skip_web_pages, {1})
            save_pages(cid, range(2, 12))
            return ()
        receipts = [dict(status='accepted', document_id=f'doc_{page}', version_id=f'ver_{page}')
                    for page in range(1, 12)]
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=recover) as run, \
             patch('realtime.middle_direct.processing_receipts', return_value=receipts):
            self.assertTrue(restarted.poll(allow_claim=False))
            run.assert_called_once()
        with self.base.connect() as db:
            final = db.execute('SELECT state,status FROM sf_middle_direct_tasks').fetchone()
            pages = db.execute('SELECT page FROM serp_requests WHERE campaign_id=%s ORDER BY page',
                               (state['campaign_id'],)).fetchall()
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual([r['page'] for r in pages], list(range(1, 12)))
        self.assertEqual(len(final['state']['messages']), 11)
        self.assertEqual(self.collector.post.call_args.args[0], '/v1/collection/tasks/ctask_direct/complete')

    def test_web_underfilled_round_recovers_gaps_then_refreshes_sparse_pages(self):
        task = self.task()
        task['payload']['max_items'] = 100
        self.runtime.accept(task)
        passes = []

        def run(cid, config, **kwargs):
            skipped = set(kwargs.get('skip_web_pages', ()))
            refreshed = set(kwargs.get('refresh_web_pages', ()))
            passes.append((skipped, refreshed))
            number = len(passes)
            for page in sorted(set(range(1, 12)) - skipped):
                if number == 1 and page in (3, 6):
                    self.console.record(cid, source='google_web', provider='openserp', query='energy',
                                        page=page, language='en', elapsed_seconds=.1,
                                        classification='captcha', http_status=429, error_code='google_captcha',
                                        response={})
                    continue
                links = [dict(link=f'https://example.org/{number}-{page}-{i}', title='link', position=i + 1)
                         for i in range(2 if number == 1 else 10)]
                self.console.record(cid, source='google_web', provider='openserp', query='energy',
                                    page=page, language='en', elapsed_seconds=.1, classification='results',
                                    response=dict(organic_results=links))
            return ('page 3: google web google_captcha',) if number == 1 else ()

        receipts = [dict(status='accepted', document_id=f'doc_{i}', version_id=f'ver_{i}') for i in range(100)]
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=run), \
             patch('realtime.middle_direct.processing_receipts', return_value=receipts):
            self.runtime.poll(allow_claim=False)
        self.assertEqual(len(passes), 3)
        self.assertEqual(set(range(1, 12)) - passes[1][0], {3, 6})
        self.assertEqual(passes[2][1], set(range(1, 12)) - {3, 6})
        with self.base.connect() as db:
            final = db.execute('SELECT state,status FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(len(final['state']['messages']), 100)
        self.assertFalse(final['state']['web_partial'])

    def test_expired_proxy_wait_reports_the_specific_reason_once(self):
        task = self.task()
        self.runtime.accept(task)
        self.runtime.save(dict(task=task, stage='waiting_for_collection', collection_deadline_at=1,
                               collection_retry_code='proxy_pool_unavailable'))
        with patch.object(self.runtime, 'collect') as collect:
            self.assertTrue(self.runtime.poll(allow_claim=False))
            self.assertFalse(self.runtime.poll(allow_claim=False))
            collect.assert_not_called()
        failures = [c.args[1] for c in self.collector.post.call_args_list if c.args[0].endswith('/fail')]
        self.assertEqual(failures, [{'agent_id': 'agent', 'message': 'proxy_pool_unavailable', 'retry': False}])
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM sf_middle_direct_tasks').fetchone()['status'], 'failed')

    def test_web_delivers_saved_links_when_upstream_blocks_and_pool_runs_out(self):
        from realtime.live_collection import WEB_DELIVERY_RESERVE_SECONDS
        self.runtime.deadline_lookup = Mock(return_value=datetime.now(timezone.utc) +
                                            timedelta(seconds=WEB_DELIVERY_RESERVE_SECONDS + 5))
        task = self.task()
        task['payload']['max_items'] = 20
        self.runtime.accept(task)
        def sample(cid, config, **kwargs):
            self.console.record(cid, source='google_web', provider='openserp', query='energy',
                page=1, language='en', elapsed_seconds=.1, classification='rate_limited',
                http_status=429, error_code='google_http_429', response={})
            self.record(cid, 'google_web', 'https://example.org/saved', 'Saved link')
            return ('energy: page 3: google web google_proxy_unavailable',)
        receipts = [dict(status='accepted', document_id='doc_saved', version_id='ver_saved')]
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=sample), \
             patch('realtime.middle_direct.processing_receipts', return_value=receipts):
            self.assertTrue(self.runtime.poll(allow_claim=False))
        completion = next(c.args[1] for c in self.collector.post.call_args_list
                          if c.args[0].endswith('/complete'))
        self.assertEqual(completion['result']['document_ids'], ['doc_saved'])
        self.assertEqual(completion['result']['returned_count'], 1)
        self.assertTrue(completion['result']['partial'])
        with self.base.connect() as db:
            final = db.execute('SELECT state,status FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(final['status'], 'succeeded')
        coverage = final['state']['messages'][0]['content']['platform_payload']['record']['collection_coverage']
        self.assertEqual(coverage['interruption_code'], 'proxy_pool_unavailable')

    def test_web_uses_delivery_reserve_to_send_saved_page_without_new_search(self):
        from realtime.live_collection import WEB_DELIVERY_RESERVE_SECONDS
        from uuid import NAMESPACE_URL, uuid5
        task = self.task()
        task['payload']['max_items'] = 20
        cid = str(uuid5(NAMESPACE_URL, 'whale-direct:'+task['id']))
        with self.base.connect() as db:
            db.execute("INSERT INTO campaigns(id,query,aliases,sources,daily_target,proxy_profile,status) "
                       "VALUES(%s,'energy','[]','[\"google_web\"]',10,'direct','active')",(cid,))
        self.record(cid, 'google_web', 'https://example.org/cutoff', 'Saved before cutoff')
        from realtime.product import now
        deadline = now()+timedelta(seconds=WEB_DELIVERY_RESERVE_SECONDS-5)
        self.runtime.deadline_lookup = Mock(return_value=deadline)
        self.runtime.accept(task)
        receipts = [dict(status='accepted', document_id='doc_saved', version_id='ver_saved')]
        with patch('realtime.serp_worker.run_serp_campaign') as run, \
             patch('realtime.middle_direct.processing_receipts', return_value=receipts):
            self.assertTrue(self.runtime.poll(allow_claim=False))
            run.assert_not_called()
        self.runtime.deadline_lookup.assert_called_once_with(task['payload']['live_query_id'], task['id'])
        with self.base.connect() as db:
            final = db.execute('SELECT state,status FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['state']['web_hard_deadline_at'], deadline.timestamp())
        self.assertTrue(final['state']['web_partial'])

    def test_news_resumes_after_proxy_cache_is_refreshed(self):
        import tempfile
        from pathlib import Path
        from realtime.proxy_pool import ProxyRecord

        directory = self.enterContext(tempfile.TemporaryDirectory())
        config = replace(self.runtime.config, default_proxy_profile='private',
                         proxy_cache_dir=Path(directory), whale_source_platform='google_news')
        runtime = DirectTasks(self.base, self.collector, source='google_news', config=config)
        task = self.task()
        task['source_platform'] = 'google_news'
        runtime.accept(task)
        with patch('requests.Session.get') as get:
            self.assertFalse(runtime.poll(allow_claim=False))
            get.assert_not_called()
        with self.base.connect() as db:
            state = db.execute('SELECT state FROM sf_middle_direct_tasks').fetchone()['state']
        self.assertEqual(state['collection_retry_code'], 'proxy_pool_unavailable')
        state['retry_at'] = '2000-01-01T00:00:00+00:00'
        runtime.save(state)
        runtime.proxy_pool.cache.publish('private', [
            ProxyRecord('192.0.2.1', 8080, 'http', last_checked=datetime.now(timezone.utc).isoformat()),
        ], None)
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [
            b'<rss><channel><item><title>Energy</title><link>https://example.org/recovery</link></item></channel></rss>',
        ]
        receipts = [dict(status='accepted', document_id='doc_news', version_id='ver_news')]
        with patch('requests.Session.get', return_value=response) as get, \
             patch('realtime.middle_direct.processing_receipts', return_value=receipts):
            self.assertTrue(runtime.poll(allow_claim=False))
            get.assert_called_once()
        with self.base.connect() as db:
            final = db.execute('SELECT status FROM sf_middle_direct_tasks').fetchone()['status']
            attempts = db.execute('SELECT error_code,http_status FROM serp_requests WHERE campaign_id=%s '
                                  'ORDER BY completed_at', (state['campaign_id'],)).fetchall()
        self.assertEqual(final, 'succeeded')
        self.assertEqual(attempts, [
            {'error_code': 'proxy_pool_unavailable', 'http_status': None},
            {'error_code': None, 'http_status': 200},
        ])

    def test_direct_claim_is_durable_and_does_not_create_website_job(self):
        task=self.task()
        task['payload']['max_items'] = 1
        self.assertTrue(self.runtime.accept(task))
        with self.base.connect() as db:
            state=db.execute('SELECT state FROM sf_middle_direct_tasks').fetchone()['state']
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_jobs').fetchone()['n'],0)
        with patch('realtime.serp_worker.run_serp_campaign') as run:
            def record(cid,config,*,should_continue):
                self.assertTrue(should_continue())
                self.record(cid,'google_web','https://example.org/direct','Direct result')
            run.side_effect=record
            messages=self.runtime.collect(state)
        self.assertEqual(len(messages),1)
        self.assertEqual(messages[0]['source']['collection_task_id'],'ctask_direct')
        self.assertIn('platform_payload',messages[0]['content'])
        self.assertTrue(self.runtime.accept(task))
        changed=self.task();changed['payload']['query']='other'
        with self.assertRaises(ValueError):self.runtime.accept(changed)

    def test_idle_claim_then_saved_receipt_recovery(self):
        self.runtime.collect=Mock(return_value=[])
        def post(path,payload):
            if path.endswith('/claim'):return {'tasks':[self.task()]}
            return {}
        self.collector.post.side_effect=post
        self.assertTrue(self.runtime.poll(allow_claim=True))
        with self.base.connect() as db:
            row=db.execute('SELECT status,state FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(row['status'],'succeeded')
        self.assertEqual(row['state']['receipts'],[])
        self.runtime.collect.assert_called_once()

    def test_image_dispatch_claims_on_shared_platform_and_collects_image_assets(self):
        from realtime.middle_collector import image_conditions

        task = self.task()
        task['required_capabilities'] += ['condition:other', 'assets']
        task['payload']['conditions'] = image_conditions()
        def post(path, payload):
            if path.endswith('/claim'):
                capable = set(task['required_capabilities']) <= set(payload['declared_capabilities'])
                return {'tasks': [task] if capable else []}
            if path.endswith('/receipts'):
                return {'receipts': [{**key, 'status': 'accepted', 'document_id': 'doc_image',
                                      'version_id': 'ver_image'} for key in payload]}
            return {}

        def record(cid, config, *, should_continue):
            assert should_continue()
            with self.base.connect() as db:
                assert db.execute('SELECT sources FROM campaigns WHERE id=%s', (cid,)).fetchone()['sources'] == ['google_images']
            image_url = 'https://example.org/butterfly.jpg'
            self.console.record(cid, source='google_images', provider='openserp', query='energy',
                                page=1, language='en', classification='results', elapsed_seconds=.1,
                                response={'image_results': [{'link': image_url, 'image_url': image_url,
                                                            'title': 'Butterfly', 'position': 1}]})

        self.collector.post.side_effect = post
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=record) as driver:
            self.assertTrue(self.runtime.poll(allow_claim=True))
        driver.assert_called_once()
        with self.base.connect() as db:
            row = db.execute('SELECT status,state FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(row['status'], 'succeeded')
        messages = row['state']['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['source']['collection_task_id'], task['id'])
        self.assertEqual(messages[0]['assets'][0]['media_type'], 'image')
        self.assertEqual(messages[0]['content']['platform_payload']['assets'], messages[0]['assets'])

    def test_transient_product_error_is_deferred_without_failing_task(self):
        self.runtime.accept(self.task())
        self.runtime.advance=Mock(side_effect=ProductError('middle_rate_limited','retry',429))
        self.runtime.poll(allow_claim=False)
        self.assertFalse(any(c.args[0].endswith('/fail') for c in self.collector.post.call_args_list))
        with self.base.connect() as db:
            row=db.execute('SELECT status,state FROM sf_middle_direct_tasks').fetchone()
        self.assertEqual(row['status'],'running')
        self.assertIn('retry_at',row['state'])
        self.runtime.advance.reset_mock()
        self.runtime.poll(allow_claim=False)
        self.runtime.advance.assert_not_called()

    def test_claim_for_website_keeps_original_owner_and_reserved_quota(self):
        from realtime.product import ProductStore
        from psycopg.types.json import Jsonb
        store=ProductStore(self.dsn)
        account=store.create_account(dict(name='website',monthly_limit=5))
        owner=store.authenticate(account['api_key'],rate_limit=False)
        jid,_=store.submit(owner,dict(query='energy',provider='middle'), 'website-owned', self.runtime.config)
        task=self.task()
        state=dict(middle_query_id='lq_direct',platform_task_ids=['ctask_direct'])
        with self.base.connect() as db:db.execute('UPDATE sf_jobs SET response=%s WHERE id=%s',(Jsonb(state),jid))
        self.assertFalse(self.runtime.accept(task))
        with self.base.connect() as db:
            row=db.execute('SELECT account_id,response,status FROM sf_jobs WHERE id=%s',(jid,)).fetchone()
            direct=db.execute('SELECT count(*) n FROM sf_middle_direct_tasks').fetchone()['n']
        self.assertEqual(str(row['account_id']),account['account_id'])
        self.assertEqual(row['response']['task']['id'],'ctask_direct')
        self.assertEqual(direct,0)
        self.assertEqual(store.usage(account['account_id'])['reserved'],1)

    def test_multiple_persisted_pages_deliver_unique_links_with_page_proof(self):
        task = self.task()
        task['payload']['max_items'] = 3
        self.runtime.accept(task)
        with self.base.connect() as db:
            state = db.execute('SELECT state FROM sf_middle_direct_tasks').fetchone()['state']
        def collect_pages(cid, config, *, should_continue):
            self.assertEqual(config.google_web_max_pages, 11)
            self.assertTrue(should_continue())
            for page, links in ((1, ['a', 'b']), (2, ['b', 'c'])):
                self.console.record(cid, source='google_web', provider='openserp', query='energy',
                    page=page, language='en', elapsed_seconds=.1, classification='results', http_status=200,
                    response=dict(organic_results=[dict(link='https://example.org/'+link,
                        title=link, position=rank) for rank, link in enumerate(links, 1)]))
        with patch('realtime.serp_worker.run_serp_campaign', side_effect=collect_pages):
            messages = self.runtime.collect(state)
        self.assertEqual(len(messages), 3)
        records = [m['content']['platform_payload']['record'] for m in messages]
        self.assertEqual([r['page'] for r in records], [1, 1, 2])
        self.assertTrue(all(r['collection_coverage']['pages_searched'] == [1, 2] for r in records))
        self.assertTrue(all(r['collection_coverage']['delivered_entities'] == 3 for r in records))
