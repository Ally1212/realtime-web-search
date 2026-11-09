"""Bounded Google-only News URL resolution. Never fetch a publisher page."""
from __future__ import annotations
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import requests
from .serp_fields import public_fields, public_serp_url, link_fields, field_status, result_items

VERSION = 'google-news-links-1'
SOURCE = 'google_news_link_resolution'
RPC = 'https://news.google.com/_/DotsSplashUi/data/batchexecute'
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_RPC_BYTES = 128 * 1024


def target_url(value):
    if not isinstance(value,str) or len(value)>16384 or any(ord(c)<=32 or ord(c)==127 for c in value):
        return None
    if not public_serp_url(value) or public_fields(value)!=value:
        return None
    host=(urlsplit(value).hostname or '').lower().rstrip('.')
    if host=='google.com' or host.endswith('.google.com') or host in ('g.co','googleusercontent.com') or host.endswith('.googleusercontent.com'):
        return None
    return value


def article_id(value):
    try:
        p=urlsplit(value)
        if p.scheme!='https' or p.hostname!='news.google.com' or p.username or p.password or p.port not in (None,443):return None
        match=re.fullmatch(r'/(?:rss/articles|articles|read)/([A-Za-z0-9_-]{8,4096})/?',p.path)
        return match[1] if match else None
    except (TypeError,ValueError):return None


def offline_target(value):
    if target_url(value):return value
    token=article_id(value)
    if not token:return None
    try:
        raw=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True)
        if not raw.startswith(b'\x08\x13\x22'):return None
        offset,length,shift=3,0,0
        while offset<len(raw) and shift<=28:
            part=raw[offset];offset+=1;length|=(part&127)<<shift
            if part<128:break
            shift+=7
        else:return None
        if not 1<=length<=16384 or offset+length>len(raw):return None
        return target_url(raw[offset:offset+length].decode('utf-8'))
    except (ValueError,UnicodeError):return None


def parameters(body,token):
    node=BeautifulSoup(body,'html.parser').select_one('[data-n-a-sg][data-n-a-ts]')
    if node is None:raise ValueError('google_parameters_missing')
    native=node.get('data-n-a-id')
    if native and native!=token:raise ValueError('google_article_id_mismatch')
    timestamp=node.get('data-n-a-ts','');signature=node.get('data-n-a-sg','')
    if not re.fullmatch(r'[0-9]{1,16}',timestamp) or not 0<int(timestamp)<2**53 or not 1<=len(signature)<=4096:
        raise ValueError('google_parameters_invalid')
    return int(timestamp),signature


def rpc_body(token,timestamp,signature):
    request=['garturlreq',[
        ['en-US','US',['FINANCE_TOP_INDICES','WEB_TEST_1_0_0'],None,None,1,1,'US:en',None,180,None,None,None,None,None,0,None,None,[1608992183,723341000]],
        'en-US','US',1,[2,3,4,8],1,0,'655000234',0,0,None,0],token,timestamp,signature]
    return json.dumps([[['Fbv4je',json.dumps(request,separators=(',',':')),None,'generic']]],separators=(',',':'))


def rpc_target(body):
    """Parse framed/XSSI JSON; only accept the named RPC result, never regex URLs."""
    text=body.decode('utf-8') if isinstance(body,bytes) else body
    if text.startswith(")]}'"):text=text[4:]
    decoder=json.JSONDecoder();offset=0
    for _ in range(64):
        while offset<len(text) and text[offset].isspace():offset+=1
        if offset>=len(text):break
        # Google sometimes prefixes each JSON frame with its decimal byte length.
        line=text.find('\n',offset)
        if line>=0 and text[offset:line].strip().isdigit():offset=line+1;continue
        try:frame,end=decoder.raw_decode(text,offset)
        except ValueError:raise ValueError('google_rpc_invalid_json') from None
        offset=end
        entries=[frame] if isinstance(frame,list) and frame[:1]==['wrb.fr'] else frame
        if not isinstance(entries,list):continue
        for entry in entries:
            if not isinstance(entry,list) or len(entry)<3 or entry[:2]!=['wrb.fr','Fbv4je'] or not isinstance(entry[2],str):continue
            try:result=json.loads(entry[2])
            except ValueError:continue
            if isinstance(result,list) and len(result)>1 and result[0]=='garturlres':
                target=target_url(result[1])
                if target:return target
                raise ValueError('google_rpc_invalid_target')
    raise ValueError('google_rpc_result_missing')


class ResolutionDeferred(Exception):pass


class NewsLinkResolver:
    def __init__(self,discovery,store,campaign_id,*,budget_seconds=150,max_links=100):
        self.discovery,self.store,self.campaign_id=discovery,store,str(campaign_id)
        self.deadline=time.monotonic()+budget_seconds
        self.max_links=max_links;self.links=0;self.requests=0;self.network_unavailable=None
        self.private=(getattr(discovery,'task_options',{}) or {}).get('customer_private') is True

    def _check(self):
        check=getattr(self.discovery,'should_continue',None)
        if callable(check) and not check():raise ResolutionDeferred('task_not_runnable')
        if time.monotonic()>=self.deadline:raise ResolutionDeferred('resolution_batch_deadline')

    def _request(self,method,url,**kwargs):
        self._check()
        if not (url==RPC or article_id(url)):raise ValueError('non_google_resolution_request')
        slot=self.store.acquire_discovery_slot(SOURCE,2.0,maximum_rps=2.0)
        if not slot['allowed']:
            self.network_unavailable='resolution_source_cooling'
            raise ResolutionDeferred(self.network_unavailable)
        wait=float(slot.get('wait',0))
        if wait>=self.deadline-time.monotonic():
            self.deadline=time.monotonic()  # Do not reserve unused future slots for the remaining links.
            raise ResolutionDeferred('resolution_batch_deadline')
        if wait>0:time.sleep(wait)
        self._check();self.requests+=1;started=time.monotonic()
        response=None;status=None;ok=False;captcha=False
        try:
            call=self.discovery._external_get if method=='GET' else self.discovery._external_post
            response=call(url,timeout=max(.1,min(8,self.deadline-time.monotonic())),stream=True,allow_redirects=False,**kwargs)
            status=int(response.status_code)
            if status in (403,429):raise ResolutionDeferred('google_http_'+str(status))
            if not 200<=status<400:raise ValueError('google_http_'+str(status))
            location=response.headers.get('Location')
            if 300<=status<400:
                ok=True
                return status,urljoin(url,location) if location else None,b''
            maximum=MAX_RPC_BYTES if url==RPC else MAX_HTML_BYTES
            content=[];size=0
            for chunk in response.iter_content(65536):
                self._check();size+=len(chunk)
                if size>maximum:raise ValueError('google_resolution_response_too_large')
                content.append(chunk)
            body=b''.join(content)
            if len(body)<100000 and (b'id="captcha-form"' in body or b'<title>Sorry' in body):
                captcha=True
                raise ResolutionDeferred('google_captcha')
            ok=True
            # Do not copy HTML, session material or temporary signatures into SERP data.
            return status,None,body
        finally:
            if response is not None:response.close()
            self.store.record_discovery_result(SOURCE,success=ok,limited=status in (403,429),captcha=captcha or status in (403,429),maximum_rps=2,source_cooldown_seconds=1800,error_code='' if ok else 'http_'+str(status) if status else 'transport_error',elapsed_seconds=time.monotonic()-started,shared_exit=True)

    def _cached(self,key,wrapper):
        cached=self.store.get_discovery_cache(key,SOURCE)
        entries=cached.get('payload') if cached else None
        if isinstance(entries,list) and entries and isinstance(entries[0],dict):
            saved=entries[0]
            if (saved.get('adapter_version')==VERSION and saved.get('wrapper')==wrapper
                    and saved.get('status') in ('resolved','unresolved','deferred')
                    and (saved.get('status')!='resolved' or target_url(saved.get('target')))):
                return dict(saved,cache_hit=True,http_request_count=0)
        return None

    def resolve(self,wrapper):
        cached_key=hashlib.sha256((VERSION+'|'+(self.campaign_id if self.private else 'public')+'|'+wrapper).encode()).hexdigest()
        cached=self._cached(cached_key,wrapper)
        if cached is not None:return cached
        base=dict(adapter_version=VERSION,wrapper=wrapper,status='unresolved',target=None,method=None,cache_hit=False,http_request_count=0,publisher_fetched=False)
        token=article_id(wrapper)
        if not token:return dict(base,reason='unsupported_google_news_url')
        direct=offline_target(wrapper)
        if direct:return dict(base,status='resolved',target=direct,method='offline_protobuf',observed_at=datetime.now(timezone.utc).isoformat())
        if self.network_unavailable:return dict(base,status='deferred',reason=self.network_unavailable)
        try:self._check()
        except ResolutionDeferred as exc:return dict(base,status='deferred',reason=str(exc))
        if self.links>=self.max_links:return dict(base,status='deferred',reason='resolution_link_limit')
        lease=self.store.acquire_query_lease(cached_key)
        if not lease:return dict(base,status='deferred',reason='resolution_inflight')
        try:
            cached=self._cached(cached_key,wrapper)
            if cached is not None:return cached
            start_count=self.requests;self.links+=1
            try:
                # Canonical Google article endpoint avoids RSS-to-read redirect shells.
                url='https://news.google.com/articles/'+token+'?hl=en-US&gl=US&ceid=US:en'
                for _ in range(3):
                    status,redirect,body=self._request('GET',url)
                    if redirect:
                        target=target_url(redirect)
                        if target:
                            base.update(status='resolved',target=target,method='google_http_redirect');break
                        if article_id(redirect)!=token:raise ValueError('google_redirect_not_allowed')
                        url=redirect;continue
                    timestamp,signature=parameters(body,token)
                    metadata_hash=hashlib.sha256(body).hexdigest()
                    rpc_status,redirect,rpc=self._request('POST',RPC,data={'f.req':rpc_body(token,timestamp,signature)},headers={'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'})
                    if redirect:raise ValueError('google_rpc_redirect_not_allowed')
                    base.update(status='resolved',target=rpc_target(rpc),method='google_news_rpc',metadata_response_sha256=metadata_hash,rpc_response_sha256=hashlib.sha256(rpc).hexdigest());break
                else:raise ValueError('google_redirect_limit')
            except ResolutionDeferred as exc:base.update(status='deferred',reason=str(exc))
            except (requests.RequestException,ValueError) as exc:
                base['reason']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            base.update(http_request_count=self.requests-start_count,observed_at=datetime.now(timezone.utc).isoformat())
            # Cache source errors, not local deadline/stop/inflight deferrals.
            reason=base.get('reason','');blocked=reason in ('google_http_403','google_http_429','google_captcha')
            if base['status']=='resolved' or base['status']=='unresolved' or blocked:
                self.store.put_discovery_cache(cached_key,SOURCE,hashlib.sha256(wrapper.encode()).hexdigest(),'global',1,[base],604800 if base['status']=='resolved' else 1800 if blocked else 300)
            return base
        finally:
            self.store.release_query_lease(cached_key,lease)


def enrich_feed(response,discovery,store,campaign_id):
    started=time.monotonic()
    resolver=getattr(discovery,'news_link_resolver',None)
    if resolver is None:
        resolver=NewsLinkResolver(discovery,store,campaign_id);discovery.news_link_resolver=resolver
    summary=dict(adapter_version=VERSION,eligible=0,resolved=0,unresolved=0,deferred=0,cache_hits=0,http_requests=0,publisher_fetches=0)
    for row in result_items(response):
        original=row.get('google_news_link') or row.get('original_link') or row.get('link')
        if not isinstance(original,str) or (urlsplit(original).hostname or '').lower()!='news.google.com':continue
        summary['eligible']+=1
        evidence=resolver.resolve(original)
        row['link_resolution']=evidence
        summary['cache_hits']+=bool(evidence.get('cache_hit'));summary['http_requests']+=evidence.get('http_request_count',0)
        if evidence['status']=='resolved':
            target=evidence['target'];row.update(**link_fields(target));row.update(link=target,publisher_link=target,redirect_link=original,google_news_link=original,original_link=original,is_resolved=True,resolution_method=evidence['method'],link_status='publisher_url')
            row['field_status']=field_status(row,('target_host','domain','url_scheme','url_path','publisher_host','resolution_method','is_resolved'))
            summary['resolved']+=1
        else:
            summary['unresolved']+=1;summary['deferred']+=evidence['status']=='deferred'
    summary['elapsed_seconds']=time.monotonic()-started
    summary['complete']=summary['unresolved']==0
    response['link_resolution_summary']=summary
    response.setdefault('capabilities',{})['news_link_resolution']='google_only_bounded'
    return summary


def record_news_aliases(db,response,normalized):
    """Preserve historical wrappers and their first contribution when resolving.

    Called inside the SERP write transaction before normal URL registration.
    Raw historical responses and immutable delivery snapshots are never rewritten.
    """
    from .serp_fields import normalize_serp_url
    pairs={}
    if response.get('source') in ('google_news','google_trends'):
        for item in result_items(response):
            proof=item.get('link_resolution')
            if not isinstance(proof,dict) or proof.get('adapter_version')!=VERSION or proof.get('status')!='resolved':continue
            original=proof.get('wrapper');target=proof.get('target')
            if not article_id(original) or target!=item.get('link') or not target_url(target):continue
            if proof.get('method') not in ('google_news_rpc','google_http_redirect','offline_protobuf'):continue
            old=hashlib.sha256(normalize_serp_url(original).encode()).hexdigest()
            new=hashlib.sha256(normalize_serp_url(target).encode()).hexdigest()
            pairs[old]=(new,normalize_serp_url(target),proof)
    # All current writers take the same ordered locks, including Web writers
    # that may discover a target concurrently with a News wrapper resolution.
    keys=sorted({int.from_bytes(hashlib.sha256(('serp-url:'+h).encode()).digest()[:8],'big',signed=True) for h in set(normalized)|set(pairs)})
    for key in keys:db.execute('SELECT pg_advisory_xact_lock(%s)',(key,))
    for old,(new,url,proof) in sorted(pairs.items()):
        previous=db.execute('SELECT * FROM serp_urls WHERE url_hash=%s',(old,)).fetchone()
        if not previous or previous['canonical_url_hash'] not in (None,new):continue
        db.execute('INSERT INTO serp_urls(url_hash,normalized_url,first_request_id,first_seen,last_seen,historical) VALUES(%s,%s,%s,%s,clock_timestamp(),%s) ON CONFLICT DO NOTHING',(new,url,previous['first_request_id'],previous['first_seen'],previous['historical']))
        destination=db.execute('SELECT canonical_url_hash FROM serp_urls WHERE url_hash=%s',(new,)).fetchone()
        if destination['canonical_url_hash'] is not None:continue  # Never create an alias chain/cycle.
        db.execute('UPDATE serp_urls SET first_request_id=CASE WHEN first_seen>%s THEN %s ELSE first_request_id END,first_seen=least(first_seen,%s),historical=historical OR %s WHERE url_hash=%s',(previous['first_seen'],previous['first_request_id'],previous['first_seen'],previous['historical'],new))
        db.execute("UPDATE serp_urls SET canonical_url_hash=%s,normalization_original=coalesce(normalization_original,'{}'::jsonb)||jsonb_build_object('news_link_resolution',%s::jsonb) WHERE url_hash=%s AND canonical_url_hash IS NULL",(new,json.dumps(proof),old))
        db.execute('INSERT INTO serp_url_sources(url_hash,source,first_seen) SELECT %s,source,first_seen FROM serp_url_sources WHERE url_hash=%s ON CONFLICT(url_hash,source) DO UPDATE SET first_seen=least(serp_url_sources.first_seen,excluded.first_seen)',(new,old))
