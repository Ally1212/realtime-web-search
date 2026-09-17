"""Load-test many users creating the same local keyword campaign."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--query", required=True)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--keep-active", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.requests <= 100_000 or not 1 <= args.concurrency <= 2_000:
        raise SystemExit("invalid requests or concurrency")
    endpoint = args.base_url.rstrip("/") + "/api/local-campaigns"
    body = json.dumps({"query": args.query, "proxy_profile": "direct"}).encode()
    start = threading.Event()

    def request_once() -> tuple[int, dict, float]:
        start.wait()
        began = time.monotonic()
        request = urllib.request.Request(
            endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                return response.status, json.load(response), time.monotonic() - began
        except urllib.error.HTTPError as exc:
            return exc.code, {"error": exc.read().decode(errors="replace")}, time.monotonic() - began

    started = time.monotonic()
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(request_once) for _ in range(args.requests)]
        start.set()
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append((0, {"error": type(exc).__name__}, args.timeout))
    elapsed = time.monotonic() - started
    successful = [row for row in rows if row[0] == 202]
    campaign_ids = {row[1].get("campaign_id") for row in successful}
    campaign_ids.discard(None)
    latencies = [row[2] for row in rows]
    result = {
        "query": args.query,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successful": len(successful),
        "failed": len(rows) - len(successful),
        "unique_campaign_ids": len(campaign_ids),
        "created_responses": sum(not row[1].get("reused", False) for row in successful),
        "reused_responses": sum(bool(row[1].get("reused")) for row in successful),
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / max(elapsed, .001), 2),
        "latency_p50_seconds": round(statistics.median(latencies), 4),
        "latency_p95_seconds": round(percentile(latencies, .95), 4),
        "failure_summary": dict(Counter(
            str(row[0]) if row[0] else str(row[1].get("error") or "unknown")
            for row in rows if row[0] != 202
        )),
    }
    if campaign_ids and not args.keep_active:
        campaign_id = next(iter(campaign_ids))
        stop = urllib.request.Request(
            args.base_url.rstrip("/") + f"/api/campaigns/{campaign_id}/stop",
            data=b"", method="POST",
        )
        with urllib.request.urlopen(stop, timeout=args.timeout):
            pass
        result["campaign_stopped"] = campaign_id
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if len(successful) != args.requests or len(campaign_ids) != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
