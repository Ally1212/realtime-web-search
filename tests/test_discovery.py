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


    def test_fallback_records_each_attempt_and_caches_only_success(self):
        recorder, cache = Mock(), Mock()
        d = SearchDiscovery(providers=("curl", "browser"), cache_put=cache,
            source_slot_acquirer=Mock(return_value={"allowed": True}), source_result_recorder=recorder)
        expected = [SearchResult("https://example.com/ai", "AI", ("google_web",))]
        d.transport.fetch = Mock(side_effect=[GoogleBlocked("google_javascript_required"), expected])
        self.assertEqual(d._discover_google_page("AI", 1), expected)
        self.assertEqual(recorder.call_count, 4)
        self.assertFalse(recorder.call_args_list[0].kwargs["success"])
        self.assertEqual(recorder.call_args_list[0].kwargs["error_code"], "google_javascript_required")
        self.assertTrue(recorder.call_args_list[2].kwargs["success"])
        self.assertEqual(cache.call_count, 1)

    def test_timeout_is_counted_and_never_cached(self):
        recorder, cache = Mock(), Mock()
        d = SearchDiscovery(providers=("browser",), cache_put=cache,
            source_slot_acquirer=Mock(return_value={"allowed": True}), source_result_recorder=recorder)
        d.transport.fetch = Mock(side_effect=TimeoutError("credential-bearing message"))
        with self.assertRaisesRegex(GoogleBlocked, "^google_timeout$"):
            d._discover_google_page("AI", 1)
        cache.assert_not_called()
        self.assertEqual(recorder.call_count, 2)
        self.assertNotIn("credential", str(d.attempts))

    def test_provider_stops_after_three_failures(self):
        d = SearchDiscovery(providers=("curl",), source_slot_acquirer=Mock(return_value={"allowed": True}))
        d.transport.fetch = Mock(side_effect=TimeoutError())
        for _ in range(4):
            with self.assertRaises(GoogleBlocked):
                d._discover_google_page("AI", 1)
        self.assertEqual(d.transport.fetch.call_count, 3)

    def test_circuit_open_does_not_attempt_transport(self):
        d = SearchDiscovery(source_slot_acquirer=Mock(return_value={"allowed": False}))
        d.transport.fetch = Mock()
        with self.assertRaises(GoogleBlocked):
            d._discover_google_page("AI", 1)
        d.transport.fetch.assert_not_called()

    def test_singleflight_lease_released_after_failure(self):
        release = Mock()
        d = SearchDiscovery(providers=("curl",), singleflight_acquirer=Mock(return_value="token"),
            singleflight_releaser=release, source_slot_acquirer=Mock(return_value={"allowed": True}))
        d.transport.fetch = Mock(side_effect=TimeoutError())
        with self.assertRaises(GoogleBlocked):
            d._discover_google_page("AI", 1)
        self.assertEqual(release.call_args.args[1], "token")

    def test_inflight_query_is_not_dispatched(self):
        d = SearchDiscovery(singleflight_acquirer=Mock(return_value=None))
        d.transport.fetch = Mock()
        with self.assertRaisesRegex(GoogleBlocked, "google_query_inflight"):
            d._discover_google_page("AI", 1)
        d.transport.fetch.assert_not_called()

    def test_deep_pages_have_daily_cache(self):
        cache = Mock()
        d = SearchDiscovery(providers=("searxng",), cache_put=cache,
            source_slot_acquirer=Mock(return_value={"allowed": True}))
        d.transport.fetch = Mock(return_value=[SearchResult("https://example.com/ai", "AI", ("google_web",))])
        d._discover_google_page("AI", 3)
        self.assertEqual(cache.call_args.args[6], 21600)
        d._discover_google_page("AI", 11)
        self.assertEqual(cache.call_args.args[6], 86400)

    def test_proxy_identifier_is_hashed_for_browser_too(self):
        import hashlib
        pool, recorder = Mock(), Mock()
        pool.choose.return_value = ("http://user:password@proxy.example:80", "proxy-key")
        d = SearchDiscovery(providers=("browser",), proxy_pool=pool, proxy_profile="private",
            proxy_result_recorder=recorder, source_slot_acquirer=Mock(return_value={"allowed": True}))
        d.transport.fetch = Mock(side_effect=TimeoutError())
        with self.assertRaises(GoogleBlocked):
            d._discover_google_page("AI", 1)
        self.assertEqual(recorder.call_args.args[0], hashlib.sha256(b"proxy-key").hexdigest())
        self.assertFalse(recorder.call_args.kwargs["success"])
