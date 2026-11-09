"""No real RSS calls: opt-in entity bytes never enter public SERP responses."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import hashlib
import unittest
from unittest.mock import Mock
from uuid import uuid4

from realtime.discovery import SearchDiscovery
from realtime.google_feeds import collect, FeedCaptureError
from realtime.google_raw_capture import PrivateResponseVault


class FeedRawCaptureTests(unittest.TestCase):
    def setup_case(self, body, status=200):
        discovery=SearchDiscovery(proxy_profile='direct')
        http=Mock(status_code=status,headers={});http.iter_content.return_value=[body]
        discovery._external_get=Mock(return_value=http)
        store,serp=Mock(),Mock();serp.cached_feed.return_value=None
        serp.record.side_effect=lambda *a,**kw:kw.get('upstream_request_id',str(uuid4()))
        return discovery,http,store,serp

    def test_both_sources_keep_exact_unknown_xml_and_non_utf8_failures_privately(self):
        bodies=[(b'<rss xmlns:ht="urn:test"><channel><item><title>Topic</title>'
                 b'<unknown value="original">unmapped &amp; exact</unknown></item></channel></rss>',200),
                (b'not-xml\xff\x00private-secret',200),(b'failure\xff\x00',503)]
        for source in ('google_news','google_trends'):
            for body,status in bodies:
                with self.subTest(source=source,status=status,body=body),tempfile.TemporaryDirectory() as folder:
                    d,http,store,serp=self.setup_case(body,status)
                    vault=PrivateResponseVault(Path(folder).resolve()/'vault',key=b'f'*32)
                    now=datetime.now(timezone.utc)
                    def capture(**kw):
                        self.assertEqual(kw['source'],source)
                        self.assertEqual((kw['query'],kw['locale']),('q','US:en'))
                        return vault.put(kw['body'],tenant_id='fixture-tenant',
                            request_id=kw['request_id'],http_status=kw['http_status'],
                            expires_at=now+timedelta(hours=1),authorization_ref='fixture-policy',now=now)
                    d.raw_feed_response_recorder=capture
                    collect(d,store,serp,str(uuid4()),source,'q','US:en',900)
                    proof=d._thread_state.last_private_feed_observation
                    self.assertEqual(proof['capture_status'],'captured')
                    self.assertEqual(proof['request_id'],proof['saved_request_id'])
                    self.assertEqual(vault.read(tenant_id='fixture-tenant',request_id=proof['request_id'],now=now),body)
                    self.assertNotIn('capture',str(serp.record.call_args.kwargs['response']))
                    self.assertNotIn('private-secret',str(serp.record.call_args.kwargs['response']))
                    if status==503:self.assertEqual(serp.record.call_args.kwargs['classification'],'http_error')
                    http.close.assert_called_once();store.release_query_lease.assert_called_once()

    def test_sink_failure_escapes_without_database_success_or_cache(self):
        d,http,store,serp=self.setup_case(b'<rss><channel/></rss>')
        d.raw_feed_response_recorder=Mock(side_effect=OSError('secret/path/do-not-expose'))
        with self.assertRaisesRegex(FeedCaptureError,'^private feed response capture failed$'):
            collect(d,store,serp,str(uuid4()),'google_news','q','US:en',900)
        serp.record.assert_not_called();serp.cache_feed.assert_not_called()
        self.assertEqual(d._thread_state.last_private_feed_observation['capture_status'],'capture_failed')
        http.close.assert_called_once();store.release_query_lease.assert_called_once()

    def test_cached_response_clears_previous_private_proof_and_never_recaptures(self):
        d,http,store,serp=self.setup_case(b'not requested')
        d._thread_state.last_private_feed_observation={'capture':'stale'}
        d.raw_feed_response_recorder=Mock();serp.cached_feed.return_value={}
        collect(d,store,serp,str(uuid4()),'google_trends','q','US',900)
        self.assertIsNone(d._thread_state.last_private_feed_observation)
        d.raw_feed_response_recorder.assert_not_called();d._external_get.assert_not_called()

    def test_partial_oversize_or_interrupted_response_is_not_claimed_as_complete(self):
        for failure in (ValueError('feed_too_large'),OSError('connection interrupted')):
            with self.subTest(failure=type(failure).__name__):
                d,http,store,serp=self.setup_case(b'unread')
                d.raw_feed_response_recorder=Mock();d._read_limited=Mock(side_effect=failure)
                collect(d,store,serp,str(uuid4()),'google_news','q','US:en',900)
                d.raw_feed_response_recorder.assert_not_called()
                self.assertEqual(d._thread_state.last_private_feed_observation['capture_status'],'not_captured')
                self.assertEqual(serp.record.call_args.kwargs['classification'],'parse_error')
                http.close.assert_called_once()

    def test_no_sink_has_no_private_capture_or_new_request_identity_override(self):
        d,http,store,serp=self.setup_case(b'<rss><channel/></rss>')
        collect(d,store,serp,str(uuid4()),'google_news','q','US:en',900)
        self.assertIsNone(d._thread_state.last_private_feed_observation)
        self.assertNotIn('upstream_request_id',serp.record.call_args.kwargs)

    def test_database_failure_preserves_private_capture_evidence_without_caching(self):
        d,http,store,serp=self.setup_case(b'<rss><channel/></rss>')
        d.raw_feed_response_recorder=Mock(side_effect=lambda **kw:dict(request_id=kw['request_id'],
            http_status=kw['http_status'],body_bytes=len(kw['body']),
            body_sha256=hashlib.sha256(kw['body']).hexdigest(),
            representation='http_entity_bytes_after_content_decoding'))
        serp.record.side_effect=RuntimeError('database failure')
        with self.assertRaisesRegex(RuntimeError,'database failure'):
            collect(d,store,serp,str(uuid4()),'google_news','q','US:en',900)
        proof=d._thread_state.last_private_feed_observation
        self.assertEqual(proof['capture_status'],'captured');self.assertIsNone(proof['saved_request_id'])
        serp.cache_feed.assert_not_called();store.release_query_lease.assert_called_once()

    def test_missing_or_mismatched_sink_proof_is_not_claimed_as_capture(self):
        for proof in (None, {}, {'request_id':'another-attempt'}):
            with self.subTest(proof=proof):
                d,http,store,serp=self.setup_case(b'<rss><channel/></rss>')
                d.raw_feed_response_recorder=Mock(return_value=proof)
                with self.assertRaises(FeedCaptureError):
                    collect(d,store,serp,str(uuid4()),'google_news','q','US:en',900)
                serp.record.assert_not_called();serp.cache_feed.assert_not_called()
                self.assertEqual(d._thread_state.last_private_feed_observation['capture_status'],'capture_failed')


import os
from tests import test_serp_collection as fixtures


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class FeedRawCaptureStoreTests(unittest.TestCase):
    setUp=fixtures.SerpStoreIntegrationTests.setUp
    tearDown=fixtures.SerpStoreIntegrationTests.tearDown

    def test_each_source_success_and_error_binds_ciphertext_to_saved_request_without_public_leak(self):
        from realtime.google_data import normalize_request
        from realtime.serp_console import ConsoleStore
        from realtime.config import Config
        from realtime.google_data_export import export_public_data,verify_bundle
        from realtime.google_legacy import read_public_request
        import json
        ConsoleStore(self.dsn).initialize_console(Config(database_url=self.dsn))
        news=b'<rss><channel><item><title>News</title><link>https://publisher.example/a</link><unknown>unmapped-private-value</unknown></item></channel></rss>'
        trends=b'<rss xmlns:ht="urn:test"><channel><item><title>Topic</title><unknown>unmapped-private-value</unknown><ht:news_item><ht:news_item_url>https://publisher.example/a</ht:news_item_url></ht:news_item></item></channel></rss>'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();vault=PrivateResponseVault(root/'vault',key=b'f'*32)
            now=datetime.now(timezone.utc)
            for source in ('google_news','google_trends'):
                for i,(body,status) in enumerate(((news if source=='google_news' else trends,200),(b'bad-xml\xff\x00',200),(b'failure\xff\x00',503))):
                    with self.subTest(source=source,status=status,i=i):
                        d,http,_,_=FeedRawCaptureTests().setup_case(body,status)
                        def capture(**kw):
                            return vault.put(kw['body'],tenant_id='fixture-tenant',
                                request_id=kw['request_id'],http_status=kw['http_status'],
                                expires_at=now+timedelta(hours=1),authorization_ref='fixture-policy',now=now)
                        d.raw_feed_response_recorder=capture
                        query='case-'+str(i)
                        collect(d,self.base,self.serp,str(self.cid),source,query,'US:en',900)
                        proof=d._thread_state.last_private_feed_observation
                        self.assertEqual(proof['request_id'],proof['saved_request_id'])
                        with self.base.connect() as db:
                            row=db.execute('SELECT request_id,source,query,page,classification,completed_at,response FROM serp_requests WHERE request_id=%s',(proof['request_id'],)).fetchone()
                        self.assertEqual(row['source'],source)
                        self.assertEqual(row['classification'],'results' if i==0 else 'parse_error' if i==1 else 'http_error')
                        self.assertEqual(vault.read(tenant_id='fixture-tenant',request_id=proof['request_id'],now=now),body)
                        record=normalize_request(row)
                        api=read_public_request(self.dsn,proof['request_id'],normalized=True)['request']
                        self.assertEqual(record,api)
                        self.assertNotIn('capture',str(record));self.assertNotIn('unmapped-private-value',str(record))
                        target=root/(source+str(i))
                        export_public_data(self.dsn,target,filters={'source':source,'query':query})
                        self.assertEqual(verify_bundle(target)['requests'],1)
                        self.assertEqual(json.loads((target/'requests.jsonl').read_text()),api)
                        self.assertNotIn(proof['capture']['capture_id'],(target/'requests.jsonl').read_text())
                        # Reusing the persistent cache is a reference to the same
                        # observation, not another capture or source request.
                        collect(d,self.base,self.serp,str(self.cid),source,query,'US:en',900)
                        self.assertIsNone(d._thread_state.last_private_feed_observation)
                        d._external_get.assert_called_once()
