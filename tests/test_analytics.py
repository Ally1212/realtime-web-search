import os
import unittest
from datetime import datetime,timezone,timedelta

from realtime.product import ProductError,compare_snapshots
from realtime.analytics import filters,sync_projection,projection_status
from tests import test_product as product_tests


class AnalyticsMathTests(unittest.TestCase):
    def test_missing_ranks_reentry_and_content_changes(self):
        def snap(i,items):return dict(id=str(i),provider='test',completed_at=f'2026-10-{i:02d}T00:00:00+00:00',response={'organic_results':items})
        def item(rank,title='First',snippet='summary'):
            return dict(link='https://example.com/page?utm_source=x',position=rank,title=title,snippet=snippet)
        d=compare_snapshots([snap(4,[item(2,'Changed','new summary')]),snap(3,[item(None)]),snap(2,[]),snap(1,[item(5)])],['example.com'])
        p=d['pages'][0];m=d['domains'][0]
        self.assertEqual(d['trend']['unique_urls'],1)
        self.assertEqual(p['observations'],3);self.assertEqual(p['reentries'],1)
        self.assertEqual(p['mean_rank'],3.5);self.assertEqual(m['median_rank'],3.5)
        self.assertEqual(m['latest_delta'],None);self.assertEqual(m['period_delta'],3)
        self.assertEqual(p['title_changes'],1);self.assertEqual(p['snippet_changes'],1)
        self.assertEqual(d['changes'][0]['kind'],'moved')
        self.assertTrue(d['changes'][0]['title_changed'])
        self.assertEqual(d['quality']['rank_coverage'],0.6667)
        self.assertEqual(d['transitions'][-1]['comparable_ranks'],0)

    def test_title_only_change_and_duplicate_cache(self):
        a=dict(id='a',provider='x',completed_at='2026-10-27',response={'organic_results':[dict(link='https://x.com/',position=2,title='A')]})
        b=dict(id='b',provider='x',completed_at='2026-10-26',response={'organic_results':[dict(link='https://x.com/',position=2,title='B')]})
        d=compare_snapshots([a,a,b],[])
        self.assertEqual(d['samples'],2);self.assertEqual(d['changes'][0]['kind'],'content')
        self.assertEqual(d['trend']['title_change_events'],1)
        self.assertEqual(d['transitions'][0]['mean_rank_movement'],0)
        self.assertEqual(d['trend']['mean_overlap'],1)

    def test_filter_validation_and_calendar_days(self):
        for kwargs in (dict(days='365'),dict(source='maps'),dict(region='XX'),dict(language='fr')):
            with self.assertRaises(ProductError):filters(**kwargs)
        _,_,start,end=filters('7');self.assertEqual(start.hour,0);self.assertEqual((end.date()-start.date()).days,6)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AnalyticsDatabaseTests(unittest.TestCase):
    setUp=product_tests.ProductIntegrationTests.setUp
    tearDown=product_tests.ProductIntegrationTests.tearDown

    def record(self,source,query,items,region='US',language='en',status='results',hours=0):
        cid=self.console.create_task({'keywords':[query],'sources':[source],'start':False},self.config)['campaign_id']
        field={'google_web':'organic_results','google_news':'news_results','google_trends':'trending_searches'}[source]
        rid=self.console.record(cid,source=source,provider='openserp' if source=='google_web' else 'google_rss',query=query,page=1,language=language,
                response={field:items,'search_parameters':{'gl':region.lower(),'hl':language}},elapsed_seconds=.1,classification=status,http_status=200 if status=='results' else 503,error_code=None if status=='results' else 'test_error')
        if hours:
            with self.base.connect() as db:db.execute("UPDATE serp_requests SET completed_at=now()-interval '1 hour'*%s WHERE request_id=%s",(hours,rid))
        sync_projection(self.base)
        return cid,rid

    def test_exact_portfolio_source_semantics_and_private_exclusion(self):
        a=lambda slug,rank=None:dict(title=slug,link='https://example.com/'+slug,position=rank,snippet='text')
        self.record('google_web','keyword',[a('a',1),a('b',3)],hours=25)
        self.record('google_web','keyword',[a('a',2),a('c')])
        self.record('google_news','news-keyword',[dict(title='News',link='https://example.com/a',publisher='Media',date='today')],region='HK',language='zh')
        self.record('google_trends','ignored',[dict(keyword='Topic',approx_traffic='200+',articles=[a('a'),a('e')])])
        self.record('google_web','failed-keyword',[],status='error')
        private=self.store.submit(self.owner,{'query':'private'},'private',self.config)[0]
        with self.base.connect() as db:cid=db.execute('SELECT campaign_id FROM sf_jobs WHERE id=%s',(private,)).fetchone()['campaign_id']
        self.console.record(str(cid),source='google_web',provider='openserp',query='private',page=1,language='en',response={'organic_results':[a('secret',1)]},elapsed_seconds=.1,classification='results',http_status=200)
        self.assertEqual(sync_projection(self.base),0)  # private responses stay out of the projection
        d=self.store.analytics_overview(days='7');s=d['summary']
        self.assertEqual((s['requests'],s['successful'],s['failed']),(5,4,1))
        self.assertEqual((s['keywords'],s['web_repeated_keywords']),(3,1))
        self.assertEqual((s['link_records'],s['unique_urls'],s['repeated_link_records']),(7,4,3))
        self.assertEqual(len(d['daily']),7);self.assertEqual(sum(x['requests'] for x in d['daily']),5)
        self.assertEqual(sum(x['unique_urls'] for x in d['sources']),6)
        self.assertNotIn('private',[x['query'] for x in d['keywords']])
        self.assertEqual(next(q for q in d['quality'] if q['source']=='google_web')['rank'],3)
        self.assertEqual(d['topics'][0]['keyword'],'Topic')
        news=self.store.analytics_overview(days='7',source='google_news',region='HK',language='zh')
        self.assertEqual(news['summary']['requests'],1)
        self.assertEqual(self.store.analytics_overview(days='7',keyword_q='no match')['keyword_total'],0)
        with self.assertRaises(ProductError):self.store.analytics_overview(days='7',offset='-1')

    def test_keyword_scope_selection_window_and_comparisons(self):
        a=lambda rank:dict(title='Page',link='https://example.com/a',position=rank)
        self.record('google_web','repeated',[a(5)],hours=26)
        self.record('google_web','repeated',[a(2)],hours=1)
        self.record('google_web','repeated',[a(1)],region='GB')
        self.record('google_web','latest-only',[a(8)])
        self.record('google_web','repeated',[a(10)],hours=24*40)
        d=self.store.history_analysis(days='7',domain='example.com',competitors='competitor.com')
        self.assertEqual(d['query'],'repeated');self.assertEqual(d['samples'],2)
        self.assertEqual(d['scope']['region'],'US');self.assertEqual(d['excluded_incompatible_samples'],1)
        self.assertEqual(d['domains'][0]['mean_rank'],3.5)
        self.assertEqual(d['domains'][0]['period_delta'],3)
        other=next(g['id'] for g in d['scope_choices'] if g['region']=='GB')
        self.assertEqual(self.store.history_analysis(query='repeated',days='7',scope=other)['samples'],1)
        self.assertEqual(self.store.history_analysis(query='repeated',days='90',region='US')['samples'],3)
        with self.assertRaises(ProductError):self.store.history_analysis(scope='bad')
        empty=self.store.analytics_overview(days='1',source='google_trends')
        self.assertEqual(empty['summary']['requests'],0);self.assertEqual(empty['summary']['repeat_rate'],None)

    def test_projection_is_idempotent_tracks_pending_and_cascades(self):
        _,rid=self.record('google_web','initial',[dict(title='A',link='https://example.com/a',position=1)])
        with self.base.connect() as db:
            original=db.execute('SELECT response FROM serp_requests WHERE request_id=%s',(rid,)).fetchone()['response']
        self.assertEqual(sync_projection(self.base),0)
        first=self.store.analytics_overview(days='7')
        cid=self.console.create_task({'keywords':['pending'],'sources':['google_web'],'start':False},self.config)['campaign_id']
        self.console.record(cid,source='google_web',provider='openserp',query='pending',page=1,language='en',response={'search_parameters':[], 'organic_results':[dict(title='B',link='https://example.com/b',position=2),dict(link='javascript:alert(1)')]},elapsed_seconds=.1,classification='results',http_status=200)
        with self.base.connect() as db:self.assertEqual(projection_status(db)['pending'],1)
        self.assertEqual(sync_projection(self.base),1)
        d=self.store.analytics_overview(days='7')
        self.assertEqual(first['summary']['requests'],1)
        self.assertEqual(d['summary']['requests'],2)
        self.assertEqual(d['summary']['unique_urls'],2)
        self.assertEqual(d['projection']['pending'],0)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT response FROM serp_requests WHERE request_id=%s',(rid,)).fetchone()['response'],original)
            db.execute('DELETE FROM sf_analytics_requests WHERE request_id=%s',(rid,))
            self.assertEqual(db.execute('SELECT count(*) n FROM serp_requests WHERE request_id=%s',(rid,)).fetchone()['n'],1)
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_analytics_items WHERE request_id=%s',(rid,)).fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_analytics_requests WHERE request_id=%s',(rid,)).fetchone()['n'],0)
        self.assertEqual(sync_projection(self.base),1)
