"""Execute the migration program against disposable real PostgreSQL databases."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from realtime.campaign_store import SCHEMA, CampaignStore, PageRecord, _POOLS

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires disposable PostgreSQL root')
class RevisionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn=os.environ['TEST_DATABASE_URL'];self.name='transition_'+uuid4().hex
        with psycopg.connect(self.root_dsn,autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.dsn=make_conninfo(self.root_dsn,dbname=self.name)
        self.run='legacy-continuous';self.campaign=str(uuid4())
        self.a='Original article text. '*50;self.b='Changed article text. '*50
        self.ah=hashlib.sha256(self.a.encode()).hexdigest();self.bh=hashlib.sha256(self.b.encode()).hexdigest()
        with psycopg.connect(self.dsn,row_factory=dict_row) as db:
            db.execute(SCHEMA)
            db.execute((ROOT/'migrations/20261009_body_admission_audit.sql').read_text())
            db.execute('CREATE TABLE wml_continuous_runs(id text PRIMARY KEY)')
            db.execute('CREATE TABLE wml_continuous_campaigns(run_id text,campaign_id uuid)')
            db.execute("INSERT INTO campaigns(id,query,daily_target,proxy_profile,status) VALUES(%s,'fixture',1000000,'direct','active')",(self.campaign,))
            db.execute('INSERT INTO wml_continuous_runs VALUES(%s)',(self.run,))
            db.execute('INSERT INTO wml_continuous_campaigns VALUES(%s,%s)',(self.run,self.campaign))
            db.execute("INSERT INTO pages(url,content_hash,title,summary,content,http_status,fetched_at,source_engines) "
                       "VALUES('https://example.invalid/original',%s,'Fixture','Fixture',%s,200,now(),'[\"google_web\"]')",(self.ah,self.a))
            db.execute('UPDATE pages SET content=%s',(self.b,))
            db.execute('INSERT INTO campaign_pages(campaign_id,page_id) SELECT %s,id FROM pages',
                       (self.campaign,))
            # Simulate the predecessor's missing history/audit schema.
            db.execute('DROP TABLE page_content_hashes,campaign_schema_migrations,discovery_aux_audit_events,discovery_known_result_urls')

    def tearDown(self):
        for key in list(_POOLS):
            if self.name in key[0]:_POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn,autocommit=True) as db:
            db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.name)))

    def execute(self, digest=None):
        expected=hashlib.sha256((ROOT/'realtime/campaign_store.py').read_bytes()).hexdigest()
        result=subprocess.run([sys.executable,str(ROOT/'scripts/google_revision_migrate.py'),digest or expected,self.run],
            env={**os.environ,'DATABASE_URL':self.dsn,'POSTGRES_POOL_MIN':'1','POSTGRES_POOL_MAX':'2'},
            text=True,capture_output=True,timeout=30,cwd=ROOT)
        return result,json.loads(result.stdout)

    def assert_unmodified(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as db:
            self.assertEqual(db.execute('SELECT status FROM campaigns WHERE id=%s',(self.campaign,)).fetchone()['status'],'active')
            self.assertIsNone(db.execute("SELECT to_regclass('page_content_hashes') n").fetchone()['n'])
            row=db.execute('SELECT content,content_hash FROM pages').fetchone()
            self.assertEqual((row['content'],row['content_hash']),(self.b,self.ah))

    def test_real_migration_preserves_old_text_audit_and_backfills_both_hashes(self):
        result,evidence=self.execute()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(evidence['all_checks_passed'])
        self.assertEqual(evidence['retired_active_campaigns'],1)
        self.assertEqual(evidence['before'],evidence['after'])
        self.assertEqual(evidence['after']['current_hash_mismatches'],1)
        self.assertEqual(evidence['retained_history_hashes'],2)
        self.assertEqual(evidence['readonly_constructor_state'],'on')
        with psycopg.connect(self.dsn,row_factory=dict_row) as db:
            r=db.execute('SELECT p.id,p.content,p.content_hash,a.original_content_hash FROM pages p JOIN page_admission_audit a ON a.page_id=p.id').fetchone()
            self.assertEqual((r['content'],r['content_hash'],r['original_content_hash']),(self.b,self.ah,self.ah))
            self.assertEqual(db.execute('SELECT status FROM campaigns').fetchone()['status'],'stopped')
        store=CampaignStore(self.dsn)
        from datetime import datetime,timezone
        record=PageRecord(url='https://example.invalid/duplicate',content_hash=self.bh,title='Fixture',summary='Fixture',
            content=self.b,language='en',http_status=200,fetched_at=datetime.now(timezone.utc).isoformat(),source_engines=('google_web',))
        outcome=store.record_page(self.campaign,record)
        self.assertEqual(outcome[:3],(r['id'],False,True))

    def test_existing_database_client_refuses_all_mutation(self):
        with psycopg.connect(self.dsn):
            result,evidence=self.execute()
        self.assertEqual(result.returncode,1)
        self.assertEqual(evidence['migration_error'],'RuntimeError')
        self.assert_unmodified()

    def test_unrelated_active_campaign_refuses_all_mutation(self):
        with psycopg.connect(self.dsn) as db:
            db.execute("INSERT INTO campaigns(id,query,daily_target,proxy_profile,status) VALUES(%s,'unrelated',1000000,'direct','active')",(str(uuid4()),))
        result,evidence=self.execute()
        self.assertEqual(result.returncode,1)
        self.assertEqual(evidence['migration_error'],'RuntimeError')
        self.assert_unmodified()

    def test_wrong_frozen_package_identity_refuses_all_mutation(self):
        result,evidence=self.execute('wrong-sha')
        self.assertEqual(result.returncode,1)
        self.assertEqual(evidence['migration_error'],'RuntimeError')
        self.assert_unmodified()
