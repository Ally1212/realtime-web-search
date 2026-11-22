"""Bounded self-hosted Maps search with fast and browser provider modes."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from .product import ProductError

VERSION = '1.18.2'
BINARY_SHA256 = '468eec927939b3bcbdd94c59d6d7eef7472b5365409d66545bbe78cc910b309f'
TTL = 24 * 3600
FAST_MAX_PLACES = 21
DEEP_TIMEOUT_SECONDS = 540
DELIVERY_RESERVE_SECONDS = 60
CITIES = {'Singapore': (1.29027,103.851959), 'London': (51.5074,-0.1278),
          'New York': (40.7128,-74.0060), 'Tokyo': (35.6762,139.6503),
          'Hong Kong': (22.3193,114.1694), 'Taipei': (25.0330,121.5654),
          'Sydney': (-33.8688,151.2093), 'Berlin': (52.5200,13.4050)}


def config():
    try:
        value=json.loads(Path(os.getenv('SEARCHFLOW_MAPS_CONFIG','/app/state/maps/config.json')).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}


def availability():
    c=config()
    return dict(enabled=c.get('enabled') is True, configured=c.get('enabled') is True,
                provider='gosom', version=VERSION, modes=['text','nearby'], max_results=100,
                cache_ttl_seconds=TTL, details_on_demand=False, exports=True,
                global_active_limit=4, per_space_active_limit=2, poll_after_seconds=3,
                middle_enabled=False, region_is_metadata=True, included_type_behavior='keyword_hint', cities=[dict(name=k,lat=v[0],lng=v[1]) for k,v in CITIES.items()],
                attribution='Google Maps', ordering='distance', mode='fast',
                notice='按距离排序，每次最多100家；实际数量取决于当前地图结果，不提供完整评论。')


def prepare(p):
    p=dict(p)
    if not p.get('location'):
        center=CITIES.get(p.get('city'))
        if center:
            p['location']=dict(lat=center[0],lng=center[1])
        else:
            from .maps_location import location_text, resolve
            resolved=resolve(p.get('city') or location_text(p['query']), region='', language=p.get('language','en'))
            p['location']=resolved['location']
    if p['mode']=='nearby': p['query']=p['included_type']
    if any(ord(ch)<32 for ch in p['query']+p.get('included_type','')): raise ProductError('invalid_query','关键词不能包含换行或控制字符')
    if not p['query']: raise ProductError('invalid_query','请填写商家关键词')
    return p


def _url(value):
    if not isinstance(value,str): return None
    try:
        p=urlparse(value)
        return value if p.scheme in ('https','http') and p.hostname and not p.username and not p.password else None
    except ValueError: return None


def normalize(raw, p):
    places, _, _ = _normalize_observation(raw, p)
    return places[:p.get('max_places', FAST_MAX_PLACES)]


def _normalize_observation(raw, p, *, filter_outside=False):
    if not isinstance(raw,list): raise ProductError('maps_invalid_response','地图数据格式不正确',502)
    # v1.18.2 serializes its nil entry slice as one JSONL `null` after
    # successful parsing/radius filtering. search() separately requires a
    # successful job log; missing output or a fetch/parser error still fails.
    if raw == [None]:
        return [], raw, 0
    if len(raw)==1 and isinstance(raw[0],list):raw=raw[0]
    if len(raw)>500: raise ProductError('maps_invalid_response','地图原始结果超过安全上限',502)
    places=[];seen=set()
    in_scope = []
    outside = 0
    for r in raw:
        if not isinstance(r,dict) or not isinstance(r.get('title'),str) or not r['title'].strip():
            raise ProductError('maps_invalid_response','地图结果缺少商家名称',502)
        lat,lng=r.get('latitude'),r.get('longitude',r.get('longtitude'))
        if (type(lat) not in (int,float) or type(lng) not in (int,float)
                or not math.isfinite(lat) or not math.isfinite(lng) or not -90<=lat<=90 or not -180<=lng<=180):
            raise ProductError('maps_invalid_response','地图返回无效坐标',502)
        data_id=r.get('data_id')
        if not isinstance(data_id,str) or not data_id:
            raise ProductError('maps_invalid_response','地图返回缺失商家标识',502)
        center=p['location']; a,b=math.radians(center['lat']),math.radians(lat)
        h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(lng-center['lng'])/2)**2
        distance=6371000*2*math.asin(min(1,math.sqrt(h)))
        if distance > p['radius_m'] + (0 if filter_outside else 1):
            if not filter_outside:
                raise ProductError('maps_invalid_response','地图结果超出查询半径',502)
            outside += 1
            continue
        in_scope.append(r)
        # Deeper scrolling may expose the same stable business more than once.
        # Keep the first source occurrence deterministically and report the
        # duplicate count in collection_coverage; never synthesize an identity.
        if data_id in seen:
            continue
        seen.add(data_id)
        categories=r.get('categories')
        if categories is None: categories=[]
        if (not isinstance(categories,list) or any(not isinstance(v,str) for v in categories)
                or any(r.get(k) is not None and not isinstance(r[k],str)
                       for k in ('address','phone','status'))
                or (r.get('open_hours') is not None and not isinstance(r['open_hours'],dict))):
            raise ProductError('maps_invalid_response','地图返回的商家字段类型无效',502)
        # The upstream parser defaults missing ratings/counts to zero. Do not
        # present that default as an independently observed zero.
        rating=r.get('review_rating'); count=r.get('review_count')
        rating=rating if type(rating) in (int,float) and 0<rating<=5 else None
        count=count if type(count) is int and count>0 else None
        places.append(dict(data_id=data_id,place_id=None,name=r['title'].strip(),formatted_address=r.get('address') or None,
            categories=categories,primary_type=(categories or [None])[0],
            rating=rating,user_rating_count=count,location=dict(latitude=lat,longitude=lng),distance_m=round(distance,2),
            national_phone_number=r.get('phone') or None,website_uri=_url(r.get('web_site')),
            regular_opening_hours=r.get('open_hours') or None,business_status=r.get('status') or None,
            google_maps_uri='https://www.google.com/maps/search/?'+urlencode(dict(api=1,query=f"{r['title']} {lat},{lng}")),
            source_fields=r))
    places.sort(key=lambda r:r['distance_m'])
    return places, in_scope, outside


def search(p, *, deadline_at=None):
    if not availability()['enabled']: raise ProductError('maps_disabled','地图查询尚未启用',503)
    p=prepare(p); settings=config(); binary=settings.get('binary','/opt/maps/scraper')
    # A release hash is pinned at worker startup as well as before execution.
    try:
        with open(binary,'rb') as stream:
            checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError: raise ProductError('maps_unavailable','地图采集程序不可用',503) from None
    if checksum!=BINARY_SHA256: raise ProductError('maps_binary_mismatch','地图程序版本校验失败',503)
    with tempfile.TemporaryDirectory(prefix='searchflow-maps-') as folder:
        query=p['query']+(' '+p['included_type'] if p['mode']=='text' and p.get('included_type') else '')
        root=Path(folder); (root/'queries.txt').write_text(query+'\n')
        requested=p.get('max_places',FAST_MAX_PLACES)
        deep=requested>FAST_MAX_PLACES
        depth=min(25,math.ceil(requested/6)) if deep else 1
        timeout = 360 if deep else 75
        if deadline_at is not None:
            remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
            timeout = min(DEEP_TIMEOUT_SECONDS if deep else timeout,
                          remaining - DELIVERY_RESERVE_SECONDS)
            if timeout <= 0:
                raise ProductError('maps_timeout', '地图查询已无剩余采集时间', 504)
        args=[binary,'-input',str(root/'queries.txt'),'-results',str(root/'results.jsonl'),'-json']
        if not deep:args.append('-fast-mode')
        args += ['-geo',f"{p['location']['lat']},{p['location']['lng']}",'-zoom','15','-radius',str(p['radius_m']),
                 '-lang',p['language'],'-c','1','-depth',str(depth),'-exit-on-inactivity','60s' if deep else '20s']
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':settings.get('browser_home',folder),
             'DISABLE_TELEMETRY':'1','TMPDIR':folder}
        timed_out = False
        with (root/'run.log').open('wb') as log:
            try:process=subprocess.Popen(args,cwd=folder,env=env,stdout=log,stderr=log,start_new_session=True)
            except OSError:raise ProductError('maps_unavailable','地图程序无法启动',503) from None
            try: code=process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = process.wait()
        timeout_error = ProductError('maps_timeout', '地图查询超时，请稍后重试', 504)
        logs=(root/'run.log').read_text(errors='replace')
        provider_confirmed = '"status":"success"' in logs and '"level":"error"' not in logs
        if timed_out and (not deep or not provider_confirmed):
            raise timeout_error
        if not timed_out and (code or not provider_confirmed):
            raise ProductError('maps_upstream_failed','地图来源暂未返回有效响应，请稍后重试',502)
        try:
            file=root/'results.jsonl'
            if file.stat().st_size>16*1024*1024:raise ValueError()
            raw=[]
            with file.open() as stream:
                for line in stream:
                    if len(line)>2*1024*1024 or len(raw)>500:raise ValueError()
                    if line.strip():
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            if timed_out and not line.endswith('\n'):
                                break
                            raise
                        raw.extend(value if isinstance(value,list) else [value])
            if not raw: raise ValueError()
        except (OSError,ValueError):
            if timed_out:
                raise timeout_error from None
            raise ProductError('maps_invalid_response','地图未返回可验证的数据',502) from None
    provider_count = len(raw)
    all_places, raw, outside = _normalize_observation(raw, p, filter_outside=deep)
    places = all_places[:requested]
    if timed_out and not places:
        raise timeout_error
    body=json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()
    unique_raw=len(all_places)
    coverage=dict(requested_count=requested,returned_count=len(places),raw_occurrence_count=len(raw),
        provider_occurrence_count=provider_count,out_of_radius_occurrence_count=outside,
        unique_entity_count=unique_raw,duplicate_occurrence_count=0 if raw == [None] else len(raw)-unique_raw,
        provider_depth=depth,provider_mode='browser' if deep else 'fast',truncated=unique_raw>len(places),
        provider_timed_out=timed_out,
        stop_reason=('provider_timeout' if timed_out else 'requested_limit_reached' if len(places)>=requested
                     else 'provider_scan_ended' if deep else 'upstream_exhausted'),
        coverage_complete=False,coverage_status='not_independently_verified')
    return dict(places=places,raw_response=raw,provider='gosom',provider_version=VERSION,provider_request_id=None,
                response_hash=hashlib.sha256(body).hexdigest(),field_mask='gosom-browser-v1' if deep else 'gosom-fast-v1',sku='self_hosted',
                ordering='distance',middle_verified=False,coverage=coverage,
                notice='本次结果来自自建采集，按距离排序；实际数量取决于地图当前可见结果，不含完整评论。')
