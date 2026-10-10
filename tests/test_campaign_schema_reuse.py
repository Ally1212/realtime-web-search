"""Real cold-process coverage of versioned schema initialization."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, SCHEMA, _INITIALIZED_DSNS, _POOLS


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires TEST_DATABASE_URL')
class CampaignSchemaReuseTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ['TEST_DATABASE_URL']
        self.schema = 'schema_reuse_' + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema} -c lock_timeout=2s')

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def child(self, dsn):
        env = dict(os.environ, FIXTURE_DSN=dsn, POSTGRES_POOL_MIN='1', POSTGRES_POOL_MAX='2')
        code = '''import os
from realtime.campaign_store import CampaignStore, _POOLS
s=CampaignStore(os.environ['FIXTURE_DSN'])
with s.connect() as db:
 assert db.execute('SELECT count(*) AS n FROM campaigns').fetchone()['n']==0
for p in _POOLS.values(): p.close()
print('schema-ready')
'''
        return subprocess.Popen([sys.executable, '-c', code], env=env,
            cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def check_children(self, children):
        for child in children:
            try:
                out, _ = child.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                self.fail('cold-process initialization did not complete')
            self.assertEqual(child.returncode, 0, 'cold-process initialization failed; raw stderr withheld')
            self.assertEqual(out.strip(), 'schema-ready')

    def test_two_real_processes_bootstrap_one_committed_revision(self):
        self.check_children([self.child(self.dsn), self.child(self.dsn)])
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM campaign_schema_migrations WHERE name LIKE 'campaign_schema_v1_%'").fetchone()[0], 1)

    def test_cold_readonly_process_reuses_schema_while_pages_has_a_writer(self):
        CampaignStore(self.dsn)
        readonly = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema} -c default_transaction_read_only=on -c lock_timeout=200ms')
        with psycopg.connect(self.dsn) as writer:
            writer.execute('LOCK TABLE pages IN ROW EXCLUSIVE MODE')
            self.check_children([self.child(readonly)])

    def test_schema_change_gets_a_new_marker_and_failed_migration_is_atomic(self):
        store = CampaignStore(self.dsn)
        original = store._schema_marker()
        with patch('realtime.campaign_store.SCHEMA', SCHEMA + '\nCREATE TABLE revision_fixture(id integer);'):
            store.initialize(reuse_existing=True)
            changed = store._schema_marker()
        self.assertNotEqual(original, changed)
        with patch('realtime.campaign_store.SCHEMA', SCHEMA + '\nCREATE TABLE rollback_fixture(id integer); SELECT 1/0;'):
            failed = store._schema_marker()
            with self.assertRaises(psycopg.errors.DivisionByZero):
                store.initialize(reuse_existing=True)
        with store.connect() as db:
            self.assertIsNotNone(db.execute("SELECT to_regclass('revision_fixture') AS n").fetchone()['n'])
            self.assertIsNone(db.execute("SELECT to_regclass('rollback_fixture') AS n").fetchone()['n'])
            self.assertIsNone(db.execute('SELECT 1 FROM campaign_schema_migrations WHERE name=%s', (failed,)).fetchone())
