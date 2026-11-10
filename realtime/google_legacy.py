"""Read-only access to saved public Google SERP requests; never claims Whale readback."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .google_domain import DomainError, utc_text
from .google_data import DATA_VERSION, news_time, normalize_request

SOURCES = frozenset({'google_web', 'google_news', 'google_trends', 'google_images'})
CLASSIFICATIONS = frozenset({'results','empty','error','timeout','unknown','captcha',
                            'rate_limited','http_error','parse_error','transport_error','service_error'})
LIMITATIONS = [
    '旧库直接读取；Whale 旧上传样本核查见交付状态，尚未完成正式观测投影。',
    'News 列表顺序不是 Google 自然排名；新闻结果不代表完整媒体报道数量。',
    'Trends 是 Trending Now RSS 热门话题，不是关键词历史热度曲线或绝对搜索量。',
    '汇总发布时间 provided 仅表示字段存在；有效 RSS 时间须核对原始 pubDate，不等于出版者网页验证。',
    '缺失设备、精确位置、登录态、缓存状态及源观测时间均为未知。',
]


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _rank(value):
    return value if type(value) is int and value >= 1 else None


def _news_published_at(item):
    return news_time(item)['published_at']


def _item(item, source):
    if not isinstance(item, dict):
        return None
    if source == 'google_web':
        return dict(kind='organic', url=_url(item.get('link')),
                    title=_text(item.get('title')), snippet=_text(item.get('snippet')),
                    organic_position=_rank(item.get('position')),
                    source_absolute_rank=_rank(item.get('rank_absolute')))
    if source == 'google_images':
        return dict(kind='image',url=_url(item.get('image_url')),title=_text(item.get('title')),
                    source_page_url=_url(item.get('source_url')),thumbnail_url=_url(item.get('thumbnail')),
                    width=_rank(item.get('width')),height=_rank(item.get('height')),
                    observed_order=_rank(item.get('observed_order')),upstream_rank=_rank(item.get('position')),
                    rank_basis='upstream_image_grid_order',organic_position=None,media_downloaded=False)
    if source == 'google_news':
        return dict(kind='news_index', url=_url(item.get('link')),
                    publisher_url=_url(item.get('publisher_url')),
                    link_status=_text(item.get('link_status')) or 'unknown',
                    title=_text(item.get('title')), snippet=_text(item.get('snippet')),
                    publisher=_text(item.get('publisher') or item.get('source')),
                    feed_position=_rank(item.get('feed_position')),
                    published_at=_news_published_at(item), publication=news_time(item),
                    organic_position=None)
    articles = item.get('articles')
    return dict(kind='trending_now_topic', topic=_text(item.get('keyword')),
                approx_traffic_label=_text(item.get('approx_traffic')),
                feed_position=_rank(item.get('feed_position')), numeric_trend_series=None,
                articles=[dict(url=_url(a.get('link')), title=_text(a.get('title')),
                               publisher=_text(a.get('source') or a.get('publisher')))
                          for a in (articles if isinstance(articles, list) else []) if isinstance(a, dict)])


def _result(row):
    response = row['response'] if isinstance(row['response'], dict) else {}
    source = row['source']
    key = {'google_web': 'organic_results', 'google_news': 'news_results',
           'google_trends': 'trending_searches', 'google_images':'image_results'}[source]
    values = response.get(key)
    values = values if isinstance(values, list) else []
    params = response.get('search_parameters') or {}
    params = params if isinstance(params, dict) else {}
    metadata = response.get('search_metadata') or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    items = [mapped for item in values if (mapped := _item(item, source)) is not None]
    classification = row['classification']
    availability = 'source_error' if classification not in ('results', 'empty') else 'true_zero' if classification == 'empty' else 'available'
    return dict(request_id=str(row['request_id']), source=source, query=_text(row['query']),
                classification=classification, availability=availability,
                completed_at=utc_text(row['completed_at']), source_observed_at=None,
                query_conditions=dict(country_code=_text(params.get('gl')),
                                      language_code=_text(params.get('hl')),
                                      device=_text(params.get('device')),
                                      location_precision='unknown', login_state='unknown', page=row['page']),
                cache_status=_text(metadata.get('cache_status')) or 'unknown',
                result_count=len(items) if availability != 'source_error' else None,
                items=items, source_schema_version=_text(response.get('schema_version')) or 'unknown',
                field_status={k: 'available' if v is not None else 'unknown' for k, v in {
                    'query': _text(row['query']), 'device': _text(params.get('device')),
                    'country_code': _text(params.get('gl')), 'language_code': _text(params.get('hl')),
                    'cache_status': _text(metadata.get('cache_status'))}.items()},
                provenance='legacy_serp_requests_not_whale_readback')


def _when(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError):
        raise DomainError('invalid_argument', 'Time must be an ISO 8601 UTC timestamp') from None


def _cursor_encode(row):
    data = [utc_text(row['completed_at']), str(row['request_id'])]
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')


def _cursor_decode(value):
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        date, rid = json.loads(raw)
        return _when(date), UUID(rid)
    except (ValueError, TypeError, UnicodeError, IndexError, json.JSONDecodeError):
        raise DomainError('invalid_argument', 'Invalid cursor') from None


def _filters(parameters):
    allowed = {'source', 'query', 'domain', 'from', 'to', 'classification', 'limit', 'cursor',
               'country', 'language', 'device', 'page', 'field_contract'}
    if set(parameters) - allowed:
        raise DomainError('invalid_argument', 'Unknown legacy filter')
    from .google_field_adaptation import validate_field_contract
    validate_field_contract(parameters.get('field_contract', 'legacy'))
    source = parameters.get('source') or None
    if source is not None and source not in SOURCES:
        raise DomainError('invalid_argument', 'Unsupported legacy source')
    try:
        limit = int(parameters.get('limit', 20))
    except (ValueError, TypeError):
        raise DomainError('invalid_argument', 'Invalid limit') from None
    if not 1 <= limit <= 50:
        raise DomainError('invalid_argument', 'Legacy limit must be 1–50')
    query = parameters.get('query') or None
    if query and len(query) > 300:
        raise DomainError('invalid_argument', 'Query is too long')
    domain = parameters.get('domain') or None
    if domain:
        parsed = urlsplit('https://' + domain.lower().strip())
        if not parsed.hostname or parsed.hostname != domain.lower().strip() or len(domain) > 253:
            raise DomainError('invalid_argument', 'Use a hostname for domain')
        domain = parsed.hostname
    classification = parameters.get('classification') or None
    if classification and classification not in CLASSIFICATIONS:
        raise DomainError('invalid_argument', 'Unsupported classification')
    start = _when(parameters['from']) if parameters.get('from') else None
    end = _when(parameters['to']) if parameters.get('to') else None
    if start and end and start >= end:
        raise DomainError('invalid_argument', 'from must precede to')
    where = ["r.source IN ('google_web','google_news','google_trends','google_images')",
             "NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')"]
    args = []
    if source:
        where.append('r.source=%s'); args.append(source)
    if query:
        where.append('r.query ILIKE %s'); args.append('%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    if classification:
        where.append('r.classification=%s'); args.append(classification)
    if start:
        where.append('r.completed_at >= %s'); args.append(start)
    if end:
        where.append('r.completed_at < %s'); args.append(end)
    for key, field in [('country','gl'), ('language','hl'), ('device','device')]:
        value = parameters.get(key)
        if value:
            if len(value) > 80:
                raise DomainError('invalid_argument', 'Filter too long')
            if key == 'country':
                value = value.lower()
            expr = "NULLIF(r.response->'search_parameters'->>%s,'')"
            if key == 'country':
                expr = 'lower(' + expr + ')'
            where.append(expr + (' IS NULL' if value == 'unknown' else '=%s'))
            args.append(field)
            if value != 'unknown': args.append(value)
    if parameters.get('page'):
        try: page = int(parameters['page'])
        except (ValueError, TypeError): raise DomainError('invalid_argument', 'Invalid page') from None
        if not 1 <= page <= 100: raise DomainError('invalid_argument', 'Invalid page')
        where.append('r.page=%s'); args.append(page)
    if domain:
        # Stored links are inspected by exact hostname after bounded candidate reads.
        # SQL also applies the predicate so private or unrelated rows never enter a page.
        where.append("""EXISTS (
            SELECT 1 FROM jsonb_array_elements(
              CASE WHEN jsonb_typeof(r.response->
                CASE r.source WHEN 'google_web' THEN 'organic_results'
                WHEN 'google_news' THEN 'news_results' WHEN 'google_images' THEN 'image_results' ELSE 'trending_searches' END)='array'
              THEN r.response->CASE r.source WHEN 'google_web' THEN 'organic_results'
                WHEN 'google_news' THEN 'news_results' WHEN 'google_images' THEN 'image_results' ELSE 'trending_searches' END
              ELSE '[]'::jsonb END) item
            WHERE lower(substring(item->>'link' from '^https?://([^/?#:]+)'))=%s
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(
                 CASE WHEN jsonb_typeof(item->'articles')='array'
                 THEN item->'articles' ELSE '[]'::jsonb END) article
                 WHERE lower(substring(article->>'link' from '^https?://([^/?#:]+)'))=%s)
        )""")
        args.extend([domain, domain])
    if parameters.get('cursor'):
        date, rid = _cursor_decode(parameters['cursor'])
        where.append('(r.completed_at,r.request_id) < (%s,%s)'); args.extend([date,rid])
    return where, args, limit


def read_public_requests(dsn, parameters, *, normalized=False):
    """Bounded keyset page with SQL-level private exclusion and a read-only transaction."""
    where, args, limit = _filters(parameters)
    field_contract = parameters.get('field_contract', 'legacy')
    if field_contract != 'legacy' and not normalized:
        raise DomainError('invalid_argument', 'field_contract is available only on legacy-data')
    from .google_field_adaptation import adapt_request_fields
    project = adapt_request_fields if field_contract == 'prd-v1' else normalize_request
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout = '10s'")
        rows = db.execute('SELECT r.request_id,r.source,r.query,r.page,r.classification,r.completed_at,r.response FROM serp_requests r WHERE '
                          + ' AND '.join(where) + ' ORDER BY r.completed_at DESC,r.request_id DESC LIMIT %s',
                          args + [limit + 1]).fetchall()
    visible = rows[:limit]
    from .google_delivery import read_delivery_status
    return dict(delivery_summary=read_delivery_status(dsn),
                requests=[(project(row) if normalized else _result(row)) for row in visible],
                data_contract_version=DATA_VERSION if normalized else None, limit=limit, has_more=len(rows)>limit,
                next_cursor=_cursor_encode(visible[-1]) if len(rows)>limit else None,
                filters={k:v for k,v in parameters.items() if k not in ('cursor','limit')},
                generated_at=utc_text(datetime.now(timezone.utc)), scope='public_legacy_history_only',
                coverage={'status':'available' if visible else 'not_covered', 'sample_size':len(visible)},
                quality={'status':'historical_preview', 'methodology_version':'legacy-serp-v2'},
                limitations=LIMITATIONS, whale_readback='not_connected', frozen_manifest=None)


def read_public_request(dsn, request_id, *, normalized=False, field_contract='legacy'):
    from .google_field_adaptation import adapt_request_fields, validate_field_contract
    validate_field_contract(field_contract)
    if field_contract != 'legacy' and not normalized:
        raise DomainError('invalid_argument', 'field_contract is available only on legacy-data')
    try: rid = UUID(request_id)
    except (ValueError, TypeError): raise DomainError('invalid_argument', 'Invalid request id') from None
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout = '3s'")
        row = db.execute("SELECT r.request_id,r.source,r.query,r.page,r.classification,r.completed_at,r.response FROM serp_requests r WHERE r.request_id=%s AND r.source IN ('google_web','google_news','google_trends','google_images') AND NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')", (rid,)).fetchone()
    if not row: raise DomainError('not_found', 'Request not found', 404)
    project = adapt_request_fields if field_contract == 'prd-v1' else normalize_request
    return dict(request=project(row) if normalized else _result(row), generated_at=utc_text(datetime.now(timezone.utc)),
                limitations=LIMITATIONS, provenance='legacy_serp_requests_not_whale_readback')


def _analysis_filters(source, parameters):
    allowed = {'query', 'domain', 'from', 'to', 'classification', 'country', 'language', 'device', 'page'}
    if set(parameters) - allowed:
        raise DomainError('invalid_argument', 'Unsupported analysis filter')
    clauses = ["r.source=%s", "NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')"]
    values = [source]
    if parameters.get('query'):
        query = parameters['query']
        if len(query) > 300: raise DomainError('invalid_argument', 'Query is too long')
        clauses.append('r.query ILIKE %s'); values.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    if parameters.get('from'):
        clauses.append('r.completed_at >= %s'); values.append(_when(parameters['from']))
    if parameters.get('to'):
        clauses.append('r.completed_at < %s'); values.append(_when(parameters['to']))
    if parameters.get('from') and parameters.get('to') and values[-2] >= values[-1]:
        raise DomainError('invalid_argument', 'from must precede to')
    if parameters.get('classification'):
        v=parameters['classification']
        if v not in CLASSIFICATIONS: raise DomainError('invalid_argument', 'Unsupported classification')
        clauses.append('r.classification=%s');values.append(v)
    for key,column in [('country','region'),('language','language'),('device','device')]:
        v=parameters.get(key)
        if v:
            if len(v)>80: raise DomainError('invalid_argument', 'Filter too long')
            clauses.append('r.'+column+(' IS NULL' if v=='unknown' else '=%s'))
            if v!='unknown': values.append(v)
    if parameters.get('page'):
        try: v=int(parameters['page'])
        except (ValueError,TypeError): raise DomainError('invalid_argument', 'Invalid page') from None
        if not 1<=v<=100: raise DomainError('invalid_argument', 'Invalid page')
        clauses.append('r.page=%s');values.append(v)
    if parameters.get('domain'):
        domain=parameters['domain'].lower().strip()
        parsed=urlsplit('https://'+domain)
        if parsed.hostname != domain or len(domain)>253: raise DomainError('invalid_argument', 'Use a hostname for domain')
        clauses.append('EXISTS (SELECT 1 FROM sf_analytics_items d WHERE d.request_id=r.request_id AND d.host=%s)')
        values.append(domain.removeprefix('www.'))
    return ' AND '.join(clauses),values


def read_source_analysis(dsn, source, parameters):
    """Read disposable public projections; report lag instead of claiming full coverage."""
    if source not in {'google_news','google_trends'}:
        raise DomainError('invalid_argument', 'Source analysis supports News and Trends')
    where, values = _analysis_filters(source, parameters)
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        db.execute("SET LOCAL statement_timeout = '15s'")
        db.execute('SET LOCAL jit=off')
        db.execute("SET LOCAL work_mem='32MB'")
        request = db.execute("SELECT count(*) requests,count(*) FILTER(WHERE r.classification='results') with_results,"
            "count(*) FILTER(WHERE r.classification='empty') empty,"
            "count(*) FILTER(WHERE r.classification NOT IN('results','empty')) failed,"
            "min(r.completed_at) observed_from,max(r.completed_at) observed_to "
            'FROM sf_analytics_requests r WHERE '+where,values).fetchone()
        projection = db.execute("SELECT count(*) total FROM serp_requests r WHERE r.source=%s "
            "AND NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')",(source,)).fetchone()['total']
        selected = 'WITH selected AS MATERIALIZED (SELECT r.request_id FROM sf_analytics_requests r WHERE '+where+" AND r.classification='results') "
        join = 'FROM selected s JOIN sf_analytics_items i USING(request_id)'
        if source=='google_news':
            totals=db.execute(selected+"SELECT count(*) records,"
                "count(*) FILTER(WHERE i.url IS NOT NULL AND i.host<>'news.google.com') resolved_links,"
                "count(*) FILTER(WHERE i.host='news.google.com') unresolved_links,"
                "count(*) FILTER(WHERE nullif(i.published_at,'') IS NOT NULL) published_at_provided,"
                "count(*) FILTER(WHERE nullif(i.title,'') IS NOT NULL) title_provided,"
                "count(*) FILTER(WHERE nullif(i.snippet,'') IS NOT NULL) snippet_provided,"
                "count(DISTINCT i.url) FILTER(WHERE i.url IS NOT NULL) unique_urls "
                +join+' WHERE i.child_ordinal=0',values).fetchone()
            groups=db.execute(selected+"SELECT COALESCE(NULLIF(i.publisher,''),'未知') label,count(*) records "
                +join+' WHERE i.child_ordinal=0 GROUP BY 1 ORDER BY records DESC,label LIMIT 30',values).fetchall()
            totals['duplicate_url_records']=max(0,totals['records']-totals['unique_urls'])
            breakdown={'publishers':groups}
        else:
            totals=db.execute(selected+"SELECT count(*) FILTER(WHERE i.child_ordinal=0) topics,"
                "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.title,'') IS NOT NULL) topic_provided,"
                "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.traffic_label,'') IS NOT NULL) traffic_label_provided,"
                "count(*) FILTER(WHERE i.child_ordinal>0) articles " +join,values).fetchone()
            groups=db.execute(selected+"SELECT COALESCE(NULLIF(i.title,''),'未知') label,count(*) observations "
                +join+' WHERE i.child_ordinal=0 GROUP BY 1 ORDER BY observations DESC,label LIMIT 30',values).fetchall()
            traffic=db.execute(selected+"SELECT COALESCE(NULLIF(i.traffic_label,''),'未知') label,count(*) topics "
                +join+' WHERE i.child_ordinal=0 GROUP BY 1 ORDER BY topics DESC,label LIMIT 30',values).fetchall()
            breakdown={'topics':groups,'traffic_labels':traffic}
    applicable=totals.get('records',totals.get('topics',0))
    return dict(source=source,filters=parameters,generated_at=utc_text(datetime.now(timezone.utc)),
        observed_from=utc_text(request['observed_from']) if request['observed_from'] else None,
        observed_to=utc_text(request['observed_to']) if request['observed_to'] else None,
        sample_size=request['requests'],eligible_denominator=request['with_results'],
        request_status={'total':request['requests'],'results':request['with_results'],
                        'empty':request['empty'],'failed':request['failed']},
        totals=totals,breakdown=breakdown,
        missing_counts={k:applicable-v for k,v in totals.items() if k.endswith('_provided')},
        availability='available' if request['requests'] else 'not_covered',
        projection={'indexed':request['requests'],'source_total':projection,'pending':max(0,projection-request['requests']) if not parameters else None},
        methodology_version='legacy-serp-v3',
        publication_quality={'provided_is_verified': False, 'verification_status': 'not_evaluated',
                             'basis': 'projection_field_presence_only'}, limitations=LIMITATIONS,
        provenance='legacy_serp_requests_projection_not_whale_readback')


def read_legacy_quality(dsn):
    """Public legacy coverage and field completeness, separate from formal domain quality."""
    visible = "NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')"
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        db.execute("SET LOCAL statement_timeout='15s'")
        db.execute('SET LOCAL jit=off')
        db.execute("SET LOCAL work_mem='32MB'")
        raw = db.execute('SELECT r.source,count(*) requests FROM serp_requests r WHERE '+visible+
                         " AND r.source IN ('google_web','google_news','google_trends','google_images') GROUP BY r.source").fetchall()
        requests = db.execute("SELECT r.source,count(*) requests,"
            "count(*) FILTER(WHERE r.classification='results') successful,"
            "count(*) FILTER(WHERE r.classification='empty') empty,"
            "count(*) FILTER(WHERE r.classification NOT IN('results','empty')) failed,"
            "count(DISTINCT r.query) queries,min(r.completed_at) earliest,max(r.completed_at) latest,"
            "max(r.projected_at) last_read FROM sf_analytics_requests r WHERE " + visible +
            " AND r.source IN ('google_web','google_news','google_trends','google_images') GROUP BY r.source ORDER BY r.source").fetchall()
        items = db.execute("SELECT r.source,count(*) FILTER(WHERE i.child_ordinal=0) records,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.title,'') IS NOT NULL) title_provided,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.snippet,'') IS NOT NULL) snippet_provided,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND i.rank IS NOT NULL) rank_provided,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.publisher,'') IS NOT NULL) publisher_provided,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.published_at,'') IS NOT NULL) published_at_provided,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND nullif(i.traffic_label,'') IS NOT NULL) traffic_label_provided,"
            "count(*) FILTER(WHERE i.child_ordinal>0) child_articles,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND i.host='news.google.com') unresolved_news_links,"
            "count(*) FILTER(WHERE i.child_ordinal=0 AND i.url IS NOT NULL AND i.host<>'news.google.com') resolved_links "
            "FROM sf_analytics_requests r JOIN sf_analytics_items i USING(request_id) WHERE " + visible +
            " AND r.source IN ('google_web','google_news','google_trends','google_images') GROUP BY r.source",()).fetchall()
    raw_map={r['source']:r['requests'] for r in raw}
    item_map={r['source']:r for r in items}
    sources=[]
    for row in requests:
        row=dict(row)
        row.update(item_map.get(row['source'],{}))
        row['raw_requests']=raw_map.get(row['source'],0)
        row['projection_pending']=max(0,row['raw_requests']-row['requests'])
        count=row.get('records',0)
        row['field_quality']={field:{'provided':row.get(field+'_provided',0),
                                      'missing':count-row.get(field+'_provided',0),
                                      'applicable':count}
                              for field in ('title','snippet','rank','publisher','published_at','traffic_label')}
        for k in ('earliest','latest','last_read'):
            row[k]=utc_text(row[k]) if row[k] else None
        sources.append(row)
    return dict(source='public_legacy_history',generated_at=utc_text(datetime.now(timezone.utc)),
                observed_from=min((r['earliest'] for r in sources if r['earliest']),default=None),
                observed_to=max((r['latest'] for r in sources if r['latest']),default=None),
                filters={},sample_size=sum(r['requests'] for r in sources),
                eligible_denominator=sum(r['requests'] for r in sources),
                missing_counts={'projection_pending':sum(r['projection_pending'] for r in sources)},
                availability='available' if sources else 'not_covered',
                methodology_version='legacy-serp-v3', sources=sources,
                publication_quality={'provided_is_verified': False, 'verification_status': 'not_evaluated',
                                     'basis': 'projection_field_presence_only'}, limitations=LIMITATIONS,
                provenance='legacy_serp_requests_projection_not_whale_readback',new_collection=False,
                numeric_trends_series='not_covered')
