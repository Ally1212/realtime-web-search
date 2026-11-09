import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from realtime.google_data import normalize_request
from realtime.google_field_adaptation import adapt_request_fields, validate_field_contract, FIELD_VERSION, _number
from realtime.google_domain import DomainError
from realtime.google_legacy import read_public_requests, read_public_request
from realtime.google_data_export import export_public_data, verify_bundle
from realtime.google_serp_delivery import saved_web_run_message
from tests.test_google_data import request
from tests import test_google_legacy as legacy


class FieldAdaptationTests(unittest.TestCase):
    def test_opt_in_preserves_every_legacy_fact_and_immutable_event(self):
        row = request(rid='a124135e-ac5c-4aca-956e-fd0c536bca31', response={
            'organic_results':[dict(title='Observed', link='https://example.com/', position=1)]})
        before = copy.deepcopy(row)
        value = adapt_request_fields(row)
        self.assertEqual(value.pop('field_adaptation'), adapt_request_fields(row)['field_adaptation'])
        self.assertEqual(value, normalize_request(row))
        self.assertEqual(row, before)
        message = saved_web_run_message(row, dataset_id='dataset', source_name='fixture', collector_id='collector')
        self.assertEqual(message['content']['platform_payload']['observation'], normalize_request(row))
        self.assertNotIn('field_adaptation', message['content']['platform_payload']['observation'])

    def test_country_language_do_not_invent_device_source_time_or_google_api_version(self):
        value = adapt_request_fields(request(response={
            'search_parameters':dict(gl='US', hl='en', num=10),
            'search_metadata':dict(provider='openserp', upstream_version='2.1'),
            'organic_results':[dict(link='https://example.com/', position=1)]}))['field_adaptation']
        run = value['run']
        self.assertEqual(run['requested_country']['value'], 'US')
        self.assertEqual(run['provider_version']['value'], '2.1')
        for field in ('source_api_version', 'source_parser_version', 'device', 'source_language',
                      'query_language', 'source_observed_at', 'location_precision', 'ingested_at'):
            self.assertIsNone(run[field]['value'], field)
            self.assertEqual(run[field]['availability'], 'unknown')
        self.assertEqual(run['requested_item_limit']['value'], 10)
        self.assertEqual(run['returned_item_count']['value'], 1)
        self.assertIsNone(value['rights']['license_id'])
        self.assertFalse(value['rights']['source_admission_verified'])

    def test_time_basis_is_explicit_and_not_publisher_time(self):
        row = request(response={'search_metadata':{'collection_times':{
            'received_at':'2026-11-05T12:00:00+08:00', 'requested_at':'2026-11-05T03:59:58Z'}}})
        run = adapt_request_fields(row)['field_adaptation']['run']
        self.assertEqual(run['observed_at']['value'], '2026-11-05T04:00:00Z')
        self.assertEqual(run['observed_at']['basis'], 'received_at')
        self.assertIsNone(run['source_observed_at']['value'])
        row['response']['search_metadata']['collection_times']['received_at'] = '2026-11-05T12:00:00'
        run = adapt_request_fields(row)['field_adaptation']['run']
        self.assertEqual(run['observed_at']['basis'], 'completed_at proxy')

    def test_cache_unknown_false_zero_and_age_are_distinct(self):
        for metadata, hit, age in [({}, None, None), ({'cache_status':'MISS'}, False, None),
                                   ({'cache_hit':True, 'cache_age_seconds':0}, True, 0),
                                   ({'cache_hit':False, 'cache_age_seconds':True}, False, None)]:
            run = adapt_request_fields(request(response={'search_metadata':metadata}))['field_adaptation']['run']
            self.assertIs(run['cache_hit']['value'], hit)
            self.assertEqual(run['cache_age_seconds']['value'], age)
        run = adapt_request_fields(request(response={'search_parameters':{'safe_search':False,'start':0}}))['field_adaptation']['run']
        self.assertIs(run['safe_search']['value'], False)
        self.assertEqual(run['safe_search']['availability'], 'available')
        self.assertEqual(run['requested_offset']['value'], 0)
        self.assertIsNone(_number(float('inf')))

    def test_partial_invalid_and_has_more_do_not_prove_complete_or_truncated(self):
        record = adapt_request_fields(request(response={'pagination':{'has_more':True},
            'organic_results':[None, dict(link='https://example.com/')]}))
        self.assertIs(record['field_adaptation']['run']['is_partial']['value'], True)
        self.assertIsNone(record['field_adaptation']['run']['is_truncated']['value'])
        empty = adapt_request_fields(request(response={}))['field_adaptation']
        self.assertIsNone(empty['run']['is_partial']['value'])
        self.assertFalse(empty['feature_absence_inference'])

    def test_failed_response_zero_saved_records_is_not_a_true_zero_return(self):
        value = adapt_request_fields(request(status='timeout'))['field_adaptation']['run']
        self.assertEqual(value['stored_occurrence_count']['value'], 0)
        self.assertIsNone(value['returned_item_count']['value'])
        self.assertEqual(value['returned_item_count']['missing_reason'], 'source_error')

    def test_question_error_blob_is_not_promoted_to_answer_and_item_links_are_explicit(self):
        value = adapt_request_fields(request(response={
            'organic_results':[dict(link='https://example.com/', upstream_result_id='s1')],
            'serp_features':[dict(type='people_also_ask', source_result_ids=['s1'], items=[
                dict(title='What is solar?', text='What is solar?An error has occurred.')])]}))
        feature = value['field_adaptation']['features'][0]
        self.assertEqual(feature['matched_occurrence_ids'], [value['occurrences'][0]['occurrence_id']])
        self.assertIsNone(feature['children'][0]['answer_text'])
        self.assertEqual(feature['children'][0]['answer_availability'], 'unknown')
        self.assertIn('error', feature['children'][0]['text'])

    def test_features_preserve_duplicates_unknown_types_and_do_not_invent_citations(self):
        feature = dict(id='same-id', type='ai_summary', text='Observed answer', links=[
            dict(url='https://example.com/a', title='Link'), dict(url='https://example.com/a')])
        row = request(response={'serp_features':[feature, feature, dict(type='new_provider_block')]})
        value = adapt_request_fields(row)['field_adaptation']
        self.assertEqual(len(value['features']), 3)
        a,b,c = value['features']
        self.assertNotEqual(a['feature_id'], b['feature_id'])
        self.assertEqual(a['target_urls'], ['https://example.com/a']*2)
        self.assertEqual(a['citation_urls'], [])
        self.assertEqual(a['citation_availability'], 'unknown')
        self.assertIsNone(a['channel'])
        self.assertIsNone(a['block_order'])
        self.assertIsNone(a['expanded_state'])
        self.assertEqual(c['feature_type'], 'new_provider_block')
        self.assertEqual(adapt_request_fields(row)['field_adaptation'], value)

    def test_named_passthrough_is_not_doubled_but_named_occurrences_stay_distinct(self):
        feature = dict(id='f1', type='people_also_ask', text='Question')
        named = dict(type='knowledge_panel', title='Entity')
        value = adapt_request_fields(request(response={'serp_features':[feature],
            'related_questions':[feature], 'knowledge_graph':[named, named]}))['field_adaptation']
        self.assertEqual(len(value['features']), 3)
        self.assertEqual(sum(f['feature_type']=='people_also_ask' for f in value['features']), 1)

    def test_explicit_citations_expansion_order_and_malformed_blocks(self):
        value = adapt_request_fields(request(response={'serp_features':[None, dict(
            type='ai_summary', expanded=False, position={'absolute':3}, citations=[
                {'url':'https://example.org/article'}, {'url':'http://127.0.0.1/private'}])]}))['field_adaptation']
        self.assertEqual(len(value['invalid_features']), 1)
        feature = value['features'][0]
        self.assertEqual(feature['observed_order'], 2)
        self.assertEqual(feature['block_order'], 3)
        self.assertEqual(feature['expanded_state'], 'collapsed')
        self.assertEqual(feature['citation_urls'], ['https://example.org/article'])
        self.assertTrue(feature['citations_partial'])

    def test_news_wrapper_and_image_metadata_do_not_become_publisher_article(self):
        news = adapt_request_fields(request('google_news', {'news_results':[dict(
            link='https://news.google.com/rss/articles/wrapper', publisher='Publisher',
            date='2026-11-05T00:00:00Z', date_raw='Thu, 05 Nov 2026 00:00:00 GMT')]}))['field_adaptation']['items'][0]
        self.assertIsNone(news['publisher_article_url'])
        self.assertEqual(news['publication_basis'], 'rss_pubDate')
        self.assertFalse(news['publisher_page_verified'])
        image = adapt_request_fields(request('google_images', {'image_results':[dict(
            link='https://cdn.example.com/a.jpg?w=200', source_url='https://example.com/a', width=200)]}))
        self.assertEqual(image['field_adaptation']['items'][0]['content_semantics'], 'image_metadata')
        self.assertEqual(image['occurrences'][0]['url']['normalized_url'], 'https://cdn.example.com/a.jpg?w=200')
        web = adapt_request_fields(request(response={'organic_results':[dict(link='https://example.com/category',
            redirect_link='https://www.google.com/goto?url=observed-redirect')]}))
        self.assertIsNone(web['field_adaptation']['items'][0]['publisher_article_url'])
        self.assertEqual(web['field_adaptation']['items'][0]['redirect_url'], 'https://www.google.com/goto?url=observed-redirect')

    def test_credentials_do_not_enter_new_nested_fields(self):
        value = adapt_request_fields(request(response={'search_parameters':{'location':{'label':'London','api_key':'private'}},
            'serp_features':[dict(type='unknown', cookie='private', links=[{'url':'https://user:private@example.com/a'}])]}))
        self.assertNotIn('private', json.dumps(value))
        self.assertEqual(value['field_adaptation']['features'][0]['target_urls'], [])

    def test_contract_requires_explicit_supported_version(self):
        for value in ('latest', '', None, [], True):
            with self.assertRaises(DomainError):
                validate_field_contract(value)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class FieldAdaptationBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(legacy.LegacyPreviewTests.setUpClass.__func__)
    tearDownClass = classmethod(legacy.LegacyPreviewTests.tearDownClass.__func__)
    setUp = legacy.LegacyPreviewTests.setUp
    insert = legacy.LegacyPreviewTests.insert

    def seed(self):
        public = self.insert('google_web', {'organic_results':[dict(link='https://example.com/a', position=1)],
            'serp_features':[dict(type='ai_summary', text='Public observed answer')]})
        private = self.insert('google_web', {'serp_features':[dict(type='ai_summary', text='PRIVATE ANSWER')]}, private=True)
        return public, private

    def test_opt_in_list_detail_private_isolation_and_legacy_unchanged(self):
        public, private = self.seed()
        old = read_public_requests(self.dsn, {}, normalized=True)['requests']
        self.assertNotIn('field_adaptation', old[0])
        new = read_public_requests(self.dsn, {'field_contract':'prd-v1'}, normalized=True)['requests']
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]['field_adaptation']['version'], FIELD_VERSION)
        self.assertEqual(read_public_request(self.dsn, public, normalized=True, field_contract='prd-v1')['request'], new[0])
        self.assertNotIn('PRIVATE ANSWER', str(new))
        with self.assertRaises(DomainError):
            read_public_request(self.dsn, private, normalized=True, field_contract='prd-v1')
        for kwargs in ({'field_contract':'unknown'}, {'field_contract':'prd-v1'}):
            with self.assertRaises(DomainError):
                read_public_requests(self.dsn, kwargs)

    def test_lossless_export_opt_in_and_original_bundle_both_verify(self):
        self.seed()
        with tempfile.TemporaryDirectory() as d:
            old = Path(d)/'old'; new = Path(d)/'new'
            export_public_data(self.dsn, old)
            manifest = export_public_data(self.dsn, new, filters={'field_contract':'prd-v1'})
            self.assertEqual(manifest['field_adaptation_version'], FIELD_VERSION)
            self.assertEqual(verify_bundle(old)['status'], 'verified')
            self.assertEqual(verify_bundle(new)['status'], 'verified')
            enhanced = json.loads((new/'requests.jsonl').read_text())
            self.assertEqual(enhanced['field_adaptation']['features'][0]['text'], 'Public observed answer')
            self.assertNotIn('PRIVATE ANSWER', (new/'requests.jsonl').read_text())
