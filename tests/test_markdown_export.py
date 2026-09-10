import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from realtime.discovery import GoogleBlocked, SearchResult
from realtime.fetcher import FetchResult, LiveDocument
from realtime.markdown_export import audit_export, document_markdown, quality_warnings, run_markdown_export


class MarkdownExportTests(unittest.TestCase):
    def test_quality_separates_youtube_shell_and_truncation(self):
        warnings = quality_warnings({"url": "https://www.youtube.com/watch?v=1"}, "About Press Copyright Contact us " * 5)
        self.assertTrue(any("未取得视频" in warning for warning in warnings))
        self.assertTrue(quality_warnings({"content_at_limit": True}, "AI " * 10000))
        self.assertEqual(quality_warnings({"url": "https://example.org/"}, "AI research " * 100), [])
    def test_full_content_and_safe_fence(self):
        content = "完整正文```\n<script>text</script>" * 150
        text = document_markdown({"status": "success", "document": {"content": content, "url": "https://example.org/"}})
        self.assertIn(content, text)
        self.assertIn("````text", text)
        self.assertNotIn("\n```text\n" + content, text)

    def test_failed_result_has_no_fake_body(self):
        text = document_markdown({"status": "blocked", "error": "robots.txt 禁止抓取"})
        self.assertIn("robots.txt 禁止抓取", text)
        self.assertIn("未取得合格正文", text)

    def test_audit_is_repeatable_and_preserves_body(self):
        content = "About Press Copyright Contact us " * 5
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "documents").mkdir()
            (root / "README.md").write_text("# Export\n")
            target = root / "documents/001.md"
            target.write_text(document_markdown({"status": "success", "document": {"url": "https://www.youtube.com/watch?v=1", "content": content}}))
            first = audit_export(root)
            text = target.read_text()
            self.assertEqual(first, audit_export(root))
            self.assertEqual(text, target.read_text())
            self.assertIn(content, text)
            self.assertEqual(first["youtube_navigation_only"], 1)

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "keep.md"
            target.write_text("keep")
            with self.assertRaisesRegex(ValueError, "必须为空"):
                run_markdown_export(SimpleNamespace(query="AI", languages="zh", pages=1, output=directory))
            self.assertEqual(target.read_text(), "keep")

    def test_export_keeps_page_order_duplicates_and_failures(self):
        result = SearchResult("https://example.org/ai", "AI | test", ("google_web",))
        blocked = SearchResult("https://example.org/blocked", "blocked", ("google_web",))
        client = Mock(attempts=[])
        client._discover_google_page.side_effect = [[result, blocked], [result], GoogleBlocked("google_captcha", captcha=True)]
        doc = LiveDocument("id", result.url, "AI", "AI content " * 50, "summary", "AI", ("google_web",), "t1", "t2", 200, "hash", "en")
        def fetch(item, query, discovered):
            return FetchResult("success", item.url, item.title, 200, doc) if item.url == result.url else FetchResult("blocked", item.url, item.title, error="robots")
        with tempfile.TemporaryDirectory() as directory, patch("realtime.markdown_export.CampaignStore"), patch("realtime.markdown_export.ProxyPool"), patch("realtime.markdown_export.SearchDiscovery", return_value=client), patch("realtime.markdown_export.LiveFetcher") as factory:
            factory.return_value.fetch.side_effect = fetch
            run_markdown_export(SimpleNamespace(query="AI", languages="zh", pages=3, output=directory, profile="direct"))
            root = Path(directory)
            summary = (root / "README.md").read_text()
            self.assertIn('"unique_urls": 2', summary)
            self.assertIn('"result_occurrences": 3', summary)
            self.assertIn('"downloaded_documents": 1', summary)
            self.assertIn('"finished": true', summary)
            self.assertEqual(factory.return_value.fetch.call_count, 2)
            first = (root / "search-pages/zh-01.md").read_text()
            self.assertIn("AI &#124; test", first)
            self.assertIn("google_captcha", (root / "search-pages/zh-03.md").read_text())
            self.assertIn(doc.content, (root / "全部正文.md").read_text())
            documents = list((root / "documents").glob("*.md"))
            self.assertEqual(len(documents), 2)
            self.assertIn('"page": 2', next(path for path in documents if '"status": "success"' in path.read_text()).read_text())


if __name__ == "__main__":
    unittest.main()
