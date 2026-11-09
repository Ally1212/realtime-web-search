"""Pinned artifact, temporary native package and synthetic paused snapshots."""
import copy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import google_feed_capture_release_guard as guard

BASE=Path(__file__).parents[1]/'state/google-data-closure-20261106/feed-capture-freeze'


class FeedReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve();self.root=self.folder/'realtime';self.journal=self.folder/'backup'
        shutil.copytree(BASE/'worker-before-package/realtime',self.root,ignore=shutil.ignore_patterns('__pycache__'))
        self.manifest,self.content=guard.load_review(BASE/'candidate-runtime.tar',BASE/'runtime-manifest.json')
        self.snapshot=json.loads((BASE/'database-before.json').read_text());self.refresh()
        self.original=self.files()

    def refresh(self):self.snapshot['checked_at']=datetime.now(timezone.utc).isoformat()

    def files(self):return {p.name:(p.read_bytes(),p.stat().st_mode & 0o777) for p in self.root.glob('*.py')}

    def run_action(self,action,content=None):
        return guard.operate(action,self.manifest,self.content if content is None else content,self.root,self.journal,self.snapshot)

    def test_default_check_is_read_only(self):
        before=list(self.folder.iterdir());self.assertEqual(self.run_action('check')['status'],'checked')
        self.assertFalse(self.journal.exists());self.assertEqual(before,list(self.folder.iterdir()))
        self.assertEqual(self.original,self.files())

    def test_exact_install_and_idempotent_rollback_remove_only_owned_six_new_modules(self):
        result=self.run_action('install');self.assertEqual(result['status'],'installed')
        self.assertEqual(self.journal.stat().st_mode & 0o777,0o700)
        for name,h in self.manifest['files'].items():self.assertEqual(guard.sha((self.root/name).read_bytes()),h['after'])
        for name in set(guard.NAMES)-{'google_feeds.py'}:
            self.assertTrue((self.root/name).samefile(self.journal/'new'/name))
        self.assertEqual(self.run_action('rollback',{})['status'],'rolled_back');self.assertEqual(self.original,self.files())
        self.run_action('rollback',{});self.assertEqual(self.original,self.files())

    def test_crash_after_new_publish_before_acknowledgment_uses_persistent_anchor(self):
        original=guard.fsync_directory;failed=[]
        def interrupt(path):
            if path==self.root and not failed:
                failed.append(True);raise OSError('crash after link')
            original(path)
        with patch.object(guard,'fsync_directory',side_effect=interrupt):
            with self.assertRaises(OSError):self.run_action('install')
        self.assertEqual(len(set(self.files())-set(self.original)),1)
        self.run_action('rollback',{});self.assertEqual(self.original,self.files())

    def test_backup_interruption_never_changes_native_files_and_can_close_without_backup(self):
        original=guard.atomic_write
        def interrupt(path,raw,mode):
            if path==self.journal/'old/google_feeds.py':raise OSError('backup interrupted')
            original(path,raw,mode)
        with patch.object(guard,'atomic_write',side_effect=interrupt):
            with self.assertRaises(OSError):self.run_action('install')
        self.assertEqual(self.original,self.files())
        self.run_action('rollback',{});self.run_action('rollback',{})
        self.assertEqual(self.original,self.files())

    def test_foreign_identical_new_file_created_at_link_race_is_never_overwritten_or_deleted(self):
        original=guard.os.link;foreign=[]
        def race(src,dst,*a,**kw):
            if not foreign:
                dst.write_bytes(Path(src).read_bytes());foreign.append(Path(dst))
            return original(src,dst,*a,**kw)
        with patch.object(guard.os,'link',side_effect=race):
            with self.assertRaises(FileExistsError):self.run_action('install')
        before=self.files()
        with self.assertRaisesRegex(ValueError,'replaced or edited'):self.run_action('rollback',{})
        self.assertEqual(before,self.files());self.assertTrue(foreign[0].exists())

    def test_foreign_replacement_with_identical_bytes_blocks_all_rollback(self):
        self.run_action('install');path=self.root/'google_domain.py';raw=path.read_bytes()
        path.unlink();path.write_bytes(raw);path.chmod(0o644);before=self.files()
        with self.assertRaisesRegex(ValueError,'replaced or edited'):self.run_action('rollback',{})
        self.assertEqual(before,self.files())

    def test_corrupt_backup_or_changed_protected_file_prevents_every_rollback_mutation(self):
        for target in ('backup','protected','new_content'):
            with self.subTest(target=target):
                if self.journal.exists():shutil.rmtree(self.journal)
                shutil.rmtree(self.root);shutil.copytree(BASE/'worker-before-package/realtime',self.root,ignore=shutil.ignore_patterns('__pycache__'))
                self.run_action('install')
                path=self.journal/'old/google_feeds.py' if target=='backup' else self.root/('free_google.py' if target=='protected' else 'google_domain.py')
                path.write_bytes(b'CHANGED = True\n');before=self.files()
                with self.assertRaises(ValueError):self.run_action('rollback',{})
                self.assertEqual(before,self.files())

    def test_interrupted_rollback_can_retry_without_new_bundle_payload(self):
        self.run_action('install');original=Path.unlink;calls=[]
        def interrupt(path,*a,**kw):
            if path.parent==self.root and path.suffix=='.py':
                calls.append(path)
                if len(calls)==2:raise OSError('unlink interrupted')
            return original(path,*a,**kw)
        with patch.object(Path,'unlink',new=interrupt):
            with self.assertRaises(OSError):self.run_action('rollback',{})
        self.run_action('rollback',{});self.assertEqual(self.original,self.files())

    def test_stale_enabled_or_live_snapshot_rejects_install_before_creating_backup(self):
        for key,value in [('checked_at',(datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()),('live_request_leases',1)]:
            with self.subTest(key=key):
                initial=copy.deepcopy(self.snapshot);self.snapshot[key]=value
                with self.assertRaises(ValueError):self.run_action('install')
                self.assertFalse(self.journal.exists());self.assertEqual(self.original,self.files());self.snapshot=initial
        self.snapshot['settings']['continuous_enabled']='true'
        with self.assertRaises(ValueError):self.run_action('install')
        self.assertFalse(self.journal.exists())

    def test_symlink_extra_module_and_preexisting_new_path_reject_before_backup(self):
        paths=[self.root/'google_domain.py',self.root/'unreviewed.py']
        for path in paths:
            path.symlink_to(self.root/'google_feeds.py')
            with self.assertRaises(ValueError):self.run_action('install')
            self.assertFalse(self.journal.exists());path.unlink()
        (self.root/'google_domain.py').write_bytes(self.content['google_domain.py'])
        with self.assertRaises(ValueError):self.run_action('install')
        self.assertFalse(self.journal.exists())

    def test_manifest_content_and_bundle_changes_are_rejected(self):
        changed=copy.deepcopy(self.manifest);changed['protected_files'].pop('free_google.py')
        with self.assertRaisesRegex(ValueError,'unreviewed manifest'):
            guard.operate('install',changed,self.content,self.root,self.journal,self.snapshot)
        broken=dict(self.content);broken['google_domain.py']+=b'\n# changed'
        with self.assertRaisesRegex(ValueError,'candidate digest'):self.run_action('install',broken)
        bad=self.folder/'bad.tar';bad.write_bytes((BASE/'candidate-runtime.tar').read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'bundle digest'):guard.load_review(bad,BASE/'runtime-manifest.json')
        self.assertFalse(self.journal.exists())

    def test_install_replay_and_journal_from_another_package_refuse_mutation(self):
        self.run_action('install');before=self.files()
        with self.assertRaises(ValueError):self.run_action('install')
        state=json.loads((self.journal/'journal.json').read_text());state['root']=str(self.folder/'other')
        (self.journal/'journal.json').write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'binding differs'):self.run_action('rollback',{})
        self.assertEqual(before,self.files())

    def test_special_source_permissions_refuse_before_backup(self):
        (self.root/'google_feeds.py').chmod(0o4644)
        with self.assertRaisesRegex(ValueError,'special module permissions'):self.run_action('install')
        self.assertFalse(self.journal.exists())

    def test_concurrent_mode_change_before_original_replace_is_not_overwritten(self):
        original=guard.save_state
        def mutate(journal,state):
            original(journal,state)
            if state.get('intent')=='google_feeds.py':(self.root/'google_feeds.py').chmod(0o600)
        with patch.object(guard,'save_state',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'mode changed'):self.run_action('install')
        self.assertEqual((self.root/'google_feeds.py').read_bytes(),self.original['google_feeds.py'][0])
        self.assertEqual((self.root/'google_feeds.py').stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError):self.run_action('rollback',{})

    def test_standalone_kit_default_check_install_and_rollback_without_candidate_bundle(self):
        import subprocess,sys,tarfile,hashlib
        kit_base=BASE.parent/'feed-capture-release-tool'
        metadata=json.loads((kit_base/'kit-manifest.json').read_text())
        kit=kit_base/'release-kit.tar'
        self.assertEqual(hashlib.sha256(kit.read_bytes()).hexdigest(),metadata['sha256'])
        folder=self.folder/'kit';folder.mkdir(mode=0o700)
        with tarfile.open(kit) as tar:
            members=tar.getmembers()
            self.assertEqual({m.name for m in members},set(metadata['files']))
            self.assertTrue(all(m.isfile() and Path(m.name).name==m.name for m in members))
            for m in members:
                raw=tar.extractfile(m).read();self.assertEqual(hashlib.sha256(raw).hexdigest(),metadata['files'][m.name])
                (folder/m.name).write_bytes(raw)
        snapshot=folder/'snapshot.json';snapshot.write_text(json.dumps(self.snapshot))
        command="import sys;from pathlib import Path;import google_feed_capture_release_guard as g;g.NATIVE_ROOT=Path(sys.argv[1]);g.STATE_ROOT=Path(sys.argv[2]);g.main(sys.argv[3:])"
        common=['--manifest','runtime-manifest.json','--snapshot','snapshot.json','--journal',str(self.journal)]
        def cli(args):
            r=subprocess.run([sys.executable,'-B','-c',command,str(self.root),str(self.folder),*args],
                cwd=folder,text=True,capture_output=True,check=True,timeout=15)
            return json.loads(r.stdout)
        self.assertEqual(cli(['--bundle','candidate-runtime.tar',*common])['status'],'checked')
        self.assertFalse(self.journal.exists());self.assertEqual(self.original,self.files())
        self.assertEqual(cli(['install','--bundle','candidate-runtime.tar',*common])['status'],'installed')
        (folder/'candidate-runtime.tar').unlink()
        self.assertEqual(cli(['rollback',*common])['status'],'rolled_back')
        self.assertEqual(self.original,self.files())

    def test_different_file_owner_context_refuses_before_backup(self):
        with patch.object(guard.os,'geteuid',return_value=guard.os.geteuid()+1):
            with self.assertRaisesRegex(ValueError,'source ownership'):self.run_action('install')
        self.assertFalse(self.journal.exists());self.assertEqual(self.original,self.files())

    def test_rollback_under_different_operator_refuses_all_mutation(self):
        self.run_action('install');before=self.files()
        with patch.object(guard.os,'geteuid',return_value=guard.os.geteuid()+1):
            with self.assertRaisesRegex(ValueError,'operator/owner'):self.run_action('rollback',{})
        self.assertEqual(before,self.files())
