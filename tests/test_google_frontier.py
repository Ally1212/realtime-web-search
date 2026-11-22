import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from realtime.campaign_store import CampaignStore


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires TEST_DATABASE_URL")
class GoogleFrontierIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = CampaignStore(os.environ["TEST_DATABASE_URL"])
        self.campaign_id = self.store.create_campaign("frontier-test", [], 1, "direct")

    def tearDown(self):
        with self.store.connect() as connection:
            connection.execute("DELETE FROM campaigns WHERE id=%s", (self.campaign_id,))

    def test_four_exclusive_batches_cover_pages_one_through_eleven(self):
        expected_batches = ((1, 3), (4, 6), (7, 9), (10, 11))
        for start, end in expected_batches:
            batch = self.store.acquire_google_page_batch(
                self.campaign_id, "AI after:2026-09-01", "en-SG", 11, 3, 21600
            )
            self.assertIsNotNone(batch)
            self.assertEqual((batch["start_page"], batch["end_page"]), (start, end))
            self.assertIsNone(self.store.acquire_google_page_batch(
                self.campaign_id, "AI after:2026-09-01", "en-SG", 11, 3, 21600
            ))
            for page in range(start, end + 1):
                self.assertTrue(self.store.record_google_page_result(
                    batch, page, success=True, result_count=10,
                    unique_count=9, novel_count=4,
                ))
            if end < 11:
                self.assertEqual(self.store.next_google_frontier_delay(self.campaign_id), 0)
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT state,next_page,page_stats FROM google_page_frontier "
                "WHERE campaign_id=%s", (self.campaign_id,),
            ).fetchone()
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["next_page"], 12)
        self.assertEqual(len(row["page_stats"]), 11)

    def test_expired_lease_and_failed_page_resume_without_skipping(self):
        batch = self.store.acquire_google_page_batch(
            self.campaign_id, "AI failure test", "en-SG", 11, 3, 21600
        )
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE google_page_frontier SET lease_expires_at=now()-interval '1 second' "
                "WHERE campaign_id=%s", (self.campaign_id,),
            )
        recovered = self.store.acquire_google_page_batch(
            self.campaign_id, "AI failure test", "en-SG", 11, 3, 21600
        )
        self.assertEqual(recovered["start_page"], 1)
        self.assertNotEqual(recovered["lease_token"], batch["lease_token"])
        self.assertTrue(self.store.record_google_page_result(
            recovered, 1, success=False, error="google_captcha", captcha=True
        ))
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT state,next_page FROM google_page_frontier WHERE campaign_id=%s",
                (self.campaign_id,),
            ).fetchone()
            connection.execute(
                "UPDATE google_page_frontier SET next_run_at=now()-interval '1 second' "
                "WHERE campaign_id=%s", (self.campaign_id,),
            )
        self.assertEqual((row["state"], row["next_page"]), ("cooling", 1))
        resumed = self.store.acquire_google_page_batch(
            self.campaign_id, "AI failure test", "en-SG", 11, 3, 21600
        )
        self.assertEqual(resumed["start_page"], 1)

    def test_release_mid_batch_preserves_successful_page_and_rejects_old_owner(self):
        b=self.store.acquire_google_page_batch(self.campaign_id,'pause batch','en-SG',2,2,21600)
        self.assertTrue(self.store.record_google_page_result(b,1,success=True,result_count=8))
        self.assertTrue(self.store.release_google_page_batch(b))
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM google_page_frontier WHERE campaign_id=%s',(self.campaign_id,)).fetchone()
        self.assertEqual((row['state'],row['next_page'],row['attempts']),('pending',2,0))
        self.assertEqual(list(row['page_stats']),['1'])
        self.assertIsNone(row['lease_token']);self.assertIsNone(row['lease_expires_at'])
        new=self.store.acquire_google_page_batch(self.campaign_id,'pause batch','en-SG',2,2,21600)
        self.assertEqual(new['start_page'],2)
        self.assertFalse(self.store.release_google_page_batch(b))
        self.assertTrue(self.store.record_google_page_result(new,2,success=True,result_count=8))

    def test_unchanged_reconcile_and_lease_check_do_not_rewrite_frontier(self):
        batch=self.store.acquire_google_page_batch(self.campaign_id,'same query','en-SG',11,1,21600)
        self.assertIsNotNone(batch)

        def stored():
            with self.store.connect() as db:
                return db.execute(
                    'SELECT ctid::text AS tuple_id,updated_at,enabled,max_page,lease_token '
                    'FROM google_page_frontier WHERE campaign_id=%s',(self.campaign_id,),
                ).fetchone()

        before=stored()
        self.store.reconcile_google_queries(self.campaign_id,('same query',),'en-SG')
        self.assertIsNone(self.store.acquire_google_page_batch(
            self.campaign_id,'same query','en-SG',11,1,21600))
        self.assertEqual(stored(),before)
        self.assertIsNone(self.store.acquire_google_page_batch(
            self.campaign_id,'same query','en-SG',9,1,21600))
        self.assertEqual(stored()['max_page'],9)
        self.assertEqual(stored()['lease_token'],before['lease_token'])
        self.store.reconcile_google_queries(self.campaign_id,('another query',),'en-SG')
        self.assertFalse(stored()['enabled'])

    def test_three_page_discovery_batch_resumes_pause_and_reaches_page_eleven(self):
        from realtime.discovery import SearchDiscovery, SearchResult

        paused=False
        pages=[]
        query='realistic batch pause'

        def fetch_page(term,page):
            nonlocal paused
            self.assertEqual(term,query)
            pages.append(page)
            if page==2:
                paused=True
            return [SearchResult(f'https://example.com/result/{page}',f'Page {page}',('google_web',))]

        discovery=SearchDiscovery(
            google_web_max_pages=11,
            google_web_pages_per_batch=3,
            page_batch_acquirer=lambda term,locale,maximum,size: self.store.acquire_google_page_batch(
                self.campaign_id,term,locale,maximum,size,21600),
            page_batch_releaser=self.store.release_google_page_batch,
            page_result_recorder=self.store.record_google_page_result,
        )
        discovery.should_continue=lambda: not paused
        discovery._discover_google_page=fetch_page
        self.assertEqual(len(discovery.discover(query,11)[0]),2)
        paused=False
        for _ in range(3):
            self.assertEqual(len(discovery.discover(query,11)[0]),3)
        self.assertEqual(pages,list(range(1,12)))
        with self.store.connect() as db:
            row=db.execute(
                'SELECT state,next_page,lease_token FROM google_page_frontier WHERE campaign_id=%s',
                (self.campaign_id,),
            ).fetchone()
        self.assertEqual((row['state'],row['next_page'],row['lease_token']),('completed',12,None))

    def test_local_proxy_wait_preserves_page_and_upstream_retry_budget(self):
        def lease():
            return self.store.acquire_google_page_batch(
                self.campaign_id, 'temporary capacity', 'en-SG', 11, 1, 21600
            )

        batch = lease()
        self.assertTrue(self.store.record_google_page_result(batch, 1, success=True))
        batch = lease()
        self.assertTrue(self.store.record_google_page_result(
            batch, 2, success=False, error='google_timeout'
        ))
        for _ in range(8):
            with self.store.connect() as db:
                db.execute('UPDATE google_page_frontier SET next_run_at=now() WHERE campaign_id=%s',
                           (self.campaign_id,))
            batch = lease()
            self.assertEqual(batch['start_page'], 2)
            self.assertTrue(self.store.record_google_page_result(
                batch, 2, success=False, error='google_proxy_unavailable'
            ))
            self.assertIsNone(lease())
        with self.store.connect() as db:
            row = db.execute(
                """SELECT *, extract(epoch FROM next_run_at-now()) AS remaining
                   FROM google_page_frontier WHERE campaign_id=%s""",
                (self.campaign_id,),
            ).fetchone()
        self.assertEqual((row['state'], row['next_page'], row['attempts']), ('pending', 2, 1))
        self.assertEqual(row['page_stats']['1']['status'], 'success')
        self.assertEqual(row['page_stats']['2']['status'], 'failed')
        self.assertGreater(row['remaining'], 55)
        self.assertLessEqual(row['remaining'], 60)

        with self.store.connect() as db:
            db.execute('UPDATE google_page_frontier SET next_run_at=now() WHERE campaign_id=%s',
                       (self.campaign_id,))
        self.assertTrue(self.store.record_google_page_result(
            lease(), 2, success=False, error='google_proxy_unavailable', retry_after_seconds=12
        ))
        remaining = self.store.next_google_frontier_delay(self.campaign_id)
        self.assertGreater(remaining, 11)
        self.assertLessEqual(remaining, 12)

    def test_exit_scoped_403_uses_short_half_open_retry(self):
        batch = self.store.acquire_google_page_batch(
            self.campaign_id, 'exit blocked', 'en-SG', 11, 3, 21600
        )
        self.assertTrue(self.store.record_google_page_result(
            batch, 1, success=False, error='google_http_403'
        ))
        with self.store.connect() as db:
            row = db.execute(
                "SELECT state,attempts,extract(epoch FROM next_run_at-now()) remaining "
                "FROM google_page_frontier WHERE campaign_id=%s", (self.campaign_id,)
            ).fetchone()
        self.assertEqual((row['state'], row['attempts']), ('cooling', 1))
        self.assertGreater(row['remaining'], 55)
        self.assertLessEqual(row['remaining'], 60)

    def test_global_rate_limit_keeps_conservative_cooldown(self):
        batch = self.store.acquire_google_page_batch(
            self.campaign_id, 'globally limited', 'en-SG', 11, 3, 21600
        )
        self.assertTrue(self.store.record_google_page_result(
            batch, 1, success=False, error='google_http_429'
        ))
        with self.store.connect() as db:
            remaining = db.execute(
                "SELECT extract(epoch FROM next_run_at-now()) remaining "
                "FROM google_page_frontier WHERE campaign_id=%s", (self.campaign_id,)
            ).fetchone()['remaining']
        self.assertGreater(remaining, 1795)
        self.assertLessEqual(remaining, 1800)

    def test_retry_schedule_preserves_subsecond_deadline(self):
        batch = self.store.acquire_google_page_batch(self.campaign_id, 'fractional wait', 'en-SG', 11, 3, 21600)
        self.assertTrue(self.store.record_google_page_result(
            batch, 1, success=False, error='google_proxy_unavailable', retry_after_seconds=12,
        ))
        with self.store.connect() as db:
            due = db.execute('SELECT next_run_at FROM google_page_frontier WHERE campaign_id=%s',
                             (self.campaign_id,)).fetchone()['next_run_at']
        with patch('realtime.campaign_store.datetime') as clock:
            clock.now.return_value = due - timedelta(milliseconds=375)
            self.assertEqual(self.store.next_google_frontier_delay(self.campaign_id), .375)
            clock.now.return_value = due
            self.assertEqual(self.store.next_google_frontier_delay(self.campaign_id), 0)


if __name__ == "__main__":
    unittest.main()
