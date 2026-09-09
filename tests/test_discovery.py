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
        SearchDiscovery(session=session, language="zh")._discover_google_page(
            "人工智能", 1
        )
        self.assertEqual(session.get.call_args.kwargs["params"]["hl"], "zh-CN")
        self.assertIn("zh-CN", session.get.call_args.kwargs["headers"]["Accept-Language"])

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
            session=session,
            cache_get=Mock(return_value={"payload": cached}),
        )
        results = discovery._discover_google_page("AI", 1)
        self.assertEqual([row.url for row in results], ["https://example.com/a"])
        session.get.assert_not_called()

    def test_javascript_retry_shell_uses_browser_fallback_and_closes_response(self):
        response = Mock(
            status_code=200,
            content=b'<a href="/httpservice/retry/enablejs">enable javascript</a>',
        )
        response.url = "https://www.google.com/search?q=AI"
        response.headers = {}
        response.raise_for_status.return_value = None
        browser_results = [
            SearchResult("https://example.com/browser", "Browser result", ("google_web",))
        ]
        cache_put = Mock()
        discovery = SearchDiscovery(session=Mock(), cache_put=cache_put)
        discovery._google_web_get = Mock(return_value=response)
        discovery._discover_google_browser_page = Mock(return_value=browser_results)

        results = discovery._discover_google_page("AI", 1)

        self.assertEqual(results, browser_results)
        discovery._discover_google_browser_page.assert_called_once_with("AI", 1)
        response.close.assert_called_once_with()
        self.assertEqual(cache_put.call_args.args[5][0]["url"], "https://example.com/browser")

    def test_proxy_captcha_uses_direct_browser_fallback(self):
        browser_results = [
            SearchResult("https://example.com/direct", "Direct result", ("google_web",))
        ]
        discovery = SearchDiscovery(session=Mock(), cache_put=Mock())
        discovery._google_web_get = Mock(side_effect=GoogleBlocked(
            "google_captcha", 200, captcha=True
        ))
        discovery._discover_google_browser_page = Mock(return_value=browser_results)

        results = discovery._discover_google_page("AI", 3)

        self.assertEqual(results, browser_results)
        discovery._discover_google_browser_page.assert_called_once_with("AI", 3)

    def test_discover_closes_thread_local_browser_resources(self):
        discovery = SearchDiscovery(google_web_enabled=False)
        discovery._close_browser = Mock()

        self.assertEqual(discovery.discover("AI", 1), ([], []))

        discovery._close_browser.assert_called_once_with()

    def test_low_novelty_does_not_stop_fixed_paging(self):
        discovery = SearchDiscovery(
            google_web_max_pages=11, novelty_counter=Mock(return_value=0),
        )
        discovery._discover_google_page = Mock(
            side_effect=lambda query, page: [
                SearchResult(f"https://example.com/{page}", f"Page {page}", ("google_web",))
            ]
        )

        results, errors = discovery.discover("AI", 11)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 11)
        discovery._discover_google_page.assert_called_with("AI", 11)

    def test_persistent_frontier_collects_only_leased_three_page_batch(self):
        recorder = Mock(return_value=True)
        discovery = SearchDiscovery(
            google_web_max_pages=11, google_web_pages_per_batch=3,
            novelty_counter=Mock(return_value=1),
            page_batch_acquirer=Mock(return_value={
                "start_page": 4, "end_page": 6, "lease_token": "lease"
            }),
            page_result_recorder=recorder,
        )
        discovery._discover_google_page = Mock(
            side_effect=lambda query, page: [
                SearchResult(f"https://example.com/{page}", f"Page {page}", ("google_web",))
            ]
        )

        results, errors = discovery.discover("AI", 11)

        self.assertEqual(errors, [])
        self.assertEqual([row.url for row in results], [
            "https://example.com/4", "https://example.com/5", "https://example.com/6",
        ])
        self.assertEqual(discovery._discover_google_page.call_count, 3)
        self.assertEqual(recorder.call_count, 3)
        self.assertEqual(recorder.call_args.args[1], 6)
        self.assertTrue(recorder.call_args.kwargs["success"])

    def test_frontier_failure_keeps_later_pages_unrequested(self):
        recorder = Mock(return_value=True)
        discovery = SearchDiscovery(
            google_web_max_pages=11,
            page_batch_acquirer=Mock(return_value={
                "start_page": 7, "end_page": 9, "lease_token": "lease"
            }),
            page_result_recorder=recorder,
        )
        discovery._discover_google_page = Mock(side_effect=GoogleBlocked(
            "google_captcha", 200, captcha=True
        ))

        results, errors = discovery.discover("AI", 11)

        self.assertEqual(results, [])
        self.assertIn("page 7: google web google_captcha", errors)
        discovery._discover_google_page.assert_called_once_with("AI", 7)
        self.assertFalse(recorder.call_args.kwargs["success"])
        self.assertTrue(recorder.call_args.kwargs["captcha"])

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
                session=Mock(), proxy_pool=pool,
                proxy_profile="private", proxy_reserver=Mock(return_value=(True, 0)),
            )
            discovery._discover_google_page("AI one", 1)
            discovery._discover_google_page("AI two", 1)
        self.assertEqual(proxy_session.get.call_count, 2)
        self.assertEqual(len(discovery._proxy_sessions), 1)
