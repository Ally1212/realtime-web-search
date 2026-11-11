"""Authenticated, bounded queries for new domains; no implicit source calls."""
from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import hmac
import json
from uuid import UUID

from .customer_data import bounded_integer
from .data_capability_contract import CAPABILITIES, admit_request, validate_request
from .data_capability_facts import timestamp
from .data_capability_jobs import TERMINAL, amount, digest, job_id, required_text
from .data_capability_store import CapabilityData
from .product import ProductError

VERSION = 'searchflow-capability-api-1'
ROOTS = {'series': ('trends_history',), 'volume': ('keyword_volume',), 'answer': ('aio_answer', 'ai_mode_answer')}
KINDS = {'point': 'series', 'month': 'volume', 'block': 'answer', 'citation': 'answer', 'link': 'answer'}
STATUS = ('queued', 'running', 'succeeded', 'partial', 'failed', 'canceled', 'reconciliation_required')


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def unb64(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def object_id(oid, kind, source_id):
    return 'sfobj1.' + b64(canonical([str(oid), kind, source_id]))


def decode_object(value, kind):
    try:
        if not isinstance(value, str) or not value.startswith('sfobj1.') or len(value) > 8192:
            raise ValueError()
        v = json.loads(unb64(value[7:]))
        if (not isinstance(v, list) or len(v) != 3 or v[1] != kind
                or not isinstance(v[2], str) or not v[2] or len(v[2]) > 1024):
            raise ValueError()
        return str(UUID(v[0])), v[2]
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise ProductError('invalid_parameter', '对象ID格式或数据类型无效') from None


class Cursors:
    def __init__(self, secret, clock):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError('cursor secret must contain at least 32 private bytes')
        self.secret, self.clock = secret, clock

    def encode(self, tenant, route, filters, key):
        payload = dict(v=1, tenant=str(tenant), route=route, filters=filters, key=key,
                       expires_at=int(self.clock().timestamp()) + 3600)
        text = b64(canonical(payload))
        return text + '.' + hmac.new(self.secret, text.encode(), hashlib.sha256).hexdigest()

    def decode(self, token, tenant, route, filters):
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise ValueError()
            text, signature = token.split('.')
            if not hmac.compare_digest(hmac.new(self.secret, text.encode(), hashlib.sha256).hexdigest(), signature):
                raise ValueError()
            v = json.loads(unb64(text))
            if (set(v) != {'v', 'tenant', 'route', 'filters', 'key', 'expires_at'} or v['v'] != 1
                    or v['tenant'] != str(tenant) or v['route'] != route or v['filters'] != filters
                    or type(v['expires_at']) is not int or not self.clock().timestamp() < v['expires_at'] <= self.clock().timestamp() + 3600):
                raise ValueError()
            if not isinstance(v['key'], list):
                raise ValueError()
            return v['key']
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ProductError('invalid_cursor', '游标无效、过期或与租户/筛选/排序不符') from None


def submission(body, key):
    if not isinstance(body, dict) or 'budget' not in body:
        raise ProductError('invalid_parameter', '需要显式budget以及能力参数')
    budget = body['budget']
    if not isinstance(budget, dict) or set(budget) != {'maximum_cost', 'currency'}:
        raise ProductError('invalid_parameter', 'budget仅支持maximum_cost和currency')
    parameters = validate_request({k: v for k, v in body.items() if k != 'budget'})
    required_text(key, 'Idempotency-Key', 120)
    ceiling = amount(budget['maximum_cost'])
    import re
    if not isinstance(budget['currency'], str) or not re.fullmatch('[A-Z]{3}', budget['currency']):
        raise ProductError('invalid_parameter', 'currency必须为三个大写字母')
    return parameters, ceiling, budget['currency']


class CapabilityRuntime:
    """Trusted opt-in mount, never assembled from customer body or auto-enabled.

    worker_ready must inspect a supervised live executor for tenant/capability/
    provider. Its default rejects all submissions. Grants are reloaded for each
    access. Construction does not initialize tables, start workers or call a
    source; the operator must initialize the candidate service explicitly.
    """
    def __init__(self, service: CapabilityData, grant_for, *, cursor_secret, worker_ready=None, queue_timeout_seconds=300, response_store=None, access_ready=None):
        if not callable(grant_for) or worker_ready is not None and not callable(worker_ready):
            raise ValueError('trusted grant/worker resolver required')
        if type(queue_timeout_seconds) is not int or not 1 <= queue_timeout_seconds <= 3600:
            raise ValueError('queue timeout must be 1–3600 seconds')
        if access_ready is not None and not callable(access_ready):
            raise ValueError('trusted access admission callback required')
        self.access_ready=access_ready
        self.service, self.grant_for = service, grant_for
        self.worker_ready = worker_ready or (lambda tenant, capability, provider: False)
        self.cursors = Cursors(cursor_secret, service.now)
        self.queue_timeout = queue_timeout_seconds
        if response_store is not None and response_store.service is not service:
            raise ValueError('response store must share the runtime service')
        self.response_store = response_store

    def ensure_recovered(self):
        self.service.require_recovered()
        if self.access_ready is not None:
            try:ready=self.access_ready()
            except Exception:ready=False
            if not ready:raise ProductError('deployment_not_running','新数据域装配尚未运行，未开放访问',503)

    def response(self, tenant, jid):
        self.job(tenant,jid)
        if self.response_store is None:
            raise ProductError('response_spool_disabled','此实例尚未配置独立来源响应保存',503)
        return self.response_store.get(str(tenant),jid)

    def grant(self, tenant, capability):
        return self.grant_for(str(tenant), capability)

    def maintain(self, tenant):
        """Poll-time local recovery, scoped to this tenant; never retries a call."""
        self.ensure_recovered()
        now = self.service.now()
        with self.service.connect() as db:
            rows = db.execute("UPDATE sf_capability_jobs SET status='failed',actual_cost=0,error_code='queue_timeout',completed_at=%s "
                              "WHERE tenant_id=%s AND status='queued' AND created_at<=%s RETURNING *",
                              (now, str(tenant), now - timedelta(seconds=self.queue_timeout))).fetchall()
            rows += db.execute("UPDATE sf_capability_jobs SET status='reconciliation_required',error_code='lease_expired' "
                               "WHERE tenant_id=%s AND status='running' AND lease_expires_at<=%s RETURNING *",
                               (str(tenant), now)).fetchall()
            for row in rows:
                self.service.event(db, row, now)
        if self.response_store is not None:
            self.response_store.seal_abandoned(tenant=str(tenant))

    def preview(self, tenant, payload):
        self.ensure_recovered()
        p = validate_request(payload)
        now = self.service.now()
        grant = self.grant(tenant, p['capability'])
        admitted = admit_request(p, grant, grant.get('maximum_cost') if grant else 0,
                                 grant.get('currency') if grant else '', now=now)
        ceiling = amount(admitted['maximum_cost'])
        with self.service.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            budget = db.execute('SELECT * FROM sf_capability_budgets WHERE tenant_id=%s AND currency=%s',
                                (str(tenant), admitted['currency'])).fetchone()
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            used = db.execute('SELECT COALESCE(sum(COALESCE(actual_cost,reserved)) FILTER (WHERE created_at>=%s),0) daily_used,'
                              'COALESCE(sum(COALESCE(actual_cost,reserved)),0) monthly_used FROM sf_capability_jobs '
                              'WHERE tenant_id=%s AND currency=%s AND created_at>=%s',
                              (day, str(tenant), admitted['currency'], day.replace(day=1))).fetchone()
        result = dict(schema_version=VERSION, parameters=p, provider=admitted['provider'], license_id=admitted['license_id'],
                      allowed_use=grant['allowed_use'], retention_days=grant['retention_days'],
                      maximum_cost=str(ceiling), currency=admitted['currency'], billing_unit=admitted['billing_unit'],
                      source_retry_policy=grant.get('source_retry_policy'),
                      cost_basis='approved_job_cost_ceiling_not_actual_bill', reservation_created=False,
                      legacy_search_quota_debited=False,
                      executor_available=self.worker_ready(str(tenant), p['capability'], admitted['provider']) is True,
                      estimated_at=now.isoformat(), expires_at=min(now + timedelta(seconds=60), timestamp(grant['expires_at'])).isoformat(),
                      budget_status='configured' if budget else 'not_configured',
                      budget_snapshot=None, submission_rechecks_admission_budget_and_executor=True)
        if budget:
            result['budget_snapshot'] = dict(daily_limit=str(budget['daily_limit']), monthly_limit=str(budget['monthly_limit']),
                daily_remaining=str(max(0, budget['daily_limit'] - used['daily_used'])),
                monthly_remaining=str(max(0, budget['monthly_limit'] - used['monthly_used'])), currency=admitted['currency'],
                window_basis='UTC_job_creation_date;not_provider_invoice_date')
        result['preview_hash'] = digest(result)
        return result

    def submit(self, tenant, body, key):
        parameters, ceiling, currency = submission(body, key)
        self.maintain(tenant)
        with self.service.connect() as db:
            previous = db.execute('SELECT * FROM sf_capability_jobs WHERE tenant_id=%s AND idempotency_key=%s',
                                  (str(tenant), key)).fetchone()
        grant = self.grant(tenant, parameters['capability'])
        if previous:
            # A replay authorizes the frozen job, not a new source quote. Its
            # budget/parameter conflict is still checked by create atomically.
            self._authorize_job(tenant, previous)
        else:
            admitted = admit_request(parameters, grant, ceiling, currency, now=self.service.now())
            if self.worker_ready(str(tenant), parameters['capability'], admitted['provider']) is not True:
                raise ProductError('executor_unavailable', '此来源暂无可用执行器，未受理或预留费用', 503)
        job = self.service.create(str(tenant), parameters, key, grant, ceiling, currency)
        return dict(**job, domain='data', schema_version=VERSION, status_url='/v1/jobs/' + job['id'],
                    data_url='/v1/jobs/' + job['id'] + '/data', queue_timeout_seconds=self.queue_timeout)

    def _authorize_job(self, tenant, job):
        self.ensure_recovered()
        from .data_capability_job_retention import assert_live
        with self.service.connect() as db:
            assert_live(db,str(tenant),job['id'])
        grant = self.grant(tenant, job['capability'])
        admitted = admit_request(job['parameters'], grant, grant.get('maximum_cost') if grant else 0,
                                 grant.get('currency') if grant else '', now=self.service.now())
        if (admitted['provider'] != job['provider'] or admitted['license_id'] != job['license_id']
                or any(grant[k] != job['admission'][k] for k in ('allowed_use', 'retention_days', 'billing_unit', 'currency'))):
            raise ProductError('admission_changed', '任务的来源准入已变化', 409)
        return grant

    def job(self, tenant, jid):
        self.maintain(tenant)
        with self.service.connect() as db:
            job = self.service._get(db, str(tenant), jid)
        self._authorize_job(tenant, job)
        return dict(**self.service.public(job), domain='data', schema_version=VERSION)

    def cancel(self, tenant, jid):
        # Revoking source read rights must not prevent an authenticated owner
        # from stopping queued/in-flight spending. No source facts are returned.
        self.maintain(tenant)
        from .data_capability_job_retention import assert_live
        with self.service.connect() as db:
            self.service._get(db,str(tenant),jid)
            assert_live(db,str(tenant),jid)
        result=self.service.cancel(str(tenant), jid)
        with self.service.connect() as db:
            assert_live(db,str(tenant),jid)
        return result

    def job_data(self, tenant, jid):
        job = self.job(tenant, jid)
        return self.service.job_data(str(tenant), jid, self.grant(tenant, job['capability']))

    def progress(self, tenant, jid):
        job = self.job(tenant, jid)
        charges=[];attempts=[]
        with self.service.connect() as db:
            # Runtime can be mounted before the optional executor is installed.
            installed=db.execute("SELECT count(*) n FROM pg_attribute WHERE attrelid=to_regclass('sf_capability_dispatches') "
                                 "AND attname IN ('billing_parts','call_attempts') AND NOT attisdropped").fetchone()
            if installed['n']==2:
                row=db.execute('SELECT billing_parts,call_attempts FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()
                if row:
                    charges=row['billing_parts'];confirmed={p['phase'] for p in charges}
                    attempts=[dict(p,bill_status='confirmed' if p['phase'] in confirmed else 'unconfirmed') for p in row['call_attempts']]
        return dict(id=job['id'], domain='data', status=job['status'], cancel_requested=job['cancel_requested'],
                    progress_basis='task_terminal_not_fabricated_source_percentage',
                    completed=job['status'] in TERMINAL, schema_version=VERSION,
                    result_ref=job['result_ref'], error_code=job['error_code'],
                    reservation_status=job['reservation_status'], actual_cost=job['actual_cost'],
                    confirmed_call_charges=charges,
                    source_retry_policy=job.get('source_retry_policy'),
                    source_call_attempts=attempts,
                    confirmed_call_cost=str(sum((amount(p['actual_cost']) for p in charges),amount('0'))) if charges else None,
                    call_charge_basis='confirmed_call_receipts_not_final_settlement' if charges else 'not_reported',
                    platform_delivery='not_submitted')

    def observation(self, tenant, oid):
        self.ensure_recovered()
        with self.service.connect() as db:
            row = db.execute('SELECT capability FROM sf_capability_observations WHERE tenant_id=%s AND id=%s',
                             (str(tenant), job_id(oid))).fetchone()
        if not row:
            raise ProductError('not_found', '观测不存在', 404)
        return self.service.observation(str(tenant), oid, self.grant(tenant, row['capability']))

    @staticmethod
    def filters(p, *, jobs=False, capabilities=CAPABILITIES):
        if not isinstance(p, dict) or any(not isinstance(v, str) for v in p.values()):
            raise ProductError('invalid_parameter', '查询参数必须为字符串')
        allowed = {'limit', 'cursor', 'capability', 'provider', 'country', 'language', 'from', 'to'} | ({'status'} if jobs else set())
        if set(p) - allowed:
            raise ProductError('invalid_parameter', '不支持的筛选参数')
        limit = bounded_integer(p.get('limit'), 20, 1, 50, 'limit')
        f = {k: v for k, v in p.items() if k not in ('limit', 'cursor')}
        if 'capability' in f and f['capability'] not in capabilities:
            raise ProductError('invalid_parameter', '能力与数据类型不符')
        if 'status' in f and f['status'] not in STATUS:
            raise ProductError('invalid_parameter', '无效任务状态')
        if 'country' in f:
            import re
            if not re.fullmatch('[A-Za-z]{2}', f['country']):
                raise ProductError('invalid_parameter', 'country必须为两位地区代码')
            f['country'] = f['country'].upper()
        if 'language' in f:
            import re
            if not re.fullmatch('[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', f['language']):
                raise ProductError('invalid_parameter', 'language格式无效')
        if 'provider' in f:
            required_text(f['provider'], 'provider')
        for name in ('from', 'to'):
            if name in f:
                f[name] = timestamp(f[name]).isoformat()
        if 'from' in f and 'to' in f and timestamp(f['from']) >= timestamp(f['to']):
            raise ProductError('invalid_parameter', 'from必须早于to')
        return f, limit

    def _selection(self, tenant, f, *, jobs=False, capabilities=CAPABILITIES):
        self.ensure_recovered()
        clauses, args = ['o.tenant_id=%s'], [str(tenant)]
        if jobs:
            clauses.append('NOT EXISTS(SELECT 1 FROM sf_capability_job_erasures e WHERE e.job_id=o.id)')
        if not jobs:
            clauses.append('NOT EXISTS(SELECT 1 FROM sf_capability_fact_erasures e WHERE e.observation_id=o.id)')
            clauses.append('o.expires_at>%s'); args.append(self.service.now())
        clauses.append('o.capability=ANY(%s)'); args.append(list(capabilities))
        for key in ('capability', 'provider', 'status'):
            if key in f:
                clauses.append('o.' + key + '=%s'); args.append(f[key])
        for key in ('country', 'language'):
            if key in f:
                clauses.append("o.parameters->>'" + key + "'=%s"); args.append(f[key])
        for key, operator in (('from', '>='), ('to', '<')):
            if key in f:
                clauses.append('o.created_at' + operator + '%s'); args.append(timestamp(f[key]))
        # Rights are checked again on each returned record. SQL also excludes
        # capabilities with no current approval so page metadata cannot reveal
        # their counts or timing through cursors.
        permitted = []
        for capability in capabilities:
            grant = self.grant(tenant, capability)
            if grant and grant.get('status') == 'approved':
                try:
                    names = ('provider', 'license_id', 'allowed_use', 'retention_days', 'billing_unit', 'currency')
                    if timestamp(grant['expires_at']) <= self.service.now() or any(grant.get(k) is None for k in names):
                        continue
                    permitted.append("(o.capability=%s AND o.provider=%s AND o.license_id=%s AND "
                                     "o.admission->>'allowed_use'=%s AND o.admission->>'retention_days'=%s AND "
                                     "o.admission->>'billing_unit'=%s AND o.admission->>'currency'=%s)")
                    args.extend([capability, grant['provider'], grant['license_id'], grant['allowed_use'],
                                 str(grant['retention_days']), grant['billing_unit'], grant['currency']])
                except (KeyError, ProductError):
                    pass
        clauses.append('(' + ' OR '.join(permitted) + ')' if permitted else 'false')
        return clauses, args

    def _key(self, token, tenant, route, f, dimensions):
        key = self.cursors.decode(token, tenant, route, f)
        try:
            if len(key) != dimensions:
                raise ValueError()
            stamp, oid = timestamp(key[0]), str(UUID(key[1]))
            if dimensions == 3 and (type(key[2]) is not int or key[2] < 0):
                raise ValueError()
            return [stamp, oid] + key[2:]
        except (ValueError, TypeError, ProductError, AttributeError):
            raise ProductError('invalid_cursor', '游标排序键无效') from None

    def jobs(self, tenant, p):
        self.maintain(tenant)
        f, limit = self.filters(p, jobs=True)
        clauses, args = self._selection(tenant, f, jobs=True)
        route = 'jobs:created_desc,id_desc'
        if p.get('cursor'):
            key = self._key(p['cursor'], tenant, route, f, 2)
            clauses.append('(o.created_at,o.id)<(%s,%s)'); args.extend(key)
        with self.service.connect() as db:
            result = db.execute('SELECT o.* FROM sf_capability_jobs o WHERE ' + ' AND '.join(clauses) +
                                ' ORDER BY o.created_at DESC,o.id DESC LIMIT %s', args + [limit + 1]).fetchall()
        visible = result[:limit]
        for row in visible:
            self._authorize_job(tenant, row)
        next_cursor = self.cursors.encode(tenant, route, f, [visible[-1]['created_at'].isoformat(), str(visible[-1]['id'])]) if len(result) > limit else None
        return dict(schema_version=VERSION, domain='data', jobs=[dict(**self.service.public(r), domain='data') for r in visible],
                    has_more=len(result) > limit, next_cursor=next_cursor, limit=limit, filters=f,
                    sort='created_at_desc,id_desc')

    def observations(self, tenant, p, *, kind=None):
        capabilities = ROOTS[kind] if kind else CAPABILITIES
        f, limit = self.filters(p, capabilities=capabilities)
        clauses, args = self._selection(tenant, f, capabilities=capabilities)
        route = 'observations:created_desc,id_desc' if kind is None else kind + ':created_desc,id_desc,position_asc'
        root_join = ''
        if kind:
            field = {'series': 'series', 'volume': 'groups', 'answer': 'answer'}[kind]
            target = "o.payload->'data'->'" + field + "'"
            collection = ("CASE WHEN jsonb_typeof(" + target + ")='object' THEN jsonb_build_array(" + target + ") ELSE '[]'::jsonb END") if kind == 'answer' else target
            root_join = (' JOIN LATERAL (SELECT v.value->>\'id\' source_id,(v.ordinality-1)::integer position '
                         'FROM jsonb_array_elements(' + collection + ') WITH ORDINALITY v(value,ordinality)) r ON true')
        if p.get('cursor'):
            key = self._key(p['cursor'], tenant, route, f, 3 if kind else 2)
            clauses.append('(o.created_at,o.id,-r.position)<(%s,%s,-%s)' if kind else '(o.created_at,o.id)<(%s,%s)')
            args.extend(key)
        with self.service.connect() as db:
            query = ('SELECT o.id,o.created_at' + (',r.source_id,r.position' if kind else '') +
                     ' FROM sf_capability_observations o' + root_join +
                     ' WHERE ' + ' AND '.join(clauses) + ' ORDER BY o.created_at DESC,o.id DESC' +
                     (',r.position ASC' if kind else '') + ' LIMIT %s')
            result = db.execute(query, args + [limit + 1]).fetchall()
        visible = result[:limit]
        output = []
        for row in visible:
            snapshot = self.observation(tenant, row['id'])
            if kind:
                output.append(self._typed(snapshot, kind, row['source_id'], full=False))
            else:
                output.append({k: v for k, v in snapshot.items() if k != 'payload'})
        cursor_key = [visible[-1]['created_at'].isoformat(), str(visible[-1]['id'])] + ([visible[-1]['position']] if kind else []) if visible else None
        next_cursor = self.cursors.encode(tenant, route, f, cursor_key) if len(result) > limit else None
        return dict(schema_version=VERSION, records=output, has_more=len(result) > limit, next_cursor=next_cursor,
                    limit=limit, filters=f, sort=route.split(':')[1], scope='own')

    @staticmethod
    def _typed(snapshot, kind, source_id, *, full):
        data = snapshot['payload']['data']
        candidates = data['series'] if kind == 'series' else data['groups'] if kind == 'volume' else [data['answer']]
        record = next((v for v in candidates if v['id'] == source_id), None)
        if record is None:
            raise ProductError('not_found', '记录不存在', 404)
        record = dict(record)
        if not full:
            for field in ('points', 'monthly', 'blocks', 'citations', 'links'):
                record.pop(field, None)
        oid = object_id(snapshot['observation_id'], kind, source_id)
        return dict(object_id=oid, observation_id=snapshot['observation_id'], payload_hash=snapshot['payload_hash'],
                    snapshot_hash=snapshot['snapshot_hash'], capability=snapshot['capability'], record=record,
                    includes_children=full, schema_version=VERSION)

    def typed(self, tenant, value, kind):
        oid, source_id = decode_object(value, kind)
        snapshot = self.observation(tenant, oid)
        if snapshot['capability'] not in ROOTS[kind]:
            raise ProductError('not_found', '记录不存在', 404)
        return self._typed(snapshot, kind, source_id, full=True)

    def children(self, tenant, value, kind, p):
        parent_kind = KINDS[kind]
        oid, source_id = decode_object(value, parent_kind)
        if set(p) - {'cursor', 'limit'}:
            raise ProductError('invalid_parameter', '子记录仅支持limit和cursor')
        limit = bounded_integer(p.get('limit'), 100, 1, 500, 'limit')
        snapshot = self.observation(tenant, oid)
        if snapshot['capability'] not in ROOTS[parent_kind]:
            raise ProductError('not_found', '记录不存在', 404)
        self._typed(snapshot, parent_kind, source_id, full=False)
        binding = dict(object_id=value, payload_hash=snapshot['payload_hash'], sort='position_asc')
        route = 'children:' + kind
        after = -1
        if p.get('cursor'):
            key = self.cursors.decode(p['cursor'], tenant, route, binding)
            if len(key) != 1 or type(key[0]) is not int or key[0] < 0:
                raise ProductError('invalid_cursor', '子记录游标无效')
            after = key[0]
        page = self.service.records(str(tenant), oid, kind, self.grant(tenant, snapshot['capability']),
                                    parent_id=source_id, offset=after + 1, limit=limit)
        return dict(schema_version=VERSION, object_id=value, observation_id=oid, payload_hash=snapshot['payload_hash'],
                    records=page['records'], limit=limit, has_more=page['has_more'], sort='position_asc',
                    next_cursor=self.cursors.encode(tenant, route, binding, [after + len(page['records'])]) if page['has_more'] else None)
