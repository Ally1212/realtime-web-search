import hashlib
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from realtime.campaign_store import CampaignStore, PageRecord, _INITIALIZED_DSNS, _POOLS
from realtime.google_run_audit import read_first_body_window

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('augmented_hour_control', ROOT / 'scripts/google_hourly_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires TEST_DATABASE_URL')
class GoogleRunAuditTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ['TEST_DATABASE_URL']
        self.schema = 'run_audit_' + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema}')
        self.store = CampaignStore(self.dsn)
        self.start = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.end = self.start + timedelta(hours=1)
        self.campaign = self.store.create_campaign('fixture', [], 1000000, 'direct')
        with self.store.connect() as db:
            db.execute((ROOT / 'migrations/20261009_body_admission_audit.sql').read_text())
            db.execute('CREATE TABLE wml_continuous_runs(id text PRIMARY KEY, started_at timestamptz,baseline_page_id bigint)')
            db.execute('CREATE TABLE wml_continuous_campaigns(run_id text,campaign_id uuid)')
            db.execute('INSERT INTO wml_continuous_runs VALUES(%s,%s,0)', ('fixture-run', self.start))
            db.execute('INSERT INTO wml_continuous_campaigns VALUES(%s,%s)', ('fixture-run', self.campaign))

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def page(self, body):
        return PageRecord('https://publisher.example/body', hashlib.sha256(body.encode()).hexdigest(),
                          'Fixture', body[:500], body, 'en', 200,
                          datetime.now(timezone.utc).isoformat(), ('google_news',))

    def readonly(self):
        return psycopg.connect(make_conninfo(self.root_dsn, options=f'-c search_path={self.schema} -c default_transaction_read_only=on'),
                               row_factory=dict_row)

    def test_refresh_keeps_original_window_count_and_current_hash_valid(self):
        self.store.record_page(self.campaign, self.page('Original body. ' * 50))
        self.store.record_page(self.campaign, self.page('Refreshed body. ' * 50))
        with self.readonly() as db:
            r = read_first_body_window(db, 'fixture-run', self.start, self.end, 1, 0)
            later = read_first_body_window(db, 'fixture-run', self.end, self.end + timedelta(hours=1), 0, 0)
        self.assertTrue(r['all_checks_passed'])
        self.assertEqual(r['counts']['qualified_bodies'], 1)
        self.assertTrue(later['all_checks_passed'])
        self.assertEqual(later['counts']['qualified_bodies'], 0)

    def test_body_drift_and_missing_hash_owner_are_reported_without_repair(self):
        self.store.record_page(self.campaign, self.page('Original body. ' * 50))
        with self.store.connect() as db:
            db.execute("UPDATE pages SET content='unexpected mutation'")
            db.execute('DELETE FROM page_content_hashes')
        with self.readonly() as db:
            r = read_first_body_window(db, 'fixture-run', self.start, self.end, 1, 0)
        self.assertEqual(r['counts']['qualified_bodies'], 1)
        self.assertEqual(r['counts']['current_body_hash_mismatches'], 1)
        self.assertEqual(r['counts']['current_hash_owner_missing'], 1)
        self.assertFalse(r['all_checks_passed'])

    def test_readwrite_connection_is_rejected(self):
        with self.store.connect() as db:
            with self.assertRaisesRegex(ValueError, 'read-only'):
                read_first_body_window(db, 'fixture-run', self.start, self.end, 0, 0)

    def test_augmented_fixed_hour_uses_real_aux_events_and_body_audit(self):
        self.store.record_page(self.campaign, self.page('Original body. ' * 50))
        from realtime.discovery import SearchDiscovery
        from test_discovery_aux_audit import response, FEED
        from unittest.mock import Mock, patch
        session = Mock();session.get.return_value = response('https://news.google.com/rss', FEED)
        with patch('realtime.discovery_audit.is_public_url', side_effect=lambda url: '127.0.0.1' not in url):
            SearchDiscovery(session=session, news_locales=('US:en',),
                auxiliary_audit_recorder=lambda **e: self.store.record_discovery_audit(self.campaign, **e)).discover_feeds(('one',), ('news',))
        manifest = dict(run='fixture-run',provider='openserp',sources=['google_web','google_news'],
                        deadline=self.end.isoformat(),auxiliary_audit=dict(revision='fixture-v1',enabled_since=self.start.isoformat()))
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp);(state / 'baseline-url-hashes.txt').write_text('')
            with self.readonly() as db:
                report = control.metrics(db, state, manifest, self.end)
        self.assertEqual(report['elapsed_seconds'], 3600)
        self.assertEqual(report['new_qualified_bodies'], 1)
        self.assertEqual(report['google_source_audit']['google_news_search_feed_calls'], 1)
        self.assertEqual(report['google_source_audit']['combined_valid_new_discovery_links'], 1)
        self.assertTrue(report['body_integrity_valid'])
        self.assertTrue(report['extended_audit_complete'])

    def test_augmented_report_preserves_unknown_unfinished_requests(self):
        self.store.record_page(self.campaign, self.page('Original body. ' * 50))
        self.store.record_discovery_audit(self.campaign, event_id=str(uuid4()), trace_id=str(uuid4()),
            request_id=str(uuid4()),kind='request_started',source='google_news',operation='feed',
            occurred_at=datetime.now(timezone.utc).isoformat(),
            data=dict(method='GET',request_host='news.google.com',request_url_hash='f'*64))
        manifest = dict(run='fixture-run',provider='openserp',sources=['google_news'],
                        deadline=self.end.isoformat(),auxiliary_audit=dict(revision='fixture-v1',enabled_since=self.start.isoformat()))
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp);(state / 'baseline-url-hashes.txt').write_text('')
            with self.readonly() as db:
                report = control.metrics(db, state, manifest, self.end)
        aux = report['google_source_audit']['auxiliary']
        self.assertEqual(aux['unfinished_started_records'], 1)
        self.assertEqual(aux['completed_client_calls'], 0)
        self.assertIsNone(aux['explicit_captcha_rate'])
        self.assertFalse(report['extended_audit_complete'])
        self.assertTrue(report['body_integrity_valid'])
        self.assertIn('Incomplete auxiliary evidence or body integrity failure', report['alerts'])

    def test_missing_metadata_invalidates_body_audit_and_enablement_is_not_assumed(self):
        self.store.record_page(self.campaign, self.page('Original body. ' * 50))
        with self.store.connect() as db: db.execute('DELETE FROM page_admission_audit')
        manifest = dict(run='fixture-run',provider='openserp',sources=['google_news'],
                        deadline=self.end.isoformat(),auxiliary_audit=dict(revision='fixture-v1'))
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp);(state / 'baseline-url-hashes.txt').write_text('')
            with self.readonly() as db: report = control.metrics(db, state, manifest, self.end)
        self.assertFalse(report['body_integrity_valid'])
        self.assertFalse(report['auxiliary_enablement_valid'])
        self.assertEqual(report['google_source_audit'], dict(status='unavailable', metrics=None))
        self.assertEqual(report['missing_body_admission_rows'], 1)
