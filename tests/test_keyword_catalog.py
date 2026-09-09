import unittest

from realtime.keyword_catalog import base_keyword_specs


class KeywordCatalogTests(unittest.TestCase):
    def test_base_catalog_has_two_languages_and_bounded_size(self):
        specs = base_keyword_specs()
        self.assertEqual(len(specs), 260)
        self.assertEqual(len({spec.key for spec in specs}), 260)
        self.assertEqual({spec.language for spec in specs}, {"en", "zh"})
        self.assertTrue(all(spec.aliases for spec in specs))


if __name__ == "__main__":
    unittest.main()
