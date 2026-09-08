import unittest
from unittest.mock import patch

from realtime.fetcher import detect_language, extract_text, normalize_url, relevant_to
from realtime.crawler import is_javascript_shell


class FetcherTests(unittest.TestCase):
    def test_detects_javascript_shell(self):
        self.assertTrue(is_javascript_shell(b'<html><div id="root"></div><script src="app.js"></script></html>'))
        self.assertFalse(is_javascript_shell(b"<html><main>A normal article</main></html>"))

    def test_extracts_visible_text(self):
        title, text = extract_text(
            b"<html><head><title> Example </title><script>bad()</script></head><body><nav>menu</nav><main>Hello world</main></body></html>",
            "https://example.com/",
        )
        self.assertEqual(title, "Example")
        self.assertEqual(text, "Example Hello world")

    def test_trafilatura_removes_navigation_boilerplate(self):
        article = "Singapore AI policy provides governance guidance. " * 8
        raw = (
            "<html><head><title>AI policy</title></head><body>"
            "<nav>Navigation marker</nav><main><h1>AI policy</h1><p>"
            f"{article}</p></main><footer>Footer marker</footer></body></html>"
        ).encode()
        title, text = extract_text(raw, "https://example.com/policy")
        self.assertIn("Singapore AI policy", text)
        self.assertNotIn("Navigation marker", text)
        self.assertNotIn("Footer marker", text)
        self.assertTrue(title)

    def test_falls_back_when_trafilatura_fails(self):
        raw = b"<html><head><title>Fallback</title></head><body><main>Visible text</main></body></html>"
        with patch("realtime.fetcher.bare_extraction", side_effect=RuntimeError("failed")):
            title, text = extract_text(raw, "https://example.com/")
        self.assertEqual(title, "Fallback")
        self.assertEqual(text, "Fallback Visible text")

    def test_prefers_json_ld_article_body(self):
        body = "Structured article text about artificial intelligence. " * 4
        raw = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"NewsArticle","headline":"Structured title",'
            f'"articleBody":"{body}"}}'
            '</script></head><body>Subscribe to continue</body></html>'
        ).encode()
        title, text = extract_text(raw, "https://example.com/article")
        self.assertEqual(title, "Structured title")
        self.assertIn("Structured article text", text)
        self.assertNotIn("Subscribe", text)

    def test_normalizes_url(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM:443/a?q=1&utm_source=x#x"),
            "https://example.com/a?q=1",
        )

    def test_relevance_and_language(self):
        self.assertTrue(relevant_to("Singapore released a new AI policy", "News", ("AI policy",)))
        self.assertFalse(relevant_to("Sports results and weather", "News", ("AI policy",)))
        self.assertFalse(relevant_to("Visit Singapore for food and culture", "Singapore", ("Singapore AI policy",)))
        self.assertEqual(detect_language("这是一个中文网页，包含足够多的中文内容用于判断。"), "zh")
