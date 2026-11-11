import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import zipfile

from realtime.insights import InsightService
from realtime.insights_core import (InsightError, prepare_records, taxonomy, validate_annotations,
    report, rule_annotations, digest)
from realtime.insights_model import ResponsesModel

TAGS=[dict(id='effect',level1='功能价值',level2='产品表现',name='安神效果',description='明确讨论安神效果',keywords=['calm']),
      dict(id='taste',level1='体验价值',level2='进食',name='适口性',description='明确讨论口味',keywords=['taste'])]
ROWS=[dict(source_id='r1',source='amazon',native_id='a',text_raw='Great taste but not calm',brand='A',rating=1),
      dict(source_id='r2',source='amazon',native_id='b',text_raw='Calm and good taste',brand='B',rating=5),
      dict(source_id='r3',source='reddit',native_id='t1_x',parent_id='thread',text_raw='Still not calm',rating=None)]


class InsightCoreTests(unittest.TestCase):
    def test_saved_news_wrapper_and_publisher_text_count_once_keep_occurrences(self):
        from realtime.insights_source import saved_text_identity
        raw='Battery research - Publisher\nBattery research Publisher'
        rows=[dict(source_id=str(i),source='google_news',url=url,text_raw=raw,
                   native_id=saved_text_identity('google_news',url,raw))
              for i,url in enumerate(['https://news.google.com/rss/articles/opaque',
                                      'https://publisher.example/research'])]
        rows.append(dict(source_id='other',source='google_news',text_raw=raw+' updated',
                         native_id=saved_text_identity('google_news',rows[1]['url'],raw+' updated')))
        records,q=prepare_records(rows,'search')
        self.assertEqual(len(records),3)
        self.assertEqual(q['eligible_rows'],2)
        self.assertEqual(records[1]['duplicate_of'],'0')
        self.assertEqual(records[1]['url'],'https://publisher.example/research')
        self.assertNotEqual(saved_text_identity('google_web','https://a.example',raw),
                            saved_text_identity('google_web','https://b.example',raw))

    def test_dedup_native_ids_keep_distinct_short_comments(self):
        rows=[dict(text_raw='good',source='x',native_id='a'),dict(text_raw='good',source='x',native_id='b'),dict(text_raw='good',source='x',native_id='a')]
        records,q=prepare_records(rows,'voc')
        self.assertEqual(q['eligible_rows'],2)
        self.assertEqual(q['excluded']['duplicate_source_id'],1)
        self.assertEqual(len(records),2)  # generated source_id duplicate is counted separately below

    def test_thread_cap_retains_raw_rows(self):
        rows=[dict(text_raw=str(i),source='reddit',native_id=str(i),parent_id='p') for i in range(6)]
        records,q=prepare_records(rows,'voc',2)
        self.assertEqual(len(records),6);self.assertEqual(q['eligible_rows'],2)
        self.assertEqual(q['excluded']['thread_cap'],4)

    def test_bad_inputs_never_coerce_stars_or_unknowns(self):
        for value in [True,0,6,float('nan'),'1']:
            with self.subTest(value=value),self.assertRaises(InsightError):
                prepare_records([dict(text_raw='ok',rating=value)],'voc')
        for mode in ('bad',None):
            with self.assertRaises(InsightError):prepare_records(ROWS,mode)
        records,q=prepare_records([dict(text_raw='[deleted]'),ROWS[0]],'voc')
        self.assertEqual(q['excluded']['deleted_removed'],1)
        self.assertIsNone(records[0]['url'])

    def test_taxonomy_validated_and_unknown_fields_rejected(self):
        self.assertEqual(taxonomy(TAGS,'voc'),TAGS)
        for tags in [[],TAGS+TAGS,[dict(TAGS[0],level1='主题议题')],[dict(TAGS[0],surprise=True)]]:
            with self.assertRaises(InsightError):taxonomy(tags,'voc')

    def test_evidence_exact_offsets_and_duplicate_labels(self):
        r=prepare_records(ROWS,'voc')[0][0]
        valid=validate_annotations([dict(tag_id='taste',sentiment='positive',quote='taste')],r,TAGS)
        self.assertEqual(r['text_raw'][valid[0]['start']:valid[0]['end']],'taste')
        for ann in [[dict(tag_id='taste',quote='Taste')],[dict(tag_id='bad',quote='taste')],
                    [dict(tag_id='taste',quote='taste',start=0)],
                    [dict(tag_id='taste',quote='taste')]*2]:
            with self.assertRaises(InsightError):validate_annotations(ann,r,TAGS)

    def test_rules_do_not_invent_sentiment_or_match_word_fragments(self):
        r=prepare_records([dict(text_raw='calming and TASTE')],'voc')[0][0]
        a=rule_annotations(r,TAGS)
        self.assertEqual([x['tag_id'] for x in a],['taste']);self.assertEqual(a[0]['sentiment'],'unknown')

    def test_aspect_sentiment_and_brand_denominators(self):
        records,q=prepare_records(ROWS,'voc')
        annotations={'r1':validate_annotations([dict(tag_id='taste',sentiment='positive',quote='taste'),dict(tag_id='effect',sentiment='negative',quote='not calm')],records[0],TAGS)}
        r=report(records,TAGS,annotations,{'r1':{}})
        self.assertEqual(r['n'],3);self.assertEqual(r['low_star']['n'],1)
        self.assertEqual(r['low_star']['negative_mentions'][0]['count'],1)
        self.assertEqual(r['brand_cross'][0]['n'],1)
        self.assertEqual(r['reviewed_records'],1)


class InsightServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=InsightService(Path(self.tmp.name)/'analysis.db',start_worker=False)
        self.d=self.s.create_dataset('a',dict(name='test',mode='voc',records=ROWS,allow_external_processing=True),'create')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def approve(self):
        self.d=self.s.save_taxonomy('a',self.d['id'],dict(tags=TAGS,approve=True,revision=self.d['revision']))
    def submit(self,**kw):
        return self.s.submit('a',self.d['id'],dict(method='rules',revision=self.d['revision'],**kw),'run')

    def test_tenant_isolation_all_surfaces(self):
        self.assertEqual(self.s.list_datasets('b')['datasets'],[])
        for fn in (self.s.dataset,self.s.records,self.s.bundle):
            with self.assertRaises(InsightError) as e:fn('b',self.d['id'])
            self.assertEqual(e.exception.status,404)
        with self.assertRaises(InsightError):self.s.export('b',self.d['id'],'json')

    def test_idempotency_conflict_no_duplicate(self):
        self.assertEqual(self.s.create_dataset('a',dict(name='test',mode='voc',records=ROWS,allow_external_processing=True),'create')['id'],self.d['id'])
        with self.assertRaises(InsightError):self.s.create_dataset('a',dict(name='different',mode='voc',records=ROWS),'create')
        self.assertEqual(len(self.s.list_datasets('a')['datasets']),1)

    def test_approval_required_and_revision_conflict(self):
        with self.assertRaises(InsightError):self.submit()
        self.approve()
        with self.assertRaises(InsightError):self.s.save_taxonomy('a',self.d['id'],dict(tags=TAGS,approve=True,revision=1))

    def test_rules_end_to_end_fixed_export_and_manifest(self):
        self.approve();run=self.submit();self.assertTrue(self.s.process_next())
        self.assertEqual(self.s.run('a',run['id'])['status'],'succeeded')
        d=self.s.dataset('a',self.d['id']);self.assertEqual(d['report']['annotated_records'],3)
        raw,mime=self.s.export('a',self.d['id'],'zip')
        self.assertEqual(raw,self.s.export('a',self.d['id'],'zip')[0])
        import hashlib
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            manifest=json.loads(z.read('manifest.json'))
            for name,proof in manifest['files'].items():
                self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),proof['sha256'])
            self.assertEqual(len(z.read('records.jsonl').decode().splitlines()),3)
        self.assertEqual(mime,'application/zip')

    def test_manual_aspects_preserved_on_rerun(self):
        self.approve()
        self.d=self.s.review('a',self.d['id'],dict(revision=self.d['revision'],source_id='r1',annotations=[dict(tag_id='effect',sentiment='negative',quote='not calm')]))
        run=self.submit();self.s.process_next()
        values=self.s.records('a',self.d['id'])['records']
        self.assertEqual(values[0]['annotations'][0]['sentiment'],'negative')
        self.assertEqual(self.s.run('a',run['id'])['processed'],2)

    def test_active_edits_and_export_rejected_cancel_unblocks(self):
        self.approve();run=self.submit()
        with self.assertRaises(InsightError):self.s.export('a',self.d['id'],'json')
        with self.assertRaises(InsightError):self.s.save_taxonomy('a',self.d['id'],dict(tags=TAGS,approve=True,revision=self.d['revision']))
        self.assertEqual(self.s.cancel('a',run['id'])['status'],'cancelled')
        self.assertFalse(self.s.process_next())
        self.s.export('a',self.d['id'],'json')

    def test_revoke_blocks_derived_reads_not_deletion(self):
        self.s.validator=lambda *_: (_ for _ in ()).throw(InsightError('revoked','revoked',403))
        self.assertEqual(self.s.list_datasets('a')['datasets'],[])
        with self.assertRaises(InsightError):self.s.bundle('a',self.d['id'])
        self.assertTrue(self.s.delete('a',self.d['id'],self.d['revision'])['deleted'])

    def test_manual_exclusion_reversible_and_duplicate_not_resurrected(self):
        self.approve()
        d=self.s.review('a',self.d['id'],dict(revision=self.d['revision'],source_id='r1',exclude=True,annotations=[]))
        self.assertEqual(d['report']['n'],2)
        d=self.s.review('a',self.d['id'],dict(revision=d['revision'],source_id='r1',exclude=False,annotations=[]))
        self.assertEqual(d['report']['n'],3)

    def test_restart_marks_uncertain_not_replays(self):
        self.approve();run=self.submit()
        another=InsightService(self.s.path,start_worker=False)
        self.assertEqual(another.run('a',run['id'])['status'],'interrupted')
        self.assertFalse(another.process_next());another.close()

    def test_corrupt_snapshot_refused(self):
        with self.s.db() as db:db.execute("UPDATE datasets SET records='[]' WHERE id=?",(self.d['id'],))
        with self.assertRaises(InsightError):self.s.dataset('a',self.d['id'])

    def test_concurrent_submit_same_intent_one_job(self):
        self.approve()
        with ThreadPoolExecutor(2) as pool:ids=list(pool.map(lambda _:self.submit()['id'],range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(len(self.s.list_runs('a',self.d['id'])['runs']),1)

    def test_xss_and_csv_formula_safe_exports(self):
        d=self.s.create_dataset('a',dict(name='<script>alert(1)</script>',mode='voc',records=[dict(source_id='evil',text_raw='=HYPERLINK("evil") <script>alert(1)</script>')]),'evil')
        html=self.s.export('a',d['id'],'html')[0].decode()
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        csv=self.s.export('a',d['id'],'csv')[0].decode()
        self.assertIn("'=HYPERLINK",csv)


class FakeModel:
    def __init__(self,invalid=False):self.calls=0;self.invalid=invalid
    def config(self):return {'model':'fixture'}
    def capabilities(self):return {'configured':True,'model':'fixture'}
    def call(self,kind,payload):
        self.calls+=1
        if kind=='draft':return {'tags':TAGS},{'usage':{'total_tokens':20}}
        return {'records':[dict(source_id=r['source_id'],annotations=[dict(tag_id='effect',quote='fabricated' if self.invalid else ('calm' if 'calm' in r['text_raw'] else 'Calm'),sentiment='unknown')]) for r in payload['records']]},{'usage':{'total_tokens':20}}


class InsightAITests(unittest.TestCase):
    tearDown=InsightServiceTests.tearDown
    approve=InsightServiceTests.approve
    def setUp(self):
        InsightServiceTests.setUp(self);self.fake=FakeModel();self.s.model=self.fake
    def test_ai_end_to_end_ledger_and_no_duplicate_call(self):
        self.approve();p=dict(method='ai',revision=self.d['revision'],max_calls=1)
        run=self.s.submit('a',self.d['id'],p,'paid');self.s.process_next()
        r=self.s.run('a',run['id']);self.assertEqual(r['status'],'succeeded')
        self.assertEqual(r['calls'],1);self.assertEqual(r['telemetry'][0]['usage']['total_tokens'],20)
        ledger=self.s.bundle('a',self.d['id'])['runs'];self.assertEqual(ledger[0]['calls'],1)
        self.assertNotIn('intent',ledger[0]);self.assertEqual(ledger[0]['telemetry'][0]['usage']['total_tokens'],20)
        self.assertEqual(self.s.submit('a',self.d['id'],p,'paid')['id'],run['id'])
        self.assertEqual(self.fake.calls,1)
    def test_completed_records_reused_with_new_intent(self):
        self.approve()
        for intent in ('first','reuse'):
            d=self.s.dataset('a',self.d['id'])
            run=self.s.submit('a',d['id'],dict(method='ai',revision=d['revision'],max_calls=1),intent)
            self.s.process_next()
            self.assertEqual(self.s.run('a',run['id'])['status'],'succeeded')
        self.assertEqual(self.fake.calls,1)
        self.assertEqual(self.s.dataset('a',self.d['id'])['coded_records'],3)

    def test_empty_annotation_is_completed_and_reused(self):
        self.approve()
        def empty(kind,payload):
            self.fake.calls+=1
            return {'records':[dict(source_id=x['source_id'],annotations=[]) for x in payload['records']]},{}
        self.fake.call=empty
        for intent in ('empty','empty-reuse'):
            d=self.s.dataset('a',self.d['id'])
            run=self.s.submit('a',d['id'],dict(method='ai',revision=d['revision'],max_calls=1),intent)
            self.s.process_next()
            self.assertEqual(self.s.run('a',run['id'])['status'],'succeeded')
        self.assertEqual(self.fake.calls,1)
        self.assertEqual(self.s.dataset('a',self.d['id'])['report']['annotated_records'],0)
        self.assertEqual(self.s.dataset('a',self.d['id'])['coded_records'],3)

    def test_fabricated_evidence_fails_without_retry(self):
        self.approve();self.fake.invalid=True
        run=self.s.submit('a',self.d['id'],dict(method='ai',revision=self.d['revision'],max_calls=1),'bad')
        self.s.process_next();r=self.s.run('a',run['id'])
        self.assertEqual(r['status'],'failed');self.assertEqual(self.fake.calls,1)
        self.assertEqual(r['calls'],1);self.assertEqual(len(r['telemetry']),1)
        self.assertEqual(self.s.dataset('a',self.d['id'])['report']['annotated_records'],0)
    def test_ai_draft_stops_for_approval(self):
        run=self.s.submit('a',self.d['id'],dict(method='ai',kind='draft',revision=self.d['revision'],max_calls=1),'draft')
        self.s.process_next();d=self.s.dataset('a',self.d['id'])
        self.assertFalse(d['approved']);self.assertEqual(d['tags'],TAGS)
        self.assertEqual(self.s.run('a',run['id'])['status'],'succeeded')
    def test_no_external_consent_blocks_paid_call(self):
        with self.s.db() as db:db.execute('UPDATE datasets SET external=0')
        self.approve()
        with self.assertRaises(InsightError):self.s.submit('a',self.d['id'],dict(method='ai',revision=self.d['revision']),'blocked')
        self.assertEqual(self.fake.calls,0)


class ModelTests(unittest.TestCase):
    def test_configuration_private_and_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'secret.json';p.write_text(json.dumps({'base_url':'https://example.com/v1','api_key':'secret','model':'test'}));p.chmod(0o644)
            self.assertFalse(ResponsesModel(str(p)).capabilities()['configured'])
            p.chmod(0o600);m=ResponsesModel(str(p));self.assertTrue(m.capabilities()['configured'])
            self.assertNotIn('secret',json.dumps(m.capabilities()))
    def test_output_array_all_messages_and_usage(self):
        import contextlib
        result={'status':'completed','output':[{'type':'reasoning'},{'type':'message','content':[{'type':'output_text','text':'{"tags": []}'}]}],'usage':{'input_tokens':10,'output_tokens':3,'total_tokens':13}}
        fake=io.BytesIO(json.dumps(result).encode())
        with patch.object(ResponsesModel,'config',return_value={'base_url':'https://example.com/v1','api_key':'secret','model':'test'}),patch('realtime.insights_model.build_opener') as op:
            op.return_value.open.return_value=fake
            value,meta=ResponsesModel().call('draft',{})
        self.assertEqual(value,{'tags':[]});self.assertEqual(meta['usage']['total_tokens'],13)

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class InsightSourceTests(unittest.TestCase):
    from tests.test_product import ProductIntegrationTests as _P
    from tests.test_customer_data_api import CustomerDataDatabaseTests as _C
    setUp=_P.setUp
    tearDown=_P.tearDown
    allow_more=_C.allow_more
    record=_C.record

    def test_actual_sql_private_isolation_and_revocation(self):
        from realtime.insights_source import SavedSearchSource
        source=SavedSearchSource(self.dsn);self.allow_more()
        _,cid,mine=self.record(query='mine')
        _,_,public=self.record(query='public',private=False)
        other=self.store.authenticate(self.other['api_key'],rate_limit=False)
        _,_,foreign=self.record(query='foreign',owner=other)
        rows,manifest=source.load(str(self.owner['id']),dict(scope='accessible',request_limit=100))
        self.assertEqual(set(manifest['request_ids']),{mine,public})
        self.assertNotIn(foreign,manifest['request_ids'])
        self.assertEqual(len(rows),2);source.validate(str(self.owner['id']),manifest)
        with self.assertRaises(InsightError):source.validate(str(other['id']),manifest)
        with self.base.connect() as db:
            db.execute("UPDATE sf_jobs SET account_id=%s WHERE campaign_id=%s",(other['id'],cid))
        with self.assertRaises(InsightError):source.validate(str(self.owner['id']),manifest)
        source.store.base.pool.close()

    def test_search_inputs_are_not_reviews_and_freeze_source_ids(self):
        from realtime.insights_source import SavedSearchSource
        source=SavedSearchSource(self.dsn)
        _,_,rid=self.record(query='a topic',private=False,response={'organic_results':[dict(title='A title',snippet='A real saved snippet',link='https://example.com/a',position=1)]})
        with tempfile.TemporaryDirectory() as tmp:
            s=InsightService(Path(tmp)/'a.db',source_loader=source.load,source_validator=source.validate,start_worker=False)
            d=s.create_dataset(str(self.owner['id']),dict(name='search',mode='search',filters={'scope':'public','query':'a topic'}),'real-sql')
            r=s.records(str(self.owner['id']),d['id'])['records'][0]
            self.assertEqual(r['text_raw'],'A title\nA real saved snippet');self.assertEqual(r['request_id'],rid)
            self.assertIsNone(r['rating']);self.assertIsNone(r['brand'])
            self.assertEqual(r['source_fields']['query'],'a topic')
            self.assertEqual(r['source_fields']['title'],'A title')
            self.assertEqual(r['source_fields']['snippet'],'A real saved snippet')
            self.assertIsNotNone(r['source_fields']['collected_at'])
            self.assertEqual(r['source_fields']['collection_time_basis'],'request_completed_at')
            self.assertEqual(r['source_fields']['source_record']['organic_position'],1)
            with self.assertRaises(InsightError):s.create_dataset(str(self.owner['id']),dict(name='bad',mode='voc',filters={'scope':'public'}),'bad')
            s.close()
        source.store.base.pool.close()
