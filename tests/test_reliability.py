import csv
import io
import json
import os
import subprocess
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from realtime.analytics import overview_summary
from realtime.product import ProductError
from realtime.product_http import export, handle
from realtime.serp_page import PAGE
from realtime.reliability_ui import RESULT_SINGLE_FLIGHT_JS
from tests import test_analytics as analytics_tests


class ExportReliabilityTests(unittest.TestCase):
    def job(self, source, data):
        return dict(parameters=dict(query='test', source=source),
                    response=dict(data=data, fetched_at='2026-11-02T00:00:00Z'))

    def csv(self, job):
        handler = Mock()
        export(handler, job, 'csv')
        raw, content_type = handler.send_body.call_args.args
        self.assertIn('text/csv', content_type)
        return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))

    def test_trends_keeps_topic_and_every_related_article(self):
        topics = [dict(keyword='Topic', articles=[
            dict(title='=unsafe', link='https://example.com/a', snippet='Report'),
            dict(title='Other', link='https://example.com/b'),
        ]), dict(keyword='No articles', articles=[])]
        rows = self.csv(self.job('google_trends', dict(trending_searches=topics)))
        self.assertEqual(len(rows), 4)
        self.assertEqual(list(rows[0]), ['query', 'source', 'fetched_at', 'title', 'link', 'snippet', 'position'])
        self.assertEqual([r['link'] for r in rows if r['link']],
                         ['https://example.com/a', 'https://example.com/b'])
        self.assertEqual(rows[1]['title'], "'=unsafe")
        self.assertEqual(rows[0]['title'], 'Topic')
        self.assertTrue(all(r['position'] == '' for r in rows))

    def test_web_and_news_keep_existing_columns_and_values(self):
        for source, field in [('google_web', 'organic_results'), ('google_news', 'news_results')]:
            rows = self.csv(self.job(source, {field: [dict(title='Saved', link='https://example.com/', position=2)]}))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['position'], '2')
            self.assertEqual(rows[0]['title'], 'Saved')
        handler = Mock()
        job = self.job('google_trends', dict(trending_searches=[dict(keyword='Topic', articles=[dict(link='https://example.com')])]))
        export(handler, job, 'jsonl')
        self.assertEqual(json.loads(handler.send_body.call_args.args[0]), job)

    def test_invalid_export_format_is_rejected_before_writing(self):
        handler = Mock()
        with self.assertRaises(ProductError):
            export(handler, self.job('google_web', {}), 'html')
        handler.send_body.assert_not_called()


class ReliabilityUITests(unittest.TestCase):
    def test_slow_read_refresh_dedup_changed_filters_and_error_recovery(self):
        program = r'''
import assert from 'node:assert/strict';
let resultMode='results',resultOffset=0,query='A',calls=[];
const resultParams=()=>new URLSearchParams({q:query});
let loadResults=()=>new Promise((resolve,reject)=>calls.push({resolve,reject}));
''' + RESULT_SINGLE_FLIGHT_JS + r'''
const first=loadResults(); assert.equal(loadResults(),first); assert.equal(calls.length,1);
query='B';const second=loadResults();assert.equal(calls.length,2);
calls[0].resolve();await first;await Promise.resolve();assert.equal(loadResults(),second);
query='A';const third=loadResults();assert.notEqual(third,first);assert.equal(calls.length,3);
calls[1].resolve();calls[2].reject(Error('read failed'));
await Promise.allSettled([second,third]);await Promise.resolve();
const retry=loadResults();assert.equal(calls.length,4);calls[3].resolve();await retry;
'''
        result = subprocess.run(['node', '--input-type=module'], input=program, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_composed_page_preserves_existing_ui_and_adds_recovery(self):
        page = PAGE
        for identifier in ('taskReadStatus', 'resultReadStatus', 'archiveReadStatus'):
            self.assertEqual(page.count('id="' + identifier + '"'), 1)
        self.assertIn('/api/product/analytics/summary?days=90&source=all', page)
        self.assertNotIn("api('/api/product/analytics/overview?days=90&source=all')", page)
        self.assertIn('data-archive-retry', page)
        self.assertIn('appliedResultFilters', page)
        self.assertIn('操作可能已保存', page)
        self.assertIn('不代表最新条件', page)
        self.assertIn('导出返回了非预期文件，未保存', page)
        self.assertIn("clearReadNotice(lastResultReadError)", page)
        self.assertEqual(page.count('showView();setInterval(refreshView,10000);'), 1)
        self.assertIn('退出访问', page)

    def test_google_research_workspace_is_composed_readonly(self):
        page = PAGE
        for identifier in (
                'researchView', 'grStatusSummary', 'grCompareResult',
                'grReports', 'grEvidenceResult', 'grTabArtifacts'):
            self.assertEqual(page.count('id="' + identifier + '"'), 1)
        self.assertIn("productViews.push('research')", page)
        self.assertIn('/v1/google/reports?limit=20', page)
        self.assertIn('data-gr-evidence', page)
        self.assertIn('仅显示前 25 条', page)
        self.assertNotIn('content_fetch_enabled=true', page)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class SummaryReliabilityTests(unittest.TestCase):
    setUp = analytics_tests.AnalyticsDatabaseTests.setUp
    tearDown = analytics_tests.AnalyticsDatabaseTests.tearDown
    record = analytics_tests.AnalyticsDatabaseTests.record

    def test_summary_matches_full_report_and_excludes_private_jobs(self):
        self.record('google_web', 'keyword', [
            dict(title='Page', link='https://example.com/a?utm_source=one', position=1)])
        self.record('google_news', 'news', [
            dict(title='News', link='https://example.com/a')])
        self.record('google_trends', 'ignored', [
            dict(keyword='Topic', articles=[dict(title='Report', link='https://example.com/b')])])
        self.record('google_web', 'failed', [], status='error')
        jid = self.store.submit(self.owner, {'query': 'private'}, 'private-summary', self.config)[0]
        with self.base.connect() as db:
            cid = db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s', (jid,)).fetchone()['campaign_id']
        self.console.record(str(cid), source='google_web', provider='openserp', query='private',
                            page=1, language='en', response={'organic_results': [
                                dict(title='Secret', link='https://secret.example.com')]},
                            elapsed_seconds=.1, classification='results', http_status=200)
        summary = overview_summary(self.base, days='7')
        full = self.store.analytics_overview(days='7')
        for key in ('requests', 'keywords', 'unique_urls', 'last_request'):
            actual, expected = summary['summary'][key], full['summary'][key]
            if key == 'last_request' and actual is not None:
                actual = datetime.fromisoformat(str(actual).replace('Z', '+00:00'))
                expected = datetime.fromisoformat(str(expected).replace('Z', '+00:00'))
            self.assertEqual(actual, expected)
        self.assertEqual(summary['summary']['requests'], 4)
        self.assertEqual(summary['summary']['unique_urls'], 2)
        self.assertEqual(summary['projection']['pending'], 0)
        self.assertEqual(summary['cache_seconds'], 60)
        self.assertIs(overview_summary(self.base, days='7'), summary)

    def test_empty_scope_invalid_filters_and_projection_lag_are_explicit(self):
        self.record('google_web', 'web', [dict(title='A', link='https://example.com/a')])
        empty = overview_summary(self.base, days='1', source='google_news')
        self.assertEqual(empty['summary']['requests'], 0)
        self.assertEqual(empty['summary']['unique_urls'], 0)
        self.assertIsNone(empty['summary']['last_request'])
        with self.base.connect() as db:
            db.execute('DELETE FROM sf_analytics_requests')
        lagged = overview_summary(self.base, days='7')
        self.assertEqual(lagged['projection']['pending'], 1)
        self.assertEqual(lagged['summary']['requests'], 0)
        for filters in (dict(days='365'), dict(source='maps'), dict(region='XX'), dict(language='fr')):
            with self.assertRaises(ProductError):
                overview_summary(self.base, **filters)

    def test_summary_route_is_readonly_and_does_not_need_operator_key(self):
        handler = Mock()
        handler.path = '/api/product/analytics/summary?days=7&source=google_web'
        handler.config = self.config
        handler.headers = {}
        handler.client_address = ('127.0.0.1', 12345)
        with patch.dict(os.environ, {'SEARCHFLOW_PRODUCT_ENABLED': 'true'}):
            self.assertTrue(handle(handler, 'GET'))
        result = handler.send_json.call_args.args[0]
        self.assertEqual(result['summary']['requests'], 0)
        self.assertEqual(result['filters']['source'], 'google_web')
