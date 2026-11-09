"""Real PostgreSQL boundary for the read-only legacy preview."""
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from realtime.google_domain import DomainError
from realtime.google_legacy import read_public_requests, read_public_request, read_source_analysis, read_legacy_quality


class LegacyPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_dsn = os.environ["TEST_DATABASE_URL"]
        cls.schema = "google_legacy_test_" + uuid4().hex
        with psycopg.connect(cls.base_dsn, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.dsn = make_conninfo(cls.base_dsn, options=f"-c search_path={cls.schema}")
        with psycopg.connect(cls.dsn) as db:
            db.execute("""CREATE TABLE serp_requests (
                request_id uuid PRIMARY KEY, campaign_id uuid NOT NULL,
                source text NOT NULL, query text NOT NULL, page integer NOT NULL,
                classification text NOT NULL, completed_at timestamptz NOT NULL,
                response jsonb NOT NULL)""")
            db.execute("""CREATE TABLE serp_task_options (
                campaign_id uuid PRIMARY KEY, options jsonb NOT NULL)""")
            db.execute("""CREATE TABLE sf_analytics_requests (
                request_id uuid PRIMARY KEY, campaign_id uuid, query text NOT NULL, source text NOT NULL,
                provider text NOT NULL DEFAULT 'test', page integer NOT NULL, completed_at timestamptz NOT NULL,
                classification text NOT NULL, error_code text, region text, language text, device text,
                projected_at timestamptz NOT NULL DEFAULT now(), response jsonb NOT NULL)""")
            db.execute("""CREATE TABLE sf_analytics_items (
                request_id uuid NOT NULL, ordinal integer NOT NULL, child_ordinal integer NOT NULL DEFAULT 0,
                title text,snippet text,url text,host text,rank integer,publisher text,published_at text,traffic_label text,
                PRIMARY KEY(request_id,ordinal,child_ordinal))""")

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.base_dsn, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        with psycopg.connect(self.dsn) as db:
            db.execute("TRUNCATE serp_task_options,serp_requests,sf_analytics_items,sf_analytics_requests")

    def insert(self, source, response, *, private=False, classification="results"):
        campaign_id, request_id = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as db:
            db.execute("""INSERT INTO serp_requests
                (request_id,campaign_id,source,query,page,classification,completed_at,response)
                VALUES (%s,%s,%s,'test query',1,%s,now(),%s)""",
                (request_id, campaign_id, source, classification, Jsonb(response)))
            if private:
                db.execute("INSERT INTO serp_task_options VALUES (%s,%s)",
                           (campaign_id, Jsonb({"customer_private": True})))
            else:
                db.execute("""INSERT INTO sf_analytics_requests
                    (request_id,campaign_id,query,source,page,completed_at,classification,response)
                    VALUES (%s,%s,'test query',%s,1,now(),%s,%s)""",
                    (request_id,campaign_id,source,classification,Jsonb(response)))
                key = {'google_web':'organic_results','google_news':'news_results',
                       'google_trends':'trending_searches'}[source]
                for ordinal, item in enumerate(response.get(key, []), 1):
                    if source == 'google_trends':
                        db.execute("INSERT INTO sf_analytics_items(request_id,ordinal,title,traffic_label) VALUES(%s,%s,%s,%s)",
                                   (request_id,ordinal,item.get('keyword'),item.get('approx_traffic')))
                        for child, article in enumerate(item.get('articles', []), 1):
                            db.execute("INSERT INTO sf_analytics_items(request_id,ordinal,child_ordinal,title,url) VALUES(%s,%s,%s,%s,%s)",
                                       (request_id,ordinal,child,article.get('title'),article.get('link')))
                    else:
                        link = item.get('link')
                        host = link.split('/')[2] if isinstance(link,str) and link.startswith('https://') else None
                        db.execute("""INSERT INTO sf_analytics_items
                            (request_id,ordinal,title,snippet,url,host,rank,publisher,published_at)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (request_id,ordinal,item.get('title'),item.get('snippet'),link,host,
                             item.get('position'),item.get('publisher'),item.get('date')))
        return str(request_id)

    def test_public_web_and_private_exclusion(self):
        public = self.insert("google_web", {
            "schema_version": "google-serp-v1",
            "search_parameters": {"gl": "us", "hl": "en", "device": None},
            "organic_results": [
                {"link": "https://example.com/one", "title": "One", "position": 1,
                 "rank_absolute": 3},
                {"link": "https://user:password@example.com/secret", "position": None}],
        })
        private = self.insert("google_web", {
            "organic_results": [{"link": "https://private.example/", "title": "SECRET"}]},
            private=True)
        data = read_public_requests(self.dsn, {"source": "google_web", "limit": "20"})
        ids = [r["request_id"] for r in data["requests"]]
        self.assertIn(public, ids)
        self.assertNotIn(private, ids)
        row = next(r for r in data["requests"] if r["request_id"] == public)
        self.assertEqual(row["query_conditions"]["location_precision"], "unknown")
        self.assertIsNone(row["query_conditions"]["device"])
        self.assertEqual(row["cache_status"], "unknown")
        self.assertEqual(row["items"][0]["organic_position"], 1)
        self.assertIsNone(row["items"][1]["url"])
        self.assertEqual(data["whale_readback"], "not_connected")

    def test_news_and_trends_keep_distinct_meanings(self):
        self.insert("google_news", {"news_results": [{
            "link": "https://news.google.com/rss/articles/x", "title": "A",
            "feed_position": 2, "link_status": "google_news_redirect"}]})
        self.insert("google_trends", {"trending_searches": [{
            "keyword": "Topic", "approx_traffic": "20,000+",
            "articles": [{"link": "https://example.com/report"}]}]})
        news = read_public_requests(self.dsn, {"source": "google_news"})["requests"][0]["items"][0]
        trend = read_public_requests(self.dsn, {"source": "google_trends"})["requests"][0]["items"][0]
        self.assertEqual(news["feed_position"], 2)
        self.assertIsNone(news["organic_position"])
        self.assertIsNone(news["publisher_url"])
        self.assertEqual(trend["approx_traffic_label"], "20,000+")
        self.assertIsNone(trend["numeric_trend_series"])

    def test_news_pubdate_requires_matching_raw_rss_value(self):
        saved = self.insert("google_news", {"news_results": [
            {"title":"Verified", "date":"2026-11-05T00:00:00+00:00",
             "date_raw":"Thu, 05 Nov 2026 00:00:00 GMT"},
            {"title":"Mismatched", "date":"2026-11-05T00:00:00+00:00",
             "date_raw":"Thu, 05 Nov 2026 01:00:00 GMT"},
            {"title":"No raw date", "date":"2026-11-05T00:00:00+00:00"}]})
        rows=read_public_request(self.dsn,saved)['request']['items']
        self.assertEqual(rows[0]['published_at'],'2026-11-05T00:00:00Z')
        self.assertIsNone(rows[1]['published_at'])
        self.assertIsNone(rows[2]['published_at'])

    def test_filters_pagination_and_detail_exclude_private(self):
        first = self.insert("google_web", {"search_parameters":{"gl":"us","hl":"en"},
            "organic_results":[{"link":"https://example.com/one","position":2}]})
        second = self.insert("google_web", {"search_parameters":{"gl":"us","hl":"en"},
            "organic_results":[{"link":"https://example.com/two","position":3}]})
        private = self.insert("google_web", {"organic_results":[{"link":"https://example.com/secret"}]}, private=True)
        params={"source":"google_web","query":"test","domain":"example.com","country":"us","limit":"1"}
        one=read_public_requests(self.dsn,params)
        self.assertTrue(one["has_more"])
        two=read_public_requests(self.dsn,{**params,"cursor":one["next_cursor"]})
        self.assertIn(one["requests"][0]["request_id"],{first,second})
        self.assertIn(two["requests"][0]["request_id"],{first,second})
        self.assertNotEqual(one["requests"][0]["request_id"],two["requests"][0]["request_id"])
        self.assertFalse(two["has_more"])
        self.assertEqual(read_public_request(self.dsn,first)["request"]["request_id"],first)
        with self.assertRaises(DomainError): read_public_request(self.dsn,private)

    def test_source_analysis_keeps_news_and_trends_separate(self):
        self.insert("google_news", {"news_results": [
            {"link":"https://news.google.com/rss/articles/x","link_status":"google_news_redirect","title":"A","publisher":"Wire"},
            {"link":"https://example.com/a","link_status":"publisher_url","title":"B","publisher":"Wire", "date":"2026-11-05T00:00:00+00:00", "date_raw":"Thu, 05 Nov 2026 00:00:00 GMT", "collection_method":"google_news_rss"}]})
        self.insert("google_trends", {"trending_searches":[{"keyword":"Topic","approx_traffic":"20,000+","articles":[{"link":"https://example.com/report"}]}]})
        news=read_source_analysis(self.dsn,"google_news",{})
        trends=read_source_analysis(self.dsn,"google_trends",{})
        self.assertEqual(news["totals"]["records"],2)
        self.assertEqual(news["totals"]["unresolved_links"],1)
        self.assertEqual(news["totals"]["published_at_provided"],1)
        self.assertEqual(trends["totals"]["topics"],1)
        self.assertEqual(trends["totals"]["articles"],1)
        self.assertEqual(trends["breakdown"]["traffic_labels"][0]["label"],"20,000+")

    def test_quality_separates_raw_and_projection_and_excludes_private(self):
        self.insert("google_web", {"organic_results":[{"link":"https://example.com/a","title":"A","position":1}]})
        self.insert("google_news", {"news_results":[{"link":"https://news.google.com/rss/articles/x","title":"B"}]})
        self.insert("google_trends", {"trending_searches":[{"keyword":"Topic","articles":[]}]})
        self.insert("google_web", {"organic_results":[{"link":"https://secret.example/a"}]}, private=True)
        quality=read_legacy_quality(self.dsn)
        self.assertEqual(quality['sample_size'],3)
        by_source={row['source']:row for row in quality['sources']}
        self.assertEqual(by_source['google_web']['raw_requests'],1)
        self.assertEqual(by_source['google_web']['field_quality']['rank']['provided'],1)
        self.assertEqual(by_source['google_news']['unresolved_news_links'],1)
        self.assertEqual(quality['numeric_trends_series'],'not_covered')

    def test_rejects_unbounded_or_unknown_filters(self):
        for parameters in ({"limit": "51"}, {"source": "gsc"}, {"unexpected": "secret"}):
            with self.subTest(parameters=parameters), self.assertRaises(DomainError):
                read_public_requests(self.dsn, parameters)


if __name__ == "__main__":
    unittest.main()
