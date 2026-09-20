"""Offline singleflight comparison; never contacts Google."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import threading
import time
from pathlib import Path

from .discovery import GoogleBlocked, SearchDiscovery, SearchResult
from .experiment_store import ExperimentStore


class LeaseStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None

    def acquire(self, cache_key: str):
        with self.lock:
            if self.owner is None:
                self.owner = "lease"
                return self.owner
            return None

    def release(self, cache_key: str, token: str):
        with self.lock:
            if self.owner == token:
                self.owner = None


def run_case(store: ExperimentStore, name: str, wait_seconds: float, concurrency: int):
    cache = {}
    cache_lock = threading.Lock()
    leases = LeaseStore()
    fetch_started = threading.Event()
    fetch_release = threading.Event()
    network_requests = 0
    latencies = []

    def cache_get(key, source):
        with cache_lock:
            return cache.get(key)

    def cache_put(key, source, query_hash, locale, page, results, ttl, **kwargs):
        with cache_lock:
            cache[key] = {
                'payload': results,
                'metadata': kwargs.get('metadata') or {},
                'created_at': time.time(), 'ttl': ttl,
            }

    def fetch(provider, query, page, proxy_url, **kwargs):
        nonlocal network_requests
        network_requests += 1
        fetch_started.set()
        fetch_release.wait(2)
        return [SearchResult('https://example.com/ai', 'AI', ('google_web',), rank=1)]

    discovery = SearchDiscovery(
        providers=('wml',), cache_get=cache_get, cache_put=cache_put,
        singleflight_acquirer=leases.acquire,
        singleflight_releaser=leases.release,
        singleflight_wait_seconds=wait_seconds,
        source_slot_acquirer=lambda *args, **kwargs: {'allowed': True},
    )
    discovery.transport.fetch = fetch

    def request_one():
        started = time.monotonic()
        try:
            discovery._discover_google_page('AI', 1)
            return None
        except GoogleBlocked as exc:
            return exc.reason
        finally:
            latencies.append(time.monotonic() - started)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(request_one) for _ in range(concurrency)]
        if not fetch_started.wait(.5):
            fetch_started.set()
        time.sleep(.05)
        fetch_release.set()
        outcomes = [future.result() for future in futures]

    errors = sum(value is not None for value in outcomes)
    cache_reuses = concurrency - errors - network_requests
    store.record_inflight_experiment(
        name, wait_seconds=wait_seconds, concurrency=concurrency,
        network_requests=network_requests, cache_reuses=cache_reuses,
        errors=errors, latencies=latencies,
    )
    discovery.close()
    return {
        'name': name, 'wait_seconds': wait_seconds,
        'concurrency': concurrency, 'network_requests': network_requests,
        'cache_reuses': cache_reuses, 'errors': errors,
        'p50_latency': round(statistics.median(latencies), 4),
        'p95_latency': round(statistics.quantiles(latencies, n=20)[-1], 4) if len(latencies) >= 2 else None,
    }


def command(args):
    root = Path(args.directory)
    store = ExperimentStore(root / 'state', create=True)
    cases = [
        run_case(store, 'wait_0', 0, args.concurrency),
        run_case(store, 'wait_2', 2, args.concurrency),
    ]
    report = {'cases': cases, 'ledger': store.inflight_experiments()}
    path = root / 'singleflight-report.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--concurrency', type=int, default=20)
    command(parser.parse_args())


if __name__ == '__main__':
    main()
