import os
import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.discovery import GoogleBlocked
from realtime.product import now
from realtime.web_live_admission import reserve_live_web
from tests.search_database_fixture import SearchDatabaseFixture


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class WebLiveAdmissionTests(SearchDatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, google_web_initial_rps=2, google_web_max_rps=4)
        self.exit = 'a' * 64

    def reserve(self):
        return reserve_live_web(self.base, self.config, self.exit, 'en', 60)

    def test_busy_exit_does_not_consume_either_source_slot(self):
        self.assertEqual(self.base.reserve_google_proxy(self.exit, 'en', 60), (True, 0))
        allowed, wait = self.reserve()
        self.assertFalse(allowed)
        self.assertGreater(wait, 55)
        with self.base.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM discovery_source_runtime').fetchone()['count'], 0)

    def test_blocked_second_source_rolls_back_exit_and_first_source(self):
        with self.base.connect() as db:
            db.execute("INSERT INTO discovery_source_runtime(source,current_rps,circuit_until) VALUES('google_web',2,%s)",
                       (now()+timedelta(minutes=1),))
        with self.assertRaises(GoogleBlocked) as blocked:
            self.reserve()
        self.assertEqual(blocked.exception.reason, 'google_web_circuit_open')
        with self.base.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM google_proxy_sessions WHERE proxy_key_hash=%s',(self.exit,)).fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM discovery_source_runtime WHERE source='google_openserp'").fetchone())

    def test_quarantine_recheck_uses_original_exit_and_respects_cooldown(self):
        old = now()-timedelta(hours=2)
        with self.base.connect() as db:
            db.execute("""INSERT INTO google_proxy_sessions(proxy_key_hash,successes,failures,
                       consecutive_failures,updated_at,last_used_at,cooldown_until)
                       VALUES(%s,1,30,4,%s,%s,%s)""",
                       (self.exit,old,old,now()+timedelta(minutes=2)))
        self.assertFalse(self.reserve()[0])
        with self.base.connect() as db:
            db.execute("UPDATE google_proxy_sessions SET cooldown_until=NULL WHERE proxy_key_hash=%s",(self.exit,))
        self.assertTrue(self.reserve()[0])
        self.base.record_google_proxy_result(self.exit,success=True,result_count=2)
        with self.base.connect() as db:
            db.execute("UPDATE google_proxy_sessions SET last_used_at=%s WHERE proxy_key_hash=%s",(old,self.exit))
        self.assertTrue(self.reserve()[0])
