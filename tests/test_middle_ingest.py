import json
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock, patch
from realtime.config import Config
from realtime.middle_ingest import WhaleLinkClient, link_message, task_spec
class MessageTests(unittest.TestCase):
    def test_retry_after_seconds_http_date_and_invalid_headers(self):
        from realtime.middle_ingest import retry_delay
        now=datetime(2026,11,6,0,0,tzinfo=timezone.utc)
        with patch('realtime.middle_ingest.random.uniform',return_value=0):
            self.assertEqual(retry_delay(1,'120',now=now),120)
            self.assertEqual(retry_delay(1,'Fri, 06 Nov 2026 00:02:00 GMT',now=now),120)
            for value in ('invalid','nan','inf','-10'):
                self.assertEqual(retry_delay(1,value,now=now),2)
            self.assertEqual(retry_delay(1,minimum=5),5)

    def test_run_event_identity_is_separate_from_url_and_payload(self):
        row=dict(query='same query',source='google_web',page=1,completed_at=datetime.now(timezone.utc),
                 request_id=str(uuid4()),campaign_id=str(uuid4()),response={'search_parameters':{'gl':'us'}})
        item=dict(link='https://example.org/a',title='Same title',snippet='Same snippet',position=1)
        key,message=link_message(row,item,Config())
        self.assertEqual(link_message(dict(row),dict(item),Config()),(key,message))
        newer={**row,'request_id':str(uuid4())}
        next_key,next_message=link_message(newer,item,Config())
        self.assertNotEqual(next_key,key)
        self.assertEqual(next_message['content']['external_content_id'],message['content']['external_content_id'])
        changed_key,changed_message=link_message(row,{**item,'snippet':'changed'},Config())
        self.assertEqual(changed_key,key)
        self.assertNotEqual(changed_message['source']['payload_hash'],message['source']['payload_hash'])

    def test_search_result_preserves_fields_without_invented_body_or_publication(self):
        row=dict(query='research',source='google_web',page=1,completed_at=datetime.now(timezone.utc),
                 request_id=str(uuid4()),campaign_id=str(uuid4()),response={'search_parameters':{'q':'research'}})
        item=dict(link='https://example.org/a?srsltid=x',title='Real title',snippet='Actual snippet',position=2)
        key,msg=link_message(row,item,Config(),'ctask_1')
        self.assertEqual(msg['content']['canonical_url'],'https://example.org/a')
        self.assertEqual(msg['content']['content_type'],'search_result')
        self.assertEqual(msg['source']['collection_task_id'],'ctask_1')
        self.assertNotIn('body_text',msg['content']);self.assertNotIn('published_at',msg['content'])
        self.assertEqual(msg['discovery']['metadata']['result']['snippet'],'Actual snippet')
        self.assertEqual(msg['provided_capabilities'],['identity','title'])
        self.assertEqual(link_message(row,item,Config(),'ctask_1')[0],key)
        row.update(request_id=str(uuid4()),campaign_id=str(uuid4()),completed_at=datetime.now(timezone.utc))
        self.assertNotEqual(link_message(row,item,Config(),'ctask_1')[0],key)
        self.assertNotEqual(link_message(row,item,Config(),'ctask_2')[0],key)
        item['date']='2026-10-01T00:00:00Z'
        self.assertEqual(link_message(row,item,Config())[1]['content']['published_at'],'2026-10-01T00:00:00+00:00')

    def test_explicit_https_endpoint_and_redirect_refusal(self):
        from realtime.middle_ingest import collector_endpoint
        config=Config(whale_collector_api_key='test-secret')
        with patch('realtime.middle_ingest.Path.exists',return_value=True), patch('realtime.middle_ingest.Path.read_text',return_value='{"base_url":"https://www.marsyoo.com"}'):
            self.assertEqual(collector_endpoint(config),'https://www.marsyoo.com')
            session=Mock()
            session.post.return_value=Mock(status_code=302,headers={})
            client=WhaleLinkClient(config,session=session)
            with self.assertRaisesRegex(RuntimeError,'Whale HTTP 302'):client.post('/v1/documents/validate',[])
            self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
            self.assertEqual(session.post.call_args.args[0],'https://www.marsyoo.com/v1/documents/validate')
        for value in ('http://example.com','https://user:secret@example.com','https://example.com/?key=x'):
            with self.subTest(value=value), patch('realtime.middle_ingest.Path.exists',return_value=True), patch('realtime.middle_ingest.Path.read_text',return_value=json.dumps({'base_url':value})), self.assertRaises(ValueError):collector_endpoint(config)

    def test_legacy_tasks_rejected_even_if_old_config_names_them(self):
        c = Config(whale_supported_task_types=('keyword_search', 'backfill', 'content_detail', 'live_query'))
        for kind in ('keyword_search', 'backfill', 'content_detail'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                task_spec(dict(dataset_id=c.whale_dataset_id, source_platform=c.whale_source_platform,
                               task_type=kind, payload={'keyword': 'one'}), c)
