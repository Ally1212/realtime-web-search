import csv
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from realtime.google_data import normalize_request
from realtime.google_data_export import export_public_data, verify_bundle
from tests import test_google_legacy as legacy_tests


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class GoogleDataExportTests(unittest.TestCase):
    setUpClass = classmethod(legacy_tests.LegacyPreviewTests.setUpClass.__func__)
    tearDownClass = classmethod(legacy_tests.LegacyPreviewTests.tearDownClass.__func__)
    setUp = legacy_tests.LegacyPreviewTests.setUp
    insert = legacy_tests.LegacyPreviewTests.insert

    def test_consistently_checksummed_occurrence_corruption_is_rejected(self):
        import hashlib
        from realtime.google_data import digest
        self.insert('google_web', {'organic_results': [
            {'link': 'https://example.com/a', 'title': '=A', 'position': 1},
            {'link': 'https://example.com/a', 'title': 'B', 'position': 2}]})
        for mutation in ('payload', 'display', 'order', 'extra'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'export'
                export_public_data(self.dsn, target)
                flat = target / 'occurrences.csv'
                with flat.open(newline='') as stream:
                    reader = csv.DictReader(stream)
                    fields, rows = reader.fieldnames, list(reader)
                if mutation == 'payload':
                    item = json.loads(rows[0]['record_json'])
                    item['title'] = 'changed'
                    rows[0]['record_json'] = json.dumps(item)
                elif mutation == 'display':
                    rows[0]['organic_position'] = '99'
                elif mutation == 'order':
                    rows.reverse()
                else:
                    rows.append(rows[0])
                with flat.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                manifest_path = target / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                manifest['files'][flat.name] = dict(
                    bytes=flat.stat().st_size, sha256=hashlib.sha256(flat.read_bytes()).hexdigest())
                manifest.pop('manifest_sha256')
                manifest['manifest_sha256'] = digest(manifest)
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'occurrence'):
                    verify_bundle(target)

    def test_full_export_private_isolation_child_links_and_lossless_csv(self):
        self.insert('google_web', {'organic_results': [
            {'link': 'https://example.com/a?utm_source=x', 'title': '=BAD()', 'position': 1},
            {'link': 'https://example.com/a', 'position': 2}]})
        self.insert('google_trends', {'trending_searches': [{'keyword': 'topic', 'approx_traffic': '100+',
            'articles': [{'link': 'https://example.com/report'}]}]})
        secret = self.insert('google_web', {'organic_results': [{'title': 'PRIVATE'}]}, private=True)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'export'
            result = export_public_data(self.dsn, target)
            self.assertTrue(result['complete_scope'])
            self.assertEqual(result['counts']['requests'], 2)
            self.assertEqual(result['counts']['occurrences'], 3)
            self.assertEqual(result['counts']['unique_urls'], 2)
            self.assertEqual(result['counts']['duplicate_url_occurrences'], 1)
            self.assertNotIn(secret, (target / 'requests.jsonl').read_text())
            self.assertNotIn('PRIVATE', (target / 'requests.csv').read_text())
            with (target / 'occurrences.csv').open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            formula = next(r for r in rows if r['title'])
            self.assertEqual(formula['title'], "'=BAD()")
            self.assertEqual(json.loads(formula['record_json'])['title'], '=BAD()')
            self.assertEqual(verify_bundle(target)['status'], 'verified')
            restored = Path(folder) / 'restored'
            shutil.copytree(target, restored)
            self.assertEqual(verify_bundle(restored), verify_bundle(target))
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual((target / 'requests.jsonl').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                export_public_data(self.dsn, target)
            with (target / 'requests.jsonl').open('a') as stream:
                stream.write('{}\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                verify_bundle(target)

    def test_large_realistic_response_csv_verifies_and_cli_needs_no_database(self):
        import io
        from realtime.google_data_export import main
        self.insert('google_web', {'organic_results': [
            {'link': 'https://example.com/a', 'snippet': 'large snippet ' * 20000}]})
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'large'
            export_public_data(self.dsn, target)
            self.assertGreater((target / 'requests.csv').stat().st_size, 131072)
            self.assertEqual(verify_bundle(target)['requests'], 1)
            with patch.dict(os.environ, {'DATABASE_URL': 'invalid'}), patch('sys.stdout', new_callable=io.StringIO) as stdout:
                self.assertEqual(main([str(target), '--verify']), 0)
                self.assertEqual(json.loads(stdout.getvalue())['status'], 'verified')

    def test_limit_marked_and_failure_not_published_then_retry(self):
        for _ in range(2):
            self.insert('google_web', {'organic_results': [{'link': 'https://example.com/a'}]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'limited'
            result = export_public_data(self.dsn, path, max_requests=1)
            self.assertTrue(result['truncated'])
            self.assertFalse(verify_bundle(path)['complete_scope'])
            failed = Path(folder) / 'failed'
            with patch('realtime.google_data_export.normalize_request', side_effect=RuntimeError('disk or parser failure')):
                with self.assertRaises(RuntimeError):
                    export_public_data(self.dsn, failed)
            self.assertFalse(failed.exists())
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['limited'])
            self.assertTrue(export_public_data(self.dsn, failed)['complete_scope'])

    def test_one_snapshot_excludes_concurrent_insert_and_filters_apply(self):
        self.insert('google_web', {'organic_results': [{'link': 'https://example.com/a'}]})
        self.insert('google_news', {'news_results': [{'link': 'https://example.com/news'}]})
        inserted = False
        def during_export(row):
            nonlocal inserted
            if not inserted:
                inserted = True
                self.insert('google_web', {'organic_results': [{'link': 'https://example.com/later'}]})
            return normalize_request(row)
        with tempfile.TemporaryDirectory() as folder:
            with patch('realtime.google_data_export.normalize_request', side_effect=during_export):
                result = export_public_data(self.dsn, Path(folder) / 'snapshot', filters={'source': 'google_web'})
            self.assertEqual(result['counts']['requests'], 1)
            later = export_public_data(self.dsn, Path(folder) / 'later', filters={'source': 'google_web'})
            self.assertEqual(later['counts']['requests'], 2)

    def test_quality_audit_counts_verified_missing_and_mismatch_without_private_rows(self):
        from scripts.google_data_quality import audit_news_times
        self.insert('google_news', {'news_results': [
            {'date': '2026-11-05T00:00:00Z', 'date_raw': 'Thu, 05 Nov 2026 00:00:00 GMT'},
            {'date': '2026-11-05T01:00:00Z', 'date_raw': 'Thu, 05 Nov 2026 00:00:00 GMT'},
            {'date': '2026-11-05T00:00:00Z'}, {}]})
        self.insert('google_news', {'news_results': [{'date': 'private'}]}, private=True)
        result = audit_news_times(self.dsn)
        self.assertEqual(result['counts']['records'], 4)
        self.assertEqual(result['counts']['verified_rss'], 1)
        self.assertEqual(result['counts']['mismatch'], 1)
        self.assertEqual(result['counts']['unverified_missing_raw'], 1)
        self.assertEqual(result['counts']['missing'], 1)

    def test_invalid_filters_never_create_partial_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for filters in ({'cursor': 'x'}, {'limit': '1'}, {'source': 'gsc'}):
                with self.subTest(filters=filters), self.assertRaises(ValueError):
                    export_public_data(self.dsn, Path(folder) / 'bad', filters=filters)
            self.assertFalse(list(Path(folder).iterdir()))
