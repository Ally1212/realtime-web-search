from dataclasses import replace
import unittest
from realtime.config import Config
from realtime.middle_collector import live_payload
from realtime.middle_ingest import task_spec

class MiddleCollectorTests(unittest.TestCase):
    def payload(self):
        return dict(query='battery',region='US',language='en',max_items=3,
                    live_query_id='lq_abc123',resource_kind='content',url='',external_id='')

    def test_real_live_contract_maps_to_bounded_web_without_losing_task_type(self):
        c=replace(Config(),whale_dataset_id='social_media_raw',whale_source_platform='google_search',
                  whale_supported_task_types=('keyword_search','live_query'))
        task=dict(dataset_id='social_media_raw',source_platform='google_search',task_type='live_query',
                  required_capabilities=['identity','title'],payload=self.payload())
        s=task_spec(task,c)
        self.assertEqual(s['sources'],['google_web'])
        self.assertEqual(s['task_type'],'live_query')
        self.assertEqual(s['max_items'],3)
        self.assertEqual(s['google_pages'],11)
        self.assertEqual(s['options']['web_region'],'US')
        self.assertEqual(task['payload'],self.payload())

    def test_wrong_resource_and_unknown_fields_reject_before_collection(self):
        for change in ({'resource_kind':'user'},{'url':'https://example.com'},{'external_id':'id'},
                       {'max_items':True},{'max_items':101},{'live_query_id':'../id'},{'extra':1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                live_payload({**self.payload(),**change})

    def test_type_must_be_explicitly_enabled_in_collector_configuration(self):
        c=replace(Config(),whale_dataset_id='social_media_raw',whale_source_platform='google_search',
                  whale_supported_task_types=('keyword_search',))
        with self.assertRaises(ValueError):
            task_spec(dict(dataset_id='social_media_raw',source_platform='google_search',
                           task_type='live_query',payload=self.payload()),c)

    def test_feed_native_options_preserve_requested_region_and_language(self):
        for platform, region, language, expected in (('google_news', 'GB', 'en', {'news_locale': 'GB:en'}),
                                                    ('google_news', 'HK', 'zh', {'news_locale': 'HK:zh-Hant'}),
                                                    ('google_trends', 'SG', 'en', {'trends_geo': 'SG'})):
            c = replace(Config(), whale_source_platform=platform, whale_supported_task_types=('live_query',))
            task = dict(dataset_id=c.whale_dataset_id, source_platform=platform, task_type='live_query',
                        payload={**self.payload(), 'region': region, 'language': language})
            if platform == 'google_trends':
                from realtime.middle_collector import trends_conditions
                task['payload']['conditions'] = trends_conditions()
            spec = task_spec(task, c)
            for key, value in expected.items():
                self.assertEqual(spec['options'][key], value)
            self.assertEqual(spec['google_pages'], 1)
