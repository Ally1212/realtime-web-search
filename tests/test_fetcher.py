import unittest

from realtime.fetcher import extract_text, normalize_url


class FetcherTests(unittest.TestCase):
    def test_extracts_visible_text(self):
        title, text = extract_text(
            b"<html><head><title> Example </title><script>bad()</script></head><body><nav>menu</nav><main>Hello world</main></body></html>",
            "https://example.com/",
        )
        self.assertEqual(title, "Example")
        self.assertEqual(text, "Example Hello world")

    def test_normalizes_url(self):
        self.assertEqual(normalize_url("HTTPS://Example.COM:443/a?q=1#x"), "https://example.com/a?q=1")
