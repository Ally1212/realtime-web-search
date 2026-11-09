import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_guard', Path(__file__).parents[1] / 'scripts/google_data_release_guard.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
BASE = Path(__file__).parents[1] / 'state/google-data-closure-20261106/release-review'


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'realtime'
        self.root.mkdir()
        self.journal = Path(self.temp.name).resolve() / 'backup'
        self.manifest = {'files': [], 'protected_files': {'web': {}}}
        self.content = {}
        for name in ('one.py', 'two.py'):
            before, after = b'OLD = 1\n', b'NEW = 2\n'
            (self.root / name).write_bytes(before)
            archive = 'web/realtime/' + name
            self.manifest['files'].append(dict(host_kind='web', relative_path='realtime/' + name,
                archive_path=archive, before=guard.digest(before), after=guard.digest(after)))
            self.content[archive] = after
        self.snapshot = dict(checked_at=datetime.now(timezone.utc).isoformat(),
            scope='repeatable_read_read_only_preflight',
            settings=dict(content_fetch_enabled=False, continuous_enabled='false'),
            whale_settings=dict(uploads_enabled=False, claims_enabled=False),
            monitor_counts={'sf_monitors': 0}, whale_job_states=[], outbox_states=[{'state':'queued','count':9}],
            **{k:0 for k in ('active_manual_serp_tasks','live_request_leases','live_discovery_query_leases',
                            'live_whale_upload_leases','active_customer_api_jobs')})

    def run_action(self, action):
        return guard.operate(action, self.manifest, self.content, 'web', self.root, self.journal, self.snapshot)

    def test_check_is_read_only(self):
        self.assertEqual(self.run_action('check')['status'], 'checked')
        self.assertFalse(self.journal.exists())
        self.assertEqual((self.root/'one.py').read_bytes(), b'OLD = 1\n')

    def test_install_and_rollback(self):
        self.run_action('install')
        self.assertEqual((self.root/'two.py').read_bytes(), b'NEW = 2\n')
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o700)
        self.run_action('rollback')
        self.assertEqual((self.root/'one.py').read_bytes(), b'OLD = 1\n')

    def test_interrupted_install_can_restore_mixed_files(self):
        original = guard.atomic_write
        def fail_second(path, raw, mode):
            if path == self.root/'two.py':
                raise OSError('injected interruption')
            original(path, raw, mode)
        with patch.object(guard, 'atomic_write', side_effect=fail_second):
            with self.assertRaises(OSError): self.run_action('install')
        self.assertEqual((self.root/'one.py').read_bytes(), b'NEW = 2\n')
        self.assertEqual((self.root/'two.py').read_bytes(), b'OLD = 1\n')
        self.run_action('rollback')
        self.assertTrue(all((self.root/n).read_bytes() == b'OLD = 1\n' for n in ('one.py','two.py')))

    def test_third_party_edit_blocks_all_rollback(self):
        self.run_action('install')
        (self.root/'two.py').write_bytes(b'OTHER = 3\n')
        with self.assertRaises(ValueError): self.run_action('rollback')
        self.assertEqual((self.root/'one.py').read_bytes(), b'NEW = 2\n')

    def test_corrupt_backup_blocks_all_rollback(self):
        self.run_action('install')
        (self.journal/'two.py').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.run_action('rollback')
        self.assertEqual((self.root/'one.py').read_bytes(), b'NEW = 2\n')

    def test_stale_enabled_or_live_work_refuses_install(self):
        changes = [lambda d:d.update(checked_at=(datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()),
                   lambda d:d['settings'].update(continuous_enabled='true'),
                   lambda d:d.update(live_request_leases=1),
                   lambda d:d.update(monitor_counts={'sf_monitors':{'total':0}}),
                   lambda d:d.update(outbox_states=[{'state':'queued','count':10}])]
        for change in changes:
            with self.subTest(change=change):
                original = self.snapshot
                self.snapshot = copy.deepcopy(original)
                change(self.snapshot)
                with self.assertRaises(ValueError): self.run_action('install')
                self.assertFalse(self.journal.exists())
                self.snapshot = original

    def test_symlink_is_not_followed(self):
        (self.root/'two.py').unlink()
        (self.root/'two.py').symlink_to(self.root/'one.py')
        with self.assertRaises(ValueError): self.run_action('install')
        self.assertFalse(self.journal.exists())

    @unittest.skipUnless((BASE/'candidate-bundle.tar').is_file(), 'local reviewed artifact unavailable')
    def test_exact_reviewed_bundle_and_corruption(self):
        manifest, files = guard.load_bundle(BASE/'candidate-bundle.tar')
        self.assertEqual(len(files),4)
        bad=Path(self.temp.name)/'bad.tar'
        bad.write_bytes((BASE/'candidate-bundle.tar').read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'digest mismatch'): guard.load_bundle(bad)


if __name__ == '__main__':
    unittest.main()
