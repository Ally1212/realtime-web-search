import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from realtime.google_domain import DomainError
from realtime.google_whale_readback import ReadbackTarget
from scripts.google_whale_readback_probe import (
    load_targets, load_targets_from_outbox, run,
)


TARGET = ReadbackTarget("doc-1", "ver-1", "data-1", "google_search")


class ProbeTests(unittest.TestCase):
    def test_missing_key_makes_no_consumer_call(self):
        with patch("scripts.google_whale_readback_probe.WhaleReadbackClient") as cls:
            result = run(base_url="https://example.test", consumer_key="", targets=[TARGET])
        cls.assert_not_called()
        self.assertEqual(result["status"], "blocked_missing_consumer_key")
        self.assertEqual(result["attempted"], 0)

    def test_readable_and_failed_versions_are_counted_without_ids(self):
        with patch("scripts.google_whale_readback_probe.WhaleReadbackClient") as cls:
            cls.return_value.verify.side_effect = [
                {"version_readable": True},
                DomainError("not_covered", "missing", 404),
                DomainError("source_error", "failed", 503),
            ]
            result = run(base_url="https://example.test", consumer_key="secret",
                         targets=[TARGET] * 3)
        self.assertEqual((result["readable"], result["not_covered"],
                          result["source_error"], result["attempted"]), (1, 1, 1, 3))
        self.assertEqual(result["status"], "incomplete")
        self.assertNotIn("secret", str(result))
        self.assertNotIn("doc-1", str(result))

    def test_auth_failure_stops_further_requests(self):
        with patch("scripts.google_whale_readback_probe.WhaleReadbackClient") as cls:
            cls.return_value.verify.side_effect = DomainError("not_authorized", "denied", 403)
            result = run(base_url="https://example.test", consumer_key="secret",
                         targets=[TARGET] * 9)
        self.assertEqual(result["status"], "blocked_not_authorized")
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(cls.return_value.verify.call_count, 1)

    def test_batch_results_keep_readability_separate_from_hash_and_schema_verification(self):
        with patch('scripts.google_whale_readback_probe.WhaleReadbackClient') as cls:
            cls.return_value.verify_many.return_value = [
                {'proof': {'version_readable': True, 'content_verification': 'not_requested', 'platform_payload_visible': False}},
                {'error': 'not_covered'}]
            result = run(base_url='https://example.test', consumer_key='secret', targets=[TARGET]*2, batch=True)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['readable'], 1)
        self.assertEqual(result['hash_verified'], 0)
        self.assertFalse(result['formal_run_item_schema_verified'])
        for targets in ([], [TARGET]*101):
            with self.assertRaises(DomainError):
                run(base_url='https://example.test', consumer_key='secret', targets=targets, batch=True)

    def test_target_file_requires_exact_safe_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "targets.json"
            path.write_text(json.dumps([TARGET.__dict__]), encoding="utf-8")
            self.assertEqual(load_targets(str(path)), [TARGET])
            path.write_text(json.dumps([{**TARGET.__dict__, "payload": "raw"}]),
                            encoding="utf-8")
            with self.assertRaises(DomainError):
                load_targets(str(path))

    def test_outbox_query_is_read_only_and_limits_set(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.execute.side_effect = [None, Mock(fetchall=Mock(return_value=[
            ("doc-1", "ver-1", "data-1", "google_search")]))]
        with patch("psycopg.connect", return_value=connection) as connect:
            self.assertEqual(load_targets_from_outbox("postgres://example"), [TARGET])
        connect.assert_called_once_with("postgres://example", autocommit=False)
        self.assertEqual(connection.execute.call_args_list[0].args[0],
                         "SET TRANSACTION READ ONLY")
        query = connection.execute.call_args_list[1].args[0]
        self.assertIn("ORDER BY id LIMIT 101", query)
        self.assertNotIn("UPDATE ", query)


if __name__ == "__main__":
    unittest.main()
