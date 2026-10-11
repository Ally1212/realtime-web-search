"""Whale link-only integration. Independent additive tables; never migrate articles."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import requests
from psycopg.types.json import Jsonb

from .campaign_store import CampaignStore
from .serp_fields import normalize_serp_url, public_serp_url, result_items
from .serp_console import ConsoleStore, options, sources

SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_serp_settings (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), uploads_enabled boolean NOT NULL DEFAULT false,
 claims_enabled boolean NOT NULL DEFAULT false, runtime jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO whale_serp_settings(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS whale_serp_jobs (
 id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('upload','platform')),
 campaign_id uuid REFERENCES campaigns(id), platform_task_id text UNIQUE,
 automatic boolean NOT NULL DEFAULT false, reported boolean NOT NULL DEFAULT false, pause_origin text,
 status text NOT NULL CHECK(status IN ('active','paused','stopped','succeeded','failed')),
 specification jsonb NOT NULL, discovery_done boolean NOT NULL DEFAULT false,
 started_at timestamptz, last_error text, remote_control text NOT NULL DEFAULT 'none',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS whale_serp_auto_job ON whale_serp_jobs(campaign_id) WHERE automatic;
CREATE TABLE IF NOT EXISTS whale_serp_task_uploads (campaign_id uuid PRIMARY KEY REFERENCES campaigns(id),enabled boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS whale_serp_outbox (
 id bigserial PRIMARY KEY, dataset_id text NOT NULL, record_key text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','queued','accepted','duplicate','rejected')),
 attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_expires_at timestamptz, receipt jsonb, last_error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(dataset_id,record_key)
);
CREATE INDEX IF NOT EXISTS whale_serp_outbox_due ON whale_serp_outbox(next_attempt_at,id) WHERE state='pending';
CREATE TABLE IF NOT EXISTS whale_serp_job_items (
 job_id uuid REFERENCES whale_serp_jobs(id), outbox_id bigint REFERENCES whale_serp_outbox(id),
 PRIMARY KEY(job_id,outbox_id)
);
"""
TERMINAL_RECEIPTS = {'queued', 'accepted', 'duplicate'}
TASK_TYPES = ('keyword_search', 'backfill', 'content_detail')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def link_message(row, item, config, platform_task_id=None):
    """A search observation, never label the snippet as article body/publication time."""
    url = normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
    if not public_serp_url(url):
        raise ValueError('结果链接不是有效公开 HTTP 地址')
    digest = hashlib.sha256(url.encode()).hexdigest()
    received = row['completed_at'].isoformat() if hasattr(row['completed_at'], 'isoformat') else str(row['completed_at'])
    observed = {k: v for k, v in item.items() if k != 'is_new_url'}
    metadata = dict(result=observed, query=row['query'], source=row['source'], page=row['page'],
                    request_id=str(row['request_id']), campaign_id=str(row['campaign_id']),
                    search_parameters=row['response'].get('search_parameters', {}))
    metadata['request_metadata'] = row['response'].get('search_metadata',{})
    for field in ('serp_features','search_information','pagination','module_status'):
        if field in row['response']:metadata[field]=row['response'][field]
    if row['source']=='google_trends':
        for topic in row['response'].get('trending_searches') or []:
            if any((a.get('normalized_link') or a.get('link')) == (item.get('normalized_link') or item.get('link')) for a in topic.get('articles') or []):
                metadata['trend']={k:v for k,v in topic.items() if k!='articles'};break
    # Request/campaign IDs and observation time do not change retry identity.
    event = canonical_hash(dict(item=observed, query=row['query'], source=row['source'], page=row['page']))
    task_part = ':' + canonical_hash(platform_task_id)[:16] if platform_task_id else ''
    record_key = f'{config.whale_source_platform}:serp:{digest}:{event}{task_part}'
    content = dict(external_content_id='url:'+digest, content_type='search_result',
                   source_url=url, canonical_url=url, status='published')
    capabilities = ['identity']
    if item.get('title'):
        content['title'] = item['title']; capabilities.append('title')
    # A News date may be known; Web/Trends observation time is not publication time.
    published = item.get('date')
    if published:
        try:
            dt = datetime.fromisoformat(str(published).replace('Z', '+00:00'))
            if dt.tzinfo is not None: content['published_at'] = dt.isoformat()
        except (ValueError, TypeError): pass
    source = dict(source_platform=config.whale_source_platform, source_name=config.whale_source_name+'.serp',
                  source_record_key=record_key, payload_hash='sha256:'+event, received_at=received,
                  trace_id=str(row['request_id']))
    if platform_task_id: source['collection_task_id'] = platform_task_id
    message = dict(schema_version='whale.ingest.v1', dataset_id=config.whale_dataset_id, source=source,
                   discovery=dict(collector=config.whale_agent_id, acquisition_type='crawler',
                                  source_url=url, discovered_at=received, metadata=metadata),
                   content=content, provided_capabilities=capabilities)
    return record_key, message


def bounded_int(value, default, low, high, name):
    if value is None: return default
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} 必须为 {low}–{high} 的整数')
    return value


def task_spec(task, config):
    if task.get('task_type') not in TASK_TYPES or task['task_type'] not in config.whale_supported_task_types:
        raise ValueError('任务类型未在当前采集器授权范围内')
    if task.get('dataset_id') != config.whale_dataset_id or task.get('source_platform') != config.whale_source_platform:
        raise ValueError('任务数据集或来源与采集器授权不匹配')
    if set(task.get('required_capabilities') or []) - {'identity', 'title'}:
        raise ValueError('链接模式仅提供 identity/title，未启用正文或其他数据能力')
    p = task.get('payload') or {}
    if not isinstance(p, dict): raise ValueError('payload 必须为对象')
    allowed = {'keyword','keywords','query','aliases','sources','options','max_items','daily_target',
               'max_pages','page_size','proxy_profile','max_duration_minutes','urls','region','language',
               'since','until','test_purpose'}
    if set(p)-allowed: raise ValueError('未支持的任务参数：'+','.join(sorted(set(p)-allowed)))
    words = p.get('keywords') or ([p.get('keyword') or p.get('query')] if p.get('keyword') or p.get('query') else [])
    if isinstance(words, str): words = [words]
    if not isinstance(words,list) or not isinstance(p.get('aliases',[]),list): raise ValueError('keywords/aliases 必须为列表')
    words = words + p.get('aliases',[])
    if not isinstance(words, list) or len(words)>50 or any(not isinstance(w,str) or not w.strip() or len(w)>300 for w in words):
        raise ValueError('关键词必须为 1–50 条有效字符串')
    words = list(dict.fromkeys(w.strip() for w in words))
    selected = sources(p.get('sources', ['google_web']))
    urls = p.get('urls') or []
    if task['task_type']=='content_detail':
        if not isinstance(urls,list) or not 1<=len(urls)<=1000 or any(not isinstance(u,str) or not public_serp_url(u) for u in urls):
            raise ValueError('content_detail 需要 1–1000 个有效 urls；链接模式返回本地已保存的搜索详情')
    elif not words and selected != ['google_trends']:
        raise ValueError('Web/News 任务缺少关键词')
    opts = dict(p.get('options') or {})
    if 'region' in p: opts['web_region'] = p['region']
    if 'language' in p: opts['web_language'] = p['language']
    opts = options(opts)
    pages = bounded_int(p.get('max_pages'),1,1,11,'max_pages')
    # Platform logical pages are expanded into observed native ten-result Google pages.
    page_size=bounded_int(p.get('page_size'),10,1,100,'page_size')
    google_pages=(pages*page_size+9)//10
    profile=p.get('proxy_profile',config.default_proxy_profile)
    if profile not in ('private','public','direct'): raise ValueError('无效 proxy_profile')
    for key in ('since','until'):
        if p.get(key):
            dt=datetime.fromisoformat(str(p[key]).replace('Z','+00:00'))
            if not dt.tzinfo: raise ValueError(key+' 必须带时区')
    if p.get('since') and p.get('until') and datetime.fromisoformat(str(p['since']).replace('Z','+00:00'))>=datetime.fromisoformat(str(p['until']).replace('Z','+00:00')):
        raise ValueError('since 必须早于 until')
    return dict(keywords=words or ['当前热门趋势'], sources=selected, options=opts, urls=urls,
                max_items=bounded_int(p.get('max_items',p.get('daily_target')),100,1,10000,'max_items'),
                max_pages=pages, page_size=page_size, google_pages=google_pages, proxy_profile=profile,
                max_duration_minutes=bounded_int(p.get('max_duration_minutes'),15,1,1440,'max_duration_minutes'),
                since=p.get('since'),until=p.get('until'), task_type=task['task_type'])


class WhaleSerpStore:
    def __init__(self, dsn): self.store=CampaignStore(dsn,initialize=False)

    def initialize(self):
        with self.store.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261011,1)'); db.execute(SCHEMA)

    def settings(self):
        with self.store.connect() as db: return db.execute('SELECT * FROM whale_serp_settings WHERE id=true').fetchone()

    def save_settings(self,payload):
        if not isinstance(payload,dict) or not payload or set(payload)-{'uploads_enabled','claims_enabled'} or any(type(v) is not bool for v in payload.values()):
            raise ValueError('上传/领取开关必须为布尔值')
        with self.store.connect() as db:
            db.execute('SELECT id FROM whale_serp_settings WHERE id=true FOR UPDATE')
            for k,v in payload.items(): db.execute('UPDATE whale_serp_settings SET '+k+'=%s,updated_at=clock_timestamp() WHERE id=true',(v,))
            if payload.get('claims_enabled') is False:
                db.execute("UPDATE campaigns SET status='paused',updated_at=now() WHERE id IN (SELECT campaign_id FROM whale_serp_jobs WHERE kind='platform' AND status='active')")
                db.execute("UPDATE whale_task_runs SET status='paused',updated_at=now() WHERE task_id IN (SELECT platform_task_id FROM whale_serp_jobs WHERE kind='platform' AND status='active')")
                db.execute("UPDATE whale_serp_jobs SET status='paused',pause_origin='settings',updated_at=now() WHERE kind='platform' AND status='active'")
        return self.settings()

    def runtime(self, **values):
        with self.store.connect() as db:
            db.execute('UPDATE whale_serp_settings SET runtime=runtime||%s WHERE id=true',(Jsonb(values),))

    def get_job(self,jid):
        with self.store.connect() as db:
            r=db.execute('SELECT * FROM whale_serp_jobs WHERE id=%s',(str(UUID(str(jid))),)).fetchone()
            if r: r.update(self.counts(db,r['id']))
            return r

    @staticmethod
    def counts(db,jid):
        rows=db.execute('SELECT o.state,count(*) n FROM whale_serp_job_items i JOIN whale_serp_outbox o ON o.id=i.outbox_id WHERE i.job_id=%s GROUP BY o.state',(jid,)).fetchall()
        c={k:0 for k in ('pending','queued','accepted','duplicate','rejected')};c.update({r['state']:r['n'] for r in rows})
        return dict(counts=c,collected=sum(c.values()),received=c['queued']+c['accepted'],duplicates=c['duplicate'])

    def active_jobs(self):
        with self.store.connect() as db:
            rows=db.execute("SELECT * FROM whale_serp_jobs WHERE status IN ('active','paused') OR (kind='platform' AND status='stopped' AND NOT reported) ORDER BY created_at,id LIMIT 100").fetchall()
            for r in rows:r.update(self.counts(db,r['id']))
        return rows

    def jobs(self,limit=20,offset=0):
        limit=bounded_int(int(limit),20,1,100,'limit');offset=max(0,int(offset))
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM whale_serp_jobs ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',(limit,offset)).fetchall()
            for r in rows: r.update(self.counts(db,r['id']))
            total=db.execute('SELECT count(*) n FROM whale_serp_jobs').fetchone()['n']
        return dict(jobs=rows,total=total,limit=limit,offset=offset)

    def action(self,jid,operation,origin='local'):
        valid={'pause':('active',),'resume':('paused',),'stop':('active','paused','failed'),'retry':('failed',)}
        if operation not in valid: raise ValueError('无效操作')
        with self.store.connect() as db:
            r=db.execute('SELECT * FROM whale_serp_jobs WHERE id=%s FOR UPDATE',(str(UUID(str(jid))),)).fetchone()
            if not r: return None
            if r['status'] not in valid[operation]: raise ValueError('当前状态不能执行该操作')
            status={'pause':'paused','resume':'active','stop':'stopped','retry':'active'}[operation]
            db.execute('UPDATE whale_serp_jobs SET status=%s,pause_origin=%s,last_error=NULL,updated_at=now() WHERE id=%s',(status,origin if status=='paused' else None,r['id']))
            if r['kind']=='platform':
                db.execute('UPDATE campaigns SET status=%s,updated_at=now() WHERE id=%s',('active' if status=='active' else 'paused' if status=='paused' else 'stopped',r['campaign_id']))
                db.execute('UPDATE whale_task_runs SET status=%s,updated_at=now() WHERE task_id=%s',('running' if status=='active' else 'paused' if status=='paused' else 'canceled',r['platform_task_id']))
            if operation=='stop' and r['automatic']:
                db.execute('UPDATE whale_serp_task_uploads SET enabled=false WHERE campaign_id=%s',(r['campaign_id'],))
            if operation=='retry':
                db.execute("UPDATE whale_serp_outbox SET state='pending',last_error=NULL,next_attempt_at=now() WHERE state='rejected' AND id IN (SELECT outbox_id FROM whale_serp_job_items WHERE job_id=%s)",(r['id'],))
        return self.get_job(jid)

    def finish(self,jid,status,error=None):
        with self.store.connect() as db:
            r=db.execute('UPDATE whale_serp_jobs SET status=%s,last_error=%s,reported=true,updated_at=now() WHERE id=%s AND status=\'active\' RETURNING campaign_id,kind',(status,error,jid)).fetchone()
            if r and r['kind']=='platform':
                db.execute("UPDATE campaigns SET status='stopped',updated_at=now() WHERE id=%s",(r['campaign_id'],))
                db.execute('UPDATE whale_task_runs SET status=%s,updated_at=now() WHERE campaign_id=%s',(status if status in ('failed','succeeded') else 'canceled',r['campaign_id']))

    def enqueue(self,payload,config):
        allowed={'campaign_id','source','since','until','q','new_only','urls','limit','start'}
        if not isinstance(payload,dict) or set(payload)-allowed: raise ValueError('无效上传筛选字段')
        limit=bounded_int(payload.get('limit'),100,1,10000,'limit')
        if type(payload.get('start',True)) is not bool: raise ValueError('start 必须为布尔值')
        filters={k:v for k,v in payload.items() if k not in ('urls','limit','start')}
        if 'new_only' in filters and type(filters['new_only']) is not bool: raise ValueError('new_only 必须为布尔值')
        filters['new_only']=str(filters.get('new_only',True)).lower()
        selected=payload.get('urls')
        if selected is not None and (not isinstance(selected,list) or not 1<=len(selected)<=1000 or any(not isinstance(u,str) or not public_serp_url(u) for u in selected)):
            raise ValueError('urls 必须为 1–1000 个有效链接')
        jid=str(uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO whale_serp_jobs(id,kind,status,specification,discovery_done) VALUES(%s,\'upload\',%s,%s,true)',(jid,'active' if payload.get('start',True) else 'paused',Jsonb(dict(filters=filters,urls=selected,limit=limit))))
            self.stage(db,jid,config,filters,selected,limit)
            if not self.counts(db,jid)['collected']: raise ValueError('没有符合筛选条件的已采集链接')
        return self.get_job(jid)

    def stage(self,db,jid,config,filters,selected,limit,task_id=None):
        clauses,params=ConsoleStore.filters(filters)
        where=' AND '.join(clauses) if clauses else 'true'
        selected_set={normalize_serp_url(u) for u in selected} if selected else None
        existing=db.execute("SELECT o.payload->'content'->>'canonical_url' url FROM whale_serp_job_items i JOIN whale_serp_outbox o ON o.id=i.outbox_id WHERE i.job_id=%s",(jid,)).fetchall()
        seen={r['url'] for r in existing}
        remaining=max(0,limit-len(existing))
        if not remaining:return
        # A server-side cursor bounds memory and can stop after enough matching links.
        with db.cursor(name='whale_stage_'+uuid4().hex) as cursor:
            cursor.execute('SELECT r.* FROM serp_requests r WHERE '+where+" AND r.classification IN ('results','empty') ORDER BY r.completed_at DESC,r.request_id DESC",params)
            for row in cursor:
                for item in result_items(row['response']):
                    raw=item.get('normalized_link') or item.get('link')
                    if not isinstance(raw,str) or not public_serp_url(raw):continue
                    url=normalize_serp_url(raw)
                    if url in seen or (selected_set is not None and url not in selected_set):continue
                    if filters.get('new_only')=='true' and not item.get('is_new_url'):continue
                    q=str(filters.get('q') or '').casefold()
                    if q and not any(q in str(v or '').casefold() for v in [row['query'],item.get('title'),item.get('snippet'),raw]):continue
                    key,msg=link_message(row,item,config,task_id)
                    out=db.execute('INSERT INTO whale_serp_outbox(dataset_id,record_key,payload) VALUES(%s,%s,%s) ON CONFLICT(dataset_id,record_key) DO UPDATE SET record_key=excluded.record_key RETURNING id',(config.whale_dataset_id,key,Jsonb(msg))).fetchone()
                    db.execute('INSERT INTO whale_serp_job_items(job_id,outbox_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(jid,out['id']))
                    seen.add(url);remaining-=1
                    if not remaining:return

    def stage_subscription(self,cid,config):
        with self.store.connect() as db:
            flag=db.execute('SELECT enabled FROM whale_serp_task_uploads WHERE campaign_id=%s FOR UPDATE',(cid,)).fetchone()
            if not flag or not flag['enabled']:return
            job=db.execute('SELECT id,status FROM whale_serp_jobs WHERE automatic AND campaign_id=%s FOR UPDATE',(cid,)).fetchone()
            if not job:
                jid=str(uuid4())
                db.execute("INSERT INTO whale_serp_jobs(id,kind,campaign_id,automatic,status,specification,discovery_done) VALUES(%s,'upload',%s,true,'active',%s,true)",(jid,cid,Jsonb({'campaign_id':cid,'new_only':False,'limit':10000})))
            else:jid=job['id']
            self.stage(db,jid,config,{'campaign_id':cid,'new_only':'false'},None,10000)
            if job and job['status']=='succeeded':
                pending=self.counts(db,jid)['counts']['pending']
                if pending:db.execute("UPDATE whale_serp_jobs SET status='active',updated_at=now() WHERE id=%s",(jid,))

    def receipts(self,jid,limit=50,offset=0):
        with self.store.connect() as db:
            return db.execute('SELECT o.id,o.state,o.attempts,o.last_error,o.next_attempt_at,o.receipt,o.payload FROM whale_serp_job_items i JOIN whale_serp_outbox o ON o.id=i.outbox_id WHERE i.job_id=%s ORDER BY o.id LIMIT %s OFFSET %s',(str(UUID(str(jid))),min(max(int(limit),1),100),max(int(offset),0))).fetchall()

    def reserve(self,jid,batch_size):
        token=str(uuid4())
        with self.store.connect() as db:
            rows=db.execute("SELECT o.id FROM whale_serp_outbox o JOIN whale_serp_job_items i ON i.outbox_id=o.id JOIN whale_serp_jobs j ON j.id=i.job_id WHERE j.id=%s AND j.status='active' AND o.state='pending' AND o.next_attempt_at<=now() AND (o.lease_expires_at IS NULL OR o.lease_expires_at<=now()) AND (SELECT uploads_enabled FROM whale_serp_settings WHERE id=true) ORDER BY o.id FOR UPDATE OF o SKIP LOCKED LIMIT %s",(jid,batch_size)).fetchall()
            ids=[r['id'] for r in rows]
            if not ids: return []
            return db.execute("UPDATE whale_serp_outbox SET lease_token=%s,lease_expires_at=now()+interval '120 seconds',attempts=attempts+1 WHERE id=ANY(%s) RETURNING *",(token,ids)).fetchall()

    def settle(self,rows,receipts=None,error=None,retryable=True,retry_after=None):
        with self.store.connect() as db:
            for index,row in enumerate(rows):
                receipt=receipts[index] if receipts is not None else None
                state=receipt.get('receipt_status') if isinstance(receipt,dict) else None
                if state not in TERMINAL_RECEIPTS:
                    state='pending' if retryable and receipt is None else 'rejected'
                reason=None if state in TERMINAL_RECEIPTS else error or 'platform_rejected'
                delay=min(3600,2**min(row['attempts'],10))
                if retry_after is not None:
                    try:delay=max(delay,min(86400,max(0,float(retry_after))))
                    except (ValueError,TypeError):pass
                db.execute("UPDATE whale_serp_outbox SET state=%s,receipt=%s,last_error=%s,lease_token=NULL,lease_expires_at=NULL,next_attempt_at=now()+(%s*interval '1 second'),updated_at=now() WHERE id=%s AND lease_token=%s",(state,Jsonb(receipt) if receipt else None,reason,delay,row['id'],row['lease_token']))

    def import_task(self,task,config):
        taskid=str(task.get('id') or task.get('task_id') or '')
        if not taskid or len(taskid)>200 or not all(c.isalnum() or c in '_-' for c in taskid): raise ValueError('缺少有效平台任务 ID')
        spec=task_spec(task,config)
        with self.store.connect() as db:
            current=db.execute('SELECT claims_enabled FROM whale_serp_settings WHERE id=true FOR UPDATE').fetchone()
            enabled=current['claims_enabled']
            db.execute('SELECT pg_advisory_xact_lock(20261011,2)')
            prior=db.execute('SELECT id,status,pause_origin FROM whale_serp_jobs WHERE platform_task_id=%s',(taskid,)).fetchone()
            if prior:
                if prior['status']=='paused' and prior['pause_origin']!='local' and enabled:
                    db.execute("UPDATE whale_serp_jobs SET status='active',updated_at=now() WHERE id=%s",(prior['id'],))
                    db.execute("UPDATE campaigns SET status='active',updated_at=now() WHERE id=(SELECT campaign_id FROM whale_serp_jobs WHERE id=%s)",(prior['id'],))
                return str(prior['id'])
            cid,jid=str(uuid4()),str(uuid4())
            db.execute("INSERT INTO campaigns(id,query,aliases,sources,daily_target,proxy_profile,status) VALUES(%s,%s,%s,%s,%s,%s,'active')",(cid,spec['keywords'][0],Jsonb(spec['keywords'][1:]),Jsonb(spec['sources']),spec['max_items'],spec['proxy_profile']))
            # Existing local workers exclude Whale campaigns; only this link runner executes them.
            db.execute("INSERT INTO whale_task_runs(task_id,campaign_id,dataset_id,source_platform,task_type,payload,status) VALUES(%s,%s,%s,%s,%s,%s,'running')",(taskid,cid,config.whale_dataset_id,config.whale_source_platform,task['task_type'],Jsonb(task.get('payload') or {})))
            db.execute('INSERT INTO serp_task_options(campaign_id,options) VALUES(%s,%s)',(cid,Jsonb(spec['options'])))
            db.execute("INSERT INTO whale_serp_jobs(id,kind,campaign_id,platform_task_id,status,specification,started_at) VALUES(%s,'platform',%s,%s,'active',%s,now())",(jid,cid,taskid,Jsonb(spec)))
            if not enabled:
                db.execute("UPDATE whale_serp_jobs SET status='paused',pause_origin='settings' WHERE id=%s",(jid,))
                db.execute("UPDATE campaigns SET status='paused' WHERE id=%s",(cid,))
        return jid


class WhaleLinkClient:
    def __init__(self,config,session=None):
        self.config=config;self.session=session or requests.Session();self.agent_id=config.whale_agent_id
        if not config.whale_collector_api_key: raise ValueError('未配置 Whale Collector Key')
    def post(self,path,payload):
        r=self.session.post(self.config.whale_base_url.rstrip('/')+path,json=payload,
                            headers={'Authorization':'Bearer '+self.config.whale_collector_api_key},timeout=20)
        if not r.ok:
            e=RuntimeError('Whale HTTP '+str(r.status_code));e.status_code=r.status_code
            e.retry_after=r.headers.get('Retry-After');raise e
        return r.json()
    def register(self):
        types=[t for t in self.config.whale_supported_task_types if t in TASK_TYPES]
        r=self.post('/v1/collectors/register',dict(name='Google SERP Link Collector',agent_type=self.config.whale_source_platform,
           runtime='python',version='serp-1',supported_platforms=[self.config.whale_source_platform],supported_task_types=types,
           declared_capabilities=['identity','title'],max_concurrency=1,current_load=0,
           metadata={'mode':'serp_links','sources':['google_web','google_news','google_trends'],'body':False}))
        self.agent_id=str(r.get('agent_id') or (r.get('data') or {}).get('agent_id') or (r.get('agent') or {}).get('id') or self.agent_id)
    def heartbeat(self,load):return self.post('/v1/collectors/heartbeat',dict(agent_id=self.agent_id,current_load=load))
    def claim(self):
        r=self.post('/v1/collection/tasks/claim',dict(agent_id=self.agent_id,declared_capabilities=['identity','title'],limit=1))
        return r.get('tasks') or []
    def task_heartbeat(self,job):
        stats=dict(collected_count=job['collected'],ingested_count=job['received'],duplicate_count=job['duplicates'])
        cursor=json.dumps(dict(job_id=str(job['id']),campaign_id=str(job['campaign_id']),local_status=job['status'],counts=job['counts']))
        return self.post('/v1/collection/tasks/'+job['platform_task_id']+'/heartbeat',dict(agent_id=self.agent_id,cursor=cursor,stats=stats))
    def complete(self,job):
        return self.post('/v1/collection/tasks/'+job['platform_task_id']+'/complete',dict(agent_id=self.agent_id,
            collected_count=job['collected'],ingested_count=job['received'],duplicate_count=job['duplicates'],cursor=str(job['id'])))
    def fail(self,task_id,message,retry=True):
        return self.post('/v1/collection/tasks/'+task_id+'/fail',dict(agent_id=self.agent_id,message=message,cursor='',retry=retry))


class WhaleSerpRuntime:
    def __init__(self,config,store=None,client=None):
        self.config=config;self.store=store or WhaleSerpStore(config.database_url)
        self.client=client;self.registered=False;self.searches={};self.pool=ThreadPoolExecutor(max_workers=1)
    def flush(self,job):
        rows=self.store.reserve(job['id'],min(max(self.config.whale_ingest_batch_size,1),100))
        if not rows:return
        try:
            receipts=self.client.post('/v1/documents/bulk',[r['payload'] for r in rows])
            if not isinstance(receipts,list) or len(receipts)!=len(rows) or any(not isinstance(r,dict) for r in receipts):
                self.store.settle(rows,error='unexpected_bulk_response',retryable=True);return
            self.store.settle(rows,receipts)
        except Exception as e:
            code=getattr(e,'status_code',None)
            retry=code is None or code in (408,425,429) or code>=500
            self.store.settle(rows,error='Whale HTTP '+str(code) if code else type(e).__name__,retryable=retry,retry_after=getattr(e,'retry_after',None))
            self.store.runtime(last_error='Whale HTTP '+str(code) if code else type(e).__name__)
    def collect(self,job):
        from .serp_worker import run_serp_campaign
        spec=job['specification']
        c=replace(self.config,google_web_max_pages=spec['google_pages'],google_web_pages_per_batch=min(3,spec['google_pages']),content_fetch_enabled=False)
        run_serp_campaign(str(job['campaign_id']),c)
    def stage_platform(self,job):
        spec=job['specification']
        with self.store.store.connect() as db:
            if spec['task_type']=='content_detail':
                filters={'new_only':'false'};selected=spec['urls']
            else:
                filters={'campaign_id':str(job['campaign_id']),'new_only':'false'};selected=None
            if spec.get('since'):filters['since']=spec['since']
            if spec.get('until'):filters['until']=spec['until']
            limit=spec['max_items']
            if spec['sources']==['google_web'] and spec['task_type']!='content_detail':limit=min(limit,len(spec['keywords'])*spec['max_pages']*spec['page_size'])
            self.store.stage(db,job['id'],self.config,filters,selected,limit,job['platform_task_id'])
    def tick(self):
        with self.store.store.connect() as leader:
            if not leader.execute('SELECT pg_try_advisory_xact_lock(20261011,3) acquired').fetchone()['acquired']:return
            self._tick()

    def _tick(self):
        settings=self.store.settings()
        if not self.config.whale_collector_api_key:return
        jobs=self.store.active_jobs()
        for jid,future in list(self.searches.items()):
            job=self.store.get_job(jid)
            if future.done() and job['status']!='active':
                try:future.result();self.stage_platform(job)
                except Exception:pass
                del self.searches[jid]
        active=[j for j in jobs if j['status']=='active']
        platform=[j for j in jobs if j['kind']=='platform' and j['status'] in ('active','paused')]
        notifications=[j for j in jobs if j['kind']=='platform' and j['status']=='stopped' and not j['reported']]
        if not settings['uploads_enabled'] and not settings['claims_enabled'] and not platform and not notifications:return
        if self.client is None:self.client=WhaleLinkClient(self.config)
        if not self.registered:
            self.client.register();self.registered=True
        self.client.heartbeat(len([j for j in platform if j['status']=='active']))
        self.store.runtime(agent_id=self.client.agent_id,connected_at=datetime.now(timezone.utc).isoformat(),last_error=None)
        for job in notifications:
            try:self.client.fail(job['platform_task_id'],'采集任务已停止，历史数据保留',False)
            except Exception as e:
                if getattr(e,'status_code',None) not in (404,409):continue
            with self.store.store.connect() as db:db.execute('UPDATE whale_serp_jobs SET reported=true WHERE id=%s',(job['id'],))
        for job in platform:
            try:reply=self.client.task_heartbeat(job)
            except Exception as e:
                self.store.runtime(last_error='task_heartbeat:'+str(getattr(e,'status_code',type(e).__name__)))
                continue
            control=reply.get('control_signal','none')
            with self.store.store.connect() as db:db.execute('UPDATE whale_serp_jobs SET remote_control=%s WHERE id=%s',(str(control),job['id']))
            if control=='pause' and job['status']=='active':self.store.action(str(job['id']),'pause',origin='remote');continue
            if control in ('cancel','stop'):
                self.store.action(str(job['id']),'stop');continue
            if (control=='resume' or (control=='none' and job['remote_control']=='pause' and job['pause_origin']=='remote')) and job['status']=='paused' and settings['claims_enabled']:
                self.store.action(str(job['id']),'resume')
        for job in active:
            job=self.store.get_job(job['id'])
            if job['status']!='active':continue
            if job['kind']=='platform' and settings['claims_enabled']:
                spec=job['specification'];jid=str(job['id']);future=self.searches.get(jid)
                if future and future.done():
                    try:future.result()
                    except Exception as e:
                        self.client.fail(job['platform_task_id'],type(e).__name__)
                        self.store.finish(job['id'],'failed',type(e).__name__);del self.searches[jid];continue
                    del self.searches[jid];self.stage_platform(job)
                if spec['task_type']=='content_detail':
                    self.stage_platform(job);done=True
                    if not self.store.get_job(job['id'])['collected']:
                        self.client.fail(job['platform_task_id'],'本地没有这些 URL 的已采集链接详情；正文保持关闭',False)
                        self.store.finish(job['id'],'failed','links_not_found');continue
                else:
                    with self.store.store.connect() as db:
                        pages=db.execute('SELECT state FROM google_page_frontier WHERE campaign_id=%s AND enabled',(job['campaign_id'],)).fetchall()
                        feeds=db.execute("SELECT DISTINCT ON (r.source,r.query) r.source,r.query,r.classification FROM serp_requests r JOIN serp_campaign_requests cr USING(request_id) WHERE cr.campaign_id=%s AND r.source<>'google_web' ORDER BY r.source,r.query,r.completed_at DESC",(job['campaign_id'],)).fetchall()
                    expected=[]
                    if 'google_news' in spec['sources']:expected.extend(('google_news',q) for q in spec['keywords'])
                    if 'google_trends' in spec['sources']:expected.append(('google_trends','trending-now'))
                    latest={(r['source'],r['query']):r['classification'] for r in feeds}
                    feeds_done=all(latest.get(pair) in ('results','empty') for pair in expected)
                    web_done='google_web' not in spec['sources'] or (bool(pages) and all(p['state']=='completed' for p in pages))
                    done=web_done and feeds_done
                    timed_out=job['started_at'] and (datetime.now(timezone.utc)-job['started_at']).total_seconds()>spec['max_duration_minutes']*60
                    if timed_out and not done:
                        self.store.store.set_status(str(job['campaign_id']),'paused')
                        self.client.fail(job['platform_task_id'],'任务达到运行时限，分页游标和上传记录保留',True)
                        self.store.finish(job['id'],'failed','duration_limit');continue
                    if job['collected']>=spec['max_items']:done=True
                    if not done and not self.searches and not job['discovery_done']:
                        self.searches[jid]=self.pool.submit(self.collect,job)
                if done:
                    self.store.store.set_status(str(job['campaign_id']),'paused')
                    with self.store.store.connect() as db:db.execute('UPDATE whale_serp_jobs SET discovery_done=true WHERE id=%s',(job['id'],))
            if settings['uploads_enabled']:self.flush(job)
            job=self.store.get_job(job['id'])
            if job['discovery_done'] and not job['counts']['pending']:
                if job['counts']['rejected']:
                    if job['kind']=='platform':self.client.fail(job['platform_task_id'],'Whale 拒收，已保留记录',False)
                    self.store.finish(job['id'],'failed','platform_rejected')
                else:
                    if job['kind']=='platform':self.client.complete(job)
                    self.store.finish(job['id'],'succeeded')
        with self.store.store.connect() as db:
            active_platform=db.execute("SELECT count(*) n FROM whale_serp_jobs WHERE kind='platform' AND status='active'").fetchone()['n']
        if self.store.settings()['claims_enabled'] and not active_platform and not self.searches:
            for task in self.client.claim():
                try:self.store.import_task(task,self.config)
                except ValueError as e:self.client.fail(str(task.get('id') or task.get('task_id')),str(e),False)
    def run(self,stop):
        try:
            while not stop.is_set():
                with self.store.store.connect() as leader:
                    acquired=leader.execute('SELECT pg_try_advisory_lock(20261011,4) acquired').fetchone()['acquired']
                    leader.commit()
                    if not acquired:
                        stop.wait(5);continue
                    try:
                        while not stop.is_set():
                            try:self.tick()
                            except Exception as e:
                                self.registered=False
                                self.store.runtime(last_error='Whale HTTP '+str(getattr(e,'status_code','')) if hasattr(e,'status_code') else type(e).__name__)
                            stop.wait(max(2,min(self.config.whale_heartbeat_seconds,20)))
                    finally:
                        leader.execute('SELECT pg_advisory_unlock(20261011,4)');leader.commit()
        finally:self.pool.shutdown(wait=False,cancel_futures=True)
