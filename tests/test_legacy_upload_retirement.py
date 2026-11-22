"""Retired upload URLs must never initialize a queue or read credentials."""
import unittest
from unittest.mock import Mock, patch
from realtime.serp_console_http import get, post


class LegacyUploadRetirementTests(unittest.TestCase):
    def test_all_old_urls_return_gone_before_database_access(self):
        for operation in (get, post):
            for path in ('/api/whale/status', '/api/whale/settings', '/api/whale/jobs', '/api/whale/jobs/old/retry'):
                handler = Mock(path=path)
                with patch('realtime.product_http.handle', return_value=False), patch('realtime.serp_console_http.ConsoleStore') as store:
                    self.assertTrue(operation(handler))
                self.assertEqual(handler.send_json.call_args.args[1], 410)
                store.assert_not_called()
