import os
import unittest

from realtime.keyword_catalog import (
    base_keyword_specs, catalog_keyword_specs, expanded_keyword_specs,
)


class KeywordCatalogTests(unittest.TestCase):
    def test_base_catalog_has_two_languages_and_bounded_size(self):
        specs = base_keyword_specs()
        self.assertEqual(len(specs), 400)
        self.assertEqual(len({spec.key for spec in specs}), 400)
        self.assertEqual({spec.language for spec in specs}, {"en", "zh"})
        self.assertTrue(all(spec.aliases for spec in specs))

    def test_expanded_catalog_adds_stable_relevant_variants(self):
        specs = expanded_keyword_specs()
        self.assertEqual(len(specs), 10_000)
        self.assertEqual(len(specs) - len(base_keyword_specs()), 9_600)
        self.assertEqual(len({spec.key for spec in specs}), len(specs))
        self.assertEqual(len({spec.query.casefold() for spec in specs}), len(specs))
        self.assertEqual({spec.language for spec in specs}, {"en", "zh"})
        self.assertTrue(all(spec.kind == "base" for spec in specs))
        self.assertTrue(all(spec.aliases for spec in specs))
        self.assertIn('base:gdp:en:variant:analysis:latest report', {spec.key for spec in specs})
        self.assertIn('"GDP" latest report', {spec.query for spec in specs})

    def test_variants_are_disabled_by_default_and_enabled_by_environment(self):
        old = os.environ.get("CONTINUOUS_QUERY_VARIANTS_ENABLED")
        try:
            os.environ.pop("CONTINUOUS_QUERY_VARIANTS_ENABLED", None)
            self.assertEqual(catalog_keyword_specs(), base_keyword_specs())
            os.environ["CONTINUOUS_QUERY_VARIANTS_ENABLED"] = "true"
            self.assertEqual(catalog_keyword_specs(), expanded_keyword_specs())
        finally:
            if old is None:
                os.environ.pop("CONTINUOUS_QUERY_VARIANTS_ENABLED", None)
            else:
                os.environ[old] = old
