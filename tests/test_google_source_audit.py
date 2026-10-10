import unittest
from datetime import datetime, timedelta, timezone
from scripts.google_source_audit import audit_availability


class SourceAuditAvailabilityTests(unittest.TestCase):
    def test_old_frozen_run_is_unavailable_not_zero(self):
        start = datetime.now(timezone.utc)
        self.assertEqual(audit_availability({}, start, start + timedelta(hours=1))[0], 'unavailable')

    def test_partial_enablement_cannot_report_complete_window(self):
        start = datetime.now(timezone.utc)
        manifest = dict(auxiliary_audit=dict(revision='v1', enabled_since=(start + timedelta(minutes=1)).isoformat()))
        self.assertEqual(audit_availability(manifest, start, start + timedelta(hours=1))[0], 'unavailable')
        manifest['auxiliary_audit']['enabled_since'] = start.isoformat()
        self.assertEqual(audit_availability(manifest, start, start + timedelta(hours=1))[0], 'available')

    def test_invalid_timestamp_is_unknown(self):
        start = datetime.now(timezone.utc)
        manifest = dict(auxiliary_audit=dict(revision='v1', enabled_since='2026-10-09'))
        self.assertEqual(audit_availability(manifest, start, start + timedelta(hours=1))[0], 'unknown')
