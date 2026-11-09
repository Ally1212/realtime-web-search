import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from realtime.google_delivery import read_delivery_status, PROOF_SCHEMA, CHECKS
from tests import test_google_legacy as fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.LegacyPreviewTests.setUpClass.__func__(cls)
        with psycopg.connect(cls.dsn) as db:
            db.execute('CREATE TABLE whale_serp_outbox(id bigserial PRIMARY KEY,dataset_id text,payload jsonb,receipt jsonb,state text)')

    tearDownClass = classmethod(fixtures.LegacyPreviewTests.tearDownClass.__func__)

    def setUp(self):
        fixtures.LegacyPreviewTests.setUp(self)
        with psycopg.connect(self.dsn) as db:
            db.execute('TRUNCATE whale_serp_outbox')
        self.now = datetime.now(timezone.utc)

    def row(self, doc, *, private=False, matched=True):
        cid = uuid4()
        with psycopg.connect(self.dsn) as db:
            db.execute('INSERT INTO whale_serp_outbox(dataset_id,payload,receipt,state) VALUES(%s,%s,%s,%s)',
                ('social_media_raw', Jsonb({'source': {'source_platform': 'google_search'},
                    'discovery': {'metadata': {'campaign_id': str(cid)}}}),
                 Jsonb({'document_id': doc, 'version_id': 'v-' + doc}), 'queued'))
            if private:
                db.execute('INSERT INTO serp_task_options VALUES(%s,%s)', (cid, Jsonb({'customer_private': True})))
        row = dict(document_id=doc, version_id='v-' + doc, dataset_id='social_media_raw', source_platform='google_search',
                   terminal_receipt_status='unknown', **{key: True for key in CHECKS})
        row.update(source_payload_hash_matches=matched, pinned_platform_payload_present=False)
        return cid, row

    def write(self, folder, rows, *, checked_at=None):
        path = Path(folder) / 'proof.json'
        path.write_text(json.dumps(dict(proof_schema=PROOF_SCHEMA, checked_at=(checked_at or self.now).isoformat(), rows=rows)))
        return path

    def test_sample_readback_not_global_completion_and_receipts_not_rewritten(self):
        _, a = self.row('a')
        _, b = self.row('b', matched=False)
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, [a, b])
            result = read_delivery_status(self.dsn, proof_path=path, now=self.now)
        self.assertEqual(result['status'], 'connected_legacy_partial')
        self.assertEqual((result['readable_versions'], result['raw_hash_matches'], result['raw_hash_mismatches']), (2, 1, 1))
        self.assertEqual(result['pinned_platform_payload_records'], 0)
        self.assertEqual(result['receipt_statuses'], {'unknown': 2})
        self.assertFalse(result['formal_observation_reader'])
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute('SELECT DISTINCT state FROM whale_serp_outbox').fetchall(), [('queued',)])

    def test_private_and_later_revoked_campaigns_are_excluded(self):
        cid, public = self.row('public')
        _, private = self.row('private', private=True)
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, [public, private])
            self.assertEqual(read_delivery_status(self.dsn, proof_path=path, now=self.now)['selected_records'], 1)
            with psycopg.connect(self.dsn) as db:
                db.execute('INSERT INTO serp_task_options VALUES(%s,%s)', (cid, Jsonb({'customer_private': True})))
            result = read_delivery_status(self.dsn, proof_path=path, now=self.now)
            self.assertEqual(result['status'], 'not_covered')
            self.assertEqual(result['selected_records'], 0)

    def test_full_run_preparation_excludes_private_requests(self):
        from realtime.google_serp_delivery import prepare_public_web_run
        from realtime.google_domain import DomainError
        public = fixtures.LegacyPreviewTests.insert(self, 'google_web', {'organic_results': [{'link':'https://example.com/a','position':1}]})
        private = fixtures.LegacyPreviewTests.insert(self, 'google_web', {'organic_results': [{'link':'https://private.example/a'}]}, private=True)
        options = dict(dataset_id='social_media_raw', source_name='research', collector_id='collector-one')
        message = prepare_public_web_run(self.dsn, public, **options)
        self.assertEqual(message['content']['platform_payload']['run_id'], public)
        self.assertEqual(len(message['content']['platform_payload']['observation']['occurrences']), 1)
        with self.assertRaises(DomainError): prepare_public_web_run(self.dsn, private, **options)

    def test_full_run_sample_quality_and_private_scope_are_separate(self):
        _, legacy = self.row('a')
        rid = fixtures.LegacyPreviewTests.insert(self, 'google_web', {'organic_results':[{'link':'https://example.com/a'}]})
        checks = ('document_scope_matches','external_identity_matches','pinned_identity_matches',
                  'pinned_payload_present','pinned_payload_matches','raw_hash_matches',
                  'occurrence_order_and_fields_match','source_response_matches','run_id_matches')
        with tempfile.TemporaryDirectory() as folder:
            path=self.write(folder,[legacy]);proof=json.loads(path.read_text())
            proof['full_run_sample']={**{key:True for key in checks},'source_run_id':rid,
                 'checked_at':self.now.isoformat(),'expected_occurrences':1,'actual_occurrences':1,
                 'terminal_status':'accepted','document_quality_status':'low'}
            path.write_text(json.dumps(proof))
            result=read_delivery_status(self.dsn,proof_path=path,now=self.now)
            self.assertTrue(result['full_run_sample']['integrity_verified'])
            self.assertEqual(result['full_run_sample']['status'],'verified_with_quality_limit')
            self.assertFalse(result['formal_observation_reader'])
            with psycopg.connect(self.dsn) as db:
                cid=db.execute('SELECT campaign_id FROM serp_requests WHERE request_id=%s',(rid,)).fetchone()[0]
                db.execute('INSERT INTO serp_task_options VALUES(%s,%s)',(cid,Jsonb({'customer_private':True})))
            self.assertIsNone(read_delivery_status(self.dsn,proof_path=path,now=self.now)['full_run_sample'])

    def test_corrupt_duplicate_and_future_proof_fail_closed_without_database_access(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'proof.json'
            for text in ('[]', '{}', 'not-json'):
                path.write_text(text)
                self.assertEqual(read_delivery_status('invalid', proof_path=path)['status'], 'invalid_evidence')
            _, row = self.row('a')
            self.write(folder, [row, row])
            self.assertEqual(read_delivery_status('invalid', proof_path=path)['status'], 'invalid_evidence')
            self.write(folder, [row], checked_at=self.now + timedelta(hours=1))
            self.assertEqual(read_delivery_status('invalid', proof_path=path, now=self.now)['status'], 'invalid_evidence')

    def test_missing_configuration_does_not_query_or_call_whale_and_stale_proof_is_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'absent'
            with patch('realtime.google_delivery.psycopg.connect') as connect:
                self.assertEqual(read_delivery_status('invalid', proof_path=path)['status'], 'not_configured')
                connect.assert_not_called()
            _, row = self.row('a')
            path = self.write(folder, [row], checked_at=self.now - timedelta(days=2))
            self.assertTrue(read_delivery_status(self.dsn, proof_path=path, now=self.now)['stale'])
