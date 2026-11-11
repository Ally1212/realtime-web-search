"""Read-only adapter over existing customer visibility predicates."""
from __future__ import annotations

from .customer_data import CustomerDataStore, selection
from .insights_core import InsightError, digest, fields, integer
from .product import ProductStore


def saved_text_identity(source, normalized, raw):
    # News wrapper URLs can later become publisher URLs. Count identical stored
    # text once, while retaining every occurrence and URL in the frozen input.
    if source == 'google_news':
        return digest(['news_exact_saved_text_v1', raw])
    return digest([normalized, raw]) if normalized else None


class SavedSearchSource:
    def __init__(self, dsn):
        self.store=ProductStore(dsn)

    def load(self,tenant,filters):
        fields(filters,('scope','source','query','from','to','country','language','request_limit'))
        p=dict(filters)
        request_limit=integer(p.pop('request_limit',20),'请求上限',1,100)
        p.setdefault('scope','public');p['field_contract']='prd-v1'
        data=CustomerDataStore(self.store,tenant)
        requests_list=[]
        remaining=request_limit
        while remaining:
            p['limit']=str(min(50,remaining))
            result=data.requests(p)
            requests_list.extend(result['requests'])
            remaining-=len(result['requests'])
            if not result['has_more'] or not result['requests']:
                break
            p['cursor']=result['next_cursor']
        result['requests']=requests_list
        rows=[];requests=[]
        for request in result['requests']:
            requests.append(request['request_id'])
            for item in request['occurrences']:
                title=item.get('title') if isinstance(item.get('title'),str) else ''
                snippet=item.get('snippet') if isinstance(item.get('snippet'),str) else ''
                raw=title+('\n'+snippet if snippet and snippet!=title else '')
                url=item.get('url') or {}
                normalized=url.get('normalized_url')
                rows.append(dict(source_id=item['occurrence_id'],source=request['source'],
                    native_id=saved_text_identity(request['source'],normalized,raw),
                    request_id=request['request_id'],occurrence_id=item['occurrence_id'],
                    text_raw=raw,url=url.get('original_url'),content_kind=item.get('result_type'),
                    market=request.get('query_conditions',{}).get('gl'),
                    language=request.get('query_conditions',{}).get('hl'),
                    title=title or None,snippet=snippet or None,query=request.get('query'),
                    collected_at=request.get('completed_at'),
                    collection_time_basis='request_completed_at' if request.get('completed_at') else None,
                    completed_at=request.get('completed_at'),received_at=request.get('received_at'),
                    requested_at=request.get('requested_at'),
                    source_observed_at=request.get('source_observed_at'),
                    source_record=item,
                    query_conditions=request.get('query_conditions'),
                    source_response_hash=request.get('source_response_hash'),
                    published_at=(item.get('publication') or {}).get('published_at')))
            if len(rows)>5000:
                raise InsightError('dataset_too_large','当前请求包含超过5000条结果，请缩小请求上限或筛选范围')
        if not rows:
            raise InsightError('no_source_text','选中的已存请求没有可分析的标题或摘要，请增加请求上限或调整筛选条件',409)
        return rows,dict(kind='saved_search',source_scope=result['scope'],request_ids=requests,
            source_response_hashes={r['request_id']:r.get('source_response_hash') for r in result['requests']},
            request_count=len(requests),request_limit=request_limit,has_more_requests=result['has_more'],
            sample_selection='latest_saved_requests_with_fixed_cap',
            news_statistics_unit='distinct_exact_saved_text_not_unique_articles',
            text_basis='saved_title_and_snippet_not_full_reviews',provenance=result['provenance'])

    def validate(self,tenant,manifest):
        if manifest.get('kind')!='saved_search':
            return
        ids=manifest['request_ids']
        where,args,_,_,_=selection({'scope':manifest['source_scope']},tenant)
        with self.store.base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='10s'")
            count=db.execute('SELECT count(*) AS n FROM serp_requests r WHERE r.request_id=ANY(%s::uuid[]) AND '+
                ' AND '.join(where),[ids]+args).fetchone()['n']
        if count!=len(ids):
            raise InsightError('source_access_revoked','来源访问范围已改变，停止展示此数据集与派生报告',403)
