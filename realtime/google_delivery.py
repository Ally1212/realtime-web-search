"""Read-only delivery evidence for legacy uploads; never a formal observation reader.

Only a local operator can install a proof file. Every read rechecks public outbox
scope; no source call, upload, collection, table creation or receipt mutation.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .google_data import timestamp
from .google_domain import DomainError

PROOF_SCHEMA = 'google-legacy-readback-1'
DEFAULT_PROOF_PATH = '/app/state/private/google-whale-readback-proof.json'
CHECKS = ('document_scope_matches', 'pinned_version_readable', 'version_identity_matches',
          'metadata_matches', 'title_matches', 'url_matches', 'source_payload_hash_matches',
          'current_version_matches', 'pinned_platform_payload_present')


def _full_run_sample(proof, db, now):
    sample = proof.get('full_run_sample')
    if sample is None:
        return None
    checks = ('document_scope_matches', 'external_identity_matches', 'pinned_identity_matches',
              'pinned_payload_present', 'pinned_payload_matches', 'raw_hash_matches',
              'occurrence_order_and_fields_match', 'source_response_matches', 'run_id_matches')
    if not isinstance(sample, dict) or any(sample.get(k) is not True for k in checks):
        return dict(status='invalid_evidence', integrity_verified=False)
    checked_at = timestamp(sample.get('checked_at'))
    count = sample.get('actual_occurrences')
    run_id = sample.get('source_run_id')
    if (not checked_at or type(count) is not int or not 0 <= count <= 100000
            or sample.get('expected_occurrences') != count
            or sample.get('terminal_status') not in {'accepted','duplicate'}
            or not isinstance(run_id, str) or not 1 <= len(run_id) <= 256):
        return dict(status='invalid_evidence', integrity_verified=False)
    age = (now - datetime.fromisoformat(checked_at.replace('Z', '+00:00'))).total_seconds()
    if age < -300:
        return dict(status='invalid_evidence', integrity_verified=False)
    source = db.execute("""SELECT 1 FROM serp_requests r WHERE r.request_id::text=%s
          AND r.source='google_web' AND NOT EXISTS (
            SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id
            AND o.options->>'customer_private'='true')""", (run_id,)).fetchone()
    if source is None:
        return None
    quality = sample.get('document_quality_status')
    quality = quality if quality in {'normal','low','high','unknown'} else 'unknown'
    return dict(status='sample_roundtrip_verified' if quality in {'normal','high'} else 'verified_with_quality_limit',
                integrity_verified=True, scope='one_approved_public_web_run',
                checked_at=checked_at, stale=age>86400, expected_occurrences=count,
                actual_occurrences=count, terminal_status=sample['terminal_status'],
                platform_quality=quality, payload_binding='pinned_version')


def read_delivery_status(dsn, *, proof_path=None, now=None):
    now = now or datetime.now(timezone.utc)
    empty = dict(status='not_configured', scope='public_legacy_upload_samples',
                 checked_at=None, stale=True, readable_versions=0, raw_hash_matches=0,
                 raw_hash_mismatches=0, metadata_matches=0, receipt_statuses={},
                 formal_observation_reader=False, new_collection=False)
    path = Path(proof_path or os.getenv('SEARCHFLOW_WHALE_READBACK_PROOF_FILE', DEFAULT_PROOF_PATH))
    try:
        if not path.exists():
            return empty
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('proof too large')
        proof = json.loads(path.read_text(encoding='utf-8'))
        checked_at = timestamp(proof.get('checked_at'))
        if proof.get('proof_schema') != PROOF_SCHEMA or not checked_at:
            raise ValueError('proof schema or time missing')
        age = (now - datetime.fromisoformat(checked_at.replace('Z', '+00:00'))).total_seconds()
        if age < -300:
            raise ValueError('proof in future')
        rows = proof.get('rows')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError('proof row bound invalid')
        pairs = set()
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] or len(row[k]) > 256
                    for k in ('document_id', 'version_id', 'dataset_id', 'source_platform')):
                raise ValueError('invalid identity')
            if any(type(row.get(k)) is not bool for k in CHECKS):
                raise ValueError('invalid proof booleans')
            pair = (row['document_id'], row['version_id'])
            if pair in pairs:
                raise ValueError('duplicate proof identity')
            pairs.add(pair)
    except (OSError, ValueError, TypeError, AttributeError):
        return {**empty, 'status': 'invalid_evidence'}
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='5s'")
        public = db.execute("""SELECT o.receipt->>'document_id' document_id,
                  o.receipt->>'version_id' version_id,o.dataset_id,
                  o.payload->'source'->>'source_platform' source_platform,o.state
            FROM whale_serp_outbox o
            WHERE o.receipt->>'document_id'=ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM serp_task_options p
                WHERE p.campaign_id::text=o.payload->'discovery'->'metadata'->>'campaign_id'
                  AND p.options->>'customer_private'='true')""",
            ([r['document_id'] for r in rows],)).fetchall()
        full_run = _full_run_sample(proof, db, now)
    allowed = {(r['document_id'], r['version_id'], r['dataset_id'], r['source_platform']) for r in public}
    selected = [r for r in rows if (r['document_id'], r['version_id'], r['dataset_id'], r['source_platform']) in allowed]
    readable = [r for r in selected if r['document_scope_matches'] and r['pinned_version_readable'] and r['version_identity_matches']]
    matched = sum(r['source_payload_hash_matches'] for r in readable)
    receipt_statuses = Counter(r.get('terminal_receipt_status', 'unknown')
                              if r.get('terminal_receipt_status') in {'unknown','pending','queued','retrying','failed','accepted','duplicate'}
                              else 'unknown' for r in selected)
    return dict(status='connected_legacy_partial' if readable else 'not_covered',
        scope='public_legacy_upload_samples', full_run_sample=full_run, checked_at=checked_at, age_seconds=max(0, round(age)),
        stale=age > 86400, selected_records=len(selected), readable_versions=len(readable),
        raw_hash_matches=matched, raw_hash_mismatches=len(readable)-matched,
        metadata_matches=sum(r['metadata_matches'] and r['current_version_matches'] for r in readable),
        pinned_platform_payload_records=sum(r['pinned_platform_payload_present'] for r in readable),
        receipt_statuses=dict(receipt_statuses), local_outbox_states=dict(Counter(r['state'] for r in public)),
        formal_observation_reader=False, new_collection=False,
        limitations=['仅核对旧上传样本，不表示全部历史请求已进入 Whale。',
                      '当前文档元数据匹配与固定版本平台字段可重放是不同证明。',
                      '旧回执 unknown 不等于失败，也不自动改写为 accepted。'])
