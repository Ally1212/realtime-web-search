"""Customer pull subscriptions: durable batches, explicit acknowledgements, no collection.

The acknowledged-request ledger deliberately has no timestamp high water mark:
late commits with older completed_at values remain eligible. One locked pending
batch per subscription makes parallel retries and acknowledgements deterministic.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from .customer_data import COLUMNS, project, selection, bounded_integer
from .product import ProductError, identifier
from .google_legacy import _cursor_encode, _cursor_decode

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_data_subscriptions (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id),
 name text NOT NULL, filters jsonb NOT NULL, mode text NOT NULL,
 interval_seconds integer NOT NULL, active boolean NOT NULL DEFAULT false,
 idempotency_key text NOT NULL, request_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), next_poll_at timestamptz,
 UNIQUE(account_id,idempotency_key)
);
ALTER TABLE sf_data_subscriptions ADD COLUMN IF NOT EXISTS start_mode text NOT NULL DEFAULT 'history';
CREATE TABLE IF NOT EXISTS sf_data_subscription_baselines (
 subscription_id uuid NOT NULL REFERENCES sf_data_subscriptions(id),
 request_id uuid NOT NULL, PRIMARY KEY(subscription_id,request_id)
);
CREATE TABLE IF NOT EXISTS sf_data_batches (
 id uuid PRIMARY KEY, subscription_id uuid NOT NULL REFERENCES sf_data_subscriptions(id),
 payload jsonb NOT NULL, checksum text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), acknowledged_at timestamptz, discarded_at timestamptz
);
ALTER TABLE sf_data_batches ADD COLUMN IF NOT EXISTS request_count integer;
ALTER TABLE sf_data_batches ADD COLUMN IF NOT EXISTS event_count integer;
ALTER TABLE sf_data_batches ADD COLUMN IF NOT EXISTS payload_bytes bigint;
UPDATE sf_data_batches SET request_count=(payload->>'request_count')::integer,
 event_count=(payload->>'event_count')::integer,payload_bytes=pg_column_size(payload)
 WHERE request_count IS NULL;
CREATE INDEX IF NOT EXISTS sf_data_batches_history ON sf_data_batches(subscription_id,created_at DESC,id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS sf_data_batches_pending
 ON sf_data_batches(subscription_id) WHERE acknowledged_at IS NULL AND discarded_at IS NULL;
CREATE TABLE IF NOT EXISTS sf_data_seen_requests (
 subscription_id uuid NOT NULL REFERENCES sf_data_subscriptions(id),
 request_id uuid NOT NULL, PRIMARY KEY(subscription_id,request_id)
);
CREATE TABLE IF NOT EXISTS sf_data_seen_urls (
 subscription_id uuid NOT NULL REFERENCES sf_data_subscriptions(id),
 url_id text NOT NULL, PRIMARY KEY(subscription_id,url_id)
);
CREATE INDEX IF NOT EXISTS sf_data_subscriptions_account ON sf_data_subscriptions(account_id,created_at);
"""
MAX_BYTES = 8 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class DataSubscriptions:
    def __init__(self, store, account_id):
        self.store, self.account_id = store, identifier(account_id)

    def _get(self, db, sid):
        row = db.execute('SELECT * FROM sf_data_subscriptions WHERE id=%s AND account_id=%s FOR UPDATE',
                         (identifier(sid), self.account_id)).fetchone()
        if not row:
            raise ProductError('not_found', '订阅不存在', 404)
        return row

    @staticmethod
    def public(row):
        return {k: row.get(k) for k in ('id', 'name', 'filters', 'mode', 'interval_seconds', 'active', 'created_at', 'next_poll_at', 'start_mode', 'last_acknowledged_at')}

    def create(self, body, key):
        if not isinstance(body, dict) or set(body) - {'name', 'filters', 'mode', 'interval_seconds', 'start_mode'}:
            raise ProductError('invalid_parameter', '订阅仅支持 name、filters、mode、interval_seconds、start_mode；创建默认暂停')
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ProductError('invalid_parameter', '需要 1–100 字符的 Idempotency-Key')
        name = body.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ProductError('invalid_parameter', 'name 必须为 1–200 字符')
        filters = body.get('filters', {})
        if not isinstance(filters, dict) or set(filters) - {'scope', 'source', 'query', 'query_match', 'country', 'language', 'classification', 'from', 'to', 'domain', 'device', 'page'}:
            raise ProductError('invalid_parameter', '不支持的订阅筛选')
        if any(not isinstance(v, str) for v in filters.values()):
            raise ProductError('invalid_parameter', '筛选值必须为字符串')
        selection(filters, self.account_id)
        mode = body.get('mode', 'occurrences')
        if mode not in ('occurrences', 'new_urls'):
            raise ProductError('invalid_parameter', 'mode 仅支持 occurrences、new_urls')
        interval = bounded_integer(body.get('interval_seconds'), 3600, 60, 604800, 'interval_seconds')
        if type(body.get('interval_seconds', 3600)) is not int:
            raise ProductError('invalid_parameter', 'interval_seconds 必须为整数')
        start_mode = body.get('start_mode', 'history')
        if start_mode not in ('history', 'now'):
            raise ProductError('invalid_parameter', 'start_mode 仅支持 history、now')
        normalized = dict(name=name.strip(), filters=filters, mode=mode, interval_seconds=interval)
        if start_mode != 'history':
            normalized['start_mode'] = start_mode
        digest = checksum(normalized)
        with self.store.base.connect() as db:
            db.execute("SET LOCAL statement_timeout='20s'")
            db.execute("SET LOCAL lock_timeout='5s'")
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))', (self.account_id,))
            previous = db.execute('SELECT * FROM sf_data_subscriptions WHERE account_id=%s AND idempotency_key=%s',
                                  (self.account_id, key)).fetchone()
            if previous:
                if previous['request_hash'] != digest:
                    raise ProductError('idempotency_conflict', '同一幂等键参数不同', 409)
                return self.public(previous)
            count = db.execute('SELECT count(*) n FROM sf_data_subscriptions WHERE account_id=%s', (self.account_id,)).fetchone()['n']
            if count >= 100:
                raise ProductError('subscription_limit', '每个客户最多 100 个订阅', 429)
            row = db.execute('INSERT INTO sf_data_subscriptions(id,account_id,name,filters,mode,interval_seconds,idempotency_key,request_hash) '
                             'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                             (str(uuid4()), self.account_id, normalized['name'], Jsonb(filters), mode, interval, key, digest)).fetchone()
            if start_mode == 'now':
                db.execute("UPDATE sf_data_subscriptions SET start_mode='now' WHERE id=%s", (row['id'],))
                where, args, *_ = selection(filters, self.account_id)
                db.execute('INSERT INTO sf_data_subscription_baselines(subscription_id,request_id) '
                           'SELECT %s,r.request_id FROM serp_requests r WHERE ' + ' AND '.join(where), [row['id']] + args)
                row['start_mode'] = 'now'
            return self.public(row)

    @staticmethod
    def _remaining(sub, where, args):
        for table in ('sf_data_seen_requests', 'sf_data_subscription_baselines'):
            where.append('NOT EXISTS (SELECT 1 FROM ' + table + ' s WHERE s.subscription_id=%s AND s.request_id=r.request_id)')
            args.append(sub['id'])

    def list(self):
        with self.store.base.connect() as db:
            rows = db.execute('SELECT s.*,(SELECT max(b.acknowledged_at) FROM sf_data_batches b WHERE b.subscription_id=s.id) last_acknowledged_at FROM sf_data_subscriptions s WHERE account_id=%s ORDER BY created_at DESC,id DESC',
                              (self.account_id,)).fetchall()
        return {'subscriptions': [self.public(r) for r in rows], 'delivery': 'pull_only_no_collection'}

    def action(self, sid, action):
        with self.store.base.connect() as db:
            self._get(db, sid)
            row = db.execute('UPDATE sf_data_subscriptions SET active=%s WHERE id=%s RETURNING *',
                             (action == 'resume', identifier(sid))).fetchone()
            return self.public(row)

    def _batch(self, db, sub, bid=None):
        args = [sub['id']]
        condition = 'id=%s' if bid else 'acknowledged_at IS NULL AND discarded_at IS NULL'
        if bid:
            args.append(identifier(bid))
        row = db.execute('SELECT * FROM sf_data_batches WHERE subscription_id=%s AND ' + condition, args).fetchone()
        if row:
            # Revoked ownership/public access must also revoke a frozen batch.
            where, values, *_ = selection(sub['filters'], self.account_id)
            ids = [r['request_id'] for r in row['payload']['requests']]
            if ids:
                visible = db.execute('SELECT count(*) n FROM serp_requests r WHERE r.request_id=ANY(%s::uuid[]) AND ' +
                                     ' AND '.join(where), [ids] + values).fetchone()['n']
                if visible != len(ids):
                    raise ProductError('batch_access_changed', '批次数据权限已变化，拒绝读取或确认', 409)
        return row

    @staticmethod
    def envelope(row):
        return dict(batch_id=str(row['id']), payload=row['payload'], sha256=row['checksum'],
                    checksum_encoding='UTF-8 JSON sort_keys=true ensure_ascii=false separators=comma:colon',
                    acknowledged_at=row['acknowledged_at'], discarded_at=row['discarded_at'])

    def batch(self, sid, *, limit=20, bid=None):
        limit = bounded_integer(limit, 20, 1, 50, 'limit')
        with self.store.base.connect() as db:
            db.execute("SET LOCAL statement_timeout='20s'")
            sub = self._get(db, sid)
            pending = self._batch(db, sub, bid)
            if pending:
                return self.envelope(pending)
            if bid:
                raise ProductError('not_found', '批次不存在', 404)
            if not sub['active']:
                raise ProductError('subscription_paused', '订阅已暂停，先显式继续', 409)
            if sub['next_poll_at'] and db.execute('SELECT now()<%s waiting', (sub['next_poll_at'],)).fetchone()['waiting']:
                return dict(batch_id=None, next_poll_at=sub['next_poll_at'], reason='interval_not_elapsed')
            where, args, *_ = selection(sub['filters'], self.account_id)
            self._remaining(sub, where, args)
            rows = db.execute('SELECT ' + COLUMNS + ' FROM serp_requests r WHERE ' + ' AND '.join(where) +
                              ' ORDER BY r.completed_at,r.request_id LIMIT %s', args + [limit + 1]).fetchall()
            projected = [project(row) for row in rows[:limit]]
            seen = set()
            if sub['mode'] == 'new_urls':
                ids = list({i['url']['url_id'] for record in projected for i in record['occurrences'] if i['url']['url_id']})
                if ids:
                    seen = {r['url_id'] for r in db.execute('SELECT url_id FROM sf_data_seen_urls WHERE subscription_id=%s AND url_id=ANY(%s::text[])', (sub['id'], ids)).fetchall()}
            records, events = [], []
            for record in projected:
                selected = []
                for item in record['occurrences']:
                    uid = item['url']['url_id']
                    if sub['mode'] == 'new_urls':
                        if not uid or uid in seen:
                            continue
                        seen.add(uid)
                    selected.append(dict(event_id=checksum([str(sub['id']), item['occurrence_id']]),
                                         request_id=record['request_id'], occurrence_id=item['occurrence_id'], url_id=uid))
                candidate = dict(requests=records + [record], events=events + selected)
                if len(canonical(candidate)) > MAX_BYTES - 4096:
                    if not records:
                        raise ProductError('request_too_large', '单请求超过订阅 8MiB 上限，请使用完整单请求导出', 413)
                    break
                records.append(record)
                events.extend(selected)
            if not records:
                row = db.execute("UPDATE sf_data_subscriptions SET next_poll_at=now()+interval_seconds*interval '1 second' WHERE id=%s RETURNING next_poll_at",
                                 (sub['id'],)).fetchone()
                return dict(batch_id=None, next_poll_at=row['next_poll_at'], reason='no_unacknowledged_requests')
            payload = dict(version='searchflow-incremental-1', subscription_id=str(sub['id']), mode=sub['mode'],
                           requests=records, events=events, request_count=len(records), event_count=len(events),
                           has_more=len(rows) > len(records),
                           semantics='events select occurrences; requests retain complete context, including duplicates. new_urls means first delivered URL within this subscription, not global first discovery.')
            row = db.execute('INSERT INTO sf_data_batches(id,subscription_id,payload,checksum) VALUES(%s,%s,%s,%s) RETURNING *',
                             (str(uuid4()), sub['id'], Jsonb(payload), checksum(payload))).fetchone()
            # JSONB can normalize numeric notation. Hash the persisted payload,
            # exactly as subsequent GET/retries will return it.
            raw = canonical(row['payload'])
            if len(raw) > MAX_BYTES:
                raise ProductError('request_too_large', '持久化批次超过 8MiB', 413)
            row['checksum'] = hashlib.sha256(raw).hexdigest()
            db.execute('UPDATE sf_data_batches SET checksum=%s,request_count=%s,event_count=%s,payload_bytes=%s WHERE id=%s',
                       (row['checksum'], payload['request_count'], payload['event_count'], len(raw), row['id']))
            return self.envelope(row)

    def status(self, sid):
        with self.store.base.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout='20s'")
            sub = db.execute('SELECT * FROM sf_data_subscriptions WHERE id=%s AND account_id=%s', (identifier(sid), self.account_id)).fetchone()
            if not sub:
                raise ProductError('not_found', '订阅不存在', 404)
            where, args, *_ = selection(sub['filters'], self.account_id)
            self._remaining(sub, where, args)
            remaining = db.execute('SELECT count(*) n FROM serp_requests r WHERE ' + ' AND '.join(where), args).fetchone()['n']
            summary = db.execute("SELECT count(*) batch_count,count(*) FILTER(WHERE acknowledged_at IS NOT NULL) acknowledged_batches,"
                                 "max(acknowledged_at) last_acknowledged_at,coalesce(sum(payload_bytes),0)::bigint stored_payload_bytes,"
                                 "coalesce(sum(request_count) FILTER(WHERE acknowledged_at IS NOT NULL),0)::bigint acknowledged_requests,"
                                 "coalesce(sum(event_count) FILTER(WHERE acknowledged_at IS NOT NULL),0)::bigint acknowledged_events "
                                 "FROM sf_data_batches WHERE subscription_id=%s", (sub['id'],)).fetchone()
            pending = db.execute('SELECT id,created_at,request_count,event_count FROM sf_data_batches WHERE subscription_id=%s AND acknowledged_at IS NULL AND discarded_at IS NULL', (sub['id'],)).fetchone()
            generated_at = db.execute('SELECT transaction_timestamp() at').fetchone()['at']
            return dict(subscription=self.public(sub), remaining_requests=remaining, pending_batch=pending,
                        delivery=dict(summary), generated_at=generated_at,
                        methodology='Exact currently accessible unacknowledged requests, including pending batches. Events/URLs are not counted as requests. Historical delivery counts do not change if source permissions change.')

    def history(self, sid, parameters):
        if set(parameters) - {'limit', 'cursor'}:
            raise ProductError('invalid_parameter', '交付记录只支持 limit、cursor')
        limit = bounded_integer(parameters.get('limit'), 20, 1, 50, 'limit')
        with self.store.base.connect() as db:
            sub = db.execute('SELECT id FROM sf_data_subscriptions WHERE id=%s AND account_id=%s', (identifier(sid), self.account_id)).fetchone()
            if not sub:
                raise ProductError('not_found', '订阅不存在', 404)
            condition, args = '', [sub['id']]
            if parameters.get('cursor'):
                date, bid = _cursor_decode(parameters['cursor'])
                condition = ' AND (created_at,id)<(%s,%s)'
                args += [date, bid]
            rows = db.execute('SELECT id,created_at,acknowledged_at,discarded_at,request_count,event_count,payload_bytes,checksum FROM sf_data_batches '
                              'WHERE subscription_id=%s' + condition + ' ORDER BY created_at DESC,id DESC LIMIT %s', args + [limit + 1]).fetchall()
            page = rows[:limit]
            cursor = _cursor_encode(dict(completed_at=page[-1]['created_at'], request_id=page[-1]['id'])) if len(rows) > limit else None
            return dict(batches=[dict(row, state='acknowledged' if row['acknowledged_at'] else 'discarded' if row['discarded_at'] else 'pending') for row in page],
                        has_more=len(rows) > limit, next_cursor=cursor, limit=limit)

    def discard(self, sid, bid):
        with self.store.base.connect() as db:
            sub = self._get(db, sid)
            row = db.execute('SELECT * FROM sf_data_batches WHERE id=%s AND subscription_id=%s', (identifier(bid), sub['id'])).fetchone()
            if not row:
                raise ProductError('not_found', '批次不存在', 404)
            if row['acknowledged_at']:
                raise ProductError('batch_already_acknowledged', '已确认批次不能丢弃', 409)
            db.execute('UPDATE sf_data_batches SET discarded_at=coalesce(discarded_at,now()) WHERE id=%s', (row['id'],))
            return dict(batch_id=str(row['id']), discarded=True, progress_advanced=False)

    def acknowledge(self, sid, bid, digest):
        with self.store.base.connect() as db:
            db.execute("SET LOCAL statement_timeout='20s'")
            sub = self._get(db, sid)
            batch = self._batch(db, sub, bid)
            if not batch:
                raise ProductError('not_found', '批次不存在', 404)
            if batch['discarded_at']:
                raise ProductError('batch_discarded', '批次已丢弃，不能确认', 409)
            if digest != batch['checksum']:
                raise ProductError('checksum_mismatch', '确认前请核验批次 SHA256', 409)
            if not batch['acknowledged_at']:
                rids = [record['request_id'] for record in batch['payload']['requests']]
                uids = list({event['url_id'] for event in batch['payload']['events'] if event['url_id']})
                if rids:
                    db.execute('INSERT INTO sf_data_seen_requests SELECT %s,unnest(%s::uuid[]) ON CONFLICT DO NOTHING', (sub['id'], rids))
                if uids:
                    db.execute('INSERT INTO sf_data_seen_urls SELECT %s,unnest(%s::text[]) ON CONFLICT DO NOTHING', (sub['id'], uids))
                db.execute('UPDATE sf_data_batches SET acknowledged_at=now() WHERE id=%s', (batch['id'],))
                db.execute("UPDATE sf_data_subscriptions SET next_poll_at=CASE WHEN %s THEN now() ELSE now()+interval_seconds*interval '1 second' END WHERE id=%s",
                           (batch['payload']['has_more'], sub['id']))
            return dict(batch_id=str(batch['id']), acknowledged=True)


def handle(handler, method, path, query, store, account):
    import re
    if not path.startswith('/v1/data/subscriptions'):
        return False
    from .customer_http import parameters
    from .product_http import body
    p = parameters(query)
    service = DataSubscriptions(store, account['id'])
    if path == '/v1/data/subscriptions':
        if p:
            raise ProductError('invalid_parameter', '列表与创建不接受查询参数')
        if method == 'GET':
            result = service.list()
        elif method == 'POST':
            result = service.create(body(handler), handler.headers.get('Idempotency-Key'))
        else:
            raise ProductError('method_not_allowed', '请使用 GET 或 POST', 405)
    elif m := re.fullmatch(r'/v1/data/subscriptions/([^/]+)/(status|batches)', path):
        if method != 'GET' or (m[2] == 'status' and p):
            raise ProductError('invalid_parameter', '进度与交付记录需要 GET；进度不接受参数')
        result = service.status(m[1]) if m[2] == 'status' else service.history(m[1], p)
    elif m := re.fullmatch(r'/v1/data/subscriptions/([^/]+)/(pause|resume|pull|batches/([^/]+)(/ack|/export|/discard)?)', path):
        sid, action, bid, suffix = m.groups()
        if action in ('pause', 'resume'):
            if method != 'POST' or p or body(handler):
                raise ProductError('invalid_parameter', '暂停/继续需要 POST 空对象')
            result = service.action(sid, action)
        elif action == 'pull':
            if method != 'POST' or set(p) - {'limit'} or body(handler):
                raise ProductError('invalid_parameter', '拉取需要 POST 空对象，仅支持 limit')
            result = service.batch(sid, limit=p.get('limit'))
        elif suffix == '/discard':
            if method != 'POST' or p or body(handler):
                raise ProductError('invalid_parameter', '丢弃需要 POST 空对象')
            result = service.discard(sid, bid)
        elif suffix == '/ack':
            if method != 'POST' or p:
                raise ProductError('invalid_parameter', '确认需要 POST')
            value = body(handler)
            if set(value) != {'sha256'}:
                raise ProductError('invalid_parameter', '确认只接受 sha256')
            result = service.acknowledge(sid, bid, value['sha256'])
        else:
            if method != 'GET' or set(p) - ({'format'} if suffix else set()):
                raise ProductError('invalid_parameter', '读取批次需要 GET')
            result = service.batch(sid, bid=bid)
            if suffix:
                export_batch(handler, result, p.get('format', 'jsonl'))
                return True
    else:
        raise ProductError('not_found', '订阅接口不存在', 404)
    handler.send_json(result)
    return True


def batch_file(envelope, fmt):
    """Each line/CSV row carries the complete request and its selected event IDs."""
    import csv
    import io
    payload = envelope['payload']
    rows, grouped = [], {}
    for event in payload['events']:
        grouped.setdefault(event['request_id'], []).append(event)
    for record in payload['requests']:
        rows.append(dict(batch_id=envelope['batch_id'], batch_sha256=envelope['sha256'],
                         request=record, events=grouped.get(record['request_id'], [])))
    if fmt == 'jsonl':
        content = b''.join(canonical(r) + b'\n' for r in rows)
        mime = 'application/x-ndjson'
    elif fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['batch_id', 'batch_sha256', 'request_id', 'record_json', 'events_json'])
        for row in rows:
            writer.writerow([row['batch_id'], row['batch_sha256'], row['request']['request_id'],
                             canonical(row['request']).decode(), canonical(row['events']).decode()])
        content, mime = ('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8'
    else:
        raise ProductError('invalid_parameter', '批次导出仅支持 jsonl、csv、manifest')
    return content, mime


def export_batch(handler, envelope, fmt):
    if fmt == 'manifest':
        files = {}
        for kind in ('jsonl', 'csv'):
            content, _ = batch_file(envelope, kind)
            files[kind] = dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        manifest = dict(version='searchflow-batch-manifest-1', batch_id=envelope['batch_id'],
                        batch_sha256=envelope['sha256'], request_count=envelope['payload']['request_count'],
                        event_count=envelope['payload']['event_count'], files=files,
                        checksum_encoding=envelope['checksum_encoding'])
        content, mime = canonical(manifest), 'application/json'
    else:
        content, mime = batch_file(envelope, fmt)
    handler.send_body(content, mime)
