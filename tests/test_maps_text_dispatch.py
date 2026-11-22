"""Whale's generic text request must not require a caller-supplied map center."""
import unittest
from realtime.middle_direct import maps_parameters


def task(**changes):
    payload=dict(resource_kind='content',query='coffee in Seattle',region='',language='',max_items=2,live_query_id='lq_seattle',url='',external_id='')
    payload.update(changes)
    return dict(id='ctask_seattle',task_type='live_query',dataset_id='google_maps_raw',source_platform='google_maps',required_capabilities=['identity','title'],payload=payload)


class MapsTextDispatchTests(unittest.TestCase):
    def test_original_whale_text_request_is_accepted(self):
        p=maps_parameters(task())
        self.assertEqual(p['query'],'coffee in Seattle')
        self.assertIsNone(p['location'])
        self.assertEqual(p['language'],'en')
        self.assertEqual(p['max_places'],2)

    def test_text_query_preserves_city_text_and_uses_documented_default_radius(self):
        p=maps_parameters(task(query='dentists in Kyoto',region='JP',language='en',max_items=5))
        self.assertIsNone(p['location'])
        self.assertEqual(p['radius_m'],5000)
        self.assertEqual(p['query'],'dentists in Kyoto')

    def test_map_limit_supports_one_hundred_without_silent_shrinking(self):
        condition=dict(fact_type='location',name='search_area',op='within',
                       value=dict(lat=47.6,lng=-122.3,radius_m=1200))
        for count in (21,22,99,100):
            with self.subTest(count=count):
                self.assertEqual(maps_parameters(task(max_items=count))['max_places'],count)
                self.assertEqual(maps_parameters(task(max_items=count,conditions=[condition]))['max_places'],count)
        with self.assertRaisesRegex(Exception,'between 1 and 100'):
            maps_parameters(task(max_items=101))

from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory
from pathlib import Path
from realtime.maps_location import resolve, location_text
from realtime.product import ProductError


class MapsLocationTests(unittest.TestCase):
    def response(self, **changes):
        row=dict(lat='47.6038321',lon='-122.3300620',display_name='Seattle, Washington, United States',addresstype='city',importance=.75,address={'country_code':'us'},osm_type='relation',osm_id=237385)
        row.update(changes)
        return row

    def test_observed_coordinates_are_cached_and_attributed(self):
        with TemporaryDirectory() as tmp:
            session=Mock();session.get.return_value=Mock(status_code=200,content=b'[]')
            session.get.return_value.json.return_value=[self.response()]
            opts=dict(session=session,cache_file=str(Path(tmp)/'geo.sqlite'))
            result=resolve('Seattle',**opts)
            self.assertEqual(result['location']['lat'],47.6038321)
            self.assertEqual(result['provider'],'nominatim')
            self.assertEqual(resolve('Seattle',**opts),result)
            session.get.assert_called_once()
            self.assertNotIn('countrycodes',session.get.call_args.kwargs['params'])
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])

    def test_ambiguous_empty_malformed_and_unavailable_never_guess(self):
        for rows,code in [([], 'maps_location_not_found'),([self.response(),self.response(importance=.7)],'maps_location_ambiguous'),([self.response(lat='nan')],'maps_geocoder_invalid')]:
            with self.subTest(code=code),TemporaryDirectory() as tmp:
                session=Mock();session.get.return_value=Mock(status_code=200,content=b'[]')
                session.get.return_value.json.return_value=rows
                with self.assertRaises(ProductError) as error:resolve('Springfield',session=session,cache_file=str(Path(tmp)/'geo.sqlite'))
                self.assertEqual(error.exception.code,code)
        with TemporaryDirectory() as tmp:
            session=Mock();session.get.return_value.status_code=503
            with self.assertRaises(ProductError) as error:resolve('Seattle',session=session,cache_file=str(Path(tmp)/'geo.sqlite'))
            self.assertEqual(error.exception.code,'maps_geocoder_unavailable')

    def test_location_must_be_explicit_and_query_is_not_rewritten(self):
        self.assertEqual(location_text('coffee in Seattle'),'Seattle')
        self.assertEqual(location_text('dentists near Kyoto, Japan'),'Kyoto, Japan')
        self.assertEqual(location_text('西雅图的咖啡店'),'西雅图')
        with self.assertRaises(ProductError):location_text('coffee')

    def test_maps_limits_actual_places_and_preserves_original_raw_data(self):
        from realtime.maps_provider import normalize
        rows=[dict(title='Cafe '+str(i),data_id=str(i),latitude=47.6038+i*.0001,longitude=-122.3300) for i in range(4)]
        p=dict(location=dict(lat=47.6038321,lng=-122.3300620),radius_m=5000,max_places=2)
        self.assertEqual(len(normalize(rows,p)),2)
        self.assertEqual(len(rows),4)

    def test_website_uses_the_same_location_resolver(self):
        from realtime.maps_provider import prepare
        from realtime.google_local_jobs import parameters
        with patch('realtime.maps_location.resolve',return_value={'location':{'lat':47.6038,'lng':-122.3300}}) as call:
            p=prepare(parameters({'query':'coffee in Seattle'}))
        self.assertEqual(p['location']['lat'],47.6038)
        call.assert_called_once()

    def test_website_text_contract_is_dispatched_without_geocoding_on_api(self):
        from realtime.middle_maps_contract import maps_request, task_parameters
        from realtime.google_local_jobs import parameters
        p=parameters(dict(query='coffee in Seattle',max_places=2))
        with patch('realtime.maps_location.resolve') as geocoder:
            request=maps_request(p)
        geocoder.assert_not_called()
        self.assertEqual(request['conditions'],[])
        self.assertEqual(request['max_items'],2)
        t=task(region='US',language='en'); t['payload']['conditions']=[]
        parsed=task_parameters(t,query_id='lq_seattle',task_id='ctask_seattle',expected_parameters=p)
        self.assertIsNone(parsed['location'])
        self.assertEqual(parsed['max_places'],2)

    def test_explicit_conditions_are_preserved_and_unsupported_ones_rejected(self):
        condition=dict(fact_type='location',name='search_area',op='within',value=dict(lat=47.6,lng=-122.3,radius_m=1200))
        parsed=maps_parameters(task(conditions=[condition],max_items=1))
        self.assertEqual(parsed['radius_m'],1200)
        self.assertNotIn('max_places',parsed)
        parsed=maps_parameters(task(conditions=[condition],max_items=3))
        self.assertEqual(parsed['max_places'],3)
        with self.assertRaises(ProductError):maps_parameters(task(conditions=[dict(condition,name='ignored')]))
        for changes in [dict(max_items=True),dict(language='invalid'),dict(url='https://example.org'),dict(conditions={'x':1})]:
            with self.subTest(changes=changes),self.assertRaises(ProductError):maps_parameters(task(**changes))

    def test_original_request_collects_two_places_with_resolution_provenance(self):
        import hashlib,json
        from realtime.maps_provider import normalize
        from realtime.middle_direct import DirectTasks
        from realtime.google_data import digest
        runtime=object.__new__(DirectTasks)
        runtime.platform='google_maps';runtime.save=Mock()
        rows=[dict(title='Seattle cafe '+str(i),data_id='id:'+str(i),latitude=47.6038+i*.001,longitude=-122.3300,address='Seattle, WA') for i in range(4)]
        def search(p):
            self.assertEqual(p['location'],{'lat':47.6038321,'lng':-122.3300620})
            return dict(raw_response=rows,places=normalize(rows,p),provider='gosom',provider_version='1.18.2',ordering='distance',response_hash=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest())
        runtime.search=Mock(side_effect=search)
        resolution=dict(location={'lat':47.6038321,'lng':-122.3300620},provider='nominatim',display_name='Seattle, WA, US')
        state={'task':task()}
        with patch('realtime.maps_location.resolve',return_value=resolution) as call:
            messages=DirectTasks.collect(runtime,state)
        call.assert_called_once_with('Seattle',region='',language='en')
        payload=messages[0]['content']['platform_payload']
        self.assertEqual(payload['place_count'],2)
        self.assertEqual(len(payload['raw_response']),4)
        self.assertEqual(payload['original_request'],task()['payload'])
        self.assertEqual(payload['location_resolution'],resolution)
        self.assertEqual(messages[0]['source']['payload_hash'],'sha256:'+digest(payload))
        self.assertEqual(state['resolved_parameters']['max_places'],2)
