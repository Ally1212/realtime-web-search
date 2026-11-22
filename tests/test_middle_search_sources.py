import unittest
from unittest.mock import Mock
from realtime.middle_platform_client import SearchMiddlePlatformClient, search_request
from realtime.product import ProductError, parameters, middle_search_payload


class SearchSourcesTests(unittest.TestCase):
    def test_source_is_preserved_in_request_and_response(self):
        for source, platform in [('google_web','google_search'), ('google_news','google_news'), ('google_trends','google_trends')]:
            payload = dict(source=source, query='battery', language='en', region='US')
            if source == 'google_trends':
                payload['trends_mode'] = 'regional_hotlist'
            request = search_request(payload)
            self.assertEqual(request['source_platforms'], [platform])
            session = Mock()
            session.request.return_value = Mock(status_code=202)
            session.request.return_value.json.return_value = dict(live_query=dict(request, id='lq_test', status='pending'),
                runs=[dict(id='run_test', live_query_id='lq_test', source_platform=platform,
                           collection_task_id='ctask_test', status='pending')])
            client = SearchMiddlePlatformClient('https://middle.test', 'secret', source=source, session=session)
            self.assertFalse(client.create('tenant', payload, 'retry')['download_ready'])
            session.request.return_value.json.return_value['runs'][0]['source_platform'] = 'website'
            with self.assertRaises(ProductError):
                client.create('tenant', payload, 'retry')

    def test_images_are_not_disguised_as_web(self):
        request = search_request(dict(source='google_images', query='battery'))
        self.assertEqual(request['source_platforms'], ['google_search'])
        self.assertEqual(request['dataset_id'], 'social_media_raw')

    def test_product_accepts_middle_feeds_and_preserves_source(self):
        for source in ('google_news', 'google_trends'):
            result = parameters(dict(query='battery', source=source, provider='middle', **({'trends_mode': 'regional_hotlist'} if source == 'google_trends' else {})))
            self.assertEqual((result['source'], result['provider']), (source, 'middle'))

    def test_new_customer_requests_cannot_bypass_middle(self):
        for source in ('google_web', 'google_news', 'google_trends'):
            payload = middle_search_payload({'query': 'battery', 'source': source, **({'trends_mode': 'regional_hotlist'} if source == 'google_trends' else {})})
            self.assertEqual(payload['provider'], 'middle')
            self.assertEqual(payload['source'], source)
        for provider in ('openserp', 'serper'):
            with self.assertRaises(ProductError) as error:
                middle_search_payload({'query': 'battery', 'provider': provider})
            self.assertEqual(error.exception.status, 410)
        with self.assertRaises(ProductError):
            middle_search_payload({'query': 'battery', 'source': 'google_images'})
