import json
import os
import tempfile
import time
import unittest
import realtime.config
from importlib import reload
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from realtime.config import Config
from realtime.experiment_store import ExperimentStore, digest
from realtime.google_experiment import Runner, command, experiment_message, export_report, fetch_one, normalize_languages, publication_metadata, quality, seed_queries, site_queries


def document(url='https://example.org/ai', text=None):
    text = text or 'Artificial intelligence research and machine learning experiments. ' * 20
    return {'url':url, 'title':'AI research', 'content':text, 'content_hash':digest(text), 'summary':text[:500],
            'language':'en', 'http_status':200, 'source_engines':['google_web'], 'query':'AI',
            'discovered_at':'2026-09-09T00:00:00+00:00', 'fetched_at':'2026-09-09T00:01:00+00:00', 'document_id':'test'}


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ExperimentStore(self.root/'state', create=True)
        self.store.set('id', 'experiment-test')
        self.store.set('started_at', time.time()-10)
        self.store.set('deadline', time.time()+86400)
        self.store.set('state', 'running')
        self.store.set('whale', False)
        self.store.set('output', str(self.root/'export'))
        self.query = self.store.add_query('topic','AI','en','AI')

    def test_baseline_tsv_import_skips_blank_and_malformed_rows(self):
        from realtime.google_experiment import import_baselines
        path = self.root / 'baseline.tsv'
        path.write_text('https://example.com/a\thash-a\n\nbad-row\nhttps://example.com/b\thash-b\n', encoding='utf-8')
        store = ExperimentStore(self.root / 'baseline-run', create=True)
        args = SimpleNamespace(baseline_run=[], baseline_export=[], baseline_tsv=[str(path)])
        class Production:
            def connect(self):
                class Empty:
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def execute(self, *args):
                        class Result:
                            def fetchall(self): return []
                        return Result()
                return Empty()
        try:
            import_baselines(store, Production(), args)
            self.assertEqual(store.db.execute('select count(*) from baseline_urls').fetchone()[0], 2)
            self.assertEqual(store.db.execute('select count(*) from baseline_hashes').fetchone()[0], 2)
        finally:
            store.db.close()

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def discover(self, url='https://example.org/ai', family='topic', text='AI'):
        key = self.store.add_query(family,text,'en',text)
        row = self.store.db.execute('SELECT * FROM queries WHERE id=?',(key,)).fetchone()
        return self.store.search(row,1,time.time(),[{'url':url,'title':text}],[])

    def save(self, doc=None, finished=None):
        doc = doc or document()
        self.discover(doc['url'])
        return self.store.save_document({'requested_url':doc['url'], 'document':doc, 'status':'success',
                                        'seconds':.1, 'finished':finished or time.time()},quality(doc),self.store.get('deadline'))

    def test_add_query_backfills_missing_pages_for_existing_query(self):
        key = self.store.add_query('site', 'site:example.com AI', 'zh', 'AI', pages=3)
        self.store.add_query('site', 'site:example.com AI', 'zh', 'AI')
        pages = [row[0] for row in self.store.db.execute(
            'SELECT page FROM schedule WHERE query_id=? ORDER BY page', (key,)
        )]
        self.assertEqual(pages, list(range(1, 12)))

    def test_event_query_is_not_collapsed_and_dates_are_explicit(self):
        seed_queries(self.store,Config(),time.time())
        rows = list(self.store.db.execute("SELECT family,query FROM queries WHERE family IN ('event','recent')"))
        self.assertTrue(any('news research' in r['query'] for r in rows if r['family']=='event'))
        self.assertTrue(all('after:' in r['query'] for r in rows if r['family']=='recent'))
        self.assertTrue(all('-site:youtube.com' in r['query'] for r in rows))
        before = self.store.db.execute('SELECT count(*) FROM schedule').fetchone()[0]
        seed_queries(self.store,Config(),time.time())
        self.assertEqual(before,self.store.db.execute('SELECT count(*) FROM schedule').fetchone()[0])

    def test_chinese_only_seed_excludes_all_english_queries(self):
        self.store.db.execute('DELETE FROM schedule')
        self.store.db.execute('DELETE FROM queries')
        self.store.db.commit()
        seed_queries(self.store, Config(), time.time(), languages=('zh',))
        rows = list(self.store.db.execute('SELECT language,query,family FROM queries'))
        self.assertTrue(rows)
        self.assertEqual({row['language'] for row in rows}, {'zh'})
        self.assertTrue(any(row['family'] == 'topic' for row in rows))
        self.assertTrue(any(row['family'] == 'event' for row in rows))
        self.assertTrue(any(row['family'] == 'recent' for row in rows))

    def test_chinese_only_preflight_seeds_one_chinese_query(self):
        seed_queries(self.store, Config(), time.time(), preflight=True, languages=('zh',))
        rows = list(self.store.db.execute('SELECT language,query FROM queries'))
        self.assertEqual(len(rows), 2)  # setUp query plus the isolated preflight query
        seeded = [row for row in rows if row['query'] != 'AI']
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0]['language'], 'zh')
        self.assertEqual(self.store.get('preflight_search_target'), 1)

    def test_language_validation(self):
        self.assertEqual(normalize_languages('zh'), ('zh',))
        self.assertEqual(normalize_languages('zh,en,zh'), ('zh', 'en'))
        with self.assertRaises(ValueError):
            normalize_languages('ja')

    def test_quality_excludes_false_ai_substring_shells_and_short_text(self):
        self.assertIn('no_ai_context', quality(dict(document(text='Daily mail rain ' * 100), title='Daily mail')))
        self.assertTrue(quality(document(text='About Press Copyright Contact us ' * 5)))
        self.assertIn('possibly_truncated',quality(document(text='AI ' * 40000)))
        self.assertEqual(quality(document()), [])

    def test_cross_query_duplicate_and_updates_are_not_new(self):
        self.assertEqual(self.save()[1],'new')
        self.discover(family='event',text='AI update')
        self.assertEqual(self.save()[1],'duplicate')
        changed = document(text='AI changed research result and model evaluation. '*30)
        self.assertEqual(self.save(changed)[1],'update')
        counts = self.store.counts()
        self.assertEqual(counts['new'],1)
        self.assertEqual(counts['by_family']['topic']['new_content_covered'],1)
        self.assertEqual(counts['by_family']['event']['new_content_covered'],1)
        self.assertEqual(counts['by_family']['topic']['exclusive_new_content'],0)

    def test_redirect_alias_and_same_hash_other_url_deduplicate(self):
        self.save()
        self.assertEqual(self.save(document(url='https://other.org/copy'))[1],'duplicate')

    def test_baseline_does_not_count_as_new(self):
        self.store.baseline(['https://example.org/ai'],[])
        self.assertEqual(self.save()[1],'baseline')
        self.store.baseline([], [document()['content_hash']])
        self.assertEqual(self.save(document(url='https://elsewhere.org/ai'))[1],'duplicate')

    def test_late_completion_is_reported_but_excluded(self):
        self.assertEqual(self.save(finished=self.store.get('deadline')+1)[1],'late_new')
        counts=self.store.counts()
        self.assertEqual(counts.get('new',0),0)
        self.assertEqual(counts['late_results'],1)

    def test_search_cache_is_isolated_and_expires(self):
        self.discover()
        self.assertIsNotNone(self.store.cached(self.query,1,time.time()))
        self.assertIsNone(self.store.cached(self.query,1,time.time()+3601))
        self.assertIsNone(self.store.cached(self.query,2,time.time()))

    def test_failed_page_does_not_stall_all_queries(self):
        row=self.store.db.execute('SELECT * FROM queries WHERE id=?',(self.query,)).fetchone()
        self.store.search(row,1,time.time(),[],[], 'google_captcha')
        due=self.store.due_query('topic',time.time())
        self.assertEqual(due['page'],2)
        self.assertEqual(self.store.counts()['search_failures'],1)

    def test_site_expansion_requires_two_qualified_documents(self):
        self.save()
        self.assertEqual(site_queries(self.store),0)
        self.save(document(url='https://example.org/second', text='AI agents open source models and science. '*30))
        self.assertEqual(site_queries(self.store),1)
        row=self.store.db.execute("SELECT query FROM queries WHERE family='site'").fetchone()
        self.assertIn('site:example.org',row[0])

    def test_message_keeps_identity_and_does_not_invent_publication_time(self):
        msg=experiment_message(document(),'run-a','AI',['topic'],Config())
        again=experiment_message(document(),'run-b','AI news',['event'],Config())
        self.assertNotIn('published_at',msg['content'])
        self.assertEqual(msg['source']['source_record_key'],again['source']['source_record_key'])
        self.assertEqual(msg['discovery']['metadata']['experiment_id'],'run-a')

    def test_publication_only_uses_explicit_timezone_aware_published_field(self):
        value,source=publication_metadata(b'<meta property="article:published_time" content="2026-09-01T10:00:00+08:00">')
        self.assertEqual(value,'2026-09-01T10:00:00+08:00')
        self.assertIsNotNone(source)
        self.assertEqual(publication_metadata(b'<time datetime="2026-09-01T10:00:00+08:00">Published</time>')[0],
                         '2026-09-01T10:00:00+08:00')
        self.assertEqual(publication_metadata(b'<meta name="date" content="2026-09-01T10:00:00Z">')[0],
                         '2026-09-01T10:00:00+00:00')
        self.assertEqual(publication_metadata(b'<meta property="article:modified_time" content="2026-09-01T10:00:00Z">'),(None,None))
        self.assertEqual(publication_metadata(b'<meta property="article:published_time" content="2026-09-01">'),(None,None))
        msg=experiment_message(dict(document(),published_at=value),'run','AI',['topic'],Config())
        self.assertEqual(msg['content']['published_at'],value)

    def test_missing_publication_is_retained_locally_without_posting(self):
        self.discover()
        runner=Runner(self.store,Config(),Mock(),self.root/'export')
        runner.output.mkdir()
        runner.whale=Mock()
        runner.save_body({'document':document(),'requested_url':document()['url'],'status':'success','seconds':1})
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'blocked_missing_publication')
        self.assertEqual(self.store.counts()['new'],1)
        runner.whale.bulk_ingest.assert_not_called()

    def test_remote_only_scrubs_bodies_after_queue_and_payload_after_receipt(self):
        self.store.set('whale', True)
        self.store.set('remote_only', True)
        self.discover()
        with patch('realtime.google_experiment.WhaleClient', return_value=Mock()):
            runner = Runner(self.store, Config(), Mock(), self.root/'export')
        doc = dict(document(), published_at='2026-09-01T10:00:00+08:00')
        runner.save_body({'document': doc, 'requested_url': doc['url'], 'status': 'success', 'seconds': 1})
        row = self.store.db.execute('SELECT document FROM documents').fetchone()
        outbox = self.store.db.execute('SELECT payload,status FROM outbox').fetchone()
        self.assertEqual(row['document'], 'null')
        self.assertEqual(outbox['status'], 'pending')
        self.assertIn(doc['content'], outbox['payload'])
        self.assertFalse((self.root/'export/documents').exists())
        runner.whale.bulk_ingest.return_value = [{'receipt_status': 'accepted'}]
        runner.flush_whale()
        outbox = self.store.db.execute('SELECT payload,status FROM outbox').fetchone()
        self.assertEqual(outbox['status'], 'accepted')
        self.assertIsNone(outbox['payload'])

    def test_remote_only_discards_body_without_publication_date(self):
        self.store.set('whale', True)
        self.store.set('remote_only', True)
        self.discover()
        with patch('realtime.google_experiment.WhaleClient', return_value=Mock()):
            runner = Runner(self.store, Config(), Mock(), self.root/'export')
        runner.save_body({'document': document(), 'requested_url': document()['url'], 'status': 'success', 'seconds': 1})
        self.assertEqual(self.store.db.execute('SELECT document FROM documents').fetchone()[0], 'null')
        row = self.store.db.execute('SELECT payload,status FROM outbox').fetchone()
        self.assertIsNone(row['payload'])
        self.assertEqual(row['status'], 'blocked_missing_publication')

    def test_pausing_does_not_change_deadline(self):
        deadline=self.store.get('deadline')
        for action,expected in [('pause','paused'),('resume','running')]:
            command(SimpleNamespace(action=action,directory=str(self.root/'state')))
            self.assertEqual(self.store.get('state'),expected)
        self.assertEqual(self.store.get('deadline'),deadline)

    def test_expired_run_cannot_resume(self):
        self.store.set('deadline',time.time()-1)
        with self.assertRaises(ValueError):
            command(SimpleNamespace(action='resume',directory=str(self.root/'state')))

    def test_runtime_stops_without_search_after_deadline(self):
        self.store.set('deadline',time.time()-1)
        runner=Runner(self.store,Config(),Mock(),self.root/'export')
        with patch.object(runner,'search') as search:
            runner.run()
        search.assert_not_called()
        self.assertEqual(self.store.get('state'),'complete')

    def test_body_subprocess_deadline_is_reported(self):
        with patch('realtime.google_experiment.subprocess.run',side_effect=subprocess.TimeoutExpired('fetch',65)):
            result=fetch_one({'url':'https://example.org/'},threading.BoundedSemaphore(2))
        self.assertEqual(result['error'],'body_hard_deadline')
        self.assertIsNone(result['document'])

    def test_local_two_rps_cap_and_global_cooling_are_both_respected(self):
        production=Mock()
        production.acquire_discovery_slot.return_value={'allowed':True,'wait':0}
        runner=Runner(self.store,Config(),production,self.root/'export')
        runner.slot('google_web',2)
        self.assertGreater(runner.slot('google_web',2)['wait'],.4)
        production.acquire_discovery_slot.return_value={'allowed':False,'wait':1800}
        self.assertFalse(runner.slot('google_web',.5)['allowed'])
        self.assertGreater(self.store.get('search_cooling_until'),time.time()+1799)

    def test_openserp_canary_uses_a_small_independent_rate(self):
        production=Mock();production.acquire_discovery_slot.return_value={'allowed':True,'wait':0}
        runner=Runner(self.store,Config(),production,self.root/'export')
        runner.slot('google_openserp',2)
        args=production.acquire_discovery_slot.call_args
        self.assertLessEqual(args.args[1],.05)
        self.assertIn(args.kwargs.get('maximum_rps'),(2,None))

    def test_openserp_rate_is_tunable_for_isolated_experiments(self):
        self.store.set('openserp_rps',.5)
        production=Mock();production.acquire_discovery_slot.return_value={'allowed':True,'wait':0}
        self.store.set('search_rps',4)
        Runner(self.store,Config(),production,self.root/'export').slot('google_openserp',2)
        self.assertEqual(production.acquire_discovery_slot.call_args.args[1],.5)

    def test_provider_rate_is_runtime_tunable(self):
        self.store.set('search_rps', 4)
        self.store.set('provider_rps', {'google_wml': .35})
        production=Mock();production.acquire_discovery_slot.return_value={'allowed':True,'wait':0}
        Runner(self.store,Config(),production,self.root/'export').slot('google_wml', 1.0)
        self.assertEqual(production.acquire_discovery_slot.call_args.args[1], .35)

    def test_experiment_connects_serp_evidence_settings(self):
        self.store.set('output', str(self.root/'export'))
        production = Mock()
        client = Runner(self.store, Config(), production, self.root/'export').client('zh')
        self.assertFalse(client.transport.google_serp_save_html)

    def test_experiment_records_openserp_attempt_audit(self):
        production = Mock()
        runner = Runner(self.store, Config(), production, self.root/'export')
        client = runner.client('zh')
        self.assertIs(client.serp_attempt_recorder, production.record_google_serp_attempt)

    def test_wml_only_global_provider_configuration(self):
        with patch.dict(os.environ, {"GOOGLE_FREE_PROVIDERS": "wml"}):
            config = reload(realtime.config).Config()
        self.assertEqual(config.google_free_providers, ("wml",))

    def test_historical_hash_copy_in_second_family_is_attributed(self):
        self.save()
        copied=document(url='https://elsewhere.org/copied')
        self.discover(copied['url'],family='event',text='AI release')
        self.save(copied)
        counts=self.store.counts()
        self.assertEqual(counts['new'],1)
        self.assertEqual(counts['by_family']['event']['new_content_covered'],1)
        self.assertEqual(counts['by_family']['topic']['exclusive_new_content'],0)

    def test_storage_stop_finishes_instead_of_remaining_in_drain(self):
        self.store.set('state','storage_stopped')
        self.store.set('stop_reason','disk_budget_or_free_space')
        runner=Runner(self.store,Config(),Mock(),self.root/'export')
        runner.run()
        self.assertEqual(self.store.get('state'),'storage_stopped')

    def test_whale_receipts_and_retry_only_touch_experiment_outbox(self):
        doc_id,_=self.save()
        with self.store.db:
            self.store.db.execute("INSERT INTO outbox(document_id,payload,status) VALUES(?,?,'pending')", (doc_id,'{}'))
        runner=Runner(self.store,Config(),Mock(),self.root/'export')
        runner.whale=Mock()
        runner.whale.bulk_ingest.side_effect=RuntimeError('network')
        runner.flush_whale()
        row=self.store.db.execute('SELECT * FROM outbox').fetchone()
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['attempts'],1)
        runner.next_whale=0
        self.store.db.execute('UPDATE outbox SET next_attempt=0')
        self.store.db.commit()
        runner.whale.bulk_ingest.side_effect=None
        runner.whale.bulk_ingest.return_value=[{'receipt_status':'duplicate'}]
        runner.flush_whale()
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'duplicate')
        runner.whale.claim.assert_not_called()

    def test_report_has_content_and_keeps_whale_baseline_separate(self):
        self.save()
        report=export_report(self.store,self.root/'export',full=True)
        self.assertEqual(report['new'],1)
        self.assertTrue((self.root/'export/全部正文.md').is_file())
        self.assertIn(document()['content'],(self.root/'export/全部正文.md').read_text())
        self.assertEqual(len(list((self.root/'export/search-pages').glob('*.md'))),1)

    def test_resume_preserves_due_times_and_records(self):
        self.save()
        due=self.store.db.execute('SELECT due FROM schedule WHERE page=1').fetchone()[0]
        second=ExperimentStore(self.root/'state')
        self.assertEqual(second.counts()['new'],1)
        self.assertEqual(second.db.execute('SELECT due FROM schedule WHERE page=1').fetchone()[0],due)
        second.db.close()


if __name__=='__main__':
    unittest.main()
