import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_images import fetch_page, image_result
from realtime.free_google import GoogleTransport, GoogleBlocked
from realtime.google_data import normalize_request
from realtime.serp_console import ConsoleStore
from realtime.campaign_store import CampaignStore, _INITIALIZED_DSNS, _POOLS
from realtime.config import Config
from realtime.google_legacy import read_public_requests as browse, read_public_request as detail
from realtime.google_data_export import export_public_data, verify_bundle as verify_export
from realtime.product import parameters
from realtime.serp_presentation import user_result_rows


def item():
    return dict(id='i_a',engine='google',type='image',rank=2,title='Actual image',
                image=dict(url='https://cdn.example.com/a.jpg?w=100&utm_source=content',
                    thumbnail='https://cdn.example.com/t.jpg',width=1200,height=800),
                source=dict(page_url='https://example.com/article',domain='example.com'))


class ImagesTests(unittest.TestCase):
    def transport(self):
        return GoogleTransport(10,'en','',openserp_url='http://example.test:7000')

    def run_fetch(self,items,**changes):
        transport=self.transport()
        def get(url,**kw):
            response=Mock(status_code=200,content=b'{}',headers={'X-Request-ID':kw['headers']['X-Request-ID']})
            data=dict(query=dict(text='cats',engines_requested=['google']),
                meta=dict(engines_failed=[],request_id=kw['headers']['X-Request-ID'],version='test'),
                results=items,pagination=dict(page=1,has_more=False,next_start=0))
            data.update(changes);response.json.return_value=data
            self.assertTrue(url.endswith('/google/image'))
            self.assertFalse(kw['allow_redirects'])
            self.assertEqual(kw['params']['limit'],20)
            return response
        session=Mock();session.get.side_effect=get
        with patch('realtime.google_images.requests.Session',return_value=session):
            results=fetch_page(transport,'cats',1,None,'')
        session.close.assert_called_once()
        return results,transport.last_serp_response

    def test_nested_contract_and_duplicate_occurrences(self):
        a=item();b=copy.deepcopy(a);b['source']['page_url']='https://example.org/other'
        results,response=self.run_fetch([a,b])
        self.assertEqual(len(results),2)
        self.assertEqual([x['observed_order'] for x in response['image_results']],[1,2])
        self.assertNotEqual(*[x['source_url'] for x in response['image_results']])
        self.assertEqual(response['image_results'][0]['width'],1200)
        self.assertIsNone(response['image_results'][0]['rank_absolute'])
        self.assertEqual(response['organic_results'],[])
        self.assertEqual(response['upstream_response']['results'],[a,b])

    def test_empty_failure_partial_and_missing_fields(self):
        self.assertEqual(self.run_fetch([])[0],[])
        bad=item();bad['image']['url']='http://127.0.0.1/private'
        with self.assertRaises(GoogleBlocked):self.run_fetch([bad])
        _,p=self.run_fetch([item(),bad]);self.assertTrue(p['coverage']['partial'])
        self.assertEqual(len(p['invalid_image_results']),1)
        wrong=item();wrong['engine']='bing'
        with self.assertRaises(GoogleBlocked):self.run_fetch([wrong])
        with self.assertRaises(GoogleBlocked):self.run_fetch([],query={'text':'wrong'})
        raw=item();raw['image'].pop('width');raw['image'].pop('height')
        mapped=image_result(raw);self.assertIsNone(mapped['width']);self.assertIsNone(mapped['mime_type'])

    def test_image_identity_and_api_keep_source_page_separate(self):
        mapped=image_result(item())
        row=dict(request_id=str(uuid4()),source='google_images',query='cats',page=1,
                 classification='results',completed_at=datetime.now(timezone.utc),
                 response={'image_results':[mapped]})
        result=normalize_request(row)['occurrences'][0]
        self.assertIsNone(result['article_url'])
        self.assertEqual(result['source_page_url'],'https://example.com/article')
        self.assertEqual(result['url']['normalized_url'],item()['image']['url'])
        self.assertEqual(parameters({'query':'cats','source':'google_images'})['depth'],20)
        csvrow=next(user_result_rows(dict(data=mapped,source='google_images')))
        self.assertEqual(csvrow['图片来源网页'],'https://example.com/article')
        self.assertIsNone(csvrow['自然排名'])

    def test_public_url_encoding_preserved_and_secret_queries_rejected(self):
        raw=item();raw['image']['url']='https://cdn.example.com/a.jpg?label=red%20panda&w=100'
        self.assertEqual(image_result(raw)['image_url'],raw['image']['url'])
        raw['image']['url']='https://cdn.example.com/a.jpg?access_token=private-secret'
        mapped=image_result(raw)
        self.assertIsNone(mapped['image_url'])
        self.assertNotIn('private-secret',str(mapped))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class ImagesDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='images_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn=make_conninfo(self.root,options=f'-c search_path={self.schema}')
        self.base=CampaignStore(self.dsn);self.console=ConsoleStore(self.dsn)
        self.console.initialize();self.console.initialize_console(Config(database_url=self.dsn))

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0]==self.dsn:_POOLS.pop(key).close()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_persistence_browse_exports_and_private_isolation(self):
        cid=self.console.create_task(dict(keywords=['cats'],sources=['google_images'],start=False),Config(database_url=self.dsn))['campaign_id']
        mapped=image_result(item())
        response={'image_results':[mapped,copy.deepcopy(mapped)],'organic_results':[]}
        rid=self.console.record(cid,source='google_images',provider='openserp',query='cats',page=1,
            language='en',region='US',classification='results',http_status=200,elapsed_seconds=1,response=response)
        saved=self.console.request(rid)
        self.assertEqual(saved['new_unique_links'],1)
        self.assertEqual([r['is_new_url'] for r in saved['image_results']],[True,False])
        self.assertEqual(saved['image_results'][0]['normalized_link'],item()['image']['url'])
        for exact in ('true','false'):
            data=self.console.browse({'source':'google_images','count_total':exact})
            self.assertEqual(len(data['rows']),2)
        legacy=browse(self.dsn,{'source':'google_images'})
        self.assertEqual(legacy['requests'][0]['items'][0]['kind'],'image')
        self.assertEqual(detail(self.dsn,rid)['request']['source'],'google_images')
        from realtime.analytics import initialize_projection, sync_projection
        with self.base.connect() as db:initialize_projection(db)
        self.assertEqual(sync_projection(self.base),0)  # Not a Web ranking observation.
        from realtime.whale_serp import WhaleSerpStore
        whale=WhaleSerpStore(self.dsn);whale.initialize()
        config=Config(database_url=self.dsn)
        with self.assertRaises(ValueError):whale.enqueue({'source':'google_images'},config)
        with self.assertRaises(ValueError):whale.enqueue({'new_only':False,'start':False},config)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM whale_serp_outbox').fetchone()['n'],0)
        with tempfile.TemporaryDirectory() as tmp:
            export_public_data(self.dsn,Path(tmp)/'bundle',filters={'source':'google_images'})
            verify_export(Path(tmp)/'bundle')
        with self.base.connect() as db:
            db.execute("UPDATE serp_task_options SET options=options || '{\"customer_private\":true}'::jsonb WHERE campaign_id=%s",(cid,))
        self.assertEqual(self.console.browse({'source':'google_images'})['rows'],[])
        self.assertEqual(browse(self.dsn,{'source':'google_images'})['requests'],[])

    def test_worker_routes_images_and_reuses_only_image_cache(self):
        from realtime.serp_worker import run_serp_campaign
        config=Config(database_url=self.dsn,default_proxy_profile='direct',google_web_query_cache_seconds=300)
        def get(url,**kw):
            self.assertTrue(url.endswith('/google/image'))
            rid=kw['headers']['X-Request-ID']
            response=Mock(status_code=200,content=b'{}',headers={'X-Request-ID':rid})
            response.json.return_value=dict(query=dict(text='cats',engines_requested=['google']),
                meta=dict(request_id=rid,version='test',engines_failed=[]),
                results=[item()],pagination=dict(page=1,has_more=False,next_start=20))
            return response
        session=Mock();session.get.side_effect=get
        with patch('realtime.google_images.requests.Session',return_value=session):
            for _ in range(2):
                cid=self.console.create_task(dict(keywords=['cats'],sources=['google_images'],start=True),config)['campaign_id']
                run_serp_campaign(cid,config,self.base,self.console)
                self.assertEqual(self.base.campaign(cid)['status'],'completed')
        session.get.assert_called_once()
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT source,count(*) n FROM serp_requests GROUP BY source").fetchone()['source'],'google_images')
        self.assertEqual(self.console.task_usage(cid)['cached'],1)

    def test_once_image_failure_waits_with_bounded_retry_and_busy_lock_does_not_fetch(self):
        from realtime.serp_worker import run_serp_campaign
        config=Config(database_url=self.dsn,default_proxy_profile='direct')
        cid=self.console.create_task(dict(keywords=['cats'],sources=['google_images']),config)['campaign_id']
        with psycopg.connect(self.dsn,autocommit=True) as db:
            db.execute('SELECT pg_advisory_lock(20261106,1)')
            with patch('realtime.google_images.requests.Session') as session:
                run_serp_campaign(cid,config,self.base,self.console)
                session.assert_not_called()
            self.assertEqual(self.base.campaign(cid)['status'],'active')
        response=Mock(status_code=500,content=b'{}',headers={})
        response.json.return_value={'error':'failure','code':'engine_internal'}
        session=Mock();session.get.return_value=response
        with patch('realtime.google_images.requests.Session',return_value=session):
            run_serp_campaign(cid,config,self.base,self.console)
            self.assertEqual(self.base.campaign(cid)['status'],'active')
            with self.base.connect() as db:
                db.execute("UPDATE serp_task_options SET options=jsonb_set(options,'{_retry_started_at}',to_jsonb((now()-interval '2 hours')::text)) WHERE campaign_id=%s", (cid,))
            run_serp_campaign(cid,config,self.base,self.console)
            self.assertEqual(self.base.campaign(cid)['status'],'failed')
            self.assertEqual(self.console.task_options(cid)['retry_window_seconds'],3600)
        session.get.assert_called_once()
