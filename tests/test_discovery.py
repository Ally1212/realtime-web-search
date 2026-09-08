import unittest
from unittest.mock import Mock, patch

from realtime.discovery import GoogleBlocked, SearchDiscovery, SearchResult


class DiscoveryTests(unittest.TestCase):
    def test_parses_google_html_results(self):
        content = (
            b'<a href="/url?q=https%3A%2F%2Fexample.com%2Fai&amp;sa=U">'
            b'<h3>AI report</h3></a><a href="https://www.google.com/preferences">'
            b'<h3>Preferences</h3></a>'
        )

        results = SearchDiscovery._parse_google_html(content)

        self.assertEqual([row.url for row in results], ["https://example.com/ai"])
        self.assertEqual(results[0].engines, ("google_web",))

    def test_chinese_google_locale(self):
        response = Mock(content=b"")
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        SearchDiscovery("http://search", session=session, language="zh")._discover_google_page(
            "人工智能", 1
        )
        self.assertEqual(session.get.call_args.kwargs["params"]["hl"], "zh-CN")
        self.assertIn("zh-CN", session.get.call_args.kwargs["headers"]["Accept-Language"])
    def test_deduplicates_results(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"url": "https://example.com/a", "title": "A", "engines": ["google"]},
                {"url": "https://example.com/a", "title": "A2", "engines": ["google"]},
            ],
            "unresponsive_engines": [],
        }
        session = Mock()
        session.get.return_value = response
        results, errors = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            searxng_enabled=True,
        ).discover("test", 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [])

    def test_discovers_configured_rss_and_encodes_query(self):
        response = Mock()
        response.headers = {}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = [
            b"<rss><channel><item><title>AI Policy</title>"
            b"<link>https://example.com/ai</link></item></channel></rss>"
        ]
        session = Mock()
        session.get.return_value = response
        discovery = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            feeds=(("policy-feed", "https://feeds.example/search?q={query}"),),
        )

        results, errors = discovery.discover_feeds(("Singapore AI",))

        self.assertEqual(errors, [])
        self.assertEqual(results[0].url, "https://example.com/ai")
        self.assertEqual(results[0].engines, ("policy-feed",))
        self.assertEqual(
            session.get.call_args.args[0],
            "https://feeds.example/search?q=Singapore%20AI",
        )

    def test_google_is_the_only_search_engine_requested(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [], "unresponsive_engines": []}
        session = Mock()
        session.get.return_value = response

        SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            searxng_enabled=True,
        ).discover("test", 1)

        self.assertEqual(session.get.call_args.kwargs["params"]["engines"], "google")

    def test_ignores_non_google_results_and_errors(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"url": "https://google.example/a", "title": "A", "engines": ["google"]},
                {"url": "https://ddg.example/b", "title": "B", "engines": ["duckduckgo"]},
            ],
            "unresponsive_engines": [
                ["duckduckgo", "captcha"],
                ["google", "timeout"],
            ],
        }
        session = Mock()
        session.get.return_value = response

        results, errors = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            searxng_enabled=True,
        ).discover("test", 1)

        self.assertEqual([result.url for result in results], ["https://google.example/a"])
        self.assertEqual(errors, ["google: timeout"])

    def test_parses_atom_alternate_link(self):
        content = (
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Policy</title>'
            b'<link rel="alternate" href="https://example.com/atom"/></entry></feed>'
        )
        results = SearchDiscovery._parse_feed(content, "atom-feed")
        self.assertEqual(results[0].url, "https://example.com/atom")
        self.assertEqual(results[0].title, "Policy")

    def test_feed_failure_is_isolated(self):
        good = Mock()
        good.headers = {}
        good.raise_for_status.return_value = None
        good.iter_content.return_value = [
            b"<rss><channel><item><link>https://example.com/good</link></item></channel></rss>"
        ]
        bad = Mock()
        bad.raise_for_status.side_effect = RuntimeError("unavailable")
        session = Mock()
        session.get.side_effect = lambda url, **kwargs: bad if "bad" in url else good
        discovery = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            feeds=(
                ("good-feed", "https://good.example/{query}"),
                ("bad-feed", "https://bad.example/{query}"),
            ),
        )

        results, errors = discovery.discover_feeds(("test",))

        self.assertEqual([result.url for result in results], ["https://example.com/good"])
        self.assertEqual(errors, ["bad-feed: RuntimeError"])

    def test_rejects_oversized_feed(self):
        response = Mock()
        response.headers = {"Content-Length": "5000001"}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        discovery = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            feeds=(("large-feed", "https://large.example/{query}"),),
        )

        results, errors = discovery.discover_feeds(("test",))

        self.assertEqual(results, [])
        self.assertEqual(errors, ["large-feed: ValueError"])

    def test_google_news_feed_does_not_wait_for_network_resolution(self):
        feed = Mock()
        feed.headers = {}
        feed.raise_for_status.return_value = None
        feed.iter_content.return_value = [
            b"<rss><channel><item><title>AI</title>"
            b"<link>https://news.google.com/rss/articles/abc</link></item></channel></rss>"
        ]
        article = Mock()
        article.url = "https://publisher.example/ai"
        article.raise_for_status.return_value = None
        article.close.return_value = None
        session = Mock()
        session.get.side_effect = [feed, article]
        discovery = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            feeds=(("google-news-rss", "https://news.google.com/rss/search?q={query}"),),
        )

        results, errors = discovery.discover_feeds(("AI",))

        self.assertEqual(errors, [])
        self.assertEqual(results[0].url, "https://news.google.com/rss/articles/abc")

    def test_decodes_modern_google_news_article_urls(self):
        article = Mock()
        article.url = "https://news.google.com/rss/articles/token"
        article.content = (
            b'<div data-n-a-id="article-id" data-n-a-ts="1700000000" '
            b'data-n-a-sg="signature"></div>'
        )
        article.raise_for_status.return_value = None
        article.close.return_value = None
        decoded = Mock()
        decoded.text = (
            r'garturlres\",\"https://publisher.example/full-article\",1]'
        )
        decoded.raise_for_status.return_value = None
        decoded.close.return_value = None
        session = Mock()
        session.get.return_value = article
        session.post.return_value = decoded
        discovery = SearchDiscovery("http://search", session=session)

        result = discovery._resolve_google_news(
            SearchResult(article.url, "Article", ("google-news-rss",))
        )

        self.assertEqual(result.url, "https://publisher.example/full-article")
        self.assertIn("f.req", session.post.call_args.kwargs["data"])

    def test_drops_unresolved_google_news_article_urls(self):
        article = Mock()
        article.url = "https://news.google.com/rss/articles/token"
        article.content = b"<html></html>"
        article.raise_for_status.return_value = None
        article.close.return_value = None
        session = Mock()
        session.get.return_value = article
        discovery = SearchDiscovery("http://search", session=session)

        result = discovery._resolve_google_news(
            SearchResult(article.url, "Article", ("google-news-rss",))
        )

        self.assertIsNone(result)

    def test_resolves_google_news_urls_returned_by_regular_search(self):
        discovery = SearchDiscovery("http://search")
        source = SearchResult(
            "https://news.google.com/articles/token", "Article", ("google",)
        )
        resolved = SearchResult(
            "https://publisher.example/article", "Article", ("google",)
        )
        discovery.discover = Mock(return_value=([source], []))
        discovery.discover_feeds = Mock(return_value=([], []))
        discovery.discover_trends = Mock(return_value=([], []))
        discovery._prepare_google_news = Mock(return_value=resolved)

        results, errors = discovery.discover_many(("AI",), 1)

        self.assertEqual(errors, [])
        self.assertEqual([item.url for item in results], [resolved.url])

    def test_http_200_captcha_opens_block_path(self):
        response = Mock(status_code=200, content=b"Our systems have detected unusual traffic")
        response.url = "https://www.google.com/search?q=AI"
        blocked = SearchDiscovery._google_block(response)
        self.assertIsInstance(blocked, GoogleBlocked)
        self.assertTrue(blocked.captcha)

    def test_cache_hit_avoids_network(self):
        session = Mock()
        cached = [{"url": "https://example.com/a", "title": "A", "engines": ["google_web"]}]
        discovery = SearchDiscovery(
            "http://search", session=session,
            cache_get=Mock(return_value={"payload": cached}),
        )
        results = discovery._discover_google_page("AI", 1)
        self.assertEqual([row.url for row in results], ["https://example.com/a"])
        session.get.assert_not_called()

    def test_low_first_page_novelty_skips_second_page(self):
        discovery = SearchDiscovery(
            "http://search", second_page_min_novelty=0.15,
            novelty_counter=Mock(return_value=0),
        )
        discovery._discover_google_page = Mock(return_value=[
            SearchResult("https://example.com/old", "Old", ("google_web",))
        ])
        discovery.discover("AI", 2)
        discovery._discover_google_page.assert_called_once_with("AI", 1)

    def test_six_google_news_locales_are_rendered(self):
        locales = ("US:en", "GB:en", "SG:en", "SG:zh-Hans", "HK:zh-Hant", "TW:zh-Hant")
        targets = SearchDiscovery("http://search", news_locales=locales)._feed_targets(("AI",))
        self.assertEqual(len(targets), 6)

    def test_web_circuit_does_not_stop_news(self):
        feed = Mock(headers={}, status_code=200)
        feed.raise_for_status.return_value = None
        feed.iter_content.return_value = [
            b"<rss><channel><item><title>AI</title><link>https://example.com/a</link></item></channel></rss>"
        ]
        session = Mock()
        session.get.return_value = feed
        discovery = SearchDiscovery(
            "http://search", session=session, news_locales=("SG:en",),
            source_slot_acquirer=Mock(return_value={"allowed": False, "wait": 1800}),
        )
        results, errors = discovery.discover_many(("AI",), 1)
        self.assertIn("circuit_open", " ".join(errors))
        self.assertIn("https://example.com/a", [row.url for row in results])

    def test_news_uses_conditional_request_after_cache_expiry(self):
        response = Mock(status_code=304, headers={})
        session = Mock()
        session.get.return_value = response
        cache_put = Mock()
        stale = {
            "payload": [{"url": "https://example.com/a", "title": "A", "engines": ["google_news"]}],
            "etag": '"abc"', "last_modified": "Mon, 07 Sep 2026 00:00:00 GMT",
        }
        discovery = SearchDiscovery(
            "http://search", session=session, google_web_enabled=False,
            news_locales=("SG:en",), cache_get=Mock(return_value=None),
            cache_metadata_get=Mock(return_value=stale), cache_put=cache_put,
        )
        results, errors = discovery.discover_feeds(("OpenAI",))
        self.assertEqual(errors, [])
        self.assertEqual([row.url for row in results], ["https://example.com/a"])
        self.assertEqual(session.get.call_args.kwargs["headers"]["If-None-Match"], '"abc"')

    def test_one_session_is_bound_to_each_google_proxy(self):
        response = Mock(status_code=200, content=b"")
        response.url = "https://www.google.com/search"
        response.raise_for_status.return_value = None
        proxy_session = Mock()
        proxy_session.get.return_value = response
        pool = Mock()
        pool.choose.return_value = ("http://proxy.example:8080", "proxy-key")
        with patch("realtime.discovery.requests.Session", return_value=proxy_session):
            discovery = SearchDiscovery(
                "http://search", session=Mock(), proxy_pool=pool,
                proxy_profile="private", proxy_reserver=Mock(return_value=(True, 0)),
            )
            discovery._discover_google_page("AI one", 1)
            discovery._discover_google_page("AI two", 1)
        self.assertEqual(proxy_session.get.call_count, 2)
        self.assertEqual(len(discovery._proxy_sessions), 1)
