"""Owned pull delivery of immutable observations; no source calls or cache events.

Each batch carries one complete observation. The durable ID ledger, not a time
watermark, preserves late commits. Acknowledgement is the only advancing action.
"""
from __future__ import annotations

from datetime import timedelta
from threading import BoundedSemaphore
from uuid import uuid4
from psycopg.types.json import Jsonb

from .data_capability_contract import CAPABILITIES
from .data_capability_jobs import digest, job_id, required_text
from .product import ProductError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_subscriptions (
 id uuid PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL, filters jsonb NOT NULL,
 start_mode text NOT NULL, interval_seconds integer NOT NULL, active boolean NOT NULL DEFAULT false,
 idempotency_key text NOT NULL, request_hash text NOT NULL, created_at timestamptz NOT NULL,
 next_poll_at timestamptz, UNIQUE(tenant_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS sf_capability_subscription_seen (
 subscription_id uuid NOT NULL REFERENCES sf_capability_subscriptions(id),
 observation_id uuid NOT NULL, baseline boolean NOT NULL,
 PRIMARY KEY(subscription_id,observation_id)
);
CREATE TABLE IF NOT EXISTS sf_capability_batches (
 id uuid PRIMARY KEY, subscription_id uuid NOT NULL REFERENCES sf_capability_subscriptions(id),
 payload jsonb NOT NULL, checksum text NOT NULL, created_at timestamptz NOT NULL,
 acknowledged_at timestamptz, discarded_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS sf_capability_batches_pending ON sf_capability_batches(subscription_id)
 WHERE acknowledged_at IS NULL AND discarded_at IS NULL;
CREATE INDEX IF NOT EXISTS sf_capability_batches_history ON sf_capability_batches(subscription_id,created_at,id);
CREATE INDEX IF NOT EXISTS sf_capability_subscriptions_owner ON sf_capability_subscriptions(tenant_id,created_at,id);
"""
from .data_capability_subscription_retention import SCHEMA as ERASURE_SCHEMA, assert_live
SCHEMA += ERASURE_SCHEMA
VERSION = 'searchflow-observation-batch-1'


class CapabilitySubscriptions:
    def __init__(self, runtime, tenant):
        self.runtime, self.service, self.tenant = runtime, runtime.service, str(tenant)

    def owns(self, sid):
        with self.service.connect() as db:
            return bool(db.execute('SELECT 1 FROM sf_capability_subscriptions WHERE id=%s AND tenant_id=%s',
                                   (job_id(sid), self.tenant)).fetchone())

    def _get(self, db, sid):
        self.runtime.ensure_recovered()
        row = db.execute('SELECT * FROM sf_capability_subscriptions WHERE id=%s AND tenant_id=%s FOR UPDATE',
                         (job_id(sid), self.tenant)).fetchone()
        if not row:
            raise ProductError('not_found', '订阅不存在', 404)
        assert_live(db,self.tenant,row['id'])
        return row

    @staticmethod
    def public(row):
        return dict(domain='data', mode='observations', **{k: str(row[k]) if k == 'id' else row[k]
                    for k in ('id', 'name', 'filters', 'start_mode', 'interval_seconds', 'active', 'created_at', 'next_poll_at')})

    def _selection(self, sub):
        where, args = self.runtime._selection(self.tenant, sub['filters'])
        where.append('NOT EXISTS (SELECT 1 FROM sf_capability_subscription_seen s WHERE s.subscription_id=%s AND s.observation_id=o.id)')
        args.append(sub['id'])
        return ' AND '.join(where), args

    def create(self, body, key):
        self.runtime.ensure_recovered()
        if not isinstance(body, dict) or set(body) - {'domain', 'name', 'filters', 'mode', 'start_mode', 'interval_seconds'}:
            raise ProductError('invalid_parameter', '无效订阅参数')
        if body.get('domain') != 'data' or body.get('mode', 'observations') != 'observations':
            raise ProductError('invalid_parameter', '新域使用domain=data和mode=observations')
        name = required_text(body.get('name'), 'name', 200)
        required_text(key, 'Idempotency-Key', 100)
        filters = body.get('filters', {})
        if not isinstance(filters, dict) or set(filters) - {'capability', 'provider', 'country', 'language', 'from', 'to'}:
            raise ProductError('invalid_parameter', '无效订阅筛选')
        f, _ = self.runtime.filters(filters)
        if f.get('capability') not in CAPABILITIES:
            raise ProductError('invalid_parameter', '订阅必须明确capability')
        start = body.get('start_mode', 'history')
        interval = body.get('interval_seconds', 3600)
        if start not in ('history', 'now') or type(interval) is not int or not 60 <= interval <= 604800:
            raise ProductError('invalid_parameter', '无效起点或间隔')
        normalized = dict(name=name, filters=f, start_mode=start, interval_seconds=interval)
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock_shared(20261110,30)')
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261112))', (self.tenant,))
            if db.execute('SELECT 1 FROM sf_capability_subscription_erasures WHERE tenant_id=%s AND idempotency_digest=%s',(self.tenant,digest(key))).fetchone():
                raise ProductError('expired','原幂等键对应订阅已清除',410)
            previous = db.execute('SELECT * FROM sf_capability_subscriptions WHERE tenant_id=%s AND idempotency_key=%s', (self.tenant, key)).fetchone()
            if previous:
                assert_live(db,self.tenant,previous['id'])
                if previous['request_hash'] != digest(normalized):
                    raise ProductError('idempotency_conflict', '同一幂等键参数不同', 409)
                return self.public(previous)
            if db.execute('SELECT count(*) n FROM sf_capability_subscriptions s WHERE tenant_id=%s AND NOT EXISTS (SELECT 1 FROM sf_capability_subscription_erasures e WHERE e.subscription_id=s.id)', (self.tenant,)).fetchone()['n'] >= 100:
                raise ProductError('subscription_limit', '每租户新域订阅最多100个', 429)
            row = db.execute('INSERT INTO sf_capability_subscriptions(id,tenant_id,name,filters,start_mode,interval_seconds,idempotency_key,request_hash,created_at) '
                             'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                             (str(uuid4()), self.tenant, name, Jsonb(f), start, interval, key, digest(normalized), self.service.now())).fetchone()
            if start == 'now':
                # Snapshot ALL matching owned observations, even currently expired or
                # revoked ones: later rights restoration cannot turn them into new data.
                clauses, args = ['o.tenant_id=%s'], [self.tenant]
                for k, v in f.items():
                    if k in ('capability', 'provider'):
                        clauses.append('o.' + k + '=%s'); args.append(v)
                    elif k in ('country', 'language'):
                        clauses.append("o.parameters->>'" + k + "'=%s"); args.append(v)
                    else:
                        clauses.append('o.created_at' + ('>=' if k == 'from' else '<') + '%s'); args.append(v)
                db.execute('INSERT INTO sf_capability_subscription_seen(subscription_id,observation_id,baseline) '
                           'SELECT %s,o.id,true FROM sf_capability_observations o WHERE ' + ' AND '.join(clauses), [row['id']] + args)
            return self.public(row)

    def list(self):
        self.runtime.ensure_recovered()
        with self.service.connect() as db:
            rows = db.execute('SELECT * FROM sf_capability_subscriptions s WHERE tenant_id=%s AND NOT EXISTS (SELECT 1 FROM sf_capability_subscription_erasures e WHERE e.subscription_id=s.id) ORDER BY created_at DESC,id DESC', (self.tenant,)).fetchall()
        return dict(domain='data', subscriptions=[self.public(r) for r in rows], delivery='pull_only_no_collection', batch_observation_limit=1)

    def action(self, sid, action):
        with self.service.connect() as db:
            self._get(db, sid)
            row = db.execute('UPDATE sf_capability_subscriptions SET active=%s WHERE id=%s RETURNING *', (action == 'resume', job_id(sid))).fetchone()
            return self.public(row)

    def _envelope(self, db, batch):
        payload = batch['payload']
        if digest(payload) != batch['checksum']:
            raise ProductError('corrupt_snapshot', '批次摘要不一致', 503)
        cap = payload['capability']
        row = self.service._observation(db, self.tenant, payload['observation_id'], self.runtime.grant(self.tenant, cap))
        observation = self.service._public_observation(row)
        if observation['snapshot_hash'] != payload['snapshot_hash']:
            raise ProductError('version_conflict', '批次固定版本不一致', 409)
        return dict(batch_id=str(batch['id']), sha256=batch['checksum'], payload=payload, observation=observation,
                    checksum_encoding='sha256-canonical-json-utf8-of-payload', acknowledged_at=batch['acknowledged_at'],
                    discarded_at=batch['discarded_at'], created_at=batch['created_at'])

    def batch(self, sid, bid=None):
        with self.service.connect() as db:
            sub = self._get(db, sid)
            if bid:
                row = db.execute('SELECT * FROM sf_capability_batches WHERE id=%s AND subscription_id=%s', (job_id(bid), sub['id'])).fetchone()
                if not row:
                    raise ProductError('not_found', '批次不存在', 404)
            else:
                row = db.execute('SELECT * FROM sf_capability_batches WHERE subscription_id=%s AND acknowledged_at IS NULL AND discarded_at IS NULL', (sub['id'],)).fetchone()
            # Frozen pending batches remain redeliverable after pause or before
            # the interval; rights/retention are always rechecked first.
            if row:
                return self._envelope(db, row)
            if not sub['active']:
                return dict(batch=None, reason='paused')
            if sub['next_poll_at'] and sub['next_poll_at'] > self.service.now():
                return dict(batch=None, reason='not_due', next_poll_at=sub['next_poll_at'])
            where, args = self._selection(sub)
            row = db.execute('SELECT o.* FROM sf_capability_observations o WHERE ' + where + ' ORDER BY o.created_at,o.id LIMIT 1', args).fetchone()
            db.execute('UPDATE sf_capability_subscriptions SET next_poll_at=%s WHERE id=%s', (self.service.now() + timedelta(seconds=sub['interval_seconds']), sub['id']))
            if not row:
                return dict(batch=None, reason='no_eligible_observations')
            grant = self.runtime.grant(self.tenant, row['capability'])
            self.service._authorize(row, grant, self.service.now())
            self.service._check_snapshot(row)
            self.service._authorize_raw(row['payload'], grant)
            snapshot = self.service._public_observation(row)
            payload = dict(schema_version=VERSION, observation_id=str(row['id']), event_id=str(row['id']),
                           event_type='revision' if row['supersedes'] else 'new_observation',
                           supersedes=str(row['supersedes']) if row['supersedes'] else None,
                           capability=row['capability'], payload_hash=row['payload_hash'], snapshot_hash=snapshot['snapshot_hash'])
            batch = db.execute('INSERT INTO sf_capability_batches(id,subscription_id,payload,checksum,created_at) VALUES(%s,%s,%s,%s,%s) RETURNING *',
                               (str(uuid4()), sub['id'], Jsonb(payload), digest(payload), self.service.now())).fetchone()
            return self._envelope(db, batch)

    def acknowledge(self, sid, bid, checksum):
        with self.service.connect() as db:
            sub = self._get(db, sid)
            batch = db.execute('SELECT * FROM sf_capability_batches WHERE subscription_id=%s AND id=%s FOR UPDATE', (sub['id'], job_id(bid))).fetchone()
            if not batch:
                raise ProductError('not_found', '批次不存在', 404)
            envelope = self._envelope(db, batch)
            if checksum != batch['checksum']:
                raise ProductError('checksum_mismatch', '批次摘要不匹配', 409)
            if batch['discarded_at']:
                raise ProductError('batch_discarded', '已丢弃批次不能确认', 409)
            if not batch['acknowledged_at']:
                db.execute('INSERT INTO sf_capability_subscription_seen(subscription_id,observation_id,baseline) VALUES(%s,%s,false) ON CONFLICT DO NOTHING', (sub['id'], batch['payload']['observation_id']))
                db.execute('UPDATE sf_capability_batches SET acknowledged_at=%s WHERE id=%s', (self.service.now(), batch['id']))
                # Permit immediate drain of accumulated data, not an artificial
                # observation/hour bottleneck. Empty polls still honor interval.
                db.execute('UPDATE sf_capability_subscriptions SET next_poll_at=NULL WHERE id=%s', (sub['id'],))
                envelope['acknowledged_at'] = self.service.now()
            return envelope

    def discard(self, sid, bid):
        with self.service.connect() as db:
            sub = self._get(db, sid)
            batch = db.execute('SELECT * FROM sf_capability_batches WHERE subscription_id=%s AND id=%s FOR UPDATE', (sub['id'], job_id(bid))).fetchone()
            if not batch:
                raise ProductError('not_found', '批次不存在', 404)
            if batch['acknowledged_at']:
                raise ProductError('batch_acknowledged', '已确认批次不能丢弃', 409)
            db.execute('UPDATE sf_capability_batches SET discarded_at=COALESCE(discarded_at,%s) WHERE id=%s', (self.service.now(), batch['id']))
            db.execute('UPDATE sf_capability_subscriptions SET next_poll_at=NULL WHERE id=%s', (sub['id'],))
            return dict(batch_id=str(batch['id']), discarded=True, progress_advanced=False)

    def status(self, sid):
        with self.service.connect() as db:
            sub = self._get(db, sid)
            where, args = self._selection(sub)
            n = 0
            # Provider admission may contain executable parameter restrictions
            # that cannot be represented in SQL. Check every eligible metadata
            # row before reporting an exact count; fail closed on policy change.
            with db.cursor(name='capability_subscription_count') as cursor:
                cursor.execute('SELECT o.parameters,o.provider,o.license_id,o.admission,o.expires_at FROM sf_capability_observations o WHERE ' + where, args)
                for row in cursor:
                    self.service._authorize(row, self.runtime.grant(self.tenant, sub['filters']['capability']), self.service.now())
                    n += 1
            return dict(subscription=self.public(sub), pending_observations=n, generated_at=self.service.now(),
                        includes_unacknowledged=True, source_calls=0)

    def history(self, sid, p):
        from .customer_data import bounded_integer
        if set(p) - {'limit', 'cursor'}:
            raise ProductError('invalid_parameter', '不支持的批次参数')
        limit = bounded_integer(p.get('limit'), 20, 1, 50, 'limit')
        with self.service.connect() as db:
            sub = self._get(db, sid)
            clauses, rights_args = self.runtime._selection(self.tenant, sub['filters'])
            args, where = [sub['id']] + rights_args, 'b.subscription_id=%s AND ' + ' AND '.join(clauses)
            route = 'capability-batches:' + str(sub['id'])
            if p.get('cursor'):
                key = self.runtime._key(p['cursor'], self.tenant, route, {}, 2)
                where += ' AND (b.created_at,b.id)<(%s,%s)'; args += key
            rows = db.execute("SELECT b.id,b.created_at,b.acknowledged_at,b.discarded_at,o.parameters,o.provider,o.license_id,o.admission,o.expires_at FROM sf_capability_batches b JOIN sf_capability_observations o ON o.id=(b.payload->>'observation_id')::uuid WHERE " + where + ' ORDER BY b.created_at DESC,b.id DESC LIMIT %s', args + [limit + 1]).fetchall()
            for row in rows:
                self.service._authorize(row, self.runtime.grant(self.tenant, sub['filters']['capability']), self.service.now())
            visible = [{k: row[k] for k in ('id', 'created_at', 'acknowledged_at', 'discarded_at')} for row in rows[:limit]]
            for r in visible:
                r['id'] = str(r['id'])
            cursor = self.runtime.cursors.encode(self.tenant, route, {}, [visible[-1]['created_at'].isoformat(), visible[-1]['id']]) if len(rows) > limit else None
            return dict(batches=visible, has_more=len(rows) > limit, next_cursor=cursor)


def _batch_files(envelope):
    """Complete event+observation in every format, with byte-level manifest.

    CSV has one envelope JSON cell: deliberately not a scalar metric table.
    ZIP also embeds the existing independently verifiable observation bundle.
    """
    import csv
    import io
    import json
    import zipfile
    from .data_capability_export import build_bundle, sha
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str).encode()
    # Acknowledgement/discard timestamps are mutable delivery state, excluded
    # from immutable exported bytes so historical downloads stay identical.
    fixed = {k: envelope[k] for k in ('batch_id', 'sha256', 'payload', 'observation', 'checksum_encoding', 'created_at')}
    raw = canonical(fixed)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['batch_id', 'batch_sha256', 'event_id', 'event_type', 'envelope_json'])
    writer.writerow([fixed['batch_id'], fixed['sha256'], fixed['payload']['event_id'], fixed['payload']['event_type'], raw.decode()])
    files = {'batch.json': raw, 'batch.jsonl': raw + b'\n', 'batch.csv': ('\ufeff' + output.getvalue()).encode(),
             'observation.zip': build_bundle(envelope['observation'])['files']['bundle.zip']}
    manifest = dict(schema_version=VERSION, batch_id=fixed['batch_id'], batch_sha256=fixed['sha256'],
                    payload_hash=fixed['payload']['payload_hash'], snapshot_hash=fixed['payload']['snapshot_hash'],
                    observation_count=1, event_count=1, files={name: dict(bytes=len(value), sha256=sha(value)) for name, value in files.items()})
    files['manifest.json'] = canonical(manifest)
    if sum(map(len, files.values())) > 256 * 1024 * 1024:
        raise ProductError('export_too_large', '完整批次导出超过资源上限；不会截断或推进', 413)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, value)
    files['batch.zip'] = output.getvalue()
    return files


_EXPORTS = BoundedSemaphore(2)


def batch_files(envelope):
    if not _EXPORTS.acquire(blocking=False):
        raise ProductError('export_busy', '批次导出繁忙，请稍后重试', 429)
    try:
        return _batch_files(envelope)
    finally:
        _EXPORTS.release()


def dispatch(handler, method, path, p, account, payload=None):
    """Extend the existing subscription entry point; preserve legacy defaults."""
    import re
    from .data_capability_http import runtime_for, MIMES
    from .product_http import body
    runtime = runtime_for(handler)
    root = path == '/v1/data/subscriptions'
    new = root and ((method == 'GET' and p.get('domain') == 'data') or
                    (method == 'POST' and isinstance(payload, dict) and payload.get('domain') == 'data'))
    match = re.fullmatch(r'/v1/data/subscriptions/([^/]+)/(status|batches|pause|resume|pull|batches/([^/]+)(?:/(ack|discard|export))?)', path)
    service = CapabilitySubscriptions(runtime, account['id']) if runtime else None
    if match and service:
        new = service.owns(match[1])
    if not new:
        return False
    if not runtime:
        raise ProductError('data_capabilities_disabled', '新域订阅尚未配置', 503)
    runtime.ensure_recovered()
    if root:
        if method == 'GET':
            if set(p) != {'domain'}:
                raise ProductError('invalid_parameter', '新域列表只支持domain=data')
            result = service.list()
        else:
            if p:
                raise ProductError('invalid_parameter', '创建不接受查询参数')
            result = service.create(payload, handler.headers.get('Idempotency-Key'))
    else:
        sid, action, bid, suffix = match.groups()
        if action in ('status', 'batches'):
            if method != 'GET' or (action == 'status' and p):
                raise ProductError('invalid_parameter', '需要GET及合法参数')
            result = service.status(sid) if action == 'status' else service.history(sid, p)
        elif action in ('pause', 'resume', 'pull') or suffix in ('ack', 'discard'):
            if method != 'POST' or p:
                raise ProductError('invalid_parameter', '操作需要POST；新域每批完整观测，不接受limit')
            value = body(handler, reject_duplicates=True)
            if suffix == 'ack':
                if not isinstance(value, dict) or set(value) != {'sha256'}:
                    raise ProductError('invalid_parameter', '确认仅接受sha256')
                result = service.acknowledge(sid, bid, value['sha256'])
            else:
                if value != {}:
                    raise ProductError('invalid_parameter', '操作需要空对象')
                result = service.discard(sid, bid) if suffix == 'discard' else service.batch(sid) if action == 'pull' else service.action(sid, action)
        else:
            if method != 'GET' or set(p) - ({'format'} if suffix == 'export' else set()):
                raise ProductError('invalid_parameter', '批次需要GET及合法参数')
            result = service.batch(sid, bid)
            if suffix == 'export':
                fmt = p.get('format', 'jsonl')
                if fmt not in MIMES:
                    raise ProductError('invalid_parameter', '无效导出格式')
                files = batch_files(result)
                current = service.batch(sid, bid)
                if current['sha256'] != result['sha256']:
                    raise ProductError('version_conflict', '导出生成期间批次版本改变', 409)
                handler.send_body(files[{'json': 'batch.json', 'jsonl': 'batch.jsonl', 'csv': 'batch.csv', 'zip': 'batch.zip', 'manifest': 'manifest.json'}[fmt]], MIMES[fmt])
                return True
    handler.send_json(result)
    return True


def verify_batch_bundle(content, *, expected_batch_id, expected_batch_sha256):
    """Verify delivery against an independently received authorized batch proof."""
    import csv
    import io
    import json
    import zipfile
    import zlib
    from .data_capability_export import sha, verify_bundle
    try:
        if len(content) > 256 * 1024 * 1024:
            raise ValueError()
        required = {'batch.json', 'batch.jsonl', 'batch.csv', 'observation.zip', 'manifest.json'}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if (len(infos) != len(required) or {i.filename for i in infos} != required
                    or sum(i.file_size for i in infos) > 256 * 1024 * 1024):
                raise ValueError()
            files = {name: archive.read(name) for name in required}
        manifest = json.loads(files['manifest.json'])
        fixed = json.loads(files['batch.json'])
        payload, observation = fixed['payload'], fixed['observation']
        if (manifest['schema_version'] != VERSION or payload['schema_version'] != VERSION
                or manifest['batch_id'] != expected_batch_id or fixed['batch_id'] != expected_batch_id
                or manifest['batch_sha256'] != expected_batch_sha256 or fixed['sha256'] != expected_batch_sha256
                or digest(payload) != expected_batch_sha256 or manifest['event_count'] != 1 or manifest['observation_count'] != 1
                or payload['observation_id'] != observation['observation_id'] or payload['event_id'] != observation['observation_id']
                or payload['capability'] != observation['capability'] or payload['supersedes'] != observation['supersedes']
                or payload['event_type'] != ('revision' if observation['supersedes'] else 'new_observation')
                or payload['payload_hash'] != observation['payload_hash'] or payload['snapshot_hash'] != observation['snapshot_hash']
                or manifest['payload_hash'] != payload['payload_hash'] or manifest['snapshot_hash'] != payload['snapshot_hash']
                or set(manifest['files']) != required - {'manifest.json'}):
            raise ValueError()
        for name, proof in manifest['files'].items():
            if proof != dict(bytes=len(files[name]), sha256=sha(files[name])):
                raise ValueError()
        if files['batch.jsonl'] != files['batch.json'] + b'\n':
            raise ValueError()
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(['batch_id', 'batch_sha256', 'event_id', 'event_type', 'envelope_json'])
        writer.writerow([fixed['batch_id'], fixed['sha256'], payload['event_id'], payload['event_type'], files['batch.json'].decode()])
        if files['batch.csv'] != ('\ufeff' + output.getvalue()).encode():
            raise ValueError()
        verify_bundle(files['observation.zip'], expected_payload_hash=payload['payload_hash'],
                      expected_snapshot_hash=payload['snapshot_hash'], expected_observation_id=payload['observation_id'])
        # The outer JSON must contain exactly the independently verified inner
        # observation, not a different body with copied digest fields.
        with zipfile.ZipFile(io.BytesIO(files['observation.zip'])) as inner:
            if json.loads(inner.read('observation.json')) != observation:
                raise ValueError()
        return manifest
    except (ValueError, KeyError, TypeError, UnicodeError, zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error):
        raise ProductError('corrupt_bundle', '批次文件、事件、观测及独立摘要校验失败') from None
