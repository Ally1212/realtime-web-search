"""User exports of saved results; no transport payloads or fabricated values."""
from urllib.parse import urlsplit
import json

SOURCES = {'google_web': 'Google 网页', 'google_news': 'Google 新闻', 'google_trends': 'Google 热门话题'}
USER_COLUMNS = ('数据类型', '标题', '链接', '摘要', '链接网站', '媒体', '自然排名', '新闻列表顺序', '搜索关键词', '搜索地区', '搜索语言', '采集时间', '发布时间', '搜索页数', '历史首次发现', '热门话题', '搜索量标记')
EXTRA_COLUMNS = {
    '绝对排名':'rank_absolute', '上游域名':'upstream_domain', '上游图标':'upstream_favicon',
    '作者':'author', '缩略图':'thumbnail', '网站图标':'favicon', '显示链接':'displayed_link',
    '跳转链接':'redirect_link', '新闻原始链接':'publisher_link', '新闻包装链接':'google_news_link',
    '链接解析状态':'link_status', '原始发布时间':'date_raw', '站内链接':'sitelinks',
    '富摘要':'rich_snippet', '评分':'rating', '评价数量':'reviews', '结果分类':'classification',
    '上游域名分类':'upstream_domain_info', '字段状态':'field_status', '采集方式':'collection_method',
}
BASE_COLUMNS = USER_COLUMNS
USER_COLUMNS += (*EXTRA_COLUMNS, '热点地区', '热点图片', '热点图片来源', '热度量级数值', '量级是否为下限', '设备', '城市')


def user_result_rows(row):
    item = row.get('data') or {}
    params = row.get('search_parameters') or {}
    topic = item if row.get('source') == 'google_trends' else {}
    records = (topic.get('articles') or [{}]) if topic else [item]
    for result in records:
        url = result.get('normalized_link') or result.get('link')
        try:
            domain = urlsplit(url).hostname if url else None
        except ValueError:
            domain = None
        values = dict(zip(BASE_COLUMNS, (
            SOURCES.get(row.get('source'), '未知'), result.get('title'), url,
            result.get('snippet'), domain, result.get('publisher'),
            result.get('position') if row.get('source') == 'google_web' else None,
            result.get('feed_position') if row.get('source') == 'google_news' else None,
            row.get('query') if not topic else None, params.get('gl') or params.get('geo'), params.get('hl'),
            row.get('completed_at'), result.get('date') or topic.get('published_at'),
            result.get('page'), {True:'是',False:'否'}.get(result.get('is_new_url')),
            topic.get('keyword'), topic.get('approx_traffic'),
        )))
        values.update({label:result.get(key) for label,key in EXTRA_COLUMNS.items()})
        values.update({'热点地区':topic.get('geo'), '热点图片':topic.get('picture'),
                       '热点图片来源':topic.get('picture_source'), '热度量级数值':topic.get('traffic_value'),
                       '量级是否为下限':topic.get('traffic_is_lower_bound'),
                       '设备':params.get('device'), '城市':params.get('location')})
        yield values


def csv_value(value):
    if value is None or value == '':
        return '未知'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
