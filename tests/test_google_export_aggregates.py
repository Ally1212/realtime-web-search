"""Restore verification must reject a self-checksummed, false summary."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from realtime.google_data import DATA_VERSION, digest, normalize_request
from realtime.google_domain import canonical_json
from realtime.google_data_export import OCCURRENCE_FIELDS, _occurrence_row, verify_bundle
from tests.test_google_data import request


class ExportAggregateTests(unittest.TestCase):
    def bundle(self, root):
        record=normalize_request(request(response={'organic_results':[
            {'link':'https://example.com/a','position':1},
            {'link':'https://example.com/a?utm_source=x','position':2}]}))
        encoded=canonical_json(record)
        (root/'requests.jsonl').write_text(encoded+'\n')
        with (root/'requests.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['request_id','source','record_json'])
            writer.writerow([record['request_id'],record['source'],encoded])
        with (root/'occurrences.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=OCCURRENCE_FIELDS);writer.writeheader()
            writer.writerows(_occurrence_row(item) for item in record['occurrences'])
        files={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in root.iterdir()}
        return dict(contract_version=DATA_VERSION,files=files,complete_scope=True,
            counts=dict(requests=1,occurrences=2,topics=0,invalid_records=0,
                valid_url_occurrences=2,unique_urls=1,duplicate_url_occurrences=1,unresolved_news_wrappers=0),
            sources={'google_web':1},availability={record['availability']:1},news_time_quality={})

    def write_manifest(self, root, manifest):
        (root/'manifest.json').write_text(json.dumps({**manifest,'manifest_sha256':digest(manifest)}))

    def test_summary_corruption_rejected_even_with_valid_manifest_checksum(self):
        for group, key in [('counts',k) for k in ('topics','invalid_records','valid_url_occurrences',
                'unique_urls','duplicate_url_occurrences','unresolved_news_wrappers')]+[
                ('sources','google_news'),('availability','unknown'),('news_time_quality','verified')]:
            with self.subTest(group=group,key=key),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);manifest=self.bundle(root);manifest[group][key]=99
                self.write_manifest(root,manifest)
                before={p.name:p.read_bytes() for p in root.iterdir()}
                with self.assertRaisesRegex(ValueError,'aggregate'):verify_bundle(root)
                self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})

    def test_valid_duplicate_url_summary_and_temporary_index_cleanup(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as scratch:
            root=Path(folder);self.write_manifest(root,self.bundle(root))
            real=tempfile.TemporaryDirectory
            with patch('realtime.google_data_export.tempfile.TemporaryDirectory',
                       side_effect=lambda **kw:real(dir=scratch,**kw)):
                result=verify_bundle(root)
            self.assertEqual((result['requests'],result['occurrences']),(1,2))
            self.assertEqual(list(Path(scratch).iterdir()),[])
