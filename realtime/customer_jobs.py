"""Bounded customer task listing, with stable keyset pagination."""
from .customer_data import bounded_integer
from .google_legacy import _cursor_decode, _cursor_encode
from .product import ProductError


def list_jobs(store, aid, parameters):
    p = dict(parameters)
    if set(p) - {'limit', 'cursor', 'source', 'status', 'query'}:
        raise ProductError('invalid_parameter', '不支持的任务筛选参数')
    limit = bounded_integer(p.get('limit'), 30, 1, 100, 'limit')
    clauses, args = ['account_id=%s'], [aid]
    if p.get('source'):
        if p['source'] not in ('google_web', 'google_news', 'google_trends', 'google_images'):
            raise ProductError('invalid_parameter', '无效来源')
        clauses.append("parameters->>'source'=%s"); args.append(p['source'])
    if p.get('status'):
        if p['status'] not in ('queued', 'running', 'succeeded', 'failed', 'cancelled'):
            raise ProductError('invalid_parameter', '无效状态')
        clauses.append('status=%s'); args.append(p['status'])
    if p.get('query'):
        if len(p['query']) > 300:
            raise ProductError('invalid_parameter', '关键词过长')
        clauses.append("parameters->>'query' ILIKE %s")
        args.append('%' + p['query'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    if p.get('cursor'):
        date, jid = _cursor_decode(p['cursor'])
        clauses.append('(created_at,id)<(%s,%s)'); args.extend([date, jid])
    with store.base.connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='10s'")
        rows = db.execute('SELECT id,monitor_id,parameters,status,error_code,created_at,completed_at,charged '
                          'FROM sf_jobs WHERE ' + ' AND '.join(clauses) +
                          ' ORDER BY created_at DESC,id DESC LIMIT %s', args + [limit + 1]).fetchall()
    visible = rows[:limit]
    cursor = _cursor_encode(dict(completed_at=visible[-1]['created_at'], request_id=visible[-1]['id'])) if len(rows) > limit else None
    return dict(jobs=visible, limit=limit, has_more=len(rows) > limit, next_cursor=cursor)
