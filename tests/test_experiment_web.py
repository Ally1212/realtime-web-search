import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from realtime.experiment_store import ExperimentStore
from realtime.experiment_web import ExperimentDashboard, ReadOnlyStore, timing


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(started_at=1000, deadline=87400, heartbeat=1095, state='running')
        self.runtime = dict(active=60, google_cooling=10, paused=20, offline=5, search_pacing_wait=50)

    def test_running_excludes_paused_offline_and_overlapping_pacing(self):
        result = timing(self.settings, self.runtime, 1100)
        self.assertEqual(result['elapsed_seconds'], 100)
        self.assertEqual(result['online_seconds'], 75)
        self.assertEqual(result['remaining_seconds'], 86300)
        self.assertEqual(result['display_state'], 'running')

    def test_pause_freezes_online_but_not_wall_clock(self):
        self.settings['state'] = 'paused'
        a = timing(self.settings, self.runtime, 1100)
        b = timing(self.settings, self.runtime, 1110)
        self.assertEqual(a['display_state'], 'paused')
        self.assertEqual(a['online_seconds'], b['online_seconds'])
        self.assertEqual(b['elapsed_seconds']-a['elapsed_seconds'], 10)

    def test_cooling_counts_online(self):
        self.settings['search_cooling_until'] = 1500
        result = timing(self.settings, self.runtime, 1100)
        self.assertEqual(result['display_state'], 'cooling')
        self.assertEqual(result['online_seconds'], 75)

    def test_stale_or_missing_heartbeat_is_not_healthy(self):
        for heartbeat in (None, 900):
            result = timing(dict(self.settings, heartbeat=heartbeat), self.runtime, 1100)
            self.assertEqual(result['display_state'], 'offline')
            self.assertEqual(result['online_seconds'], 70)

    def test_terminal_freezes_at_finish_or_deadline(self):
        for state in ('complete', 'stopped', 'storage_stopped'):
            settings = dict(self.settings, state=state, finished_at=1080)
            a = timing(settings, self.runtime, 1100)
            b = timing(settings, self.runtime, 200000)
            self.assertEqual(a['elapsed_seconds'], 80)
            self.assertEqual(a['elapsed_seconds'], b['elapsed_seconds'])
            self.assertEqual(b['display_state'], state)
            self.assertEqual(b['remaining_seconds'], 0)
        result = timing(dict(settings, finished_at=90000), self.runtime, 100000)
        self.assertEqual(result['elapsed_seconds'], 86400)

    def test_deadline_stops_clock_without_midnight_reset(self):
        result = timing(dict(self.settings, heartbeat=87405), self.runtime, 87410)
        self.assertEqual(result['display_state'], 'draining')
        self.assertEqual(result['elapsed_seconds'], 86400)
        self.assertEqual(result['remaining_seconds'], 0)

    def test_initializing_and_online_clamped(self):
        self.assertEqual(timing({}, {}, 1100)['display_state'], 'initializing')
        self.assertEqual(timing(self.settings, {'active': 99999}, 1100)['online_seconds'], 100)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dashboard = ExperimentDashboard(self.root)
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.db.close()
        self.tmp.cleanup()

    def create(self, key='daily-test', **settings):
        store = ExperimentStore(self.root/key, create=True)
        self.stores.append(store)
        for field, value in dict(id=key, started_at=time.time()-30, deadline=time.time()+86400,
                                 state='running', heartbeat=time.time(), **settings).items():
            store.set(field, value)
        return store

    def test_catalog_prefers_formal_even_when_preflight_newer(self):
        first = self.create('daily-old')
        first.set('started_at', time.time()-1000)
        self.create('preflight-new', preflight=True)
        self.create('daily-new')
        result = self.dashboard.catalog()
        self.assertEqual(result['default_key'], 'daily-new')
        self.assertEqual([r['key'] for r in result['experiments']], ['daily-new', 'daily-old', 'preflight-new'])

    def test_empty_and_corrupt_catalog(self):
        self.assertEqual(self.dashboard.catalog()['experiments'], [])
        broken = self.root/'broken'
        broken.mkdir()
        db = sqlite3.connect(broken/'experiment.sqlite3')
        db.close()
        self.assertEqual(self.dashboard.catalog()['unavailable'], 1)

    def test_paths_reject_traversal_missing_and_symlinks(self):
        store = self.create()
        for key in ('../daily-test', '%2e%2e', '/tmp', '', 'a'*129):
            with self.assertRaises(ValueError):
                self.dashboard.path(key)
        with self.assertRaises(FileNotFoundError):
            self.dashboard.detail('missing')
        self.assertFalse((self.root/'missing').exists())
        (self.root/'linked').symlink_to(store.path.parent, target_is_directory=True)
        with self.assertRaises(FileNotFoundError):
            self.dashboard.path('linked')
        (self.root/'linked-file').mkdir()
        (self.root/'linked-file'/'experiment.sqlite3').symlink_to(store.path)
        with self.assertRaises(FileNotFoundError):
            self.dashboard.path('linked-file')

    def test_read_only_store_cannot_write_or_create(self):
        store = self.create()
        reader = ReadOnlyStore(store.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                reader.set('state', 'paused')
        finally:
            reader.db.close()
        self.assertEqual(store.get('state'), 'running')
        with self.assertRaises(sqlite3.OperationalError):
            ReadOnlyStore(self.root/'absent.sqlite3')
        self.assertFalse((self.root/'absent.sqlite3').exists())

    def test_detail_matches_counts_without_mutation_or_secret_settings(self):
        store = self.create(whale=True, secret='never-expose-this')
        key = store.add_query('topic', 'AI <script>', 'en', 'AI')
        row = store.db.execute('SELECT * FROM queries WHERE id=?', (key,)).fetchone()
        store.search(row, 1, time.time(), [{'url': 'https://example.org/ai', 'title': 'AI'}], [])
        now = time.time()
        with store.db:
            store.db.execute("INSERT INTO documents(id,finished,classification,quality,seconds) VALUES(1,?,'new','[]',1)", (now,))
            store.db.execute("INSERT INTO outbox(document_id,payload,status) VALUES(1,'{}','blocked_missing_publication')")
        before = list(store.db.iterdump())
        data = self.dashboard.detail('daily-test')
        self.assertEqual(data['metrics']['new'], 1)
        self.assertEqual(data['metrics']['whale_blocked_missing_publication'], 1)
        self.assertEqual(data['metrics'].get('whale_accepted', 0), 0)
        self.assertEqual(data['recent_new_per_minute'], 1)
        self.assertEqual(data['recent_searches'][0]['query'], 'AI <script>')
        self.assertEqual(data['query_counts']['enabled'], 1)
        self.assertNotIn('never-expose-this', json.dumps(data))
        self.assertEqual(list(store.db.iterdump()), before)
        self.assertIs(self.dashboard.detail('daily-test'), data)
        with patch('realtime.experiment_web.time.monotonic', return_value=time.monotonic()+4):
            self.assertIsNot(self.dashboard.detail('daily-test'), data)


if __name__ == '__main__':
    unittest.main()
