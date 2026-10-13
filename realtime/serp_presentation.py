"""User exports of saved results; no transport payloads or fabricated values."""
from urllib.parse import urlsplit

SOURCES = {'google_web': 'Google 网页', 'google_news': 'Google 新闻', 'google_trends': 'Google 热门话题'}
USER_COLUMNS = ('数据类型', '标题', '链接', '摘要', '链接网站', '媒体', '自然排名', '新闻列表顺序', '搜索关键词', '搜索地区', '搜索语言', '采集时间', '发布时间', '搜索页数', '历史首次发现', '热门话题', '搜索量标记')


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
        yield dict(zip(USER_COLUMNS, (
            SOURCES.get(row.get('source'), '未知'), result.get('title'), url,
            result.get('snippet'), domain, result.get('publisher'),
            result.get('position') if row.get('source') == 'google_web' else None,
            result.get('feed_position') if row.get('source') == 'google_news' else None,
            row.get('query') if not topic else None, params.get('gl') or params.get('geo'), params.get('hl'),
            row.get('completed_at'), result.get('date') or topic.get('published_at'),
            result.get('page'), {True:'是',False:'否'}.get(result.get('is_new_url')),
            topic.get('keyword'), topic.get('approx_traffic'),
        )))


def csv_value(value):
    if value is None or value == '':
        return '未知'
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
