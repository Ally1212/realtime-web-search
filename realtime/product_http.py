"""Versioned customer API. Credentials are accepted only over TLS or loopback."""
from __future__ import annotations

import csv
import hmac
import io
import json
import os
import re
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .product import ProductError, ProductStore, admin_secret, capability_payload, identifier, middle_search_payload
from .google_domain import DomainError


def openapi():
    search = {'type':'object','additionalProperties':False,'required':['query'],'properties':{
        'query':{'type':'string','minLength':1,'maxLength':300},
        'source':{'type':'string','enum':['google_web','google_news','google_trends'],'default':'google_web'},
        'region':{'type':'string','enum':capability_payload()['regions'],'default':'US'},
        'language':{'type':'string','enum':['en','zh'],'default':'en'},
        'max_items':{'type':'integer','minimum':1,'maximum':100,'default':10,
                     'description':'最多交付链接数，不保证搜满；Web按10条估算分页预算，最多10页。'},
        'trends_time_range':{'type':'string','enum':['now 7-d','today 1-m','today 3-m','today 12-m','today 5-y'],'default':'today 3-m'},
        'trends_mode':{'type':'string','enum':['keyword','regional_hotlist'],
                       'description':'默认关键词趋势；地区热榜需显式选择 regional_hotlist。'},
        'provider':{'type':'string','enum':['middle'],'default':'middle'}}}
    def op(summary, method='get', body=None, code='200'):
        v={'summary':summary,'responses':{code:{'description':'成功'},'400':{'description':'参数无效'},'422':{'description':'不支持关键词趋势；地区热榜需显式声明模式'},'401':{'description':'密钥无效'},'429':{'description':'限流、并发或额度已满'}}}
        if body:v['requestBody']={'required':True,'content':{'application/json':{'schema':body}}}
        if summary=='提交搜索':v['parameters']=[{'in':'header','name':'Idempotency-Key','required':True,'schema':{'type':'string','maxLength':120}}]
        return {method:v}
    paths={'/v1/search':op('提交搜索','post',search,'202'), '/v1/jobs':op('最近 30 个任务'),
           '/v1/jobs/{id}':op('查询任务及冻结结果'),'/v1/jobs/{id}/cancel':op('取消任务','post'),
           '/v1/usage':op('UTC 月度额度'),'/v1/keys':{**op('列出密钥'),**op('创建密钥','post',{'type':'object','required':['name'],'properties':{'name':{'type':'string'}}},'201')},
           '/v1/keys/{id}/revoke':op('撤销密钥','post'),'/v1/monitors':{**op('监测列表'),**op('创建暂停的监测','post',{'type':'object','required':['query','domain'],'properties':{**{k:v for k,v in search['properties'].items() if k not in ('source', 'max_items', 'trends_mode')},'name':{'type':'string'},'domain':{'type':'string'},'competitors':{'type':'array','items':{'type':'string'},'maxItems':10},'interval_seconds':{'type':'integer','minimum':3600,'maximum':604800}}},'201')},
           '/v1/monitors/{id}/start':op('开始监测，会消耗空间额度','post'),'/v1/monitors/{id}/pause':op('暂停监测','post'),'/v1/monitors/{id}/report':op('监测排名与竞品变化'),'/v1/jobs/{id}/export':op('导出结果，format=jsonl 或 csv')}
    for path, methods in paths.items():
        if '{id}' in path:
            for v in methods.values():v.setdefault('parameters',[]).append({'in':'path','name':'id','required':True,'schema':{'type':'string','format':'uuid'}})
    document = {'openapi':'3.0.3','info':{'title':'SearchFlow API','version':'1.2.0','description':'异步搜索和完整数据交付；不保证每次新采或上游成功。所有请求还需站点访问凭据。'},'servers':[{'url':'/'}],'security':[{'bearerAuth':[], 'siteAccess':[]}],'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}, 'siteAccess':{'type':'apiKey','in':'header','name':'X-SearchFlow-Access-Key'}, 'adminAuth':{'type':'http','scheme':'bearer','description':'管理密钥；客户密钥不可替代'}}},'paths':paths}
    local_job = {'type':'object','additionalProperties':False,'properties':{
        'mode':{'type':'string','enum':['text','nearby'],'default':'text'},
        'query':{'type':'string','maxLength':300},
        'region':{'type':'string','enum':capability_payload()['regions'],'default':'US'},
        'language':{'type':'string','enum':['en','zh'],'default':'en'},
        'city':{'type':'string','maxLength':120},
        'included_type':{'type':'string','maxLength':80},
        'location':{'type':'object','required':['lat','lng'],'properties':{
            'lat':{'type':'number','minimum':-90,'maximum':90},
            'lng':{'type':'number','minimum':-180,'maximum':180}}},
        'radius_m':{'type':'integer','minimum':1,'maximum':50000,'default':5000}}}
    document['paths'].update({
        '/v1/google/local/capabilities':op('Google Maps 实时数据能力'),
        '/v1/google/local/jobs':op('创建 Google Maps 实时查询','post',local_job,'202'),
        '/v1/google/local/jobs/{id}':op('查询 Google Maps 任务状态'),
        '/v1/google/local/jobs/{id}/results':op('读取 24 小时 Maps 商家快照'),
        '/v1/google/local/jobs/{id}/export':op('下载 Maps 商家快照，format=csv 或 jsonl'),
        '/v1/google/places/{place_id}':op('地点详情暂不可用，返回409'),
    })
    document['paths']['/v1/google/local/jobs']['post']['parameters']=[{'in':'header','name':'Idempotency-Key','required':True,'schema':{'type':'string','maxLength':120}}]
    document['paths']['/v1/google/places/{place_id}']['get']['parameters']=[
        {'in':'path','name':'place_id','required':True,'schema':{'type':'string'}},
        {'in':'query','name':'language','schema':{'type':'string','enum':['en','zh'],'default':'en'}},
        {'in':'query','name':'region','schema':{'type':'string','enum':capability_payload()['regions'],'default':'US'}}]
    from .customer_schema import extend_openapi
    from .maps_schema import extend_openapi as maps_openapi
    return maps_openapi(extend_openapi(document))


def data_openapi():
    """Public catalog for the data-only API workspace.

    Legacy operator, monitoring and analysis routes remain implemented for
    existing deployments, but are deliberately not offered in this catalog.
    """
    document = openapi()
    data_prefixes = (
        '/v1/search', '/v1/jobs', '/v1/data/', '/v1/google/local/',
        '/v1/google/places/', '/v1/google/legacy-data',
        '/v1/google/legacy-delivery', '/v1/usage', '/v1/keys',
    )
    document['paths'] = {
        path: operations for path, operations in document['paths'].items()
        if path.startswith(data_prefixes) and not path.startswith('/v1/google/places/')
    }
    document['info'] = {
        **document['info'],
        'title': 'SearchFlow Data API',
        'description': (
            '搜索、地点、历史记录、文件导出与数据订阅接口。'
            '当前目录不包含数据分析、排名报告、洞察、监测或工作台管理服务。'
        ),
    }
    document['components']['securitySchemes'].pop('adminAuth', None)
    return document


def secure(handler):
    # Only the loopback reverse proxy may assert TLS. Never trust public forwarded headers.
    peer=handler.client_address[0]
    return peer in ('127.0.0.1','::1') and (handler.headers.get('X-Forwarded-Proto')=='https' or not handler.headers.get('X-Forwarded-For'))


def body(handler, *, reject_duplicates=False):
    try:length=int(handler.headers.get('Content-Length','0'))
    except ValueError:raise ProductError('invalid_body','无效的 Content-Length') from None
    if not 0<length<=65536:raise ProductError('invalid_body','JSON 请求大小必须为 1–65536 字节')
    if handler.headers.get('Content-Type','').split(';')[0]!='application/json':
        raise ProductError('invalid_body','请使用 application/json')
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    try:p=json.loads(handler.rfile.read(length),object_pairs_hook=pairs if reject_duplicates else None)
    except (ValueError,UnicodeError):raise ProductError('invalid_body','JSON 格式无效') from None
    if not isinstance(p,dict):raise ProductError('invalid_body','请求必须是 JSON 对象')
    return p


def handle(handler, method):
    parsed=urlparse(handler.path);path=parsed.path
    if path == '/api/quick-request' or path.startswith('/api/quick-request/'):
        from .quick_request_http import handle as quick_handle
        return quick_handle(handler, method)
    if path.startswith('/api/maps/') or path.startswith('/v1/google/local/') or re.fullmatch(r'/v1/google/places/[^/]+', path):
        from .google_local_http import handle as local_handle
        if local_handle(handler, method):
            return True
    if path=='/v1/google' or path.startswith('/v1/google/'):
        from .google_http import handle as google_handle
        return google_handle(handler, method)
    if os.getenv('SEARCHFLOW_PRODUCT_ENABLED')=='true' and legacy_guard(handler, method, path):return True
    if not (path.startswith('/v1/') or path.startswith('/api/product/')):return False
    try:
        if path=='/v1/capabilities' and method=='GET':
            from .data_capability_http import runtime_for
            payload=capability_payload()
            if runtime_for(handler):payload['data_capabilities']['availability']='tenant_dependent'
            handler.send_json(payload);return True
        if path=='/v1/openapi.json' and method=='GET':
            catalog=parse_qs(parsed.query).get('catalog',[''])[0]
            handler.send_json(data_openapi() if catalog=='data' else openapi());return True
        if os.getenv('SEARCHFLOW_PRODUCT_ENABLED')!='true':
            raise ProductError('product_disabled','产品模块尚未启用',503)
        store=ProductStore(handler.config.database_url)
        if path=='/api/product/analytics/summary' and method=='GET':
            from .analytics import overview_summary
            f={k:v[-1] for k,v in parse_qs(parsed.query).items()}
            handler.send_json(overview_summary(store.base,**{k:v for k,v in f.items() if k in ('days','source','region','language')}));return True
        if path=='/api/product/analytics/overview' and method=='GET':
            f={k:v[-1] for k,v in parse_qs(parsed.query).items()}
            handler.send_json(store.analytics_overview(**{k:v for k,v in f.items() if k in ('days','source','region','language','keyword_q','offset')}));return True
        if path=='/api/product/analytics' and method=='GET':
            f={k:v[-1] for k,v in parse_qs(parsed.query).items()}
            handler.send_json(store.history_analysis(**{k:v for k,v in f.items() if k in ('query','domain','region','language','days','competitors','scope')}));return True
        if not secure(handler):raise ProductError('https_required','请通过 HTTPS 使用客户密钥',403)
        auth=handler.headers.get('Authorization','')
        secret=auth[7:] if auth.startswith('Bearer ') else ''
        if path=='/api/product/admin/accounts':
            master=admin_secret()
            if not master or not hmac.compare_digest(secret,master):raise ProductError('unauthorized','需要管理员密钥',401)
            if method=='GET':handler.send_json({'accounts':store.accounts()})
            elif method=='POST':handler.send_json(store.create_account(body(handler)),201)
            else:raise ProductError('method_not_allowed','此方法不受支持',405)
            return True
        if path.startswith('/api/product/'):raise ProductError('not_found','接口不存在',404)
        account=store.authenticate(secret);aid=account['id']
        from .data_capability_http import handle as capability_handle
        if capability_handle(handler,method,path,parse_qs(parsed.query,keep_blank_values=True),account):return True
        from .customer_http import handle as customer_handle, parameters as query_parameters
        if customer_handle(handler, method, path, parse_qs(parsed.query, keep_blank_values=True), store, account):
            return True
        params = query_parameters(parse_qs(parsed.query, keep_blank_values=True))
        status=200
        if path=='/v1/search' and method=='POST':
            jid,reused=store.submit(account,middle_search_payload(body(handler)),handler.headers.get('Idempotency-Key'),handler.config)
            result=dict(id=jid,reused=reused,status_url='/v1/jobs/'+jid);status=202
        elif path=='/v1/search/batch' and method=='POST':
            batch = body(handler)
            if not isinstance(batch, dict) or set(batch) != {'searches'} or not isinstance(batch['searches'], list):
                raise ProductError('invalid_parameter', '批量请求仅支持 searches 数组')
            batch = {'searches': [middle_search_payload(item) for item in batch['searches']]}
            result=store.submit_batch(account,batch,handler.headers.get('Idempotency-Key'),handler.config);status=202
        elif path=='/v1/usage' and method=='GET':result=store.usage(aid)
        elif path=='/v1/jobs' and method=='GET':
            from .customer_jobs import list_jobs
            if params.pop('domain','search')!='search':raise ProductError('invalid_parameter','domain仅支持search/data')
            result=list_jobs(store,aid,params)
        elif path=='/v1/keys' and method=='GET':result={'keys':store.keys(aid)}
        elif path=='/v1/keys' and method=='POST':
            p=body(handler)
            if set(p)!={'name'}:raise ProductError('invalid_parameter','只支持 name')
            result=store.issue_key(aid,p['name']);status=201
        elif path=='/v1/monitors' and method=='GET':result={'monitors':store.monitors(aid)}
        elif path=='/v1/monitors' and method=='POST':result=store.create_monitor(aid,body(handler));status=201
        elif m:=re.fullmatch(r'/v1/keys/([^/]+)/revoke',path):
            if method!='POST':raise ProductError('method_not_allowed','请使用 POST',405)
            store.revoke(aid,m[1]);result={'revoked':True}
        elif m:=re.fullmatch(r'/v1/jobs/([^/]+)(?:/(cancel|export|data|progress|pause|resume))?',path):
            if m[2]=='cancel' and method=='POST':result=store.cancel(aid,m[1])
            elif m[2] in ('pause','resume') and method=='POST':
                result=store.job_action(aid,m[1],m[2])
                if m[2]=='resume':handler.queue.enqueue(result['campaign_id'],priority=True)
            elif method=='GET' and m[2] in ('data','progress'):
                from .customer_data import CustomerDataStore
                data_store=CustomerDataStore(store,aid)
                if set(params)-({'field_contract'} if m[2]=='data' else set()):
                    raise ProductError('invalid_parameter','不支持的查询参数')
                result=(data_store.job_data(m[1],params.get('field_contract','prd-v1'))
                        if m[2]=='data' else data_store.progress(m[1]))
            elif method=='GET' and m[2] in (None,'export'):
                if set(params)-({'format','profile','field_contract'} if m[2]=='export' else {'field_contract'}):
                    raise ProductError('invalid_parameter','不支持的查询参数')
                result=store.job(aid,m[1])
                if params.get('field_contract'):
                    from .customer_data import CustomerDataStore
                    result['data_record']=CustomerDataStore(store,aid).job_data(m[1],params['field_contract'])
                if m[2]=='export':
                    if result['status']!='succeeded':raise ProductError('job_not_ready','任务尚未成功完成',409)
                    profile=params.get('profile','compact')
                    if profile=='full':
                        from .customer_data import CustomerDataStore
                        from .customer_export import export_record
                        record=CustomerDataStore(store,aid).job_data(m[1],params.get('field_contract','prd-v1'))
                        export_record(handler,record,params.get('format','jsonl'))
                    elif profile=='compact':export(handler,result,params.get('format','jsonl'))
                    else:raise ProductError('invalid_parameter','profile 仅支持 compact、full')
                    return True
            else:raise ProductError('method_not_allowed','此方法不受支持',405)
        elif m:=re.fullmatch(r'/v1/monitors/([^/]+)/(start|pause|report)',path):
            if m[2]=='report' and method=='GET':result=store.monitor_report(aid,m[1])
            elif m[2] in ('start','pause') and method=='POST':result=store.monitor_action(aid,m[1],m[2]=='start')
            else:raise ProductError('method_not_allowed','此方法不受支持',405)
        else:raise ProductError('not_found','接口不存在或请求方法不受支持',404)
        handler.send_json(result,status)
    except (ProductError, DomainError) as exc:
        # Stable machine code; request id contains no customer content.
        payload={'error':str(exc),'code':exc.code,'request_id':str(uuid4())}
        if exc.status==429:payload['retry_after_seconds']=60
        if exc.status==429:
            raw=json.dumps(payload,ensure_ascii=False).encode()
            handler.send_response(429)
            for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(raw)),'Cache-Control':'no-store','Retry-After':'60'}.items():handler.send_header(k,v)
            handler.end_headers();handler.wfile.write(raw)
        else:handler.send_json(payload,exc.status)
    except (ValueError,TypeError,UnicodeError):handler.send_json({'error':'请求参数无效','code':'invalid_parameter'},400)
    except (BrokenPipeError,ConnectionResetError):pass
    except Exception as exc:
        print('product API: '+type(exc).__name__,flush=True)
        handler.send_json({'error':'服务暂不可用，请稍后重试','code':'service_unavailable'},503)
    return True


def export(handler,job,fmt):
    data=job['response']['data'];p=job['parameters']
    if fmt=='jsonl':out=(json.dumps(job,ensure_ascii=False,default=str)+'\n').encode();ctype='application/x-ndjson; charset=utf-8'
    elif fmt=='csv':
        output=io.StringIO();writer=csv.writer(output)
        writer.writerow(['query','source','fetched_at','title','link','snippet','position'] + (['image_url','source_page_url','thumbnail_url','width','height','observed_order','record_json'] if p['source']=='google_images' else []))
        field={'google_web':'organic_results','google_news':'news_results','google_trends':'trending_searches','google_images':'image_results'}[p['source']]
        rows=[]
        for item in data.get(field,[]):
            rows.append(item)
            if p['source']=='google_trends':
                # Keep the topic row and the seven-column contract; include saved reports too.
                rows.extend(item.get('articles') or [])
        for r in rows:
            vals=[p['query'],p['source'],job['response']['fetched_at'],r.get('title') or r.get('keyword'),r.get('link'),r.get('snippet'),r.get('position')]
            if p['source']=='google_images':vals += [r.get(k) for k in ('image_url','source_url','thumbnail','width','height','observed_order')] + [json.dumps(r,ensure_ascii=False,separators=(',',':'))]
            writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in vals])
        out=('\ufeff'+output.getvalue()).encode();ctype='text/csv; charset=utf-8'
    else:raise ProductError('invalid_format','仅支持 jsonl/csv')
    handler.send_body(out,ctype)


def legacy_guard(handler, method, path):
    """Keep customer jobs out of unauthenticated legacy task and request endpoints."""
    from .serp_console import ConsoleStore
    if method=='POST' and path.startswith('/api/') and not path.startswith('/api/product/'):
        master=admin_secret()
        auth=handler.headers.get('Authorization','')
        if not secure(handler) or not master or not hmac.compare_digest(auth,'Bearer '+master):
            handler.send_json({'error':'此管理操作需要管理密钥，请在 API 中心连接','code':'admin_key_required'},401);return True
    store=ProductStore(handler.config.database_url)
    # These superseded APIs are absent from the current workbench. They otherwise
    # expose all campaigns and bypass the customer quota and ownership controls.
    if (path.startswith(('/api/campaigns','/api/local-campaigns')) or path in ('/api/stats','/metrics','/api/search','/api/benchmark/free')):
        handler.send_json({'error':'旧任务接口已停用，请使用工作台或 /v1 API','code':'legacy_endpoint_retired'},410);return True
    match=re.fullmatch(r'/api/serp/tasks/([^/]+)/(start|pause|resume|stop)',path)
    try:
        if match:
            with store.base.connect() as db:
                private=db.execute("SELECT 1 FROM serp_task_options WHERE campaign_id=%s AND options->>'customer_private'='true'",(identifier(match[1]),)).fetchone()
            if private:
                handler.send_json({'error':'任务不存在'},404);return True
        if method=='GET' and path=='/api/serp/requests':
            p={k:v[-1] for k,v in parse_qs(urlparse(handler.path).query).items()}
            p['limit']=p.get('limit',50)
            handler.send_json({'requests':ConsoleStore(handler.config.database_url).browse(p,'requests')['rows']});return True
        if method=='GET' and path.startswith('/api/serp/requests/'):
            rid=identifier(path.rsplit('/',1)[1])
            with store.base.connect() as db:
                private=db.execute("SELECT 1 FROM serp_requests r JOIN serp_task_options o ON o.campaign_id=r.campaign_id WHERE r.request_id=%s AND o.options->>'customer_private'='true'",(rid,)).fetchone()
            if private:
                handler.send_json({'error':'任务不存在'},404);return True
    except (ValueError,TypeError):
        handler.send_json({'error':'无效的记录编号'},400);return True
    except Exception:
        handler.send_json({'error':'数据暂不可用'},503);return True
    return False
