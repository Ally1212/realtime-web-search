import copy
import csv
import hashlib
import io
import json
import os
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from realtime.data_capability_subscriptions import CapabilitySubscriptions, batch_files, verify_batch_bundle
from realtime.data_capability_queries import CapabilityRuntime
from realtime.data_capability_export import verify_bundle
from realtime.product import ProductError
from tests import test_data_capability_store as fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class CapabilitySubscriptionTests(unittest.TestCase):
    start = fixtures.CapabilityStoreTests.start
    finish = fixtures.CapabilityStoreTests.finish

    def setUp(self):
        fixtures.CapabilityStoreTests.setUp(self)
        self.runtime = CapabilityRuntime(self.data, lambda t, c: self.grants.get(c) if t == self.tenant else None,
                                         cursor_secret=b'isolated-subscription-test-secret' * 2)
        self.subs = CapabilitySubscriptions(self.runtime, self.tenant)

    def tearDown(self):
        with self.data.connect() as db:
            for table in ('sf_capability_batches', 'sf_capability_subscription_seen'):
                db.execute('DELETE FROM ' + table + ' WHERE subscription_id IN (SELECT id FROM sf_capability_subscriptions WHERE tenant_id=%s)', (self.tenant,))
            db.execute('DELETE FROM sf_capability_subscriptions WHERE tenant_id=%s', (self.tenant,))
        fixtures.CapabilityStoreTests.tearDown(self)

    def create(self, cap='trends_history', **kw):
        return self.subs.create(dict(domain='data', name='fixture', filters=dict(capability=cap), **kw), str(uuid4()))

    def error(self, code, call):
        with self.assertRaises(ProductError) as e:
            call()
        self.assertEqual(e.exception.code, code)

    def test_all_four_complete_delivery_and_file_proofs(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                job = self.finish(self.start(cap))
                sub = self.create(cap)
                self.assertFalse(sub['active'])
                self.assertEqual(self.subs.batch(sub['id'])['reason'], 'paused')
                self.subs.action(sub['id'], 'resume')
                batch = self.subs.batch(sub['id'])
                self.assertEqual(batch['payload']['event_type'], 'new_observation')
                self.assertEqual(batch['observation']['observation_id'], job['result_ref'])
                self.assertEqual(self.subs.status(sub['id'])['pending_observations'], 1)
                before = batch_files(batch)
                verify_batch_bundle(before['batch.zip'], expected_batch_id=batch['batch_id'], expected_batch_sha256=batch['sha256'])
                self.error('corrupt_bundle', lambda: verify_batch_bundle(before['batch.zip'], expected_batch_id=batch['batch_id'], expected_batch_sha256='0' * 64))
                for filename, proof in json.loads(before['manifest.json'])['files'].items():
                    self.assertEqual(proof['sha256'], hashlib.sha256(before[filename]).hexdigest())
                    self.assertEqual(proof['bytes'], len(before[filename]))
                with zipfile.ZipFile(io.BytesIO(before['batch.zip'])) as z:
                    self.assertEqual(z.read('batch.json'), before['batch.json'])
                # Verify inner observation independently with its authoritative hashes.
                verify_bundle(before['observation.zip'], expected_observation_id=batch['payload']['observation_id'], expected_payload_hash=batch['payload']['payload_hash'], expected_snapshot_hash=batch['payload']['snapshot_hash'])
                row = list(csv.DictReader(io.StringIO(before['batch.csv'].decode('utf-8-sig'))))[0]
                self.assertEqual(json.loads(row['envelope_json']), json.loads(before['batch.json']))
                self.subs.action(sub['id'], 'pause')
                self.assertEqual(self.subs.batch(sub['id'])['batch_id'], batch['batch_id'])
                ack = self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256'])
                self.assertTrue(ack['acknowledged_at'])
                self.assertEqual(batch_files(ack), before)
                self.assertEqual(self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256']), ack)
                self.assertEqual(self.subs.status(sub['id'])['pending_observations'], 0)

    def test_parallel_pull_ack_and_restart_return_same_batch(self):
        self.finish(self.start())
        sub = self.create(); self.subs.action(sub['id'], 'resume')
        with ThreadPoolExecutor(4) as pool:
            batches = list(pool.map(lambda _: self.subs.batch(sub['id']), range(4)))
        self.assertEqual(len({b['batch_id'] for b in batches}), 1)
        restarted = CapabilitySubscriptions(self.runtime, self.tenant)
        self.assertEqual(restarted.batch(sub['id']), batches[0])
        b = batches[0]
        with ThreadPoolExecutor(4) as pool:
            acks = list(pool.map(lambda _: restarted.acknowledge(sub['id'], b['batch_id'], b['sha256']), range(4)))
        self.assertEqual(acks, [acks[0]] * 4)
        self.assertEqual(restarted.status(sub['id'])['pending_observations'], 0)

    def test_now_snapshot_and_late_commit_not_timestamp_watermark(self):
        self.finish(self.start())
        sub = self.create(start_mode='now'); self.subs.action(sub['id'], 'resume')
        self.assertEqual(self.subs.status(sub['id'])['pending_observations'], 0)
        late = self.finish(self.start())
        with self.data.connect() as db:
            db.execute('UPDATE sf_capability_observations SET created_at=created_at-interval \'1 hour\' WHERE id=%s', (late['result_ref'],))
        b = self.subs.batch(sub['id'])
        self.assertEqual(b['payload']['observation_id'], late['result_ref'])

    def test_distinct_equal_observations_revision_discard_and_cursor(self):
        one = self.finish(self.start())
        two = self.finish(self.start())
        self.finish(self.start(), supersedes=two['result_ref'], revision='source-revision-2')
        sub = self.create(); self.subs.action(sub['id'], 'resume')
        b = self.subs.batch(sub['id'])
        self.error('checksum_mismatch', lambda: self.subs.acknowledge(sub['id'], b['batch_id'], 'wrong'))
        self.assertEqual(self.subs.status(sub['id'])['pending_observations'], 3)
        self.subs.discard(sub['id'], b['batch_id'])
        self.error('batch_discarded', lambda: self.subs.acknowledge(sub['id'], b['batch_id'], b['sha256']))
        again = self.subs.batch(sub['id'])
        self.assertNotEqual(b['batch_id'], again['batch_id'])
        self.assertEqual(b['payload'], again['payload'])
        events = []
        for _ in range(3):
            b = self.subs.batch(sub['id']); events.append(b['payload'])
            self.subs.acknowledge(sub['id'], b['batch_id'], b['sha256'])
        self.assertEqual(len({e['event_id'] for e in events}), 3)
        self.assertEqual(sum(e['event_type'] == 'revision' for e in events), 1)
        page = self.subs.history(sub['id'], {'limit': '1'}); ids = [page['batches'][0]['id']]
        while page['has_more']:
            page = self.subs.history(sub['id'], {'limit': '1', 'cursor': page['next_cursor']})
            ids.extend(r['id'] for r in page['batches'])
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(self.subs.batch(sub['id'])['reason'], 'no_eligible_observations')
        self.assertEqual(self.subs.batch(sub['id'])['reason'], 'not_due')

    def test_revoke_expiry_corrupt_and_cross_tenant_no_progress(self):
        self.finish(self.start()); sub = self.create(); self.subs.action(sub['id'], 'resume')
        b = self.subs.batch(sub['id'])
        other = CapabilitySubscriptions(self.runtime, 'another')
        self.error('not_found', lambda: other.batch(sub['id'], b['batch_id']))
        self.grants['trends_history']['status'] = 'revoked'
        self.assertEqual(self.subs.status(sub['id'])['pending_observations'], 0)
        self.assertEqual(self.subs.history(sub['id'], {})['batches'], [])
        self.error('source_not_admitted', lambda: self.subs.batch(sub['id']))
        self.error('source_not_admitted', lambda: self.subs.acknowledge(sub['id'], b['batch_id'], b['sha256']))
        self.subs.action(sub['id'], 'pause')
        self.grants['trends_history']['status'] = 'approved'
        self.now += timedelta(days=2)
        self.error('expired', lambda: self.subs.batch(sub['id']))
        self.subs.discard(sub['id'], b['batch_id'])
        self.now -= timedelta(days=2)
        self.subs.action(sub['id'], 'resume'); b = self.subs.batch(sub['id'])
        with self.data.connect() as db:
            db.execute("UPDATE sf_capability_batches SET payload=payload||'{\"capability\":\"aio_answer\"}'::jsonb WHERE id=%s", (b['batch_id'],))
        self.error('corrupt_snapshot', lambda: self.subs.acknowledge(sub['id'], b['batch_id'], b['sha256']))

    def test_raw_revocation_blocks_pending_batch_and_ack_without_advancing(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                self.finish(self.start(cap))
                sub = self.create(cap); self.subs.action(sub['id'], 'resume')
                batch = self.subs.batch(sub['id'])
                self.grants[cap]['raw_response_retention_allowed'] = False
                self.error('raw_retention_not_admitted', lambda: self.subs.batch(sub['id']))
                self.error('raw_retention_not_admitted', lambda: self.subs.batch(sub['id'], batch['batch_id']))
                self.error('raw_retention_not_admitted', lambda: self.subs.acknowledge(sub['id'], batch['batch_id'], batch['sha256']))
                self.grants[cap]['raw_response_retention_allowed'] = True
                self.assertEqual(self.subs.batch(sub['id']), batch)

    def test_idempotency_invalid_input_and_no_side_effects(self):
        body = dict(domain='data', name='fixture', filters=dict(capability='trends_history'))
        sub = self.subs.create(body, 'key')
        self.assertEqual(self.subs.create(body, 'key'), sub)
        self.error('idempotency_conflict', lambda: self.subs.create(dict(body, name='other'), 'key'))
        for bad in (dict(body, mode='new_urls'), dict(body, interval_seconds=True), dict(body, active=True), dict(body, filters={}), dict(body, filters=dict(capability='trends_history', limit='1'))):
            self.error('invalid_parameter', lambda: self.subs.create(bad, str(uuid4())))
        self.assertEqual(len(self.subs.list()['subscriptions']), 1)
        self.assertEqual(self.data.list(self.tenant)['jobs'], [])


    def test_export_rejects_rehashed_mismatched_envelope_and_foreign_cursor(self):
        self.finish(self.start()); sub = self.create(); self.subs.action(sub['id'], 'resume')
        batch = self.subs.batch(sub['id']); files = batch_files(batch)
        fixed = json.loads(files['batch.json']); fixed['observation']['payload']['warnings'].append('tampered')
        files['batch.json'] = json.dumps(fixed).encode()
        manifest = json.loads(files['manifest.json'])
        manifest['files']['batch.json'] = dict(bytes=len(files['batch.json']), sha256=hashlib.sha256(files['batch.json']).hexdigest())
        files['manifest.json'] = json.dumps(manifest).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            for name in ('batch.json', 'batch.jsonl', 'batch.csv', 'observation.zip', 'manifest.json'):
                z.writestr(name, files[name])
        self.error('corrupt_bundle', lambda: verify_batch_bundle(output.getvalue(), expected_batch_id=batch['batch_id'], expected_batch_sha256=batch['sha256']))
        self.subs.discard(sub['id'], batch['batch_id']); self.subs.batch(sub['id'])
        cursor = self.subs.history(sub['id'], {'limit': '1'})['next_cursor']
        other = self.create()
        self.error('invalid_cursor', lambda: self.subs.history(other['id'], dict(cursor=cursor)))


    def test_changed_executable_parameter_policy_cannot_leak_counts_or_history(self):
        self.finish(self.start()); sub = self.create(); self.subs.action(sub['id'], 'resume')
        self.subs.batch(sub['id'])
        def deny(parameters):
            raise ProductError('unsupported_parameter', 'fixture grant changed', 409)
        self.grants['trends_history']['validate_parameters'] = deny
        self.error('unsupported_parameter', lambda: self.subs.status(sub['id']))
        self.error('unsupported_parameter', lambda: self.subs.history(sub['id'], {}))
