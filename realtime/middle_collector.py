"""Translate the Consumer live-query contract into a bounded SERP task payload."""

from .product import ProductError


def image_conditions():
    return [dict(fact_type='other', name='searchflow_vertical', op='eq', value='images')]

def trends_conditions(mode='regional_hotlist', time_range='today 3-m'):
    conditions = [dict(fact_type='other', name='searchflow_trends_mode', op='eq', value=mode)]
    if mode == 'keyword':
        conditions.append(dict(fact_type='other', name='searchflow_trends_time_range', op='eq', value=time_range))
    return conditions


def live_payload(payload, *, source='google_web'):
    if source not in ('google_web', 'google_news', 'google_trends', 'google_images'):
        raise ValueError('此来源缺少中台实时采集契约')
    allowed = {'query', 'region', 'language', 'max_items', 'live_query_id',
               'resource_kind', 'url', 'external_id', 'conditions'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('未支持的中台实时查询参数')
    conditions = payload.get('conditions')
    if conditions is None:
        conditions = []
    if not isinstance(conditions, list):
        if source == 'google_trends':
            raise ProductError('invalid_trends_conditions', '趋势条件必须为数组')
        raise ValueError('实时查询条件必须为数组')
    mode, window = 'regional_hotlist', 'today 3-m'
    if source == 'google_trends':
        if not conditions:
            mode = 'keyword'
        elif conditions != trends_conditions('regional_hotlist'):
            windows = [w for w in ('now 7-d','today 1-m','today 3-m','today 12-m','today 5-y')
                       if conditions == trends_conditions('keyword', w)]
            if not windows:
                raise ProductError('invalid_trends_conditions', '关键词趋势模式或时间范围无效')
            mode, window = 'keyword', windows[0]
    elif conditions != (image_conditions() if source == 'google_images' else []):
        raise ValueError('查询条件与采集模式不匹配')
    if payload.get('resource_kind') != 'content' or payload.get('url') or payload.get('external_id'):
        raise ValueError('Google 实时查询目前仅支持关键词 content 请求')
    live_id = payload.get('live_query_id')
    if not isinstance(live_id, str) or not live_id.startswith('lq_') or not live_id[3:].isalnum():
        raise ValueError('缺少有效中台实时查询编号')
    count = payload.get('max_items', 100 if source == 'google_web' else 10)
    if source == 'google_images' and type(count) is int and count > 20:
        raise ProductError('image_max_items_exceeded', 'Google Images 实时查询最多支持 20 张')
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError('实时查询数量需要 1–100')
    return dict(query=payload.get('query'), region=payload.get('region') or 'US',
                language=payload.get('language') or 'en', sources=[source],
                trends_mode=mode, trends_time_range=window, max_items=count, max_pages=11 if source == 'google_web' else 1,
                page_size=10, max_duration_minutes=5)
