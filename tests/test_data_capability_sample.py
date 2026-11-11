import contextlib
import hashlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from realtime.data_capability_export import build_bundle
from realtime.data_capability_sample import main, run_sample_file
from realtime.product import ProductError
from tests.test_data_capability_export import observation


class SamplePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sf-sample-publication-')
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / 'sample.zip'
        self.record = observation()
        content = build_bundle(self.record)['files']['bundle.zip']
        proof = {k: self.record[k] for k in ('observation_id', 'payload_hash', 'snapshot_hash')}
        proof.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.sample = dict(report=dict(capability='trends_history', status='succeeded'), bundle=content, proof=proof)
        self.executor = SimpleNamespace(service=Mock(), grant_for=Mock(return_value={'approved': True}))
        self.executor.service.job_data.return_value = dict(observation=self.record)

    def publish(self):
        return run_sample_file(self.executor, 'tenant', self.record['observation_id'],
                               maximum_cost='0.5', currency='USD', output=self.destination)

    def test_complete_private_zip_and_preexisting_target_never_calls_source(self):
        with patch('realtime.data_capability_sample.run_sample_step', return_value=self.sample) as step:
            self.assertTrue(self.publish()['published'])
            self.assertEqual(self.destination.read_bytes(), self.sample['bundle'])
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.publish()
            self.assertEqual(step.call_count, 1)
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_revocation_or_corrupt_bundle_never_publishes(self):
        with patch('realtime.data_capability_sample.run_sample_step', return_value=self.sample):
            self.executor.service.job_data.side_effect = ProductError('source_not_admitted', 'private detail', 409)
            with self.assertRaises(ProductError):
                self.publish()
        self.assertEqual(list(self.destination.parent.iterdir()), [])
        self.executor.service.job_data.side_effect = None
        self.sample['proof']['sha256'] = '0' * 64
        with patch('realtime.data_capability_sample.run_sample_step', return_value=self.sample):
            with self.assertRaises(ProductError):
                self.publish()
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_competing_destination_is_preserved(self):
        real_link = os.link
        def competing(source, destination):
            Path(destination).write_bytes(b'other-writer')
            return real_link(source, destination)
        with patch('realtime.data_capability_sample.run_sample_step', return_value=self.sample), patch('os.link', side_effect=competing):
            with self.assertRaises(FileExistsError):
                self.publish()
        self.assertEqual(self.destination.read_bytes(), b'other-writer')
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_no_artifact_on_pending_and_public_directory_refused_before_source(self):
        sample = dict(self.sample, bundle=None, proof=None)
        with patch('realtime.data_capability_sample.run_sample_step', return_value=sample) as step:
            self.assertFalse(self.publish()['published'])
            self.assertEqual(list(self.destination.parent.iterdir()), [])
            self.destination.parent.chmod(0o755)
            try:
                with self.assertRaises(ValueError):
                    self.publish()
            finally:
                self.destination.parent.chmod(0o700)
            self.assertEqual(step.call_count, 1)

    def test_cli_factory_errors_are_sanitized(self):
        output = io.StringIO()
        with patch('importlib.import_module', side_effect=RuntimeError('secret-token-fixture')), contextlib.redirect_stdout(output):
            code = main(['--factory', 'trusted_fixture:factory', '--tenant', 'tenant', '--job', self.record['observation_id'],
                         '--maximum-cost', '0.5', '--currency', 'USD', '--output', str(self.destination)])
        self.assertEqual(code, 1)
        self.assertNotIn('secret-token-fixture', output.getvalue())
        self.assertFalse(self.destination.exists())
