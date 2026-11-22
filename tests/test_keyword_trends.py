import json,unittest
from unittest.mock import Mock
from realtime.keyword_trends import collect,TrendsError

class KeywordTrendsTests(unittest.TestCase):
    def test_real_keyword_and_time_window_reach_upstream_and_series_is_preserved(self):
        session=Mock();session.get.side_effect=[Mock(status_code=200,text=")]}'\n"+json.dumps(dict(widgets=[dict(id='TIMESERIES',token='test-token',request=dict(time='2026-01-01 2026-02-01'))]))),Mock(status_code=200,text=")]}'\n"+json.dumps(dict(default=dict(timelineData=[dict(time='1767225600',value=[42],isPartial=False)]))))]
        result=collect(session,query='OpenAI',region='US',time_range='today 3-m')
        request=json.loads(session.get.call_args_list[0].kwargs['params']['req'])
        self.assertEqual(request['comparisonItem'][0],dict(keyword='OpenAI',geo='US',time='today 3-m'))
        self.assertEqual(result['points'][0]['value'],42)
        self.assertEqual(result['measurement'],'relative_interest_index_0_100')
    def test_rate_limit_is_explicit(self):
        session=Mock();session.get.return_value=Mock(status_code=429)
        with self.assertRaises(TrendsError) as e:collect(session,query='OpenAI')
        self.assertEqual(e.exception.code,'trends_rate_limited')


class KeywordContractTests(unittest.TestCase):
    def test_keyword_default_survives_all_backend_boundaries(self):
        from dataclasses import replace
        from realtime.config import Config
        from realtime.product import parameters
        from realtime.middle_platform_client import search_request
        from realtime.middle_ingest import task_spec
        request=search_request(dict(query='OpenAI',source='google_trends'))
        self.assertEqual(request['max_items'],1)
        self.assertEqual(parameters(dict(query='OpenAI',source='google_trends'))['trends_mode'],'keyword')
        payload={k:v for k,v in request.items() if k not in ('source_platforms','dataset_id','deadline_seconds')}
        payload['live_query_id']='lq_keyword'
        c=replace(Config(),whale_source_platform='google_trends',whale_supported_task_types=('live_query',))
        spec=task_spec(dict(task_type='live_query',source_platform='google_trends',dataset_id=c.whale_dataset_id,payload=payload),c)
        self.assertEqual(spec['options']['trends_mode'],'keyword')
        self.assertEqual(spec['options']['trends_time_range'],'today 3-m')

class SavedKeywordSeriesTests(unittest.TestCase):
    def test_curve_survives_storage_projection_without_becoming_rss_hotlist(self):
        from unittest.mock import MagicMock,patch
        from datetime import datetime,timezone
        from uuid import uuid4
        from realtime.keyword_trends import collect_saved
        from realtime.customer_data import project
        discovery=Mock(timeout=25,language='en',proxy_pool=None);discovery._cache_key.return_value='keyword-cache'
        store=Mock();store.acquire_query_lease.return_value='lease'
        serp=Mock();serp.cached_feed.return_value=None;captured={};rid=str(uuid4())
        def record(cid,**event):captured.update(event);return rid
        serp.record.side_effect=record
        series=dict(query='OpenAI',region='US',time_range='today 3-m',measurement='relative_interest_index_0_100',point_count=2,points=[dict(timestamp='2026-11-01T00:00:00+00:00',value=30,is_partial=False),dict(timestamp='2026-11-02T00:00:00+00:00',value=42,is_partial=True)])
        session=MagicMock();session.__enter__.return_value=session;session.get.return_value.status_code=200
        with patch('realtime.keyword_trends.requests.Session',return_value=session),patch('realtime.keyword_trends.collect',return_value=series):
            _,errors=collect_saved(discovery,store,serp,'campaign','OpenAI','US','today 3-m')
        self.assertEqual(errors,[])
        row=dict(captured,request_id=rid,completed_at=datetime.now(timezone.utc))
        row.pop('results',None)
        data=project(row)
        self.assertEqual(data['topics'][0]['numeric_trend_series'],series)
        self.assertEqual(data['field_adaptation']['topics'][0]['numeric_series_availability'],'available')
        self.assertEqual(data['search_parameters']['q'],'OpenAI')
        self.assertEqual(len(data['occurrences']),1)
        self.assertEqual(data['occurrences'][0]['result_type'],'trend_reference')
        self.assertIsNone(data['occurrences'][0]['article_url'])
        from realtime.customer_export import full_rows
        points=[r for r in full_rows(data) if r['record_type']=='trend_point']
        self.assertEqual([r['trend_value'] for r in points],[30,42])
        self.assertTrue(points[-1]['trend_is_partial'])
        store.release_query_lease.assert_called_once_with('keyword-cache','lease')

class CachedFailureTests(unittest.TestCase):
    def test_cached_collection_failure_is_not_silent_empty_success(self):
        from realtime.keyword_trends import collect_saved
        discovery=Mock();discovery._cache_key.return_value='key'
        store,serp=Mock(),Mock();serp.cached_feed.return_value=dict(trending_searches=[],collection_error='trends_rate_limited')
        rows,errors=collect_saved(discovery,store,serp,'campaign','OpenAI','US','today 3-m')
        self.assertEqual(rows,[]);self.assertEqual(errors,['trends_rate_limited']);store.acquire_query_lease.assert_not_called()
