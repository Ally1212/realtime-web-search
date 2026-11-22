import unittest
from realtime.middle_collector import live_payload
from realtime.product import parameters

class ExpandedWebTests(unittest.TestCase):
    def test_default_web_delivers_up_to_contract_limit_and_searches_eleven_pages(self):
        p=parameters(dict(query='OpenAI'))
        self.assertEqual(p['depth'],100)
        payload=dict(query='OpenAI',resource_kind='content',live_query_id='lq_expanded',max_items=100)
        self.assertEqual(live_payload(payload)['max_pages'],11)

class ContinuePagingTests(unittest.TestCase):
    def test_failed_page_does_not_prevent_later_pages_in_expanded_mode(self):
        from unittest.mock import Mock
        from realtime.discovery import SearchDiscovery
        d=object.__new__(SearchDiscovery);d.should_continue=lambda:True
        d.google_web_max_pages=11;d.google_web_enabled=True;d.web_query_eligible=True
        d.page_batch_acquirer=None;d.page_result_recorder=None;d.novelty_counter=None
        d.continue_on_page_error=True
        d._discover_google_page=Mock(side_effect=[[],RuntimeError('test_page_failure')]+[[]]*9)
        _,errors=d._discover('OpenAI',11)
        self.assertEqual(d._discover_google_page.call_count,11)
        self.assertEqual(len(errors),1)


class ExpandedRetryBudgetTests(unittest.TestCase):
    def test_extra_attempt_is_scoped_to_private_eleven_page_queries(self):
        from unittest.mock import Mock,patch
        from realtime.serp_worker import run_serp_campaign
        from realtime.config import Config
        for private in (True,False):
            store,serp,discovery=Mock(),Mock(),Mock()
            store.campaign.return_value=dict(status='active',query='OpenAI',sources=['google_web'],serp_options=dict(customer_private=private,web_pages=11))
            store.whale_task_for_campaign.return_value=None
            discovery.proxy_provider_attempts=1;discovery.discover_many.return_value=([],[])
            with patch('realtime.serp_worker.make_discovery',return_value=discovery):run_serp_campaign('campaign',Config(),store,serp)
            self.assertEqual(discovery.proxy_provider_attempts,2 if private else 1)
            if private:self.assertIsNone(discovery.page_batch_acquirer)


class RecoveredPageCoverageTests(unittest.TestCase):
    def test_recovered_page_is_not_final_failure(self):
        from realtime.middle_live_runtime import collection_coverage
        rows=[dict(page=1,classification='error',http_status=403),dict(page=1,classification='results',http_status=200)]
        result=collection_coverage(rows,100,11,10)
        self.assertEqual(result['successful_pages'],[1]);self.assertEqual(result['failed_pages'],[])
        self.assertEqual(result['retry_recovered_pages'],[1]);self.assertFalse(result['partial_failure'])
        self.assertFalse(result['scope_complete']);self.assertEqual(result['missing_pages'],list(range(2,12)))
        self.assertTrue(result['failures'][0]['recovered'])

    def test_eleven_page_scope_requires_every_page_to_succeed(self):
        from realtime.middle_live_runtime import collection_coverage
        rows=[dict(page=page,classification='results',http_status=200) for page in range(1,12)]
        result=collection_coverage(rows,100,11,95)
        self.assertTrue(result['scope_complete']);self.assertEqual(result['missing_pages'],[])

    def test_attempted_failure_remains_missing_until_recovered(self):
        from realtime.middle_live_runtime import collection_coverage
        rows=[dict(page=page,classification='results',http_status=200) for page in range(1,12)]
        rows[7]=dict(page=8,classification='rate_limited',http_status=429)
        result=collection_coverage(rows,100,11,85)
        self.assertFalse(result['scope_complete']);self.assertEqual(result['failed_pages'],[8])
        self.assertEqual(result['missing_pages'],[8])

    def test_deadline_prevents_scope_completion_even_with_all_pages_saved(self):
        from realtime.middle_live_runtime import collection_coverage
        rows=[dict(page=page,classification='empty' if page==11 else 'results') for page in range(1,12)]
        result=collection_coverage(rows,100,11,90,deadline_exceeded=True)
        self.assertFalse(result['scope_complete']);self.assertEqual(result['missing_pages'],[])
        self.assertEqual(result['stop_reason'],'deadline_exceeded')
