import tempfile
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from realtime.proxy_pool import (
    ProxyApiClient, ProxyApiError, ProxyCache, ProxyRecord, ProxySynchronizer, ProxyPool,
)


def config(directory: str = "/tmp"):
    return SimpleNamespace(
        proxy_api_base="https://proxy.example",
        proxy_api_key="secret-key",
        proxy_cache_dir=Path(directory),
        proxy_sync_seconds=1800,
    )


class ProxyApiTests(unittest.TestCase):
    def test_google_defer_applies_to_other_protocols_without_shortening_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [ProxyRecord('192.0.2.1', 8080, 'http'), ProxyRecord('192.0.2.1', 1080, 'socks5')]
            cache = ProxyCache(Path(directory))
            cache.publish('private', records, None)
            pool = ProxyPool(config(directory))
            with patch.object(ProxyRecord, 'fresh', return_value=True), patch('realtime.proxy_pool.time.monotonic', return_value=100):
                self.assertEqual(pool.available_count('private', 'www.google.com'), 2)
                pool.defer(records[0].key, 'www.google.com', 300)
                pool.defer(records[1].key, 'www.google.com', 30)
                self.assertEqual(pool.available_count('private', 'www.google.com'), 0)
            with patch.object(ProxyRecord, 'fresh', return_value=True), patch('realtime.proxy_pool.time.monotonic', return_value=150):
                self.assertEqual(pool.available_count('private', 'www.google.com'), 0)
                self.assertEqual(pool.available_count('private', 'example.com'), 2)

    def test_google_identity_includes_stale_cross_profile_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ProxyCache(Path(directory))
            http = ProxyRecord('192.0.2.1', 8080, 'http')
            socks = ProxyRecord('192.0.2.1', 1080, 'socks5')
            cache.publish('private', [http], None)
            cache.publish('public_google', [socks], None)
            pool = ProxyPool(config(directory))
            a = pool.google_identity(http.key)
            self.assertEqual(a, pool.google_identity(socks.key))
            self.assertEqual(set(a[1]), {hashlib.sha256(r.key.encode()).hexdigest() for r in [http, socks]})
            self.assertNotEqual(a[0], pool.google_identity('192.0.2.2:8080/http')[0])

    def test_google_public_profile_keeps_https_threat_and_freshness_filters(self):
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'data': [], 'meta': {}}
        session = Mock()
        session.get.return_value = response
        ProxyApiClient(config(), session).fetch_all('public_google')
        params = session.get.call_args.kwargs['params']
        self.assertEqual(params['supports_https'], 'true')
        self.assertEqual(params['threat_free'], 'true')
        self.assertEqual(params['fresh_within'], '30')
        self.assertNotIn('min_quality', params)

    def test_unchanged_cache_cannot_keep_expired_records_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            cache = ProxyCache(Path(directory))
            cache.publish("private", [ProxyRecord("192.0.2.1", 8080, "http")], None)
            pool = ProxyPool(cfg)
            with patch.object(ProxyRecord, "fresh", return_value=True):
                self.assertEqual(len(pool._reload("private")), 1)
            with patch.object(ProxyRecord, "fresh", return_value=False):
                self.assertEqual(pool._reload("private"), [])

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
