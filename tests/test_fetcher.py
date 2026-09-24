import io
import json
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from scrapy import Request
from scrapy.http import Response

from realtime.fetcher import (_json_ld_article, LiveFetcher, detect_language,
                              extract_pdf_text, extract_response_text,
                              extract_text, normalize_url, relevant_to)
from realtime.crawler import FocusedSpider, is_javascript_shell


class FetcherTests(unittest.TestCase):
    @staticmethod
    def _pdf(text: str) -> bytes:
        stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode()
        objects = [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            (b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
             b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'),
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
            b'<< /Title (AI Research Report) >>',
        ]
        raw = bytearray(b'%PDF-1.4\n')
        offsets = [0]
        for index, item in enumerate(objects, 1):
            offsets.append(len(raw))
            raw.extend(f'{index} 0 obj\n'.encode() + item + b'\nendobj\n')
        xref = len(raw)
        raw.extend(f'xref\n0 {len(objects)+1}\n'.encode())
        raw.extend(b'0000000000 65535 f \n')
        for offset in offsets[1:]:
            raw.extend(f'{offset:010d} 00000 n \n'.encode())
        raw.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R /Info 6 0 R >>\n'
                   f'startxref\n{xref}\n%%EOF\n'.encode())
        return bytes(raw)

    @staticmethod
    def _writer_pdf(pages: int, password: str | None = None) -> bytes:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=612, height=792)
        if password is not None:
            writer.encrypt(password)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_extracts_pdf_text_and_fetch_accepts_pdf(self):
        body = 'Artificial intelligence research and machine learning results. ' * 12
        raw = self._pdf(body)
        title, text = extract_pdf_text(raw, 'https://example.com/report.pdf')
        self.assertEqual(title, 'AI Research Report')
        self.assertGreater(len(text), 500)
        self.assertIn('Artificial intelligence research', text)

        response = Mock(status_code=200, url='https://example.com/report.pdf',
                        headers={'Content-Type': 'application/pdf'})
        fetcher = LiveFetcher('test')
        result = Mock(url=response.url, title='Report', engines=('google_web',))
        with patch('realtime.fetcher.is_public_url', return_value=True), \
             patch.object(fetcher, '_allowed', return_value=True), \
             patch.object(fetcher, '_request', return_value=(response, raw)) as request:
            fetched = fetcher.fetch(result, 'AI report', '2026-09-16T00:00:00+00:00')
        self.assertEqual(fetched.status, 'success')
        self.assertEqual(fetched.document.title, 'AI Research Report')
        request.assert_called_once_with(
            response.url,
            ('text/html', 'application/xhtml+xml', 'pdf', 'octet-stream'),
        )

    def test_extracts_pdf_served_as_generic_binary(self):
        body = 'Artificial intelligence research and machine learning results. ' * 12
        title, text = extract_response_text(
            self._pdf(body),
            'https://example.com/download',
            'application/octet-stream',
        )
        self.assertEqual(title, 'AI Research Report')
        self.assertGreater(len(text), 500)

    def test_rejects_non_pdf_generic_binary(self):
        with self.assertRaisesRegex(ValueError, 'application/octet-stream'):
            extract_response_text(
                b'not a document',
                'https://example.com/download',
                'application/octet-stream',
            )

    def test_scrapy_pipeline_accepts_pdf(self):
        body = 'Artificial intelligence research and machine learning results. ' * 12
        url = 'https://example.com/report.pdf'
        request = Request(url, meta={'source_engines': ('google_web',)})
        response = Response(
            url,
            status=200,
            headers={'Content-Type': 'application/pdf'},
            body=self._pdf(body),
            request=request,
        )
        spider = object.__new__(FocusedSpider)
        spider.page_responses = 0
        spider.browser_requests = 0
        spider.store = Mock()
        spider.campaign_id = 'test'
        spider.config = Mock(trafilatura_enabled=True, crawler_min_content_chars=100)
        spider.terms = ('artificial intelligence',)
        spider.keyword_kind = 'base'
        spider.daily_target = 0
        spider.starting_daily_count = 0
        spider.pending_accepts = 0
        spider._pending_counters = {}
        spider._pending_stages = {}
        spider.query = 'AI research'

        items = list(spider.parse_page(response))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], 'AI Research Report')
        self.assertGreater(len(items[0]['content']), 500)
        spider.store.record_domain_result.assert_called_once_with(url, True)

    def test_scrapy_item_carries_explicit_publication_time(self):
        body = 'Artificial intelligence research and machine learning results. ' * 12
        url = 'https://example.com/report'
        request = Request(url, meta={'source_engines': ('google_web',)})
        response = Response(
            url,
            status=200,
            headers={'Content-Type': 'text/html'},
            body=(
                '<html><head><title>AI policy</title>'
                '<meta property="article:published_time" content="2026-09-01T10:00:00+08:00">'
                f'</head><body>{body}</body></html>'
            ).encode(),
            request=request,
        )
        spider = object.__new__(FocusedSpider)
        spider.page_responses = 0
        spider.browser_requests = 0
        spider.store = Mock()
        spider.campaign_id = 'test'
        spider.config = Mock(trafilatura_enabled=True, crawler_min_content_chars=100)
        spider.terms = ('artificial intelligence',)
        spider.keyword_kind = 'base'
        spider.daily_target = 0
        spider.starting_daily_count = 0
        spider.pending_accepts = 0
        spider._pending_counters = {}
        spider._pending_stages = {}
        spider.query = 'AI policy'

        items = list(spider.parse_page(response))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['published_at'], '2026-09-01T10:00:00+08:00')
        self.assertEqual(items[0]['publication_source'], 'html:datePublished')

    def test_encrypted_pdf_is_recorded_as_permanent_failure(self):
        url = 'https://example.com/encrypted.pdf'
        request = Request(url, meta={'source_engines': ('google_web',)})
        response = Response(
            url,
            status=200,
            headers={'Content-Type': 'application/pdf'},
            body=self._writer_pdf(1, password='secret'),
            request=request,
        )
        spider = object.__new__(FocusedSpider)
        spider.page_responses = 0
        spider.browser_requests = 0
        spider.store = Mock()
        spider.campaign_id = 'test'
        spider.config = Mock(trafilatura_enabled=True, crawler_min_content_chars=100)
        spider.terms = ('artificial intelligence',)
        spider.keyword_kind = 'base'
        spider.daily_target = 0
        spider.starting_daily_count = 0
        spider.pending_accepts = 0
        spider._pending_counters = {}
        spider._pending_stages = {}
        spider.query = 'AI report'

        self.assertEqual(list(spider.parse_page(response)), [])
        spider.store.record_event.assert_called_once_with(
            'test', url, 'permanent_failed', 200, 'PDF 已加密'
        )

    def test_rejects_encrypted_pdf(self):
        raw = self._writer_pdf(1, password='secret')
        with self.assertRaisesRegex(ValueError, 'PDF 已加密'):
            extract_pdf_text(raw, 'https://example.com/encrypted.pdf')

    def test_rejects_pdf_over_page_limit(self):
        raw = self._writer_pdf(301)
        with self.assertRaisesRegex(ValueError, 'PDF 超过 300 页限制'):
            extract_pdf_text(raw, 'https://example.com/too-long.pdf')

    def test_json_ld_article_tolerates_literal_control_characters(self):
        body = 'Artificial intelligence research body. ' * 20
        raw = ('<script type="application/ld+json">'
               '{"@type":"NewsArticle","description":"line\nbreak",'
               '"headline":"AI study","articleBody":' + json.dumps(body) + '}'
               '</script>').encode()
        self.assertEqual(_json_ld_article(raw, 'https://example.com'), ('AI study', body.strip()))

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
        self.assertEqual(title, "AI policy")

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
            normalize_url(
                "HTTPS://Example.COM:443/a?q=1&utm_source=x&srsltid=AfmBOop-tracking#x"
            ),
            "https://example.com/a?q=1",
        )

    def test_relevance_and_language(self):
        self.assertTrue(relevant_to("Singapore released a new AI policy", "News", ("AI policy",)))
        self.assertFalse(relevant_to("Sports results and weather", "News", ("AI policy",)))
        self.assertFalse(relevant_to("Visit Singapore for food and culture", "Singapore", ("Singapore AI policy",)))
        self.assertEqual(detect_language("这是一个中文网页，包含足够多的中文内容用于判断。"), "zh")

    def test_cross_origin_redirect_keeps_cookie_session(self):
        redirect = Mock(
            is_redirect=True, is_permanent_redirect=False,
            headers={'Location': 'https://idp.example/authorize'},
        )
        final = Mock(
            is_redirect=False, is_permanent_redirect=False,
            headers={'Content-Type': 'text/html'},
        )
        final.iter_content.return_value = [b'<html>article</html>']
        session = Mock()
        session.get.side_effect = [redirect, final]
        fetcher = LiveFetcher('test', reuse_sessions=True)
        with patch.object(fetcher, '_session', return_value=session) as choose, \
             patch('realtime.fetcher.is_public_url', return_value=True):
            response, raw = fetcher._request('https://article.example/post', ('text/html',))
        choose.assert_called_once_with('https://article.example/post')
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(response.url, 'https://idp.example/authorize')
        self.assertEqual(raw, b'<html>article</html>')
        session.close.assert_not_called()

    def test_pdf_has_a_larger_bounded_download_limit(self):
        def response(content_type):
            item = Mock(
                is_redirect=False, is_permanent_redirect=False,
                headers={'Content-Type': content_type},
            )
            item.iter_content.return_value = [b'%PDF-', b'1234567890']
            return item

        fetcher = LiveFetcher('test')
        with patch('realtime.fetcher.is_public_url', return_value=True), \
             patch('realtime.fetcher.MAX_DOWNLOAD_BYTES', 10), \
             patch('realtime.fetcher.MAX_PDF_DOWNLOAD_BYTES', 20):
            pdf_session = Mock()
            pdf_session.get.return_value = response('application/pdf')
            with patch('realtime.fetcher.requests.Session', return_value=pdf_session):
                _, raw = fetcher._request('https://example.com/report.pdf', ('pdf',))
            self.assertEqual(raw, b'%PDF-1234567890')

            generic_session = Mock()
            generic_session.get.return_value = response('application/octet-stream')
            with patch('realtime.fetcher.requests.Session', return_value=generic_session):
                _, raw = fetcher._request('https://example.com/download', ('octet-stream',))
            self.assertEqual(raw, b'%PDF-1234567890')

            html_session = Mock()
            html = response('text/html')
            html.iter_content.return_value = [b'<html>', b'1234567890']
            html_session.get.return_value = html
            with patch('realtime.fetcher.requests.Session', return_value=html_session):
                with self.assertRaisesRegex(ValueError, '页面超过'):
                    fetcher._request('https://example.com/article', ('text/html',))
            html.close.assert_called_once()

    def test_one_shot_request_always_closes_session(self):
        session = Mock()
        session.get.side_effect = RuntimeError('transport failed')
        with patch('realtime.fetcher.requests.Session', return_value=session), \
             patch('realtime.fetcher.is_public_url', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'transport failed'):
                LiveFetcher('test')._request('https://article.example/post', ('text/html',))
        session.close.assert_called_once()
