import copy
import csv
import io
import json
import unittest
import zipfile

from realtime.google_data import digest
from realtime.google_domain import DomainError
from realtime.google_middle_export import build_middle_web_bundle
from tests import test_google_serp_delivery as fixtures


class MiddleExportTests(unittest.TestCase):
    def packet(self):
        fixture=fixtures.FullRunDeliveryTests()
        payload=fixture.build(fixture.row())['content']['platform_payload']
        return dict(payload=payload, proof=dict(document_id='doc-fixture',version_id='ver-fixture',
                    dataset_id='social_media_raw',source_platform='google_search',version_readable=True,
                    raw_payload_hash='sha256:'+digest(payload)))

    def test_full_payload_and_duplicate_occurrences_survive_all_formats(self):
        packet=self.packet();bundle=build_middle_web_bundle(packet)
        self.assertEqual(bundle['manifest']['occurrence_count'],2)
        self.assertEqual(json.loads(bundle['files']['platform-payload.json']),packet['payload'])
        rows=list(csv.DictReader(io.StringIO(bundle['files']['occurrences.csv'].decode('utf-8-sig'))))
        self.assertEqual([json.loads(row['record_json']) for row in rows],packet['payload']['observation']['occurrences'])
        self.assertEqual([json.loads(line) for line in bundle['files']['occurrences.jsonl'].splitlines()],packet['payload']['observation']['occurrences'])
        with zipfile.ZipFile(io.BytesIO(bundle['zip'])) as archive:
            for name,value in bundle['files'].items():self.assertEqual(archive.read(name),value)
        self.assertEqual(bundle['zip'],build_middle_web_bundle(packet)['zip'])

    def test_hash_identity_and_source_tampering_refused(self):
        original=self.packet()
        for kind in ('hash','source','run','duplicate','platform'):
            packet=copy.deepcopy(original)
            if kind=='hash':packet['proof']['raw_payload_hash']='0'*64
            elif kind=='source':packet['payload']['source_response']['organic_results']=[]
            elif kind=='run':packet['payload']['run_id']='another'
            elif kind=='duplicate':packet['payload']['observation']['occurrences'][1]['occurrence_id']=packet['payload']['observation']['occurrences'][0]['occurrence_id']
            else:packet['proof']['source_platform']='google_news'
            with self.subTest(kind=kind),self.assertRaises(DomainError):build_middle_web_bundle(packet)
