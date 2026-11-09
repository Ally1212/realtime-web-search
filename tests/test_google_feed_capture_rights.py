"""All source/grant approvals here are isolated fixtures, never live rights."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from realtime.google_domain import DomainError
from realtime.google_raw_capture import PrivateResponseVault
from realtime.google_feed_capture_journal import PrivateFeedCaptureJournal
from realtime.google_feed_capture_rights import RightsBoundFeedCapture,FIELDS,ENDPOINTS
from realtime.google_rights import FieldRight,Grant
from realtime.google_feeds import collect,FeedCaptureError
from tests import test_google_registry as fixtures
from tests.test_google_domain import registration
from tests import test_google_feed_raw_capture as feeds


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class FeedCaptureRightsTests(unittest.TestCase):
    def setUp(self):
        fixtures.RegistryDatabaseTests.setUp(self)
        self.now=datetime.now(timezone.utc);self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.rid=str(uuid4());self.cid=str(uuid4());self.vault_root=Path(self.temp.name).resolve()/'vault'
        self.source=registration(endpoint=ENDPOINTS['google_news'],coverage={'countries':['US'],'languages':['en']},
            field_rights=tuple(FieldRight(f,frozenset({'raw_store','internal_display'}),self.now+timedelta(hours=2)) for f in FIELDS),
            production_approved=True)
        self.grant=Grant('one','fixture-asset','fixture-grant','1',frozenset(FIELDS),frozenset({'raw_store','internal_display'}),
            self.now+timedelta(hours=2),source_adapter='fixture',source_version='1')
        self.registry.save_source('one',self.source,expected_revision=0,actor_ref='fixture',now=self.now)
        self.registry.save_grant(self.grant,expected_revision=0,actor_ref='fixture',now=self.now)
        self.registry.save_subject('one','fixture-subject',grant_ids={self.grant.grant_id},roles={'manager'},expected_revision=0,actor_ref='fixture',now=self.now)
        self.journal=PrivateFeedCaptureJournal(PrivateResponseVault(self.vault_root,key=b'r'*32),tenant_id='one',attempt_id=self.rid,
            expires_at=self.now+timedelta(hours=1),authorization_ref=self.source.approval_ref,clock=lambda:self.now)
        self.begin_args=dict(request_id=self.rid,source='google_news',query='q',locale='US:en',campaign_id=self.cid,requested_at=self.now.isoformat())

    tearDown=fixtures.RegistryDatabaseTests.tearDown

    def guard(self,**kw):
        principal=self.registry.principal('one',now=self.now,subject_ref='fixture-subject')
        args=dict(registry=self.registry,principal=principal,grant_id=self.grant.grant_id,adapter_id='fixture',adapter_version='1',
            source='google_news',query='q',locale='US:en')
        return RightsBoundFeedCapture(self.journal,**{**args,**kw})

    def revoke(self):
        self.registry.save_grant(replace(self.grant,revoked=True,version='2'),expected_revision=1,actor_ref='fixture',now=self.now)

    def capture(self,guard):
        return guard(request_id=self.rid,body=b'failure',http_status=503,source='google_news',query='q',locale='US:en')

    def row(self):return dict(request_id=self.rid,campaign_id=self.cid,source='google_news',query='q',page=1,
        http_status=503,response={'search_parameters':{'locale':'US:en'}})

    def test_live_rights_allow_capture_without_registry_mutation_then_revocation_blocks_read(self):
        guard=self.guard();before=self.registry.principal('one',now=self.now,subject_ref='fixture-subject')
        guard.begin(**self.begin_args);self.capture(guard);guard.saved(request_id=self.rid,saved_request_id=self.rid)
        self.assertEqual(guard.reconcile(self.rid,read_saved=lambda rid:self.row())['status'],'capture_and_saved_context_verified')
        self.assertEqual(self.registry.principal('one',now=self.now,subject_ref='fixture-subject'),before)
        self.revoke()
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=lambda rid:self.row())
        with self.assertRaises(DomainError):self.guard().reconcile(self.rid,read_saved=lambda rid:self.row())

    def test_unregistered_field_scope_retention_and_unapproved_production_are_rejected_before_source(self):
        mutations=[replace(self.source,production_approved=False),replace(self.source,admission='revoked'),
            replace(self.source,approval_ref='other'),replace(self.source,endpoint=ENDPOINTS['google_trends']),
            replace(self.source,coverage={'countries':['GB'],'languages':['en']}),
            replace(self.source,coverage={'countries':['US'],'languages':['zh']}),
            replace(self.source,field_rights=(self.source.field_rights[0],)),
            replace(self.source,field_rights=tuple(replace(r,allowed_uses=frozenset({'internal_display'})) for r in self.source.field_rights)),
            replace(self.source,field_rights=tuple(replace(r,retention_deadline=self.now+timedelta(minutes=30)) for r in self.source.field_rights))]
        for revision,source in enumerate(mutations,1):
            with self.subTest(revision=revision):
                self.registry.save_source('one',source,expected_revision=revision,actor_ref='fixture',now=self.now)
                d,http,store,serp=feeds.FeedRawCaptureTests().setup_case(b'failure',503)
                d.raw_feed_response_recorder=self.guard()
                with self.assertRaisesRegex(FeedCaptureError,'intent failed'):
                    collect(d,store,serp,self.cid,'google_news','q','US:en',900)
                d._external_get.assert_not_called();serp.record.assert_not_called()
                self.assertEqual(list(self.vault_root.glob('*.sealed')),[])

    def test_revoked_during_source_request_prevents_raw_capture_and_database_save(self):
        d,http,store,serp=feeds.FeedRawCaptureTests().setup_case(b'failure',503)
        d.raw_feed_response_recorder=self.guard()
        def request(*a,**kw):self.revoke();return http
        d._external_get.side_effect=request
        with self.assertRaisesRegex(FeedCaptureError,'response capture failed'):
            collect(d,store,serp,self.cid,'google_news','q','US:en',900)
        serp.record.assert_not_called();serp.cache_feed.assert_not_called();store.release_query_lease.assert_called_once()
        self.assertEqual(len(list(self.vault_root.glob('*.sealed'))),2)  # pre-request intent only

    def test_revoked_during_reconciliation_is_rechecked_before_return(self):
        guard=self.guard();guard.begin(**self.begin_args);self.capture(guard)
        def reader(rid):self.revoke();return self.row()
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=reader)

    def test_read_use_and_subject_permissions_are_independent_from_capture(self):
        source=replace(self.source,field_rights=tuple(replace(r,allowed_uses=frozenset({'raw_store'})) for r in self.source.field_rights))
        self.registry.save_source('one',source,expected_revision=1,actor_ref='fixture',now=self.now)
        guard=self.guard();guard.begin(**self.begin_args);self.capture(guard)
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=lambda rid:self.row())
        self.registry.save_subject('one','fixture-subject',grant_ids=set(),roles={'manager'},expected_revision=1,actor_ref='fixture',now=self.now)
        with self.assertRaises(DomainError):self.guard().check()

    def test_context_and_grant_expiry_do_not_inherit_unrelated_approval(self):
        for kw in ({'source':'google_trends'},{'query':'another'},{'locale':'SG:en'}):
            guard=self.guard()
            with self.subTest(kw=kw),self.assertRaises(DomainError):guard.begin(**{**self.begin_args,**kw})
        self.registry.save_grant(replace(self.grant,expires_at=self.now+timedelta(minutes=10)),expected_revision=1,actor_ref='fixture',now=self.now)
        with self.assertRaises(DomainError):self.guard().check()

    def test_trending_now_has_independent_endpoint_country_and_source_revocation(self):
        source=replace(self.source,endpoint=ENDPOINTS['google_trends'])
        self.registry.save_source('one',source,expected_revision=1,actor_ref='fixture',now=self.now)
        guard=self.guard(source='google_trends',query='trending-now',locale='US')
        guard.begin(**{**self.begin_args,'source':'google_trends','query':'trending-now','locale':'US'})
        guard(request_id=self.rid,body=b'trend fixture',http_status=200,source='google_trends',query='trending-now',locale='US')
        row={**self.row(),'source':'google_trends','query':'trending-now','http_status':200,'response':{'search_parameters':{'locale':'US'}}}
        self.assertEqual(guard.reconcile(self.rid,read_saved=lambda rid:row)['status'],'capture_and_saved_context_verified')
        self.registry.save_source('one',replace(source,admission='revoked'),expected_revision=2,actor_ref='fixture',now=self.now)
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=lambda rid:row)
        with self.assertRaises(DomainError):self.guard(source='google_trends',query='trending-now',locale='US').check(reading=True)
        with self.assertRaises(ValueError):self.guard(source='google_trends',query='keyword-history',locale='US')

    def test_post_decrypt_gate_prevents_return_when_source_rights_change(self):
        guard=self.guard();guard.begin(**self.begin_args);self.capture(guard)
        def reader(rid):
            self.registry.save_source('one',replace(self.source,admission='revoked'),expected_revision=1,actor_ref='fixture',now=self.now)
            return self.row()
        with self.assertRaises(DomainError):guard.reconcile(self.rid,read_saved=reader)

    def test_registry_outage_never_falls_back_to_cached_authorization(self):
        d,http,store,serp=feeds.FeedRawCaptureTests().setup_case(b'failure',503)
        d.raw_feed_response_recorder=self.guard()
        with patch.object(self.registry,'connect',side_effect=RuntimeError('registry unavailable')):
            with self.assertRaisesRegex(FeedCaptureError,'intent failed'):
                collect(d,store,serp,self.cid,'google_news','q','US:en',900)
        d._external_get.assert_not_called();serp.record.assert_not_called()
        self.assertEqual(list(self.vault_root.glob('*.sealed')),[])

    def test_post_persist_revocation_prevents_capture_reference_return(self):
        guard=self.guard();guard.begin(**self.begin_args)
        original=type(self.journal).__call__
        def persist_then_revoke(journal,**kw):
            value=original(journal,**kw);self.revoke();return value
        with patch.object(type(self.journal),'__call__',new=persist_then_revoke):
            with self.assertRaises(DomainError):self.capture(guard)
        # Ciphertexts written under the pre-write permission may still exist;
        # denying exposure is not a claim of physical or backup deletion.
        self.assertEqual(len(list(self.vault_root.glob('*.sealed'))),4)
        with self.assertRaises(DomainError):self.guard().reconcile(self.rid,read_saved=lambda rid:self.row())
