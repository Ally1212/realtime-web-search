"""Opt-in source executor. No adapters, threads, schema or calls on import.

Adapters are trusted code, not customer configuration. They must bound their own
transport to context.deadline and report confirmed bills; an exception never
means a free call. Uncertain/late outcomes are quarantined without source retry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import copy
import threading
import time
from uuid import uuid4

from .data_capability_contract import CAPABILITIES, admit_request
from .data_capability_jobs import SAFE_CODES, amount, digest, required_text
from .product import ProductError
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_executor_scopes (
 scope text PRIMARY KEY, provider text NOT NULL, policy_hash text NOT NULL,
 next_at timestamptz NOT NULL, token uuid, expires_at timestamptz
);
CREATE TABLE IF NOT EXISTS sf_capability_dispatches (
 job_id uuid PRIMARY KEY REFERENCES sf_capability_jobs(id),
 scope text NOT NULL REFERENCES sf_capability_executor_scopes(scope),
 entered_at timestamptz NOT NULL, finished_at timestamptz,
 state text NOT NULL CHECK(state IN ('entered','completed','quarantined')),
 actual_cost numeric CHECK(actual_cost>=0), error_code text, billing_evidence jsonb
);
ALTER TABLE sf_capability_dispatches ADD COLUMN IF NOT EXISTS billing_evidence jsonb;
ALTER TABLE sf_capability_dispatches ADD COLUMN IF NOT EXISTS billing_parts jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE sf_capability_dispatches ADD COLUMN IF NOT EXISTS call_attempts jsonb NOT NULL DEFAULT '[]'::jsonb;
"""


@dataclass(frozen=True)
class SourceContext:
    job_id: str
    parameters: dict
    maximum_cost: str
    currency: str
    deadline: object
    # Adapters must propagate this stable identity to an upstream idempotency
    # mechanism when available. It is never a claim that the source supports it.
    event_key: str
    canceled: object
    response: object | None = None
    confirm_bill: object | None = None
    before_call: object | None = None
    defer_calls: object | None = None
    source_retry_policy: dict | None = None


@dataclass(frozen=True)
class SourceResult:
    facts: dict
    actual_cost: object
    revision: str | None = None
    supersedes: str | None = None
    billing_evidence: dict | None = None
    retry_after: int = 0


class SourceFailure(Exception):
    """Confirmed source failure; cost=None means billing remains unknown.

    retry_after delays other jobs on this account/asset. retryable is only a
    trusted adapter hint; the executor never replays the adapter by itself.
    Supply only a safe code; raw response, credentials and exception text are
    deliberately excluded from the persistent dispatch/event ledger.
    """
    def __init__(self, code, *, actual_cost=None, retry_after=0, billing_evidence=None, retryable=False):
        if not isinstance(code, str) or not SAFE_CODES.fullmatch(code):
            raise ValueError('safe source error code required')
        if type(retry_after) is not int or not 0 <= retry_after <= 86400:
            raise ValueError('retry_after must be 0–86400 seconds')
        if type(retryable) is not bool:raise ValueError('explicit retry classification required')
        self.retryable = retryable
        self.code = code
        self.actual_cost = None if actual_cost is None else amount(actual_cost)
        self.retry_after = retry_after
        self.billing_evidence = billing_evidence
        super().__init__(code)


class SourceCallDeferred(ProductError):
    """No source entry was recorded; a trusted adapter may wait within deadline."""
    def __init__(self, seconds):
        self.retry_after=seconds
        super().__init__('source_call_deferred','共享来源调用间隔尚未满足',429)


class CapabilityExecutor:
    """One synchronous dispatch at a time per shared provider/account/asset scope.

    Multiple processes use the same durable scope lock and spacing. A crashed
    process cannot release another owner's permit. Changing a scope's policy
    requires an explicit operator change; constructors cannot silently override
    an already registered policy. All capabilities sharing an account quota
    must use the same scope. No automatic billing retries or source fallback.
    """
    def __init__(self, service, grant_for, *, capability, provider, scope, execute,
                 interval_seconds, lease_seconds=300, poll_seconds=1, response_store=None, admission_ready=None):
        if capability not in CAPABILITIES or not callable(grant_for) or not callable(execute):
            raise ValueError('trusted capability, grant resolver and adapter required')
        required_text(provider, 'provider')
        required_text(scope, 'scope')
        if type(interval_seconds) is not int or not 0 <= interval_seconds <= 86400:
            raise ValueError('interval_seconds must be 0–86400')
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 900:
            raise ValueError('lease_seconds must be 10–900')
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or not .01 <= poll_seconds <= 10:
            raise ValueError('poll_seconds must be .01–10')
        if response_store is not None and response_store.service is not service:
            raise ValueError('response store must share the executor service')
        if admission_ready is not None and not callable(admission_ready):
            raise ValueError('trusted lifecycle admission callback required')
        self.admission_ready=admission_ready
        self.response_store = response_store
        self.service, self.grant_for, self.execute = service, grant_for, execute
        self.capability, self.provider, self.scope = capability, provider, scope
        self.interval, self.lease, self.poll = interval_seconds, lease_seconds, poll_seconds
        self.policy = digest(dict(provider=provider, interval=interval_seconds, concurrency=1, lease=lease_seconds))
        self._stop = threading.Event()
        self._thread = None
        self._guard = threading.Lock()
        self._last_tick = float('-inf')
        self.failure_code = None

    def initialize(self):
        """Explicit executor tables only; base service must be initialized first."""
        with self.service.connect() as db:
            db.execute(SCHEMA)
            db.execute('INSERT INTO sf_capability_executor_scopes(scope,provider,policy_hash,next_at) '
                       'VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (self.scope, self.provider, self.policy, self.service.now()))
            row = db.execute('SELECT * FROM sf_capability_executor_scopes WHERE scope=%s', (self.scope,)).fetchone()
            if row['provider'] != self.provider or row['policy_hash'] != self.policy:
                raise ProductError('executor_policy_conflict', '共享来源限流配置不一致', 409)

    def ready(self, tenant, capability, provider):
        """Mount as runtime.worker_ready only for explicitly supervised adapters."""
        try:
            self.service.require_recovered()
            if self.admission_ready is not None and not self.admission_ready():return False
        except Exception:return False
        alive = (capability == self.capability and provider == self.provider
                 and self._thread is not None and self._thread.is_alive()
                 and not self._stop.is_set() and self.failure_code is None
                 and time.monotonic() - self._last_tick < self.lease)
        if not alive:
            return False
        policy=getattr(self.execute,'retry_policy',None)
        if policy is not None:
            try:
                approved=self.grant_for(tenant,capability).get('source_retry_policy')
                if (policy.maximum_attempts>1 or approved is not None) and approved!=policy.public():return False
                if approved is not None and (not getattr(self.execute,'retain_raw',False) or getattr(self.execute,'call_ceiling',getattr(self.execute,'_ceiling',None)) is None):return False
            except Exception:return False
        if getattr(self.execute,'requires_response_store',False):
            if self.response_store is None or not self.response_store.ready(tenant,capability,provider):
                return False
        try:
            with self.service.connect() as db:
                row = db.execute('SELECT policy_hash FROM sf_capability_executor_scopes WHERE scope=%s', (self.scope,)).fetchone()
                blocked = db.execute("SELECT 1 FROM sf_capability_dispatches d JOIN sf_capability_jobs j ON j.id=d.job_id "
                                     "WHERE d.scope=%s AND (j.status='reconciliation_required' OR "
                                     "(d.state='entered' AND j.status='running' AND j.lease_expires_at<=%s)) LIMIT 1",
                                     (self.scope, self.service.now())).fetchone()
            return row is not None and row['policy_hash'] == self.policy and blocked is None
        except Exception:
            return False

    def start(self):
        self.service.require_recovered()
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError('executor is already running or still stopping')
            with self.service.connect() as db:
                row = db.execute('SELECT policy_hash FROM sf_capability_executor_scopes WHERE scope=%s', (self.scope,)).fetchone()
                if not row or row['policy_hash'] != self.policy:
                    raise ProductError('executor_policy_conflict', '执行器未初始化或配置不符', 409)
            self._stop.clear()
            self.failure_code = None
            self._last_tick = float('-inf')
            self._thread = threading.Thread(target=self._run, name='capability-executor', daemon=True)
            self._thread.start()

    def is_alive(self):
        """Include a stopping in-flight thread; false readiness is not exit."""
        thread=self._thread
        return thread is not None and thread.is_alive()

    def stop(self, timeout=5):
        """Stops accepting work immediately; in-flight calls retain actual billing."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _run(self):
        try:
            while not self._stop.is_set():
                self._last_tick = time.monotonic()
                self.run_once()
                self._stop.wait(self.poll)
        except Exception:
            # Do not log arbitrary exception text: it can contain source secrets.
            self.failure_code = 'executor_internal_error'
            self._stop.set()

    def _permit(self):
        now, token = self.service.now(), str(uuid4())
        with self.service.connect() as db:
            row = db.execute('SELECT * FROM sf_capability_executor_scopes WHERE scope=%s FOR UPDATE', (self.scope,)).fetchone()
            if not row or row['provider'] != self.provider or row['policy_hash'] != self.policy:
                raise ProductError('executor_policy_conflict', '执行器未初始化或配置不符', 409)
            if row['next_at'] > now or row['token'] and row['expires_at'] > now:
                return None
            # A crashed dispatch is not automatically replayed, even when its
            # account permit eventually expires. Billing remains reserved.
            rows = db.execute("UPDATE sf_capability_jobs j SET status='reconciliation_required',error_code='lease_expired' "
                              "FROM sf_capability_dispatches d WHERE d.job_id=j.id AND d.scope=%s "
                              "AND j.status='running' AND j.lease_expires_at<=%s RETURNING j.*", (self.scope, now)).fetchall()
            for job in rows:
                self.service.event(db, job, now)
            db.execute("UPDATE sf_capability_dispatches d SET state='quarantined',finished_at=%s,error_code='lease_expired' "
                       "FROM sf_capability_jobs j WHERE j.id=d.job_id AND d.scope=%s AND d.state='entered' "
                       "AND j.status='reconciliation_required'", (now, self.scope))
            # Crash after the atomic fact/settlement commit but before this
            # small journal update: repair from the terminal local ledger,
            # without a second adapter call or a new observation.
            db.execute("UPDATE sf_capability_dispatches d SET state='completed',finished_at=j.completed_at,"
                       "actual_cost=j.actual_cost,error_code=j.error_code FROM sf_capability_jobs j "
                       "WHERE j.id=d.job_id AND d.scope=%s AND d.state='entered' "
                       "AND j.status IN ('succeeded','partial','failed','canceled')", (self.scope,))
            unresolved = db.execute("SELECT 1 FROM sf_capability_dispatches d JOIN sf_capability_jobs j ON j.id=d.job_id "
                                    "WHERE d.scope=%s AND (j.status='reconciliation_required' OR (d.state='entered' AND j.status='running')) LIMIT 1", (self.scope,)).fetchone()
            if unresolved:
                return None
            queued = db.execute("SELECT 1 FROM sf_capability_jobs WHERE status='queued' AND capability=%s AND provider=%s LIMIT 1",
                                (self.capability, self.provider)).fetchone()
            if not queued:
                return None
            db.execute('UPDATE sf_capability_executor_scopes SET token=%s,expires_at=%s,next_at=%s WHERE scope=%s',
                       (token, now + timedelta(seconds=self.lease), now + timedelta(seconds=self.interval), self.scope))
        return token

    def _cooldown(self, seconds):
        if type(seconds) is not int or not 0<=seconds<=86400:raise ValueError('bounded shared cooldown required')
        with self.service.connect() as db:
            db.execute('UPDATE sf_capability_executor_scopes SET next_at=GREATEST(next_at,%s) '
                       'WHERE scope=%s AND policy_hash=%s',
                       (self.service.now() + timedelta(seconds=seconds), self.scope, self.policy))

    def _release(self, token, retry_after=0):
        with self.service.connect() as db:
            # A late rate-limit response must still extend account cooldown,
            # but an old owner must never clear a newer owner's active permit.
            db.execute('UPDATE sf_capability_executor_scopes SET token=CASE WHEN token=%s THEN NULL ELSE token END,'
                       'expires_at=CASE WHEN token=%s THEN NULL ELSE expires_at END,next_at=GREATEST(next_at,%s) '
                       'WHERE scope=%s AND policy_hash=%s',
                       (token, token, self.service.now() + timedelta(seconds=retry_after), self.scope, self.policy))

    def _dispatch(self, claim):
        job, tenant, token = claim['job'], claim['tenant_id'], claim['lease_token']
        grant = self.grant_for(tenant, self.capability)
        # Recheck after acquiring the account permit and claiming the job.
        with self.service.connect() as db:
            row = self.service._get(db, tenant, job['id'], lock=True)
            if row['status'] != 'running' or str(row['lease_token']) != token or row['lease_expires_at'] <= self.service.now():
                raise ProductError('stale_lease', '执行lease已过期', 409)
            try:self.service.require_recovered()
            except ProductError as exc:
                self.service._settle(tenant,job['id'],'failed','0',None,exc.code,token,None,connection=db)
                return None
            if row['cancel_requested'] or self._stop.is_set():
                self.service._settle(tenant, job['id'], 'canceled', '0', None, None, token, None, connection=db)
                return None
            try:
                admit_request(row['parameters'], grant, row['reserved'], row['currency'], now=self.service.now())
                if getattr(self.execute, 'retain_raw', False) and grant.get('raw_response_retention_allowed') is not True:
                    raise ProductError('response_retention_not_admitted', '执行前原文许可未获准', 409)
                if grant.get('source_retry_policy')!=row['admission'].get('source_retry_policy'):
                    raise ProductError('admission_changed','执行前重试授权已变化',409)
                if any(grant[k] != row['admission'][k] for k in ('provider', 'license_id', 'allowed_use', 'retention_days', 'billing_unit', 'currency')):
                    raise ProductError('admission_changed', '执行前来源准入变化', 409)
            except ProductError as exc:
                self.service._settle(tenant, job['id'], 'failed', '0', None, exc.code, token, None, connection=db)
                return None
            db.execute("INSERT INTO sf_capability_dispatches(job_id,scope,entered_at,state) VALUES(%s,%s,%s,'entered')",
                       (job['id'], self.scope, self.service.now()))
        return grant

    def _bill(self, claim, cost, evidence):
        """Persist a confirmed, correctly denominated source bill before facts.

        Older synthetic/internal adapters may omit evidence; actual providers
        should supply it. Evidence is an opaque safe reference, never a URL,
        credential or a provider response body.
        """
        if evidence is not None:
            job = claim['job']
            if (not isinstance(evidence, dict) or set(evidence) != {'reference', 'currency', 'billing_unit', 'actual_cost'}
                    or evidence['currency'] != job['currency'] or evidence['billing_unit'] != job['billing_unit']
                    or amount(evidence['actual_cost']) != cost):
                raise ValueError('source billing evidence conflicts with frozen job units')
            import re
            if not isinstance(evidence['reference'], str) or not re.fullmatch(r'[A-Za-z0-9:_.\-/]{1,200}', evidence['reference']):
                raise ValueError('safe opaque billing reference required')
        with self.service.connect() as db:
            row=db.execute('SELECT billing_parts,call_attempts FROM sf_capability_dispatches WHERE job_id=%s FOR UPDATE',
                           (claim['job']['id'],)).fetchone()
            if row and {p['phase'] for p in row['call_attempts']}!={p['phase'] for p in row['billing_parts']}:
                raise ValueError('an entered source call still lacks a confirmed bill')
            if row and row['billing_parts'] and sum(amount(p['actual_cost']) for p in row['billing_parts'])!=cost:
                raise ValueError('final source bill must equal all confirmed call receipts')
            db.execute('UPDATE sf_capability_dispatches SET actual_cost=%s,billing_evidence=%s WHERE job_id=%s',
                       (cost, Jsonb(evidence) if evidence is not None else None, claim['job']['id']))

    def _before_call(self, claim, phase, maximum_cost, permit):
        """Recheck frozen rights/lease/cancel and remaining budget per paid hop."""
        self.service.require_recovered()
        if not isinstance(phase,str) or not SAFE_CODES.fullmatch(phase):raise ValueError('safe call phase required')
        ceiling=amount(maximum_cost);now=self.service.now()
        with self.service.connect() as db:
            account=db.execute('SELECT * FROM sf_capability_executor_scopes WHERE scope=%s FOR UPDATE',(self.scope,)).fetchone()
            if (not account or str(account['token'])!=permit or account['policy_hash']!=self.policy
                    or account['expires_at']<=now):
                raise ProductError('source_call_not_admitted','共享来源许可已结束或属于其他执行',409)
            row=self.service._get(db,claim['tenant_id'],claim['job']['id'],lock=True)
            if (self._stop.is_set() or row['cancel_requested'] or row['status']!='running'
                    or str(row['lease_token'])!=claim['lease_token'] or row['lease_expires_at']<=now+timedelta(seconds=2)):
                raise ProductError('source_call_not_admitted','来源下一次调用已停止或执行过期',409)
            grant=self.grant_for(claim['tenant_id'],self.capability)
            admit_request(row['parameters'],grant,row['reserved'],row['currency'],now=now)
            if grant.get('source_retry_policy')!=row['admission'].get('source_retry_policy'):
                raise ProductError('admission_changed','来源下一次调用前重试授权已变化',409)
            if any(grant[k]!=row['admission'][k] for k in ('provider','license_id','allowed_use','retention_days','billing_unit','currency')):
                raise ProductError('admission_changed','来源下一次调用前准入已变化',409)
            if getattr(self.execute,'retain_raw',False) and grant.get('raw_response_retention_allowed') is not True:
                raise ProductError('response_retention_not_admitted','下一次调用原文许可已撤销',409)
            if getattr(self.execute,'requires_response_store',False):
                if self.response_store is None:
                    raise ProductError('response_capture_unavailable','下一次调用没有响应预留',409)
                receipt=db.execute('SELECT state,lease_token,expires_at,retain_raw FROM sf_capability_responses WHERE job_id=%s',
                                   (row['id'],)).fetchone()
                if (not receipt or receipt['state']!='reserved' or str(receipt['lease_token'])!=claim['lease_token']
                        or receipt['expires_at']<=now or not receipt['retain_raw']):
                    raise ProductError('response_capture_unavailable','下一次调用的原文预留已结束或被清除',409)
            dispatch=db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s FOR UPDATE',(row['id'],)).fetchone()
            if not dispatch or dispatch['state']!='entered':raise ProductError('stale_lease','来源执行已结束',409)
            attempts=dispatch['call_attempts'];confirmed={p['phase'] for p in dispatch['billing_parts']}
            if any(p['phase']==phase for p in attempts):
                raise ProductError('source_call_already_entered','同一阶段不得重新调用来源',409)
            if any(p['phase'] not in confirmed for p in attempts):
                raise ProductError('source_call_unresolved','前一阶段费用未确认，不得继续收费调用',409)
            paid={p['phase']:amount(p['actual_cost']) for p in dispatch['billing_parts']}
            if any(paid[p['phase']]>amount(p['maximum_cost']) for p in attempts):
                raise ProductError('source_call_budget_exceeded','前一阶段实际费用超过批准上界，不再调用',409)
            if len(attempts)>=8:raise ProductError('source_call_limit','单任务最多8次来源调用',409)
            if attempts and account['next_at']>now:
                import math
                raise SourceCallDeferred(math.ceil((account['next_at']-now).total_seconds()))
            spent=sum((amount(p['actual_cost']) for p in dispatch['billing_parts']),amount('0'))
            if spent+ceiling>row['reserved']:
                raise ProductError('source_call_budget_exceeded','下一次调用上界超过剩余授权',409)
            attempts.append(dict(phase=phase,maximum_cost=str(ceiling),entered_at=now.isoformat()))
            db.execute('UPDATE sf_capability_dispatches SET call_attempts=%s WHERE job_id=%s',(Jsonb(attempts),row['id']))
            db.execute('UPDATE sf_capability_executor_scopes SET next_at=GREATEST(next_at,%s) WHERE scope=%s',
                       (now+timedelta(seconds=self.interval),self.scope))
            return str(spent)

    def _partial_bill(self, claim, phase, evidence):
        """Durable per-call receipts; never treat a partial sum as final cost."""
        if not isinstance(phase,str) or not SAFE_CODES.fullmatch(phase):raise ValueError('safe call phase required')
        job=claim['job']
        if (not isinstance(evidence,dict) or set(evidence)!={'reference','currency','billing_unit','actual_cost'}
                or evidence['currency']!=job['currency'] or evidence['billing_unit']!=job['billing_unit']):
            raise ValueError('confirmed call bill units must match job')
        import re
        if not isinstance(evidence['reference'],str) or not re.fullmatch(r'[A-Za-z0-9:_.\-/]{1,200}',evidence['reference']):
            raise ValueError('safe opaque call bill reference required')
        part=dict(evidence,phase=phase)
        part['actual_cost']=str(amount(evidence['actual_cost']))
        with self.service.connect() as db:
            row=self.service._get(db,claim['tenant_id'],job['id'],lock=True)
            if (str(row['lease_token'])!=claim['lease_token'] or row['status'] not in ('running','reconciliation_required')):
                raise ProductError('stale_lease','费用检查点不属于本次执行',409)
            dispatch=db.execute('SELECT * FROM sf_capability_dispatches WHERE job_id=%s FOR UPDATE',(row['id'],)).fetchone()
            if not dispatch or dispatch['state'] not in ('entered','quarantined'):
                raise ProductError('stale_lease','费用执行已结束',409)
            if not any(p['phase']==phase for p in dispatch['call_attempts']):
                raise ProductError('billing_conflict','费用必须对应已准入的来源调用',409)
            parts=dispatch['billing_parts'];prior=next((p for p in parts if p['phase']==phase),None)
            if prior is not None:
                if prior!=part:raise ProductError('billing_conflict','同一调用费用不可覆盖',409)
                return str(sum((amount(p['actual_cost']) for p in parts),amount('0')))
            if len(parts)>=8:raise ProductError('billing_conflict','单任务费用检查点最多8次',409)
            parts.append(part);total=sum((amount(p['actual_cost']) for p in parts),amount('0'))
            db.execute('UPDATE sf_capability_dispatches SET billing_parts=%s,actual_cost=%s WHERE job_id=%s',
                       (Jsonb(parts),total,row['id']))
            return str(total)

    def _record(self, claim, *, error=None, cost=None, quarantine=False):
        now = self.service.now()
        with self.service.connect() as db:
            row = self.service._get(db, claim['tenant_id'], claim['job']['id'], lock=True)
            if quarantine and row['status'] == 'running' and str(row['lease_token']) == claim['lease_token']:
                row = db.execute("UPDATE sf_capability_jobs SET status='reconciliation_required',error_code=%s WHERE id=%s RETURNING *",
                                 (error, row['id'])).fetchone()
                self.service.event(db, row, now)
            db.execute('UPDATE sf_capability_dispatches SET state=%s,finished_at=%s,actual_cost=COALESCE(%s,actual_cost),error_code=%s WHERE job_id=%s',
                       ('quarantined' if quarantine else 'completed', now, cost, error, row['id']))

    def run_once(self, *, target_tenant_id=None, target_job_id=None):
        """One explicit polling step, also usable by a synchronous supervisor."""
        if (target_tenant_id is None) != (target_job_id is None):
            raise ProductError('invalid_parameter', '指定任务必须同时提供租户和任务ID')
        target = {}
        if target_job_id is not None:
            from .data_capability_jobs import job_id
            required_text(target_tenant_id, 'target_tenant_id')
            target = dict(target_tenant_id=target_tenant_id, target_job_id=job_id(target_job_id))
        if self._stop.is_set():
            return False
        self.service.require_recovered()
        if self.admission_ready is not None and not self.admission_ready():return False
        permit = self._permit()
        if permit is None:
            return False
        claim, delay, response_session = None, 0, None
        try:
            claim = self.service.claim(self.capability, self.provider, self.grant_for, lease_seconds=self.lease, **target)
            if claim is None:
                return False
            if self._dispatch(claim) is None:
                return True
            job, tenant, token = claim['job'], claim['tenant_id'], claim['lease_token']
            context = SourceContext(job['id'], copy.deepcopy(job['parameters']), job['reserved'], job['currency'],
                                    claim['lease_expires_at'] - timedelta(seconds=2), 'capability:' + job['id'],
                                    lambda: self._stop.is_set() or self.service.get(tenant, job['id'])['cancel_requested'],
                                    source_retry_policy=copy.deepcopy(job.get('source_retry_policy')))
            context=replace(context,confirm_bill=lambda phase,evidence:self._partial_bill(claim,phase,evidence),
                            before_call=lambda phase,ceiling:self._before_call(claim,phase,ceiling,permit),defer_calls=self._cooldown)
            cost = None
            try:
                if self.response_store is not None:
                    try:
                        response_session = self.response_store.begin(tenant, job['id'], token,
                                                                    retain_raw=getattr(self.execute,'retain_raw',False))
                    except ProductError as exc:
                        raise SourceFailure(exc.code,actual_cost='0') from None
                    except Exception:
                        raise SourceFailure('response_storage_unavailable',actual_cost='0') from None
                    context=replace(context,response=response_session)
                outcome = self.execute(context)
                if not isinstance(outcome, SourceResult):
                    raise ValueError('adapter must return a typed SourceResult')
                candidate_cost = amount(outcome.actual_cost)
                self._bill(claim, candidate_cost, outcome.billing_evidence)
                cost = candidate_cost
                if type(outcome.retry_after) is not int or not 0<=outcome.retry_after<=86400:
                    raise ValueError('bounded source cooldown required')
                delay=outcome.retry_after
                if delay:self._cooldown(delay)
                # Current rights are resolved again; never persist a revoked response.
                grant = self.grant_for(tenant, self.capability)
                self.service.complete(tenant, job['id'], token, outcome.facts, cost, grant,
                                      event_key=context.event_key, revision=outcome.revision, supersedes=outcome.supersedes)
                self._record(claim, cost=cost)
            except SourceFailure as exc:
                delay, cost = exc.retry_after, exc.actual_cost
                if cost is not None:
                    try:
                        self._bill(claim, cost, exc.billing_evidence)
                    except Exception:
                        self._record(claim, error='source_billing_unresolved', quarantine=True)
                        return True
                if delay:
                    # Persist before making the job terminal: another process
                    # must not slip through an expired permit/settlement gap.
                    self._cooldown(delay)
                if cost is None:
                    self._record(claim, error=exc.code, quarantine=True)
                else:
                    try:
                        self.service.settle(tenant, job['id'], token, 'failed', cost, error_code=exc.code)
                    except Exception:
                        self._record(claim, error='settlement_unresolved', cost=cost, quarantine=True)
                    else:
                        self._record(claim, error=exc.code, cost=cost)
            except ProductError as exc:
                # Keep billing unresolved, but retain known policy failures.
                # Never persist arbitrary adapter messages or error codes.
                code = exc.code if exc.code in {
                    'raw_retention_not_admitted', 'response_retention_not_admitted',
                    'source_not_admitted', 'admission_changed', 'expired',
                } else 'source_outcome_unresolved'
                self._record(claim, error=code, cost=cost, quarantine=True)
            except Exception:
                # Covers unknown transport outcome, invalid fact, revoked rights,
                # oversized bill, stale lease and persistence failure alike.
                self._record(claim, error='source_outcome_unresolved', cost=cost, quarantine=True)
            return True
        finally:
            try:
                if response_session is not None:
                    response_session.finish()
            finally:
                self._release(permit, delay)
