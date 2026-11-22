"""External Maps API contract. Keep documented fields tied to delivered snapshots."""
from copy import deepcopy
from .maps_provider import CITIES


def extend_openapi(document):
    schemas = document['components'].setdefault('schemas', {})
    text = {'type': 'string', 'nullable': True}
    place = {
        'data_id': {'type': 'string', 'description': '来源商家标识；不是 Google Place ID。'},
        'place_id': {**text, 'description': '当前来源不提供，返回 null。'},
        'name': {'type': 'string'}, 'formatted_address': text,
        'categories': {'type': 'array', 'items': {'type': 'string'}}, 'primary_type': text,
        'rating': {'type': 'number', 'minimum': 0, 'maximum': 5, 'nullable': True},
        'user_rating_count': {'type': 'integer', 'minimum': 0, 'nullable': True},
        'location': {'type': 'object', 'required': ['latitude', 'longitude'], 'properties': {
            'latitude': {'type': 'number', 'minimum': -90, 'maximum': 90},
            'longitude': {'type': 'number', 'minimum': -180, 'maximum': 180}}},
        'distance_m': {'type': 'number', 'minimum': 0},
        'national_phone_number': text, 'website_uri': {**text, 'format': 'uri'},
        'regular_opening_hours': {'type': 'object', 'nullable': True, 'additionalProperties': True},
        'business_status': text,
        'google_maps_uri': {'type': 'string', 'format': 'uri', 'description': '由名称和坐标生成的地图查找链接。'},
        'source_fields': {'type': 'object', 'additionalProperties': True, 'description': '完整来源字段；未知扩展不丢弃。'},
    }
    schemas['MapsPlace'] = {'type': 'object', 'required': list(place), 'properties': deepcopy(place)}
    paths = document['paths']
    base = '/v1/google/local/jobs'
    request = paths[base]['post']['requestBody']['content']['application/json']['schema']
    request['description'] = ('text 模式支持带城市的文字查询，例如 coffee in Seattle，无需坐标；'
        '也可提供city或location。nearby模式需要included_type及预设城市或坐标。'
        '文字城市自动定位，默认半径5000米；结果保留定位信息。max_places限制实际商家数。')
    request['properties']['location']['additionalProperties'] = False
    request['properties']['city']['description'] = 'text模式支持城市名称自动定位；nearby预设城市：'+', '.join(CITIES)
    request['properties']['max_places'] = {'type':'integer','minimum':1,'maximum':100,'description':'最多交付商家数；22–100使用浏览器深度采集，实际数量取决于地图当前结果。'}
    request['allOf'] = [{'oneOf': [
        {'required': ['query'], 'properties': {'mode': {'enum': ['text']},
            'query': {'type': 'string', 'minLength': 1}}},
        {'required': ['mode', 'included_type'], 'properties': {'mode': {'enum': ['nearby']},
            'included_type': {'type': 'string', 'minLength': 1}}, 'anyOf': [
                {'required':['location']}, {'required':['city'],'properties':{'city':{'enum':list(CITIES)}}}]}]}]
    request['example'] = {'mode':'text','query':'coffee in Seattle','language':'en','max_places':2}
    schemas['MapsRequest'] = deepcopy(request)
    paths[base]['post']['description'] = ('创建异步查询。每次最多100家，按距离排序并返回覆盖证明；不保证完整覆盖。'
        '同一空间、同一幂等键与相同参数返回原任务，不重复采集或扣次；不同参数返回409。'
        '成功扣1次（含有效零结果），失败不扣次。按 poll_after_seconds 轮询，完成后读取 results 或 export。')
    for path, operations in paths.items():
        if not path.startswith('/v1/google/local/'):
            continue
        for operation in operations.values():
            operation['tags'] = ['Google Maps 数据']
            if '{id}' in path:
                path_parameters = operation.setdefault('parameters', [])
                if not any(item.get('in') == 'path' and item.get('name') == 'id' for item in path_parameters):
                    path_parameters.append({'in': 'path', 'name': 'id', 'required': True,
                        'schema': {'type': 'string', 'format': 'uuid'}})
            for code, description in [('400', '参数无效'), ('401', '凭据无效'), ('403', '访问被拒绝'),
                    ('404', '任务不存在或属于其他空间'), ('409', '幂等冲突或结果未完成'),
                    ('410', '结果快照已过期'), ('429', '频率、并发或额度超限'), ('503', '服务暂不可用')]:
                operation['responses'][code] = {'description': description, 'content': {
                    'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}
    paths[base]['post']['responses']['202']['content'] = {'application/json': {'schema': {
        'type': 'object', 'required': ['id', 'status', 'reused', 'status_url', 'results_url', 'export_url', 'poll_after_seconds'],
        'properties': {'id': {'type': 'string', 'format': 'uuid'}, 'status': {'type': 'string',
            'enum': ['queued', 'running', 'succeeded', 'failed']}, 'reused': {'type': 'boolean'},
            **{k: {'type': 'string'} for k in ('status_url', 'results_url', 'export_url')},
            'poll_after_seconds': {'type': 'integer', 'minimum': 1}}}}}
    snapshot = {'type': 'object', 'required': ['places', 'provider', 'provider_version', 'ordering', 'middle_verified'],
        'properties': {'places': {'type': 'array', 'maxItems': 100, 'items': {'$ref': '#/components/schemas/MapsPlace'}},
            'provider': {'type': 'string'}, 'provider_version': {'type': 'string'},
            'ordering': {'type': 'string', 'enum': ['distance']}, 'middle_verified': {'type': 'boolean'},
            'notice': {'type': 'string'}, 'attribution': {'type': 'string'},
            'location_resolution': {'type':'object','description':'文字位置解析的真实中心点、显示名称、来源及OpenStreetMap署名。'},
            'search_area': {'type':'object','description':'本次实际使用的中心坐标location和半径radius_m。'}}}
    schemas['MapsJob'] = {'type': 'object', 'required': ['id', 'parameters', 'status', 'charged'],
        'properties': {'id': {'type': 'string', 'format': 'uuid'},
            'parameters': {'$ref': '#/components/schemas/MapsRequest'},
            'status': {'type': 'string', 'enum': ['queued', 'running', 'succeeded', 'failed']},
            'charged': {'type': 'integer', 'enum': [0, 1]}, 'error_code': text,
            **{k: {'type': 'string', 'format': 'date-time', 'nullable': True}
               for k in ('created_at', 'completed_at', 'result_expires_at')},
            'response_hash': text, 'result_cache': snapshot}}
    for suffix in ('/{id}', '/{id}/results'):
        paths[base+suffix]['get']['responses']['200']['content'] = {'application/json': {
            'schema': {'$ref': '#/components/schemas/MapsJob'}}}
    download = paths[base+'/{id}/export']['get']
    download['parameters'].append({'in': 'query', 'name': 'format', 'required': True,
        'schema': {'type': 'string', 'enum': ['csv', 'jsonl']}})
    download['description'] = ('读取同一次查询快照，不重新采集。CSV每商家一行，JSON字段保留原始对象；'
        'JSONL为一行完整任务信封，零结果也保留请求条件。CSV普通单元格有公式转义。'
        '快照24小时到期返回410；缺失值为null，不补造电话、评分、Place ID或完整评论。')
    download['responses']['200']['content'] = {mime: {'schema': {'type': 'string', 'format': 'binary'}}
        for mime in ('text/csv', 'application/x-ndjson')}
    return document
