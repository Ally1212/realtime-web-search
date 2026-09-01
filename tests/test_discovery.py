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
