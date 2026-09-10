import unittest
from unittest.mock import Mock, MagicMock, patch

from realtime.discovery import GoogleBlocked
from realtime.free_google import GoogleTransport


class FreeGoogleTests(unittest.TestCase):
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
