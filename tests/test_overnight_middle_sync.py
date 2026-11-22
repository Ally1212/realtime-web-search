"""Archive identities survive retries; late task journals cannot hide old requests."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

spec=importlib.util.spec_from_file_location('overnight_sync',Path(__file__).parents[1]/'scripts/overnight_middle_sync.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
rpc_spec = importlib.util.spec_from_file_location('overnight_rpc', Path(__file__).parents[1]/'scripts/overnight_middle_rpc.py')
rpc = importlib.util.module_from_spec(rpc_spec)
rpc_spec.loader.exec_module(rpc)

class ArchiveTests(unittest.TestCase):
    def test_window_uses_transport_from_same_release_as_runner(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync, 'STATE', Path(root)), \
             patch.object(sync.subprocess, 'run', return_value=Mock(stdout='{"submitted": 1}', returncode=0)) as run:
            (Path(root) / 'middle_rpc.py').write_text('stale transport')
            self.assertEqual(sync.rpc('google_web', 'upload'), {'submitted': 1})
            self.assertEqual(run.call_args.args[0][-1], Path(rpc_spec.origin).read_text())

    def test_restart_honors_persisted_retry_window_before_sending(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)), \
             patch.object(sync,'WEB_SUPPLY_RUN','supply-x'):
            sync.initialize()
            with sync.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS retry_state(source TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_at REAL NOT NULL)')
                db.execute("INSERT INTO retry_state VALUES('google_web',3,1060)")
            with patch.object(sync.time,'time',return_value=1020), \
                 patch.object(sync.time,'sleep',side_effect=InterruptedError('still waiting')) as wait, \
                 patch.object(sync,'advance') as advance:
                with self.assertRaisesRegex(InterruptedError,'still waiting'):
                    sync.worker('google_web',drain_prepared=True)
                wait.assert_called_once_with(40)
                advance.assert_not_called()
            with patch.object(sync.time,'time',return_value=1060), patch.object(sync,'advance') as advance:
                sync.worker('google_web',drain_prepared=True)
                advance.assert_called_once_with('google_web',upload_limit=1)
            with sync.connect() as db:
                self.assertIsNone(db.execute('SELECT * FROM retry_state').fetchone())

    def test_drain_prepared_never_scans_new_source_rows(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)), \
             patch.object(sync,'WEB_SUPPLY_RUN','supply-x'):
            state=Path(root)
            (state/'manifest.json').write_text(json.dumps(dict(deadline='2026-11-18T11:00:00+00:00')))
            (state/'final.json').write_text(json.dumps(dict(anomalies=[],inflight=0)))
            (state/'audit.json').write_text(json.dumps(dict(cutoff=dict(successful=1,new_urls=2))))
            sync.initialize()
            message=json.dumps(sync.message('google_web','a',{'query':'fixture'},2))
            with sync.connect() as c:
                c.execute("INSERT INTO outbox (source,identity,message,result_count) VALUES('google_web','a',?,2)",(message,))
                c.execute("INSERT INTO web_url_credit VALUES('a',2)")
            sync.require_audited_web_queue()
            def finish(*_):
                with sync.connect() as c:
                    c.execute("UPDATE outbox SET state='verified'")
            with patch.object(sync,'discover') as discover, patch.object(sync,'advance',finish):
                sync.worker('google_web',drain_prepared=True)
            discover.assert_not_called()
            with sync.connect() as c:
                c.execute("UPDATE web_url_credit SET new_urls=3")
            with self.assertRaisesRegex(ValueError,'disagrees with the cutoff'):
                sync.require_audited_web_queue()

    def test_web_window_counts_only_verified_historical_first_discoveries(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)), \
             patch.object(sync,'RUN_ID','test-web-window'), patch.object(sync,'WEB_SUPPLY_RUN','supply-x'):
            deadline='2026-11-18T11:00:00.000002+00:00'
            (Path(root)/'manifest.json').write_text(json.dumps(dict(
                started_at='2026-11-18T10:00:00+00:00',deadline=deadline)))
            sync.initialize()
            items=[dict(count=8,new_urls=4,row=dict(request_id='first',query='one',
                completed_at='2026-11-18T10:10:00+00:00')),
                dict(count=5,new_urls=2,row=dict(request_id='second',query='two',
                completed_at='2026-11-18T10:20:00+00:00'))]
            with patch.object(sync,'rpc',return_value={'rows':items}) as remote:
                self.assertEqual(sync.discover('google_web'),2)
                self.assertEqual(remote.call_args.kwargs['supply_run'],'supply-x')
            with sync.connect() as db:
                message=json.loads(db.execute("SELECT message FROM outbox WHERE identity='first'").fetchone()[0])
                self.assertEqual(message['source']['source_record_key'],'test-web-window:google_web:first')
                for identity,stamp in [('first','2026-11-18T11:00:00.000001+00:00'),('second',deadline)]:
                    db.execute("UPDATE outbox SET state='verified',proof=? WHERE identity=?",
                               (json.dumps({'verified_at':stamp}),identity))
            self.assertEqual(sync.report()['web_historical_new_urls'],{
                'discovered':6,'verified':6,'verified_before_deadline':4,
            })

    def test_archive_upload_uses_backfill_admission(self):
        message = sync.message('google_web', 'r', {'query': 'q'}, 3)
        config = Mock(whale_collector_api_key='test', whale_source_platform='google_search',
                      whale_dataset_id='social_media_raw')
        with patch('realtime.config.Config', return_value=config), \
             patch('realtime.middle_ingest.collector_endpoint', return_value='https://whale.test'), \
             patch.object(rpc.Path, 'read_text', return_value='test'), \
             patch('requests.Session') as session:
            post = session.return_value.post
            post.return_value = Mock(status_code=202, json=Mock(return_value=[{}]))
            self.assertEqual(rpc.main({'action': 'upload', 'source': 'google_web', 'messages': [message]}),
                             {'submitted': 1})
            self.assertEqual(post.call_args.kwargs['headers']['X-Ingest-Class'], 'backfill')
            self.assertEqual(post.call_args.kwargs['json'], [message])

    def test_archive_throttle_surfaces_retry_and_only_machine_readable_reason(self):
        message=sync.message('google_web','r',{'query':'q'},3)
        config=Mock(whale_collector_api_key='test',whale_source_platform='google_search',
                    whale_dataset_id='social_media_raw')
        with patch('realtime.config.Config',return_value=config), \
             patch('realtime.middle_ingest.collector_endpoint',return_value='https://whale.test'), \
             patch.object(rpc.Path,'read_text',return_value='test'), \
             patch('requests.Session') as session:
            session.return_value.post.return_value=Mock(status_code=429,headers={'Retry-After':'30'},
                json=Mock(return_value={'error':{'code':'ingest_overloaded','message':'private response'}}))
            with self.assertRaisesRegex(RuntimeError,'ingest_overloaded') as throttled:
                rpc.main({'action':'upload','source':'google_web','messages':[message]})
            self.assertGreaterEqual(throttled.exception.retry_after,30)
            self.assertNotIn('private response',str(throttled.exception))

    def test_readback_throttle_preserves_retry_window_for_coordinator(self):
        message = sync.message('google_web', 'r', {'query':'q'}, 3)
        config = Mock(whale_collector_api_key='test', whale_source_platform='google_search',
                      whale_dataset_id='social_media_raw')
        response = requests.Response()
        response.status_code = 429
        response.headers['Retry-After'] = '120'
        response._content = b'{"error":{"code":"read_throttled","message":"private response"}}'
        with patch('realtime.config.Config', return_value=config), \
             patch('realtime.middle_ingest.collector_endpoint', return_value='https://whale.test'), \
             patch.object(rpc.Path, 'read_text', return_value='test'), \
             patch('requests.adapters.HTTPAdapter.send', return_value=response) as send:
            with self.assertRaisesRegex(RuntimeError, 'middle_http_429') as throttled:
                rpc.main({'action':'verify', 'source':'google_web', 'messages':[message],
                          'receipts':[{'status':'accepted','document_id':'d','version_id':'v'}]})
        self.assertGreaterEqual(throttled.exception.retry_after, 120)
        self.assertNotIn('private response', str(throttled.exception))
        send.assert_called_once()

    def test_timestamp_and_unknown_maps_observation(self):
        m=sync.message('google_web','r',{'query':'q'},3,'2026-11-17 10:20:00+00:00')
        self.assertEqual(m['source']['received_at'],'2026-11-17T10:20:00+00:00')
        self.assertEqual(m['source']['payload_hash'],'sha256:'+sync.digest(m['content']['platform_payload']))
        maps=sync.message('google_maps','7',{},0)
        self.assertIsNone(maps['content']['platform_payload']['observed_at'])
        self.assertFalse(maps['content']['platform_payload']['observed_at_known'])
        self.assertNotIn('collection_task_id',maps['source'])

    def test_unknown_upload_polls_before_retry_when_middle_accepted_it(self):
        with tempfile.TemporaryDirectory() as root,patch.object(sync,'STATE',Path(root)):
            sync.initialize();m=sync.message('google_maps','7',{'result':{'places':[]}},0)
            with sync.connect() as c:c.execute('INSERT INTO outbox(source,identity,message,result_count) VALUES(?,?,?,?)',('google_maps','7',json.dumps(m),0))
            uploaded=[]
            def rpc(source,action,**p):
                if action=='upload':
                    uploaded.append(p['messages'])
                    if len(uploaded)==1:raise RuntimeError('transport result unknown')
                    return {}
                if action=='poll':return {'receipts':[dict(status='accepted',document_id='d',version_id='v')]}
                return {'proofs':[dict(document_id='d',version_id='v')]}
            with patch.object(sync,'rpc',rpc):
                with self.assertRaises(RuntimeError):sync.advance('google_maps')
                sync.advance('google_maps')
                sync.advance('google_maps')
            self.assertEqual(len(uploaded),1)
            with sync.connect() as c:self.assertEqual(c.execute('SELECT state FROM outbox').fetchone()['state'],'verified')

    def test_backfill_throttling_does_not_block_readback_of_accepted_snapshot(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)):
            sync.initialize()
            message=json.dumps(sync.message('google_web','a',{'query':'q'},1))
            receipt=json.dumps(dict(status='accepted',document_id='d',version_id='v'))
            with sync.connect() as c:
                c.execute("INSERT INTO outbox VALUES('google_web','a',?,1,'accepted',?,NULL)",(message,receipt))
                c.execute("INSERT INTO outbox VALUES('google_web','b',?,1,'prepared',NULL,NULL)",(message,))
            rejection=RuntimeError('middle_http_429')
            rejection.status_code=429
            remote=Mock(side_effect=[{'proofs':[dict(document_id='d',version_id='v')]},rejection])
            with patch.object(sync,'rpc',remote), self.assertRaisesRegex(RuntimeError,'429'):
                sync.advance('google_web')
            self.assertEqual([call.args[1] for call in remote.call_args_list],['verify','upload'])
            with sync.connect() as c:
                states={row['identity']:row['state'] for row in c.execute('SELECT identity,state FROM outbox')}
            self.assertEqual(states,{'a':'verified','b':'prepared'})

    def test_terminal_status_without_version_keeps_polling_other_steps(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)):
            sync.initialize()
            message=json.dumps(sync.message('google_web','a',{'query':'q'},1))
            with sync.connect() as db:
                db.execute("INSERT INTO outbox VALUES('google_web','a',?,1,'submitted',NULL,NULL)",(message,))
            incomplete={'receipts':[dict(status='accepted',document_id='d')]}
            complete={'receipts':[dict(status='accepted',document_id='d',version_id='v')]}
            proof={'proofs':[dict(document_id='d',version_id='v')]}
            with patch.object(sync,'rpc',side_effect=[incomplete,complete,proof]) as remote:
                sync.advance('google_web')
                sync.advance('google_web')
            self.assertEqual([call.args[1] for call in remote.call_args_list],['poll','poll','verify'])
            with sync.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'verified')

    def test_worker_death_after_upload_replays_the_saved_request(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)):
            sync.initialize()
            payload=sync.message('google_web','42',{'query':'fixture'},1)
            with sync.connect() as db:
                db.execute('INSERT INTO outbox(source,identity,message,result_count) VALUES(?,?,?,?)',
                           ('google_web','42',json.dumps(payload),1))
            child=r"""
import json, os, sys
from pathlib import Path
import overnight_middle_sync as sync
sync.STATE=Path(sys.argv[1])
def transport(source, action, **request):
    assert action=='upload'
    (sync.STATE/'wire.json').write_text(json.dumps(request['messages']))
    os._exit(17)
sync.rpc=transport
sync.advance('google_web')
"""
            failed=subprocess.run([sys.executable,'-c',child,root],
                                  cwd=Path(sync.__file__).parent,timeout=5)
            self.assertEqual(failed.returncode,17)
            uploaded=json.loads((Path(root)/'wire.json').read_text())
            with sync.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'submitted')
            with patch.object(sync,'rpc',side_effect=[{'receipts':[{'status':'unknown'}]},
                                                     {'submitted':1}]) as retry:
                sync.advance('google_web')
            self.assertEqual([call.args[1] for call in retry.call_args_list],['poll','upload'])
            self.assertEqual(retry.call_args.kwargs['messages'],uploaded)

    def test_slow_receipts_rotate_and_backpressure_leaves_unsent_rows_durable(self):
        with tempfile.TemporaryDirectory() as root, patch.object(sync,'STATE',Path(root)), \
             patch.object(sync,'WEB_SUPPLY_RUN','supply-x'):
            sync.initialize()
            with sync.connect() as db:
                for i in range(81):
                    message=sync.message('google_web',str(i),{'query':'fixture'},1)
                    db.execute('INSERT INTO outbox(source,identity,message,result_count,state) VALUES(?,?,?,?,?)',
                               ('google_web',str(i),json.dumps(message),1,'submitted' if i<80 else 'prepared'))
            seen=[]
            def poll(source,action,**payload):
                self.assertEqual(action,'poll')
                seen.append({m['content']['platform_payload']['observation_id'] for m in payload['messages']})
                return {'receipts':[{'status':'queued'} for _ in payload['messages']]}
            with patch.object(sync,'rpc',poll):
                sync.advance('google_web')
                sync.advance('google_web')
            self.assertEqual([len(batch) for batch in seen],[20,20])
            self.assertTrue(seen[0].isdisjoint(seen[1]))
            with sync.connect() as db:
                self.assertEqual(db.execute("SELECT state FROM outbox WHERE identity='80'").fetchone()[0],'prepared')

    def test_late_completed_task_is_not_lost_behind_scan_cursor(self):
        with tempfile.TemporaryDirectory() as root,patch.object(sync,'STATE',Path(root)):
            sync.initialize()
            (Path(root)/'manifest.json').write_text(json.dumps(dict(started_at='2026-11-17T10:00:00+00:00',deadline='2026-11-18T01:00:00+00:00')))
            with sqlite3.connect(Path(root)/'results.sqlite') as c:c.execute('CREATE TABLE tasks(source TEXT,result TEXT)')
            calls=[]
            def rpc(source,action,**p):
                calls.append(p)
                index=len(calls)
                return dict(rows=[dict(row=dict(request_id=str(index),query='q',completed_at=f'2026-11-17T10:{30-index}:00+00:00'),count=1)])
            with patch.object(sync,'rpc',rpc):sync.discover('google_news');sync.discover('google_news')
            self.assertEqual(calls[0]['cursor'],calls[1]['cursor'])
            self.assertEqual(calls[1]['exclude'],['1'])
            with sync.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM outbox').fetchone()[0],2)

if __name__=='__main__':unittest.main()
