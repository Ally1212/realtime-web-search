import unittest
from unittest.mock import Mock
from realtime.middle_platform_client import MapsMiddlePlatformClient
from realtime.google_data import digest
from realtime.product import ProductError

class MapsSnapshotHydrationTests(unittest.TestCase):
    def fixture(self):
        client=MapsMiddlePlatformClient('https://middle.test','test-only')
        payload={'place_count':0,'places':[],'platform_metrics':{'place_count':0}}
        hashed='sha256:'+digest(payload)
        row=dict(id='snap_one',document_id='doc_one',version_id='ver_one',dataset_id='google_maps_raw',source_platform='google_maps',snapshot_at='2026-11-13T00:00:00Z',payload_hash='')
        replies=[{'snapshots':[row,row]}, {'versions':[dict(document_id='doc_one',version_id='ver_one',raw_payload_hash=hashed)]}, {'snapshots':[dict(document_id='doc_one',snapshot_id='snap_one',platform_payload=payload)]}]
        client._request=Mock(side_effect=replies)
        return client,payload,hashed,replies
    def test_duplicate_metric_rows_hydrate_one_exact_fixed_snapshot(self):
        client,payload,hashed,_=self.fixture()
        self.assertEqual(client.read_snapshot('doc_one','ver_one',expected_payload=payload,expected_hash=hashed),payload)
        self.assertEqual(client._request.call_args.args[:2],('POST','/v1/snapshots/payloads/batch-get'))
    def test_wrong_snapshot_identity_is_rejected(self):
        client,payload,hashed,replies=self.fixture()
        replies[2]['snapshots'][0]['snapshot_id']='snap_foreign'
        with self.assertRaises(ProductError):client.read_snapshot('doc_one','ver_one',expected_payload=payload,expected_hash=hashed)
    def test_added_payload_field_cannot_pass_the_hash_check(self):
        client,payload,hashed,replies=self.fixture()
        replies[2]['snapshots'][0]['platform_payload']={**payload,'extra':'changed'}
        with self.assertRaises(ProductError):client.read_snapshot('doc_one','ver_one',expected_payload=payload,expected_hash=hashed)
