"""Marsyoo Maps conditions contract (platform reply 2026-11-12)."""
import math
from copy import deepcopy
from .product import ProductError
from .google_data import digest
from .maps_provider import prepare

CAPABILITIES = ['identity', 'title', 'condition:location', 'condition:category']


def maps_request(parameters):
    from .maps_provider import CITIES
    if not parameters.get('location') and parameters.get('city') not in CITIES:
        # Resolve only in the single Maps Collector, not in every API process.
        if parameters['mode']!='text':
            raise ProductError('maps_location_required','附近类型搜索请指定已知城市或坐标；文字查询可直接包含城市',400)
        query=parameters['query'] + (' in '+parameters['city'] if parameters.get('city') else '')
        return dict(dataset_id='google_maps_raw', resource_kind='content', source_platforms=['google_maps'],
            query=query, language=parameters['language'], region=parameters['region'],
            max_items=parameters.get('max_places',21),deadline_seconds=1800 if parameters.get('max_places',21)>21 else 300,conditions=[])
    p = prepare(parameters)
    location = p['location']
    for key, bound in (('lat', 90), ('lng', 180)):
        value = location.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > bound:
            raise ProductError('invalid_parameter', '地图坐标无效')
    radius = p['radius_m']
    if type(radius) is not int or not 1 <= radius <= 50000:
        raise ProductError('invalid_parameter', '地图半径无效')
    if p['mode'] not in ('text', 'nearby') or p['language'] not in ('en', 'zh'):
        raise ProductError('invalid_parameter', '地图模式或语言无效')
    if not isinstance(p['query'], str) or not 1 <= len(p['query']) <= 300:
        raise ProductError('invalid_query', '地图关键词无效')
    conditions = [dict(fact_type='location', name='search_area', op='within',
                       value=dict(lat=location['lat'], lng=location['lng'], radius_m=radius))]
    if p['mode'] == 'nearby':
        conditions.append(dict(fact_type='category', name='included_type', op='eq', value=p['included_type']))
    return dict(dataset_id='google_maps_raw', resource_kind='content',
                source_platforms=['google_maps'], query=p['query'], language=p['language'],
                region=p['region'], max_items=p.get('max_places',1), deadline_seconds=1800 if p.get('max_places',1)>21 else 300, conditions=conditions)


def verify_snapshot(snapshot, *, version_id, expected_payload, expected_hash):
    """Never substitute a latest version or treat queued as persisted."""
    if (not isinstance(snapshot, dict) or snapshot.get('version_id') != version_id
            or snapshot.get('payload_hash') != expected_hash
            or snapshot.get('platform_payload') != expected_payload
            or digest(snapshot.get('platform_payload')) != expected_hash.removeprefix('sha256:')):
        raise ProductError('middle_snapshot_mismatch', '中台固定版本快照与本次查询不一致', 502)
    return deepcopy(snapshot['platform_payload'])


def snapshot_message(*, job_id, task_id, payload):
    """One immutable query observation, including valid zero-result searches."""
    from uuid import UUID
    job_id = str(UUID(str(job_id)))
    if (not isinstance(task_id, str) or not task_id.startswith('ctask_')
            or not task_id[6:].isalnum()):
        raise ProductError('invalid_parameter', '中台任务编号无效')
    if (not isinstance(payload, dict) or payload.get('schema_version') not in ('searchflow-maps-query-1','searchflow-maps-query-2')
            or payload.get('job_id') != job_id or payload.get('collection_task_id') != task_id
            or not isinstance(payload.get('places'), list)
            or type(payload.get('place_count')) is not int
            or payload['place_count'] != len(payload['places'])):
        raise ProductError('invalid_parameter', '地图快照身份或计数不一致')
    observed_at = payload.get('observed_at')
    from datetime import datetime
    try:
        observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ProductError('invalid_parameter', '地图快照缺少有效采集完成时间') from None
    key = 'maps-query:' + job_id
    return dict(schema_version='whale.ingest.v1', dataset_id='google_maps_raw',
                source=dict(source_platform='google_maps', source_name='searchflow.maps.query',
                            source_record_key=key, collection_task_id=task_id,
                            payload_hash='sha256:' + digest(payload), received_at=observed_at),
                metrics=dict(snapshot_at=observed_at, platform_metrics=dict(place_count=payload['place_count'])),
                discovery=dict(collector='searchflow.maps', acquisition_type='live_query', discovered_at=observed_at),
                content=dict(content_type='search_result', external_content_id=key,
                             title='Google Maps query ' + job_id, platform_payload=deepcopy(payload)))


def task_parameters(task, *, query_id, task_id, expected_parameters):
    """Authorize and translate a claimed task; never execute another app's job."""
    from .google_local_jobs import parameters
    expected = maps_request(parameters(expected_parameters))
    platform_expected = dict(expected, max_items=min(expected['max_items'],21))
    if (not isinstance(task, dict) or task.get('id') != task_id
            or task.get('task_type') != 'live_query'
            or task.get('dataset_id') != 'google_maps_raw'
            or task.get('source_platform') != 'google_maps'
            or set(task.get('required_capabilities') or []) - set(CAPABILITIES)):
        raise ProductError('middle_claim_mismatch', '地图任务身份或能力不匹配', 503)
    payload = task.get('payload')
    if not isinstance(payload, dict) or payload.get('live_query_id') != query_id:
        raise ProductError('middle_claim_mismatch', '地图任务查询编号不匹配', 503)
    for name in ('query', 'language', 'region', 'max_items', 'conditions'):
        if digest((payload.get(name) or []) if name=='conditions' else payload.get(name)) != digest(platform_expected[name]):
            raise ProductError('middle_conditions_mismatch', '地图任务条件未完整透传', 503)
    if not expected['conditions']:
        return parameters(dict(mode='text',query=expected['query'],region=expected['region'],language=expected['language'],max_places=expected['max_items']))
    location = expected['conditions'][0]['value']
    mode = 'nearby' if len(expected['conditions']) == 2 else 'text'
    translated=dict(mode=mode, query=expected['query'], region=expected['region'],
                           language=expected['language'], location={k:location[k] for k in ('lat','lng')},
                           radius_m=location['radius_m'],
                           included_type=expected['conditions'][1]['value'] if mode == 'nearby' else '')
    if expected['max_items']!=1:translated['max_places']=expected['max_items']
    return parameters(translated)


def query_snapshot(*, job_id, query_id, task_id, parameters, result, observed_at):
    """Preserve provider raw objects and every delivered field before upload."""
    from .google_local_jobs import parameters as validate
    from .maps_provider import normalize
    p = prepare(validate(parameters))
    raw, places = result.get('raw_response'), result.get('places')
    if not isinstance(raw, list) or not isinstance(places, list):
        raise ProductError('maps_invalid_response', '地图结果缺少原始响应', 502)
    import hashlib
    import json
    source_hash = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if result.get('response_hash') != source_hash:
        raise ProductError('maps_invalid_response', '地图原始响应摘要不一致', 502)
    # Recompute from raw: a normalized result must not silently lose fields.
    if digest(normalize(raw, p)) != digest(places):
        raise ProductError('maps_invalid_response', '地图原始响应与商家列表不一致', 502)
    coverage=result.get('coverage') or dict(requested_count=p.get('max_places',1),returned_count=len(places),
        raw_occurrence_count=len(raw),unique_entity_count=len(places),duplicate_occurrence_count=0,
        provider_depth=1,provider_mode='fast',truncated=False,stop_reason='upstream_exhausted',
        coverage_complete=False,coverage_status='not_independently_verified')
    if (not isinstance(coverage,dict) or coverage.get('returned_count')!=len(places)
            or coverage.get('raw_occurrence_count')!=len(raw)
            or coverage.get('requested_count')!=p.get('max_places',1)):
        raise ProductError('maps_invalid_response','地图覆盖证明与结果不一致',502)
    payload = dict(schema_version='searchflow-maps-query-2', job_id=str(job_id),
                   live_query_id=query_id, collection_task_id=task_id, observed_at=observed_at,
                   query_conditions=p, counts=dict(document_count=1, business_entity_count=len(places),
                       business_entity_unit='places'),
                   spatial_scope=dict(requested_location=deepcopy(p.get('location')),
                       requested_radius_m=p.get('radius_m'),
                       distance_filter_verified=True, distance_verification_basis='raw_coordinates_haversine_radius_check',
                       category_relevance_verified=False),
                   provider=dict(name=result['provider'], version=result['provider_version'], mode=coverage['provider_mode'], depth=coverage['provider_depth']),
                   collection_coverage=deepcopy(coverage), ordering=result['ordering'], place_count=len(places), platform_metrics=dict(place_count=len(places)), places=deepcopy(places),
                   raw_response=deepcopy(raw), source_response_hash=result['response_hash'])
    # Validate observation identity/count with the exact upload builder.
    snapshot_message(job_id=job_id, task_id=task_id, payload=payload)
    return payload
