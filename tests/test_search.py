import unittest

from realtime.search import SearchIndex


class SearchTests(unittest.TestCase):
    def test_snippet_escapes_indexed_html(self):
        self.assertEqual(
            SearchIndex._snippet("<script>x</script> <em>hit</em>"),
            "&lt;script&gt;x&lt;/script&gt; <em>hit</em>",
        )
