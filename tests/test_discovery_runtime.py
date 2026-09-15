import os
import unittest
from uuid import uuid4

from realtime.campaign_store import CampaignStore


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires TEST_DATABASE_URL")
class DiscoveryRuntimeTests(unittest.TestCase):
    def test_aggressive_proxy_backoff_persists_and_only_success_resets_it(self):
        key=uuid4().hex.ljust(64,'0')
        try:
            for index,delay in enumerate((300,600,1200,1800,1800),1):
                self.store.record_google_proxy_result(key,success=False,error_code='google_captcha',cooldown_seconds=300)
                with self.store.connect() as db:
                    row=db.execute('SELECT limited_failure_streak,extract(epoch FROM cooldown_until-now()) AS wait FROM google_proxy_sessions WHERE proxy_key_hash=%s',(key,)).fetchone()
                self.assertEqual(row['limited_failure_streak'],index)
                self.assertAlmostEqual(float(row['wait']),delay,delta=3)
            self.store.record_google_proxy_result(key,success=False,error_code='google_timeout',cooldown_seconds=300)
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT limited_failure_streak FROM google_proxy_sessions WHERE proxy_key_hash=%s',(key,)).fetchone()['limited_failure_streak'],5)
            self.store.record_google_proxy_result(key,success=True)
            with self.store.connect() as db:
                row=db.execute('SELECT limited_failure_streak,cooldown_until>now() AS cooling FROM google_proxy_sessions WHERE proxy_key_hash=%s',(key,)).fetchone()
                self.assertEqual(row['limited_failure_streak'],0)
                self.assertTrue(row['cooling'])
                db.execute('UPDATE google_proxy_sessions SET cooldown_until=NULL WHERE proxy_key_hash=%s',(key,))
            self.store.record_google_proxy_result(key,success=False,error_code='google_http_429',cooldown_seconds=300)
            with self.store.connect() as db:
                row=db.execute('SELECT limited_failure_streak,extract(epoch FROM cooldown_until-now()) AS wait FROM google_proxy_sessions WHERE proxy_key_hash=%s',(key,)).fetchone()
                self.assertEqual(row['limited_failure_streak'],1)
                self.assertAlmostEqual(float(row['wait']),300,delta=3)
        finally:
            with self.store.connect() as db:db.execute('DELETE FROM google_proxy_sessions WHERE proxy_key_hash=%s',(key,))

    def test_proxy_aliases_share_pacing_and_keep_legacy_cooldown(self):
        group, a, b = [uuid4().hex.ljust(64, '0') for _ in range(3)]
        try:
            self.store.record_google_proxy_result(a, success=False, error_code='google_captcha', cooldown_seconds=300)
            allowed, wait = self.store.reserve_google_proxy_group(group, [a, b], 'zh-CN', 30)
            self.assertFalse(allowed)
            self.assertGreater(wait, 290)
            # Dropping an alias from a refreshed cache must not forget it.
            self.assertFalse(self.store.reserve_google_proxy_group(group, [b], 'zh-CN', 30)[0])
            self.store.record_google_proxy_result(a, success=True)
            self.assertFalse(self.store.reserve_google_proxy_group(group, [b], 'zh-CN', 30)[0])
            # Simulate passage of time; production never clears these limits.
            with self.store.connect() as connection:
                connection.execute("UPDATE google_proxy_sessions SET cooldown_until=now()-interval '1 second' WHERE proxy_key_hash=%s", (a,))
            self.assertTrue(self.store.reserve_google_proxy_group(group, [b], 'zh-CN', 30)[0])
            self.assertFalse(self.store.reserve_google_proxy(a, 'zh-CN', 30)[0])
            self.assertFalse(self.store.reserve_google_proxy_group(group, [a], 'zh-CN', 30)[0])
        finally:
            with self.store.connect() as connection:
                connection.execute('DELETE FROM google_proxy_aliases WHERE group_hash=%s', (group,))
                connection.execute('DELETE FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s)', ([group,a,b],))

    def test_proxy_group_concurrent_reservations_are_exclusive(self):
        from concurrent.futures import ThreadPoolExecutor
        group, a, b = [uuid4().hex.ljust(64, '0') for _ in range(3)]
        try:
            with ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(lambda _: self.store.reserve_google_proxy_group(group, [a,b], 'zh-CN', 30), range(6)))
            self.assertEqual(sum(allowed for allowed, _ in results), 1)
        finally:
            with self.store.connect() as connection:
                connection.execute('DELETE FROM google_proxy_aliases WHERE group_hash=%s', (group,))
                connection.execute('DELETE FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s)', ([group,a,b],))

    def test_opt_in_rate_cap_and_default_cap_share_one_budget(self):
        first = self.store.acquire_discovery_slot(self.source, 100, maximum_rps=4)
        self.assertEqual(first['current_rps'], 4)
        with self.store.connect() as connection:
            previous = connection.execute('SELECT next_request_at FROM discovery_source_runtime WHERE source=%s', (self.source,)).fetchone()['next_request_at']
        second = self.store.acquire_discovery_slot(self.source, 100)
        self.assertEqual(second['current_rps'], 2)
        with self.store.connect() as connection:
            current = connection.execute('SELECT next_request_at FROM discovery_source_runtime WHERE source=%s', (self.source,)).fetchone()['next_request_at']
        self.assertAlmostEqual((current-previous).total_seconds(), .5, places=3)

    def test_larger_requested_rate_cannot_override_open_circuit(self):
        for _ in range(3):
            self.store.acquire_discovery_slot(self.source, 1)
            self.store.record_discovery_result(self.source, success=False, error_code='google_timeout')
        self.assertFalse(self.store.acquire_discovery_slot(self.source, 4, maximum_rps=4)['allowed'])

    def setUp(self):
        self.store = CampaignStore(os.environ["TEST_DATABASE_URL"])
        self.source = "google_test_" + uuid4().hex

    def tearDown(self):
        with self.store.connect() as connection:
            connection.execute("DELETE FROM discovery_source_runtime WHERE source=%s", (self.source,))

    def test_timeouts_trip_circuit_without_captcha_and_success_does_not_reopen_it(self):
        for _ in range(3):
            self.store.acquire_discovery_slot(self.source, .5)
            self.store.record_discovery_result(self.source, success=False, error_code="google_timeout", elapsed_seconds=2)
        self.assertFalse(self.store.acquire_discovery_slot(self.source, .5)["allowed"])
        self.store.record_discovery_result(self.source, success=True, result_count=10, elapsed_seconds=1)
        self.assertFalse(self.store.acquire_discovery_slot(self.source, .5)["allowed"])
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM discovery_source_runtime WHERE source=%s", (self.source,)).fetchone()
            self.assertEqual(row["requests_total"], 4)
            self.assertEqual(row["errors_total"], 3)
            self.assertEqual(row["latency_seconds_total"], 7)
            self.assertEqual(row["state"], "circuit_open")

    def test_lease_is_exclusive_and_stale_owner_cannot_release_new_lease(self):
        key = uuid4().hex.ljust(64, "0")
        first = self.store.acquire_query_lease(key)
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.acquire_query_lease(key))
        with self.store.connect() as connection:
            connection.execute("UPDATE discovery_query_leases SET expires_at=now()-interval '1 second' WHERE cache_key=%s", (key,))
        second = self.store.acquire_query_lease(key)
        self.store.release_query_lease(key, first)
        self.assertIsNone(self.store.acquire_query_lease(key))
        self.store.release_query_lease(key, second)

    def test_one_proxy_captcha_does_not_disable_the_entire_pool(self):
        for _ in range(4):
            self.store.acquire_discovery_slot(self.source, .5)
            self.store.record_discovery_result(
                self.source, success=False, captcha=True, shared_exit=False,
            )
            self.assertTrue(self.store.acquire_discovery_slot(self.source, .5)["allowed"])
        self.store.record_discovery_result(self.source, success=True, result_count=10)
        self.assertTrue(self.store.acquire_discovery_slot(self.source, .5)["allowed"])

    def test_healthy_window_recovers_requested_starting_rate(self):
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO discovery_source_runtime(source,state,current_rps,requests_window,"
                "successes_window,captcha_window,consecutive_failures) "
                "VALUES(%s,'healthy',0.125,10,10,0,0)", (self.source,),
            )
        slot = self.store.acquire_discovery_slot(self.source, 1.0)
        self.assertTrue(slot["allowed"])
        self.assertEqual(slot["current_rps"], 1.0)

    def test_proxy_health_requires_actual_search_success(self):
        key = uuid4().hex.ljust(64, "0")
        self.store.record_google_proxy_result(key, success=False, cooldown_seconds=300)
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM google_proxy_sessions WHERE proxy_key_hash=%s", (key,)).fetchone()
            self.assertIsNone(row["last_success_at"])
        self.store.record_google_proxy_result(key, success=True)
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM google_proxy_sessions WHERE proxy_key_hash=%s", (key,)).fetchone()
            self.assertIsNotNone(row["last_success_at"])
            self.assertIsNotNone(row["cooldown_until"])
            connection.execute("DELETE FROM google_proxy_sessions WHERE proxy_key_hash=%s", (key,))

    def test_search_failure_does_not_demote_keyword_or_delay_its_page_retry(self):
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO continuous_keywords(keyword_key,concept_id,query,language,category,kind,state,score) "
                "VALUES(%s,'test','AI','en','custom','base','active',70)", (self.source,),
            )
        before = dict(candidates=0, fetched=0, delivered=0, duplicates=0, failed=0, discovery_errors=0)
        try:
            self.store.record_continuous_keyword_run(self.source, before, before | {"discovery_errors": 1}, 10, retry_delay=60)
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT score,low_yield_runs,extract(epoch FROM next_run_at-now()) AS delay "
                    "FROM continuous_keywords WHERE keyword_key=%s", (self.source,),
                ).fetchone()
            self.assertEqual(row["score"], 70)
            self.assertEqual(row["low_yield_runs"], 0)
            self.assertGreater(row["delay"], 0)
            self.assertLessEqual(row["delay"], 60)
        finally:
            with self.store.connect() as connection:
                connection.execute("DELETE FROM continuous_keyword_runs WHERE keyword_key=%s", (self.source,))
                connection.execute("DELETE FROM continuous_keywords WHERE keyword_key=%s", (self.source,))

    def test_obsolete_query_cursor_is_preserved_but_not_scheduled(self):
        campaign = self.store.create_campaign("AI", [], 1, "direct")
        try:
            self.store.acquire_google_page_batch(campaign, "AI", "en-SG", 11, 3, 21600)
            self.store.acquire_google_page_batch(campaign, "人工智能", "en-SG", 11, 3, 21600)
            self.store.reconcile_google_queries(campaign, ("AI",), "en-SG")
            with self.store.connect() as connection:
                rows = connection.execute("SELECT query,enabled FROM google_page_frontier WHERE campaign_id=%s", (campaign,)).fetchall()
            self.assertEqual({r["query"]: r["enabled"] for r in rows}, {"AI": True, "人工智能": False})
        finally:
            with self.store.connect() as connection:
                connection.execute("DELETE FROM campaigns WHERE id=%s", (campaign,))
