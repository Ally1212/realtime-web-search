"""Business acceptance gates from the 2026-11-17 Google investigation."""
import unittest
from realtime.middle_collector import live_payload
from realtime.middle_ingest import task_spec
from realtime.config import Config
from dataclasses import replace


class GoogleLiveAcceptanceTests(unittest.TestCase):
    def payload(self, **changes):
        return dict(query='OpenAI', region='US', language='en', max_items=20,
                    live_query_id='lq_acceptance', resource_kind='content', **changes)

    def test_twenty_web_items_allow_more_than_one_page(self):
        result = live_payload(self.payload())
        self.assertGreaterEqual(result['max_pages'], 2)

    def test_page_budget_reaches_task_options(self):
        config = replace(Config(), whale_supported_task_types=('live_query',),
                         whale_source_platform='google_search')
        for count, pages in ((1, 11), (10, 11), (11, 11), (20, 11), (100, 11)):
            payload = self.payload()
            payload['max_items'] = count
            spec = task_spec(dict(task_type='live_query', dataset_id=config.whale_dataset_id,
                                  source_platform='google_search', payload=payload), config)
            self.assertEqual(spec['google_pages'], pages)
            self.assertEqual(spec['max_pages'], pages)
            self.assertEqual(spec['options']['web_pages'], pages)

    def test_keyword_trends_must_not_silently_become_regional_feed(self):
        result = live_payload(self.payload(), source='google_trends')
        self.assertEqual(result['trends_mode'], 'keyword')
        self.assertEqual(result['trends_time_range'], 'today 3-m')
        with self.assertRaisesRegex(ValueError, '关键词|keyword'):
            live_payload(self.payload(conditions=[dict(name='unknown')]), source='google_trends')


class MultiPageSelectionTests(unittest.TestCase):
    def test_merge_deduplicates_and_keeps_original_page_evidence(self):
        from realtime.middle_live_runtime import selected_pages
        rows = [dict(request_id='r1', page=1, response=dict(organic_results=[
                    dict(link='https://example.com/a', title='first')])),
                dict(request_id='r2', page=2, response=dict(organic_results=[
                    dict(link='https://example.com/a', title='duplicate'),
                    dict(link='https://example.com/b', title='second')]))]
        import copy
        before = copy.deepcopy(rows)
        selections = selected_pages(rows, 'google_web', 20)
        self.assertEqual([(r['request_id'], [i['title'] for i in items])
                          for r, items, raw in selections], [('r1', ['first']), ('r2', ['second'])])
        self.assertEqual(rows, before)
        self.assertEqual(selections[1][2]['page'], 2)

    def test_limit_applies_across_pages(self):
        from realtime.middle_live_runtime import selected_pages
        rows = [dict(page=n, response=dict(organic_results=[dict(link=f'https://example.com/{n}')]))
                for n in range(1, 4)]
        self.assertEqual(sum(len(items) for _, items, _ in selected_pages(rows, 'google_web', 2)), 2)


class DirectMultiPageTests(unittest.TestCase):
    def test_direct_collection_delivers_both_saved_pages(self):
        from unittest.mock import MagicMock, Mock, patch
        from realtime.middle_direct import DirectTasks
        runner = object.__new__(DirectTasks)
        runner.platform, runner.source = 'google_search', 'google_web'
        runner.config = Config()
        runner.base = MagicMock()
        runner.save = Mock()
        runner.collector = Mock(agent_id='agent')
        rows = [dict(request_id=f'r{n}', page=n, classification='results',
                     response=dict(organic_results=[dict(link=f'https://example.com/{n}')]))
                for n in (1, 2)]
        runner.base.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = rows
        state = dict(campaign_id='campaign', task=dict(id='task', payload=dict(query='battery', live_query_id='lq_test')))
        with patch('realtime.middle_direct.task_spec', return_value=dict(keywords=['battery'],
                   sources=['google_web'], options={}, google_pages=2, max_items=20, max_duration_minutes=5)), \
             patch('realtime.serp_worker.run_serp_campaign') as collect, \
             patch('realtime.customer_data.project', side_effect=lambda raw: dict(page=raw['page'])), \
             patch('realtime.middle_direct.link_message', return_value=('key', dict(discovery={}))), \
             patch('realtime.middle_live_runtime.bind_live_observation', side_effect=lambda message, **kw: dict(message, record=kw['record'])):
            messages = runner.collect(state)
        collect.assert_not_called()
        self.assertEqual([message['record']['page'] for message in messages], [1, 2])


class MultiPageReadbackTests(unittest.TestCase):
    def test_combined_pages_survive_pinned_readback_and_detect_tampering(self):
        import copy
        from unittest.mock import patch
        from realtime.middle_live_runtime import combined_observation, verified_record
        from tests.test_middle_live_runtime import sample
        first = dict(request_id='r1', completed_at='2026-11-17T00:00:01Z',
                     occurrences=[dict(occurrence_id='one', request_id='r1')], topics=[], source='google_web')
        second = dict(request_id='r2', completed_at='2026-11-17T00:00:02Z',
                      occurrences=[dict(occurrence_id='two', request_id='r2')], topics=[], source='google_web')
        with patch('realtime.middle_live_runtime.project', side_effect=[first, second]):
            record = combined_observation([(None, None, {}), (None, None, {})])
        self.assertEqual(record['request_ids'], ['r1', 'r2'])
        self.assertEqual([o['request_id'] for o in record['occurrences']], ['r1', 'r2'])
        self.assertEqual(record['page_observations'], [first, second])
        state, reader, document, version = sample()
        state['messages'][0]['content']['platform_payload']['record'] = record
        state['messages'].append(copy.deepcopy(state['messages'][0]))
        state['receipts'].append(copy.deepcopy(state['receipts'][0]))
        # Each selected link carries the same immutable aggregate snapshot.
        result = verified_record(reader, state, 'social_media_raw')
        self.assertEqual(result['request_ids'], ['r1', 'r2'])
        tampered = copy.deepcopy(version)
        tampered['platform_payload']['record']['page_observations'][1]['request_id'] = 'wrong'
        reader._read_pair.return_value = (document, tampered)
        with self.assertRaises(ValueError):
            verified_record(reader, state, 'social_media_raw')


class CoverageTests(unittest.TestCase):
    def test_budget_exhaustion_does_not_claim_no_more_results(self):
        from realtime.middle_live_runtime import collection_coverage
        result = collection_coverage([dict(page=1, classification='results')], 20, 1, 4)
        self.assertEqual(result['stop_reason'], 'page_budget_exhausted')
        self.assertIsNone(result['has_more'])
        self.assertEqual(result['pages_searched'], [1])

    def test_empty_page_and_limit_are_distinct(self):
        from realtime.middle_live_runtime import collection_coverage
        rows = [dict(page=1, classification='results'), dict(page=2, classification='empty')]
        self.assertEqual(collection_coverage(rows, 20, 2, 4)['stop_reason'], 'empty_page')
        self.assertEqual(collection_coverage(rows, 4, 2, 4)['stop_reason'], 'max_items_reached')


class FailureCoverageTests(unittest.TestCase):
    def test_failed_page_is_disclosed_and_not_counted_as_success(self):
        from realtime.middle_live_runtime import collection_coverage
        rows = [dict(page=1, classification='results'), dict(page=2, classification='rate_limited', http_status=429, error_code='google_http_429')]
        result = collection_coverage(rows, 20, 2, 4)
        self.assertTrue(result['partial_failure'])
        self.assertEqual(result['failed_pages'], [2])
        self.assertEqual(result['failures'][0]['http_status'], 429)
        self.assertEqual(result['successful_pages'], [1])
        self.assertEqual(result['stop_reason'], 'partial_failure')


class TrendsModeTests(unittest.TestCase):
    def test_explicit_regional_hotlist_is_accepted(self):
        from realtime.middle_collector import trends_conditions
        payload = GoogleLiveAcceptanceTests().payload(conditions=trends_conditions())
        result = live_payload(payload, source='google_trends')
        self.assertEqual(result['sources'], ['google_trends'])
        self.assertEqual(result['max_pages'], 1)


class TrendsRequestTests(unittest.TestCase):
    def test_keyword_is_rejected_before_submission(self):
        from realtime.middle_platform_client import search_request
        from realtime.product import ProductError
        request=search_request(dict(source='google_trends',query='OpenAI'))
        self.assertEqual(request['conditions'][0]['value'],'keyword')

    def test_explicit_hotlist_mode_reaches_collector(self):
        from realtime.middle_platform_client import search_request
        request = search_request(dict(source='google_trends', query='Regional hotlist',
                                      trends_mode='regional_hotlist'))
        payload = {k: v for k, v in request.items() if k not in ('source_platforms', 'dataset_id', 'deadline_seconds')}
        payload['live_query_id'] = 'lq_modes'
        result = live_payload(payload, source='google_trends')
        self.assertEqual(result['sources'], ['google_trends'])


class ProductTrendsModeTests(unittest.TestCase):
    def test_keyword_request_rejected_before_job_creation(self):
        from realtime.product import parameters, ProductError
        result=parameters(dict(query='OpenAI',source='google_trends'))
        self.assertEqual(result['trends_mode'],'keyword')

    def test_hotlist_mode_survives_parameter_canonicalization(self):
        from realtime.product import parameters, canonical_parameters
        result = parameters(dict(query='Regional hotlist', source='google_trends', trends_mode='regional_hotlist'))
        self.assertEqual(result['trends_mode'], 'regional_hotlist')
        self.assertEqual(canonical_parameters(result), result)


class TrendsCapabilityTests(unittest.TestCase):
    def test_capabilities_disclose_required_mode_and_count_unit(self):
        from realtime.product import capability_payload
        source = next(s for s in capability_payload()['sources'] if s['id'] == 'google_trends')
        self.assertEqual(source['default_mode'],'keyword')
        self.assertEqual(source['keyword_trends'],'interest_over_time')
        self.assertIn('keyword',source['query_modes'])


class CollectionCancellationTests(unittest.TestCase):
    def test_deadline_and_campaign_pause_both_gate_discovery(self):
        from unittest.mock import Mock, patch
        from realtime.serp_worker import run_serp_campaign
        for deadline_allows, campaign_allows in ((False, True), (True, False), (True, True)):
            with self.subTest(deadline_allows=deadline_allows, campaign_allows=campaign_allows):
                store, serp, discovery = Mock(), Mock(), Mock()
                store.campaign.return_value = dict(status='active', query='battery', sources=['google_web'])
                store.whale_task_for_campaign.return_value = None
                discovery.should_continue = lambda: campaign_allows
                observed = []
                def discover(*args):
                    observed.append(discovery.should_continue())
                    return [], []
                discovery.discover_many.side_effect = discover
                with patch('realtime.serp_worker.make_discovery', return_value=discovery):
                    run_serp_campaign('campaign', Config(), store, serp,
                                      should_continue=lambda: deadline_allows)
                self.assertEqual(observed, [deadline_allows and campaign_allows])
                discovery.close.assert_called_once()


class CustomerQuantityTests(unittest.TestCase):
    def test_quantity_survives_customer_parameters_and_platform_translation(self):
        from realtime.product import parameters, canonical_parameters
        from realtime.middle_platform_client import search_request
        payload = dict(query='battery', max_items=20)
        normalized = parameters(payload)
        self.assertEqual(normalized['depth'], 20)
        self.assertEqual(canonical_parameters(normalized), normalized)
        request = search_request(payload)
        self.assertEqual(request['max_items'], 20)

    def test_invalid_quantity_is_rejected_at_both_boundaries(self):
        from realtime.product import parameters, ProductError
        from realtime.middle_platform_client import search_request
        for count in (True, 0, 101, '20', 1.5):
            for boundary in (parameters, search_request):
                with self.subTest(count=count, boundary=boundary.__name__), self.assertRaises(ProductError):
                    boundary(dict(query='battery', max_items=count))


class CrossPageDuplicatePrefixTests(unittest.TestCase):
    def test_duplicate_prefix_does_not_hide_later_novel_links(self):
        from realtime.middle_live_runtime import selected_pages
        rows = [dict(page=1, response=dict(organic_results=[dict(link='https://example.org/a')])),
                dict(page=2, response=dict(organic_results=[dict(link='https://example.org/a'),
                    dict(link='https://example.org/b'), dict(link='https://example.org/c')]))]
        selected = selected_pages(rows, 'google_web', 3)
        self.assertEqual([i['link'] for _, items, _ in selected for i in items],
                         ['https://example.org/a', 'https://example.org/b', 'https://example.org/c'])


class SearchOpenApiTests(unittest.TestCase):
    def test_quantity_and_trends_mode_are_documented(self):
        from realtime.product_http import openapi
        operation = openapi()['paths']['/v1/search']['post']
        properties = operation['requestBody']['content']['application/json']['schema']['properties']
        self.assertEqual(properties['max_items']['maximum'], 100)
        self.assertEqual(properties['trends_mode']['enum'], ['keyword','regional_hotlist'])
        self.assertIn('422', operation['responses'])


class DeadlineCoverageTests(unittest.TestCase):
    def test_deadline_is_not_misreported_as_page_budget_exhaustion(self):
        from realtime.middle_live_runtime import collection_coverage
        coverage = collection_coverage([dict(page=1, classification='results')], 20, 2, 4,
                                       deadline_exceeded=True)
        self.assertEqual(coverage['stop_reason'], 'deadline_exceeded')
        self.assertTrue(coverage['deadline_exceeded'])
        self.assertIsNone(coverage['has_more'])


class AllFailedCollectionTests(unittest.TestCase):
    def test_all_failed_pages_cannot_complete_as_successful_empty(self):
        from unittest.mock import MagicMock, Mock, patch
        from realtime.middle_direct import DirectTasks
        runner = object.__new__(DirectTasks)
        runner.platform, runner.source = 'google_search', 'google_web'
        runner.config, runner.base, runner.save = Config(), MagicMock(), Mock()
        runner.collector = Mock(agent_id='agent')
        runner.base.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
            dict(page=1, classification='rate_limited', response={})]
        state = dict(campaign_id='campaign', task=dict(id='task', payload=dict(query='battery')))
        with patch('realtime.middle_direct.task_spec', return_value=dict(keywords=['battery'],
                   sources=['google_web'], options={}, google_pages=1, max_items=10, max_duration_minutes=5)), \
             patch('realtime.serp_worker.run_serp_campaign') as collect, self.assertRaisesRegex(ValueError, 'successful'):
            runner.collect(state)
        collect.assert_not_called()


class MultiPageExportTests(unittest.TestCase):
    def test_csv_retains_each_pages_request_identity_and_number(self):
        from realtime.customer_export import full_rows
        records = [dict(request_id='r'+str(page), occurrences=[], topics=[],
                        query_conditions=dict(page=page)) for page in (1, 2)]
        aggregate = dict(records[0], page_observations=records)
        rows = list(full_rows(aggregate))
        self.assertEqual(rows[0]['record_type'], 'query_snapshot')
        self.assertEqual([(r['request_id'], r['page']) for r in rows[1:]], [('r1', 1), ('r2', 2)])


class MultiPageFieldsTests(unittest.TestCase):
    def test_later_page_adapted_fields_and_hash_scope_are_preserved(self):
        from unittest.mock import patch
        from realtime.middle_live_runtime import combined_observation
        records = [dict(request_id='r'+str(page), completed_at=str(page), occurrences=[], topics=[],
                        source_response_hash='hash'+str(page), field_adaptation=dict(items=[dict(id=page)]))
                   for page in (1, 2)]
        with patch('realtime.middle_live_runtime.project', side_effect=records):
            result = combined_observation([(None, None, {}), (None, None, {})])
        self.assertEqual(result['field_adaptation']['items'], [dict(id=1), dict(id=2)])
        self.assertEqual(result['source_response_hash_scope'], 'first_page')
        self.assertEqual([p['source_response_hash'] for p in result['page_response_hashes']], ['hash1', 'hash2'])


class NewsQualityTests(unittest.TestCase):
    def test_completeness_does_not_claim_relevance_or_article_body(self):
        from realtime.middle_live_runtime import observation_quality
        result = observation_quality(dict(source='google_news', occurrences=[dict(title='OpenAI',
            snippet=None, url=dict(original_url='https://news.google.com/x'), source_fields=dict(publisher='RSS publisher'))]))
        self.assertEqual(result['field_present_counts'], dict(title=1, snippet=0, link=1, publisher=1))
        self.assertEqual(result['ordering'], 'rss_feed_order')
        self.assertEqual(result['relevance_status'], 'not_independently_verified')
        self.assertFalse(result['article_body_collected'])

class WebsiteUpstreamFailureTests(unittest.TestCase):
    def test_news_429_is_not_reported_as_empty_results(self):
        from datetime import timedelta
        from unittest.mock import MagicMock, Mock, patch
        from realtime.middle_live_runtime import LiveRunner
        from realtime.product import ProductError, now
        runner=object.__new__(LiveRunner);runner.source='google_news';runner.config=Config()
        runner.store=MagicMock();runner.links=MagicMock();runner.save=Mock()
        runner.store.base.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=dict(campaign_id='campaign',status='running')
        runner.links.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[dict(page=1,classification='rate_limited',http_status=429)]
        state=dict(task=dict(payload=dict(max_items=20)),platform_task_ids=['task'])
        with patch('realtime.middle_live_runtime.task_spec',return_value=dict(google_pages=1,max_items=20)), \
             patch('realtime.middle_live_runtime.run_serp_campaign',return_value=('google_http_429',)), \
             self.assertRaises(ProductError) as error:
            runner.collect(dict(id='job',deadline_at=now()+timedelta(minutes=5)),state)
        self.assertEqual(error.exception.code,'upstream_rate_limited')

class TrendsConditionCapabilityTests(unittest.TestCase):
    def test_explicit_hotlist_condition_is_advertised_and_accepted(self):
        from realtime.middle_ingest import task_spec,WhaleLinkClient
        from realtime.middle_collector import trends_conditions
        from realtime.middle_direct import collector_capabilities
        from unittest.mock import Mock
        config=replace(Config(),whale_source_platform='google_trends',whale_supported_task_types=('live_query',),whale_collector_api_key='test')
        task=dict(task_type='live_query',dataset_id=config.whale_dataset_id,source_platform='google_trends',required_capabilities=['identity','title','condition:other'],payload=GoogleLiveAcceptanceTests().payload(conditions=trends_conditions()))
        self.assertEqual(task_spec(task,config)['sources'],['google_trends'])
        self.assertIn('condition:other',collector_capabilities('google_trends'))
        client=WhaleLinkClient(config);client.post=Mock(return_value={});client.register()
        self.assertIn('condition:other',client.post.call_args.args[1]['declared_capabilities'])
