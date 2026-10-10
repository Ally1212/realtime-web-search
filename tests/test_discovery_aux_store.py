import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock, patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, _INITIALIZED_DSNS, _POOLS
from realtime.discovery import SearchDiscovery
from realtime.discovery_audit_metrics import read_auxiliary_window, read_google_discovery_window
from test_discovery_aux_audit import FEED, response


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires TEST_DATABASE_URL')
class AuxiliaryAuditStoreTests(unittest.TestCase):
    def setUp(self):
        self.root_dsn = os.environ['TEST_DATABASE_URL']
        self.schema = 'source_audit_' + uuid4().hex
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options=f'-c search_path={self.schema}')
        self.store = CampaignStore(self.dsn)
        self.campaign = self.store.create_campaign('offline audit', [], 1000000, 'direct')
        self.events = []
        self.session = Mock()
        self.session.get.return_value = response('https://news.google.com/rss', FEED)
        self.public = patch('realtime.discovery_audit.is_public_url',
                            side_effect=lambda url: '127.0.0.1' not in url)
        self.public.start()
        self.addCleanup(self.public.stop)

    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for key in list(_POOLS):
            if key[0] == self.dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(self.root_dsn) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def recorder(self, **event):
        self.store.record_discovery_audit(self.campaign, **event)
        self.events.append(event)

    def discovery(self, recorder=None):
        return SearchDiscovery(session=self.session,
            feeds=(('google_news_rss', 'https://news.google.com/rss'),),
            auxiliary_audit_recorder=recorder or self.recorder)

    def metrics(self, start):
        dsn = make_conninfo(self.root_dsn,
                           options=f'-c search_path={self.schema} -c default_transaction_read_only=on')
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as db:
            return read_auxiliary_window(db, [self.campaign], start,
                                         datetime.now(timezone.utc) + timedelta(seconds=1), set())

    def test_actual_request_events_and_global_novelty_match_readonly_metrics(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        d = self.discovery()
        d.discover_feeds(('one',), ('generic',))
        d.discover_feeds(('two',), ('generic',))
        report = self.metrics(start)
        self.assertEqual(report['completed_client_calls'], 2)
        self.assertEqual(report['unique_eligible_discovered_links'], 1)
        self.assertEqual(report['valid_new_discovered_links'], 1)
        self.assertTrue(report['request_audit_complete'])
        self.assertTrue(report['discovery_batch_audit_complete'])
        self.assertEqual(report['database_read_only'], 'on')
        with self.store.connect() as db:
            rows = db.execute('SELECT kind,data FROM discovery_aux_audit_events').fetchall()
            self.assertEqual(len(rows), 8)
            self.assertEqual(db.execute('SELECT count(*) n FROM discovery_known_result_urls').fetchone()['n'], 1)
        self.assertNotIn('secret-body-not-stored', json.dumps(rows))

    def test_retry_cannot_overwrite_immutable_evidence_or_store_raw_payload(self):
        self.discovery().discover_feeds(('one',), ('generic',))
        event = self.events[-1]
        self.store.record_discovery_audit(self.campaign, **event)
        changed = copy.deepcopy(event)
        changed['data']['cache_hit'] = True
        with self.assertRaisesRegex(ValueError, 'conflicting immutable'):
            self.store.record_discovery_audit(self.campaign, **changed)
        changed = copy.deepcopy(event)
        changed['data']['proxy_password'] = 'secret'
        with self.assertRaisesRegex(ValueError, 'invalid auxiliary'):
            self.store.record_discovery_audit(self.campaign, **changed)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM discovery_aux_audit_events').fetchone()['n'], 4)

    def test_completion_write_failure_stays_detectable_in_real_database(self):
        def failing_recorder(**event):
            if event['kind'] == 'request_finished':
                raise RuntimeError('secret-database-error')
            self.recorder(**event)
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        results, errors = self.discovery(failing_recorder).discover_feeds(('one',), ('generic',))
        self.assertEqual(results, [])
        self.assertEqual(errors, ['google_news_rss: DiscoveryAuditFailure'])
        report = self.metrics(start)
        self.assertEqual(report['unfinished_started_records'], 1)
        self.assertFalse(report['request_audit_complete'])
        self.assertEqual(report['completed_client_calls'], 0)
        self.assertIsNone(report['explicit_captcha_rate'])

    def test_registry_uses_earliest_retained_observation_and_excludes_old_urls(self):
        self.discovery().discover_feeds(('one',), ('generic',))
        event = copy.deepcopy(self.events[-1])
        event['event_id'] = str(uuid4())
        event['trace_id'] = str(uuid4())
        event['occurred_at'] = '2000-01-01T00:00:00+00:00'
        event['data']['request_ids'] = []
        event['data']['cache_hit'] = True
        self.store.record_discovery_audit(self.campaign, **event)
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        report = self.metrics(start)
        self.assertEqual(report['valid_new_discovered_links'], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT first_seen FROM discovery_known_result_urls').fetchone()['first_seen'].year, 2000)

    def test_combined_links_union_history_and_keyword_vs_resolver_calls(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        d = SearchDiscovery(session=self.session, news_locales=('US:en',),
                            auxiliary_audit_recorder=self.recorder)
        d.discover_feeds(('one',), ('news',))
        self.session.get.return_value = response('https://news.google.com/articles/token', b'<html/>')
        from realtime.discovery import SearchResult
        d._resolve_google_news(SearchResult('https://news.google.com/rss/articles/token', 'Title', ('google_news',)))
        self.session.get.return_value = response('https://publisher.example/rss',
            b'<rss><channel><item><link>https://publisher.example/rss-new</link></item></channel></rss>')
        SearchDiscovery(session=self.session, feeds=(('rss_fixture', 'https://publisher.example/rss'),),
                        auxiliary_audit_recorder=self.recorder).discover_feeds(('one',), ('generic',))
        with self.store.connect() as db:
            db.execute("INSERT INTO discovery_known_result_urls(url,first_seen) VALUES(%s,%s)",
                       ('https://publisher.example/historical', start - timedelta(days=1)))
            db.execute("INSERT INTO google_serp_attempts(provider,query,page,classification,result_count,result_urls) "
                       "VALUES('openserp','fixture',1,'results',3,%s::jsonb)",
                       (json.dumps(['https://publisher.example/a', 'https://publisher.example/historical',
                                    'https://publisher.example/web']),))
        dsn = make_conninfo(self.root_dsn,
                           options=f'-c search_path={self.schema} -c default_transaction_read_only=on')
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as db:
            report = read_google_discovery_window(db, [self.campaign], start,
                                                  datetime.now(timezone.utc) + timedelta(seconds=1), set())
        self.assertEqual(report['google_web_serp_calls'], 1)
        self.assertEqual(report['google_news_search_feed_calls'], 1)
        self.assertEqual(report['completed_google_keyword_search_calls'], 2)
        self.assertEqual(report['other_auxiliary_client_calls'], 2)
        self.assertEqual(report['google_web_valid_new_links'], 2)
        self.assertEqual(report['overlapping_web_auxiliary_links'], 1)
        self.assertEqual(report['combined_valid_new_discovery_links'], 3)
        self.assertTrue(report['auxiliary']['request_audit_complete'])

    def test_report_cli_requires_enablement_and_tables_and_reads_real_events(self):
        from scripts.google_source_audit import main
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.discovery().discover_feeds(('one',), ('generic',))
        manifest = dict(run='fixture-run')
        report = dict(run='fixture-run', started_at=start.isoformat(),
                      measured_until=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
                      exclusive_provider=True, valid_for_comparison=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'audited-final.json').write_text(json.dumps(report))
            (root / 'baseline-url-hashes.txt').write_text('')
            def invoke():
                (root / 'manifest.json').write_text(json.dumps(manifest))
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch('scripts.google_source_audit.Config') as cfg:
                    cfg.return_value.database_url = self.dsn
                    code = main(['--run-directory', str(root)])
                return code, json.loads(output.getvalue())
            code, data = invoke()
            self.assertEqual(code, 3)
            self.assertEqual(data['status'], 'unavailable')
            self.assertIsNone(data['metrics'])
            manifest['auxiliary_audit'] = dict(revision='isolated-fixture-v1', enabled_since=start.isoformat())
            code, data = invoke()
            self.assertEqual(code, 3)
            self.assertEqual(data['database_read_only'], 'on')
            self.assertIn('wml_continuous_campaigns', data['reason'])
            with self.store.connect() as db:
                db.execute('CREATE TABLE wml_continuous_campaigns(run_id text,campaign_id uuid)')
                db.execute('INSERT INTO wml_continuous_campaigns VALUES(%s,%s)', ('fixture-run', self.campaign))
            code, data = invoke()
            self.assertEqual(code, 0)
            self.assertEqual(data['status'], 'available')
            self.assertEqual(data['metrics']['auxiliary']['completed_client_calls'], 1)
            self.assertEqual(data['metrics']['combined_valid_new_discovery_links'], 1)
            self.assertEqual(data['database_read_only'], 'on')
