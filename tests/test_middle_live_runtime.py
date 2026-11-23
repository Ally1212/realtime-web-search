import copy
from datetime import timedelta
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from realtime.google_data import digest
from realtime.google_domain import DomainError
from realtime.google_whale_readback import WhaleReadbackClient
from realtime.middle_live_runtime import LiveRunner, verified_record, bind_live_observation
from realtime.product import now, parameters, ProductError


def test_keyword_trends_counts_documents_and_points_separately():
    from realtime.middle_live_runtime import collection_coverage

    rows = [dict(page=1, classification='results', response={'keyword_trends': {'point_count': 93}})]
    coverage = collection_coverage(rows, 100, 1, 1)
    assert coverage['entity_unit'] == 'trend_series'
    assert coverage['delivered_entities'] == 1
    assert coverage['point_count'] == 93
    assert coverage['stop_reason'] == 'trend_series_collected'


def sample():
    record=dict(request_id='request-1',source='google_web',query='battery',occurrences=[{'occurrence_id':'one'}],topics=[])
    payload=dict(schema_version='searchflow-live-links-1',middle_query_id='lq_1',collection_task_id='task_1',record=record)
    message=dict(dataset_id='social_media_raw',source=dict(source_platform='google_search',source_name='live',source_record_key='event'),content=dict(platform_payload=payload,external_content_id='url:one'))
    state=dict(middle_query_id='lq_1',platform_task_ids=['task_1'],messages=[message],receipts=[dict(status='accepted',document_id='doc_1',version_id='ver_1')])
    doc=dict(id='doc_1',dataset_id='social_media_raw',source_platform='google_search',current_version_id='newer',external_content_id='url:one')
    ver=dict(document_id='doc_1',version_id='ver_1',version=dict(document_id='doc_1',version_id='ver_1'),platform_payload=payload)
    reader=WhaleReadbackClient('https://middle.test','test')
    reader._read_pair=Mock(return_value=(doc,ver))
    return state,reader,doc,ver


class MiddleLiveTests(unittest.TestCase):
    def test_multi_result_readback_batches_and_checks_exact_pinned_payloads(self):
        state, reader, doc, version = sample()
        second = copy.deepcopy(version)
        second.update(document_id='doc_2', version_id='ver_2',
                      version=dict(document_id='doc_2', version_id='ver_2'))
        second['platform_payload']['record']['occurrences'][0]['occurrence_id'] = 'two'
        doc2 = dict(doc, id='doc_2', external_content_id='url:two')
        second_msg = copy.deepcopy(state['messages'][0])
        second_msg['content'] = dict(external_content_id='url:two', platform_payload=second['platform_payload'])
        state['messages'].append(second_msg)
        state['receipts'].append(dict(status='accepted', document_id='doc_2', version_id='ver_2'))
        reader._request = Mock(side_effect=[
            {'documents': [{'document_id':'doc_2','document':doc2}, {'document_id':'doc_1','document':doc}]},
            {'versions': [second, version]},
        ])
        result = verified_record(reader, state, 'social_media_raw', batch=True)
        self.assertEqual([x['occurrence_id'] for x in result['occurrences']], ['one', 'two'])
        self.assertEqual([c.args[1] for c in reader._request.call_args_list],
                         ['/v1/documents/batch-get', '/v1/versions/batch-get'])
        reader._read_pair.assert_not_called()

    def test_intake_advances_while_previous_readback_is_blocked(self):
        runner = object.__new__(LiveRunner)
        runner.source = 'google_news'
        runner.config, runner.collector, runner.store, runner.middle = Mock(), Mock(), Mock(), Mock()
        connection = runner.store.base.connect = MagicMock()
        db = connection.return_value.__enter__.return_value
        db.execute.return_value.fetchone.return_value = {'acquired': True}
        old = dict(id='old', status='running', parameters=dict(source='google_news'),
                   deadline_at=now()+timedelta(minutes=5), response=dict(stage='readback_pending'))
        new = dict(id='new', status='queued', parameters=dict(source='google_news'),
                   deadline_at=now()+timedelta(minutes=5), response=None)
        db.execute.return_value.fetchall.side_effect = [[old], [old, new]]
        started, release = Event(), Event()
        def readback(*_):
            started.set()
            release.wait(2)
        runner.finish_readback = Mock(side_effect=readback)
        admitted_during_readback = []
        def admit(*_, **__):
            admitted_during_readback.append(started.wait(1) and not release.is_set())
        runner.advance = Mock(side_effect=admit)
        with patch('realtime.middle_direct.DirectTasks') as tasks, patch('realtime.middle_live_runtime.time.sleep') as sleep:
            tasks.return_value.poll.return_value = False
            calls = []
            def stop_after_two(_):
                if calls:
                    release.set()
                    raise StopIteration
                calls.append(True)
            sleep.side_effect = stop_after_two
            with self.assertRaises(StopIteration):
                runner.run()
        runner.advance.assert_called_once_with(new, admission_only=True, defer_readback=True)
        runner.finish_readback.assert_called_once_with(old, old['response'])
        self.assertEqual(admitted_during_readback, [True])

    def test_single_oversize_message_fails_before_writing_to_middle(self):
        runner = object.__new__(LiveRunner)
        runner.config, runner.collector = Mock(whale_ingest_batch_size=50), Mock()
        with self.assertRaises(ProductError) as failed:
            runner.upload({'messages':[{'content':'中文'*100_000}]})
        self.assertEqual(failed.exception.code,'middle_ingest_rejected')
        runner.collector.session.post.assert_not_called()

    def test_download_uses_pinned_payload_even_when_current_version_changes(self):
        state,reader,_,_=sample()
        record=verified_record(reader,state,'social_media_raw')
        self.assertTrue(record['readback_verified'])
        self.assertEqual(record['provenance'],'middle_consumer_pinned_versions')
        self.assertEqual(record['middle_versions'][0]['version_id'],'ver_1')
        self.assertEqual(record['middle_versions'][0]['verified_hashes'],['platform_payload_hash'])
        self.assertFalse(record['middle_versions'][0]['current_document_version_matches'])
        self.assertNotIn('provenance',state['messages'][0]['content']['platform_payload']['record'])

    def test_queued_mismatch_tamper_or_missing_payload_never_unlocks_download(self):
        for mutate in (lambda s,r,d,v:s['receipts'][0].update(status='queued'),
                       lambda s,r,d,v:v['platform_payload'].update(collection_task_id='other'),
                       lambda s,r,d,v:d.update(dataset_id='other'),
                       lambda s,r,d,v:d.update(external_content_id='url:other'),
                       lambda s,r,d,v:v.update(version_id='other'),
                       lambda s,r,d,v:v.pop('platform_payload')):
            state,reader,doc,version=sample()
            # Deep copy to prevent a tampered Consumer payload also changing expected upload.
            version=copy.deepcopy(version);reader._read_pair.return_value=(doc,version)
            mutate(state,reader,doc,version)
            with self.assertRaises((ValueError,KeyError,DomainError)):
                verified_record(reader,state,'social_media_raw')

    def test_realtime_upload_is_scoped_and_does_not_touch_global_switches(self):
        runner=object.__new__(LiveRunner);runner.config=Mock(whale_collector_api_key='private')
        runner.collector=Mock(base_url='https://middle.test')
        runner.collector.session.post.return_value=Mock(status_code=202,json=lambda:[{}])
        state,*_=sample();runner.upload(state)
        call=runner.collector.session.post.call_args
        self.assertEqual(call.kwargs['headers']['X-Ingest-Class'],'realtime')
        self.assertEqual(call.kwargs['json'],state['messages'])
        self.assertFalse(call.kwargs['allow_redirects'])

    def test_middle_provider_cannot_silently_drop_other_sources(self):
        self.assertEqual(parameters(dict(query='battery',provider='middle'))['provider'],'middle')
        for source in ('google_news', 'google_trends'):
            self.assertEqual(parameters(dict(query='battery', provider='middle', source=source, **({'trends_mode': 'regional_hotlist'} if source == 'google_trends' else {})))['source'], source)
        with self.assertRaises(ProductError):
            parameters(dict(query='battery', provider='middle', source='google_images'))

    def test_cancellation_and_deadline_cancel_upstream_before_any_collection(self):
        for status,deadline in [('cancelled',now()+timedelta(minutes=1)),('running',now()-timedelta(seconds=1))]:
            runner=object.__new__(LiveRunner)
            runner.store=Mock();runner.middle=Mock();runner.stop_campaign=Mock()
            runner.store.base.connect.return_value.__enter__=Mock(return_value=Mock())
            runner.store.base.connect.return_value.__exit__=Mock()
            job=dict(id='job',account_id='owner',status=status,deadline_at=deadline,response={'middle_query_id':'lq_1'})
            runner.advance(job)
            runner.middle.cancel.assert_called_once_with('lq_1')
            runner.stop_campaign.assert_called_once()

    def test_retry_never_sleeps_until_the_delivery_deadline(self):
        runner=object.__new__(LiveRunner)
        runner.store=Mock();runner.store.base.connect.return_value.__enter__=Mock(return_value=Mock())
        runner.store.base.connect.return_value.__exit__=Mock();runner.middle=Mock();runner.save=Mock()
        runner.heartbeat=Mock();runner.collector=Mock();runner.source='google_web';runner.config=Mock()
        runner.fail_collection=Mock()
        state={'middle_query_id':'lq_1','platform_task_ids':['task_1'],'task':{'id':'task_1'},'platform_status':'pending',
               'messages':[]}
        job=dict(id='job',account_id='owner',status='running',deadline_at=now()+timedelta(seconds=20),response=state)
        runner.middle.status=Mock(return_value={'platform_status':'pending'})
        runner.collect=Mock(side_effect=ProductError('google_query_inflight','busy',503))
        runner.advance(job)
        runner.fail_collection.assert_called_once_with(job,state,'google_query_inflight')

    def test_retry_stays_before_deadline_when_time_remains(self):
        runner=object.__new__(LiveRunner)
        runner.store=Mock();runner.store.base.connect.return_value.__enter__=Mock(return_value=Mock())
        runner.store.base.connect.return_value.__exit__=Mock();runner.middle=Mock();runner.save=Mock()
        runner.heartbeat=Mock();runner.collector=Mock();runner.source='google_web';runner.config=Mock()
        state={'middle_query_id':'lq_1','platform_task_ids':['task_1'],'task':{'id':'task_1'},'platform_status':'pending','messages':[]}
        job=dict(id='job',account_id='owner',status='running',deadline_at=now()+timedelta(minutes=5),response=state)
        runner.middle.status=Mock(return_value={'platform_status':'pending'})
        runner.collect=Mock(side_effect=ProductError('google_query_inflight','busy',503))
        runner.advance(job)
        retry_at=state['collection_retry_at']
        self.assertLess(retry_at,job['deadline_at'].timestamp())
        self.assertGreater(retry_at,time.time())

    def test_create_retry_keeps_tenant_and_original_idempotency(self):
        runner=object.__new__(LiveRunner);runner.collector=Mock();runner.middle=Mock();runner.store=Mock();runner.save=Mock()
        runner.middle.create.side_effect=ProductError('middle_transport_unknown','unknown',503)
        job=dict(id='job',account_id='owner',status='queued',deadline_at=now()+timedelta(minutes=1),response=None,
                 idempotency_key='same',parameters=dict(query='battery',source='google_web',region='US',language='en',provider='middle',depth=10))
        for _ in range(2):
            with self.assertRaises(ProductError):runner.advance(job)
        for call in runner.middle.create.call_args_list:
            self.assertEqual(call.args,('owner',dict(query='battery',source='google_web',region='US',language='en'),'same'))


class LiveObservationIdentityTests(unittest.TestCase):
    def test_same_url_new_query_has_distinct_document_and_retry_is_stable(self):
        source = dict(content=dict(external_content_id='url:same', canonical_url='https://example.org'), source={})
        first = bind_live_observation(source, query_id='lq_first', task_id='task_first', record={'query': 'a', 'occurrences': [], 'completed_at': '2026-11-13T00:00:00Z'})
        retry = bind_live_observation(source, query_id='lq_first', task_id='task_first', record={'query': 'a', 'occurrences': [], 'completed_at': '2026-11-13T00:00:00Z'})
        other = bind_live_observation(source, query_id='lq_other', task_id='task_other', record={'query': 'b', 'occurrences': [], 'completed_at': '2026-11-13T00:00:00Z'})
        self.assertEqual(first, retry)
        self.assertNotEqual(first['content']['external_content_id'], other['content']['external_content_id'])
        self.assertEqual(first['content']['canonical_url'], source['content']['canonical_url'])
        self.assertEqual(source['content']['external_content_id'], 'url:same')


class FeedReadbackScopeTests(unittest.TestCase):
    def test_feed_document_cannot_be_verified_as_web(self):
        state, reader, document, version = sample()
        document['source_platform'] = 'google_news'
        version['platform_payload']['record']['source'] = 'google_news'
        with self.assertRaises(DomainError):
            verified_record(reader, state, 'social_media_raw')
        record = verified_record(reader, state, 'social_media_raw', 'google_news')
        self.assertTrue(record['readback_verified'])
        self.assertEqual(record['source'], 'google_news')


class LiveServiceAvailabilityTests(unittest.TestCase):
    def test_idle_registration_heartbeat_and_single_leader(self):
        from unittest.mock import MagicMock
        for acquired in (True, False):
            with self.subTest(acquired=acquired):
                runner = object.__new__(LiveRunner)
                runner.source = 'google_news'
                runner.store, runner.collector, runner.advance = MagicMock(), Mock(), Mock()
                runner.config = Mock()
                leader, db = MagicMock(), MagicMock()
                leader.__enter__.return_value = leader
                db.__enter__.return_value = db
                leader.execute.return_value.fetchone.return_value = {'acquired': acquired}
                db.execute.return_value.fetchall.return_value = []
                runner.store.base.connect.side_effect = [leader, db]
                with patch('realtime.middle_live_runtime.time.sleep', side_effect=KeyboardInterrupt), patch('realtime.middle_direct.DirectTasks'):
                    with self.assertRaises(KeyboardInterrupt if acquired else RuntimeError):
                        runner.run()
                if acquired:
                    runner.collector.register.assert_called_once()
                    runner.collector.heartbeat.assert_called_once_with(0)
                else:
                    runner.collector.register.assert_not_called()
                    runner.collector.heartbeat.assert_not_called()
                runner.advance.assert_not_called()

class TrendsSelectedLinksTests(unittest.TestCase):
    def test_repeated_article_across_topics_has_one_delivered_observation(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        runner = object.__new__(LiveRunner)
        runner.source = 'google_trends'
        runner.config = SimpleNamespace()
        runner.store, runner.links, runner.save = MagicMock(), MagicMock(), Mock()
        runner.collector = SimpleNamespace(agent_id='collector')
        owned = runner.store.base.connect.return_value.__enter__.return_value
        owned.execute.return_value.fetchone.return_value = dict(campaign_id='campaign',status='running')
        article = dict(title='Shared article',link='https://example.org/shared')
        row=dict(request_id='00000000-0000-0000-0000-000000000001',source='google_trends',
                 query='current trends',page=1,provider='google_trends_rss',classification='results',completed_at=now(),
                 response=dict(trending_searches=[dict(keyword='Topic one',articles=[article]),
                                                  dict(keyword='Topic two',articles=[dict(article),dict(title='Other',link='https://example.org/other')])]))
        runner.links.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[row]
        state=dict(middle_query_id='lq_test',platform_task_ids=['task'],task=dict(payload=dict(max_items=10)))
        def message(row,item,config,task):
            return None,dict(content=dict(external_content_id=item['link']),source={},discovery={})
        with patch('realtime.middle_live_runtime.task_spec',return_value=dict(max_items=10,google_pages=1)),patch('realtime.middle_live_runtime.link_message',side_effect=message):
            runner.collect(dict(id='job',deadline_at=now()+timedelta(minutes=5)),state)
        self.assertEqual(len(state['messages']),2)
        record=state['messages'][0]['content']['platform_payload']['record']
        self.assertEqual(len(record['occurrences']),2)
        self.assertEqual(len(record['topics']),2)  # Keep actual topic metadata.
        self.assertEqual(len(row['response']['trending_searches'][1]['articles']),2)  # Raw response remains intact.


class ObservationPartTests(unittest.TestCase):
    def test_per_link_parts_merge_back_without_duplicating_the_query_record(self):
        from realtime.middle_live_runtime import observation_part, verified_record
        occurrences = [dict(occurrence_id=f'occ_{i}', url=dict(original_url=f'https://e.org/{i}'),
                            article_url=f'https://e.org/{i}') for i in range(3)]
        record = dict(query='q', completed_at='2026-11-23T00:00:00Z', page_observations=[{'big': 'x'}],
                      platform_metrics={'result_count': 3}, occurrences=occurrences)
        parts = [observation_part(record, f'https://e.org/{i}') for i in range(3)]
        self.assertEqual([len(p['occurrences']) for p in parts], [1, 1, 1])
        self.assertNotIn('page_observations', parts[0])
        self.assertIsNone(observation_part(record, 'https://e.org/missing'))
        envelopes = [{'schema_version': 'searchflow-live-links-1', 'middle_query_id': 'lq',
                      'collection_task_id': 'task', 'record': p} for p in parts]
        state = dict(middle_query_id='lq', platform_task_ids=['task'],
                     messages=[{'content': {'external_content_id': f'url:{i}', 'platform_payload': e}}
                               for i, e in enumerate(envelopes)],
                     receipts=[{'status': 'accepted', 'document_id': f'doc_{i}', 'version_id': f'v_{i}'}
                               for i in range(3)])
        reader = Mock()
        reader._read_pair.side_effect = [({'external_content_id': f'url:{i}'}, {'platform_payload': e})
                                         for i, e in enumerate(envelopes)]
        reader._proof.side_effect = [{'ok': True} for _ in parts]
        merged = verified_record(reader, state, 'social_media_raw')
        self.assertEqual([o['occurrence_id'] for o in merged['occurrences']], ['occ_0', 'occ_1', 'occ_2'])
        self.assertEqual(merged['platform_metrics']['result_count'], 3)
        self.assertTrue(merged['readback_verified'])
