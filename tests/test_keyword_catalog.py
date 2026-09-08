import unittest
from datetime import datetime, timezone

from realtime.keyword_catalog import base_keyword_specs, trend_keyword_specs


class KeywordCatalogTests(unittest.TestCase):
    def test_trend_extractor_ignores_invalid_row_types(self):
        self.assertEqual(trend_keyword_specs(["OpenAI news"]), ())  # type: ignore[list-item]

    def test_base_catalog_has_two_languages_and_bounded_size(self):
        specs = base_keyword_specs()
        self.assertEqual(len(specs), 260)
        self.assertEqual(len({spec.key for spec in specs}), 260)
        self.assertEqual({spec.language for spec in specs}, {"en", "zh"})
        self.assertTrue(all(spec.aliases for spec in specs))

    def test_trend_requires_repeated_independent_evidence(self):
        rows = [
            {"title": f"NovaModel AI launch update {i}", "url": f"https://site{i % 3}.example/a{i}"}
            for i in range(5)
        ]
        specs = trend_keyword_specs(rows, now=datetime(2026, 9, 7, tzinfo=timezone.utc))
        self.assertTrue(any("NovaModel" in spec.query for spec in specs))
        self.assertTrue(all(spec.kind == "trend" for spec in specs))

    def test_trend_rejects_non_ai_and_single_source_noise(self):
        rows = [
            {"title": "NovaModel launches today", "url": f"https://site{i}.example/a"}
            for i in range(5)
        ] + [
            {"title": "SoloModel AI update", "url": "https://one.example/a"},
        ]
        specs = trend_keyword_specs(rows)
        self.assertFalse(any("NovaModel" in spec.query or "SoloModel" in spec.query for spec in specs))

    def test_trend_rejects_generic_title_words(self):
        rows = [
            {"title": "English AI report", "url": f"https://site{i}.example/a"}
            for i in range(3)
        ]
        self.assertFalse(any("English" in spec.query for spec in trend_keyword_specs(rows)))

    def test_trend_rejects_pronouns_months_and_locations(self):
        rows = [
            {"title": f"Who September London AI update {i}", "url": f"https://site{i % 3}.example/a{i}"}
            for i in range(5)
        ]
        queries = [spec.query for spec in trend_keyword_specs(rows)]
        self.assertFalse(any(value in query for query in queries for value in ("Who", "September", "London")))

    def test_trend_excludes_catalog_terms(self):
        rows = [
            {"title": "OpenAI AI update", "url": f"https://site{i}.example/a"}
            for i in range(3)
        ]
        self.assertFalse(trend_keyword_specs(rows, excluded={"OpenAI"}))


if __name__ == "__main__":
    unittest.main()
