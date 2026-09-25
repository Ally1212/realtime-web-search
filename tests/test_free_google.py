import unittest
from unittest.mock import Mock, MagicMock, patch

from realtime.discovery import GoogleBlocked, SearchResult
from realtime.free_google import GoogleTransport


class FreeGoogleTests(unittest.TestCase):
    @staticmethod
    def _openserp_response(session, *, results=None, page=3, query="人工智能"):
        response = session.get.return_value
        response.status_code = 200
        response.content = b'{"openserp":"response"}'
        response.headers = {
            "X-Cache": "BYPASS", "X-Network-Bytes": "321",
        }

        def payload():
            request_id = session.get.call_args.kwargs["headers"]["X-Request-ID"]
            response.headers["X-Request-ID"] = request_id
            payload = {
                "query": {"text": query, "engines_requested": ["google"]},
                "meta": {"request_id": request_id, "version": "2.2", "engines_failed": []},
                "results": results if results is not None else [{
                    "type": "organic", "engine": "google", "title": "AI 研究",
                    "url": "https://example.com/ai",
                }],
                "pagination": {"page": page, "has_more": True, "next_start": 30},
            }
            session.last_openserp_payload = payload
            session.last_openserp_request_id = request_id
            return payload

        response.json.side_effect = payload
        return response

    def test_openserp_maps_query_proxy_and_audit_metadata(self):
        session = MagicMock()
        session.__enter__.return_value = session
        response = self._openserp_response(session)
        with patch("realtime.free_google.requests.Session", return_value=session):
            transport = GoogleTransport(
                5, "zh", "", openserp_url="http://openserp:7000",
                openserp_request_timeout_seconds=25,
            )
            results = transport.openserp(
                "人工智能", 3, "http://user:secret@proxy.example:80", "proxy-key",
            )
        request = session.get.call_args
        self.assertEqual(request.kwargs["params"]["start"], 20)
        self.assertEqual(request.kwargs["params"]["lang"], "ZH")
        self.assertEqual(request.kwargs["params"]["region"], "US")
        self.assertEqual(request.kwargs["params"]["extract"], 0)
        self.assertEqual(request.kwargs["headers"]["X-Proxy-URL"], "http://user:secret@proxy.example:80")
        self.assertEqual(len(request.kwargs["headers"]["X-Proxy-Session-ID"]), 64)
        self.assertEqual([row.url for row in results], ["https://example.com/ai"])
        self.assertEqual(transport.last_evidence["upstream_version"], "2.2")
        self.assertEqual(transport.last_evidence["upstream_attempts"], 1)
        self.assertEqual(transport.last_evidence["upstream_cache_status"], "BYPASS")
        self.assertEqual(transport.last_evidence["network_bytes"], 321)
        self.assertNotIn("secret", str(transport.last_evidence))
        response.close.assert_called_once()

    def test_openserp_rejects_fallback_cached_or_wrong_engine_envelopes(self):
        for mutation in ("fallback", "cache", "engine"):
            with self.subTest(mutation=mutation):
                session = MagicMock()
                results = [{
                    "type": "organic", "engine": "bing" if mutation == "engine" else "google",
                    "title": "AI", "url": "https://example.com/ai",
                }]
                response = self._openserp_response(session, results=results)
                if mutation == "fallback":
                    response.headers["X-Fallback-Engine"] = "bing"
                elif mutation == "cache":
                    response.headers["X-Cache"] = "HIT"
                with patch("realtime.free_google.requests.Session", return_value=session):
                    with self.assertRaisesRegex(GoogleBlocked, "openserp_invalid_response"):
                        GoogleTransport(5, "zh", "").openserp("人工智能", 3, None, None)

    def test_openserp_resolves_wrapped_google_results_once(self):
        session = MagicMock()
        session.__enter__.return_value = session
        results = [
            {"type": "organic", "engine": "google", "title": "Target", "url": "https://www.google.com/goto?token=1"},
            {"type": "organic", "engine": "google", "title": "Blocked", "url": "https://www.google.com/goto?token=2"},
        ]
        response = self._openserp_response(session, results=results, page=1, query="Target")
        redirects = [
            response,
            Mock(status_code=302, headers={"Location": "https://example.com/target"}),
            Mock(status_code=429, headers={}),
        ]
        session.get.side_effect = redirects
        with patch("realtime.free_google.requests.Session", return_value=session):
            transport = GoogleTransport(5, "en", "")
            parsed = transport.openserp("Target", 1, "http://proxy.example:80", None)
        self.assertEqual([row.url for row in parsed], ["https://example.com/target"])
        self.assertEqual(session.get.call_count, 3)
        first = session.get.call_args_list[1]
        self.assertFalse(first.kwargs["allow_redirects"])
        self.assertTrue(first.kwargs["stream"])
        self.assertEqual(first.kwargs["proxies"]["https"], "http://proxy.example:80")
        self.assertEqual(transport.last_evidence["wrapped_google_results"], 2)
        self.assertEqual(transport.last_evidence["resolved_google_results"], 1)

    def test_openserp_maps_captcha_without_exposing_error_body(self):
        session = MagicMock()
        response = session.get.return_value
        response.status_code = 429
        response.content = b'{"error":"captcha_detected","message":"do not store"}'
        response.headers = {"X-Request-ID": "request-1", "X-Network-Bytes": "50"}
        response.json.return_value = {"error": "captcha_detected", "message": "do not store"}
        with patch("realtime.free_google.requests.Session", return_value=session):
            transport = GoogleTransport(5, "en", "")
            with self.assertRaises(GoogleBlocked) as error:
                transport.openserp("AI", 1, None, None)
        self.assertEqual(error.exception.reason, "google_captcha")
        self.assertTrue(error.exception.captcha)
        self.assertEqual(transport.last_evidence["classification"], "captcha")
        self.assertNotIn("do not store", str(transport.last_evidence))

    def test_browser_watchdog_bounds_failure_and_keeps_credentials_out_of_argv(self):
        import subprocess
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("browser", 8)
        transport = GoogleTransport(3, "en", "")
        with patch("realtime.free_google.subprocess.Popen", return_value=process) as launch, patch.object(transport, "_kill_browser_process") as kill:
            with self.assertRaisesRegex(GoogleBlocked, "google_browser_deadline"):
                transport.browser("AI", 1, "http://user:secret@proxy.example:80")
        self.assertNotIn("secret", str(launch.call_args))
        self.assertEqual(process.communicate.call_args.kwargs["timeout"], 8)
        kill.assert_called_once_with(process)

    def test_wml_page_with_unusable_result_anchors_is_confirmed_empty(self):
        session = Mock()
        response = session.get.return_value
        response.content = b'<a href="/url?q=https%3A%2F%2Fwww.google.com%2Fsearch&sa=U"><span>Google</span></a><p>did not match any documents</p>'
        response.status_code = 200
        response.url = "https://www.google.com/wml/search"
        transport = GoogleTransport(5, "zh", "")
        with patch("curl_cffi.requests.Session", return_value=session):
            self.assertEqual(transport.curl("AI", 1, None, wml=True), [])
        self.assertEqual(transport.last_evidence["classification"], "empty")

    def test_wml_shell_without_localized_empty_phrase_is_confirmed_empty(self):
        query = "site:www.bright.cn AI agents after:2021-01-01 before:2022-01-01"
        html = (
            '<html><head><title>' + query + ' - Google Search</title></head><body>'
            '<a href="/search?q=x&amp;tbm=isch">IMAGES</a>'
            '<a href="#">Next &gt;</a>'
            '<a href="/url?q=https%3A%2F%2Fsupport.google.com%2Fwebsearch&amp;sa=U">Learn more</a>'
            '<a href="/url?q=https%3A%2F%2Faccounts.google.com%2FServiceLogin&amp;sa=U">Sign in</a>'
            '</body></html>'
        )
        session = Mock()
        response = session.get.return_value
        response.content = html.encode()
        response.status_code = 200
        response.url = "https://www.google.com/wml/search"
        transport = GoogleTransport(5, "en", "")
        with patch("curl_cffi.requests.Session", return_value=session):
            self.assertEqual(transport.curl(query, 1, None, wml=True), [])
        self.assertEqual(transport.last_evidence["classification"], "empty")

    def test_wml_parser_handles_unicode_titles_redirects_and_skips_navigation(self):
        html = '<a href="/search?q=AI"><span>下一页</span></a><a href="/url?q=https%3A%2F%2Fexample.com%2Fai&amp;sa=U"><span>人工智能研究</span><span>example.com</span></a>'
        with patch("realtime.free_google.public_result", return_value=True):
            results = GoogleTransport.parse_wml(html.encode())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "人工智能研究")
        self.assertEqual(results[0].url, "https://example.com/ai")

    def test_searxng_rejects_upstream_error_even_with_http_200(self):
        session = MagicMock()
        session.__enter__.return_value = session
        response = session.get.return_value
        response.json.return_value = {"results": [], "unresponsive_engines": [["google", "CAPTCHA"]]}
        with patch("realtime.free_google.requests.Session", return_value=session):
            with self.assertRaises(GoogleBlocked) as error:
                GoogleTransport(5, "zh", "http://searxng:8080").searxng("人工智能", 11)
        self.assertTrue(error.exception.captcha)
        self.assertEqual(session.get.call_args.kwargs["params"]["pageno"], 11)
        self.assertEqual(session.get.call_args.kwargs["params"]["language"], "zh-CN")
        response.close.assert_called_once()

    def test_searxng_does_not_accept_other_engine_or_unknown_empty(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value.json.return_value = {"results": [
            {"url": "https://example.com", "title": "AI", "engine": "bing"}
        ]}
        with patch("realtime.free_google.requests.Session", return_value=session):
            with self.assertRaisesRegex(GoogleBlocked, "empty_unverified"):
                GoogleTransport(5, "en", "http://searxng:8080").searxng("AI", 1)

    def test_browser_detects_captcha_before_waiting_for_result_nodes(self):
        transport = GoogleTransport(5, "en", "")
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        page.goto.return_value.status = 200
        page.content.return_value = "<html>Our systems have detected unusual traffic</html>"
        page.url = "https://www.google.com/sorry/index"
        page.is_closed.return_value = False
        transport.local.browser = (None, Mock(), Mock(), context)
        with self.assertRaises(GoogleBlocked) as error:
            transport._browser_page("AI", 1, None)
        self.assertTrue(error.exception.captcha)
        page.locator.assert_not_called()
        page.wait_for_timeout.assert_not_called()

    def test_curl_session_cache_is_bounded(self):
        transport = GoogleTransport(5, "en", "")
        sessions = []

        def make_session(**_kwargs):
            session = Mock()
            response = session.get.return_value
            response.status_code = 200
            response.content = b"<html>results</html>"
            response.url = "https://www.google.com/search"
            response.raise_for_status.return_value = None
            sessions.append(session)
            return session

        result = SearchResult("https://example.com/result", "Result", ("google_web",))
        with patch("curl_cffi.requests.Session", side_effect=make_session), patch(
            "realtime.discovery.SearchDiscovery._parse_google_html", return_value=[result]
        ):
            for index in range(transport.max_curl_sessions + 1):
                transport.curl("AI", 1, f"http://proxy-{index}:8080")

        self.assertEqual(len(transport.local.sessions), transport.max_curl_sessions)
        self.assertEqual(len(sessions), transport.max_curl_sessions + 1)
        sessions[0].close.assert_called_once()
        transport.close()
        self.assertEqual(sum(session.close.call_count for session in sessions[1:]), len(sessions) - 1)

    def test_curl_transport_failure_drops_broken_cached_session(self):
        session = Mock()
        session.get.side_effect = RuntimeError("socket closed")
        transport = GoogleTransport(5, "en", "")
        with patch("curl_cffi.requests.Session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "socket closed"):
                transport.curl("AI", 1, "http://proxy.example:8080")
        self.assertEqual(transport.local.sessions, {})
        session.close.assert_called_once()

    def test_transport_failure_evidence_includes_exception_context(self):
        transport = GoogleTransport(5, "en", "")
        failure = GoogleBlocked("google_unrecognized_page", 200)
        def fetch(*args, **kwargs):
            transport.local.last_evidence = {"classification": "parse_failure"}
            raise failure
        with patch.object(transport, "_fetch", side_effect=fetch), patch.dict(
            "os.environ", {"GOOGLE_SERP_SAVE_HTML": "1"}
        ):
            with self.assertRaises(GoogleBlocked):
                transport.fetch("wml", "AI", 1, None)
        self.assertEqual(transport.last_evidence["exception"], "GoogleBlocked:google_unrecognized_page")
        self.assertIn("GoogleBlocked", transport.last_evidence["traceback"])

    def test_curl_session_reuses_same_exit_and_rejects_js_shell(self):
        session = Mock()
        response = session.get.return_value
        response.content = b'<a href="/httpservice/retry/enablejs">Enable Javascript</a>'
        response.status_code = 200
        response.url = "https://www.google.com/search"
        transport = GoogleTransport(5, "en", "")
        with patch("curl_cffi.requests.Session", return_value=session) as factory:
            for _ in range(2):
                with self.assertRaisesRegex(GoogleBlocked, "javascript_required"):
                    transport.curl("AI", 1, None)
            factory.assert_called_once()
        self.assertEqual(response.close.call_count, 2)
        transport.close()
        session.close.assert_called_once()
