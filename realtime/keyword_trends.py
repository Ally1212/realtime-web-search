"""Keyword interest-over-time via the public Trends Explore widget protocol.

Values are normalized interest indices, never absolute search counts. The caller
owns the session, credentials/proxy policy, persistence and query deadline.
"""
import json
from datetime import datetime,timezone
import requests
from .proxy_pool import ProxyPoolUnavailable, ProxyTransportFailed, request_with_failover

class TrendsError(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def _get(session,path,params,timeout):
    try:
        response=session.get('https://trends.google.com/trends/api/'+path,params=params,
                             timeout=timeout() if callable(timeout) else timeout)
    except requests.RequestException as exc:
        if callable(timeout):
            raise
        raise TrendsError('trends_transport_error') from exc
    if response.status_code==429:raise TrendsError('trends_rate_limited')
    if response.status_code!=200:raise TrendsError('trends_http_'+str(response.status_code))
    text=response.text
    if len(text)>4_000_000:raise TrendsError('trends_response_too_large')
    if text.startswith(")]}'"):text=text.split('\n',1)[-1]
    try:
        parsed=json.loads(text)
        if not isinstance(parsed,dict):raise ValueError()
        return parsed
    except (ValueError,TypeError):raise TrendsError('trends_invalid_json') from None


def collect(session,*,query,region='US',time_range='today 3-m',language='en-US',timeout=25):
    if not isinstance(query,str) or not 1<=len(query.strip())<=300:raise ValueError('Invalid keyword')
    if not isinstance(region,str) or (region and (len(region)!=2 or not region.isalpha())):raise ValueError('Invalid region')
    if time_range not in ('now 7-d','today 1-m','today 3-m','today 12-m','today 5-y'):raise ValueError('Unsupported time range')
    request=dict(comparisonItem=[dict(keyword=query.strip(),geo=region.upper(),time=time_range)],category=0,property='')
    explored=_get(session,'explore',dict(hl=language,tz='0',req=json.dumps(request,separators=(',',':'))),timeout)
    raw_widgets=explored.get('widgets')
    if not isinstance(raw_widgets,list):raise TrendsError('trends_invalid_widgets')
    widgets=[w for w in raw_widgets if isinstance(w,dict) and w.get('id')=='TIMESERIES']
    if len(widgets)!=1:raise TrendsError('trends_timeseries_unavailable')
    widget=widgets[0]
    if not isinstance(widget.get('request'),dict) or not isinstance(widget.get('token'),str) or not widget.get('token'):raise TrendsError('trends_invalid_widget')
    data=_get(session,'widgetdata/multiline',dict(hl=language,tz='0',req=json.dumps(widget['request'],separators=(',',':')),token=widget['token']),timeout)
    default=data.get('default')
    if not isinstance(default,dict):raise TrendsError('trends_invalid_series')
    rows=default.get('timelineData')
    if not isinstance(rows,list) or len(rows)>2000:raise TrendsError('trends_invalid_series')
    points=[];previous=-1
    for row in rows:
        try:
            epoch=int(row['time']);values=row['value'];value=values[0]
            if epoch<=previous or not isinstance(values,list) or len(values)!=1 or type(value) is not int or not 0<=value<=100:raise ValueError()
            partial=row.get('isPartial',False)
            if type(partial) is not bool:raise ValueError()
            stamp=datetime.fromtimestamp(epoch,timezone.utc).isoformat()
        except (KeyError,IndexError,TypeError,ValueError,OverflowError,OSError):raise TrendsError('trends_invalid_point') from None
        points.append(dict(timestamp=stamp,value=value,is_partial=partial,has_data=row.get('hasData',[None])[0] if isinstance(row.get('hasData'),list) and row['hasData'] else None,source_fields=row));previous=epoch
    return dict(schema_version='searchflow-keyword-trends-1',query=query.strip(),region=region.upper(),time_range=time_range,
                measurement='relative_interest_index_0_100',absolute_search_volume=None,points=points,
                point_count=len(points),collection_method='google_trends_explore_widget',
                observed_at=datetime.now(timezone.utc).isoformat(),query_parameters=request)


def collect_saved(discovery,store,serp,campaign_id,query,geo,time_range):
    """Persist a keyword-series snapshot using the existing private SERP path."""
    import time
    from urllib.parse import urlencode
    from .google_feeds import rows
    key=discovery._cache_key('google_trends',query,geo+':keyword-trends-v1:'+time_range)
    cached=serp.cached_feed(key,campaign_id)
    if cached is not None:return rows(cached,'google_trends'),[cached['collection_error']] if cached.get('collection_error') else []
    lease=store.acquire_query_lease(key)
    if not lease:return [],['keyword_trends_busy']
    started=time.monotonic();status=None;error=None
    response=dict(schema_version='searchflow-keyword-trends-response-1',source='google_trends',trending_searches=[],search_parameters=dict(engine='google_trends',q=query,gl=geo.lower(),hl=discovery.language,geo=geo,time=time_range,mode='keyword'))
    def sample(chosen, timeout):
        with requests.Session() as session:
            session.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'})
            if chosen:
                session.trust_env = False
                session.proxies.update(http=chosen[0],https=chosen[0])
            warm=session.get('https://trends.google.com/trends/',timeout=timeout())
            if warm.status_code!=200:raise TrendsError('trends_rate_limited' if warm.status_code==429 else 'trends_http_'+str(warm.status_code))
            return collect(session,query=query,region=geo,time_range=time_range,timeout=timeout)

    try:
        result = request_with_failover(getattr(discovery,'proxy_pool',None),
            getattr(discovery,'proxy_profile','direct'),'trends.google.com',min(discovery.timeout,25),sample,
            on_switch=getattr(discovery,'on_proxy_switch',None))
        status=200
        url='https://trends.google.com/trends/explore?'+urlencode(dict(q=query,geo=geo,date=time_range))
        response.update(keyword_trends=result,capabilities=dict(interest_over_time='supported',absolute_search_volume='not_available'),
            trending_searches=[dict(keyword=query,data_kind='keyword_interest_over_time',numeric_trend_series=result,
                articles=[dict(data_kind='keyword_trend_reference',title=query+' — interest over time',link=url,snippet='Relative Google search interest, 0–100; not search counts.')])])
        classification='results'
    except TrendsError as exc:
        error=exc.code;status=429 if error=='trends_rate_limited' else None
        classification='rate_limited' if status==429 else 'error'
    except requests.RequestException as exc:
        error = ('trends_proxy_unavailable' if isinstance(exc, ProxyPoolUnavailable) else
                 'proxy_transport_failed' if isinstance(exc, ProxyTransportFailed) else
                 'trends_transport_error')
        classification='error'
    except Exception as exc:
        error='trends_collection_'+type(exc).__name__
        classification='error'
    finally:
        try:
            if error:response['collection_error']=error
            request_id=serp.record(campaign_id,source='google_trends',provider='google_trends_explore',query=query,page=1,
                language=discovery.language,results=[],response=response,elapsed_seconds=time.monotonic()-started,
                classification=classification if 'classification' in locals() else 'error',http_status=status,error_code=error)
            if error not in ('trends_proxy_unavailable','proxy_transport_failed'):
                serp.cache_feed(key,request_id,3600 if error is None else 120)
        finally:store.release_query_lease(key,lease)
    return rows(response,'google_trends'),[error] if error else []
