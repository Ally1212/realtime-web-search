import unittest
from datetime import datetime, timezone

from realtime.keyword_catalog import base_keyword_specs, trend_keyword_specs


class KeywordCatalogTests(unittest.TestCase):
    def test_base_catalog_has_two_languages_and_bounded_size(self):
        specs = base_keyword_specs()
        self.assertEqual(len(specs), 260)
        self.assertEqual(len({spec.key for spec in specs}), 260)
        self.assertEqual({spec.language for spec in specs}, {"en", "zh"})
        self.assertTrue(all(spec.aliases for spec in specs))

    def test_trend_requires_repeated_independent_evidence(self):
        rows = [
            {"title": "NovaModel AI launches today", "url": f"https://site{i}.example/a"}
            for i in range(3)
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

    def test_trend_excludes_catalog_terms(self):
        rows = [
            {"title": "OpenAI AI update", "url": f"https://site{i}.example/a"}
            for i in range(3)
        ]
        self.assertFalse(trend_keyword_specs(rows, excluded={"OpenAI"}))


if __name__ == "__main__":
    unittest.main()
