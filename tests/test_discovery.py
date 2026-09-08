import unittest
from unittest.mock import Mock

from realtime.discovery import SearchDiscovery, SearchResult


class DiscoveryTests(unittest.TestCase):
    def test_parses_google_html_results(self):
        content = (
            b'<a href="/url?q=https%3A%2F%2Fexample.com%2Fai&amp;sa=U">'
            b'<h3>AI report</h3></a><a href="https://www.google.com/preferences">'
            b'<h3>Preferences</h3></a>'
        )

        results = SearchDiscovery._parse_google_html(content)

        self.assertEqual([row.url for row in results], ["https://example.com/ai"])
        self.assertEqual(results[0].engines, ("google",))

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
        results, errors = SearchDiscovery("http://search", session=session).discover("test", 2)
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
            "http://search",
            session=session,
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

        SearchDiscovery("http://search", session=session).discover("test", 1)

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

        results, errors = SearchDiscovery("http://search", session=session).discover("test", 1)

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
            "http://search",
            session=session,
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
            "http://search",
            session=session,
            feeds=(("large-feed", "https://large.example/{query}"),),
        )

        results, errors = discovery.discover_feeds(("test",))

        self.assertEqual(results, [])
        self.assertEqual(errors, ["large-feed: ValueError"])

    def test_resolves_google_news_feed_urls(self):
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
            "http://search",
            session=session,
            feeds=(("google-news-rss", "https://news.google.com/rss/search?q={query}"),),
        )

        results, errors = discovery.discover_feeds(("AI",))

        self.assertEqual(errors, [])
        self.assertEqual(results[0].url, "https://publisher.example/ai")

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
        discovery._resolve_google_news = Mock(return_value=resolved)

        results, errors = discovery.discover_many(("AI",), 1)

        self.assertEqual(errors, [])
        self.assertEqual([item.url for item in results], [resolved.url])
