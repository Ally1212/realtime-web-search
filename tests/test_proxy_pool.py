import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from realtime.proxy_pool import (
    ProxyApiClient, ProxyApiError, ProxyCache, ProxyRecord, ProxySynchronizer,
)


def config(directory: str = "/tmp"):
    return SimpleNamespace(
        proxy_api_base="https://proxy.example",
        proxy_api_key="secret-key",
        proxy_cache_dir=Path(directory),
        proxy_sync_seconds=1800,
    )


class ProxyApiTests(unittest.TestCase):
    def test_reads_complete_cursor_chain_without_changing_filters(self):
        first = Mock(status_code=200, headers={"X-Request-ID": "r1"})
        first.json.return_value = {
            "data": [{"host": "192.0.2.1", "port": 8080, "protocol": "http", "lastChecked": "2026-09-01T00:00:00Z"}],
            "meta": {"nextCursor": "opaque"},
        }
        second = Mock(status_code=200, headers={"X-Request-ID": "r2"})
        second.json.return_value = {
            "data": [{"host": "192.0.2.2", "port": 1080, "protocol": "socks5", "lastChecked": "2026-09-01T00:00:00Z"}],
            "meta": {"nextCursor": None},
        }
        session = Mock()
        session.get.side_effect = [first, second]
        records, request_id = ProxyApiClient(config(), session).fetch_all("private")
        self.assertEqual(len(records), 2)
        self.assertEqual(request_id, "r2")
        first_params = session.get.call_args_list[0].kwargs["params"]
        second_params = session.get.call_args_list[1].kwargs["params"]
        self.assertNotIn("cursor", first_params)
        self.assertEqual(second_params["cursor"], "opaque")
        self.assertEqual(
            {k: v for k, v in second_params.items() if k != "cursor"}, first_params
        )
        self.assertEqual(
            session.get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer secret-key"
        )

    def test_surfaces_retry_after_on_rate_limit(self):
        response = Mock(status_code=429, headers={"Retry-After": "17"})
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(ProxyApiError) as caught:
            ProxyApiClient(config(), session).fetch_all("public")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 17)

    def test_all_contract_errors_preserve_status_without_secrets(self):
        for status in (400, 401, 404, 410, 503):
            with self.subTest(status=status):
                response = Mock(status_code=status, headers={})
                session = Mock()
                session.get.return_value = response
                with self.assertRaises(ProxyApiError) as caught:
                    ProxyApiClient(config(), session).fetch_all("private")
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn("secret-key", str(caught.exception))

    def test_atomic_cache_can_publish_authoritative_empty_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ProxyCache(Path(directory))
            cache.publish("private", [ProxyRecord("192.0.2.1", 8080, "http")], "r1")
            cache.publish("private", [], "r2")
            _, records = cache.load("private")
            self.assertEqual(records, [])
            self.assertEqual(cache.path("private").stat().st_mode & 0o777, 0o600)

    def test_failed_sync_keeps_last_complete_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            existing = ProxyRecord("192.0.2.9", 8080, "http")
            cache = ProxyCache(Path(directory))
            cache.publish("private", [existing], "old")
            client = Mock()
            client.fetch_all.side_effect = ProxyApiError("temporary", 503)
            with self.assertRaises(ProxyApiError):
                ProxySynchronizer(cfg, client).sync("private", force=True)
            _, records = cache.load("private")
            self.assertEqual(records, [existing])


if __name__ == "__main__":
    unittest.main()
