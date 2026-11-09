import copy
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from realtime.google_domain import DomainError
from realtime.google_serp_delivery import saved_web_run_message, with_search_snapshot_text


class FullRunDeliveryTests(unittest.TestCase):
    def row(self):
        return dict(request_id=str(uuid4()),source='google_web',query='C++ research',page=1,
            classification='results',completed_at=datetime(2026,11,6,tzinfo=timezone.utc),
            response={'search_parameters':{'gl':'us','hl':'en'},
                      'organic_results':[{'link':'https://example.com/a','position':1},
                                         {'link':'https://example.com/a','position':2}],
                      'serp_features':[{'type':'unknown_feature','api_key':'do-not-leak'}]})

    def build(self,row):
        return saved_web_run_message(row,dataset_id='social_media_raw',source_name='research',collector_id='collector-one')

    def test_full_run_is_not_merged_into_one_publisher_url(self):
        row=self.row();message=self.build(row)
        self.assertEqual(message,self.build(copy.deepcopy(row)))
        self.assertNotIn('canonical_url',message['content'])
        self.assertNotIn('body_text',message['content'])
        self.assertNotIn('published_at',message['content'])
        raw=message['content']['platform_payload']
        self.assertEqual(raw['run_id'],row['request_id'])
        self.assertEqual(len(raw['observation']['occurrences']),2)
        self.assertNotEqual(*[x['occurrence_id'] for x in raw['observation']['occurrences']])
        self.assertNotIn('do-not-leak',str(message))
        newer={**row,'request_id':str(uuid4())}
        other=self.build(newer)
        self.assertNotEqual(message['source']['source_record_key'],other['source']['source_record_key'])
        self.assertNotEqual(message['content']['external_content_id'],other['content']['external_content_id'])
        self.assertEqual(message['content']['platform_payload']['observation']['query_variant_id'], other['content']['platform_payload']['observation']['query_variant_id'])
        self.assertNotEqual(message['source']['payload_hash'],other['source']['payload_hash'])

    def test_unknown_time_and_failed_request_remain_explicit(self):
        row=self.row();row.update(classification='timeout')
        message=self.build(row)
        self.assertEqual(message['content']['platform_payload']['observation']['availability'],'source_error')
        self.assertIsNone(message['content']['platform_payload']['original_source_observed_at'])
        self.assertEqual(message['discovery']['acquisition_type'],'backfill')
        for change in ({'source':'google_news'},{'completed_at':datetime(2026,11,6)},{'request_id':'bad'}):
            with self.subTest(change=change),self.assertRaises(DomainError):self.build({**row,**change})

    def test_text_correction_preserves_pinned_source_and_object(self):
        row = self.row()
        row['response']['organic_results'][0].update(title='Actual title', snippet='Saved snippet')
        message = self.build(row)
        before = copy.deepcopy(message)
        corrected = with_search_snapshot_text(message)
        self.assertEqual(message, before)
        self.assertEqual(corrected['content']['platform_payload'], message['content']['platform_payload'])
        self.assertEqual(corrected['source']['payload_hash'], message['source']['payload_hash'])
        self.assertEqual(corrected['content']['external_content_id'], message['content']['external_content_id'])
        self.assertNotEqual(corrected['source']['source_record_key'], message['source']['source_record_key'])
        self.assertEqual(corrected, with_search_snapshot_text(message))
        self.assertEqual(corrected, with_search_snapshot_text(corrected))
        body = corrected['content']['body_text']
        self.assertIn('not publisher article body', body)
        self.assertIn('Actual title', body)
        self.assertIn('Saved snippet', body)
        self.assertEqual(body.count('URL: "https://example.com/a"'), 2)
        self.assertNotIn('published_at', corrected['content'])
        self.assertNotIn('do-not-leak', str(corrected))

    def test_missing_result_fields_are_not_invented_and_bad_envelopes_rejected(self):
        row = self.row()
        row['response']['organic_results'] = [{'link':'https://example.com/a'}]
        message = self.build(row)
        body = with_search_snapshot_text(message)['content']['body_text']
        self.assertNotIn('Organic position:', body)
        self.assertNotIn('Title:', body)
        self.assertNotIn('Snippet:', body)
        for field in ('schema_version','run_id'):
            bad = copy.deepcopy(message)
            bad['content']['platform_payload'][field] = 'invalid'
            with self.subTest(field=field),self.assertRaises(DomainError):
                with_search_snapshot_text(bad)
        for change in ({'classification':'timeout'}, {'response':{'organic_results':[]}}):
            with self.subTest(change=change),self.assertRaises(DomainError):
                with_search_snapshot_text(self.build({**row, **change}))
