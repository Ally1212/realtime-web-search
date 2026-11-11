"""Trusted response-only erasure journal; keys and checkpoints live outside DB backups.

No import-time work, customer endpoint, provider call, or platform deletion.
Restore must replay a current independently retained checkpoint before serving.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from .data_capability_facts import timestamp
from .product import ProductError

VERSION = 'searchflow-response-erasure-1'
MAX_ENTRIES = 100000
MAX_BYTES = 32 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_response_erasures (
 response_id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE,
 tenant_id text NOT NULL, erased_at timestamptz NOT NULL,
 state text NOT NULL CHECK(state IN ('expired','erased')), reason text NOT NULL
);
"""


def record(db, row, now, reason, state='erased'):
    """First erasure is immutable; monetary/source-call ledgers stay independent."""
    db.execute('INSERT INTO sf_capability_response_erasures VALUES(%s,%s,%s,%s,%s,%s) '
               'ON CONFLICT(response_id) DO NOTHING',
               (row['id'], row['job_id'], row['tenant_id'], now, state, reason))
    marker = db.execute('SELECT * FROM sf_capability_response_erasures WHERE response_id=%s', (row['id'],)).fetchone()
    db.execute('UPDATE sf_capability_responses SET state=%s,payload=NULL,metadata=NULL,snapshot_hash=NULL,'
               'payload_bytes=0,reserved_bytes=0,erased_at=%s,erasure_reason=%s WHERE id=%s',
               (marker['state'], marker['erased_at'], marker['reason'], row['id']))


def _key(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('independent checkpoint signing key must contain at least 32 bytes')
    return key


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class ResponseRetention:
    def __init__(self, store):
        self.store = store

    def _export(self, db, key):
        rows = db.execute('SELECT * FROM sf_capability_response_erasures ORDER BY response_id LIMIT %s',
                          (MAX_ENTRIES + 1,)).fetchall()
        if len(rows) > MAX_ENTRIES:
            raise ProductError('erasure_checkpoint_limit', '墓碑超过单次完整检查点上限；拒绝截断', 409)
        entries = [dict(response_id=str(r['response_id']), job_id=str(r['job_id']), tenant_id=r['tenant_id'],
                        erased_at=r['erased_at'].isoformat(), state=r['state'], reason=r['reason']) for r in rows]
        body = dict(schema_version=VERSION, scope='source_response_only', entries=entries)
        data = _encode(body)
        if len(data) > MAX_BYTES:
            raise ProductError('erasure_checkpoint_limit', '完整墓碑检查点超过字节上限', 409)
        return dict(body, signature=hmac.new(_key(key), data, hashlib.sha256).hexdigest())

    def export(self, key):
        _key(key)
        with self.store.service.connect() as db:
            self.store._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            return self._export(db, key)

    def _validated(self, checkpoint, key):
        from .data_capability_responses import safe_code
        _key(key)
        try:
            if not isinstance(checkpoint, dict) or set(checkpoint) != {'schema_version', 'scope', 'entries', 'signature'}:
                raise ValueError()
            body = {k: v for k, v in checkpoint.items() if k != 'signature'}
            data = _encode(body)
            if (len(data) > MAX_BYTES or body['schema_version'] != VERSION or body['scope'] != 'source_response_only'
                    or not isinstance(body['entries'], list) or len(body['entries']) > MAX_ENTRIES
                    or not isinstance(checkpoint['signature'], str)
                    or not hmac.compare_digest(checkpoint['signature'], hmac.new(key, data, hashlib.sha256).hexdigest())):
                raise ValueError()
            ids, jobs = set(), set()
            for entry in body['entries']:
                if set(entry) != {'response_id', 'job_id', 'tenant_id', 'erased_at', 'state', 'reason'}:
                    raise ValueError()
                rid, jid = str(UUID(entry['response_id'])), str(UUID(entry['job_id']))
                if rid != entry['response_id'] or jid != entry['job_id'] or rid in ids or jid in jobs:
                    raise ValueError()
                ids.add(rid); jobs.add(jid)
                if not isinstance(entry['tenant_id'], str) or not 1 <= len(entry['tenant_id']) <= 1024:
                    raise ValueError()
                if entry['state'] not in ('expired', 'erased'):
                    raise ValueError()
                timestamp(entry['erased_at']); safe_code(entry['reason'])
            return body['entries']
        except Exception:
            raise ProductError('invalid_erasure_checkpoint', '墓碑签名或完整结构无效；未执行重放', 409) from None

    def replay(self, checkpoint, key):
        entries = self._validated(checkpoint, key)
        with self.store.service.connect() as db:
            self.store._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            return self._replay(db, entries)

    def _replay(self, db, entries):
        cleared = 0
        for e in entries:
            existing = db.execute('SELECT * FROM sf_capability_response_erasures WHERE response_id=%s OR job_id=%s',
                                  (e['response_id'], e['job_id'])).fetchall()
            for old in existing:
                if (str(old['response_id']) != e['response_id'] or str(old['job_id']) != e['job_id']
                        or old['tenant_id'] != e['tenant_id']):
                    raise ProductError('erasure_checkpoint_conflict', '墓碑身份冲突；整批回滚', 409)
            row = db.execute('SELECT id,job_id,tenant_id,state,(payload IS NOT NULL OR metadata IS NOT NULL) AS has_content FROM sf_capability_responses WHERE id=%s OR job_id=%s FOR UPDATE',
                             (e['response_id'], e['job_id'])).fetchall()
            if any(str(r['id']) != e['response_id'] or str(r['job_id']) != e['job_id'] or r['tenant_id'] != e['tenant_id'] for r in row):
                raise ProductError('erasure_checkpoint_conflict', '恢复响应身份冲突；整批回滚', 409)
            db.execute('INSERT INTO sf_capability_response_erasures VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (e['response_id'], e['job_id'], e['tenant_id'], timestamp(e['erased_at']), e['state'], e['reason']))
            if row:
                cleared += int(row[0]['has_content'] or row[0]['state'] in ('reserved', 'captured'))
                record(db, row[0], timestamp(e['erased_at']), e['reason'], e['state'])
        return dict(entries=len(entries), cleared=cleared, scope='source_response_only')

    def tick(self, path, key):
        """Explicit scheduler hook: purge, durable external checkpoint, then commit.

        Operator supplies a private destination outside the database backup set.
        A crash before commit may over-delete after replay, never resurrect data.
        """
        _key(key)
        target = Path(path)
        with self.store.service.connect() as db:
            self.store._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            removed = len(self.store._purge(db, self.store.service.now()))
            checkpoint = self._export(db, key)
            write_checkpoint(target, checkpoint)
            return dict(expired=removed, entries=len(checkpoint['entries']), scope='source_response_only')


def write_checkpoint(path, checkpoint):
    """Private, atomic, fsync'd file; caller controls DB commit ordering."""
    target = Path(path)
    data = _encode(checkpoint)
    fd, name = tempfile.mkstemp(prefix='.response-erasures-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.replace(name, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)
