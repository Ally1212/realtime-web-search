import copy
from dataclasses import replace
from uuid import uuid4

import pytest
from realtime.config import Config
from realtime.middle_platform_client import search_request
from realtime.middle_ingest import task_spec, link_message
from realtime.middle_live_runtime import selected_observation
from realtime.customer_data import project


def config():
    return replace(Config(), whale_dataset_id='social_media_raw', whale_source_platform='google_search',
                   whale_supported_task_types=('live_query',))


def row():
    images = [dict(type='image', link=f'https://example.org/image.jpg?srsltid={i}',
                   image_url=f'https://example.org/image.jpg?srsltid={i}',
                   source_url='https://example.org/article', thumbnail='https://example.org/thumb.jpg',
                   width=640, height=480, title='battery', observed_order=i+1,
                   media_downloaded=False) for i in range(3)]
    return dict(request_id=str(uuid4()), campaign_id=str(uuid4()), source='google_images',
                query='battery', page=1, provider='openserp', classification='results',
                completed_at='2026-11-13T00:00:00Z', response=dict(image_results=images, organic_results=[],
                search_parameters=dict(q='battery', gl='us', hl='en')))


def test_image_request_and_task_keep_distinct_platform_and_bounded_depth():
    request = search_request(dict(source='google_images',query='battery',region='US',language='en'))
    assert request['dataset_id'] == 'social_media_raw'
    assert request['source_platforms'] == ['google_search']
    assert request['max_items'] == 20
    payload = dict(query='battery',region='US',language='en',max_items=20,
                   live_query_id='lq_abc',resource_kind='content',
                   conditions=[dict(fact_type='other',name='searchflow_vertical',op='eq',value='images')])
    task = dict(dataset_id='social_media_raw',source_platform='google_search',task_type='live_query',payload=payload)
    spec = task_spec(task, config(), source='google_images')
    assert spec['sources'] == ['google_images']
    assert spec['max_items'] == 20
    assert spec['google_pages'] == 1
    task['payload']['max_items'] = 21
    from realtime.product import ProductError
    with pytest.raises(ProductError) as error:
        task_spec(task, config(), source='google_images')
    assert error.value.code == 'image_max_items_exceeded'


def test_images_preserve_exact_urls_and_payload_has_only_selected_occurrences():
    original = row()
    saved = copy.deepcopy(original)
    selected, raw = selected_observation(original, 'google_images', 2)
    assert original == saved
    assert len(selected) == 2
    assert [i['image_url'] for i in selected] == [i['image_url'] for i in saved['response']['image_results'][:2]]
    record = project(raw)
    assert len(record['occurrences']) == 2
    keys = []
    for item in selected:
        key, message = link_message(original,item,config(),'task_images')
        keys.append(key)
        assert message['source']['source_platform'] == 'google_search'
        assert message['content']['canonical_url'] == item['image_url']
        assert message['discovery']['metadata']['result']['source_url'] == 'https://example.org/article'
    assert len(set(keys)) == 2
    assert record['occurrences'][0]['source_fields']['width'] == 640


def test_image_fixed_version_preserves_fields_and_rejects_changed_payload():
    from unittest.mock import Mock
    from realtime.middle_live_runtime import bind_live_observation, verified_record
    from realtime.google_whale_readback import WhaleReadbackClient
    from realtime.google_domain import DomainError
    original = row()
    selected, raw = selected_observation(original, 'google_images', 1)
    record = project(raw)
    _, message = link_message(original, selected[0], config(), 'task_images')
    message = bind_live_observation(message, query_id='lq_images', task_id='task_images', record=record)
    state = dict(middle_query_id='lq_images', platform_task_ids=['task_images'], messages=[message],
                 receipts=[dict(status='accepted',document_id='doc_images',version_id='ver_images')])
    document = dict(id='doc_images', dataset_id='social_media_raw', source_platform='google_search',
                    current_version_id='ver_images', external_content_id=message['content']['external_content_id'])
    version = dict(document_id='doc_images', version_id='ver_images',
                   version=dict(document_id='doc_images',version_id='ver_images'),
                   platform_payload=copy.deepcopy(message['content']['platform_payload']))
    reader = WhaleReadbackClient('https://middle.test','test')
    reader._read_pair = Mock(return_value=(document,version))
    verified = verified_record(reader,state,'social_media_raw','google_search')
    assert verified['occurrences'] == record['occurrences']
    assert verified['readback_verified'] is True
    assert version['platform_payload']['assets'] == message['assets']
    from realtime.customer_export import export_record
    import csv, io, json
    handler = Mock()
    export_record(handler,verified,'jsonl')
    assert json.loads(handler.send_body.call_args.args[0])['occurrences'] == record['occurrences']
    export_record(handler,verified,'csv')
    rows = list(csv.DictReader(io.StringIO(handler.send_body.call_args.args[0].decode('utf-8-sig'))))
    assert [json.loads(r['record_json']) for r in rows if r['record_type'] == 'result'] == record['occurrences']
    version['platform_payload']['assets'][0]['width'] = 999
    with pytest.raises(DomainError):
        verified_record(reader,state,'social_media_raw','google_search')
    version['platform_payload']['assets'] = copy.deepcopy(message['assets'])
    version['platform_payload']['record']['occurrences'][0]['image_url'] = 'https://example.org/tampered.jpg'
    with pytest.raises(DomainError):
        verified_record(reader,state,'social_media_raw','google_search')


def test_images_use_existing_assets_and_declare_capability():
    original = row()
    item = original['response']['image_results'][0]
    _, message = link_message(original, item, config())
    assert message['assets'] == [dict(media_type='image', role='media', url=item['image_url'],
                                     preview_url=item['thumbnail'], width=640, height=480)]
    assert 'assets' in message['provided_capabilities']
    assert 'cover' not in message and 'cover_url' not in message


def test_missing_image_metadata_is_not_invented():
    original = row()
    item = dict(link='https://example.org/photo', image_url='https://example.org/photo',
                width=True, height=-1, thumbnail='javascript:alert(1)')
    _, message = link_message(original, item, config())
    assert message['assets'] == [dict(media_type='image', role='media', url='https://example.org/photo')]


def test_image_collector_can_accept_asset_task_but_web_cannot():
    task = dict(dataset_id='social_media_raw',source_platform='google_search',task_type='live_query',
                required_capabilities=['identity','assets'], payload=dict(query='battery',region='US',language='en',
                max_items=20,live_query_id='lq_abc',resource_kind='content',
                   conditions=[dict(fact_type='other',name='searchflow_vertical',op='eq',value='images')]))
    assert task_spec(task,config(), source='google_images')['sources'] == ['google_images']
    task['source_platform'] = 'google_search'
    with pytest.raises(ValueError):
        task_spec(task,replace(config(),whale_source_platform='google_search'))


def test_images_reuse_google_platform_with_explicit_vertical_condition():
    request = search_request(dict(source='google_images', query='battery'))
    assert request['source_platforms'] == ['google_search']
    assert request['conditions'] == [dict(fact_type='other', name='searchflow_vertical', op='eq', value='images')]


def test_image_task_cannot_lose_vertical_or_run_as_web():
    from realtime.middle_collector import live_payload
    payload = dict(query='battery', max_items=20,live_query_id='lq_abc',resource_kind='content',
                   conditions=[dict(fact_type='other',name='searchflow_vertical',op='eq',value='images')])
    assert live_payload(payload,source='google_images')['sources'] == ['google_images']
    with pytest.raises(ValueError):
        live_payload(payload,source='google_web')
    payload.pop('conditions')
    with pytest.raises(ValueError):
        live_payload(payload,source='google_images')


def test_middle_response_must_echo_image_condition():
    from unittest.mock import Mock
    from realtime.middle_platform_client import SearchMiddlePlatformClient
    from realtime.product import ProductError
    request = search_request(dict(source='google_images',query='battery'))
    data = dict(live_query={**request,'id':'lq_images','status':'pending'},
                runs=[dict(id='run_images',live_query_id='lq_images',source_platform='google_search',
                           collection_task_id='task_images',status='pending')])
    client = SearchMiddlePlatformClient('https://middle.test','test',source='google_images',session=Mock())
    assert client._live(data,expected_request=request)['middle_query_id'] == 'lq_images'
    data['live_query'].pop('conditions')
    with pytest.raises(ProductError):
        client._live(data,expected_request=request)


def test_image_registration_advertises_google_search_and_condition_support():
    from unittest.mock import Mock
    from realtime.middle_ingest import WhaleLinkClient
    c=WhaleLinkClient(replace(config(),whale_collector_api_key='test'),source='google_images')
    c.post=Mock(return_value={'agent_id':'agent_images'})
    c.register()
    payload=c.post.call_args.args[1]
    assert payload['supported_platforms'] == ['google_search']
    assert payload['declared_capabilities'] == ['identity','title','assets','condition:other']


def test_shared_google_claim_routes_by_owned_task_not_iteration_order():
    from realtime.middle_live_runtime import owned_claim_job
    task=dict(id='task_images',dataset_id='social_media_raw',source_platform='google_search',task_type='live_query',
              required_capabilities=['identity','title','condition:other'],payload=dict(query='battery',region='US',language='en',
              max_items=20,live_query_id='lq_images',resource_kind='content',conditions=[dict(fact_type='other',name='searchflow_vertical',op='eq',value='images')]))
    web=dict(id='web',parameters=dict(source='google_web',query='battery',region='US',language='en'),response=dict(middle_query_id='lq_web',platform_task_ids=['task_web']))
    images=dict(id='images',parameters=dict(source='google_images',query='battery',region='US',language='en'),response=dict(middle_query_id='lq_images',platform_task_ids=['task_images']))
    assert owned_claim_job(task,[web,images],config()) is images
    assert owned_claim_job(task,[web],config()) is None
    wrong=copy.deepcopy(task);wrong['payload']['conditions']=[]
    with pytest.raises(ValueError):owned_claim_job(wrong,[web,images],config())


def test_image_customer_route_can_be_enabled_after_runner_rollout(monkeypatch):
    from realtime.product import parameters, middle_search_payload, capability_payload
    monkeypatch.setenv('SEARCHFLOW_MIDDLE_IMAGES_ENABLED','true')
    p=parameters(middle_search_payload(dict(query='battery',source='google_images')))
    assert p['source']=='google_images' and p['provider']=='middle' and p['depth']==20
    source=next(s for s in capability_payload()['sources'] if s['id']=='google_images')
    assert source['new_requests_enabled'] is True


def test_claimed_image_is_persisted_for_image_job_when_web_iteration_claims_it():
    from unittest.mock import Mock, MagicMock
    from datetime import timedelta
    from realtime.middle_live_runtime import LiveRunner
    from realtime.product import now
    p=dict(source='google_images',query='battery',region='US',language='en')
    task=dict(id='task_images',dataset_id='social_media_raw',source_platform='google_search',task_type='live_query',
              payload=dict(query='battery',region='US',language='en',max_items=20,live_query_id='lq_images',resource_kind='content',
              conditions=[dict(fact_type='other',name='searchflow_vertical',op='eq',value='images')]))
    target=dict(id='images',parameters=p,response=dict(middle_query_id='lq_images',platform_task_ids=['task_images']))
    runner=object.__new__(LiveRunner);runner.source='google_web';runner.config=config()
    runner.store=MagicMock();runner.save=Mock();runner.middle=Mock();runner.collector=Mock(source='google_images')
    runner.middle.status.return_value=dict(platform_status='pending')
    runner.collector.post.return_value={'tasks':[task]}
    runner.store.base.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[target]
    runner.advance(dict(id='web',status='running',deadline_at=now()+timedelta(minutes=5),
                        response=dict(middle_query_id='lq_web',platform_task_ids=['task_web'])))
    assert runner.save.call_args.args[0]['id']=='images'
    assert runner.save.call_args.args[1]['task']==task
    runner.collector.fail.assert_not_called()
    runner.save.reset_mock()
    runner.direct=Mock()
    runner.store.base.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[]
    runner.advance(dict(id='web',status='running',deadline_at=now()+timedelta(minutes=5),
                        response=dict(middle_query_id='lq_web',platform_task_ids=['task_web'])))
    runner.direct.accept.assert_called_once_with(task)
    runner.collector.fail.assert_not_called()
    runner.save.assert_not_called()


def test_web_and_images_share_one_leader_and_poll_both_modes():
    from unittest.mock import Mock, MagicMock, patch
    from realtime.middle_live_runtime import LiveRunner
    from realtime.middle_platform_client import SearchMiddlePlatformClient
    for source in ('google_web','google_images'):
        runner=object.__new__(LiveRunner);runner.source=source;runner.config=config()
        runner.middle=SearchMiddlePlatformClient('https://middle.test','test',source=source)
        runner.store=MagicMock();runner.collector=Mock(source='google_images')
        leader,db=MagicMock(),MagicMock()
        leader.__enter__.return_value=leader;db.__enter__.return_value=db
        leader.execute.return_value.fetchone.return_value={'acquired':True}
        db.execute.return_value.fetchall.return_value=[]
        runner.store.base.connect.side_effect=[leader,db]
        with patch('realtime.middle_direct.DirectTasks') as direct, \
             patch('realtime.middle_live_runtime.time.sleep',side_effect=KeyboardInterrupt),pytest.raises(KeyboardInterrupt):
            direct.return_value.poll.return_value = False
            runner.run()
        assert leader.execute.call_args.args[1]==(11,)
        assert db.execute.call_args.args[1]==(['google_web','google_images'],)
        assert direct.call_args.kwargs['source'] == 'google_web'
