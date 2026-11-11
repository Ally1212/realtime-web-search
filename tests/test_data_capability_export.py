import copy
import csv
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from realtime.data_capability_export import build_bundle, encoded, export_job, reconstruct, rows, sha, verify_bundle
from realtime.data_capability_jobs import digest
from realtime.product import ProductError
from tests.test_data_capability_facts import parameters, result


def observation(capability='trends_history'):
    p = parameters(capability)
    payload = result(p)
    record = dict(observation_id='00000000-0000-0000-0000-000000000001', entity_id='fixture',
                  capability=capability, provider='fixture', license_id='fixture-only', parameters=p,
                  revision=None, supersedes=None, payload=payload, payload_hash=digest(payload),
                  raw_hash=digest(payload['raw']['payload']), hash_encoding='sha256-canonical-json-utf8-not-source-bytes',
                  stored_at='2026-11-10T12:00:00+00:00', expires_at='2026-11-11T12:00:00+00:00', platform_delivery='not_submitted')
    record['snapshot_hash'] = digest(record)
    return record


def pin(record):
    return dict(expected_payload_hash=record['payload_hash'], expected_observation_id=record['observation_id'],
                expected_snapshot_hash=record['snapshot_hash'])


def rehash(record):
    record['payload_hash'] = digest(record['payload'])
    record['snapshot_hash'] = digest({k: v for k, v in record.items() if k != 'snapshot_hash'})


class CapabilityExportTests(unittest.TestCase):
    def test_complete_all_formats_and_deterministic_snapshot_zip(self):
        for capability in ('trends_history', 'keyword_volume', 'aio_answer', 'ai_mode_answer'):
            with self.subTest(capability=capability):
                record = observation(capability)
                before = copy.deepcopy(record)
                built = build_bundle(record)
                proof = verify_bundle(built['files']['bundle.zip'], **pin(record))
                self.assertEqual(proof, built['manifest'])
                self.assertEqual(json.loads(built['files']['observation.json']), record)
                self.assertEqual(reconstruct([json.loads(line) for line in built['files']['records.jsonl'].splitlines()]), record)
                self.assertEqual(build_bundle(record)['files']['bundle.zip'], built['files']['bundle.zip'])
                self.assertEqual(record, before)
                self.assertFalse(proof['is_truncated'])
                self.assertEqual(proof['raw_status'], 'stored')

    def test_formula_safe_csv_and_large_answer_without_truncation(self):
        record = observation()
        record['payload']['data']['series'][0]['id'] = '=1+2'
        record['payload']['data']['input_mapping'][0]['record_id'] = '=1+2'
        rehash(record)
        built = build_bundle(record)
        csv_rows = list(csv.DictReader(io.StringIO(built['files']['records.csv'].decode('utf-8-sig'))))
        self.assertEqual(csv_rows[1]['source_id'], "'=1+2")
        self.assertEqual(json.loads(csv_rows[1]['record_json'])['source_id'], '=1+2')
        verify_bundle(built['files']['bundle.zip'], **pin(record))
        record = observation('ai_mode_answer')
        answer = record['payload']['data']['answer']
        answer['answer_text'] = '完整回答\n' * 40000
        answer['blocks'][0]['text'] = answer['answer_text']
        rehash(record)
        built = build_bundle(record)
        verify_bundle(built['files']['bundle.zip'], **pin(record))
        self.assertGreater(len(built['files']['records.csv']), 128 * 1024)
        self.assertEqual(json.loads(built['files']['observation.json'])['payload']['data']['answer']['answer_text'], answer['answer_text'])

    def test_corrupt_files_scope_parent_relations_and_metadata_fail(self):
        record = observation()
        built = build_bundle(record)
        with self.assertRaises(ProductError):
            verify_bundle(built['files']['bundle.zip'][:-50], **pin(record))
        with self.assertRaises(ProductError):
            verify_bundle(built['files']['bundle.zip'], **{**pin(record), 'expected_payload_hash': 'wrong'})
        def corrupt(target):
            files = {k: v for k, v in built['files'].items() if k != 'bundle.zip'}
            manifest = copy.deepcopy(built['manifest'])
            target(files, manifest)
            for name in manifest['files']:
                manifest['files'][name] = dict(bytes=len(files[name]), sha256=sha(files[name]))
            files['manifest.json'] = encoded(manifest)
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            with self.assertRaises(ProductError):
                verify_bundle(output.getvalue(), **pin(record))
        def bad_parent(files, manifest):
            records = list(rows(record))
            records[2]['parent_id'] = 'not-parent'
            files['records.jsonl'] = b''.join(encoded(r) + b'\n' for r in records)
        corrupt(bad_parent)
        corrupt(lambda files, manifest: manifest['record_counts'].update(point=100))
        def changed_metadata(files, manifest):
            altered = copy.deepcopy(record)
            altered['expires_at'] = '2099-01-01T00:00:00Z'
            altered['snapshot_hash'] = digest({k: v for k, v in altered.items() if k != 'snapshot_hash'})
            files['observation.json'] = encoded(altered)
        corrupt(changed_metadata)

    def test_resource_limit_and_concurrency_fail_without_partial_bundle(self):
        from realtime.data_capability_export import _EXPORT_SLOTS
        with patch('realtime.data_capability_export.MAX_EXPORT_BYTES', 100):
            with self.assertRaises(ProductError) as e:
                build_bundle(observation())
            self.assertEqual(e.exception.status, 413)
        self.assertTrue(_EXPORT_SLOTS.acquire(blocking=False))
        self.assertTrue(_EXPORT_SLOTS.acquire(blocking=False))
        try:
            with self.assertRaises(ProductError) as e:
                build_bundle(observation())
            self.assertEqual(e.exception.status, 429)
        finally:
            _EXPORT_SLOTS.release()
            _EXPORT_SLOTS.release()

    def test_each_export_rechecks_service_authorization(self):
        from unittest.mock import Mock
        service = Mock()
        service.job_data.return_value = dict(observation=observation())
        data, proof = export_job(service, 'tenant', 'job', 'grant', 'zip')
        self.assertEqual(sha(data), proof['sha256'])
        self.assertEqual(proof['snapshot_hash'], observation()['snapshot_hash'])
        self.assertEqual(service.job_data.call_count,2)
        for call in service.job_data.call_args_list:
            self.assertEqual(call.args,('tenant','job','grant'))
        service.job_data.side_effect=[dict(observation=observation()),ProductError('expired','fixture revoked during generation',410)]
        with self.assertRaises(ProductError) as error:
            export_job(service,'tenant','job','grant','zip')
        self.assertEqual(error.exception.status,410)
        service.job_data.side_effect = ProductError('expired', 'fixture revoked', 410)
        with self.assertRaises(ProductError):
            export_job(service, 'tenant', 'job', 'grant', 'zip')
