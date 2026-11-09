"""Read-only deterministic audit of public legacy SERP records for the v1 website.

Run inside the existing web container with DATABASE_URL. Prints JSON only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from realtime.google_legacy import _item, _result

VISIBLE = "NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')"


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def run(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        db.execute("SET LOCAL statement_timeout='30s'")
        db.execute("SET LOCAL work_mem='32MB'")
        web = db.execute("""WITH candidates AS (
            SELECT r.request_id,r.campaign_id,r.source,r.query,r.page,r.classification,
                   r.completed_at,r.response,
                   row_number() OVER (PARTITION BY r.query ORDER BY r.completed_at,r.request_id) AS rn
            FROM serp_requests r WHERE r.source='google_web' AND r.classification='results'
              AND jsonb_typeof(r.response->'organic_results')='array'
              AND jsonb_array_length(r.response->'organic_results')>0 AND """+VISIBLE+"""
        ) SELECT request_id,campaign_id,source,query,page,classification,completed_at,response
          FROM candidates WHERE rn=1 ORDER BY query COLLATE "C",request_id LIMIT 240""").fetchall()
        if len(web)<200:
            raise RuntimeError(f'Only {len(web)} public Web result sets')
        news = db.execute("""SELECT r.request_id,r.campaign_id,r.source,r.query,r.page,r.classification,
                                 r.completed_at,r.response FROM serp_requests r
            WHERE r.source='google_news' AND r.classification='results' AND """+VISIBLE+"""
            ORDER BY r.completed_at,r.request_id LIMIT 30""").fetchall()
        trends = db.execute("""SELECT r.request_id,r.campaign_id,r.source,r.query,r.page,r.classification,
                                   r.completed_at,r.response FROM serp_requests r
            WHERE r.source='google_trends' AND """+VISIBLE+"""
            ORDER BY r.completed_at,r.request_id LIMIT 30""").fetchall()
        repeated = db.execute("""WITH groups AS (
            SELECT r.query,r.page,r.response->'search_parameters'->>'gl' gl,
                   r.response->'search_parameters'->>'hl' hl,count(*) n
            FROM serp_requests r WHERE r.source='google_web' AND r.classification='results'
              AND """+VISIBLE+"""
            GROUP BY 1,2,3,4 HAVING count(*)>=2 ORDER BY n DESC,query LIMIT 8
        ) SELECT r.request_id,r.campaign_id,r.source,r.query,r.page,r.classification,
                 r.completed_at,r.response FROM serp_requests r JOIN groups g
          ON g.query=r.query AND g.page=r.page
          AND (r.response->'search_parameters'->>'gl') IS NOT DISTINCT FROM g.gl
          AND (r.response->'search_parameters'->>'hl') IS NOT DISTINCT FROM g.hl
          WHERE r.source='google_web' AND r.classification='results' AND """+VISIBLE+"""
          ORDER BY r.query,r.completed_at,r.request_id LIMIT 80""").fetchall()
        ids=[r['request_id'] for r in web+news+trends+repeated]
        projection=db.execute("SELECT request_id,ordinal,child_ordinal,rank,url,title FROM sf_analytics_items WHERE request_id=ANY(%s)", (ids,)).fetchall()
        private=db.execute("SELECT count(*) n FROM serp_requests r WHERE r.request_id=ANY(%s) AND EXISTS (SELECT 1 FROM serp_task_options o WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')",(ids,)).fetchone()['n']
    projected={(str(p['request_id']),p['ordinal'],p['child_ordinal']):p for p in projection}
    sample=[]; mismatch=[]; urls=Counter(); url_queries=defaultdict(set)
    web_item_count=unknown_rank=ranked=0
    for row in web:
        mapped=_result(row)
        raw=row['response'].get('organic_results') or []
        if len(mapped['items'])!=len(raw):mismatch.append([str(row['request_id']),'item_count']);continue
        for ordinal,(item,view) in enumerate(zip(raw,mapped['items']),1):
            if not isinstance(item,dict):continue
            web_item_count+=1
            rank=item.get('position') if type(item.get('position')) is int and item['position']>=1 else None
            absolute=item.get('rank_absolute') if type(item.get('rank_absolute')) is int and item['rank_absolute']>=1 else None
            if view['organic_position']!=rank or view['source_absolute_rank']!=absolute:
                mismatch.append([str(row['request_id']),ordinal,'rank_mapping'])
            p=projected.get((str(row['request_id']),ordinal,0))
            if not p or p['rank']!=rank:mismatch.append([str(row['request_id']),ordinal,'projection_rank'])
            unknown_rank+=rank is None;ranked+=rank is not None
            link=view['url']
            if link:urls[link]+=1;url_queries[link].add(row['query'])
            sample.append({'request_id':str(row['request_id']),'source':'google_web','ordinal':ordinal,
                           'query':row['query'],'position':rank,'absolute_rank':absolute,
                           'url':link,'field_hash':digest(item)})
    news_items=[]; trends_topics=[]; news_unresolved=news_missing_date=0
    news_title_urls=Counter();traffic=Counter()
    for row in news:
        for ordinal,item in enumerate(row['response'].get('news_results') or [],1):
            if not isinstance(item,dict):continue
            view=_item(item,'google_news');news_items.append((str(row['request_id']),ordinal,view))
            news_unresolved+=view['link_status']=='google_news_redirect'
            news_missing_date+=view['published_at'] is None
            news_title_urls[(view['title'],view['url'])]+=1
            sample.append({'request_id':str(row['request_id']),'source':'google_news','ordinal':ordinal,
                           'query':row['query'],'feed_position':view['feed_position'],
                           'url':view['url'],'published_at':view['published_at'],'field_hash':digest(item)})
    for row in trends:
        for ordinal,item in enumerate(row['response'].get('trending_searches') or [],1):
            if not isinstance(item,dict):continue
            view=_item(item,'google_trends');trends_topics.append((str(row['request_id']),ordinal,view))
            traffic[view['approx_traffic_label'] or 'unknown']+=1
            sample.append({'request_id':str(row['request_id']),'source':'google_trends','ordinal':ordinal,
                           'topic':view['topic'],'feed_position':view['feed_position'],
                           'traffic_label':view['approx_traffic_label'],
                           'article_count':len(view['articles']),'field_hash':digest(item)})
    repeated_conditions=defaultdict(list)
    for row in repeated:
        p=row['response'].get('search_parameters') or {}
        repeated_conditions[(row['query'],row['page'],p.get('gl'),p.get('hl'))].append(str(row['request_id']))
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'methodology':'google-data-analysis-v1-real-sample-audit',
            'read_only':True,'private_sample_count':private,'web_requests':len(web),
            'web_queries':len({r['query'] for r in web}),'web_result_records':web_item_count,
            'web_ranked':ranked,'web_unknown_rank':unknown_rank,
            'web_mapping_mismatches':mismatch[:20],'web_mapping_mismatch_count':len(mismatch),
            'web_unique_urls':len(urls),'web_duplicate_url_records':sum(n-1 for n in urls.values()),
            'web_cross_query_urls':sum(len(q)>1 for q in url_queries.values()),
            'same_condition_repeated_groups':sum(len(v)>=2 for v in repeated_conditions.values()),
            'news_requests':len(news),'news_result_records':len(news_items),
            'news_unresolved_links':news_unresolved,'news_missing_published_at':news_missing_date,
            'news_duplicate_title_url_candidates':sum(v-1 for v in news_title_urls.values() if v>1),
            'trends_requests':len(trends),'trends_topics':len(trends_topics),
            'trends_traffic_labels':dict(traffic),'sample_record_count':len(sample),
            'sample_manifest_sha256':digest(sample),'sample_manifest':sample}


if __name__=='__main__':
    print(json.dumps(run(os.environ['DATABASE_URL']),ensure_ascii=False,default=str))
