#!/usr/bin/env python3
"""Recompute bounded run metrics from one read-only SQL evidence snapshot."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime
import hashlib,json
from pathlib import Path
import sys
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from realtime.serp_fields import result_items


def compute(audit, *, stop_requested_at=None):
    associations=audit['requests']
    fresh={r['request_id']:r for r in associations if not r['reused']}
    groups=defaultdict(list);sources=defaultdict(Counter)
    cutoff=datetime.fromisoformat(stop_requested_at) if stop_requested_at else None
    for row in sorted(fresh.values(),key=lambda r:(r['completed_at'],r['request_id'])):
        source=sources[row['source']];success=row['classification'] in ('results','empty')
        source['attempts']+=1;source['successful']+=success
        source['empty']+=row['classification']=='empty'
        source['completed_after_stop_requested']+=bool(cutoff and datetime.fromisoformat(row['completed_at'])>cutoff)
        source['result_occurrences']+=len(result_items(row['response'])) if success else 0
        source['topics']+=len(row['response'].get('trending_searches',[])) if success else 0
        source['stored_response_new_links']+=row['response'].get('new_unique_links',0)
        if not success:source['error:'+str(row['error_code'] or row['classification'])]+=1
        groups[(row['source'],row['query'],row['page'])].append(row)
        if success:
            for item in result_items(row['response']):
                source['items_with_title']+=bool(item.get('title'))
                source['items_with_link']+=bool(item.get('link'))
                if row['source']=='google_news':
                    source['unresolved_news_wrappers']+=(urlsplit(item.get('link') or '').hostname or '').lower()=='news.google.com'
                    source['items_with_publication_date']+=bool(item.get('date'))
                    source['items_with_publisher']+=bool(item.get('publisher'))
                if row['source']=='google_images':
                    source['items_with_dimensions']+=bool(item.get('width') and item.get('height'))
                    source['items_with_source_url']+=bool(item.get('source_url'))
                    source['items_with_thumbnail']+=bool(item.get('thumbnail'))
    outcomes=[]
    for (source,query,page),rows in groups.items():
        successes=[r for r in rows if r['classification'] in ('results','empty')]
        first=successes[0] if successes else None
        recovered=bool(first and any(r['classification'] not in ('results','empty') and r['completed_at']<first['completed_at'] for r in rows))
        outcomes.append(dict(source=source,query=query,page=page,attempts=len(rows),ever_succeeded=bool(first),recovered=recovered,
            first_success_completed_at=first['completed_at'] if first else None,
            recovery_seconds=(datetime.fromisoformat(first['completed_at'])-datetime.fromisoformat(rows[0]['completed_at'])).total_seconds() if recovered else None))
    for s,c in sources.items():
        own=[q for q in outcomes if q['source']==s]
        c.update(query_pages=len(own),ever_successful_query_pages=sum(q['ever_succeeded'] for q in own),recovered_query_pages=sum(q['recovered'] for q in own))
        c['new_unique_links']=audit['new_links_by_source'].get(s,0)
        c['success_rate']=c['successful']/c['attempts'] if c['attempts'] else None
    return dict(sources={k:dict(v) for k,v in sources.items()},fresh_attempts=len(fresh),
        successful_attempts=sum(v['successful'] for v in sources.values()),
        cached_associations=sum(bool(r['reused']) for r in associations),
        successful_cached_associations=sum(bool(r['reused']) and r['classification'] in ('results','empty') for r in associations),
        query_pages=len(outcomes),ever_successful_query_pages=sum(q['ever_succeeded'] for q in outcomes),
        recovered_query_pages=sum(q['recovered'] for q in outcomes),outcomes=outcomes,
        new_unique_links=sum(audit['new_links_by_source'].values()),
        active_owned_tasks=sum(c['status']=='active' for c in audit['campaigns']),
        raw_evidence_sha256=hashlib.sha256(json.dumps(audit,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        scope='Owned manual tasks only; fresh means persisted requests excluding association reuse. Upstream cache/freshness remains separately unknown.',
        recovery_latency_basis='persisted completion to first successful completion; excludes duration before first completion')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('audit',type=Path);p.add_argument('output',type=Path);p.add_argument('--stop-requested-at');a=p.parse_args()
    result=compute(json.loads(a.audit.read_text()),stop_requested_at=a.stop_requested_at)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('fresh_attempts','successful_attempts','new_unique_links','cached_associations','query_pages','recovered_query_pages','active_owned_tasks')}))
if __name__=='__main__':main()
