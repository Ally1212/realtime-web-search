"""Opt-in durable new-domain queue and monetary reservations.

No service starts or schema initializes on import. Source calls must be made by
an explicitly configured worker, outside the claim transaction. An expired
lease is quarantined, never automatically retried: the provider may have billed.
This ledger is independent of legacy search quota and platform delivery state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .data_capability_contract import CAPABILITIES, admit_request, request_digest, validate_request
from .product import ProductError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_budgets (
 tenant_id text NOT NULL, currency text NOT NULL,
 daily_limit numeric NOT NULL CHECK(daily_limit>=0),
 monthly_limit numeric NOT NULL CHECK(monthly_limit>=0),
 PRIMARY KEY(tenant_id,currency)
);
CREATE TABLE IF NOT EXISTS sf_capability_jobs (
 id uuid PRIMARY KEY, tenant_id text NOT NULL, idempotency_key text NOT NULL,
 request_hash text NOT NULL, parameters jsonb NOT NULL,
 capability text NOT NULL, provider text NOT NULL, license_id text NOT NULL,
 admission jsonb NOT NULL, currency text NOT NULL, billing_unit text NOT NULL,
 reserved numeric NOT NULL CHECK(reserved>=0),
 actual_cost numeric CHECK(actual_cost>=0), settlement_hash text,
 status text NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','canceled','reconciliation_required')),
 cancel_requested boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL, started_at timestamptz, completed_at timestamptz,
 lease_token uuid, lease_expires_at timestamptz,
 result_ref text, error_code text, reconciliation_reason text,
 UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,currency) REFERENCES sf_capability_budgets(tenant_id,currency)
);
CREATE INDEX IF NOT EXISTS sf_capability_jobs_queue ON sf_capability_jobs(status,created_at,id);
CREATE INDEX IF NOT EXISTS sf_capability_jobs_budget ON sf_capability_jobs(tenant_id,currency,created_at);
CREATE TABLE IF NOT EXISTS sf_capability_job_erasures (
 job_id uuid PRIMARY KEY, tenant_id text NOT NULL, request_hash text NOT NULL,
 idempotency_digest text NOT NULL, erased_at timestamptz NOT NULL, reason text NOT NULL,
 UNIQUE(tenant_id,idempotency_digest)
);
CREATE TABLE IF NOT EXISTS sf_capability_job_events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 job_id uuid NOT NULL REFERENCES sf_capability_jobs(id),
 event_at timestamptz NOT NULL, status text NOT NULL, detail jsonb NOT NULL
);
"""
TERMINAL = frozenset({'succeeded', 'partial', 'failed', 'canceled'})
SAFE_CODES = re.compile(r'[a-z][a-z0-9_]{0,79}')


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)) or isinstance(value, str) and len(value) > 100:
        raise ProductError('invalid_budget', '费用必须为非负十进制字符串或整数')
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or number > Decimal('1000000000000'):
            raise ValueError()
        if number.as_tuple().exponent < -9:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise ProductError('invalid_budget', '费用必须为有限非负值，最多9位小数') from None


def required_text(value, name, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ProductError('invalid_parameter', name + ' 无效')
    return value


def job_id(value):
    from uuid import UUID
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise ProductError('not_found', '任务不存在', 404) from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class CapabilityJobs:
    def __init__(self, dsn, *, clock=None):
        self.dsn = dsn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def require_recovered(self):
        gate=getattr(self,'restore_gate',None)
        if gate is not None:gate.require()

    def connect(self):
        connection = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)
        try:
            # Preserve caller DSN options (including an isolated search_path).
            connection.execute("SET statement_timeout='20s'")
            connection.execute("SET lock_timeout='5s'")
            return connection
        except Exception:
            connection.close()
            raise

    def now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError('clock must return aware datetime')
        return value.astimezone(timezone.utc)

    def initialize(self):
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,20)')
            db.execute(SCHEMA)

    def configure_budget(self, tenant_id, currency, daily_limit, monthly_limit):
        """Trusted operator entry point; never bind directly to a customer route."""
        required_text(tenant_id, 'tenant_id')
        if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
            raise ProductError('invalid_budget', 'currency 必须为三个大写字母')
        daily, monthly = amount(daily_limit), amount(monthly_limit)
        with self.connect() as db:
            db.execute('INSERT INTO sf_capability_budgets VALUES(%s,%s,%s,%s) '
                       'ON CONFLICT(tenant_id,currency) DO UPDATE SET daily_limit=EXCLUDED.daily_limit,'
                       'monthly_limit=EXCLUDED.monthly_limit', (tenant_id, currency, daily, monthly))

    @staticmethod
    def public(row):
        names = ('id', 'capability', 'parameters', 'provider', 'license_id', 'currency', 'billing_unit',
                 'reserved', 'actual_cost', 'status', 'cancel_requested', 'created_at',
                 'started_at', 'completed_at', 'result_ref', 'error_code')
        result = {k: row[k] for k in names}
        result['source_retry_policy'] = row['admission'].get('source_retry_policy')
        result['id'] = str(result['id'])
        for k in ('reserved', 'actual_cost'):
            result[k] = str(result[k]) if result[k] is not None else None
        result['reservation_status'] = 'settled' if row['actual_cost'] is not None else 'held'
        result['platform_delivery'] = 'not_submitted'
        return result

    @staticmethod
    def event(db, row, now, detail=None):
        if db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE job_id=%s',(row['id'],)).fetchone():
            detail={}
        db.execute('INSERT INTO sf_capability_job_events(job_id,event_at,status,detail) VALUES(%s,%s,%s,%s)',
                   (row['id'], now, row['status'], Jsonb(detail or {})))

    def _get(self, db, tenant_id, jid, *, lock=False):
        row = db.execute('SELECT * FROM sf_capability_jobs WHERE tenant_id=%s AND id=%s' +
                         (' FOR UPDATE' if lock else ''), (tenant_id, job_id(jid))).fetchone()
        if not row:
            raise ProductError('not_found', '任务不存在', 404)
        return row

    def create(self, tenant_id, payload, key, grant, maximum_cost, currency, *, _db=None):
        self.require_recovered()
        required_text(tenant_id, 'tenant_id')
        required_text(key, 'Idempotency-Key', 120)
        p = validate_request(payload)
        ceiling = amount(maximum_cost)
        if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
            raise ProductError('invalid_budget', 'currency 必须为三个大写字母')
        fingerprint = digest(dict(parameters_hash=request_digest(p), maximum_cost=str(ceiling.normalize()), currency=currency))
        now = self.now()
        with (nullcontext(_db) if _db is not None else self.connect()) as db:
            db.execute("SET LOCAL lock_timeout='5s'")
            db.execute('SELECT pg_advisory_xact_lock_shared(20261110,30)')
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))', (tenant_id,))
            # Serializes create with settlement for this tenant/currency; no
            # timestamp watermark or floating-point budget arithmetic.
            budget = db.execute('SELECT * FROM sf_capability_budgets WHERE tenant_id=%s AND currency=%s FOR UPDATE',
                                (tenant_id, currency)).fetchone()
            if db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE tenant_id=%s AND idempotency_digest=%s',(tenant_id,digest(key))).fetchone():
                raise ProductError('expired','此幂等键对应任务内容已清除，未创建新任务或收费',410)
            previous = db.execute('SELECT * FROM sf_capability_jobs WHERE tenant_id=%s AND idempotency_key=%s',
                                  (tenant_id, key)).fetchone()
            if previous:
                if db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE job_id=%s',(previous['id'],)).fetchone():
                    raise ProductError('expired','此任务内容已清除，未创建新任务或收费',410)
                if previous['request_hash'] != fingerprint:
                    raise ProductError('idempotency_conflict', '同一幂等键的参数或预算不同', 409)
                return self.public(previous)
            admitted = admit_request(p, grant, ceiling, currency, now=now)
            if not budget:
                raise ProductError('budget_not_configured', '新数据域预算尚未配置', 409)
            reserved = amount(admitted['maximum_cost'])
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            month = day.replace(day=1)
            used = db.execute('SELECT COALESCE(sum(COALESCE(actual_cost,reserved)) FILTER (WHERE created_at>=%s),0) AS daily_used,'
                              'COALESCE(sum(COALESCE(actual_cost,reserved)),0) AS monthly_used '
                              'FROM sf_capability_jobs WHERE tenant_id=%s AND currency=%s AND created_at>=%s',
                              (day, tenant_id, currency, month)).fetchone()
            if used['daily_used'] + reserved > budget['daily_limit'] or used['monthly_used'] + reserved > budget['monthly_limit']:
                raise ProductError('budget_exceeded', '日/月预算不足', 429)
            # Only safe, explicit admission metadata enters durable state. The
            # validator and provider credentials never enter the database.
            admission = {k: grant[k] for k in ('provider', 'license_id', 'allowed_use', 'retention_days',
                                              'billing_unit', 'currency', 'expires_at')}
            for proof_field in ('admission_evidence_ref','admission_verified_at'):
                if proof_field in grant:admission[proof_field]=required_text(grant[proof_field],proof_field,200)
            if 'source_retry_policy' in grant:
                admission['source_retry_policy']=dict(grant['source_retry_policy'])
            row = db.execute('INSERT INTO sf_capability_jobs(id,tenant_id,idempotency_key,request_hash,parameters,'
                             'capability,provider,license_id,admission,currency,billing_unit,reserved,status,created_at) '
                             "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING *",
                             (str(uuid4()), tenant_id, key, fingerprint, Jsonb(p), p['capability'], admitted['provider'],
                              admitted['license_id'], Jsonb(admission), currency, admitted['billing_unit'], reserved, now)).fetchone()
            self.event(db, row, now)
            return self.public(row)

    def get(self, tenant_id, jid):
        with self.connect() as db:
            return self.public(self._get(db, tenant_id, jid))

    def list(self, tenant_id, *, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProductError('invalid_parameter', 'limit 必须为 1–100')
        with self.connect() as db:
            rows = db.execute('SELECT * FROM sf_capability_jobs j WHERE tenant_id=%s AND NOT EXISTS(SELECT 1 FROM sf_capability_job_erasures e WHERE e.job_id=j.id) ORDER BY created_at DESC,id DESC LIMIT %s',
                              (tenant_id, limit + 1)).fetchall()
            return dict(jobs=[self.public(row) for row in rows[:limit]], has_more=len(rows) > limit)

    def cancel(self, tenant_id, jid):
        with self.connect() as db:
            row = self._get(db, tenant_id, jid, lock=True)
            if row['status'] in TERMINAL or row['cancel_requested']:
                return self.public(row)
            now = self.now()
            if row['status'] == 'queued':
                row = db.execute("UPDATE sf_capability_jobs SET status='canceled',cancel_requested=true,actual_cost=0,"
                                 'completed_at=%s WHERE id=%s RETURNING *', (now, row['id'])).fetchone()
            else:
                row = db.execute('UPDATE sf_capability_jobs SET cancel_requested=true WHERE id=%s RETURNING *',
                                 (row['id'],)).fetchone()
            self.event(db, row, now, {'cancel_requested': True})
            return self.public(row)

    def claim(self, capability, provider, grant_for, *, lease_seconds=300,
              target_tenant_id=None, target_job_id=None):
        """Trusted worker port. grant_for(tenant, capability) must recheck rights.

        One attempt only; grant validation must be local (no network under lock).
        Returns an internal lease, which must never be returned to customers.
        """
        self.require_recovered()
        if (target_tenant_id is None) != (target_job_id is None):
            raise ProductError('invalid_parameter', '指定任务必须同时提供租户和任务ID')
        selection, args = '', [capability, provider]
        if target_job_id is not None:
            required_text(target_tenant_id, 'target_tenant_id')
            selection = 'AND tenant_id=%s AND id=%s '
            args.extend((target_tenant_id, job_id(target_job_id)))
        if capability not in CAPABILITIES or type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
            raise ProductError('invalid_parameter', '无效能力或lease期限')
        now = self.now()
        with self.connect() as db:
            row = db.execute("SELECT * FROM sf_capability_jobs WHERE status='queued' AND capability=%s AND provider=%s "
                             + selection + 'ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1', args).fetchone()
            if not row:
                return None
            try:
                grant = grant_for(row['tenant_id'], capability)
                admitted = admit_request(row['parameters'], grant, row['reserved'], row['currency'], now=now)
                if any(grant[k] != row['admission'][k] for k in
                       ('provider', 'license_id', 'allowed_use', 'retention_days', 'billing_unit', 'currency')):
                    raise ProductError('admission_changed', '来源准入已变化', 409)
            except ProductError as exc:
                row = db.execute("UPDATE sf_capability_jobs SET status='failed',actual_cost=0,error_code=%s,"
                                 'completed_at=%s WHERE id=%s RETURNING *', (exc.code, now, row['id'])).fetchone()
                self.event(db, row, now)
                return None
            row = db.execute("UPDATE sf_capability_jobs SET status='running',started_at=%s,lease_token=%s,"
                             'lease_expires_at=%s WHERE id=%s RETURNING *',
                             (now, str(uuid4()), now + timedelta(seconds=lease_seconds), row['id'])).fetchone()
            self.event(db, row, now)
            return dict(job=self.public(row), tenant_id=row['tenant_id'], lease_token=str(row['lease_token']),
                        lease_expires_at=row['lease_expires_at'])

    def quarantine_expired(self):
        """No automatic retries/refunds for uncertain external outcomes."""
        now = self.now()
        with self.connect() as db:
            rows = db.execute("UPDATE sf_capability_jobs SET status='reconciliation_required',error_code='lease_expired' "
                              "WHERE status='running' AND lease_expires_at<=%s RETURNING *", (now,)).fetchall()
            for row in rows:
                self.event(db, row, now)
            return len(rows)

    def settle(self, tenant_id, jid, lease_token, status, actual_cost, *, result_ref=None, error_code=None):
        return self._settle(tenant_id, jid, status, actual_cost, result_ref, error_code, lease_token, None)

    def reconcile(self, tenant_id, jid, status, actual_cost, *, reason, result_ref=None, error_code=None):
        """Trusted operator only, after independently resolving provider billing."""
        required_text(reason, 'reconciliation_reason', 2000)
        return self._settle(tenant_id, jid, status, actual_cost, result_ref, error_code, None, reason)

    def _validate_result_reference(self, db, row, result_ref, status):
        raise ProductError('result_not_saved', '成功结算必须先通过事实存储完成', 409)

    def _settle(self, tenant_id, jid, status, actual_cost, result_ref, error_code, lease_token, reason, *, connection=None):
        if status not in TERMINAL:
            raise ProductError('invalid_parameter', '无效终态')
        cost = amount(actual_cost)
        if status in ('succeeded', 'partial'):
            required_text(result_ref, 'result_ref')
        elif result_ref is not None:
            raise ProductError('invalid_parameter', '失败或取消不得声明成功结果')
        if error_code is not None and (not isinstance(error_code, str) or not SAFE_CODES.fullmatch(error_code)):
            raise ProductError('invalid_parameter', 'error_code 必须为安全错误代码')
        signature = digest(dict(status=status, cost=str(cost.normalize()), result_ref=result_ref, error_code=error_code))
        now = self.now()
        with (self.connect() if connection is None else nullcontext(connection)) as db:
            row = self._get(db, tenant_id, jid, lock=True)
            if row['status'] in TERMINAL:
                if row['settlement_hash'] != signature:
                    raise ProductError('settlement_conflict', '终态结算冲突', 409)
                return self.public(row)
            if reason is not None:
                if row['status'] != 'reconciliation_required':
                    raise ProductError('reconciliation_not_required', '任务尚未进入对账', 409)
            elif (row['status'] != 'running' or str(row['lease_token']) != lease_token or row['lease_expires_at'] <= now):
                raise ProductError('stale_lease', 'lease无效或已过期', 409)
            if cost > row['reserved'] and reason is None:
                raise ProductError('cost_exceeded', '实际费用超过已预留上界，须保持对账', 409)
            # In-flight canceled work can still incur a real source charge;
            # discard its result, but never claim the source request was free.
            if row['cancel_requested']:
                status, result_ref = 'canceled', None
            elif status in ('succeeded', 'partial'):
                self._validate_result_reference(db, row, result_ref, status)
            if db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE job_id=%s',(row['id'],)).fetchone():
                reason='erased_job_reconciliation' if reason is not None else None
            row = db.execute('UPDATE sf_capability_jobs SET status=%s,actual_cost=%s,settlement_hash=%s,'
                             'result_ref=%s,error_code=%s,completed_at=%s,reconciliation_reason=%s WHERE id=%s RETURNING *',
                             (status, cost, signature, result_ref, error_code, now, reason, row['id'])).fetchone()
            self.event(db, row, now)
            return self.public(row)
