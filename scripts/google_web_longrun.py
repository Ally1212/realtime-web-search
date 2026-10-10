"""Durable Google Web-only, link-only experiments; never run article migrations.

Sequential measurements cover a full hour and include failures and switching.
The retained URL registry is shared across all stages. At the deadline, keep the
best eligible measured configuration running. A website off switch stops this
supervisor; it is never silently overridden.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/azureuser/projects/realtime-web-search-openserp')
SERIES = 'google-web-longrun-20261010'
STATE = ROOT/'state'/SERIES
PROJECT = 'realtime-web-search-openserp'
WORKER = PROJECT+'-local-worker-1'
RUN = 'google-serp-20261010'
DEADLINE = '2026-10-11T01:00:00+00:00'
PLANS = [
 dict(key='baseline-r3-s6-p1',slots=6,pages=1,interval=90,initial_rps=1,max_rps=2,revision='r3'),
 dict(key='queue-rate-fix-s6-p1',slots=6,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s12-p1',slots=12,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s18-p1',slots=18,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s12-p2',slots=12,pages=2,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s18-p1-i60-r4',slots=18,pages=1,interval=60,initial_rps=2,max_rps=4,revision='optimized'),
 dict(key='s24-p1-i45-r4',slots=24,pages=1,interval=45,initial_rps=2,max_rps=4,revision='optimized'),
 dict(key='baseline-repeat-s6-p1',slots=6,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s8-p1',slots=8,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
 dict(key='s4-p1',slots=4,pages=1,interval=90,initial_rps=1,max_rps=2,revision='optimized'),
]


def now():
    return datetime.now(timezone.utc)


def parse(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def save(path, value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str)+'\n')
    tmp.replace(path)


def command(args, source=None, timeout=180):
    return subprocess.run(args,cwd=ROOT,input=source,text=True,capture_output=True,check=True,timeout=timeout).stdout


def settings(payload):
    req=urllib.request.Request('http://127.0.0.1:8093/api/collector-settings',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:
        return json.load(response)


CONTROL = '''import json,os,sys
from datetime import datetime
from psycopg.types.json import Jsonb
from realtime.serp_console import ConsoleStore
s=ConsoleStore(os.environ['DATABASE_URL'])
payload=json.loads(sys.argv[1]);expected=datetime.fromisoformat(sys.argv[2])
assert set(payload)<= {'continuous_enabled','continuous_sources','content_fetch_enabled','web_max_pages'}
with s.store.connect() as db:
 row=db.execute('SELECT * FROM serp_collector_settings WHERE id=true FOR UPDATE').fetchone()
 if row['updated_at']!=expected:
  raise RuntimeError('operator changed settings; do not resume automatically')
 cfg=dict(row['runtime_config']);cfg.update({k:v for k,v in payload.items() if k!='content_fetch_enabled'})
 updated=db.execute('UPDATE serp_collector_settings SET runtime_config=%s,content_fetch_enabled=%s,updated_at=clock_timestamp() WHERE id=true RETURNING updated_at',(Jsonb(cfg),payload.get('content_fetch_enabled',row['content_fetch_enabled']))).fetchone()['updated_at']
 db.execute("UPDATE campaigns c SET sources=%s FROM serp_supply_campaigns s WHERE s.campaign_id=c.id AND s.run_id=%s AND c.status='active'",(Jsonb(cfg['continuous_sources']),'google-serp-20261010'))
print(json.dumps(dict(updated_at=updated),default=str))
'''


def controlled_settings(payload, expected):
    return json.loads(command(['docker','exec','-i',PROJECT+'-web-1','python','-',json.dumps(payload),expected],CONTROL))


SNAPSHOT = '''import json,os,sys
from datetime import datetime
from realtime.config import Config
from realtime.serp_console import ConsoleStore
from realtime.campaign_queue import CampaignQueue
c=Config();s=ConsoleStore(c.database_url);q=CampaignQueue(c.valkey_url)
start=sys.argv[1]
with s.store.connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
 db.execute("SET LOCAL statement_timeout='20s'")
 end=db.execute('SELECT now() t').fetchone()['t']
 if len(sys.argv)>2:
  end=min(end,datetime.fromisoformat(sys.argv[2].replace('Z','+00:00')))
 requests=db.execute("SELECT source,count(*) attempts,count(*) FILTER(WHERE classification IN ('results','empty')) successful,count(*) FILTER(WHERE classification='captcha') captcha,count(*) FILTER(WHERE http_status=429) limited,sum(coalesce((response->>'new_unique_links')::int,0)) reported_new FROM serp_requests WHERE completed_at>=%s AND completed_at<%s GROUP BY source",(start,end)).fetchall()
 links=db.execute("SELECT count(*) n FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE NOT u.historical AND u.canonical_url_hash IS NULL AND u.first_seen>=%s AND u.first_seen<%s AND r.source='google_web'",(start,end)).fetchone()['n']
 hourly=db.execute("WITH counts AS (SELECT date_trunc('hour',u.first_seen) AS hour_bucket,count(*) n FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE NOT u.historical AND u.canonical_url_hash IS NULL AND u.first_seen>=%s AND u.first_seen<%s AND r.source='google_web' GROUP BY 1), buckets AS (SELECT generate_series(date_trunc('hour',%s::timestamptz),date_trunc('hour',%s::timestamptz),interval '1 hour') AS hour_bucket) SELECT b.hour_bucket,coalesce(c.n,0) n,extract(epoch FROM least(%s::timestamptz,b.hour_bucket+interval '1 hour')-greatest(%s::timestamptz,b.hour_bucket)) elapsed_seconds FROM buckets b LEFT JOIN counts c USING(hour_bucket) WHERE b.hour_bucket<%s ORDER BY b.hour_bucket",(start,end,start,end,end,start,end)).fetchall()
 pages=db.execute('SELECT count(*) n,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
 source=db.execute("SELECT source,state,current_rps,circuit_until,requests_window,successes_window,errors_window,captcha_window FROM discovery_source_runtime WHERE source IN ('google_web','google_openserp')").fetchall()
 manual=db.execute("SELECT count(*) n FROM campaigns c WHERE status='active' AND NOT EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id)").fetchone()['n']
 active=db.execute("SELECT sources,count(*) n FROM campaigns c JOIN serp_supply_campaigns s ON s.campaign_id=c.id WHERE status='active' AND s.run_id=%s GROUP BY sources",('google-serp-20261010',)).fetchall()
 supply=db.execute('SELECT cursor,keyword_offset FROM serp_supply_runs WHERE run_id=%s',('google-serp-20261010',)).fetchone()
 inflight=db.execute("SELECT count(*) n FROM google_page_frontier WHERE state='running' AND lease_expires_at>now()").fetchone()['n']
effective=s.effective_config(c)
try:
 queue=dict(ready=q.redis.llen(q.READY),delayed=q.redis.zcard(q.DELAYED),available=True)
except Exception:
 queue=dict(ready=None,delayed=None,available=False)
print(json.dumps(dict(at=end,started_at=start,requests=requests,new_links=links,hourly=hourly,pages=pages,source_runtime=source,active_manual_tasks=manual,active_supply=active,supply=supply,inflight=inflight,settings=s.settings(c),queue=queue,effective=dict(slots=effective.crawler_slots,query_concurrency=effective.discovery_query_concurrency,pages=effective.google_web_max_pages,interval=effective.google_web_proxy_min_interval_seconds,initial_rps=effective.google_web_initial_rps,max_rps=effective.google_web_max_rps,providers=effective.google_free_providers)),default=str))
'''


def sample(start):
    data=json.loads(command(['docker','exec','-i',WORKER,'python','-',start],SNAPSHOT,timeout=45))
    info=json.loads(command(['docker','inspect',WORKER]))[0]
    data['worker']=dict(id=info['Id'],restarts=info['RestartCount'],running=info['State']['Running'],oom=info['State']['OOMKilled'])
    data['host']=dict(load=os.getloadavg(),cpus=os.cpu_count(),available_disk=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize,
                      available_memory=int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])*1024)
    return data


def result(data, plan, baseline_pages):
    seconds=(parse(data['at'])-parse(data['started_at'])).total_seconds()
    web=next((r for r in data['requests'] if r['source']=='google_web'),dict(attempts=0,successful=0,captcha=0,limited=0))
    other=sum(r['attempts'] for r in data['requests'] if r['source']!='google_web')
    actual=data['effective']
    configuration_matches=all(actual[k]==plan[k] for k in ('slots','pages','interval','initial_rps','max_rps')) and list(actual['providers'])==['openserp']
    pure=other==0 and data['pages']==baseline_pages and not data['settings']['content_fetch_enabled'] and data['settings']['continuous_sources']==['google_web'] and data['active_manual_tasks']==0 and all(r['sources']==['google_web'] for r in data['active_supply'])
    success=web['successful']/web['attempts'] if web['attempts'] else 0
    queue_safe=plan.get('revision')=='optimized'
    valid=seconds>=3600 and pure and configuration_matches and queue_safe and data['worker']['running'] and not data['worker']['oom'] and data['worker']['restarts']==0 and success>=.85 and web['attempts']>=100
    return dict(key=plan['key'],plan=plan,url_normalization='google-tracking-v2',started_at=data['started_at'],measured_until=data['at'],elapsed_seconds=seconds,new_links=data['new_links'],new_links_per_hour=data['new_links']*3600/seconds if seconds>0 else 0,requests=web,success_rate=success,pure_google_web=pure,configuration_matches=configuration_matches,queue_safe=queue_safe,eligibility_notes=[] if queue_safe else ['legacy worker reschedules stopped tasks indefinitely; control baseline only'],eligible=valid,hourly=data['hourly'],worker=data['worker'],source_runtime=data['source_runtime'],queue=data['queue'],host=data['host'])


def best(results):
    # Repeated configurations contribute all measured wall time, not just
    # their best hour. This also includes clean but slow/failed short stages.
    groups={}
    for row in results:
        if not row['pure_google_web'] or not row['configuration_matches'] or row['plan'].get('revision')!='optimized':
            continue
        identity=json.dumps({k:v for k,v in row['plan'].items() if k!='key'},sort_keys=True)
        groups.setdefault(identity,[]).append(row)
    candidates=[]
    for rows in groups.values():
        seconds=sum(r['elapsed_seconds'] for r in rows)
        links=sum(r['new_links'] for r in rows)
        attempts=sum(r['requests']['attempts'] for r in rows)
        successes=sum(r['requests']['successful'] for r in rows)
        stable=all(r['worker']['running'] and not r['worker']['oom'] and r['worker']['restarts']==0 for r in rows)
        success=successes/attempts if attempts else 0
        if not stable or not any(r['elapsed_seconds']>=3600 for r in rows) or attempts<100 or success<.85:
            continue
        winner=dict(rows[-1],elapsed_seconds=seconds,new_links=links,new_links_per_hour=links*3600/seconds,success_rate=success,eligible=True,segments=[dict(key=r['key'],started_at=r['started_at'],measured_until=r['measured_until']) for r in rows],weighted_all_measured_segments=True)
        winner['requests']={k:sum(r['requests'].get(k,0) or 0 for r in rows) for k in ('attempts','successful','captcha','limited')}
        candidates.append(winner)
    return max(candidates,key=lambda r:(r['new_links_per_hour'],r['success_rate'])) if candidates else None


def compose_for(plan):
    old=ROOT/'state/serp-website-20261010/revisions/r3'
    value=json.loads((old/'compose.website.json').read_text())
    worker=value['services']['local-worker']
    worker['environment'].update(CONTENT_FETCH_ENABLED='false',SERP_CONTINUOUS_ENABLED='true',SERP_CONTINUOUS_SOURCES='google_web',GOOGLE_FREE_PROVIDERS='openserp',CRAWLER_SLOTS=str(plan['slots']),DISCOVERY_GLOBAL_CONCURRENCY=str(plan['slots']),DISCOVERY_QUERY_CONCURRENCY='1',GOOGLE_WEB_MAX_PAGES=str(plan['pages']),GOOGLE_WEB_PAGES_PER_BATCH='1',GOOGLE_WEB_INITIAL_RPS=str(plan['initial_rps']),GOOGLE_WEB_MAX_RPS=str(plan['max_rps']),GOOGLE_WEB_PROXY_MIN_INTERVAL_SECONDS=str(plan['interval']))
    worker['volumes']=[str(STATE/'code/realtime')+':/app/realtime:ro'] if plan['revision']=='optimized' else [str(old/'code/realtime')+':/app/realtime:ro']
    normalized=STATE/'normalization-v2'
    if (normalized/'ready.json').exists():
        kind='optimized' if plan['revision']=='optimized' else 'baseline'
        worker['volumes']=[str(normalized/'code'/kind/'realtime')+':/app/realtime:ro']
        value['services']['web']['volumes']=[str(normalized/'code/web/realtime')+':/app/realtime:ro']
    return value


def verify_code():
    manifest=json.loads((STATE/'code-manifest.json').read_text())
    actual={str(p.relative_to(STATE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (STATE/'code').rglob('*.py')}
    if actual!=manifest: raise RuntimeError('frozen code hash mismatch')
    normalized=STATE/'normalization-v2'
    if (normalized/'ready.json').exists():
        expected=json.loads((normalized/'code-manifest.json').read_text())
        current={str(p.relative_to(normalized)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (normalized/'code').rglob('*.py')}
        if current!=expected: raise RuntimeError('normalized code hash mismatch')


def drain(started_at, expected):
    token=controlled_settings({'continuous_enabled':False},expected)['updated_at']
    until=time.monotonic()+90
    while time.monotonic()<until:
        data=sample(started_at)
        if parse(data['settings']['updated_at'])!=parse(token):
            raise RuntimeError('operator changed settings during drain')
        if data['inflight']==0:
            return data
        time.sleep(5)
    raise RuntimeError('in-flight requests did not drain; keep collection disabled')


def apply(plan, expected):
    verify_code()
    paused=drain(now().isoformat(),expected)
    configuration=compose_for(plan)
    save(STATE/'next-compose.json',configuration)
    command(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(STATE/'next-compose.json'),'config','--quiet'])
    command(['docker','stop','--time','20',WORKER])
    save(STATE/'current-compose.json',configuration)
    save(ROOT/'compose.override.yaml',configuration)
    command(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(STATE/'current-compose.json'),'up','-d','--no-build','--no-deps','local-worker'])
    controlled_settings({'continuous_sources':['google_web'],'content_fetch_enabled':False,'web_max_pages':plan['pages'],'continuous_enabled':True},paused['settings']['updated_at'])
    return sample(now().isoformat())


def write_status(state, data):
    save(STATE/'state.json',state)
    save(STATE/'heartbeat.json',dict(at=now(),status=state['status'],phase=state.get('phase'),deadline=DEADLINE,current=data))


def finish_segment(state, data, reason):
    row=result(data,state['plan'],state['baseline_pages'])
    row.update(reason=reason,worker_revision=state['plan']['revision'])
    state['results'].append(row)
    save(STATE/f"result-{len(state['results']):02d}-{row['key']}.json",row)
    save(STATE/'ranking.json',dict(at=now(),best=best(state['results']),results=state['results'],limits='Sequential query slices and inherited proxy health/time confound causal comparisons. Only retained-history novelty counts; no isolated new database or peak-hour extrapolation.'))
    return row


def supervise():
    state=json.loads((STATE/'state.json').read_text())
    if state['status'] not in ('experiments','long_validation'):
        return
    failures=0
    while True:
        try:
            data=sample(state['started_at']);failures=0
        except Exception as exc:
            failures+=1
            save(STATE/'telemetry-error.json',dict(at=now(),error=type(exc).__name__,failures=failures))
            if failures>=3:
                settings({'continuous_enabled':False,'content_fetch_enabled':False})
                state['status']='telemetry_failed';save(STATE/'state.json',state)
                raise
            time.sleep(30);continue
        if not data['settings']['continuous_enabled']:
            state['status']='stopped_by_operator';finish_segment(state,data,'website_off_switch');write_status(state,data);return
        saved={k:v for k,v in data['settings'].items() if k!='updated_at'}
        if state.get('expected_settings',saved)!=saved:
            settings({'continuous_enabled':False})
            state['status']='settings_changed_by_operator';finish_segment(state,data,'website_settings_changed');write_status(state,data);return
        state['expected_settings']=saved
        if (data['settings']['continuous_sources']!=['google_web'] or data['settings']['content_fetch_enabled'] or data['active_manual_tasks']
                or any(r['attempts'] for r in data['requests'] if r['source']!='google_web')):
            settings({'continuous_enabled':False,'content_fetch_enabled':False})
            state['status']='purity_interrupted';finish_segment(state,data,'source_body_or_manual_task_changed');write_status(state,data);return
        metric=result(data,state['plan'],state['baseline_pages'])
        with (STATE/'observations.jsonl').open('a') as f:
            f.write(json.dumps(dict(at=data['at'],phase=state['phase'],metric=metric),default=str)+'\n')
        if data['pages']!=state['baseline_pages'] or data['host']['available_disk']<4*1024**3 or data['host']['available_memory']<512*1024**2:
            settings({'continuous_enabled':False,'content_fetch_enabled':False})
            state['status']='safety_stopped';finish_segment(state,data,'body_or_host_resource_guard');write_status(state,data);return
        if now()>=parse(DEADLINE):
            data=drain(state['started_at'],data['settings']['updated_at'])
            finish_segment(state,data,'deadline')
            winner=best(state['results'])
            final_plan=winner['plan'] if winner else PLANS[1]
            if final_plan!=state['plan']:
                apply(final_plan,data['settings']['updated_at'])
            else:
                controlled_settings({'continuous_enabled':True},data['settings']['updated_at'])
            state.update(status='optimization_complete',winner=winner,plan=final_plan,fallback_no_eligible_winner=winner is None,completed_at=now().isoformat())
            current=sample(data['at'])
            save(STATE/'final.json',state);write_status(state,current);return
        elapsed=(parse(data['at'])-parse(state['started_at'])).total_seconds()
        degraded=elapsed>=1800 and metric['requests']['attempts']>=100 and metric['success_rate']<.7
        if state['phase']<len(PLANS) and (elapsed>=state['stage_seconds'] or degraded):
            # Keep measuring during drain/restart: the next stage begins at the
            # prior stage end, so there is no excluded restart/waiting interval.
            data=drain(state['started_at'],data['settings']['updated_at'])
            row=finish_segment(state,data,'degraded' if degraded else 'stage_complete')
            next_phase=state['phase']+1
            if next_phase<len(PLANS):
                plan=PLANS[next_phase]
            else:
                winner=best(state['results'])
                plan=winner['plan'] if winner else PLANS[1]
            state.update(status='transition',phase=next_phase,plan=plan,started_at=data['at'])
            write_status(state,data)
            observed=apply(plan,data['settings']['updated_at'])
            state['expected_settings']={k:v for k,v in observed['settings'].items() if k!='updated_at'}
            state['status']='long_validation' if next_phase>=len(PLANS) else 'experiments'
            write_status(state,observed)
        else:
            write_status(state,data)
        time.sleep(45)


def initialize(started_at, stage_seconds=5400):
    if (STATE/'state.json').exists(): raise RuntimeError('series already initialized')
    verify_code()
    data=sample(started_at)
    assert data['settings']['continuous_enabled'] and data['settings']['continuous_sources']==['google_web'] and not data['settings']['content_fetch_enabled']
    assert not data['active_manual_tasks']
    state=dict(series=SERIES,deadline=DEADLINE,status='experiments',phase=0,plan=PLANS[0],started_at=started_at,overall_started_at=started_at,baseline_pages=data['pages'],results=[],stage_seconds=stage_seconds)
    save(STATE/'original-compose.override.yaml',json.loads((ROOT/'compose.override.yaml').read_text()))
    save(STATE/'current-compose.json',json.loads((ROOT/'compose.override.yaml').read_text()))
    (STATE/'original-systemd.service').write_bytes(Path('/etc/systemd/system/realtime-google-serp.service').read_bytes())
    save(STATE/'manifest.json',dict(series=SERIES,deadline=DEADLINE,plans=PLANS,stage_seconds=stage_seconds,metric='new historical-unique normalized persisted URLs / complete elapsed wall hours',run_id=RUN,providers=['openserp'],sources=['google_web'],body=False))
    write_status(state,data)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--initialize');args=parser.parse_args()
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'supervisor.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.initialize: initialize(args.initialize)
        else: supervise()
