"""Shared middle-platform ingestion protocol; no legacy queue or worker."""
from __future__ import annotations
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
import requests
from .serp_fields import normalize_serp_url, public_serp_url, result_items
from .serp_console import options, NEWS_LOCALES
TASK_TYPES = ('live_query',)
LINK_EVENT_IDENTITY_VERSION = 'serp-link-observation-2'

def retry_delay(attempts, retry_after=None, *, minimum=0, now=None):
    delay=max(minimum,min(3600,2**min(max(0,attempts),10)))
    if retry_after is not None:
        try:
            seconds=float(retry_after)
        except (ValueError,TypeError):
            try:
                date=parsedate_to_datetime(str(retry_after))
                seconds=(date-(now or datetime.now(timezone.utc))).total_seconds()
            except (ValueError,TypeError,OverflowError):seconds=0
        if math.isfinite(seconds):delay=max(delay,min(86400,max(0,seconds)))
    return delay+random.uniform(0,min(30,delay*.1))


def task_conflict(response, error=None):
    """Expose only documented task conflict codes, never opaque response text."""
    if error is None:
        error = RuntimeError('Whale HTTP 409')
    error.status_code = 409
    error.whale_retry_after = response.headers.get('Retry-After')
    error.whale_code = ''
    try:
        body = response.json()
    except ValueError:
        return error
    if isinstance(body, dict):
        detail = body.get('error')
        for code in (body.get('code'), detail.get('code') if isinstance(detail, dict) else detail):
            if code in ('live_results_pending', 'task_lease_lost'):
                error.whale_code = code
                break
    return error


def receipt_query(row):
    payload=row['payload'];source=payload['source']
    query=dict(dataset_id=row['dataset_id'],**{k:source[k] for k in ('source_platform','source_name','source_record_key')})
    if (payload.get('dataset_id')!=row['dataset_id'] or source['source_record_key']!=row['record_key']
            or any(not isinstance(v,str) or not v for v in query.values())):
        raise ValueError('Invalid saved receipt scope')
    return query


def processing_receipts(rows, response):
    entries=response.get('receipts') if isinstance(response,dict) else None
    if not isinstance(entries,list) or len(entries)!=len(rows):raise ValueError('Incomplete processing receipt batch')
    result=[]
    for row,entry in zip(rows,entries):
        query=receipt_query(row)
        if not isinstance(entry,dict) or any(entry.get(k)!=v for k,v in query.items()):
            raise ValueError('Processing receipt scope differs')
        if entry.get('status') not in {'unknown','pending','queued','retrying','failed','accepted','duplicate'}:
            raise ValueError('Unknown processing receipt status')
        previous=row.get('receipt') or {}
        if any(k in entry and (not isinstance(entry[k],str) or len(entry[k])>256)
               for k in ('receipt_id','document_id','version_id','persisted_at')):
            raise ValueError('Invalid processing receipt identity fields')
        for field in ('document_id','version_id'):
            if previous.get(field) and entry.get(field) and previous[field]!=entry[field]:
                raise ValueError('Processing receipt version identity differs')
        # Do not persist arbitrary response fields or error text.
        result.append({k:entry[k] for k in (*query,'status','receipt_id','document_id','version_id','persisted_at') if k in entry})
    return result


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def collector_capabilities(platform):
    return ['identity', 'title', 'assets', 'condition:other'] if platform == 'google_images' else ['identity', 'title', 'condition:other'] if platform == 'google_trends' else ['identity', 'title']


def link_message(row, item, config, platform_task_id=None):
    """A search observation with optional image assets; never invent article body."""
    url = (item.get('image_url') or item.get('link') or '') if row['source'] == 'google_images' else normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
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
    # A genuine new saved request is a new observation, even with identical
    # values. Retries bind to the same run/URL/task; content is not event identity.
    # This observation may carry image assets; the bound payload retains the export.
    request_id = str(UUID(str(row['request_id'])))
    identity = dict(version=LINK_EVENT_IDENTITY_VERSION, request_id=request_id,
                    source=row['source'], page=row['page'], url_id=digest,
                    platform_task_id=platform_task_id)
    record_key = f'{config.whale_source_platform}:{LINK_EVENT_IDENTITY_VERSION}:{canonical_hash(identity)}'
    metadata['event_identity_version'] = LINK_EVENT_IDENTITY_VERSION
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
    payload_hash = canonical_hash(dict(metadata=metadata, content=content, received_at=received))
    source = dict(source_platform=config.whale_source_platform, source_name=config.whale_source_name+'.serp',
                  source_record_key=record_key, payload_hash='sha256:'+payload_hash, received_at=received,
                  trace_id=str(row['request_id']))
    if platform_task_id: source['collection_task_id'] = platform_task_id
    message = dict(schema_version='whale.ingest.v1', dataset_id=config.whale_dataset_id, source=source,
                   discovery=dict(collector=config.whale_agent_id, acquisition_type='crawler',
                                  source_url=url, discovered_at=received, metadata=metadata),
                   content=content, provided_capabilities=capabilities)
    if row['source'] == 'google_images':
        asset = dict(media_type='image', role='media', url=url)
        preview = item.get('thumbnail')
        if isinstance(preview, str) and public_serp_url(preview):
            asset['preview_url'] = preview
        for field in ('width', 'height'):
            value = item.get(field)
            if type(value) is int and value > 0:
                asset[field] = value
        message['assets'] = [asset]
        capabilities.append('assets')
        source['payload_hash'] = 'sha256:' + canonical_hash(dict(
            metadata=metadata, content=content, received_at=received, assets=message['assets']))
    return record_key, message


def task_spec(task, config, *, source=None):
    """Only Consumer live queries enter the new collector path."""
    if task.get('task_type') != 'live_query' or 'live_query' not in config.whale_supported_task_types:
        raise ValueError('旧任务类型已退役，仅支持中台live_query')
    if task.get('dataset_id') != config.whale_dataset_id or task.get('source_platform') != config.whale_source_platform:
        raise ValueError('任务数据集或来源与采集器授权不匹配')
    if source is None:
        source = {'google_search':'google_web', 'google_news':'google_news', 'google_trends':'google_trends'}.get(config.whale_source_platform)
    from .middle_platform_client import SOURCE_PLATFORMS
    if SOURCE_PLATFORMS.get(source) != config.whale_source_platform:
        raise ValueError('来源与平台不匹配')
    if set(task.get('required_capabilities') or []) - set(collector_capabilities(source)):
        raise ValueError('任务要求超出当前采集模式能力，未启用正文')
    from .middle_collector import live_payload
    p = live_payload(task.get('payload'), source=source)
    query = p['query']
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ValueError('实时查询关键词必须为1–300字符')
    opts = dict(web_region=p['region'], web_language=p['language'])
    if source == 'google_web':
        opts['web_pages'] = p['max_pages']
    if source == 'google_news':
        locales = [locale for locale in NEWS_LOCALES if locale.split(':')[0] == p['region']
                   and (locale.endswith(':en') if p['language'] == 'en' else ':zh' in locale)]
        if not locales:
            raise ValueError('新闻来源不支持此地区和语言组合')
        opts['news_locale'] = locales[0]
    if source == 'google_trends':
        opts['trends_geo'] = p['region']
        opts['trends_mode'] = p['trends_mode']
        opts['trends_time_range'] = p['trends_time_range']
    opts = options(opts)
    opts['customer_private'] = True
    return dict(keywords=[query.strip()], sources=[source], options=opts, urls=[],
                max_items=p['max_items'], max_pages=p['max_pages'], page_size=p['page_size'],
                google_pages=p['max_pages'],
                proxy_profile=config.default_proxy_profile, max_duration_minutes=5,
                since=None, until=None, task_type='live_query')


def collector_endpoint(config):
    """Explicit endpoint migration without modifying frozen Compose or credentials."""
    path = Path('/app/state/private/whale-api-endpoint.json')
    if not path.exists():
        return config.whale_base_url.rstrip('/')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        base = value['base_url']
        parsed = urlsplit(base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Whale endpoint configuration is invalid') from None
    return base.rstrip('/')


class WhaleLinkClient:
    def __init__(self,config,session=None, *, source=None):
        self.source=source or config.whale_source_platform
        self.config=config;self.session=session or requests.Session();self.agent_id=config.whale_agent_id
        self.base_url=collector_endpoint(config)
        if not config.whale_collector_api_key: raise ValueError('未配置 Whale Collector Key')
    def post(self,path,payload):
        r=self.session.post(self.base_url+path,json=payload,
                            headers={'Authorization':'Bearer '+self.config.whale_collector_api_key},timeout=20,allow_redirects=False)
        if r.status_code == 409:
            raise task_conflict(r)
        if not 200 <= r.status_code < 300:
            e=RuntimeError('Whale HTTP '+str(r.status_code));e.status_code=r.status_code
            e.retry_after=r.headers.get('Retry-After');raise e
        return r.json()
    def register(self):
        types=[t for t in self.config.whale_supported_task_types if t in TASK_TYPES]
        r=self.post('/v1/collectors/register',dict(name='Google SERP Link Collector',agent_type=self.config.whale_source_platform,
           runtime='python',version='serp-1',supported_platforms=[self.config.whale_source_platform],supported_task_types=types,
           declared_capabilities=collector_capabilities(self.source),max_concurrency=1,current_load=0,
           metadata={'mode':'middle_live_links','source_platform':self.config.whale_source_platform,'body':False}))
        self.agent_id=str(r.get('agent_id') or (r.get('data') or {}).get('agent_id') or (r.get('agent') or {}).get('id') or self.agent_id)
    def heartbeat(self,load):return self.post('/v1/collectors/heartbeat',dict(agent_id=self.agent_id,current_load=load))
    def fail(self,task_id,message,retry=True):
        return self.post('/v1/collection/tasks/'+task_id+'/fail',dict(agent_id=self.agent_id,message=message,cursor='',retry=retry))
