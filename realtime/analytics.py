"""SERP analytics: exact portfolio aggregates and comparable observations.

Only sf_analytics_* projections are maintained; no collection or source mutation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev
from urllib.parse import urlsplit
import hashlib
import json
import threading
import time

from .serp_fields import normalize_serp_url

VISIBLE = "r.source <> 'google_images' AND NOT EXISTS(SELECT 1 FROM serp_task_options private WHERE private.campaign_id=r.campaign_id AND private.options->>'customer_private'='true')"
REGION = "r.region"
LANGUAGE = "r.language"
_cache = {}
_cache_lock = threading.Lock()


def filters(days='30', source='', region='', language=''):
    from .product import ProductError
    from .serp_console import REGIONS
    if str(days) not in ('1','7','30','90'):
        raise ProductError('invalid_window','时间范围仅支持 1、7、30、90 天')
    if source not in ('','all','google_web','google_news','google_trends') or region not in ('',*REGIONS) or language not in ('','en','zh'):
        raise ProductError('invalid_filter','不支持所选来源、地区或语言')
    end=datetime.now(timezone.utc)
    start=end.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=int(days)-1)
    clauses=[VISIBLE,'r.completed_at>=%s','r.completed_at<=%s'];params=[start,end]
    if source and source!='all':clauses.append('r.source=%s');params.append(source)
    if region:clauses.append(REGION+'=%s');params.append(region)
    if language:clauses.append(LANGUAGE+'=%s');params.append(language)
    return ' AND '.join(clauses),params,start,end


def _rank(value):
    return value if type(value) is int and value>0 else None


def _items(snapshot):
    out={}
    for value in snapshot.get('response',{}).get('organic_results') or []:
        if not isinstance(value,dict) or not isinstance(value.get('link'),str):continue
        try:
            link=normalize_serp_url(value['link'])
            host=(urlsplit(link).hostname or '').lower().removeprefix('www.')
        except ValueError:continue
        rank=_rank(value.get('position'))
        if link not in out or (rank is not None and (out[link]['position'] is None or rank<out[link]['position'])):
            out[link]={'link':link,'domain':host,'position':rank,'title':value.get('title'),'snippet':value.get('snippet')}
    return out


def _date(value):
    if isinstance(value,datetime):return value
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError,TypeError):return None


def _text_changed(a,b):
    return isinstance(a,str) and isinstance(b,str) and bool(a.strip()) and bool(b.strip()) and ' '.join(a.split())!=' '.join(b.split())


def enrich_snapshots(snapshots,domains,report):
    """All ranks remain observed ranks. Missing observations are never penalized."""
    chronological=list(reversed(snapshots));observations=[_items(s) for s in chronological]
    pages=defaultdict(list);bins=Counter();rank_records=title_records=snippet_records=records=0
    for index,(s,items) in enumerate(zip(chronological,observations)):
        for url,item in items.items():
            pages[url].append((index,s,item));records+=1
            rank=item['position'];rank_records+=rank is not None
            title_records+=bool(item['title']);snippet_records+=bool(item['snippet'])
            bins['unknown' if rank is None else '1' if rank==1 else '2–3' if rank<=3 else '4–10' if rank<=10 else '11–20' if rank<=20 else '21+']+=1
    transitions=[]
    for index in range(1,len(observations)):
        a,b=observations[index-1:index+1];shared=a.keys()&b.keys();deltas=[];up=down=unchanged=0
        for url in shared:
            x,y=a[url]['position'],b[url]['position']
            if x is None or y is None:continue
            delta=x-y;deltas.append(abs(delta));up+=delta>0;down+=delta<0;unchanged+=delta==0
        union=a.keys()|b.keys()
        transitions.append(dict(previous_at=chronological[index-1]['completed_at'],fetched_at=chronological[index]['completed_at'],
                                new_urls=len(b.keys()-a.keys()),missing_urls=len(a.keys()-b.keys()),improved=up,declined=down,unchanged=unchanged,
                                comparable_ranks=len(deltas),mean_rank_movement=round(mean(deltas),2) if deltas else None,
                                overlap=round(len(shared)/len(union),4) if union else None,
                                title_changes=sum(_text_changed(a[u]['title'],b[u]['title']) for u in shared),
                                snippet_changes=sum(_text_changed(a[u]['snippet'],b[u]['snippet']) for u in shared)))
    page_rows=[]
    latest=observations[-1] if observations else {};previous=observations[-2] if len(observations)>1 else {}
    for url,visits in pages.items():
        ranks=[v['position'] for _,_,v in visits if v['position'] is not None];first,last=visits[0],visits[-1]
        before=previous.get(url,{});current=latest.get(url,{})
        title_changes=sum(_text_changed(a[2]['title'],b[2]['title']) for a,b in zip(visits,visits[1:]))
        snippet_changes=sum(_text_changed(a[2]['snippet'],b[2]['snippet']) for a,b in zip(visits,visits[1:]))
        page_rows.append(dict(url=url,domain=last[2]['domain'],title=last[2]['title'],observations=len(visits),
                              appearance_rate=round(len(visits)/len(chronological),4),first_seen=first[1]['completed_at'],last_seen=last[1]['completed_at'],
                              first_rank=first[2]['position'],last_observed_rank=last[2]['position'],current_rank=current.get('position'),
                              previous_rank=before.get('position'),latest_present=url in latest,best_rank=min(ranks) if ranks else None,
                              worst_rank=max(ranks) if ranks else None,mean_rank=round(mean(ranks),2) if ranks else None,
                              rank_deviation=round(pstdev(ranks),2) if len(ranks)>1 else None,
                              reentries=sum(b[0]>a[0]+1 for a,b in zip(visits,visits[1:])),title_changes=title_changes,snippet_changes=snippet_changes))
    # Enrich each tracked domain without treating an unknown or absent rank as 11.
    for metric in report['domains']:
        d=metric['domain'];ranks=[r['ranks'][d] for r in report['timeline'] if r['ranks'][d] is not None]
        last=report['timeline'][-1]['ranks'][d] if report['timeline'] else None
        previous_rank=report['timeline'][-2]['ranks'][d] if len(report['timeline'])>1 else None
        first=report['timeline'][0]['ranks'][d] if report['timeline'] else None
        eligible=metric['eligible_samples']
        metric.update(mean_rank=round(mean(ranks),2) if ranks else None,median_rank=median(ranks) if ranks else None,
                      worst_rank=max(ranks) if ranks else None,rank_deviation=round(pstdev(ranks),2) if len(ranks)>1 else None,
                      top3_samples=sum(r<=3 for r in ranks),top10_samples=sum(r<=10 for r in ranks),
                      top3_rate=round(sum(r<=3 for r in ranks)/eligible,4) if eligible else None,
                      top10_rate=round(sum(r<=10 for r in ranks)/eligible,4) if eligible else None,
                      latest_delta=previous_rank-last if previous_rank is not None and last is not None else None,
                      period_delta=first-last if first is not None and last is not None else None,
                      unique_pages=sum(p['domain']==d or p['domain'].endswith('.'+d) for p in page_rows))
    changes={c['url']:c for c in report['changes']}
    for url in latest.keys()&previous.keys():
        a,b=previous[url],latest[url];tc=_text_changed(a['title'],b['title']);sc=_text_changed(a['snippet'],b['snippet'])
        if tc or sc:
            c=changes.setdefault(url,dict(url=url,title=b['title'],kind='content',previous_rank=a['position'],current_rank=b['position'],delta=None))
            c.update(title_changed=tc,snippet_changed=sc,previous_title=a['title'],current_title=b['title'],previous_snippet=a['snippet'],current_snippet=b['snippet'])
    domain_rows=[]
    hosts={p['domain'] for p in page_rows}
    for host in hosts:
        rs=[x['position'] for items in observations for x in items.values() if x['domain']==host and x['position'] is not None]
        hits=sum(any(x['domain']==host for x in items.values()) for items in observations)
        domain_rows.append(dict(domain=host,samples=hits,appearance_rate=round(hits/len(observations),4),unique_pages=sum(p['domain']==host for p in page_rows),
                                mean_observed_rank=round(mean(rs),2) if rs else None,best_rank=min(rs) if rs else None))
    gap_dates=[_date(s['completed_at']) for s in chronological];gaps=[(b-a).total_seconds() for a,b in zip(gap_dates,gap_dates[1:]) if a and b and b>=a]
    valid_pairs=len(transitions);avg_overlap=[t['overlap'] for t in transitions if t['overlap'] is not None]
    report.update(changes=sorted(changes.values(),key=lambda c:(c['kind'],c['url'])),transitions=transitions,
                  pages=sorted(page_rows,key=lambda p:(not p['latest_present'],-p['observations'],p['url'])),
                  competitors=sorted(domain_rows,key=lambda d:(-d['samples'],d['mean_observed_rank'] or 100000,d['domain']))[:50],
                  distribution=[dict(bucket=k,count=bins[k]) for k in ['1','2–3','4–10','11–20','21+','unknown']],
                  quality=dict(result_records=records,rank_provided=rank_records,title_provided=title_records,snippet_provided=snippet_records,
                               rank_coverage=round(rank_records/records,4) if records else None),
                  trend=dict(comparisons=valid_pairs,unique_urls=len(pages),unique_domains=len(hosts),
                             mean_overlap=round(mean(avg_overlap),4) if avg_overlap else None,
                             mean_results=round(records/len(observations),2) if observations else None,
                             median_interval_hours=round(median(gaps)/3600,2) if gaps else None,
                             first_sample=chronological[0]['completed_at'] if chronological else None,
                             title_change_events=sum(t['title_changes'] for t in transitions),snippet_change_events=sum(t['snippet_changes'] for t in transitions)),
                  methodology=['排名均值、中位数与波动只使用实际提供的排名；缺失不补值。','出现率只描述已采样结果，不代表流量、点击率或搜索市场份额。','页面首次/最后出现仅针对当前样本窗口，不是全网首次发布。','标题和摘要变化来自搜索结果显示内容，不代表网站正文发生变更。'])
    report['note']='仅比较同一关键词与搜索条件；缓存按原采样时间去重。页面未出现不代表第 11 名或从 Google 消失。'
    return report


def history_report(base,query='',domain='',region='',language='',days='30',competitors='',scope=''):
    from .product import ProductError,compare_snapshots,domain_name
    if not isinstance(query,str) or len(query)>300:raise ProductError('invalid_query','关键词过长')
    if not isinstance(competitors,str) or len(competitors)>2600:raise ProductError('invalid_domain','竞品域名过长')
    names=[domain_name(v.strip()) for v in competitors.replace('，',',').split(',') if v.strip()]
    if len(names)>10:raise ProductError('invalid_domain','最多比较 10 个竞品')
    domains=list(dict.fromkeys(([domain_name(domain)] if domain else [])+names))
    where,params,start,end=filters(days,'google_web',region,language)
    success=" AND r.classification IN('results','empty')"
    with base.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        db.execute("SET LOCAL statement_timeout='12s'")
        choices=db.execute('SELECT r.query,count(*) samples,max(r.completed_at) last_seen FROM sf_analytics_requests r WHERE '+where+success+' GROUP BY r.query ORDER BY count(*) DESC,max(r.completed_at) DESC,r.query LIMIT 100',params).fetchall()
        if not query and choices:query=choices[0]['query']
        where+=' AND r.query=%s';params.append(query)
        groups=db.execute('SELECT r.provider,'+REGION+' region,'+LANGUAGE+" language,r.device device,r.page,count(*) samples,max(r.completed_at) latest FROM sf_analytics_requests r WHERE "+where+success+' GROUP BY 1,2,3,4,5 ORDER BY count(*) DESC,max(r.completed_at) DESC',params).fetchall()
        for g in groups:
            g['id']=hashlib.sha256(json.dumps([g[k] for k in ('provider','region','language','device','page')]).encode()).hexdigest()[:24]
        group=next((g for g in groups if g['id']==scope),None) if scope else (groups[0] if groups else None)
        if scope and not group:raise ProductError('invalid_scope','此搜索条件组不在当前关键词与时间范围内，请重新选择')
        projection=projection_status(db)
        request_summary=db.execute("SELECT count(*) requests,count(*) FILTER(WHERE classification IN('results','empty')) successful,count(*) FILTER(WHERE classification NOT IN('results','empty')) failed FROM sf_analytics_requests r WHERE "+where,params).fetchone()
        rows=[]
        if group:
            specific=where+' AND r.provider IS NOT DISTINCT FROM %s AND '+REGION+' IS NOT DISTINCT FROM %s AND '+LANGUAGE+" IS NOT DISTINCT FROM %s AND r.device IS NOT DISTINCT FROM %s AND r.page=%s"
            values=params+[group[k] for k in ('provider','region','language','device','page')]
            rows=db.execute('SELECT r.request_id id,r.provider,r.page,r.completed_at,r.response FROM sf_analytics_requests r WHERE '+specific+success+' ORDER BY r.completed_at DESC,r.request_id DESC LIMIT 1001',values).fetchall()
    truncated=len(rows)>1000;selected=rows[:1000]
    report=compare_snapshots(selected,domains)
    auto_domains=[x['domain'] for x in report['competitors'][:3]] if not domains else []
    if auto_domains:report=compare_snapshots(selected,auto_domains)
    return dict(**report,query=query,queries=choices,scope={k:group[k] for k in ('provider','region','language','device','page')} if group else {},scope_id=group['id'] if group else '',scope_choices=groups,
                request_summary=request_summary,excluded_incompatible_samples=sum(g['samples'] for g in groups if g is not group),window_days=int(days),sample_limit=1000,truncated=truncated,
                projection=projection,auto_domains=auto_domains,fetched_records=len(selected),cache_duplicates_removed=len(selected)-report['samples'],since=start,until=end,generated_at=end)


REQUEST_REPORT = r"""
WITH s AS MATERIALIZED (
 SELECT r.request_id,r.query,r.source,r.provider,r.completed_at,r.classification,r.error_code,
        REGION_EXPR region,LANGUAGE_EXPR language,r.page
 FROM sf_analytics_requests r WHERE SCOPE_WHERE
), keywords AS (
 SELECT query,count(*) requests,count(*) FILTER(WHERE classification IN('results','empty')) successful,
 count(*) FILTER(WHERE classification NOT IN('results','empty')) failed,
 count(*) FILTER(WHERE source='google_web' AND classification IN('results','empty')) web_samples,
 max(completed_at) latest,array_agg(DISTINCT source) sources
 FROM s WHERE source<>'google_trends' AND query ILIKE %s GROUP BY query
)
SELECT jsonb_build_object(
 'summary',(SELECT jsonb_build_object('requests',count(*),'successful',count(*) FILTER(WHERE classification IN('results','empty')),
 'failed',count(*) FILTER(WHERE classification NOT IN('results','empty')),'empty',count(*) FILTER(WHERE classification='empty'),
 'keywords',count(DISTINCT query) FILTER(WHERE source<>'google_trends'),'first_request',min(completed_at),'last_request',max(completed_at),
 'web_keywords',count(DISTINCT query) FILTER(WHERE source='google_web'),
 'web_repeated_keywords',(SELECT count(*) FROM (SELECT query FROM s WHERE source='google_web' AND classification IN('results','empty') GROUP BY query HAVING count(*)>=2) q)) FROM s),
 'daily',(SELECT coalesce(jsonb_agg(t ORDER BY t.day),'[]'::jsonb) FROM (
 SELECT (completed_at AT TIME ZONE 'UTC')::date AS "day",count(*) requests,count(*) FILTER(WHERE classification IN('results','empty')) successful,
 count(*) FILTER(WHERE classification NOT IN('results','empty')) failed FROM s GROUP BY 1) t),
 'sources',(SELECT coalesce(jsonb_agg(t ORDER BY t.source),'[]'::jsonb) FROM (
 SELECT source,count(*) requests,count(*) FILTER(WHERE classification IN('results','empty')) successful,
 count(*) FILTER(WHERE classification NOT IN('results','empty')) failed,count(DISTINCT query) FILTER(WHERE source<>'google_trends') keywords FROM s GROUP BY source) t),
 'errors',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (
 SELECT classification,error_code,count(*) requests FROM s WHERE classification NOT IN('results','empty') GROUP BY 1,2 ORDER BY count(*) DESC,classification,error_code LIMIT 12) t),
 'scopes',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (
 SELECT source,provider,region,language,page,count(*) requests FROM s GROUP BY 1,2,3,4,5 ORDER BY count(*) DESC,source LIMIT 50) t),
 'keywords',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT * FROM keywords ORDER BY web_samples DESC,requests DESC,latest DESC,query LIMIT 20 OFFSET %s) t),
 'keyword_total',(SELECT count(*) FROM keywords)
) result
"""

CONTENT_REPORT = r"""
WITH s AS MATERIALIZED (
 SELECT r.request_id,r.query,r.source,r.completed_at FROM sf_analytics_requests r WHERE SCOPE_WHERE AND r.classification IN('results','empty')
), items AS MATERIALIZED (
 SELECT s.source,
        CASE WHEN s.source='google_trends' THEN i.title END COLLATE "C" title,
        CASE WHEN s.source='google_trends' THEN i.traffic_label END traffic_label,
        s.completed_at,i.rank,i.url IS NOT NULL has_link,
        nullif(btrim(i.title),'') IS NOT NULL has_title,
        nullif(btrim(i.snippet),'') IS NOT NULL has_snippet,
        nullif(btrim(i.publisher),'') IS NOT NULL has_publisher,
        nullif(btrim(i.published_at),'') IS NOT NULL has_date,
        nullif(btrim(i.traffic_label),'') IS NOT NULL has_traffic
 FROM s JOIN sf_analytics_items i USING(request_id) WHERE i.child_ordinal=0
), valid AS MATERIALIZED (
 SELECT s.*,i.rank,sha256(convert_to(i.url,'UTF8')) url_key,i.host COLLATE "C" host FROM s JOIN sf_analytics_items i USING(request_id) WHERE i.url IS NOT NULL
)
SELECT jsonb_build_object(
 'summary',jsonb_build_object('records',(SELECT count(*) FROM items),'link_records',(SELECT count(*) FROM valid),
 'unique_urls',(SELECT count(DISTINCT url_key) FROM valid),'domains',(SELECT count(DISTINCT host) FROM valid WHERE host IS NOT NULL),
 'topics',(SELECT count(DISTINCT title) FROM items WHERE source='google_trends'),
 'unresolved_news_links',(SELECT count(*) FROM valid WHERE source='google_news' AND host='news.google.com')),
 'daily',(SELECT coalesce(jsonb_agg(t ORDER BY t.day),'[]'::jsonb) FROM (
 SELECT (completed_at AT TIME ZONE 'UTC')::date AS "day",count(*) link_records,count(DISTINCT url_key) unique_urls FROM valid GROUP BY 1) t),
 'sources',(SELECT coalesce(jsonb_agg(t ORDER BY t.source),'[]'::jsonb) FROM (
 SELECT source,count(*) link_records,count(DISTINCT url_key) unique_urls FROM valid GROUP BY source) t),
 'quality',(SELECT coalesce(jsonb_agg(t ORDER BY t.source),'[]'::jsonb) FROM (
 SELECT source,count(*) records,count(*) FILTER(WHERE has_title) title,
 count(*) FILTER(WHERE has_snippet) snippet,
 count(*) FILTER(WHERE rank IS NOT NULL) rank,
 count(*) FILTER(WHERE has_publisher) publisher,
 count(*) FILTER(WHERE has_date) published_at,
 count(*) FILTER(WHERE has_traffic) traffic_label,
 count(*) FILTER(WHERE has_link) link
 FROM items GROUP BY source) t),
 'domains',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (
 SELECT host domain,count(*) link_records,count(DISTINCT request_id) samples,count(DISTINCT url_key) unique_urls,
 count(DISTINCT query) FILTER(WHERE source<>'google_trends') keywords,min(rank) best_rank,round(avg(rank),2) mean_observed_rank,
 max(completed_at) latest FROM valid WHERE host IS NOT NULL AND host<>'news.google.com'
 GROUP BY host ORDER BY count(DISTINCT request_id) DESC,count(*) DESC,host LIMIT 30) t),
 'urls',(SELECT coalesce(jsonb_agg((to_jsonb(t)-'latest_request'-'url_key') || (SELECT jsonb_build_object('title',i.title,'url',i.url) FROM sf_analytics_items i WHERE i.request_id=t.latest_request AND sha256(convert_to(i.url,'UTF8'))=t.url_key ORDER BY i.ordinal,i.child_ordinal LIMIT 1)),'[]'::jsonb) FROM (
 SELECT url_key,host domain,count(*) records,count(DISTINCT request_id) samples,
 (array_agg(request_id ORDER BY completed_at DESC,request_id DESC))[1] latest_request,
 count(DISTINCT query) FILTER(WHERE source<>'google_trends') keywords,min(completed_at) first_seen,max(completed_at) last_seen
 FROM valid GROUP BY url_key,host ORDER BY count(DISTINCT request_id) DESC,url_key LIMIT 30) t),
 'topics',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (
 SELECT title keyword,count(*) samples,max(completed_at) latest,(array_agg(traffic_label ORDER BY completed_at DESC))[1] traffic_label
 FROM items WHERE source='google_trends' AND nullif(title,'') IS NOT NULL GROUP BY title ORDER BY count(*) DESC,title LIMIT 20) t)
) result
"""

_overview_compute_lock=threading.Lock()
_summary_compute_lock=threading.Lock()


def overview_summary(base, days='90', source='', region='', language=''):
    """Exact home counters without computing charts, rankings, or top-URL lists."""
    where, params, start, end = filters(days, source, region, language)
    key = ('summary-v1', base.dsn, str(days), source, region, language, start)
    with _summary_compute_lock:
        with _cache_lock:
            cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        with base.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout='8s'")
            db.execute('SET LOCAL jit=off')
            db.execute("SET LOCAL work_mem='32MB'")
            projection = projection_status(db)
            summary = db.execute(
                "SELECT count(*) requests,"
                "count(DISTINCT r.query) FILTER(WHERE r.source<>'google_trends') keywords,"
                "max(r.completed_at) last_request FROM sf_analytics_requests r WHERE " + where,
                params,
            ).fetchone()
            summary['unique_urls'] = db.execute(
                "SELECT count(DISTINCT sha256(convert_to(i.url,'UTF8'))) n "
                "FROM sf_analytics_requests r JOIN sf_analytics_items i USING(request_id) "
                "WHERE " + where + " AND r.classification IN('results','empty') AND i.url IS NOT NULL",
                params,
            ).fetchone()['n']
        report = dict(summary=summary, projection=projection, generated_at=end,
                      since=start, until=end, cache_seconds=60,
                      filters=dict(days=int(days), source=source or 'all', region=region, language=language))
        with _cache_lock:
            _cache[key] = (time.monotonic(), report)
            if len(_cache) > 32:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        return report


def overview_report(base,days='30',source='',region='',language='',keyword_q='',offset='0'):
    from .product import ProductError
    if len(keyword_q)>100 or not str(offset).isdigit() or int(offset)>1000000:
        raise ProductError('invalid_filter','关键词筛选或分页参数无效')
    where,params,start,end=filters(days,source,region,language)
    source='' if source=='all' else source
    key=(base.dsn if hasattr(base,'dsn') else id(base.pool),str(days),source,region,language,keyword_q,str(offset),start)
    # Serialize cold aggregation to bound memory and pool use. Source reads are
    # consistent; only disposable sf_* report caches are written afterward.
    with _overview_compute_lock:
        with _cache_lock:cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<60:return cached[1]
        with base.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout='20s'")
            db.execute("SET LOCAL jit=off")
            db.execute("SET LOCAL work_mem='64MB'")
            request_sql=REQUEST_REPORT.replace('REGION_EXPR',REGION).replace('LANGUAGE_EXPR',LANGUAGE).replace('SCOPE_WHERE',where)
            projection=projection_status(db)
            report=db.execute(request_sql,[*params,'%'+keyword_q+'%',int(offset)]).fetchone()['result']
            # Content depends on the indexed data and UTC boundary, not on keyword pagination.
            content_key=hashlib.sha256(json.dumps(['v5',str(days),source,region,language,str(start),projection['indexed'],str(projection['updated_at'])]).encode()).hexdigest()
            saved=db.execute('SELECT content,created_at FROM sf_analytics_reports WHERE cache_key=%s',(content_key,)).fetchone()
            content=saved['content'] if saved else db.execute(CONTENT_REPORT.replace('SCOPE_WHERE',where),params).fetchone()['result']
        if not saved:
            from psycopg.types.json import Jsonb
            with base.connect() as db:
                db.execute('INSERT INTO sf_analytics_reports(cache_key,content) VALUES(%s,%s) ON CONFLICT DO NOTHING',(content_key,Jsonb(content)))
                db.execute('DELETE FROM sf_analytics_reports WHERE cache_key IN(SELECT cache_key FROM sf_analytics_reports ORDER BY created_at DESC OFFSET 64)')
        report['summary'].update(content['summary'])
        summary=report['summary'];n=summary['requests'];linked=summary['link_records']
        summary['success_rate']=round(summary['successful']/n,4) if n else None
        summary['repeated_link_records']=linked-summary['unique_urls']
        summary['repeat_rate']=round(summary['repeated_link_records']/linked,4) if linked else None
        rd={r['day']:r for r in report['daily']};cd={r['day']:r for r in content['daily']}
        report['daily']=[dict({'day':(start+timedelta(days=i)).date().isoformat(),'requests':0,'successful':0,'failed':0,'link_records':0,'unique_urls':0},
                              **{**{k:v for k,v in rd.get((start+timedelta(days=i)).date().isoformat(),{}).items() if k!='day'},
                              **{k:v for k,v in cd.get((start+timedelta(days=i)).date().isoformat(),{}).items() if k!='day'}}) for i in range(int(days))]
        sources={r['source']:r for r in content['sources']}
        for s in report['sources']:s.update(sources.get(s['source'],dict(link_records=0,unique_urls=0)))
        report.update(domains=content['domains'],urls=content['urls'],quality=content['quality'],topics=content['topics'],
                      projection=projection,generated_at=end,since=start,until=end,filters=dict(days=int(days),source=source or 'all',region=region,language=language),
                      keyword_offset=int(offset),keyword_limit=20,keyword_q=keyword_q,
                      methodology=['统计范围为当前筛选内的公开已存请求，不包含客户私有 API 数据；不同请求条件不用于直接排名比较。',
                                   '链接使用已保存的规范化链接（缺失时回退原链接），按 SHA-256 标识去重，与链接注册表一致。窗口内重复不等于无价值采集，重复观察可用于监测排名。',
                                   '请求数来自唯一请求记录，不等于 worker 全部网络重试；关键词统计不包含忽略关键词的 Trending Now。',
                                   '按 UTC 自然日分桶，今天是不完整的一天；每天去重之和不等于整个窗口去重。',
                                   'News 是 RSS 列表，Trends 是热门话题与报道；两者不参与 Google 自然排名统计。'])
        with _cache_lock:
            _cache[key]=(time.monotonic(),report)
            if len(_cache)>32:_cache.pop(min(_cache,key=lambda k:_cache[k][0]))
        return report


PROJECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_analytics_requests (
 request_id uuid PRIMARY KEY REFERENCES serp_requests(request_id) ON DELETE CASCADE,
 campaign_id uuid,query text NOT NULL,source text NOT NULL,provider text NOT NULL,page integer NOT NULL,
 completed_at timestamptz NOT NULL,classification text NOT NULL,error_code text,
 region text,language text,device text,response jsonb NOT NULL,projected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sf_analytics_time ON sf_analytics_requests(completed_at DESC);
CREATE INDEX IF NOT EXISTS sf_analytics_query_time ON sf_analytics_requests(query,completed_at DESC);
CREATE INDEX IF NOT EXISTS sf_analytics_source_time ON sf_analytics_requests(source,completed_at DESC);
CREATE TABLE IF NOT EXISTS sf_analytics_items (
 request_id uuid NOT NULL REFERENCES sf_analytics_requests(request_id) ON DELETE CASCADE,
 ordinal integer NOT NULL,child_ordinal integer NOT NULL DEFAULT 0,
 title text,snippet text,url text,host text,rank integer,publisher text,published_at text,traffic_label text,
 PRIMARY KEY(request_id,ordinal,child_ordinal)
);
CREATE TABLE IF NOT EXISTS sf_analytics_reports (
 cache_key text PRIMARY KEY,content jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
"""


def initialize_projection(db):
    db.execute(PROJECTION_SCHEMA)


def projection_status(db):
    row=db.execute('SELECT count(*) total,max(r.completed_at) latest_source FROM serp_requests r WHERE '+VISIBLE).fetchone()
    indexed=db.execute('SELECT count(*) indexed,max(r.projected_at) updated_at FROM sf_analytics_requests r WHERE '+VISIBLE).fetchone()
    return dict(total=row['total'],indexed=indexed['indexed'],pending=max(0,row['total']-indexed['indexed']),updated_at=indexed['updated_at'])


def sync_projection(base,limit=250):
    """Incrementally derive analytics rows under a cross-process lock.

    This writes only our disposable projection; it never initializes legacy
    schemas, modifies source requests, enqueues work, or fetches external URLs.
    """
    from psycopg.types.json import Jsonb
    def text(v):return v if isinstance(v,str) else None
    def records(v):return [x for x in v if isinstance(x,dict)] if isinstance(v,list) else []
    def fields(item,topic=False):
        raw=item.get('normalized_link') or item.get('link');url=host=None
        if isinstance(raw,str):
            try:
                url=normalize_serp_url(raw);parsed=urlsplit(url);host=(parsed.hostname or '').lower().removeprefix('www.')
                if parsed.scheme not in ('http','https') or not host:url=host=None
            except ValueError:url=host=None
        return [text(item.get('keyword') if topic else item.get('title')),text(item.get('snippet')),url,host,
                _rank(item.get('position')),text(item.get('publisher')),text(item.get('date') or item.get('published_at')),text(item.get('approx_traffic'))]
    with base.connect() as db:
        if not db.execute('SELECT pg_try_advisory_xact_lock(20261027,4) acquired').fetchone()['acquired']:return 0
        db.execute("SET LOCAL statement_timeout='30s'")
        pending=db.execute('SELECT r.request_id,r.campaign_id,r.query,r.source,r.provider,r.page,r.completed_at,r.classification,r.error_code,r.response FROM serp_requests r WHERE '+VISIBLE+' AND NOT EXISTS(SELECT 1 FROM sf_analytics_requests a WHERE a.request_id=r.request_id) ORDER BY r.completed_at,r.request_id LIMIT %s',(limit,)).fetchall()
        requests=[];items=[]
        for r in pending:
            response=r['response'] if isinstance(r['response'],dict) else {}
            p=response.get('search_parameters');p=p if isinstance(p,dict) else {}
            source=r['source'];success=r['classification'] in ('results','empty')
            key={'google_web':'organic_results','google_news':'news_results','google_trends':'trending_searches'}.get(source,'organic_results')
            results=records(response.get(key)) if success else []
            compact=dict(organic_results=[])
            if source=='google_web':compact['organic_results']=[{k:v for k,v in x.items() if k in ('link','position','title','snippet')} for x in results]
            compact['search_parameters']={k:p.get(k) for k in ('gl','hl','geo','device')}
            gl=p.get('gl') or p.get('geo')
            requests.append(tuple(r[k] for k in ('request_id','campaign_id','query','source','provider','page','completed_at','classification','error_code'))+(gl.upper() if isinstance(gl,str) else None,text(p.get('hl')),text(p.get('device')),Jsonb(compact)))
            for i,item in enumerate(results,1):
                items.append((r['request_id'],i,0,*fields(item,source=='google_trends')))
                if source=='google_trends':
                    for j,article in enumerate(records(item.get('articles')),1):items.append((r['request_id'],i,j,*fields(article)))
        with db.cursor() as cur:
            cur.executemany('INSERT INTO sf_analytics_requests(request_id,campaign_id,query,source,provider,page,completed_at,classification,error_code,region,language,device,response) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',requests)
            cur.executemany('INSERT INTO sf_analytics_items(request_id,ordinal,child_ordinal,title,snippet,url,host,rank,publisher,published_at,traffic_label) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',items)
    if pending:
        with _cache_lock:_cache.clear()
    return len(pending)
