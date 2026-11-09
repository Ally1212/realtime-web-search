"""Only synthetic private ciphertexts and an isolated fixture registry."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from realtime.google_domain import DomainError
from realtime.google_feed_capture_journal import PrivateFeedCaptureJournal
from realtime.google_raw_capture import PrivateResponseVault
from tests import test_google_feed_capture_rights as rights


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class FeedCaptureCleanupTests(unittest.TestCase):
    setUp=rights.FeedCaptureRightsTests.setUp
    tearDown=rights.FeedCaptureRightsTests.tearDown
    guard=rights.FeedCaptureRightsTests.guard
    capture=rights.FeedCaptureRightsTests.capture
    row=rights.FeedCaptureRightsTests.row
    revoke=rights.FeedCaptureRightsTests.revoke

    def populate(self):
        guard=self.guard();guard.begin(**self.begin_args);self.capture(guard)
        guard.saved(request_id=self.rid,saved_request_id=self.rid)
        return guard

    def snapshot(self):return {p.name:p.read_bytes() for p in self.vault_root.iterdir()}

    def test_revoked_scope_deletes_five_records_and_owned_orphan_preserving_other_tenant_and_attempt(self):
        self.populate();vault=self.journal.vault
        args=dict(http_status=200,expires_at=self.journal.expires_at,authorization_ref=self.source.approval_ref,now=self.now)
        other=vault.put(b'other-tenant',tenant_id='other',request_id=self.rid,**args)
        another=vault.put(b'other-attempt',tenant_id='one',request_id=str(uuid4()),**args)
        preserved={r['capture_id']+'.sealed':(self.vault_root/(r['capture_id']+'.sealed')).read_bytes() for r in (other,another)}
        raw_id=vault._identity('one',self.rid)[0]
        shutil.copy2(self.vault_root/(raw_id+'.sealed'),self.vault_root/'.sealed-owned-orphan')
        self.revoke();guard=self.guard()
        result=guard.cleanup(reason='revoked')
        self.assertEqual((result['deleted_published'],result['deleted_orphans']),(5,1))
        self.assertEqual(result['status'],'current_scope_removed');self.assertFalse(result['backup_erasure_verified'])
        self.assertEqual(preserved,{name:(self.vault_root/name).read_bytes() for name in preserved})
        again=guard.cleanup(reason='revoked');self.assertEqual(again['deleted'],0);self.assertEqual(again['missing_published'],5)

    def test_expiry_cleanup_does_not_require_permission_to_return_expired_content(self):
        self.populate();self.now+=timedelta(hours=3)
        with self.assertRaises(DomainError):self.guard().check(reading=True)
        result=self.guard().cleanup(reason='expired')
        self.assertEqual(result['deleted_published'],5)
        self.assertEqual(list(self.vault_root.glob('*.sealed')),[])

    def test_active_scope_wrong_cause_or_removed_manager_assignment_cannot_delete(self):
        self.populate();before=self.snapshot()
        for reason in ('revoked','expired'):
            with self.assertRaises(DomainError):self.guard().cleanup(reason=reason)
        with self.assertRaises(ValueError):self.guard().cleanup(reason='manual-anything')
        self.assertEqual(before,self.snapshot())
        self.revoke()
        self.registry.save_subject('one','fixture-subject',grant_ids=set(),roles={'manager'},expected_revision=1,actor_ref='fixture',now=self.now)
        with self.assertRaises(DomainError):self.guard().cleanup(reason='revoked')
        self.assertEqual(before,self.snapshot())

    def test_corruption_preflight_removes_nothing_and_wrong_key_is_rejected(self):
        self.populate();self.revoke()
        path=next(self.vault_root.glob('*.sealed'));value=json.loads(path.read_bytes());value['binding']['body_bytes']+=1
        path.write_text(json.dumps(value));before=self.snapshot()
        with self.assertRaises(ValueError):self.guard().cleanup(reason='revoked')
        self.assertEqual(before,self.snapshot())
        wrong=PrivateFeedCaptureJournal(PrivateResponseVault(self.vault_root,key=b'w'*32),tenant_id='one',attempt_id=self.rid,
            expires_at=self.journal.expires_at,authorization_ref=self.source.approval_ref,clock=lambda:self.now)
        guard=self.guard();guard.journal=wrong
        with self.assertRaises(ValueError):guard.cleanup(reason='revoked')
        self.assertEqual(before,self.snapshot())

    def test_interrupted_unlink_preserves_ownership_and_explicit_retry_finishes(self):
        self.populate();self.revoke();guard=self.guard()
        ownership_id=self.journal.vault._identity('one',self.journal._event_id(self.rid,'rights'))[0]
        ownership=self.vault_root/(ownership_id+'.sealed');original=Path.unlink;calls=[]
        def fail_second(path,*a,**kw):
            calls.append(path)
            if len(calls)==2:raise OSError('simulated interrupted cleanup')
            return original(path,*a,**kw)
        with patch.object(Path,'unlink',new=fail_second):
            with self.assertRaises(OSError):guard.cleanup(reason='revoked')
        self.assertTrue(ownership.exists());self.assertEqual(len(list(self.vault_root.glob('*.sealed'))),4)
        result=guard.cleanup(reason='revoked');self.assertEqual(result['deleted'],4);self.assertEqual(result['missing_published'],1)

    def test_unclassified_orphan_is_retained_and_reported_as_partial(self):
        self.populate();(self.vault_root/'.sealed-unclassified').write_bytes(b'unknown-private-temp')
        self.revoke();result=self.guard().cleanup(reason='revoked')
        self.assertEqual(result['deleted_published'],5);self.assertEqual(result['unclassified_orphans'],1)
        self.assertEqual(result['status'],'partial_unclassified_orphans')
        self.assertTrue((self.vault_root/'.sealed-unclassified').exists())

    def test_other_asset_grant_cannot_read_or_clean_this_attempt_even_in_same_tenant(self):
        self.populate()
        other=replace(self.grant,grant_id='other-grant',asset_id='other-asset',revoked=True)
        self.registry.save_grant(other,expected_revision=0,actor_ref='fixture',now=self.now)
        self.registry.save_subject('one','fixture-subject',grant_ids={'other-grant'},roles={'manager'},expected_revision=1,actor_ref='fixture',now=self.now)
        before=self.snapshot()
        with self.assertRaises(ValueError):self.guard(grant_id='other-grant').cleanup(reason='revoked')
        self.registry.save_grant(replace(other,revoked=False,version='2'),expected_revision=1,actor_ref='fixture',now=self.now)
        with self.assertRaises(DomainError):self.guard(grant_id='other-grant').reconcile(self.rid,read_saved=lambda rid:self.row())
        self.assertEqual(before,self.snapshot())

    def test_restored_ciphertexts_are_denied_while_current_registry_remains_revoked(self):
        self.populate();backup=self.snapshot();self.revoke();self.guard().cleanup(reason='revoked')
        for name,raw in backup.items():
            path=self.vault_root/name;path.write_bytes(raw);path.chmod(0o600)
        guard=self.guard()
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=lambda rid:self.row())
        # Restoration itself was a fixture operation. The cleanup primitive
        # does not claim access to or deletion from real backup systems.
        result=guard.cleanup(reason='revoked');self.assertEqual(result['deleted_published'],5)

    def test_unbound_legacy_journal_is_not_silently_claimed_by_new_grant(self):
        self.journal.begin(**self.begin_args)
        self.journal(request_id=self.rid,body=b'legacy fixture',http_status=503,source='google_news',query='q',locale='US:en')
        self.revoke();before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'ownership unavailable'):
            self.guard().cleanup(reason='revoked')
        self.assertEqual(before,self.snapshot())

    def test_registry_restore_waits_until_scoped_cleanup_releases_rights_lock(self):
        import threading,time,psycopg
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import contextmanager
        self.populate();self.revoke();guard=self.guard()
        entered=threading.Event();release=threading.Event();pids={}
        original_remove=self.journal.vault.remove_records;original_connect=self.registry.connect
        def blocked_remove(**kw):
            entered.set()
            if not release.wait(10):raise RuntimeError('test cleanup release missing')
            return original_remove(**kw)
        @contextmanager
        def track_connect():
            with original_connect() as db:
                pids[threading.current_thread().name]=db.execute('SELECT pg_backend_pid() pid').fetchone()['pid']
                yield db
        with patch.object(self.registry,'connect',new=track_connect),patch.object(self.journal.vault,'remove_records',side_effect=blocked_remove):
            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='scope_cleanup') as pool:
                cleaning=pool.submit(guard.cleanup,reason='revoked')
                try:
                    self.assertTrue(entered.wait(5))
                    restoring=pool.submit(self.registry.save_grant,replace(self.grant,version='3'),
                        expected_revision=2,actor_ref='fixture',now=self.now)
                    deadline=time.monotonic()+5;observed=False
                    with psycopg.connect(self.root) as db:
                        while time.monotonic()<deadline:
                            pid=pids.get('scope_cleanup_1')
                            if pid:
                                blockers=db.execute('SELECT pg_blocking_pids(%s)',(pid,)).fetchone()[0]
                                if pids.get('scope_cleanup_0') in blockers:observed=True;break
                            release.wait(0.01)
                    self.assertTrue(observed,'registry writer did not demonstrably wait on cleanup rights lock')
                    self.assertFalse(restoring.done())
                finally:release.set()
                self.assertEqual(cleaning.result(timeout=5)['deleted_published'],5)
                self.assertEqual(restoring.result(timeout=5)['revision'],3)
