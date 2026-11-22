import unittest
from realtime.middle_maps_contract import maps_request, verify_snapshot, snapshot_message
from realtime.product import ProductError
from realtime.google_data import digest


class MapsMiddleContractTests(unittest.TestCase):
    def parameters(self, **changes):
        return dict(mode='text', query='coffee', city='Singapore', language='en', region='SG', radius_m=5000, **changes)

    def test_text_location_contract(self):
        request = maps_request(self.parameters())
        self.assertNotIn('source_options', request)
        self.assertEqual(request['conditions'][0]['value'], dict(lat=1.29027, lng=103.851959, radius_m=5000))
        self.assertEqual(request['max_items'], 1)

    def test_nearby(self):
        p = self.parameters()
        p.update(mode='nearby', included_type='cafe')
        request = maps_request(p)
        self.assertEqual(request['query'], 'cafe')
        self.assertEqual(request['conditions'][1]['value'], 'cafe')

    def test_invalid_coordinates_and_radius(self):
        for changes in ({'location': {'lat': True, 'lng': 0}}, {'location': {'lat': float('nan'), 'lng': 0}}, {'radius_m': True}):
            p = self.parameters()
            p.update(changes)
            with self.assertRaises(ProductError):
                maps_request(p)

    def test_hundred_place_request_is_preserved(self):
        request=maps_request(self.parameters(max_places=100))
        self.assertEqual(request['max_items'],100)

    def test_hundred_place_claim_must_authorize_hundred_places(self):
        from realtime.middle_maps_contract import task_parameters
        params = self.parameters(max_places=100)
        request = maps_request(params)
        task = dict(id='ctask_test', task_type='live_query', dataset_id='google_maps_raw',
                    source_platform='google_maps', payload={
                        **{key: request[key] for key in ('query', 'language', 'region', 'conditions', 'max_items')},
                        'live_query_id': 'lq_test', 'resource_kind': 'content',
                    })
        binding = dict(query_id='lq_test', task_id='ctask_test', expected_parameters=params)
        self.assertEqual(task_parameters(task, **binding)['max_places'], 100)
        task['payload']['max_items'] = 21
        with self.assertRaises(ProductError):
            task_parameters(task, **binding)

    def test_v2_snapshot_preserves_hundred_place_coverage(self):
        from realtime.middle_maps_contract import query_snapshot
        from realtime.maps_provider import normalize, VERSION
        import hashlib,json
        from realtime.maps_provider import prepare
        p=prepare(self.parameters(max_places=100))
        raw=[]
        for i in range(100):
            raw.append(dict(title=f'Cafe {i}',data_id=f'id:{i}',latitude=1.29027+i/1_000_000,
                            longtitude=103.851959,categories=['Cafe']))
        places=normalize(raw,p)
        coverage=dict(requested_count=100,returned_count=100,raw_occurrence_count=100,
            unique_entity_count=100,duplicate_occurrence_count=0,provider_depth=13,
            provider_mode='browser',truncated=False,stop_reason='requested_limit_reached',
            coverage_complete=False,coverage_status='not_independently_verified')
        result=dict(places=places,raw_response=raw,provider='gosom',provider_version=VERSION,
            ordering='distance',coverage=coverage,response_hash=hashlib.sha256(
                json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()).hexdigest())
        payload=query_snapshot(job_id='00000000-0000-0000-0000-000000000001',query_id='lq_abc',
            task_id='ctask_abc',parameters=p,result=result,observed_at='2026-11-20T00:00:00Z')
        self.assertEqual(payload['schema_version'],'searchflow-maps-query-2')
        self.assertEqual(payload['place_count'],100)
        self.assertEqual(payload['collection_coverage'],coverage)
        result['coverage']=dict(coverage,returned_count=99)
        with self.assertRaises(ProductError):query_snapshot(job_id='00000000-0000-0000-0000-000000000001',
            query_id='lq_abc',task_id='ctask_abc',parameters=p,result=result,observed_at='2026-11-20T00:00:00Z')

    def test_pinned_snapshot_including_zero_results(self):
        payload = dict(place_count=0, places=[])
        record = dict(version_id='v1', payload_hash=digest(payload), platform_payload=payload)
        self.assertEqual(verify_snapshot(record, version_id='v1', expected_payload=payload, expected_hash=digest(payload)), payload)
        for field in ('version_id', 'payload_hash', 'platform_payload'):
            bad = dict(record)
            bad.pop(field)
            with self.assertRaises(ProductError):
                verify_snapshot(bad, version_id='v1', expected_payload=payload, expected_hash=digest(payload))

    def test_snapshot_event_is_stable_and_body_is_empty(self):
        job = '00000000-0000-0000-0000-000000000001'
        payload = dict(schema_version='searchflow-maps-query-1', job_id=job,
                       collection_task_id='ctask_abc', places=[], place_count=0, observed_at='2026-11-13T00:00:00Z')
        message = snapshot_message(job_id=job, task_id='ctask_abc', payload=payload)
        self.assertEqual(message, snapshot_message(job_id=job, task_id='ctask_abc', payload=payload))
        self.assertNotIn('body_text', message['content'])
        self.assertEqual(message['source']['payload_hash'], 'sha256:' + digest(payload))
        payload['place_count'] = 1
        with self.assertRaises(ProductError):
            snapshot_message(job_id=job, task_id='ctask_abc', payload=payload)


class MapsClientTests(unittest.TestCase):
    def test_website_accepts_hundred_and_cancels_a_new_clamped_request(self):
        from unittest.mock import Mock
        from realtime.google_local_jobs import parameters
        from realtime.middle_platform_client import MapsMiddlePlatformClient

        params = dict(query='coffee', city='Singapore', region='SG', max_places=100)
        expected = maps_request(parameters(params))
        run = dict(id='run_test', live_query_id='lq_test', source_platform='google_maps',
                   collection_task_id='ctask_test', status='pending')
        client = MapsMiddlePlatformClient('https://middle.test', 'secret')
        client._request = Mock(return_value=dict(live_query=dict(expected, id='lq_test', status='pending'), runs=[run]))
        self.assertEqual(client.create('tenant', params, 'one')['platform_max_items'], 100)
        self.assertEqual(client.status_for_request('lq_test', params)['platform_max_items'], 100)

        client._request.return_value['live_query']['max_items'] = 21
        client.cancel = Mock()
        with self.assertRaises(ProductError) as caught:
            client.create('tenant', params, 'two')
        self.assertEqual(caught.exception.code, 'middle_maps_limit_mismatch')
        client.cancel.assert_called_once_with('lq_test')

    def test_create_validates_returned_conditions_and_does_not_unlock_download(self):
        from unittest.mock import Mock
        from realtime.middle_platform_client import MapsMiddlePlatformClient
        parameters = dict(mode='nearby', included_type='cafe', city='Singapore', region='SG', language='en')
        from realtime.google_local_jobs import parameters as validate
        request = maps_request(validate(parameters))
        data = dict(live_query=dict(request, id='lq_test', status='pending'), runs=[dict(
            id='run_test', live_query_id='lq_test', source_platform='google_maps',
            collection_task_id='ctask_test', status='pending')])
        session = Mock()
        session.request.return_value = Mock(status_code=202)
        session.request.return_value.json.return_value = data
        client = MapsMiddlePlatformClient('https://middle.test', 'secret', session=session)
        result = client.create('tenant', parameters, 'retry')
        self.assertFalse(result['download_ready'])
        self.assertEqual(session.request.call_args.kwargs['json'], request)
        data['live_query']['conditions'] = []
        with self.assertRaises(ProductError):
            client.create('tenant', parameters, 'retry')

    def test_snapshot_requires_exact_version_scope_and_computed_hash(self):
        from unittest.mock import Mock
        from copy import deepcopy
        from realtime.middle_platform_client import MapsMiddlePlatformClient
        payload = dict(schema_version='searchflow-maps-query-1', places=[], place_count=0)
        row = dict(document_id='doc_test', version_id='ver_test', dataset_id='google_maps_raw',
                   source_platform='google_maps', payload_hash=digest(payload), platform_payload=payload)
        session = Mock()
        session.request.return_value = Mock(status_code=200)
        session.request.return_value.json.return_value = dict(snapshots=[row])
        client = MapsMiddlePlatformClient('https://middle.test', 'secret', session=session)
        self.assertEqual(client.read_snapshot('doc_test', 'ver_test', expected_payload=payload,
                                             expected_hash=digest(payload)), payload)
        for changes in ({'version_id': 'ver_other'}, {'dataset_id': 'public'},
                        {'document_id': 'doc_other'}, {'source_platform': 'google_search'},
                        {'platform_payload': {'places': ['tampered']}}):
            bad = deepcopy(row)
            bad.update(changes)
            session.request.return_value.json.return_value = dict(snapshots=[bad])
            with self.assertRaises(ProductError):
                client.read_snapshot('doc_test', 'ver_test', expected_payload=payload, expected_hash=digest(payload))
        session.request.return_value.json.return_value = dict(snapshots=[row, row])
        with self.assertRaises(ProductError):
            client.read_snapshot('doc_test', 'ver_test', expected_payload=payload, expected_hash=digest(payload))


class MapsClaimTests(unittest.TestCase):
    def test_claim_preserves_conditions_and_rejects_other_jobs(self):
        from copy import deepcopy
        from realtime.middle_maps_contract import task_parameters
        from realtime.google_local_jobs import parameters
        p = dict(mode='nearby', included_type='cafe', city='London', region='GB', language='zh', radius_m=1000)
        request = maps_request(parameters(p))
        task = dict(id='ctask_abc', task_type='live_query', dataset_id='google_maps_raw',
                    source_platform='google_maps', required_capabilities=['identity','condition:location'],
                    payload=dict(request, live_query_id='lq_abc'))
        result = task_parameters(task, query_id='lq_abc', task_id='ctask_abc', expected_parameters=p)
        self.assertEqual(result['location'], dict(lat=51.5074, lng=-0.1278))
        self.assertEqual(result['included_type'], 'cafe')
        self.assertNotIn('max_places', result)
        explicit = task_parameters(task, query_id='lq_abc', task_id='ctask_abc',
                                   expected_parameters=dict(p, max_places=1))
        self.assertEqual(explicit['max_places'], 1)
        self.assertEqual(result['radius_m'], 1000)
        for field, value in [('id','ctask_other'), ('dataset_id','social_media_raw'), ('source_platform','google_search')]:
            bad = deepcopy(task)
            bad[field] = value
            with self.assertRaises(ProductError):
                task_parameters(bad, query_id='lq_abc', task_id='ctask_abc', expected_parameters=p)
        bad = deepcopy(task)
        bad['payload']['conditions'][0]['value']['radius_m'] = 5000
        with self.assertRaises(ProductError):
            task_parameters(bad, query_id='lq_abc', task_id='ctask_abc', expected_parameters=p)


class MapsSnapshotTests(unittest.TestCase):
    def test_zero_result_preserves_raw_and_query(self):
        from realtime.middle_maps_contract import query_snapshot
        p = dict(mode='text', query='coffee', city='Singapore', region='SG', language='en')
        result = dict(raw_response=[None], places=[], provider='gosom', provider_version='1.18.2',
                      ordering='distance', response_hash=__import__('hashlib').sha256(b'[null]').hexdigest())
        args = dict(job_id='00000000-0000-0000-0000-000000000001', query_id='lq_abc',
                    task_id='ctask_abc', parameters=p, result=result, observed_at='2026-11-13T00:00:00Z')
        payload = query_snapshot(**args)
        self.assertEqual(payload['raw_response'], [None])
        self.assertEqual(payload['place_count'], 0)
        self.assertEqual(payload['counts'], dict(document_count=1, business_entity_count=0, business_entity_unit='places'))
        self.assertTrue(payload['spatial_scope']['distance_filter_verified'])
        self.assertFalse(payload['spatial_scope']['category_relevance_verified'])
        self.assertEqual(payload['spatial_scope']['requested_radius_m'], payload['query_conditions']['radius_m'])
        self.assertEqual(payload['query_conditions']['location']['lat'], 1.29027)
        result['places'] = [{'name': 'fabricated'}]
        with self.assertRaises(ProductError):
            query_snapshot(**args)
