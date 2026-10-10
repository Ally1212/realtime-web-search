"""Live supervision must work alongside writers without running DDL migrations."""
import importlib.util
import os
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, _INITIALIZED_DSNS, _POOLS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hourly_schema_control', ROOT / 'scripts/google_hourly_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires TEST_DATABASE_URL')
class HourlyExistingSchemaTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ['TEST_DATABASE_URL']
        self.schema = 'hourly_schema_' + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema} -c lock_timeout=200ms')

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def prepare_schema(self):
        store = control.controller_store(self.dsn, 'prepare')
        with store.connect() as db:
            db.execute((ROOT / 'migrations/20261009_body_admission_audit.sql').read_text())
            db.execute('CREATE TABLE wml_continuous_runs (id text PRIMARY KEY,started_at timestamptz,baseline_page_id bigint,cursor bigint,cycle_date date)')
            db.execute('CREATE TABLE wml_continuous_campaigns (run_id text,campaign_id uuid,sequence bigint,queued boolean)')
        return store

    def test_prepare_bootstraps_empty_schema(self):
        store = control.controller_store(self.dsn, 'prepare')
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM campaigns').fetchone()['n'], 0)
        self.assertIn(self.dsn, _INITIALIZED_DSNS)

    def test_live_actions_and_nested_store_reuse_schema_while_pages_is_write_locked(self):
        self.prepare_schema()
        with psycopg.connect(self.dsn) as writer:
            # Compatible with reads, incompatible with ALTER TABLE in initialize.
            writer.execute('LOCK TABLE pages IN ROW EXCLUSIVE MODE')
            for action in ('tick', 'anchor', 'finish'):
                with self.subTest(action=action):
                    _INITIALIZED_DSNS.discard(self.dsn)
                    store = control.controller_store(self.dsn, action)
                    with store.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) n FROM pages').fetchone()['n'], 0)
                    # Frozen tick/finish construct a second ordinary CampaignStore.
                    nested = CampaignStore(self.dsn)
                    with nested.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) n FROM pages').fetchone()['n'], 0)

    def test_missing_required_column_fails_without_registering_schema(self):
        store = self.prepare_schema()
        with store.connect() as db:
            db.execute('ALTER TABLE google_serp_attempts DROP COLUMN result_urls')
        _INITIALIZED_DSNS.discard(self.dsn)
        with self.assertRaises(psycopg.errors.UndefinedColumn):
            control.controller_store(self.dsn, 'tick')
        self.assertNotIn(self.dsn, _INITIALIZED_DSNS)

    def test_unknown_action_does_not_initialize_empty_schema(self):
        with self.assertRaisesRegex(ValueError, 'Unknown action'):
            control.controller_store(self.dsn, 'invalid')
        self.assertNotIn(self.dsn, _INITIALIZED_DSNS)
        with psycopg.connect(self.dsn) as db:
            self.assertIsNone(db.execute("SELECT to_regclass('campaigns')").fetchone()[0])
