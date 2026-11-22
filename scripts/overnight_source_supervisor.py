"""Deadline-bounded five-source collection. No credentials in artifacts."""
import concurrent.futures, fcntl, json, os, sqlite3, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp')
STATE=ROOT/'state/overnight-20261117'
BASE=ROOT/'state/google-web-longrun-20261010/current-compose.json'
WORKER='realtime-web-search-openserp-local-worker-1'
PRD='google-workbench-backend-prd-1'
DEADLINE='2026-11-18T01:00:00+00:00'
def now(): return datetime.now(timezone.utc)
def save(name,value):
    p=STATE/name; tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(value,default=str,ensure_ascii=False,indent=2)); tmp.replace(p)
def cmd(args,source=None,timeout=180):
    p=subprocess.run(args,input=source,text=True,capture_output=True,timeout=timeout)
    if p.returncode: raise RuntimeError(f'command failed: {args[:4]}: {p.stderr[-800:]}')
    return p.stdout
CONTROL='''import json,sys
from psycopg.types.json import Jsonb
from realtime.config import Config
from realtime.serp_console import ConsoleStore
c=Config(); s=ConsoleStore(c.database_url)
with s.store.connect() as db:
 r=db.execute('SELECT * FROM serp_collector_settings WHERE id=true FOR UPDATE').fetchone()
 expected=sys.argv[2]
 if expected!='any' and str(r['updated_at'])!=expected: raise RuntimeError('operator settings changed')
 cfg=dict(r['runtime_config']); cfg.update(continuous_enabled=sys.argv[1]=='on',continuous_sources=['google_web'])
 token=db.execute('UPDATE serp_collector_settings SET runtime_config=%s,content_fetch_enabled=false,updated_at=clock_timestamp() WHERE id=true RETURNING updated_at',(Jsonb(cfg),)).fetchone()['updated_at']
print(json.dumps(dict(token=str(token))))
'''
def control(enabled,expected='any'):
    return json.loads(cmd(['docker','exec','-i',PRD,'python','-', 'on' if enabled else 'off',expected],CONTROL))['token']
SNAPSHOT='''import json,sys
from datetime import datetime
from realtime.config import Config
from realtime.campaign_store import CampaignStore
from realtime.serp_console import ConsoleStore
c=Config(); s=CampaignStore(c.database_url,initialize=False)
with s.connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
 db.execute("SET LOCAL statement_timeout='45s'")
 end=db.execute('SELECT now() t').fetchone()['t']
 rows=db.execute("WITH owned AS (SELECT r.* FROM serp_requests r WHERE r.completed_at>=%s AND r.completed_at<%s AND (EXISTS(SELECT 1 FROM serp_supply_campaigns x WHERE x.campaign_id=r.campaign_id AND x.run_id='google-serp-20261010') OR r.campaign_id=ANY(%s::uuid[]))) SELECT source,count(*) attempts,count(*) FILTER(WHERE classification IN('results','empty')) successful,count(*) FILTER(WHERE classification='captcha') captcha,count(*) FILTER(WHERE http_status=429) limited,coalesce(sum((response->>'new_unique_links')::int),0) reported_new_links FROM owned GROUP BY source",(sys.argv[1],min(end,datetime.fromisoformat(sys.argv[2])),json.loads(sys.argv[3]))).fetchall()
 links=db.execute("SELECT r.source,count(*) new_history_unique_urls FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE NOT u.historical AND u.canonical_url_hash IS NULL AND u.first_seen>=%s AND u.first_seen<%s AND (EXISTS(SELECT 1 FROM serp_supply_campaigns x WHERE x.campaign_id=r.campaign_id AND x.run_id='google-serp-20261010') OR r.campaign_id=ANY(%s::uuid[])) GROUP BY r.source",(sys.argv[1],min(end,datetime.fromisoformat(sys.argv[2])),json.loads(sys.argv[3]))).fetchall()
 inflight=db.execute("SELECT count(*) n FROM google_page_frontier WHERE state='running' AND lease_expires_at>now()").fetchone()['n']
 pages=db.execute('SELECT count(*) n,max(id) max_id FROM pages').fetchone()
print(json.dumps(dict(at=end,requests=rows,links=links,inflight=inflight,pages=pages,settings=ConsoleStore(c.database_url).settings(c)),default=str))
'''
def sample(start,ids):
    code="import json,sys\np=json.load(sys.stdin)\nsys.argv=['snapshot',p['start'],p['deadline'],json.dumps(p['ids'])]\n"+SNAPSHOT
    return json.loads(cmd(['docker','exec','-i',PRD,'python','-c',code],json.dumps(dict(start=start,deadline=DEADLINE,ids=ids)),90))
def web_metric(data):
    r=next((r for r in data['requests'] if r['source']=='google_web'),{})
    n=next((r['new_history_unique_urls'] for r in data['links'] if r['source']=='google_web'),0)
    return n,r.get('attempts',0),r.get('successful',0)
def winner(segments):
    groups={}
    for s in segments:
        g=groups.setdefault(s['slots'],dict(seconds=0,new=0,attempts=0,successful=0))
        for k in g: g[k]+=s[k]
    eligible={k:v for k,v in groups.items() if v['seconds']>=3600 and v['attempts']>=100 and v['successful']/v['attempts']>=.85}
    return max(eligible,key=lambda k:eligible[k]['new']/eligible[k]['seconds']) if eligible else 18
def switch(slots,expected):
    token=control(False,expected)
    for _ in range(36):
        d=sample(now().isoformat(),[])
        if str(d['settings']['updated_at'])!=token: raise RuntimeError('operator changed settings during drain')
        if not d['inflight']: break
        time.sleep(5)
    else: raise RuntimeError('drain timeout, collection remains off')
    value=json.loads((STATE/'original-compose.json').read_text())
    env=value['services']['local-worker']['environment']
    env.update(CRAWLER_SLOTS=str(slots),DISCOVERY_GLOBAL_CONCURRENCY=str(slots))
    save('candidate-compose.json',value)
    cmd(['docker','compose','-p','realtime-web-search-openserp','-f',str(ROOT/'compose.yaml'),'-f',str(STATE/'candidate-compose.json'),'config','--quiet'])
    BASE.write_text(json.dumps(value))
    (ROOT/'compose.override.yaml').write_text(json.dumps(value))
    cmd(['docker','compose','-p','realtime-web-search-openserp','-f',str(ROOT/'compose.yaml'),'-f',str(BASE),'up','-d','--no-build','--no-deps','local-worker'])
    if now().isoformat()>=DEADLINE: return control(False,token)
    return control(True,token)
def stop():
    control(False)
    (STATE/'STOP').touch()
    save('stop-receipt.json',dict(at=now(),deadline=DEADLINE,continuous_enabled=False))
def collect(source,index):
    container='google-workbench-backend-maps-1' if source=='google_maps' else WORKER
    out=cmd(['docker','exec','-i',container,'python','-',source,str(index)],(STATE/'collect.py').read_text(),240)
    return json.loads(out.strip().splitlines()[-1])
def main():
    if sys.argv[-1]=='stop': stop(); return
    if now().isoformat()>=DEADLINE: stop(); return
    STATE.mkdir(exist_ok=True)
    lock=(STATE/'lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    db=sqlite3.connect(STATE/'results.sqlite');db.execute('CREATE TABLE IF NOT EXISTS tasks(source TEXT,idx INTEGER,result TEXT,PRIMARY KEY(source,idx))')
    db.execute('CREATE TABLE IF NOT EXISTS places(data_id TEXT PRIMARY KEY,payload TEXT,first_index INTEGER)');db.commit()
    if not (STATE/'manifest.json').exists():
        save('original-compose.json',json.loads(BASE.read_text()))
        baseline=sample(now().isoformat(),[])
        save('manifest.json',dict(started_at=now().isoformat(),deadline=DEADLINE,baseline=baseline,web_trials=[18,24,12],stage_seconds=4500,sources=['google_web','google_news','google_trends','google_images','google_maps'],maps_identity='data_id; unique within this run, not historical novelty',delivery='local project database for SERP; results.sqlite for Maps; no automatic platform upload'))
    manifest=json.loads((STATE/'manifest.json').read_text());start=manifest['started_at']
    state=json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else dict(slots=18,phase=0,stage_start=start,segments=[],stage_base=[0,0,0])
    # Only the first invocation has authorization to turn the website switch on.
    if 'token' not in state:
        state['token']=control(True,str(manifest['baseline']['settings']['updated_at']));save('state.json',state)
    next_indices={s:db.execute('SELECT coalesce(max(idx),-1)+1 FROM tasks WHERE source=?',(s,)).fetchone()[0] for s in manifest['sources'][1:]}
    due={s:0 for s in next_indices};futures={};last_sample=0;failures=0
    executor=concurrent.futures.ThreadPoolExecutor(max_workers=5)
    try:
        while now().isoformat()<DEADLINE and not (STATE/'STOP').exists():
            t=time.monotonic()
            for future,(source,index) in list(futures.items()):
                if not future.done():continue
                try: result=future.result()
                except Exception as e: result=dict(source=source,index=index,error=str(e));due[source]=t+60
                db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?)',(source,index,json.dumps(result)))
                if source=='google_maps' and 'result' in result:
                    for place in result['result']['places']:
                        db.execute('INSERT OR IGNORE INTO places VALUES(?,?,?)',(place['data_id'],json.dumps(place),index))
                db.commit();del futures[future]
                if result.get('errors'): due[source]=t+30
            if t-last_sample>=60:
                ids=[json.loads(r[0])['campaign_id'] for r in db.execute("SELECT result FROM tasks WHERE source<>'google_maps'") if 'campaign_id' in json.loads(r[0])]
                try: d=sample(start,ids);failures=0
                except Exception:
                    failures+=1
                    if failures>=3: raise
                    time.sleep(10);continue
                if not d['settings']['continuous_enabled'] or str(d['settings']['updated_at'])!=state['token']:
                    raise RuntimeError('operator changed collector settings; stop this experiment')
                disk=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize
                mem=int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])*1024
                if disk<5*1024**3 or mem<1024**3 or d['pages']!=manifest['baseline']['pages']:
                    raise RuntimeError('resource or body-collection guard')
                n,a,ok=web_metric(d)
                elapsed=(now()-datetime.fromisoformat(state['stage_start'])).total_seconds()
                current=dict(slots=state['slots'],seconds=elapsed,new=n-state['stage_base'][0],attempts=a-state['stage_base'][1],successful=ok-state['stage_base'][2])
                maps=dict(tasks=db.execute("SELECT count(*) FROM tasks WHERE source='google_maps'").fetchone()[0],run_unique_places=db.execute('SELECT count(*) FROM places').fetchone()[0])
                report=dict(at=now(),started_at=start,deadline=DEADLINE,status='running',data=d,maps=maps,current=current,segments=state['segments'],disk_available=disk,memory_available=mem)
                save('report.json',report)
                with (STATE/'observations.jsonl').open('a') as f:f.write(json.dumps(report,default=str)+'\n')
                if elapsed>=4500:
                    state['segments'].append(current);state['phase']+=1
                    slots=[18,24,12][state['phase']] if state['phase']<3 else winner(state['segments'])
                    # Attribute transition and drain overhead to the next segment.
                    state.update(stage_start=d['at'],stage_base=[n,a,ok])
                    if slots!=state['slots']:state['token']=switch(slots,state['token'])
                    state['slots']=slots
                save('state.json',state);last_sample=t
            running={s for s,i in futures.values()}
            for source in next_indices:
                if source not in running and t>=due[source]:
                    index=next_indices[source];next_indices[source]+=1
                    futures[executor.submit(collect,source,index)]=(source,index)
                    # Trends rotates 13 countries over more than 15 minutes; avoids hammering cached feeds.
                    due[source]=t+({'google_news':2,'google_images':5,'google_maps':5,'google_trends':75}[source])
            time.sleep(2)
    except Exception as e:
        save('error.json',dict(at=now(),error=str(e)))
        raise
    finally:
        stop();executor.shutdown(wait=True,cancel_futures=True)
        for future,(source,index) in futures.items():
            if future.cancelled():continue
            try: result=future.result()
            except Exception as e: result=dict(source=source,index=index,error=str(e))
            db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?)',(source,index,json.dumps(result)))
            if source=='google_maps' and 'result' in result:
                for place in result['result']['places']:
                    db.execute('INSERT OR IGNORE INTO places VALUES(?,?,?)',(place['data_id'],json.dumps(place),index))
        db.commit()
        ids=[json.loads(r[0])['campaign_id'] for r in db.execute("SELECT result FROM tasks WHERE source<>'google_maps'") if 'campaign_id' in json.loads(r[0])]
        for _ in range(36):
            final=sample(start,ids)
            if not final['inflight']:break
            time.sleep(5)
        save('final.json',dict(at=now(),data=final,segments=state['segments'],maps_run_unique=db.execute('SELECT count(*) FROM places').fetchone()[0],stopped=True))
if __name__=='__main__':main()
