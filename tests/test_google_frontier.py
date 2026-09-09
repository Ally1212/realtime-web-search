import os
import unittest

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
                self.assertLessEqual(self.store.next_google_frontier_delay(self.campaign_id), 2)
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


if __name__ == "__main__":
    unittest.main()
