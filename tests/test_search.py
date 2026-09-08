import unittest
from unittest.mock import Mock, patch

from realtime.search import SearchIndex


class SearchTests(unittest.TestCase):
    @patch("realtime.search.requests.put")
    @patch("realtime.search.requests.head")
    def test_concurrent_index_creation_is_harmless(self, head, put):
        head.return_value.status_code = 404
        put.return_value.status_code = 400
        put.return_value.json.return_value = {
            "error": {"type": "resource_already_exists_exception"}
        }

        SearchIndex("http://search", "pages").ensure_index()

        put.return_value.raise_for_status.assert_not_called()

    @patch("realtime.search.requests.post")
    def test_bulk_index_does_not_persist_body(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"items": [{"index": {}}]}
        post.return_value = response

        SearchIndex("http://search", "pages").bulk_index([{
            "document_id": "hash", "url": "https://example.com",
            "title": "Title", "summary": "Summary", "content": "secret body",
        }])

        self.assertNotIn("secret body", post.call_args.kwargs["data"])

    def test_snippet_escapes_indexed_html(self):
        self.assertEqual(
            SearchIndex._snippet("<script>x</script> <em>hit</em>"),
            "&lt;script&gt;x&lt;/script&gt; <em>hit</em>",
        )
