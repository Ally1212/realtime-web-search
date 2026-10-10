import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('hour_archive',
    Path(__file__).resolve().parents[1] / 'scripts' / 'archive_google_hours.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class HourArchiveTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 9, 1, 54, 19, 191465, tzinfo=timezone.utc)

    def test_partial_hours_are_never_projected_or_archived(self):
        self.assertEqual(archive.completed_windows(self.start, self.start + timedelta(minutes=59),
                                                  self.start + timedelta(days=10)), [])

    def test_complete_hour_requires_commit_grace_and_keeps_worker_anchor(self):
        deadline = self.start + timedelta(days=10)
        self.assertEqual(archive.completed_windows(self.start, self.start + timedelta(hours=1), deadline), [])
        self.assertEqual(archive.completed_windows(self.start, self.start + timedelta(hours=1, minutes=1),
                                                  deadline), [(self.start, self.start + timedelta(hours=1))])

    def test_boundary_restart_invalidates_both_possible_hours(self):
        boundary = self.start + timedelta(hours=1)
        events = [dict(at=(boundary + timedelta(seconds=30)).isoformat(), reason='worker changed')]
        self.assertEqual(archive.interruptions_in_window(events, self.start, boundary), events)
        self.assertEqual(archive.interruptions_in_window(events, boundary, boundary + timedelta(hours=1)), events)

    def test_delayed_observation_invalidates_every_affected_hour(self):
        observed = self.start + timedelta(hours=3)
        events = [dict(at=observed.isoformat(),
                       affected_from=(self.start + timedelta(minutes=30)).isoformat(),
                       affected_until=observed.isoformat())]
        for hour in range(3):
            left = self.start + timedelta(hours=hour)
            self.assertEqual(archive.interruptions_in_window(events, left, left + timedelta(hours=1)), events)
        self.assertEqual(archive.interruptions_in_window(events, observed, observed + timedelta(hours=1)), [])

    def test_bad_interruption_bounds_fail_instead_of_hiding_a_gap(self):
        events = [dict(at=self.start.isoformat(), affected_from=(self.start + timedelta(hours=1)).isoformat(),
                       affected_until=self.start.isoformat())]
        with self.assertRaisesRegex(ValueError, 'ends before'):
            archive.interruptions_in_window(events, self.start, self.start + timedelta(hours=1))

    def test_continuous_hour_needs_an_actual_post_boundary_worker_probe(self):
        right = self.start + timedelta(hours=1)
        self.assertFalse(archive.worker_window_verified({}, right))
        self.assertFalse(archive.worker_window_verified(
            dict(last_verified_running_at=(right - timedelta(seconds=1)).isoformat()), right))
        self.assertTrue(archive.worker_window_verified(dict(last_verified_running_at=right.isoformat()), right))

    def reports(self, count):
        return [dict(started_at=(self.start + timedelta(hours=i)).isoformat(),
                     measured_until=(self.start + timedelta(hours=i+1)).isoformat(),
                     valid_for_comparison=True, new_qualified_bodies=10,
                     successful_searches=20, google_searches=22, valid_new_links=40) for i in range(count)]

    def test_day_claim_needs_24_contiguous_valid_full_hours(self):
        self.assertFalse(archive.summarize(self.reports(23))['last_24_hours_verified'])
        rows = self.reports(24)
        result = archive.summarize(rows)
        self.assertTrue(result['last_24_hours_verified'])
        self.assertEqual(result['last_24_hour_bodies'], 240)
        rows[12]['valid_for_comparison'] = False
        self.assertFalse(archive.summarize(rows)['last_24_hours_verified'])

    def test_missing_hour_breaks_consecutive_production_evidence(self):
        rows = self.reports(4)
        del rows[2]
        self.assertEqual(archive.summarize(rows)['consecutive_nonzero_qualified_hours'], 1)

    def test_old_day_does_not_invent_auxiliary_totals_or_complete_evidence(self):
        rows = self.reports(24)
        result = archive.summarize(rows)
        self.assertIsNone(result['last_24_hour_google_keyword_searches'])
        self.assertIsNone(result['last_24_hour_all_source_new_links'])
        for row in rows:
            row.update(completed_google_keyword_searches=25, all_source_valid_new_links=50,
                       extended_audit_complete=True)
        rows[0]['extended_audit_complete'] = False
        result = archive.summarize(rows)
        self.assertEqual(result['last_24_hour_google_keyword_searches'], 600)
        self.assertEqual(result['last_24_hour_all_source_new_links'], 1200)
        self.assertFalse(result['last_24_hour_extended_audit_complete'])


if __name__ == '__main__':
    unittest.main()
