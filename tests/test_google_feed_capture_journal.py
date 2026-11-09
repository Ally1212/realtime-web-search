"""Synthetic byte/DB failure windows. No source requests or platform calls."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from realtime.google_feed_capture_journal import PrivateFeedCaptureJournal
from realtime.google_raw_capture import PrivateResponseVault
from realtime.google_feeds import collect,FeedCaptureError
from tests import test_google_feed_raw_capture as feed_tests


class FeedCaptureJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'vault';self.key=b'j'*32
        self.now=datetime.now(timezone.utc);self.rid=str(uuid4());self.cid=str(uuid4())
        self.args=dict(tenant_id='fixture-tenant',attempt_id=self.rid,
            expires_at=self.now+timedelta(hours=1),authorization_ref='fixture-policy',clock=lambda:self.now)
        self.journal=self.restore();self.body=b'<rss><channel><unknown>PRIVATE-QUERY</unknown></channel></rss>'
        self.context=dict(request_id=self.rid,source='google_news',query='PRIVATE-QUERY',
            locale='US:en',campaign_id=self.cid,requested_at=self.now.isoformat())

    def restore(self,**changes):
        vault=PrivateResponseVault(self.root,key=self.key)
        return PrivateFeedCaptureJournal(vault,**{**self.args,**changes})

    def begin(self):self.journal.begin(**self.context)

    def capture(self):
        return self.journal(request_id=self.rid,body=self.body,http_status=503,
            **{k:self.context[k] for k in ('source','query','locale')})

    def row(self):return dict(request_id=self.rid,campaign_id=self.cid,source='google_news',
        query='PRIVATE-QUERY',page=1,http_status=503,response={'search_parameters':{'locale':'US:en'}})

    def snapshot(self):return {p.name:p.read_bytes() for p in self.root.iterdir()}

    def test_intent_is_encrypted_exclusive_and_restored_instance_forbids_replay(self):
        self.begin();before=self.snapshot()
        for raw in before.values():self.assertNotIn(b'PRIVATE-QUERY',raw)
        restored=self.restore()
        with self.assertRaisesRegex(ValueError,'replay forbidden'):restored.begin(**self.context)
        reader=Mock();result=restored.reconcile(self.rid,read_saved=reader)
        self.assertEqual(result['status'],'source_outcome_unknown');self.assertFalse(result['replay_authorized'])
        reader.assert_not_called();self.assertEqual(before,self.snapshot())

    def test_capture_without_database_commit_restores_and_never_replays(self):
        self.begin();capture=self.capture();before=self.snapshot()
        result=self.restore().reconcile(self.rid,read_saved=lambda rid:None)
        self.assertEqual(result['status'],'capture_present_database_missing')
        self.assertEqual(result['raw_body_sha256'],hashlib.sha256(self.body).hexdigest())
        self.assertFalse(result['saved_marker_present']);self.assertFalse(result['replay_authorized'])
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.journal.vault.read(tenant_id='fixture-tenant',request_id=self.rid,now=self.now),self.body)

    def test_raw_published_before_captured_marker_is_discovered_without_writing(self):
        self.begin()
        original=self.journal._event
        with patch.object(self.journal,'_event',side_effect=lambda rid,stage,value,**kw:
                          (_ for _ in ()).throw(OSError('crash')) if stage=='captured' else original(rid,stage,value,**kw)):
            with self.assertRaises(OSError):self.capture()
        before=self.snapshot();result=self.restore().reconcile(self.rid,read_saved=lambda rid:self.row())
        self.assertFalse(result['captured_marker_present'])
        self.assertEqual(result['status'],'capture_and_saved_context_verified')
        self.assertEqual(before,self.snapshot());self.assertFalse(result['payload_roundtrip_verified'])

    def test_database_committed_before_saved_marker_is_read_only_reconciled(self):
        self.begin();self.capture();before=self.snapshot();reader=Mock(return_value=self.row())
        result=self.restore().reconcile(self.rid,read_saved=reader)
        reader.assert_called_once_with(self.rid);self.assertFalse(result['saved_marker_present'])
        self.assertEqual(result['status'],'capture_and_saved_context_verified');self.assertEqual(before,self.snapshot())
        self.journal.saved(request_id=self.rid,saved_request_id=self.rid)
        marked=self.snapshot();self.journal.saved(request_id=self.rid,saved_request_id=self.rid)
        self.assertEqual(marked,self.snapshot())
        self.assertTrue(self.restore().reconcile(self.rid,read_saved=reader)['saved_marker_present'])

    def test_wrong_tenant_attempt_key_expiry_or_context_cannot_be_verified(self):
        self.begin();self.capture()
        for changes in ({'tenant_id':'other'},{'attempt_id':str(uuid4())},
                        {'expires_at':self.now+timedelta(hours=2)},{'authorization_ref':'other-policy'},
                        {'clock':lambda:self.now+timedelta(hours=2)}):
            with self.subTest(changes=list(changes)),self.assertRaises(ValueError):
                self.restore(**changes).reconcile(self.rid,read_saved=lambda rid:self.row())
        wrong=PrivateFeedCaptureJournal(PrivateResponseVault(self.root,key=b'k'*32),**self.args)
        with self.assertRaises(ValueError):wrong.reconcile(self.rid,read_saved=lambda rid:self.row())
        for key,value in [('request_id',str(uuid4())),('campaign_id',str(uuid4())),('source','google_trends'),
                          ('query','changed'),('page',2),('http_status',200),('response',{})]:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'context differs'):
                self.restore().reconcile(self.rid,read_saved=lambda rid:{**self.row(),key:value})

    def test_tamper_or_deleted_raw_file_is_not_reported_as_verified(self):
        self.begin();capture=self.capture();path=self.root/(capture['capture_id']+'.sealed')
        raw=path.read_bytes();value=json.loads(raw);value['binding']['http_status']=200;path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.restore().reconcile(self.rid,read_saved=lambda rid:self.row())
        path.unlink()
        with self.assertRaisesRegex(ValueError,'raw response unavailable'):
            self.restore().reconcile(self.rid,read_saved=lambda rid:self.row())

    def test_collector_restart_after_failed_database_does_not_issue_second_request(self):
        d,http,store,serp=feed_tests.FeedRawCaptureTests().setup_case(self.body,503)
        d.raw_feed_response_recorder=self.journal;serp.record.side_effect=RuntimeError('db failed')
        with self.assertRaisesRegex(RuntimeError,'db failed'):
            collect(d,store,serp,self.cid,'google_news','PRIVATE-QUERY','US:en',900)
        # Simulate a fresh process with no thread-local evidence or cache.
        restored=self.restore();other,http2,store2,serp2=feed_tests.FeedRawCaptureTests().setup_case(self.body,503)
        other.raw_feed_response_recorder=restored
        with self.assertRaisesRegex(FeedCaptureError,'intent failed'):
            collect(other,store2,serp2,self.cid,'google_news','PRIVATE-QUERY','US:en',900)
        other._external_get.assert_not_called();serp2.record.assert_not_called();serp2.cache_feed.assert_not_called()
        store2.release_query_lease.assert_called_once()
        result=restored.reconcile(self.rid,read_saved=lambda rid:None)
        self.assertEqual(result['status'],'capture_present_database_missing')

    def test_post_database_marker_failure_is_not_cached_or_replayed(self):
        d,http,store,serp=feed_tests.FeedRawCaptureTests().setup_case(self.body,503)
        d.raw_feed_response_recorder=self.journal
        with patch.object(self.journal,'saved',side_effect=OSError('crash after SQL commit')):
            with self.assertRaisesRegex(FeedCaptureError,'saved marker failed'):
                collect(d,store,serp,self.cid,'google_news','PRIVATE-QUERY','US:en',900)
        serp.record.assert_called_once();serp.cache_feed.assert_not_called();store.release_query_lease.assert_called_once()
        result=self.restore().reconcile(self.rid,read_saved=lambda rid:self.row())
        self.assertEqual(result['status'],'capture_and_saved_context_verified')
        self.assertFalse(result['saved_marker_present'])

    def test_recovery_in_a_separate_process_never_changes_encrypted_evidence(self):
        import subprocess,sys
        self.begin();self.capture();before=self.snapshot()
        child="""import json,sys
from datetime import datetime
from realtime.google_raw_capture import PrivateResponseVault
from realtime.google_feed_capture_journal import PrivateFeedCaptureJournal
a=json.load(sys.stdin)
v=PrivateResponseVault(a['root'],key=bytes.fromhex(a['fixture_key']))
j=PrivateFeedCaptureJournal(v,tenant_id='fixture-tenant',attempt_id=a['rid'],expires_at=datetime.fromisoformat(a['expires_at']),authorization_ref='fixture-policy')
print(json.dumps(j.reconcile(a['rid'],read_saved=lambda rid:a['row'])))
"""
        payload=dict(root=str(self.root),fixture_key=self.key.hex(),rid=self.rid,
            expires_at=self.args['expires_at'].isoformat(),row=self.row())
        result=subprocess.run([sys.executable,'-c',child],input=json.dumps(payload),text=True,
            capture_output=True,check=True,timeout=15)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'capture_and_saved_context_verified')
        self.assertEqual(report['database_writes'],0);self.assertFalse(report['replay_authorized'])
        self.assertEqual(before,self.snapshot())

    def test_concurrent_begin_allows_only_one_intent_and_expiry_cleans_all_local_stages(self):
        from concurrent.futures import ThreadPoolExecutor
        def begin_once(_):
            try:self.restore().begin(**self.context);return True
            except ValueError as exc:
                self.assertIn('replay forbidden',str(exc));return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(begin_once,range(4))),1)
        self.capture();self.journal.saved(request_id=self.rid,saved_request_id=self.rid)
        with self.assertRaises(ValueError):
            self.restore(clock=lambda:self.now+timedelta(hours=2)).reconcile(self.rid,read_saved=lambda rid:self.row())
        result=self.journal.vault.purge_expired(now=self.now+timedelta(hours=2))
        self.assertEqual(result['deleted'],4)
        self.assertEqual(list(self.root.glob('*.sealed')),[])


import os
from tests import test_serp_collection as store_fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class FeedJournalDatabaseTests(unittest.TestCase):
    setUp=store_fixtures.SerpStoreIntegrationTests.setUp
    tearDown=store_fixtures.SerpStoreIntegrationTests.tearDown

    def test_two_sources_recover_actual_commit_windows_without_replaying_or_writing(self):
        news=b'<rss><channel><item><title>News</title><link>https://publisher.example/a</link></item></channel></rss>'
        trends=b'<rss xmlns:ht="urn:test"><channel><item><title>Topic</title><ht:news_item><ht:news_item_url>https://publisher.example/a</ht:news_item_url></ht:news_item></item></channel></rss>'
        for source,body in [('google_news',news),('google_trends',trends)]:
            for window in ('before_insert','after_commit','after_marker'):
                with self.subTest(source=source,window=window),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder).resolve()/'vault';now=datetime.now(timezone.utc);rid=str(uuid4())
                    args=dict(tenant_id='fixture-tenant',attempt_id=rid,expires_at=now+timedelta(hours=1),
                        authorization_ref='fixture-policy',clock=lambda:now)
                    journal=PrivateFeedCaptureJournal(PrivateResponseVault(root,key=b'j'*32),**args)
                    d,http,_,_=feed_tests.FeedRawCaptureTests().setup_case(body,200);d.raw_feed_response_recorder=journal
                    query=source+'-'+window;record=self.serp.record;mark=journal.saved
                    def record_failure(*a,**kw):
                        if window=='after_commit':record(*a,**kw)
                        raise RuntimeError('simulated SQL boundary crash')
                    def marker_failure(**kw):
                        mark(**kw);raise OSError('simulated post-marker crash')
                    if window in ('before_insert','after_commit'):
                        with patch.object(self.serp,'record',side_effect=record_failure):
                            with self.assertRaisesRegex(RuntimeError,'SQL boundary'):
                                collect(d,self.base,self.serp,str(self.cid),source,query,'US:en',900)
                    else:
                        with patch.object(journal,'saved',side_effect=marker_failure):
                            with self.assertRaisesRegex(FeedCaptureError,'saved marker failed'):
                                collect(d,self.base,self.serp,str(self.cid),source,query,'US:en',900)
                    d._external_get.assert_called_once()
                    self.assertEqual(journal.vault.read(tenant_id='fixture-tenant',request_id=rid,now=now),body)
                    def reader(request_id):
                        with self.base.connect() as db:
                            db.execute('SET TRANSACTION READ ONLY')
                            return db.execute('SELECT request_id,campaign_id,source,query,page,http_status,response FROM serp_requests WHERE request_id=%s',(request_id,)).fetchone()
                    before={p.name:p.read_bytes() for p in root.iterdir()}
                    restored=PrivateFeedCaptureJournal(PrivateResponseVault(root,key=b'j'*32),**args)
                    result=restored.reconcile(rid,read_saved=reader)
                    self.assertFalse(result['replay_authorized']);self.assertFalse(result['journal_changed'])
                    self.assertEqual(result['database_writes'],0)
                    self.assertEqual(result['status'],'capture_present_database_missing' if window=='before_insert' else 'capture_and_saved_context_verified')
                    self.assertEqual(result['saved_marker_present'],window=='after_marker')
                    self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
                    other,http2,_,_=feed_tests.FeedRawCaptureTests().setup_case(body,200);other.raw_feed_response_recorder=restored
                    with self.assertRaisesRegex(FeedCaptureError,'intent failed'):
                        collect(other,self.base,self.serp,str(self.cid),source,query,'US:en',900)
                    other._external_get.assert_not_called()
                    with self.base.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) n FROM serp_requests WHERE request_id=%s',(rid,)).fetchone()['n'],0 if window=='before_insert' else 1)
                        self.assertEqual(db.execute('SELECT count(*) n FROM discovery_query_leases').fetchone()['n'],0)
