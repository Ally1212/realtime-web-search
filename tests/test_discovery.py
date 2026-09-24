import threading
import time
import unittest
from unittest.mock import Mock, patch

from realtime.discovery import GoogleBlocked, SearchDiscovery, SearchResult
from realtime.locales import parse_locales


class DiscoveryTests(unittest.TestCase):
    def test_locale_separates_cache_but_same_locale_reuses_without_network(self):
        cache = {}
        expected = [SearchResult('https://example.com/ai', 'AI', ('google_web',))]

        def get(key, source):
            row = cache.get(key)
            return row if row is not None else None

    def put(key, source, query_hash, locale, page, results, ttl, **kwargs):
        cache[key] = {
            'payload': results, 'metadata': kwargs.get('metadata') or {},
        }

        def discovery(locale):
            return SearchDiscovery(
                providers=('wml',), language='zh', search_locale=locale,
                cache_get=get, cache_put=put,
                source_slot_acquirer=Mock(return_value={'allowed': True}),
            )

        first = discovery(parse_locales('zh-CN-CN')[0])
        second = discovery(parse_locales('zh-TW-TW')[0])
        first.transport.fetch = Mock(return_value=expected)
        second.transport.fetch = Mock(return_value=expected)
        try:
            self.assertEqual(first._discover_google_page('AI', 1), expected)
            self.assertEqual(first._discover_google_page('AI', 1), expected)
            self.assertEqual(second._discover_google_page('AI', 1), expected)
            self.assertEqual(first.transport.fetch.call_count, 1)
            self.assertEqual(second.transport.fetch.call_count, 1)
            self.assertEqual(len(cache), 2)
        finally:
            first.close()
            second.close()

    def test_page_batch_acquirer_receives_locale_label_string(self):
        acquirer = Mock(return_value=None)
        discovery = SearchDiscovery(
            providers=('wml',), language='zh',
            web_query_eligible=True,
            page_batch_acquirer=acquirer,
            source_slot_acquirer=Mock(return_value={'allowed': True}),
        )
        try:
            discovery._discover('GDP', 3)
        finally:
            discovery.close()
        acquirer.assert_called_once()
        locale = acquirer.call_args[0][1]
        self.assertIsInstance(locale, str)
        self.assertEqual(locale, discovery.search_locale.label)

    def test_search_locale_does_not_change_proxy_locale_dimension(self):
        pool = Mock()
        pool.available_count.return_value = 1
        pool.choose.return_value = ('http://proxy.example:80', 'proxy-key')
        reserve = Mock(return_value=(True, 0))
        discovery = SearchDiscovery(
            providers=('wml',), proxy_pool=pool, proxy_profile='private',
            language='zh',
            search_locale=parse_locales('zh-TW-TW')[0], proxy_reserver=reserve,
            source_slot_acquirer=Mock(return_value={'allowed': True}),
        )
        discovery.transport.fetch = Mock(return_value=[])
        try:
            discovery._attempt('wml', 'AI', 1)
        finally:
            discovery.close()
        self.assertEqual(reserve.call_args.args[1], 'zh-CN')

    def test_google_profiles_rotate_and_record_selected_profile(self):
        pool, slot, recorder = Mock(), Mock(return_value={'allowed': True}), Mock()
        pool.available_count.return_value = 1
        pool.choose.side_effect = [
            ('http://private.example:80', 'private-key'),
            ('http://public-google.example:80', 'public-google-key'),
        ]
        discovery = SearchDiscovery(
            providers=('wml',), proxy_pool=pool, proxy_profile='private',
            proxy_profiles=('private', 'public_google', 'public'),
            source_slot_acquirer=slot, source_result_recorder=recorder,
        )
        discovery.transport.fetch = Mock(return_value=[SearchResult('https://example.com/a', 'A', ('google_web',))])

        try:
            discovery._attempt('wml', 'AI', 1)
            discovery._attempt('wml', 'AI', 2)
        finally:
            discovery.close()

        self.assertEqual([row['proxy_profile'] for row in discovery.attempts], ['private', 'public_google'])
        self.assertEqual(
            [call.args[0] for call in slot.call_args_list],
            ['google_web:private', 'google_web:public_google'],
        )
        self.assertIn('google_web:private', [call.args[0] for call in recorder.call_args_list])

    def test_profile_circuit_is_isolated_and_cannot_block_other_pools(self):
        pool = Mock()
        pool.available_count.return_value = 1
        pool.choose.return_value = ('http://proxy.example:80', 'proxy-key')
        slots = {
            'google_web:private': {'allowed': False},
            'google_web:public_google': {'allowed': True},
        }
        slot = Mock(side_effect=lambda source, *_: slots[source])
        discovery = SearchDiscovery(
            providers=('wml',), proxy_pool=pool, proxy_profile='private',
            proxy_profiles=('private', 'public_google'),
            source_slot_acquirer=slot,
        )
        discovery.transport.fetch = Mock(return_value=[])

        with self.assertRaisesRegex(GoogleBlocked, 'google_web_circuit_open'):
            discovery._attempt('wml', 'AI', 1)

        discovery._profile_cursor = 1
        discovery._attempt('wml', 'AI', 2)
        self.assertEqual(slot.call_args.args[0], 'google_web:public_google')

    def test_proxy_exhaustion_reports_earliest_reuse_time(self):
        pool = Mock()
        pool.available_count.return_value = 2
        pool.choose.side_effect = [
            ('http://one.example:80', 'one'),
            ('http://two.example:80', 'two'),
        ]
        pool.google_identity.side_effect = [('group-one', ['one']), ('group-two', ['two'])]
        reserve = Mock(side_effect=[(False, 7.5), (False, 3.25)])
        discovery = SearchDiscovery(
            providers=('wml',), proxy_pool=pool, proxy_profile='private',
            proxy_group_reserver=reserve,
        )

        with self.assertRaises(GoogleBlocked) as raised:
            discovery._select_proxy('wml')

        self.assertEqual(raised.exception.reason, 'google_proxy_unavailable')
        self.assertEqual(raised.exception.retry_after, 3.25)

    def test_http_200_parse_failure_does_not_quarantine_exit_or_cache_empty_result(self):
        for code, status, cooldown in [('google_unrecognized_page', 200, 30),
                                       ('google_unrecognized_page', None, 300),
                                       ('google_timeout', None, 300),
                                       ('google_captcha', 200, 300)]:
            with self.subTest(code=code, status=status):
                pool, recorder, cache = Mock(), Mock(), Mock()
                pool.available_count.return_value = 1
                pool.choose.return_value = ('http://proxy.example:80', 'proxy-key')
                d = SearchDiscovery(providers=('wml',), proxy_pool=pool, proxy_profile='private',
                                    proxy_result_recorder=recorder, cache_put=cache,
                                    source_slot_acquirer=Mock(return_value={'allowed': True}))
                d.transport.local.last_evidence = {'http_status': status}
                d.transport.fetch = Mock(side_effect=GoogleBlocked(code, captcha=code == 'google_captcha'))
                try:
                    with self.assertRaisesRegex(GoogleBlocked, code):
                        d._discover_google_page('AI', 1)
                    self.assertEqual(recorder.call_args.kwargs['cooldown_seconds'], cooldown)
                    self.assertFalse(recorder.call_args.kwargs['success'])
                    pool.defer.assert_called_with('proxy-key', 'www.google.com', cooldown)
                    cache.assert_not_called()
                    self.assertFalse(d.attempts[-1]['success'])
                finally:
                    d.close()

    def test_parses_google_html_results(self):
        content = (
            '<div><a href="/url?q=https%3A%2F%2Fexample.com%2Fai&amp;sa=U">'
            '<h3>AI report</h3></a><div class="VwiC3b">2 days ago — New AI research</div></div>'
            '<a href="https://www.google.com/preferences">'
            '<h3>Preferences</h3></a>'
        )

        results = SearchDiscovery._parse_google_html(content)

        self.assertEqual([row.url for row in results], ["https://example.com/ai"])
        self.assertEqual(results[0].engines, ("google_web",))
        self.assertEqual(results[0].rank, 1)
        self.assertEqual(results[0].date, "2 days ago")
        self.assertEqual(results[0].description, "New AI research")
        self.assertEqual(results[0].display_link, "example.com")

    def test_parsing_filters_zero_yield_social_and_search_domains(self):
        content = (
            '<a href="https://www.youtube.com/watch?v=1"><h3>Video</h3></a>'
            '<a href="https://www.quora.com/answer"><h3>Question</h3></a>'
            '<a href="https://www.baidu.com/baike/item/ai"><h3>Encyclopedia</h3></a>'
            '<a href="https://example.com/article"><h3>Article</h3></a>'
        )

        results = SearchDiscovery._parse_google_html(content)

        self.assertEqual([row.url for row in results], ["https://example.com/article"])

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

    def test_cache_round_trip_preserves_serp_metadata(self):
        row = {"url": "https://example.com/ai", "title": "AI", "engines": ["google_web"],
               "rank": 3, "description": "Research", "display_link": "example.com",
               "source": "example", "date": "2 days ago", "serp_module": "news"}
        discovery = SearchDiscovery(cache_get=Mock(return_value={"payload": [row]}))
        results = discovery._discover_google_page("AI", 1)
        self.assertEqual(results[0].rank, 3)
        self.assertEqual(results[0].description, "Research")
        self.assertEqual(results[0].date, "2 days ago")
        self.assertEqual(results[0].serp_module, "news")



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

    def test_rotating_proxy_provider_retries_with_another_exit(self):
        pool = Mock()
        pool.available_count.return_value = 3
        pool.choose.side_effect = [
            ("http://proxy-1.example:80", "proxy-1"),
            ("http://proxy-2.example:80", "proxy-2"),
            ("http://proxy-3.example:80", "proxy-3"),
        ]
        expected = [SearchResult("https://example.com/ai", "AI", ("google_web",))]
        d = SearchDiscovery(
            providers=("wml",), proxy_pool=pool, proxy_profile="private",
            proxy_provider_attempts=3,
            proxy_reserver=Mock(return_value=(True, 0)),
            source_slot_acquirer=Mock(return_value={"allowed": True}),
        )
        d.transport.fetch = Mock(side_effect=[
            GoogleBlocked("google_captcha", captcha=True),
            GoogleBlocked("google_captcha", captcha=True),
            expected,
        ])

        self.assertEqual(d._discover_google_page("AI", 1), expected)
        self.assertEqual(d.transport.fetch.call_count, 3)
        self.assertNotIn("wml", d._local_cooldowns)

    def test_rotating_proxy_captcha_does_not_open_source_circuit(self):
        pool = Mock()
        pool.available_count.return_value = 1
        pool.choose.return_value = ("http://proxy-1.example:80", "proxy-1")
        recorder = Mock()
        d = SearchDiscovery(
            providers=("wml",), proxy_pool=pool, proxy_profile="private",
            proxy_reserver=Mock(return_value=(True, 0)),
            source_slot_acquirer=Mock(return_value={"allowed": True}),
            source_result_recorder=recorder,
        )
        d.transport.fetch = Mock(side_effect=GoogleBlocked("google_captcha", captcha=True))

        with self.assertRaises(GoogleBlocked):
            d._discover_google_page("AI", 1)

        calls = [call for call in recorder.call_args_list if call.args[0] == "google_wml:private"]
        self.assertTrue(calls)
        self.assertFalse(any(call.kwargs.get("captcha") for call in calls))
        self.assertFalse(any(call.kwargs.get("limited") for call in calls))

    def test_exhausts_proxy_pool_before_direct_fallback(self):
        pool = Mock()
        pool.available_count.return_value = 3
        pool.choose.side_effect = [
            ("http://proxy-1.example:80", "proxy-1"),
            ("http://proxy-2.example:80", "proxy-2"),
            ("http://proxy-3.example:80", "proxy-3"),
            None,
        ]
        expected = [SearchResult("https://example.com/ai", "AI", ("google_web",))]
        d = SearchDiscovery(
            providers=("wml", "wml_direct"), proxy_pool=pool,
            proxy_profile="private", proxy_provider_attempts=0,
            proxy_reserver=Mock(return_value=(True, 0)),
            source_slot_acquirer=Mock(return_value={"allowed": True}),
        )
        d.transport.fetch = Mock(side_effect=[
            GoogleBlocked("google_captcha", captcha=True),
            GoogleBlocked("google_captcha", captcha=True),
            GoogleBlocked("google_captcha", captcha=True),
            expected,
        ])

        self.assertEqual(d._discover_google_page("AI", 1), expected)
        self.assertEqual(d.transport.fetch.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in d.transport.fetch.call_args_list],
            ["wml", "wml", "wml", "wml_direct"],
        )

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
        d = SearchDiscovery(singleflight_acquirer=Mock(return_value=None), singleflight_wait_seconds=0)
        d.transport.fetch = Mock()
        with self.assertRaisesRegex(GoogleBlocked, "google_query_inflight"):
            d._discover_google_page("AI", 1)
        d.transport.fetch.assert_not_called()

    def test_inflight_query_waits_for_cache_then_reuses_it(self):
        cache = {'pending': 0}
        expected = [SearchResult('https://example.com/ai', 'AI', ('google_web',))]

        def get(key, source):
            row = cache.get(key)
            return row if row is not None else None

        def put(key, source, query_hash, locale, page, results, ttl, **kwargs):
            cache[key] = {'payload': results, 'metadata': kwargs.get('metadata') or {}}

        release = threading.Event()

        def acquire(key):
            cache['pending'] += 1
            return None

        d = SearchDiscovery(
            providers=('wml',), cache_get=get, cache_put=put,
            singleflight_acquirer=acquire,
            singleflight_wait_seconds=2,
            source_slot_acquirer=Mock(return_value={'allowed': True}),
        )
        d.transport.fetch = Mock(return_value=expected)
        cache_key = d._cache_key('google_web', 'AI', d.search_locale.cache_identity + ':free-v1:wml', 1)

        def fill_cache():
            cache[cache_key] = {
                'payload': SearchDiscovery._serialize(expected), 'metadata': {}
            }
            release.set()

        threading.Timer(.15, fill_cache).start()
        try:
            self.assertEqual(d._discover_google_page('AI', 1), expected)
        finally:
            d.close()
        self.assertGreaterEqual(cache['pending'], 2)
        d.transport.fetch.assert_not_called()

    def test_query_mismatch_is_rejected_and_query_level_cooldown_is_isolated(self):
        recorder = Mock()
        d = SearchDiscovery(
            providers=('wml',), query_cooldown_seconds=15,
            query_cooldown_recorder=recorder,
            source_slot_acquirer=Mock(return_value={'allowed': True}),
        )
        d.transport.local.last_evidence = {
            'http_status': 200, 'detected_query': 'AI safety',
            'parser_version': 'google-serp-v2', 'parse_mode': 'light',
        }
        d.transport.fetch = Mock(return_value=[SearchResult('https://example.com/a', 'A', ('google_web',))])
        try:
            with self.assertRaisesRegex(GoogleBlocked, 'google_query_mismatch'):
                d._discover_google_page('AI agents', 1)
            d.transport.local.last_evidence['detected_query'] = 'other query'
            d.transport.fetch = Mock(return_value=[SearchResult('https://example.com/b', 'B', ('google_web',))])
            d._discover_google_page('other query', 1)
            with self.assertRaisesRegex(GoogleBlocked, 'google_query_cooling'):
                d._discover_google_page('AI agents', 2)
        finally:
            d.close()
        self.assertEqual(recorder.call_args.kwargs['reason'], 'google_query_mismatch')

    def test_time_filter_changes_request_and_cache_identity(self):
        first = SearchDiscovery(providers=('wml',), time_filter='qdr:d')
        second = SearchDiscovery(providers=('wml',), time_filter='qdr:w')
        self.assertNotEqual(
            first._cache_key('google_web', 'AI', 'en', 1),
            second._cache_key('google_web', 'AI', 'en', 1),
        )
        self.assertEqual(first.transport.time_filter, 'qdr:d')

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
        pool.available_count.return_value = 1
        pool.choose.return_value = ("http://user:password@proxy.example:80", "proxy-key")
        d = SearchDiscovery(providers=("browser",), proxy_pool=pool, proxy_profile="private",
            proxy_result_recorder=recorder, source_slot_acquirer=Mock(return_value={"allowed": True}))
        d.transport.fetch = Mock(side_effect=TimeoutError())
        with self.assertRaises(GoogleBlocked):
            d._discover_google_page("AI", 1)
        self.assertEqual(recorder.call_args.args[0], hashlib.sha256(b"proxy-key").hexdigest())
        self.assertFalse(recorder.call_args.kwargs["success"])
