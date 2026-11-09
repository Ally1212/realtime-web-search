import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_google_prd_fields import audit


class PrdFieldAuditTests(unittest.TestCase):
    def row(self):
        return dict(id='r1', source='google_web', query='C++ "Brand"', status='results',
                    completed_at='2026-11-09T00:00:00Z', response={
                        'organic_results':[dict(link='https://example.com/a'),dict(link='https://example.com/a')],
                        'serp_features':[dict(type='ai_summary',text='Observed answer')]})

    def save(self, folder, name, row, more=False):
        (folder/name).write_text(json.dumps(dict(results=[dict(status=200,body=dict(rows=[row],has_more=more))])))

    def test_reused_request_deduplicated_but_ordered_occurrences_retained_and_files_hashed(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'input';folder.mkdir();out=Path(d)/'output'
            self.save(folder,'a.json',self.row());self.save(folder,'b.json',self.row())
            result=audit(folder,out)
            self.assertEqual(result['requests'],1)
            self.assertEqual(result['sources']['google_web']['occurrences'],2)
            self.assertEqual(result['counts']['feature_blocks'],1)
            self.assertEqual(result['source_calls'],0)
            record=json.loads((out/'adapted-requests.jsonl').read_text())
            self.assertNotEqual(*[x['occurrence_id']for x in record['occurrences']])
            self.assertIsNone(record['field_adaptation']['run']['observed_page']['value'])
            for filename, expected in json.loads((out/'manifest.json').read_text()).items():
                self.assertEqual(hashlib.sha256((out/filename).read_bytes()).hexdigest(),expected)
            with self.assertRaises(FileExistsError):audit(folder,out)
            self.save(folder,'a.json',dict(self.row(),completed_at='2026-11-09T00:00:00.500Z'))
            (folder/'b.json').unlink()
            bounded = audit(folder, Path(d)/'bounded', since='2026-11-09T00:00:00Z',until='2026-11-09T00:00:01Z')
            self.assertEqual(bounded['requests'],1)

    def test_partial_page_conflicting_identity_and_invalid_time_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'input';folder.mkdir();out=Path(d)/'output'
            self.save(folder,'a.json',self.row(),more=True)
            with self.assertRaises(ValueError):audit(folder,out)
            self.assertFalse(out.exists())
            self.save(folder,'a.json',self.row())
            self.save(folder,'b.json',dict(self.row(),query='changed'))
            with self.assertRaises(ValueError):audit(folder,out)
            self.assertFalse(out.exists())
            with self.assertRaises(ValueError):audit(folder,out,since='2026-11-09T00:00:00')
