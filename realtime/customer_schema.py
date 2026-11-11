"""Discoverable contracts for live customer data and operator APIs."""
from __future__ import annotations


def extend_openapi(doc):
    schemas = doc['components'].setdefault('schemas', {})
    schemas.update({
        'Source': {'type': 'string', 'enum': ['google_web', 'google_news', 'google_trends', 'google_images']},
        'FieldValue': {'type': 'object', 'required': ['value', 'availability', 'basis', 'measurement', 'missing_reason'],
                       'properties': {'value': {'nullable': True}, 'availability': {'type': 'string', 'enum': ['available', 'unknown']},
                                      'basis': {'type': 'string', 'nullable': True}, 'measurement': {'type': 'string'},
                                      'missing_reason': {'type': 'string', 'nullable': True}}},
        'Occurrence': {'type': 'object', 'required': ['occurrence_id', 'request_id', 'url', 'source_fields'],
                       'properties': {'occurrence_id': {'type': 'string'}, 'request_id': {'type': 'string'},
                                      'url': {'type': 'object'}, 'source_fields': {'type': 'object'},
                                      'title': {'type': 'string', 'nullable': True}, 'snippet': {'type': 'string', 'nullable': True},
                                      'organic_position': {'type': 'integer', 'nullable': True}}},
        'DataRecord': {'type': 'object', 'required': ['request_id', 'source', 'occurrences', 'topics', 'source_response_hash'],
                       'properties': {'request_id': {'type': 'string'}, 'source': {'$ref': '#/components/schemas/Source'},
                                      'occurrences': {'type': 'array', 'items': {'$ref': '#/components/schemas/Occurrence'}},
                                      'topics': {'type': 'array', 'items': {'type': 'object'}},
                                      'field_adaptation': {'type': 'object', 'description': 'prd-v1 only. run/items/topics/features, availability and source basis.'},
                                      'source_response_hash': {'type': 'string'}, 'response_extensions': {'type': 'object'}}},
        'DataPage': {'type': 'object', 'required': ['requests', 'has_more', 'next_cursor'],
                     'properties': {'requests': {'type': 'array', 'items': {'$ref': '#/components/schemas/DataRecord'}},
                                    'has_more': {'type': 'boolean'}, 'next_cursor': {'type': 'string', 'nullable': True},
                                    'scope': {'type': 'string'}, 'field_contract': {'type': 'string'}}},
        'Error': {'type': 'object', 'properties': {'error': {'type': 'string'}, 'code': {'type': 'string'},
                                                 'request_id': {'type': 'string'}}},
    })

    def param(name, kind='string', **kwargs):
        return {'name': name, 'in': 'query', 'schema': {'type': kind, **kwargs}}

    scope = param('scope', enum=['own', 'public', 'accessible'], default='own')
    detail_scope = param('scope', enum=['own', 'public', 'accessible'], default='accessible')
    contract = param('field_contract', enum=['legacy', 'prd-v1'], default='prd-v1')
    filters = [scope, contract, param('source', enum=['google_web', 'google_news', 'google_trends', 'google_images']),
               *[param(k) for k in ('query', 'domain', 'from', 'to', 'classification', 'country', 'language', 'device')],
               param('page', 'integer', minimum=1, maximum=100),
               param('query_match', enum=['contains', 'exact'], default='contains'),
               param('new_only', enum=['true', 'false'], default='false')]

    def op(summary, *, method='get', params=(), schema=None, binary=False, description='', admin=False):
        response = {'description': '成功'}
        if binary:
            response['content'] = {m: {'schema': {'type': 'string', 'format': 'binary'}} for m in
                                   ('text/csv', 'application/x-ndjson', 'application/zip')}
            response['headers'] = {'X-SearchFlow-Truncated': {'schema': {'type': 'boolean'}, 'description': 'Snapshot export reached max_requests; narrow filters for a complete export.'}}
        else:
            response['content'] = {'application/json': {'schema': schema or {'type': 'object'}}}
        operation = {'summary': summary, 'description': description,
                     'tags': ['管理功能' if admin else '数据服务'],
                     'parameters': list(params), 'responses': {'200': response, **{
                         str(code): {'description': text, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}
                         for code, text in ((400, '参数无效'), (401, '凭据无效'), (404, '不存在或无权读取'),
                                            (409, '状态冲突'), (429, '限流或导出繁忙'), (503, '服务暂不可用'))}}}
        if admin:
            operation['security'] = [{'adminAuth': [], 'siteAccess': []}]
        return {method: operation}

    paths = doc['paths']
    paths['/v1/data/subscriptions'] = {**op('客户数据订阅列表'), **op('创建默认暂停的拉取订阅', method='post', description='name、filters、mode(occurrences/new_urls)、interval_seconds(60–604800)。Idempotency-Key 必填。只读取已有数据，不启动采集。start_mode=now排除创建快照已存记录，history补领历史；API默认history以保持兼容，网页默认now。')}
    paths['/v1/data/subscriptions']['post']['parameters'] = [{'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': {'type': 'string', 'minLength': 1, 'maxLength': 100}}]
    paths['/v1/data/subscriptions']['post']['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['name'], 'additionalProperties': False, 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'start_mode': {'type': 'string', 'enum': ['now', 'history'], 'default': 'history'},
        'filters': {'type': 'object', 'properties': {p['name']: p['schema'] for p in filters if p['name'] not in ('field_contract', 'new_only')}, 'additionalProperties': False},
        'mode': {'type': 'string', 'enum': ['occurrences', 'new_urls'], 'default': 'occurrences'},
        'interval_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 604800, 'default': 3600}}}}}}
    for action in ('pause', 'resume', 'pull'):
        paths['/v1/data/subscriptions/{id}/' + action] = op('订阅 ' + action, method='post', params=[param('limit', 'integer', minimum=1, maximum=50)] if action == 'pull' else [], description='POST 空对象。pull 保存待确认批次；重试返回相同批次。events 是交付项，requests 保留完整上下文。确认后推进；迟到入库记录仍会被发现。')
    paths['/v1/data/subscriptions/{id}/status'] = op('订阅进度与精确待处理请求数', description='按当前数据权限计数，含未确认批次；交付历史统计区分请求与事件。生成时间反映一次只读快照。')
    paths['/v1/data/subscriptions/{id}/batches'] = op('分页交付记录', params=[param('limit', 'integer', minimum=1, maximum=50, default=20), param('cursor')], description='轻量元数据，不载入完整历史payload；下载重检当前数据权限。')
    paths['/v1/data/subscriptions/{id}/batches/{batch_id}'] = op('重读历史或待确认批次与 SHA256')
    paths['/v1/data/subscriptions/{id}/batches/{batch_id}/export'] = op('完整批次下载', binary=True, params=[param('format', enum=['csv', 'jsonl', 'manifest'], default='jsonl')], description='manifest 包含两个导出文件的字节数与 SHA256。每个请求一行，包含完整 request JSON 与 events JSON；不是 74 列结果表。批次 SHA256 校验对象为 payload 的规范 JSON，文件 SHA256 另行计算。')
    paths['/v1/data/subscriptions/{id}/batches/{batch_id}/discard'] = op('丢弃待确认批次但不推进进度', method='post', description='POST 空对象；用于权限变更等阻塞恢复。重拉仍可能重复，event_id 不变。')
    paths['/v1/data/subscriptions/{id}/batches/{batch_id}/ack'] = op('校验并确认批次收货', method='post', description='POST {"sha256":"批次摘要"}；幂等确认。')
    paths['/v1/data/fields'] = op('字段目录、字段版本与数据范围')
    paths['/v1/data/requests'] = op('查询本人或公开的完整历史数据', params=filters + [param('limit', 'integer', minimum=1, maximum=50, default=20), param('cursor')],
                                           schema={'$ref': '#/components/schemas/DataPage'})
    paths['/v1/data/requests/{id}'] = op('完整请求详情', params=[detail_scope, contract])
    for section, title in [('items', '结果出现记录'), ('topics', '热点话题'), ('features', '富结果和子项')]:
        paths['/v1/data/requests/{id}/' + section] = op(title, params=[detail_scope, contract, param('limit', 'integer', minimum=1, maximum=500), param('offset', 'integer', minimum=0)])
    paths['/v1/data/requests/{id}/export'] = op('下载单请求完整 CSV/JSONL', binary=True,
                                                       params=[detail_scope, contract, param('format', enum=['csv', 'jsonl'])])
    paths['/v1/data/export'] = op('同一只读快照批量导出', binary=True,
                                 params=filters + [param('format', enum=['zip', 'csv', 'jsonl'], default='zip'),
                                                   param('max_requests', 'integer', minimum=1, maximum=10000, default=1000)],
                                 description='ZIP 包含完整 CSV、JSONL、出现记录及校验清单。超过请求上限明确标记截断；超过大小上限失败，不发布部分文件。每进程最多两个同步快照导出。')
    paths['/v1/data/requests/{id}/news-links'] = op('历史新闻独立补链证据', params=[detail_scope], description='保留包装链接、目标链接、状态、依据和解析时间；不改写原始请求与历史首次贡献。空列表表示没有补链证据。')
    paths['/v1/data/quality'] = op('总请求数与抽样字段完整率', params=[p for p in filters if p['name'] != 'field_contract'] + [param('sample_limit', 'integer', minimum=1, maximum=1000, default=100)],
                                  description='请求总量是全筛选统计；字段、链接去重与质量仅覆盖最新样本，不冒充历史首次贡献。')
    paths['/v1/data/delivery'] = op('公开历史 Whale 交付证明', description='缓存证明有过期状态；不发起平台上传或独立读回，不含客户私有交付。')
    paths['/v1/search/batch'] = op('原子批量创建搜索', method='post', description='1–20 个搜索；整批回滚或成功。每个任务仍受额度、并发和 15 分钟期限约束。')
    paths['/v1/search/batch']['post']['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
        'type': 'object', 'required': ['searches'], 'additionalProperties': False,
        'properties': {'searches': {'type': 'array', 'minItems': 1, 'maxItems': 20,
                                     'items': paths['/v1/search']['post']['requestBody']['content']['application/json']['schema']}}}}}}
    paths['/v1/search/batch']['post']['parameters'].append({'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': {'type': 'string', 'minLength': 1, 'maxLength': 100}})
    paths['/v1/search/batch']['post']['responses']['202'] = paths['/v1/search/batch']['post']['responses'].pop('200')
    paths['/v1/jobs']['get']['parameters'] = [param('limit', 'integer', minimum=1, maximum=100, default=30), *[param(k) for k in ('cursor', 'query', 'source', 'status')]]
    paths['/v1/jobs/{id}']['get']['parameters'].append(param('field_contract', enum=['legacy', 'prd-v1']))
    paths['/v1/jobs/{id}/export']['get']['parameters'] += [param('format', enum=['csv', 'jsonl'], default='jsonl'),
        param('profile', enum=['compact', 'full'], default='compact'), contract]
    paths['/v1/jobs/{id}/export']['get']['description'] = 'compact 保留旧 7/14 列；full 包含 request/result/topic/feature 行及无损 JSON。'
    for suffix, title in [('data', '客户自有任务的完整标准字段'), ('progress', '客户自有任务的等待、重试及缓存进度'),
                           ('pause', '暂停客户任务'), ('resume', '继续客户任务')]:
        paths['/v1/jobs/{id}/' + suffix] = op(title, method='post' if suffix in ('pause', 'resume') else 'get',
                                             params=[contract] if suffix == 'data' else [],
                                             description='暂停不延长原 15 分钟期限；在途结果仍可完成。' if suffix in ('pause', 'resume', 'progress') else '')
    for name, title in [('summary', '网站首页汇总'), ('overview', '网站数据总览与每日、域名、关键词统计'), ('rankings', '网站排名与竞品分析')]:
        params = [param('scope', enum=['public'], default='public'), param('days', enum=['1', '7', '30', '90']),
                  *[param(k) for k in ('region', 'language')], param('format', enum=['json', 'csv'])]
        params += ([param(k) for k in ('query', 'domain', 'competitors', 'scope_id')] if name == 'rankings' else
                   [param('source')] + ([param('keyword_q'), param('offset', 'integer', minimum=0)] if name == 'overview' else []))
        paths['/v1/analytics/' + name] = op(title, params=params, description='与网站相同的公开历史分析；不是客户私有任务或正式 Whale Reader 数据。')
    # Document already deployed legacy and operator operations as well, without
    # expanding their privileges or forwarding them through customer auth.
    legacy_contract = param('field_contract', enum=['legacy', 'prd-v1'], default='legacy')
    paths['/v1/google/legacy-data'] = op('兼容：公开历史数据', params=[p for p in filters if p['name'] not in ('scope', 'field_contract', 'query_match', 'new_only')] + [legacy_contract, param('limit', 'integer', maximum=50), param('cursor')])
    paths['/v1/google/legacy-data/requests/{id}'] = op('兼容：公开历史详情', params=[legacy_contract])
    paths['/v1/google/legacy-delivery'] = op('兼容：公开平台历史证明')
    for endpoint, title, methods in [
        ('/api/collector-settings', '全站采集设置', ['get', 'post']),
        ('/api/serp/tasks', '手动多关键词、组合来源采集任务', ['get', 'post']),
        ('/api/serp/results', '网站结果筛选', ['get']),
        ('/api/serp/browse-requests', '网站请求筛选', ['get']),
        ('/api/serp/stats', '网站运营统计', ['get']),
        ('/api/serp/export', '网站筛选结果导出', ['get']),
        ('/api/serp/tasks/{id}/progress', '手动任务详细进度', ['get']),
        ('/api/whale/status', '平台同步状态', ['get']),
        ('/api/whale/settings', '自动上传、平台领取设置', ['post']),
        ('/api/whale/jobs', '平台上传队列', ['get', 'post']),
        ('/api/whale/jobs/{id}', '平台上传任务详情', ['get']),
        ('/api/whale/jobs/{id}/receipts', '平台上传回执', ['get']),
        ('/api/product/admin/accounts', '分配客户空间', ['get', 'post']),
    ]:
        paths[endpoint] = {method: op(title, method=method, admin=True,
                                      description='已有工作台接口；管理写操作需管理密钥。保持原参数和权限，不创建新的数据来源授权。')[method] for method in methods}
    for prefix, actions in [('/api/serp/tasks/{id}/', ['start', 'pause', 'resume', 'stop']),
                            ('/api/whale/jobs/{id}/', ['pause', 'resume', 'stop', 'retry'])]:
        for action in actions:
            paths[prefix + action] = op('管理任务：' + action, method='post', admin=True)
    from .serp_console import REGIONS, NEWS_LOCALES
    source_array = {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                    'items': {'$ref': '#/components/schemas/Source'}}
    task_options = {'type': 'object', 'additionalProperties': False, 'properties': {
        'web_region': {'type': 'string', 'enum': list(REGIONS)},
        'web_language': {'type': 'string', 'enum': ['auto', 'en', 'zh']},
        'news_locale': {'type': 'string', 'enum': list(NEWS_LOCALES)},
        'trends_geo': {'type': 'string', 'enum': list(REGIONS)},
        'web_pages': {'type': 'integer', 'minimum': 1, 'maximum': 11},
        'retry_window_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 14400},
    }}
    task_body = {'type': 'object', 'additionalProperties': False, 'properties': {
        'query': {'type': 'string', 'maxLength': 300},
        'keywords': {'type': 'array', 'maxItems': 50, 'items': {'type': 'string', 'maxLength': 300}},
        'sources': source_array, 'options': task_options,
        'start': {'type': 'boolean', 'default': True}, 'upload_to_whale': {'type': 'boolean', 'default': False},
        'mode': {'type': 'string', 'enum': ['once', 'scheduled'], 'default': 'once'},
        'max_successful_searches': {'type': 'integer', 'minimum': 1},
    }}
    settings_body = {'type': 'object', 'minProperties': 1, 'additionalProperties': False, 'properties': {
        **{k: {'type': 'boolean'} for k in ('continuous_enabled', 'content_fetch_enabled')},
        'continuous_sources': source_array,
        'continuous_keywords': {'type': 'array', 'items': {'type': 'string'}},
        'web_region': task_options['properties']['web_region'], 'web_language': task_options['properties']['web_language'],
        'news_locales': {'type': 'array', 'items': {'type': 'string', 'enum': list(NEWS_LOCALES)}},
        'trends_geos': {'type': 'array', 'items': {'type': 'string', 'enum': list(REGIONS)}},
        'web_max_pages': {'type': 'integer', 'minimum': 1, 'maximum': 11},
        'news_interval_seconds': {'type': 'integer', 'minimum': 300, 'maximum': 86400},
        'trends_interval_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 86400},
    }}
    upload_body = {'type': 'object', 'additionalProperties': False, 'properties': {
        **{k: {'type': 'string'} for k in ('campaign_id', 'since', 'until', 'q')},
        'source': {'type': 'string', 'enum': ['google_web', 'google_news', 'google_trends']},
        'new_only': {'type': 'boolean'}, 'start': {'type': 'boolean', 'default': True},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
        'urls': {'type': 'array', 'minItems': 1, 'maxItems': 1000, 'items': {'type': 'string', 'format': 'uri'}},
    }}
    for path, schema in [('/api/serp/tasks', task_body), ('/api/collector-settings', settings_body),
                         ('/api/whale/jobs', upload_body),
                         ('/api/whale/settings', {'type': 'object', 'additionalProperties': False, 'properties': {
                             k: {'type': 'boolean'} for k in ('uploads_enabled', 'claims_enabled')}}),
                         ('/api/product/admin/accounts', {'type': 'object', 'required': ['name'], 'properties': {
                             'name': {'type': 'string'}, 'monthly_limit': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
                             'concurrency': {'type': 'integer', 'minimum': 1, 'maximum': 10}}})]:
        paths[path]['post']['requestBody'] = {'required': True, 'content': {'application/json': {'schema': schema}}}
        if path in ('/api/serp/tasks', '/api/whale/jobs', '/api/product/admin/accounts'):
            paths[path]['post']['responses']['201'] = paths[path]['post']['responses'].pop('200')
    paths['/api/serp/tasks']['get']['parameters'] = [param(k) for k in ('kind', 'status', 'q')] + [param('offset', 'integer'), param('limit', 'integer')]
    for path in ('/api/serp/results', '/api/serp/browse-requests', '/api/serp/export'):
        paths[path]['get']['parameters'] = [param(k) for k in ('source', 'campaign_id', 'status', 'since', 'until', 'q', 'new_only', 'count_total')] + [param('offset', 'integer'), param('limit', 'integer')]
    paths['/api/serp/export']['get']['parameters'] += [param('format', enum=['csv', 'jsonl']), param('mode', enum=['results', 'requests']), param('view', enum=['user'])]
    for path in ('/api/whale/jobs', '/api/whale/jobs/{id}/receipts'):
        paths[path]['get']['parameters'] = [param('offset', 'integer'), param('limit', 'integer')]
    for path, methods in paths.items():
        if '{id}' in path:
            for operation in methods.values():
                if not any(p.get('name') == 'id' and p.get('in') == 'path' for p in operation.get('parameters', [])):
                    operation.setdefault('parameters', []).append({'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'format': 'uuid'}})
    doc['info']['description'] += ' 新版 Google 研究目录另见 /v1/google/openapi.json，其登记接口不等于当前已有来源，先查 /v1/google/capabilities。'
    from .data_capability_schema import extend_openapi as extend_capabilities
    return extend_capabilities(doc)
