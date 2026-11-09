import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from scripts.google_data_scale_probe import run_probe, validate_local_dsn


class ScaleProbeGuardTests(unittest.TestCase):
    def test_remote_or_ambiguous_connections_are_rejected_before_connect(self):
        for dsn in ('postgresql://example.com:5432/db', 'postgresql:///db',
                    'host=127.0.0.1 port=5432 hostaddr=20.1.1.1 dbname=db',
                    'host=127.0.0.1 dbname=db'):
            with self.subTest(dsn=dsn), patch('scripts.google_data_scale_probe.psycopg.connect') as connect:
                with self.assertRaises(ValueError): run_probe(dsn, '/unused')
                connect.assert_not_called()

    def test_existing_output_is_not_modified(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('scripts.google_data_scale_probe.psycopg.connect') as connect:
            sentinel = Path(folder) / 'sentinel'
            sentinel.write_text('preserve')
            with self.assertRaises(FileExistsError):
                run_probe('host=127.0.0.1 port=55436 dbname=postgres', folder, occurrences=1)
            self.assertEqual(sentinel.read_text(), 'preserve')
            connect.assert_not_called()


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class ScaleProbeDatabaseTests(unittest.TestCase):
    def test_mixed_source_export_excludes_private_and_drops_only_own_schema(self):
        dsn = os.environ['TEST_DATABASE_URL']
        def schemas():
            with psycopg.connect(dsn) as db:
                return {r[0] for r in db.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'google_scale_%'")}
        before = schemas()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'probe'
            result = run_probe(dsn, target, occurrences=303)
            self.assertTrue(result['synthetic_performance_only'])
            self.assertFalse(result['real_million_row_delivery'])
            self.assertEqual(result['requests'], 4)
            self.assertEqual(result['occurrences'], 303)
            self.assertEqual(result['unique_urls'], 303)
            self.assertEqual(result['private_sentinels_excluded'], 3)
            self.assertEqual(result['api_sample_matches'], 4)
            self.assertEqual(set(result['sources']), {'google_web', 'google_news', 'google_trends'})
            self.assertEqual(json.loads((target / 'result.json').read_text()), result)
        self.assertEqual(schemas(), before)
