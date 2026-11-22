"""Resolve explicit city text to observed coordinates, with a bounded shared cache."""
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
import requests
from .product import ProductError

ENDPOINT = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'SearchFlowMaps/1.0 (https://searchflow.20-150-145-162.sslip.io)'


def location_text(query):
    # Keep the complete original search query for Maps. Extract only an
    # explicitly named place; never turn an unspecified query into local search.
    match = re.search(r'\b(?:in|near|around)\s+(.+?)\s*$', query, re.I)
    if match:
        return match.group(1).strip(' .')
    match = re.fullmatch(r'(.+?)的(.+)', query)
    if match:
        return match.group(1).strip()
    raise ProductError('maps_location_required', '请在关键词中写明城市（例如 coffee in Seattle），或提供城市/坐标', 400)


def resolve(text, *, region='', language='en', session=None, cache_file=None):
    if not isinstance(text,str) or not 1 <= len(text.strip()) <= 200 or any(ord(c)<32 for c in text):
        raise ProductError('maps_location_invalid','城市或地址无效',400)
    text=text.strip()
    path=Path(cache_file or os.getenv('SEARCHFLOW_MAPS_GEOCODE_CACHE','/tmp/searchflow-maps-geocoding.sqlite3'))
    path.parent.mkdir(parents=True,exist_ok=True)
    # BEGIN IMMEDIATE serializes processes and enforces the public service's
    # <=1 request/second requirement. Positive cache lives 30 days; no autosuggest.
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, value TEXT, expires REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS rate(id INTEGER PRIMARY KEY, at REAL)')
        db.commit();db.execute('BEGIN IMMEDIATE')
        key=json.dumps([text.casefold(),region,language],ensure_ascii=False)
        old=db.execute('SELECT value FROM cache WHERE k=? AND expires>?',(key,time.time())).fetchone()
        if old:return json.loads(old[0])
        last=db.execute('SELECT at FROM rate WHERE id=1').fetchone()
        if last:time.sleep(max(0,1.1-(time.time()-last[0])))
        db.execute('INSERT OR REPLACE INTO rate VALUES(1,?)',(time.time(),))
        params=dict(q=text,format='jsonv2',limit=3,addressdetails=1)
        if region:params['countrycodes']=region.lower()
        try:
            r=(session or requests).get(ENDPOINT,params=params,headers={'User-Agent':USER_AGENT,'Accept-Language':language},timeout=15,allow_redirects=False)
        except requests.RequestException:
            db.commit()
            raise ProductError('maps_geocoder_unavailable','城市定位服务暂不可用，请稍后重试或提供坐标',503) from None
        if r.status_code!=200:
            db.commit()
            raise ProductError('maps_geocoder_unavailable','城市定位服务暂不可用，请稍后重试或提供坐标',503)
        try:
            if len(r.content)>256000:raise ValueError()
            candidates=r.json()
            if not isinstance(candidates,list):raise ValueError()
            valid=[]
            for v in candidates:
                if not isinstance(v,dict):raise ValueError()
                lat,lng=float(v['lat']),float(v['lon'])
                if not math.isfinite(lat) or not math.isfinite(lng) or not -90<=lat<=90 or not -180<=lng<=180:raise ValueError()
                if v.get('addresstype') not in ('city','town','village','municipality','suburb','borough','quarter','neighbourhood','county','state','country'):continue
                valid.append(v)
        except (KeyError,TypeError,ValueError):
            db.commit()
            raise ProductError('maps_geocoder_invalid','城市定位返回无效数据，未执行地图搜索',502) from None
        if not valid:
            db.commit()
            raise ProductError('maps_location_not_found','无法确定查询中的城市，请写明城市和国家或提供坐标',400)
        # A bare city name can refer to multiple similarly ranked locations.
        if len(valid)>1 and abs(float(valid[0].get('importance',0))-float(valid[1].get('importance',0)))<0.15:
            db.commit()
            raise ProductError('maps_location_ambiguous','城市名称有歧义，请补充州/国家或提供坐标',400)
        v=valid[0]
        result=dict(input=text,location=dict(lat=float(v['lat']),lng=float(v['lon'])),
            display_name=v['display_name'],country_code=(v.get('address') or {}).get('country_code'),
            provider='nominatim',osm_type=v.get('osm_type'),osm_id=v.get('osm_id'),
            attribution='© OpenStreetMap contributors',resolved_at=time.time())
        db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,json.dumps(result,ensure_ascii=False),time.time()+30*86400))
        return result
