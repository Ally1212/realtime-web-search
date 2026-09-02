import unittest
from unittest.mock import Mock

from realtime.discovery import SearchDiscovery


class DiscoveryTests(unittest.TestCase):
    def test_deduplicates_results(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"url": "https://example.com/a", "title": "A", "engines": ["bing"]},
                {"url": "https://example.com/a", "title": "A2", "engines": ["brave"]},
            ],
            "unresponsive_engines": [],
        }
        session = Mock()
        session.get.return_value = response
        results, errors = SearchDiscovery("http://search", session=session).discover("test", 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [])

    def test_discovers_bing_rss_results(self):
        response = Mock()
        response.content = b"<rss><channel><item><title>Policy</title><link>https://example.com/policy</link></item></channel></rss>"
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        results, errors = SearchDiscovery("http://search", session=session).discover_rss("test")
        self.assertEqual([result.url for result in results], ["https://example.com/policy"])
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
