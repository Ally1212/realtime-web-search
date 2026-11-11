"""Explicit offline recovery checkpoint for independent capability fact/spool erasures.

No service starts, restore runs or customer endpoints are created on import.
External current-checkpoint custody and service startup admission are required.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from .data_capability_fact_retention import FactRetention
from .data_capability_facts import timestamp
from .data_capability_jobs import digest
from .data_capability_responses import safe_code
from .data_capability_retention import ResponseRetention, _key, _encode, write_checkpoint
from .product import ProductError

VERSION = 'searchflow-data-erasure-3'
SCOPE = 'observations_jobs_subscriptions_and_source_responses'
PREVIOUS_VERSION = 'searchflow-data-erasure-2'
PREVIOUS_SCOPE = 'observations_jobs_and_source_responses'
LEGACY_VERSION = 'searchflow-data-erasure-1'
LEGACY_SCOPE = 'observation_content_and_source_responses'
MAX_SUBSCRIPTIONS = 100000
MAX_JOBS = 100000
MAX_FACTS = 100000
MAX_BYTES = 64 * 1024 * 1024
MAX_SCAN = 1000000
SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_erasure_replays (
 checkpoint_id uuid PRIMARY KEY, checkpoint_hash text NOT NULL,
 applied_at timestamptz NOT NULL, fact_entries integer NOT NULL,
 response_entries integer NOT NULL
);
CREATE TABLE IF NOT EXISTS sf_capability_subscription_erasure_replays (
 checkpoint_id uuid PRIMARY KEY, subscription_entries integer NOT NULL
);
CREATE TABLE IF NOT EXISTS sf_capability_job_erasure_replays (
 checkpoint_id uuid PRIMARY KEY, job_entries integer NOT NULL
);
"""


def conflict():
    raise ProductError('erasure_checkpoint_conflict', '恢复身份或事件冲突；整批回滚', 409)


class DataErasureCheckpoint:
    def __init__(self, service, response_store=None):
        if response_store is not None and response_store.service.dsn != service.dsn:
            raise ValueError('checkpoint components must share the exact database scope')
        self.service = service
        self.responses = ResponseRetention(response_store) if response_store is not None else None

    def initialize(self):
        with self.service.connect() as db:
            db.execute(SCHEMA)

    def _check_scope(self, db, *, subscriptions=True):
        # v1/v2 contain no subscription markers. Fail visibly rather than
        # publish/admit a purported complete backup that silently omits them.
        if not subscriptions and db.execute('SELECT 1 FROM sf_capability_subscription_erasures LIMIT 1').fetchone():
            raise ProductError('erasure_checkpoint_scope','当前联合检查点未覆盖订阅墓碑；拒绝不完整恢复或导出',409)
        installed = db.execute("SELECT to_regclass('sf_capability_responses') AS t").fetchone()['t'] is not None
        if installed != (self.responses is not None):
            raise ProductError('erasure_checkpoint_scope', '检查点必须明确包含已安装响应仓；不得静默省略', 409)
        if self.responses is not None:
            self.responses.store._policy(db)

    def _response_component(self, db, key):
        self._check_scope(db)
        return self.responses._export(db, key) if self.responses is not None else None

    def _export(self, db, key):
        rows = db.execute('SELECT * FROM sf_capability_fact_erasures ORDER BY tenant_id,event_digest LIMIT %s', (MAX_FACTS+1,)).fetchall()
        if len(rows) > MAX_FACTS:
            raise ProductError('erasure_checkpoint_limit', '完整事实墓碑超过上限；拒绝截断', 409)
        facts = [dict(observation_id=str(r['observation_id']), tenant_id=r['tenant_id'], event_digest=r['event_digest'],
                      erased_at=r['erased_at'].isoformat(), reason=r['reason']) for r in rows]
        rows=db.execute('SELECT * FROM sf_capability_job_erasures ORDER BY tenant_id,idempotency_digest LIMIT %s',(MAX_JOBS+1,)).fetchall()
        if len(rows)>MAX_JOBS:
            raise ProductError('erasure_checkpoint_limit','完整任务墓碑超过上限；拒绝截断',409)
        jobs=[dict(job_id=str(r['job_id']),tenant_id=r['tenant_id'],request_hash=r['request_hash'],idempotency_digest=r['idempotency_digest'],erased_at=r['erased_at'].isoformat(),reason=r['reason']) for r in rows]
        rows=db.execute('SELECT * FROM sf_capability_subscription_erasures ORDER BY tenant_id,idempotency_digest LIMIT %s',(MAX_SUBSCRIPTIONS+1,)).fetchall()
        if len(rows)>MAX_SUBSCRIPTIONS:
            raise ProductError('erasure_checkpoint_limit','完整订阅墓碑超过上限；拒绝截断',409)
        subscriptions=[dict(subscription_id=str(r['subscription_id']),tenant_id=r['tenant_id'],request_hash=r['request_hash'],idempotency_digest=r['idempotency_digest'],erased_at=r['erased_at'].isoformat(),reason=r['reason']) for r in rows]
        body = dict(schema_version=VERSION, scope=SCOPE, checkpoint_id=str(uuid4()), created_at=self.service.now().isoformat(),
                    facts=facts, jobs=jobs, subscriptions=subscriptions, responses=self._response_component(db, key))
        encoded = _encode(body)
        if len(encoded) > MAX_BYTES:
            raise ProductError('erasure_checkpoint_limit', '完整检查点超过字节上限；拒绝截断', 409)
        return dict(body, signature=hmac.new(_key(key), encoded, hashlib.sha256).hexdigest())

    def export(self, key):
        _key(key)
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            return self._export(db, key)

    def checkpoint(self, path, key):
        """Publish a complete private checkpoint while erasures are serialized."""
        _key(key)
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            value = self._export(db, key)
            write_checkpoint(path, value)
        return dict(checkpoint_id=value['checkpoint_id'], checkpoint_hash=digest(value),
                    fact_entries=len(value['facts']), job_entries=len(value['jobs']), subscription_entries=len(value['subscriptions']), response_entries=len(value['responses']['entries']) if value['responses'] else 0,
                    scope=SCOPE)

    def _validated(self, value, key, expected_hash):
        _key(key)
        try:
            if not isinstance(expected_hash, str) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
                raise ValueError()
            if not isinstance(value,dict):raise ValueError()
            legacy=value.get('schema_version')==LEGACY_VERSION
            keys={'schema_version','scope','checkpoint_id','created_at','facts','responses','signature'}
            if not legacy:keys.add('jobs')
            if value.get('schema_version')==VERSION:keys.add('subscriptions')
            if set(value)!=keys:raise ValueError()
            if not hmac.compare_digest(digest(value), expected_hash):
                raise ValueError()
            body = {k:v for k,v in value.items() if k != 'signature'}
            encoded = _encode(body)
            if (len(encoded)>MAX_BYTES or (body['schema_version'],body['scope']) not in ((VERSION,SCOPE),(PREVIOUS_VERSION,PREVIOUS_SCOPE),(LEGACY_VERSION,LEGACY_SCOPE))
                    or str(UUID(body['checkpoint_id'])) != body['checkpoint_id']
                    or not isinstance(value['signature'], str)
                    or not hmac.compare_digest(value['signature'],hmac.new(key,encoded,hashlib.sha256).hexdigest())
                    or not isinstance(body['facts'],list) or len(body['facts'])>MAX_FACTS):
                raise ValueError()
            timestamp(body['created_at'])
            ids, events = set(), set()
            for e in body['facts']:
                if not isinstance(e,dict) or set(e) != {'observation_id','tenant_id','event_digest','erased_at','reason'}:
                    raise ValueError()
                if (str(UUID(e['observation_id'])) != e['observation_id'] or e['observation_id'] in ids
                        or not isinstance(e['tenant_id'],str) or not 1<=len(e['tenant_id'])<=1024
                        or not isinstance(e['event_digest'],str) or not re.fullmatch('[0-9a-f]{64}',e['event_digest'])
                        or (e['tenant_id'],e['event_digest']) in events):
                    raise ValueError()
                ids.add(e['observation_id']);events.add((e['tenant_id'],e['event_digest']))
                timestamp(e['erased_at']);safe_code(e['reason'])
            jobs=body.get('jobs',[])
            if not isinstance(jobs,list) or len(jobs)>MAX_JOBS:raise ValueError()
            ids,keys=set(),set()
            for e in jobs:
                if not isinstance(e,dict) or set(e)!={'job_id','tenant_id','request_hash','idempotency_digest','erased_at','reason'}:raise ValueError()
                if (str(UUID(e['job_id']))!=e['job_id'] or e['job_id'] in ids or not isinstance(e['tenant_id'],str)
                        or not 1<=len(e['tenant_id'])<=1024 or (e['tenant_id'],e['idempotency_digest']) in keys
                        or any(not isinstance(e[k],str) or not re.fullmatch('[0-9a-f]{64}',e[k]) for k in ['request_hash','idempotency_digest'])):raise ValueError()
                ids.add(e['job_id']);keys.add((e['tenant_id'],e['idempotency_digest']))
                timestamp(e['erased_at']);safe_code(e['reason'])
            subscriptions=body.get('subscriptions',[])
            if not isinstance(subscriptions,list) or len(subscriptions)>MAX_SUBSCRIPTIONS:raise ValueError()
            ids,keys=set(),set()
            for e in subscriptions:
                if not isinstance(e,dict) or set(e)!={'subscription_id','tenant_id','request_hash','idempotency_digest','erased_at','reason'}:raise ValueError()
                if (str(UUID(e['subscription_id']))!=e['subscription_id'] or e['subscription_id'] in ids or not isinstance(e['tenant_id'],str)
                        or not 1<=len(e['tenant_id'])<=1024 or (e['tenant_id'],e['idempotency_digest']) in keys
                        or any(not isinstance(e[k],str) or not re.fullmatch('[0-9a-f]{64}',e[k]) for k in ['request_hash','idempotency_digest'])):raise ValueError()
                ids.add(e['subscription_id']);keys.add((e['tenant_id'],e['idempotency_digest']))
                timestamp(e['erased_at']);safe_code(e['reason'])
            if (body['responses'] is not None) != (self.responses is not None):
                raise ValueError()
            responses = self.responses._validated(body['responses'],key) if self.responses is not None else []
            return body['facts'], responses, jobs, subscriptions
        except Exception:
            raise ProductError('invalid_erasure_checkpoint', '检查点签名、外部固定摘要或结构无效；未重放',409) from None

    def _facts(self, db, entries):
        # Markers precede data scans; no completion/reuse can pass the exclusive
        # recovery fence. This also protects a checkpoint whose rows are absent.
        for e in entries:
            old = db.execute('SELECT * FROM sf_capability_fact_erasures WHERE observation_id=%s OR (tenant_id=%s AND event_digest=%s)',
                             (e['observation_id'],e['tenant_id'],e['event_digest'])).fetchall()
            if any(str(r['observation_id'])!=e['observation_id'] or r['tenant_id']!=e['tenant_id'] or r['event_digest']!=e['event_digest'] for r in old):
                conflict()
            db.execute('INSERT INTO sf_capability_fact_erasures VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (e['observation_id'],e['tenant_id'],e['event_digest'],timestamp(e['erased_at']),e['reason']))
        # Event identities are hashed using canonical Python JSON, without a
        # PostgreSQL extension or loading source bodies. Detect the same event
        # restored under a different ID instead of letting it bypass a marker.
        expected = {}
        for e in entries:
            expected.setdefault(e['tenant_id'],{})[e['event_digest']] = e['observation_id']
        scanned = 0
        for tenant, events in expected.items():
            with db.cursor(name='erasure_restore_event_scan') as cursor:
                cursor.execute('SELECT id,event_key FROM sf_capability_observations WHERE tenant_id=%s',(tenant,))
                for row in cursor:
                    scanned += 1
                    if scanned>MAX_SCAN:
                        raise ProductError('erasure_checkpoint_limit','恢复事件扫描超过上限；整批回滚',409)
                    oid = events.get(digest(row['event_key']))
                    if oid is not None and oid != str(row['id']):
                        conflict()
        cleared = 0
        for e in entries:
            row = db.execute("SELECT id,tenant_id,event_key,(payload<>'{}' OR parameters<>'{}' OR revision IS NOT NULL) AS has_content "
                             'FROM sf_capability_observations WHERE id=%s FOR UPDATE',(e['observation_id'],)).fetchone()
            if row is None:
                continue
            if (row['tenant_id']!=e['tenant_id'] or (digest(row['event_key'])!=e['event_digest']
                    and row['event_key']!='erased:'+str(row['id']))):
                conflict()
            marker=db.execute('SELECT * FROM sf_capability_fact_erasures WHERE observation_id=%s',(row['id'],)).fetchone()
            cleared += int(row['has_content'])
            FactRetention._clear(db,row,marker['erased_at'],marker['reason'])
        return cleared

    def _jobs(self, db, entries):
        from .data_capability_job_retention import clear_row
        expected={}
        for e in entries:
            old=db.execute('SELECT * FROM sf_capability_job_erasures WHERE job_id=%s OR (tenant_id=%s AND idempotency_digest=%s)',
                           (e['job_id'],e['tenant_id'],e['idempotency_digest'])).fetchall()
            if any(str(r['job_id'])!=e['job_id'] or any(r[k]!=e[k] for k in ['tenant_id','request_hash','idempotency_digest']) for r in old):conflict()
            db.execute('INSERT INTO sf_capability_job_erasures VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (e['job_id'],e['tenant_id'],e['request_hash'],e['idempotency_digest'],timestamp(e['erased_at']),e['reason']))
            expected.setdefault(e['tenant_id'],{})[e['idempotency_digest']]=e['job_id']
        scanned=0
        for tenant,keys in expected.items():
            with db.cursor(name='erasure_restore_job_scan') as cursor:
                cursor.execute('SELECT id,idempotency_key FROM sf_capability_jobs WHERE tenant_id=%s',(tenant,))
                for row in cursor:
                    scanned+=1
                    if scanned>MAX_SCAN:raise ProductError('erasure_checkpoint_limit','恢复任务扫描超过上限；整批回滚',409)
                    jid=keys.get(digest(row['idempotency_key']))
                    if jid is not None and jid!=str(row['id']):conflict()
        cleared=0
        for e in entries:
            row=db.execute("SELECT id,tenant_id,request_hash,idempotency_key,status,actual_cost,(parameters<>'{}' OR reconciliation_reason IS NOT NULL OR idempotency_key<>('erased:'||id::text)) AS has_content "
                           'FROM sf_capability_jobs WHERE id=%s FOR UPDATE',(e['job_id'],)).fetchone()
            if row is None:continue
            if (row['tenant_id']!=e['tenant_id'] or row['request_hash']!=e['request_hash']
                    or (digest(row['idempotency_key'])!=e['idempotency_digest'] and row['idempotency_key']!='erased:'+str(row['id']))):conflict()
            marker=db.execute('SELECT * FROM sf_capability_job_erasures WHERE job_id=%s',(row['id'],)).fetchone()
            cleared+=int(row['has_content'])
            clear_row(db,row,marker['erased_at'],marker['reason'])
        return cleared

    def _subscriptions(self, db, entries):
        from .data_capability_subscription_retention import clear_row
        expected={}
        for e in entries:
            old=db.execute('SELECT * FROM sf_capability_subscription_erasures WHERE subscription_id=%s OR (tenant_id=%s AND idempotency_digest=%s)',
                           (e['subscription_id'],e['tenant_id'],e['idempotency_digest'])).fetchall()
            if any(str(r['subscription_id'])!=e['subscription_id'] or any(r[k]!=e[k] for k in ['tenant_id','request_hash','idempotency_digest']) for r in old):conflict()
            db.execute('INSERT INTO sf_capability_subscription_erasures VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (e['subscription_id'],e['tenant_id'],e['request_hash'],e['idempotency_digest'],timestamp(e['erased_at']),e['reason']))
            expected.setdefault(e['tenant_id'],{})[e['idempotency_digest']]=e['subscription_id']
        scanned=0
        for tenant,keys in expected.items():
            with db.cursor(name='erasure_restore_subscription_scan') as cursor:
                cursor.execute('SELECT id,idempotency_key FROM sf_capability_subscriptions WHERE tenant_id=%s',(tenant,))
                for row in cursor:
                    scanned+=1
                    if scanned>MAX_SCAN:raise ProductError('erasure_checkpoint_limit','恢复订阅扫描超过上限；整批回滚',409)
                    sid=keys.get(digest(row['idempotency_key']))
                    if sid is not None and sid!=str(row['id']):conflict()
        cleared=0
        for e in entries:
            row=db.execute("SELECT id,tenant_id,request_hash,idempotency_key,(name<>'' OR filters<>'{}' OR active OR next_poll_at IS NOT NULL OR idempotency_key<>('erased:'||id::text)) AS has_content "
                           'FROM sf_capability_subscriptions WHERE id=%s FOR UPDATE',(e['subscription_id'],)).fetchone()
            if row is None:continue
            if (row['tenant_id']!=e['tenant_id'] or row['request_hash']!=e['request_hash']
                    or (digest(row['idempotency_key'])!=e['idempotency_digest'] and row['idempotency_key']!='erased:'+str(row['id']))):conflict()
            batches,seen=clear_row(db,row)
            cleared+=int(row['has_content'] or batches or seen)
        return cleared

    def replay_file(self, path, key, *, expected_hash):
        """Bounded private-file input; exact external pin before JSON parsing."""
        gate=getattr(self.service,'restore_gate',None)
        if gate is not None:gate.close()
        _key(key)
        if not isinstance(expected_hash,str) or not re.fullmatch('[0-9a-f]{64}',expected_hash):
            raise ProductError('invalid_erasure_checkpoint','需要独立固定的检查点SHA256',409)
        # Body limit plus bounded signature/envelope overhead; never read an
        # arbitrarily large backup into memory before enforcing a limit.
        with Path(path).open('rb') as source:
            data=source.read(MAX_BYTES+1025)
        if len(data)>MAX_BYTES+1024:
            raise ProductError('erasure_checkpoint_limit','检查点文件超过大小上限；未重放',409)
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(),expected_hash):
            raise ProductError('invalid_erasure_checkpoint','文件字节与独立固定摘要不符；未重放',409)
        def unique(pairs):
            result={}
            for name,value in pairs:
                if name in result:raise ValueError('duplicate checkpoint field')
                result[name]=value
            return result
        def constant(_):raise ValueError('non-finite checkpoint value')
        try:
            value=json.loads(data.decode('utf-8'),object_pairs_hook=unique,parse_constant=constant)
        except (ValueError,UnicodeError,RecursionError):
            raise ProductError('invalid_erasure_checkpoint','检查点JSON无效或包含重复字段；未重放',409) from None
        return self.replay(value,key,expected_hash=expected_hash)

    def replay(self, value, key, *, expected_hash):
        """Offline pinned atomic replay; configured admission only after commit."""
        gate=getattr(self.service,'restore_gate',None)
        ticket=gate.begin(self.service) if gate is not None else None
        try:
            facts,responses,jobs,subscriptions=self._validated(value,key,expected_hash)
            if gate is not None:gate.validate(value,expected_hash)
            with self.service.connect() as db:
                # Writers take the shared lock BEFORE their job locks. Never hold
                # 29 while acquiring this fence: response writers may hold job rows.
                db.execute('SELECT pg_advisory_xact_lock(20261110,30)')
                db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
                self._check_scope(db,subscriptions=value['schema_version']==VERSION)  # Exact installed scope/policy.
                old=db.execute('SELECT checkpoint_hash FROM sf_capability_erasure_replays WHERE checkpoint_id=%s',
                               (value['checkpoint_id'],)).fetchone()
                if old and old['checkpoint_hash']!=expected_hash:
                    conflict()
                # Restore the earlier response marker before derived fact cleanup
                # can create a later one for the same associated response.
                result=self.responses._replay(db,responses) if self.responses is not None else dict(cleared=0)
                subscriptions_cleared=self._subscriptions(db,subscriptions)
                jobs_cleared=self._jobs(db,jobs)
                cleared=self._facts(db,facts)
                db.execute('INSERT INTO sf_capability_erasure_replays VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (value['checkpoint_id'],expected_hash,self.service.now(),len(facts),len(responses)))
                db.execute('INSERT INTO sf_capability_job_erasure_replays VALUES(%s,%s) ON CONFLICT DO NOTHING',(value['checkpoint_id'],len(jobs)))
                db.execute('INSERT INTO sf_capability_subscription_erasure_replays VALUES(%s,%s) ON CONFLICT DO NOTHING',(value['checkpoint_id'],len(subscriptions)))
                proof=dict(subscription_entries=len(subscriptions),subscriptions_cleared=subscriptions_cleared,checkpoint_id=value['checkpoint_id'],checkpoint_hash=expected_hash,scope=value['scope'],job_entries=len(jobs),jobs_cleared=jobs_cleared,
                            fact_entries=len(facts),response_entries=len(responses),facts_cleared=cleared,response_journal_rows_cleared=result['cleared'])
            if gate is not None:gate.admit(ticket,proof)
            return proof
        except Exception:
            if gate is not None:gate.fail(ticket)
            raise
