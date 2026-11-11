"""Bounded, opt-in response spool, separate from validated observations.

Only explicit initialize creates these independent tables. A response can be
preserved even if billing/normalization fails; it is never a successful fact.
No source retries, platform writes or background maintenance on import.
"""
from __future__ import annotations

from datetime import timedelta
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from .data_capability_jobs import digest, job_id, SAFE_CODES
from .google_observations import _safe_config
from .product import ProductError

VERSION = 'searchflow-source-response-1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_response_policy (
 id integer PRIMARY KEY CHECK(id=1), policy_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS sf_capability_responses (
 id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES sf_capability_jobs(id),
 tenant_id text NOT NULL, policy_hash text NOT NULL, lease_token uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('reserved','captured','expired','erased')),
 retain_raw boolean NOT NULL, reserved_bytes bigint NOT NULL,
 payload_bytes bigint NOT NULL DEFAULT 0, payload jsonb, payload_hash text,
 metadata jsonb, snapshot_hash text,
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 erased_at timestamptz, erasure_reason text
);
CREATE INDEX IF NOT EXISTS sf_capability_responses_expiry ON sf_capability_responses(expires_at);
"""


def safe_code(value):
    if not isinstance(value, str) or not SAFE_CODES.fullmatch(value):
        raise ProductError('invalid_parameter', '响应原因必须为安全代码')
    return value


def encoded(value):
    try:
        _safe_config(value)
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except Exception:
        raise ProductError('invalid_response', '响应必须为有限、已脱敏JSON') from None
    return data


class ResponseSession:
    def __init__(self, store, tenant, jid, token):
        self.store, self.tenant, self.job_id, self.token = store, tenant, jid, token

    def save(self, **kwargs):
        return self.store.save(self.tenant, self.job_id, self.token, **kwargs)

    def checkpoint(self, phase, **kwargs):
        return self.store.checkpoint(self.tenant, self.job_id, self.token, phase, **kwargs)

    def finish(self):
        """Release unused reservations without pretending an unseen body exists."""
        return self.store.finish(self.tenant, self.job_id, self.token)


class ResponseStore:
    def __init__(self, service, grant_for, *, max_payload_bytes=16*1024*1024,
                 max_total_bytes=128*1024*1024, max_active_records=1000, ttl_seconds=86400):
        if not callable(grant_for):
            raise ValueError('trusted current grant resolver required')
        limits = (max_payload_bytes, max_total_bytes, max_active_records, ttl_seconds)
        if any(type(v) is not int or v <= 0 for v in limits):
            raise ValueError('positive integer spool limits required')
        if max_payload_bytes > 16*1024*1024 or max_total_bytes < max_payload_bytes or ttl_seconds > 86400:
            raise ValueError('spool payload <=16MiB, total >= payload, TTL <=24h')
        self.service, self.grant_for = service, grant_for
        self.payload_limit, self.total_limit, self.record_limit, self.ttl = limits
        self.policy = digest(dict(payload=max_payload_bytes,total=max_total_bytes,records=max_active_records,ttl=ttl_seconds))

    def initialize(self):
        with self.service.connect() as db:
            db.execute(SCHEMA)
            from .data_capability_retention import SCHEMA as ERASURE_SCHEMA
            db.execute(ERASURE_SCHEMA)
            db.execute('INSERT INTO sf_capability_response_policy VALUES(1,%s) ON CONFLICT DO NOTHING', (self.policy,))
            self._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            # Bootstrap pre-journal deletions before any backup/checkpoint export.
            from .data_capability_retention import record
            for row in db.execute("SELECT id,job_id,tenant_id,erased_at,erasure_reason,state FROM sf_capability_responses WHERE state IN ('expired','erased') FOR UPDATE").fetchall():
                record(db,row,row['erased_at'] or self.service.now(),row['erasure_reason'] or 'legacy_erasure',row['state'])

    def _policy(self, db):
        row = db.execute('SELECT policy_hash FROM sf_capability_response_policy WHERE id=1').fetchone()
        if not row or row['policy_hash'] != self.policy:
            raise ProductError('response_policy_conflict', '响应保存策略未初始化或不一致', 409)

    def _rights(self, job, expires, *, raw):
        grant = self.grant_for(job['tenant_id'], job['capability'])
        self.service._authorize(dict(job,expires_at=expires),grant,self.service.now())
        if raw and grant.get('raw_response_retention_allowed') is not True:
            raise ProductError('response_retention_not_admitted', '当前来源未准许原响应保留', 409)
        return grant

    @staticmethod
    def _purge(db, now):
        from .data_capability_retention import record
        rows = db.execute("SELECT id,job_id,tenant_id FROM sf_capability_responses WHERE expires_at<=%s "
                          "AND state IN ('reserved','captured') FOR UPDATE", (now,)).fetchall()
        for row in rows:
            record(db,row,now,'expired','expired')
        return rows

    def purge_expired(self):
        with self.service.connect() as db:
            self._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            return len(self._purge(db,self.service.now()))

    def ready(self, tenant, capability, provider):
        """Read-only admission/capacity hint; begin still reserves atomically."""
        try:
            self.service.require_recovered()
            grant=self.grant_for(tenant,capability)
            if not grant or grant.get('provider')!=provider or grant.get('raw_response_retention_allowed') is not True:
                return False
            with self.service.connect() as db:
                self._policy(db)
                used=db.execute("SELECT COALESCE(sum(reserved_bytes+payload_bytes),0) bytes,count(*) n FROM sf_capability_responses "
                                "WHERE state IN ('reserved','captured') AND expires_at>%s",(self.service.now(),)).fetchone()
            return used['bytes']+self.payload_limit<=self.total_limit and used['n']<self.record_limit
        except Exception:
            return False

    def begin(self, tenant, jid, token, *, retain_raw):
        self.service.require_recovered()
        if type(retain_raw) is not bool:
            raise ProductError('invalid_parameter','响应保留策略须明确')
        now = self.service.now()
        with self.service.connect() as db:
            self._policy(db)
            # Spool quota is shared across tenant/provider/process, not merely
            # a local semaphore. Reserve before a source can incur a charge.
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            self._purge(db,now)
            job = self.service._get(db,tenant,jid,lock=True)
            if job['status'] != 'running' or str(job['lease_token']) != token or job['lease_expires_at'] <= now:
                raise ProductError('stale_lease','响应预留需要有效执行lease',409)
            if job['cancel_requested']:
                raise ProductError('response_canceled','任务已取消',410)
            grant = self.grant_for(tenant,job['capability'])
            from .data_capability_facts import timestamp
            expires = min(now+timedelta(seconds=self.ttl), now+timedelta(days=grant['retention_days']),timestamp(grant['expires_at'])) if grant else now
            self._rights(job,expires,raw=retain_raw)
            if db.execute('SELECT 1 FROM sf_capability_response_erasures WHERE job_id=%s', (job['id'],)).fetchone():
                raise ProductError('expired','响应已有不可恢复的清除标记',410)
            prior = db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s',(job['id'],)).fetchone()
            if prior:
                if (prior['state'] not in ('reserved','captured') or str(prior['lease_token'])!=token
                        or prior['retain_raw'] != retain_raw):
                    raise ProductError('response_conflict','响应预留已结束或属于其他执行',409)
                return ResponseSession(self,tenant,jid,token)
            used = db.execute("SELECT COALESCE(sum(reserved_bytes+payload_bytes),0) bytes,count(*) n FROM sf_capability_responses "
                              "WHERE state IN ('reserved','captured') AND expires_at>%s",(now,)).fetchone()
            reservation = self.payload_limit if retain_raw else 0
            if used['bytes']+reservation > self.total_limit or used['n']>=self.record_limit:
                raise ProductError('response_spool_full','响应空间已满；未调用来源',503)
            db.execute("INSERT INTO sf_capability_responses(id,job_id,tenant_id,policy_hash,lease_token,state,retain_raw,reserved_bytes,created_at,expires_at) "
                       "VALUES(%s,%s,%s,%s,%s,'reserved',%s,%s,%s,%s)",
                       (str(uuid4()),job['id'],tenant,self.policy,token,retain_raw,reservation,now,expires))
        return ResponseSession(self,tenant,jid,token)

    def save(self, tenant, jid, token, *, http_status, payload, requested_at, received_at,
             redacted_paths=(), reason=None, scope_verified=False, response_complete=True):
        if http_status is not None and (type(http_status) is not int or not 100<=http_status<=599):
            raise ProductError('invalid_response','HTTP状态无效')
        if type(scope_verified) is not bool or type(response_complete) is not bool:
            raise ProductError('invalid_response','响应完整性/范围须明确')
        if (len(redacted_paths)>1024 or any(not isinstance(p,str) or not p or len(p)>4096 for p in redacted_paths)
                or len(encoded(list(redacted_paths)))>65536):
            raise ProductError('invalid_response','脱敏路径超过保存上限')
        now = self.service.now()
        with self.service.connect() as db:
            self._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            job = self.service._get(db,tenant,jid,lock=True)
            row = db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s AND tenant_id=%s FOR UPDATE',(job['id'],tenant)).fetchone()
            if not row or row['state'] not in ('reserved','captured') or str(row['lease_token'])!=token:
                raise ProductError('response_conflict','响应预留不存在或已清除',409)
            if row['state']=='reserved' and row['payload'] is not None:
                raise ProductError('response_conflict','分阶段响应须封存，不可替换为单次响应',409)
            self._rights(job,row['expires_at'],raw=row['retain_raw'])
            if (job['cancel_requested'] or job['status']=='canceled'):
                self._erase(db,row,now,'canceled')
                return None
            if (str(job['lease_token'])!=token or job['status'] not in ('running','reconciliation_required')
                    or requested_at<job['started_at'] or requested_at>received_at or received_at>now):
                raise ProductError('invalid_response','响应不属于本次执行或时间无效',409)
            keep = row['retain_raw'] and payload is not None and scope_verified and response_complete
            body = encoded(payload) if keep else b''
            if len(body)>self.payload_limit:
                raise ProductError('response_too_large','脱敏响应超过预留上限',413)
            meta = dict(schema_version=VERSION,response_id=str(row['id']),job_id=str(job['id']),capability=job['capability'],
                        provider=job['provider'],license_id=job['license_id'],parameters=job['parameters'],
                        http_status=http_status,requested_at=requested_at.isoformat(),received_at=received_at.isoformat(),
                        stored_at=now.isoformat(),expires_at=row['expires_at'].isoformat(),scope_verified=scope_verified,
                        response_complete=response_complete,raw_status=('redacted' if redacted_paths else 'stored') if keep else 'not_retained',
                        redacted_paths=list(redacted_paths) if keep else [],
                        reason=None if keep else safe_code(reason or ('raw_retention_disabled' if not row['retain_raw'] else 'response_not_retainable')),
                        payload_bytes=len(body),payload_hash=digest(payload) if keep else None,
                        hash_encoding='sha256-canonical-json-utf8-not-source-bytes',normalized_fact_status='not_asserted',platform_delivery='not_submitted')
            snapshot=dict(**meta,payload=payload if keep else None)
            snapshot_hash=digest(snapshot)
            if row['state']=='captured':
                # stored_at is fixed on the first successful receipt.
                meta['stored_at']=row['metadata']['stored_at']
                snapshot=dict(**meta,payload=payload if keep else None)
                snapshot_hash=digest(snapshot)
                if row['snapshot_hash']!=snapshot_hash:
                    raise ProductError('response_conflict','同一来源调用不可替换响应',409)
                return dict(snapshot,snapshot_hash=snapshot_hash)
            db.execute("UPDATE sf_capability_responses SET state='captured',reserved_bytes=0,payload_bytes=%s,payload=%s,"
                       "payload_hash=%s,metadata=%s,snapshot_hash=%s WHERE id=%s",
                       (len(body),Jsonb(payload) if keep else None,meta['payload_hash'],Jsonb(meta),snapshot_hash,row['id']))
        return dict(snapshot,snapshot_hash=snapshot_hash)

    def checkpoint(self, tenant, jid, token, phase, *, http_status, payload, requested_at, received_at,
                   redacted_paths=(), reason=None, scope_verified=False, response_complete=True):
        """Append one bounded, immutable receipt before any subsequent paid call.

        The entire journal shares the original reservation; it is NOT a new
        16MiB allowance per phase. Tokens must already have been removed.
        """
        safe_code(phase)
        if (http_status is not None and (type(http_status) is not int or not 100<=http_status<=599)
                or type(scope_verified) is not bool or type(response_complete) is not bool):
            raise ProductError('invalid_response','阶段状态/范围无效')
        if (len(redacted_paths)>1024 or any(not isinstance(p,str) or not p or len(p)>4096 for p in redacted_paths)
                or len(encoded(list(redacted_paths)))>65536):
            raise ProductError('invalid_response','脱敏路径超过保存上限')
        now=self.service.now()
        with self.service.connect() as db:
            self._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            job=self.service._get(db,tenant,jid,lock=True)
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s AND tenant_id=%s FOR UPDATE',
                           (job['id'],tenant)).fetchone()
            if not row or row['state']!='reserved' or str(row['lease_token'])!=token or not row['retain_raw']:
                raise ProductError('response_conflict','分阶段响应需要有效原文预留',409)
            self._rights(job,row['expires_at'],raw=True)
            if (job['cancel_requested'] or job['status']!='running' or str(job['lease_token'])!=token
                    or job['lease_expires_at']<=now or requested_at<job['started_at']
                    or requested_at>received_at or received_at>now):
                raise ProductError('stale_lease','阶段响应不属于有效执行',409)
            keep=payload is not None and scope_verified and response_complete
            call=dict(phase=phase,http_status=http_status,requested_at=requested_at.isoformat(),received_at=received_at.isoformat(),
                      scope_verified=scope_verified,response_complete=response_complete,
                      raw_status=('redacted' if redacted_paths else 'stored') if keep else 'not_retained',
                      redacted_paths=list(redacted_paths) if keep else [],
                      reason=None if keep else safe_code(reason or 'response_not_retainable'),
                      payload=payload if keep else None,payload_hash=digest(payload) if keep else None)
            journal=row['payload'] or dict(schema_version='searchflow-source-call-journal-1',calls=[])
            if row['payload'] is not None and digest(journal)!=row['payload_hash']:
                raise ProductError('corrupt_response','阶段响应校验失败',409)
            prior=next((v for v in journal['calls'] if v['phase']==phase),None)
            if prior is not None:
                if prior!=call:raise ProductError('response_conflict','同一阶段不可替换',409)
                return call
            if len(journal['calls'])>=8:
                raise ProductError('response_too_large','阶段响应最多8次',413)
            from .data_capability_facts import timestamp
            if journal['calls'] and requested_at<timestamp(journal['calls'][-1]['received_at']):
                raise ProductError('invalid_response','阶段调用时间不可倒序')
            journal['calls'].append(call)
            body=encoded(journal)
            if len(body)>self.payload_limit:
                raise ProductError('response_too_large','全部阶段响应合计超过原预留',413)
            db.execute('UPDATE sf_capability_responses SET payload=%s,payload_bytes=%s,payload_hash=%s,reserved_bytes=%s WHERE id=%s',
                       (Jsonb(journal),len(body),digest(journal),self.payload_limit-len(body),row['id']))
            return call

    def _seal(self, db, row, job, now):
        """Seal only a received journal; never resume or retry a source call."""
        if row['payload'] is None:return False
        journal=row['payload']
        if digest(journal)!=row['payload_hash']:
            raise ProductError('corrupt_response','阶段响应校验失败',409)
        calls=journal['calls']
        meta=dict(schema_version=VERSION,response_id=str(row['id']),job_id=str(job['id']),capability=job['capability'],
                  provider=job['provider'],license_id=job['license_id'],parameters=job['parameters'],http_status=None,
                  requested_at=calls[0]['requested_at'],received_at=calls[-1]['received_at'],stored_at=now.isoformat(),
                  expires_at=row['expires_at'].isoformat(),scope_verified=all(c['scope_verified'] for c in calls),
                  response_complete=all(c['response_complete'] for c in calls),
                  raw_status='redacted' if any(c['redacted_paths'] for c in calls) else 'stored',redacted_paths=[],reason=None,
                  payload_bytes=row['payload_bytes'],payload_hash=row['payload_hash'],
                  hash_encoding='sha256-canonical-json-utf8-not-source-bytes',normalized_fact_status='not_asserted',
                  platform_delivery='not_submitted',capture_kind='source_call_journal',call_count=len(calls))
        db.execute("UPDATE sf_capability_responses SET state='captured',reserved_bytes=0,metadata=%s,snapshot_hash=%s WHERE id=%s",
                   (Jsonb(meta),digest(dict(meta,payload=journal)),row['id']))
        return True

    def seal_abandoned(self, *, limit=100, tenant=None):
        """Explicit recovery port, no thread/network; closed/expired leases only."""
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('bounded recovery limit required')
        now=self.service.now();sealed=0
        with self.service.connect() as db:
            self._policy(db)
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            self._purge(db,now)
            rows=db.execute("SELECT r.* FROM sf_capability_responses r JOIN sf_capability_jobs j ON j.id=r.job_id "
                            "WHERE r.state='reserved' AND r.payload IS NOT NULL AND "
                            "(j.status<>'running' OR j.lease_expires_at<=%s) AND (%s::text IS NULL OR r.tenant_id=%s) "
                            "ORDER BY r.created_at,r.id LIMIT %s FOR UPDATE OF r SKIP LOCKED",
                            (now,tenant,tenant,limit)).fetchall()
            for row in rows:
                job=self.service._get(db,row['tenant_id'],str(row['job_id']),lock=True)
                if job['cancel_requested']:self._erase(db,row,now,'canceled')
                else:sealed+=int(self._seal(db,row,job,now))
        return sealed

    @staticmethod
    def _erase(db,row,now,reason):
        from .data_capability_retention import record
        record(db,row,now,reason)

    def erase(self, tenant, jid, *, reason):
        """Trusted operator port; not a customer mutation endpoint."""
        safe_code(reason)
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            self.service._get(db,tenant,jid,lock=True)
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s AND tenant_id=%s FOR UPDATE',(job_id(jid),tenant)).fetchone()
            if row:
                self._erase(db,row,self.service.now(),reason)
            return row is not None

    def finish(self, tenant, jid, token):
        now=self.service.now()
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            job=self.service._get(db,tenant,jid,lock=True)
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s AND tenant_id=%s FOR UPDATE',(job['id'],tenant)).fetchone()
            if not row or str(row['lease_token'])!=token:
                return
            if job['cancel_requested']:
                self._erase(db,row,now,'canceled')
                return
            if row['state']!='reserved':
                return
            if self._seal(db,row,job,now):
                return
            meta=dict(schema_version=VERSION,response_id=str(row['id']),job_id=str(job['id']),capability=job['capability'],provider=job['provider'],
                      license_id=job['license_id'],parameters=job['parameters'],http_status=None,requested_at=None,received_at=None,
                      stored_at=now.isoformat(),expires_at=row['expires_at'].isoformat(),scope_verified=False,response_complete=False,
                      raw_status='not_retained',redacted_paths=[],reason='source_response_unavailable',payload_bytes=0,payload_hash=None,
                      hash_encoding='sha256-canonical-json-utf8-not-source-bytes',normalized_fact_status='not_asserted',platform_delivery='not_submitted')
            db.execute("UPDATE sf_capability_responses SET state='captured',reserved_bytes=0,metadata=%s,snapshot_hash=%s WHERE id=%s",
                       (Jsonb(meta),digest(dict(meta,payload=None)),row['id']))

    def get(self, tenant, jid):
        self.service.require_recovered()
        with self.service.connect() as db:
            self._policy(db)
            job=self.service._get(db,tenant,jid)
            from .data_capability_job_retention import assert_live
            assert_live(db,tenant,jid)
            if db.execute('SELECT 1 FROM sf_capability_response_erasures WHERE job_id=%s AND tenant_id=%s',(job['id'],tenant)).fetchone():
                raise ProductError('expired','响应已清除',410)
            row=db.execute('SELECT * FROM sf_capability_responses WHERE job_id=%s AND tenant_id=%s',(job['id'],tenant)).fetchone()
            if not row:
                raise ProductError('not_found','此任务没有独立响应记录',404)
            self._rights(job,row['expires_at'],raw=row['retain_raw'])
            if row['state'] in ('erased','expired') or job['cancel_requested']:
                raise ProductError('expired','响应已清除或取消',410)
            if row['state']=='reserved':
                raise ProductError('response_pending','来源响应尚未保存',409)
            snapshot=dict(row['metadata'],payload=row['payload'])
            if digest(snapshot)!=row['snapshot_hash'] or (row['payload'] is not None and digest(row['payload'])!=row['payload_hash']):
                raise ProductError('corrupt_response','响应快照校验失败',409)
            return dict(snapshot,snapshot_hash=row['snapshot_hash'])
