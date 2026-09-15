"""Audited, bounded half-open Google recovery probes beside a running experiment.

The user explicitly authorizes earlier probes of CAPTCHA cooldowns. Ordinary
reservations and their history stay intact; only selected host aliases use this
path. Real searches enter the existing ledger and existing body/Whale pipeline.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import time

from .campaign_store import CampaignStore, _POOLS
from .config import Config
from .discovery import SearchDiscovery
from .experiment_store import ExperimentStore
from .fetcher import normalize_url
from .free_google import GoogleTransport
from .proxy_pool import ProxyPool, ProxySynchronizer

DELAYS = (60, 120, 300, 600, 900, 1800)


def next_delay(delay):
    return next((d for d in DELAYS if d > delay), max(delay, DELAYS[-1]))


def transition(state, now, success, error):
    state = dict(state)
    if success:
        if not state.get('streak'):
            state['candidate_recovery_seconds'] = max(0, now-state['last_challenge_at'])
        state['streak'] = state.get('streak', 0)+1
        if state['streak'] >= 3:
            state['phase'] = 'recovered'
            state['confirmed_recovery_seconds'] = state['candidate_recovery_seconds']
        else:
            state['phase'] = 'confirming'
        state['due'] = now+60
    else:
        was_recovered = state.get('phase') == 'recovered'
        state['phase'], state['streak'] = 'waiting', 0
        if error in {'google_captcha', 'google_http_403', 'google_http_429'}:
            state['last_challenge_at'] = now
            state['timed_challenge'] = True
            state['due'] = now+state['base_delay'] if was_recovered else now+state['delay']
            state['delay'] = next_delay(state['base_delay'] if was_recovered else state['delay'])
        else:
            # Transport/layout failures do not establish a CAPTCHA recovery time.
            state['due'] = now+max(300, state['delay'])
    return state


def ready_hosts(states, now):
    # A newly timed challenge is the rare evidence needed for short intervals.
    # Do not let old, overdue initial checks postpone a 1/2/5-minute trial.
    def priority(state):
        # Confirmation priority must not starve recovered production hosts.
        if now-state['due'] >= 180:return -1
        if state['phase']=='waiting' and state.get('timed_challenge'):return 0
        if state['phase']=='confirming':return 1
        return 2
    return sorted((s for s in states if s['due']<=now),key=lambda s:(priority(s),s['due']))


def reserve_probe(production, aliases, minimum_interval=60, *, source_name="google_web"):
    """One explicit half-open request, sharing the real global token timeline.

    Never resets the global circuit or endpoint cooldowns. Host aliases remain
    unavailable to legacy workers while this request is in flight.
    """
    with production.connect() as db:
        with db.transaction():
            source = db.execute("SELECT * FROM discovery_source_runtime WHERE source=%s FOR UPDATE", (source_name,)).fetchone()
            if source is None:
                raise RuntimeError('existing Google global budget is required')
            rows = db.execute('SELECT * FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s) ORDER BY proxy_key_hash FOR UPDATE', (sorted(aliases),)).fetchall()
            if len(rows) != len(set(aliases)):
                raise RuntimeError('probe cannot introduce an unknown endpoint')
            now = datetime.now(timezone.utc)
            host_due = max([now] + [r['last_used_at']+timedelta(seconds=minimum_interval) for r in rows if r['last_used_at']])
            if host_due > now:
                return {'allowed': False, 'wait': (host_due-now).total_seconds()}
            due = max(now, source['next_request_at'])
            rate = min(4, max(.01, float(source['current_rps'])))
            db.execute("UPDATE discovery_source_runtime SET next_request_at=%s WHERE source=%s", (due+timedelta(seconds=1/rate),source_name))
            db.execute("UPDATE google_proxy_sessions SET last_used_at=%s,cooldown_until=GREATEST(cooldown_until,%s) WHERE proxy_key_hash=ANY(%s)",
                       (due, due+timedelta(seconds=120), sorted(aliases)))
            return {'allowed': True, 'wait': (due-now).total_seconds(), 'half_open_global': bool(source['circuit_until'] and source['circuit_until']>now),
                    'original_cooldowns': {r['proxy_key_hash']:r['cooldown_until'].timestamp() if r['cooldown_until'] else None for r in rows}}


def lease_query(store, now):
    store.db.execute('BEGIN IMMEDIATE')
    try:
        row = store.db.execute("SELECT q.*,s.page FROM queries q JOIN schedule s ON s.query_id=q.id WHERE q.enabled=1 AND q.family='site' AND s.due<=? ORDER BY q.last_served,q.id,s.page LIMIT 1", (now,)).fetchone()
        if row:
            store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=?', (now+120,row['id'],row['page']))
            store.db.execute('UPDATE queries SET last_served=? WHERE id=?', (now,row['id']))
        store.db.commit()
        return dict(row) if row else None
    except Exception:
        store.db.rollback()
        raise


def recovery_query(store, state):
    if state['phase']=='recovered':
        return lease_query(store,time.time())
    # Use a known-positive broad query while measuring recovery. Sparse
    # business queries must not be mistaken for an unhealthy Google exit.
    query='site:www.53ai.com 人工智能'
    key=hashlib.sha256(('recovery-control:zh:'+query).encode()).hexdigest()[:24]
    page=1+state.get('streak',0)%3
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO queries(id,family,query,language,topic,enabled) VALUES(?,'site',?,'zh','人工智能',0)",(key,query))
        store.db.execute('INSERT OR IGNORE INTO schedule(query_id,page) VALUES(?,?)',(key,page))
    return {'id':key,'family':'site','query':query,'language':'zh','page':page}


def write_summary(audit, output):
    rows = [json.loads(r[0]) for r in audit.execute('SELECT data FROM attempts ORDER BY id')]
    states = [json.loads(r[0]) for r in audit.execute('SELECT data FROM hosts')]
    completed = [r for r in rows if r.get('finished')]
    confirmed = [r['confirmed_recovery_seconds'] for r in states if r.get('phase')=='recovered']
    cohorts = {}
    for delay in (60,120,300,600,900,1800):
        attempts = [r for r in completed if r['base_delay']==delay and r['phase_before']!='recovered']
        cohorts[str(delay)] = {'attempts':len(attempts),'successful':sum(r['success'] for r in attempts)}
    report = {'observed_at':time.time(),'reserved_attempts':len(rows),'completed_attempts':len(completed),
              'successful_searches':sum(r['success'] for r in completed),
              'errors':dict(Counter(r['error'] for r in completed if r['error'])),
              'host_phases':dict(Counter(r['phase'] for r in states)),
              'confirmed_recovery_seconds':confirmed,'cohorts_by_initial_delay_seconds':cohorts,
              'minimum_confirmed_observed_seconds':min(confirmed) if confirmed else None,
              'default_recommendation':None,
              'limits':'Observed recovery intervals are upper bounds for sampled hosts; no universal optimum. Three consecutive nonempty Google pages required. Empty/transport/layout errors are not successful recovery.',
              'accounting':'Actual attempts and discovered links are recorded in the main experiment; only its original quality/dedup/body/publication/Whale pipeline can add accepted documents.'}
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');temp.replace(output)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--hosts',type=int,choices=range(1,25),default=12)
    parser.add_argument('--max-requests',type=int,choices=range(1,10001),default=600)
    args=parser.parse_args()
    root=args.directory/'recovery';root.mkdir(exist_ok=True);root.chmod(0o700)
    lock=(root/'runner.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=Config();production=CampaignStore(config.database_url);pool=ProxyPool(config)
    store=ExperimentStore(args.directory)
    audit=sqlite3.connect(root/'audit.sqlite3');audit.execute('PRAGMA journal_mode=WAL');audit.execute('PRAGMA synchronous=FULL')
    audit.executescript('CREATE TABLE IF NOT EXISTS hosts(group_hash TEXT PRIMARY KEY,data TEXT); CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,data TEXT);')
    (root/'audit.sqlite3').chmod(0o600)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    ProxySynchronizer(config).sync('private')
    _,records=pool.cache.load('private');by_key={r.key:r for r in records}
    if not audit.execute('SELECT count(*) FROM hosts').fetchone()[0]:
        groups=defaultdict(list)
        for r in records:
            if r.fresh(120):groups[r.host].append(r)
        with production.connect() as db:
            history={r['proxy_key_hash']:r for r in db.execute('SELECT * FROM google_proxy_sessions').fetchall()}
        selected=0
        for host, rs in sorted(groups.items(),key=lambda pair:hashlib.sha256(pair[0].encode()).hexdigest()):
            hashes=[hashlib.sha256(r.key.encode()).hexdigest() for r in rs]
            rows=[history.get(h,{}) for h in hashes]
            if not all(r.get('last_error')=='google_captcha' and r.get('cooldown_until') for r in rows):continue
            chosen=min(rs,key=lambda r:(r.protocol!='http',r.latency_ms))
            group,aliases=pool.google_identity(chosen.key)
            delay=(60,120,300,600,900,1800)[selected%6]
            state={'group_hash':group,'key':chosen.key,'aliases':aliases,'base_delay':delay,'delay':delay,
                   'last_challenge_at':max(r['cooldown_until'].timestamp()-21600 for r in rows),
                   'phase':'initial','streak':0,'due':time.time(),
                   'initial_challenge_time_scope':'inferred from legacy six-hour cooldown; later failures timed directly'}
            audit.execute('INSERT INTO hosts VALUES(?,?)',(group,json.dumps(state)));selected+=1
            if selected>=args.hosts:break
        audit.commit()
        store.set('operator_recovery_probe_'+str(int(time.time())),{'selected_hosts':selected,'max_requests':args.max_requests,'cohorts_seconds':[60,120,300,600,900,1800],
                  'reason':'User explicitly requests measuring shorter CAPTCHA cooldowns; bounded half-open probes; no reset/restart of main experiment or blanket cooldown clearing','source':'realtime.google_recovery'})
    # A crash after dispatch leaves the response unknown. Such a gap cannot
    # count toward three consecutive successes after resuming.
    for raw, in audit.execute('SELECT data FROM attempts').fetchall():
        unfinished=json.loads(raw)
        if unfinished.get('error') in {'google_captcha','google_http_403','google_http_429'} and unfinished.get('finished'):
            row=audit.execute('SELECT data FROM hosts WHERE group_hash=?',(unfinished['group_hash'],)).fetchone()
            state=json.loads(row[0])
            if state['last_challenge_at']==unfinished['finished']:
                state['timed_challenge']=True
                audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
        if not unfinished.get('finished'):
            row=audit.execute('SELECT data FROM hosts WHERE group_hash=?',(unfinished['group_hash'],)).fetchone()
            state=json.loads(row[0]);state.update(streak=0,phase='waiting',due=time.time()+300)
            audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
    audit.commit()
    for raw, in audit.execute('SELECT data FROM hosts').fetchall():
        state=json.loads(raw)
        state['delay']=min(state['delay'],DELAYS[-1])
        with audit:audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
    transport=GoogleTransport(15,'zh',config.searxng_url)
    last_request=0.;last_sync=time.time();last_report=0.
    try:
        while not stopped and store.get('state')=='running' and time.time()+20<store.get('deadline'):
            now=time.time()
            if now-last_report>=30:
                print(json.dumps(write_summary(audit,root/'summary.json'),ensure_ascii=False),flush=True);last_report=now
            if audit.execute('SELECT count(*) FROM attempts').fetchone()[0]>=args.max_requests:break
            if now-last_sync>=300:
                try:
                    ProxySynchronizer(config).sync('private');_,records=pool.cache.load('private');by_key={r.key:r for r in records}
                except Exception:pass
                last_sync=now
            states=[json.loads(r[0]) for r in audit.execute('SELECT data FROM hosts')]
            due=ready_hosts(states,now)
            if not due or now-last_request<10:
                time.sleep(1);continue
            state=due[0];planned_due=state['due'];record=by_key.get(state['key'])
            if not record or not record.fresh(120):
                state['due']=now+60
                with audit:audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
                continue
            reservation=reserve_probe(production,state['aliases'])
            if not reservation['allowed']:
                state['due']=now+max(1,reservation['wait'])
                with audit:audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
                continue
            ready_at=time.time()+reservation.get('wait',0)
            while not stopped and time.time()<ready_at and time.time()+20<store.get('deadline'):
                time.sleep(min(1,max(.01,ready_at-time.time())))
            if stopped or time.time()+20>=store.get('deadline'):break
            query=recovery_query(store,state)
            if query is None:time.sleep(1);continue
            started=time.time();last_request=started
            evidence={'started':started,'group_hash':state['group_hash'],'base_delay':state['base_delay'],
                      'planned_due':planned_due,'dispatch_lateness_seconds':max(0,started-planned_due),
                      'challenge_timed_in_probe':bool(state.get('timed_challenge')),
                      'phase_before':state['phase'],'seconds_since_last_challenge':started-state['last_challenge_at'],
                      'reservation':reservation,'query':query['query'],'page':query['page'],
                      'query_design':'productive' if state['phase']=='recovered' else 'known_positive_control'}
            with audit:
                attempt_id=audit.execute('INSERT INTO attempts(data) VALUES(?)',(json.dumps(evidence),)).lastrowid
                state['due']=started+300
                audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(state),state['group_hash']))
            results=[];error=''
            try:
                results=transport.fetch('wml',query['query'],query['page'],pool._url('private',record),proxy_key=record.key)
            except Exception as exc:error=SearchDiscovery._error_code(exc)
            finished=time.time();success=bool(results) and not error
            if not error and not results:error='google_empty_recovery_unconfirmed'
            ev=transport.last_evidence
            attempt={'provider':'wml','query':query['query'],'page':query['page'],'success':not error,
                     'results':len(results),'seconds':round(finished-started,3),'error':error,
                     'proxy_hash':hashlib.sha256(record.key.encode()).hexdigest(),'http_status':ev.get('http_status'),
                     'recovery_probe':True,'recovery_attempt_id':attempt_id,'request_url':ev.get('request_url')}
            normalized=[{'url':normalize_url(r.url),'raw_url':r.url,'title':r.title} for r in results]
            search_id=store.search(query,query['page'],started,normalized,[attempt],error,False)
            if not error:
                with store.db:store.db.execute('UPDATE schedule SET due=? WHERE query_id=? AND page=?',(finished+86400,query['id'],query['page']))
            production.record_google_proxy_result(attempt['proxy_hash'],success=success,cooldown_seconds=config.google_web_proxy_cooldown_seconds if error in {'google_captcha','google_http_403','google_http_429'} else 300 if error else 0,error_code=error,elapsed_seconds=finished-started,result_count=len(results),http_status=ev.get('http_status'))
            for source in ('google_web','google_wml'):
                production.record_discovery_result(source,success=success,limited=error in {'google_captcha','google_http_403','google_http_429'},captcha=error=='google_captcha',result_count=len(results),novel_count=0,maximum_rps=4,error_code=error,elapsed_seconds=finished-started,shared_exit=False)
            updated=transition(state,finished,success,error)
            if success and not state.get('streak'):
                # The recovery observation is the request start, not response latency.
                updated['candidate_recovery_seconds']=evidence['seconds_since_last_challenge']
            evidence.update(finished=finished,success=success,error=error,results=len(results),search_id=search_id,phase_after=updated['phase'])
            with audit:
                audit.execute('UPDATE attempts SET data=? WHERE id=?',(json.dumps(evidence),attempt_id))
                audit.execute('UPDATE hosts SET data=? WHERE group_hash=?',(json.dumps(updated),state['group_hash']))
            print(json.dumps(evidence,ensure_ascii=False),flush=True)
    finally:
        write_summary(audit,root/'summary.json');transport.close();audit.close();store.db.close()
        for p in _POOLS.values():p.close()


if __name__=='__main__':main()
