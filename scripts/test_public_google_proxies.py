from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from realtime.discovery import GoogleBlocked, SearchDiscovery


def build_proxy_url(profile: str, record: dict[str, object]) -> str:
    protocol = str(record["protocol"])
    scheme = "socks5h" if protocol == "socks5" else "http"
    return f"{scheme}://{record['host']}:{record['port']}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path("state/proxies"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    for profile in ("public_google", "public"):
        payload = json.loads((args.cache_dir / f"{profile}.json").read_text())
        for record in payload["records"]:
            key = f"{record['host']}:{record['port']}/{record['protocol']}"
            rows.append({
                "profile": profile,
                "key": key,
                "key_hash": hashlib.sha256(key.encode()).hexdigest(),
                "proxy_url": build_proxy_url(profile, record),
                "quality": record.get("quality", 0),
                "latency_ms": record.get("latency_ms", 0),
            })

    def test(row: dict[str, object]) -> dict[str, object]:
        client = SearchDiscovery(timeout=20, language="zh", providers=("wml",))
        started = time.monotonic()
        result = dict(row)
        try:
            results = client.transport.fetch(
                "wml", "人工智能 新闻", 1, str(row["proxy_url"])
            )
            evidence = client.transport.last_evidence
            result.update(
                success=True,
                classification="results" if results else "empty",
                result_count=len(results),
                error="",
                http_status=evidence.get("http_status"),
            )
        except Exception as exc:
            evidence = client.transport.last_evidence
            captcha = isinstance(exc, GoogleBlocked) and exc.captcha
            code = SearchDiscovery._error_code(exc) if isinstance(exc, GoogleBlocked) else type(exc).__name__
            result.update(
                success=False,
                classification="captcha" if captcha else evidence.get("classification", "error"),
                result_count=0,
                error=code,
                http_status=evidence.get("http_status"),
            )
        finally:
            client.close()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(executor.map(test, rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "version": 1,
                "tested_at": time.time(),
                "duration_seconds": round(time.time() - started, 3),
                "concurrency": args.concurrency,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps(summary(results), ensure_ascii=False))


def summary(results: list[dict[str, object]]) -> dict[str, object]:
    profiles: dict[str, dict[str, object]] = {}
    for profile in sorted({str(row["profile"]) for row in results}):
        selected = [row for row in results if row["profile"] == profile]
        successful = [row for row in selected if row["success"]]
        profiles[profile] = {
            "tested": len(selected),
            "successful": len(successful),
            "success_rate": round(len(successful) / max(1, len(selected)), 4),
            "unique_successful_hosts": len({str(row["key"]).rsplit(":", 1)[0] for row in successful}),
            "classifications": {
                str(classification): sum(row["classification"] == classification for row in selected)
                for classification in sorted({str(row["classification"]) for row in selected})
            },
        }
    return profiles


if __name__ == "__main__":
    main()
