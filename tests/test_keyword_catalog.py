import os
import unittest
from datetime import datetime, timezone

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

    def test_rolling_catalog_generates_a_fresh_two_day_window(self):
        old = os.environ.get("CONTINUOUS_QUERY_VARIANTS_ENABLED")
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        try:
            os.environ["CONTINUOUS_QUERY_VARIANTS_ENABLED"] = "true"
            specs = catalog_keyword_specs(now)
        finally:
            if old is None:
                os.environ.pop("CONTINUOUS_QUERY_VARIANTS_ENABLED", None)
            else:
                os.environ[old] = old
        self.assertEqual(len(specs), 29_200)
        self.assertEqual(len({spec.key for spec in specs}), len(specs))
        rolling = [spec for spec in specs if ":rolling:" in spec.key]
        self.assertEqual(len(rolling), 19_200)
        self.assertEqual(
            {spec.key.split(":rolling:", 1)[1][:10] for spec in rolling},
            {"2026-09-23", "2026-09-24"},
        )
        self.assertTrue(all(spec.key.count(":") >= 6 for spec in rolling))
        self.assertEqual(
            {spec.query.rsplit(" after:", 1)[0] if " after:" in spec.query else spec.query for spec in rolling[:len(rolling)//2]},
            {spec.query.rsplit(" after:", 1)[0] if " after:" in spec.query else spec.query for spec in rolling[len(rolling)//2:]},
        )
        self.assertGreater(
            min(spec.priority for spec in rolling if ":2026-09-24:" in spec.key),
            max(spec.priority for spec in rolling if ":2026-09-23:" in spec.key),
        )

    def test_variants_are_disabled_by_default_and_enabled_by_environment(self):
        old = os.environ.get("CONTINUOUS_QUERY_VARIANTS_ENABLED")
        try:
            os.environ.pop("CONTINUOUS_QUERY_VARIANTS_ENABLED", None)
            self.assertEqual(catalog_keyword_specs(), base_keyword_specs())
            os.environ["CONTINUOUS_QUERY_VARIANTS_ENABLED"] = "true"
            enabled = catalog_keyword_specs(datetime(2026, 9, 24, tzinfo=timezone.utc))
            self.assertEqual(enabled[:len(expanded_keyword_specs())], expanded_keyword_specs())
            self.assertGreater(len(enabled), len(expanded_keyword_specs()))
        finally:
            if old is None:
                os.environ.pop("CONTINUOUS_QUERY_VARIANTS_ENABLED", None)
            else:
                os.environ[old] = old
