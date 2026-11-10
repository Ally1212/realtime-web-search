"""Authenticated API equivalents of existing data workbench functions."""
from __future__ import annotations

import re

from .customer_data import CustomerDataStore, bounded_integer, field_catalog
from .product import ProductError


def parameters(query):
    if any(len(value) != 1 for value in query.values()):
        raise ProductError('invalid_parameter', '不接受重复查询参数')
    return {key: value[0] for key, value in query.items()}


def handle(handler, method, path, query, store, account):
    if not path.startswith(('/v1/data/', '/v1/analytics/')):
        return False
    from .customer_subscriptions import handle as subscription_handle
    if subscription_handle(handler, method, path, query, store, account):
        return True
    if method != 'GET':
        raise ProductError('method_not_allowed', '数据接口仅支持 GET', 405)
    p = parameters(query)
    data = CustomerDataStore(store, account['id'])
    if path == '/v1/data/fields':
        if p:
            raise ProductError('invalid_parameter', '字段目录不接受参数')
        result = field_catalog()
    elif path == '/v1/data/requests':
        result = data.requests(p)
    elif match := re.fullmatch(r'/v1/data/requests/([^/]+)(?:/(items|topics|features|export|news-links))?', path):
        rid, section = match.groups()
        if section == 'news-links':
            record = data.request(rid, p)
            with store.base.connect() as db:
                rows = db.execute('SELECT occurrence_id,wrapper,evidence,observed_at,attempts,next_retry_at FROM sf_news_link_enrichments WHERE request_id=%s ORDER BY occurrence_id', (record['request_id'],)).fetchall()
            handler.send_json(dict(request_id=record['request_id'], enrichments=rows, original_record_unchanged=True))
            return True
        if section == 'export':
            from .customer_export import export_record
            fmt = p.pop('format', 'jsonl')
            record = data.request(rid, p)
            export_record(handler, record, fmt)
            return True
        result = data.section(rid, section, p) if section else dict(request=data.request(rid, p))
    elif path == '/v1/data/quality':
        result = data.quality(p)
    elif path == '/v1/data/export':
        from .customer_export import snapshot_download
        snapshot_download(handler, store, account['id'], p)
        return True
    elif path == '/v1/data/delivery':
        from .google_delivery import read_delivery_status
        if p:
            raise ProductError('invalid_parameter', '平台历史证明不接受筛选')
        result = read_delivery_status(store.base.dsn)
    elif path.startswith('/v1/analytics/'):
        from .analytics import overview_summary
        scope = p.pop('scope', 'public')
        if scope != 'public':
            raise ProductError('unsupported_scope', '此接口与网站分析一致，仅分析公开历史；私有数据请使用 /v1/data/quality 或监测报告')
        fmt = p.pop('format', 'json')
        allowed = {'days', 'source', 'region', 'language'}
        if path == '/v1/analytics/summary':
            call = lambda: overview_summary(store.base, **p)
        elif path == '/v1/analytics/overview':
            allowed |= {'keyword_q', 'offset'}
            call = lambda: store.analytics_overview(**p)
        elif path == '/v1/analytics/rankings':
            allowed = {'query', 'domain', 'region', 'language', 'days', 'competitors', 'scope_id'}
            values = dict(p)
            if 'scope_id' in values:
                values['scope'] = values.pop('scope_id')
            call = lambda: store.history_analysis(**values)
        else:
            raise ProductError('not_found', '接口不存在', 404)
        if set(p) - allowed or fmt not in ('json', 'csv'):
            raise ProductError('invalid_parameter', '分析筛选或格式无效')
        result = call()
        if fmt == 'csv':
            # Preserve arbitrary nested reports without dropping tables.
            import csv
            import io
            import json
            from .google_domain import canonical_json
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['section', 'ordinal', 'record_json'])
            for section, value in result.items():
                for index, row in enumerate(value if isinstance(value, list) else [value], 1):
                    writer.writerow([section, index, canonical_json(json.loads(json.dumps(row, default=str)))])
            handler.send_body(('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8')
            return True
    else:
        raise ProductError('not_found', '接口不存在', 404)
    handler.send_json(result)
    return True
