#!/usr/bin/env python3
"""Read a pinned set of Whale versions without exposing payloads or credentials.

The target JSON is an operator supplied file containing only:
[{"document_id": "...", "version_id": "...", "dataset_id": "...",
  "source_platform": "..."}]

No target IDs or response bodies are printed. Without a Consumer Key this
command exits before making a network request.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from realtime.google_domain import DomainError
from realtime.google_whale_readback import ReadbackTarget, WhaleReadbackClient


def load_targets(path: str) -> list[ReadbackTarget]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or len(payload) > 100:
        raise DomainError("invalid_argument", "targets file must contain 1-100 targets")
    fields = {"document_id", "version_id", "dataset_id", "source_platform"}
    optional = {'expected_content_hash', 'expected_raw_payload_hash', 'expected_platform_payload_hash'}
    if any(not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - optional for item in payload):
        raise DomainError("invalid_argument", "target fields are incomplete or unexpected")
    return [ReadbackTarget(**item) for item in payload]


def load_targets_from_outbox(dsn: str) -> list[ReadbackTarget]:
    import psycopg

    if not dsn:
        raise DomainError("invalid_argument", "outbox database URL is required")
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            rows = conn.execute("""
                SELECT receipt->>'document_id', receipt->>'version_id', dataset_id,
                       payload->'source'->>'source_platform'
                FROM whale_serp_outbox
                WHERE state IN ('queued', 'accepted', 'duplicate')
                ORDER BY id LIMIT 101
            """).fetchall()
    except psycopg.Error:
        raise DomainError("source_error", "outbox read failed") from None
    if not rows or len(rows) > 100:
        raise DomainError("invalid_argument", "outbox must contain 1-100 eligible rows")
    return [ReadbackTarget(*row) for row in rows]


def run(*, base_url: str, consumer_key: str, targets: list[ReadbackTarget], batch=False) -> dict:
    if not isinstance(targets, list) or not 1 <= len(targets) <= 100 or any(not isinstance(t, ReadbackTarget) for t in targets):
        raise DomainError('invalid_argument', 'readback requires 1-100 typed targets')
    if not consumer_key:
        return {"status": "blocked_missing_consumer_key", "requested": len(targets),
                "attempted": 0, "readable": 0, "not_authorized": 0, "not_covered": 0,
                "source_error": 0}
    client = WhaleReadbackClient(base_url, consumer_key, min_interval_seconds=1 if batch else 0)
    counts = Counter()
    if batch:
        try:
            outcomes = client.verify_many(targets)
        except DomainError as error:
            outcomes = [{'error': error.code} for _ in targets]
        for item in outcomes:
            counts['attempted'] += 1
            if item.get('error'):
                counts[item['error']] += 1
            else:
                proof = item.get('proof') or {}
                counts['readable'] += bool(proof.get('version_readable'))
                counts['hash_verified'] += proof.get('content_verification') == 'matched'
                counts['pinned_payload_available'] += bool(proof.get('platform_payload_visible'))
        return dict(status='blocked_not_authorized' if counts['not_authorized'] else
                    'completed' if counts['readable'] == len(targets) else 'incomplete',
                    requested=len(targets), attempted=counts['attempted'], readable=counts['readable'],
                    not_authorized=counts['not_authorized'], not_covered=counts['not_covered'],
                    source_error=counts['source_error'], hash_verified=counts['hash_verified'],
                    pinned_payload_available=counts['pinned_payload_available'],
                    formal_run_item_schema_verified=False, units='target_versions')
    for target in targets:
        counts["attempted"] += 1
        try:
            proof = client.verify(target)
            counts["readable"] += 1 if proof["version_readable"] else 0
        except DomainError as error:
            counts[error.code] += 1
            if error.code == "not_authorized":
                break
    return {"status": ("blocked_not_authorized" if counts["not_authorized"]
                       else "completed" if counts["readable"] == len(targets) else "incomplete"), "requested": len(targets),
            "attempted": counts["attempted"],
            "readable": counts["readable"],
            "not_authorized": counts["not_authorized"],
            "not_covered": counts["not_covered"],
            "source_error": counts["source_error"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="?", help="operator supplied target JSON")
    parser.add_argument("--from-outbox", action="store_true")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--base-url", default=os.getenv("WHALE_BASE_URL", ""))
    parser.add_argument("--consumer-key-env", default="WHALE_CONSUMER_READ_KEY")
    parser.add_argument("--consumer-key-file", help="private Consumer key file; value never printed")
    args = parser.parse_args(argv)
    try:
        if bool(args.targets) == args.from_outbox:
            raise DomainError("invalid_argument", "choose a targets file or --from-outbox")
        targets = (load_targets_from_outbox(os.getenv(args.database_url_env, ""))
                   if args.from_outbox else load_targets(args.targets))
        if not args.base_url:
            raise DomainError("invalid_argument", "--base-url or WHALE_BASE_URL is required")
        result = run(base_url=args.base_url,
                     consumer_key=(Path(args.consumer_key_file).read_text().strip() if args.consumer_key_file
                                   else os.getenv(args.consumer_key_env, "")),
                     targets=targets, batch=True)
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "blocked_invalid_input"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
