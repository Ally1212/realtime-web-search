import copy
from contextlib import contextmanager
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from realtime.config import Config
from realtime.discovery import SearchDiscovery
from realtime.google_feeds import parse_feed, collect
from realtime.serp_fields import result_items
from realtime.serp_worker import run_serp_campaign
from tests import test_serp_collection as fixtures


NEWS = b'''<rss><channel><title>Google News</title><item><title>Research update</title>
<link>https://news.google.com/rss/articles/opaque-token</link><description>&lt;b&gt;New discovery&lt;/b&gt;</description>
<pubDate>Sat, 10 Oct 2026 01:00:00 GMT</pubDate><source url="https://publisher.example">Publisher</source></item></channel></rss>'''
TRENDS = b'''<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><title>Trending Now</title>
<item><title>solar eclipse</title><ht:approx_traffic>20,000+</ht:approx_traffic><pubDate>Sat, 10 Oct 2026 01:00:00 GMT</pubDate>
<ht:picture>https://images.example/eclipse.jpg</ht:picture><ht:picture_source>Science</ht:picture_source>
<ht:news_item><ht:news_item_title>Watch the eclipse</ht:news_item_title><ht:news_item_url>https://publisher.example/eclipse</ht:news_item_url><ht:news_item_source>Science</ht:news_item_source></ht:news_item></item>
<item><title>topic without articles</title></item></channel></rss>'''


class GoogleFeedTests(unittest.TestCase):
    def test_news_preserves_fields_and_unresolved_links_without_fetching_publisher(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError('no publisher DNS')),patch('requests.Session.get',side_effect=AssertionError('no publisher HTTP')):
            d=parse_feed(NEWS,'google_news','research','US:en')
        row=d['news_results'][0]
        self.assertEqual(row['snippet'],'New discovery')
        self.assertEqual(row['source'],'Publisher')
        self.assertEqual(row['date'],'2026-10-10T01:00:00+00:00')
        self.assertEqual(row['link_status'],'google_news_redirect')
        self.assertIsNone(row['publisher_link'])
        self.assertIsNone(row['position'])
        self.assertEqual(row['feed_position'],1)

    def test_trends_preserves_topics_even_without_article_links(self):
        d=parse_feed(TRENDS,'google_trends','trending-now','US')
        self.assertEqual(len(d['trending_searches']),2)
        self.assertEqual(d['trending_searches'][0]['approx_traffic'],'20,000+')
        self.assertEqual(len(result_items(d)),1)
        self.assertEqual(result_items(d)[0]['title'],'Watch the eclipse')
        self.assertIsNone(d['search_parameters']['q'])
        self.assertEqual(d['capabilities']['interest_over_time'],'not_implemented')

    def test_html_and_invalid_feeds_are_not_successful_empty_results(self):
        for content in (b'<html>consent</html>',b'<rss></rss>',b'broken xml'):
            with self.assertRaises(ValueError if content!=b'broken xml' else Exception):
                parse_feed(content,'google_news','q','US:en')

    def discovery(self, content=NEWS, status=200):
        d=SearchDiscovery(proxy_profile='direct')
        r=Mock(status_code=status)
        r.iter_content.return_value=[content]
        r.headers={}
        d._external_get=Mock(return_value=r)
        return d

    def test_feed_write_failure_does_not_cache_and_releases_lease(self):
        d=self.discovery();store=Mock();serp=Mock()
        serp.cached_feed.return_value=None;serp.record.side_effect=RuntimeError('database failure')
        with self.assertRaises(RuntimeError):
            collect(d,store,serp,str(uuid4()),'google_news','q','US:en',3600)
        serp.cache_feed.assert_not_called()
        store.release_query_lease.assert_called_once()
        self.assertFalse(d._external_get.call_args.kwargs['allow_redirects'])

    def test_rate_limit_records_failure_not_empty_success(self):
        d=self.discovery(status=429);store=Mock();serp=Mock();serp.cached_feed.return_value=None
        rows,errors=collect(d,store,serp,str(uuid4()),'google_news','q','US:en',3600)
        self.assertEqual(rows,[]);self.assertTrue(errors)
        self.assertEqual(serp.record.call_args.kwargs['classification'],'rate_limited')
        self.assertEqual(serp.cache_feed.call_args.args[2],120)

    def test_source_selection_does_not_silently_run_web_or_scrapy(self):
        store=Mock();cid=str(uuid4());store.campaign.return_value=dict(id=cid,query='q',aliases=[],status='active',proxy_profile='direct',sources=['google_news','google_trends'])
        store.whale_task_for_campaign.return_value=None;d=Mock()
        with patch('realtime.serp_worker.make_discovery',return_value=d),patch('realtime.google_feeds.collect_sources',return_value=([],[])) as feeds,patch('subprocess.Popen',side_effect=AssertionError('no body')):
            run_serp_campaign(cid,Config(),store,Mock())
        d.discover_many.assert_not_called();store.reconcile_google_queries.assert_not_called()
        self.assertEqual(feeds.call_args.args[5],['google_news','google_trends'])


class GoogleFeedStoreTests(fixtures.SerpStoreIntegrationTests):
    def feed_event(self,source='google_news',content=NEWS):
        e=self.event();e.update(source=source,provider='google_rss',response=parse_feed(content,source,'q','US:en'),query='q')
        return e

    def test_cross_source_union_and_source_contributions(self):
        news=self.feed_event()
        news['response']['news_results'][0]['link']='https://example.com/article'
        self.serp.record(self.cid,**self.event())
        self.serp.record(self.cid,**news)
        self.serp.record(self.cid,**self.feed_event('google_trends',TRENDS))
        s=self.serp.stats()
        self.assertEqual(s['links']['total_new'],2)
        self.assertEqual(sum(x['first_discovery_contribution'] for x in s['sources'].values()),2)
        self.assertEqual(s['sources']['google_news']['unique_new_urls'],1)
        self.assertEqual(s['sources']['google_news']['first_discovery_contribution'],0)
        self.assertEqual(len(self.serp.recent(source='google_news')),1)

    def test_trends_shared_cache_preserves_response_and_does_not_count_another_request(self):
        e=self.feed_event('google_trends',TRENDS);request_id=self.serp.record(self.cid,**e)
        key='a'*64;self.serp.cache_feed(key,request_id,900)
        other=self.base.create_campaign('another task',[],1000000,'direct',['google_trends'])
        response=self.serp.cached_feed(key,other)
        self.assertEqual(len(response['trending_searches']),2)
        self.assertEqual(len(self.serp.recent(other)),1)
        self.assertEqual(self.serp.stats()['requests']['attempts'],1)
        self.assertEqual(self.serp.record(self.cid,**e),request_id)
        changed=copy.deepcopy(e);changed['response']['trending_searches'][0]['approx_traffic']='50,000+'
        with self.assertRaises(ValueError):self.serp.record(self.cid,**changed)

    def test_schema_upgrade_retains_original_web_times_and_rows(self):
        self.serp.record(self.cid,**self.event())
        with self.base.connect() as db:
            db.execute("DELETE FROM serp_schema_versions WHERE name='sources-v1'")
            db.execute('DELETE FROM serp_campaign_requests');db.execute('DELETE FROM serp_url_sources')
        self.serp.initialize()
        self.assertEqual(len(self.serp.recent(self.cid)),1)
        self.assertEqual(self.serp.stats()['sources']['google_web']['unique_new_urls'],1)

    def test_shared_feed_lease_allows_only_one_network_fetch(self):
        d=GoogleFeedTests().discovery();cid=str(self.cid)
        collect(d,self.base,self.serp,cid,'google_news','q','US:en',3600)
        collect(d,self.base,self.serp,cid,'google_news','q','US:en',3600)
        d._external_get.assert_called_once()
        self.assertEqual(self.serp.stats()['requests']['attempts'],1)

    def test_feed_insert_failure_rolls_back_nested_article_urls(self):
        with self.base.connect() as db:
            db.execute("CREATE FUNCTION reject_feed() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$")
            db.execute('CREATE TRIGGER reject_feed BEFORE INSERT ON serp_urls FOR EACH ROW EXECUTE FUNCTION reject_feed()')
        with self.assertRaises(Exception):self.serp.record(self.cid,**self.feed_event('google_trends',TRENDS))
        self.assertEqual(self.serp.recent(),[])
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM serp_url_sources').fetchone()['n'],0)

    def test_statistics_stay_consistent_when_another_worker_commits_mid_read(self):
        self.serp.record(self.cid,**self.event())
        from realtime.serp_store import SerpStore
        writer=SerpStore(self.dsn)
        original=self.serp.store.connect
        inserted=[]
        class Connection:
            def __init__(self,db):self.db=db
            def execute(inner,query,*args):
                result=inner.db.execute(query,*args)
                if query.startswith('SELECT count(*) total_new') and not inserted:
                    inserted.append(writer.record(self.cid,**self.event(url='https://example.com/concurrent')))
                return result
        @contextmanager
        def connection():
            with original() as db:yield Connection(db)
        with patch.object(self.serp.store,'connect',side_effect=connection):
            stats=self.serp.stats()
        self.assertEqual(stats['links']['total_new'],1)
        self.assertEqual(sum(s['first_discovery_contribution'] for s in stats['sources'].values()),1)
        self.assertEqual(self.serp.stats()['links']['total_new'],2)
