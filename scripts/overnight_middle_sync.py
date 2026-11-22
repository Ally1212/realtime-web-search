"""Durable backfill and tail of this run, with terminal receipts and pinned readback."""
import argparse, concurrent.futures, fcntl, hashlib, json, sqlite3, subprocess, sys, time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp')
RUN_ID='overnight-20261117'
STATE=ROOT/'state'/RUN_ID
WEB_SUPPLY_RUN=None
BATCH_SIZE=20
WEB_MAX_UNCONFIRMED=4*BATCH_SIZE
SOURCES=['google_web','google_news','google_trends','google_images','google_maps']
CONTAINERS=dict(google_web='searchflow-middle-live-1',google_images='searchflow-middle-live-1',google_news='searchflow-middle-news-1',google_trends='searchflow-middle-trends-1',google_maps='google-workbench-backend-maps-1')
def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()
def message(source,identity,record,count,observed_at=None):
    if observed_at:observed_at=datetime.fromisoformat(observed_at.replace('Z','+00:00')).isoformat()
    platform={'google_web':'google_search','google_images':'google_search'}.get(source,source)
    dataset='google_maps_raw' if source=='google_maps' else 'social_media_raw'
    namespace='pressure-20261117' if RUN_ID=='overnight-20261117' else RUN_ID
    key=namespace+':'+source+':'+str(identity)
    payload=dict(schema_version='searchflow-pressure-observation-1',run_id=RUN_ID,source=source,
                 observation_id=str(identity),observed_at=observed_at,observed_at_known=observed_at is not None,
                 result_count=count,result_unit='places' if source=='google_maps' else 'result_occurrences',record=record)
    received=observed_at or now()
    m=dict(schema_version='whale.ingest.v1',dataset_id=dataset,
        source=dict(source_platform=platform,source_name='searchflow.pressure.'+source,source_record_key=key,
                    payload_hash='sha256:'+digest(payload),received_at=received),
        discovery=dict(collector='searchflow.pressure',acquisition_type='crawler',metadata=dict(run_id=RUN_ID,source=source)),
        content=dict(external_content_id=key,content_type='search_result',title=(source+' '+str(record.get('query') or record.get('parameters',{}).get('query') or identity))[:500],platform_payload=payload),
        provided_capabilities=['identity','title'])
    m['metrics']=dict(snapshot_at=received,platform_metrics=dict(result_count=count))
    if observed_at is None:
        m['discovery']['metadata']['snapshot_time_basis']='sync_preparation_time; original collection time unknown'
    return m
def connect():
    c=sqlite3.connect(STATE/'middle-sync.sqlite',timeout=30);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=30000');return c
def rpc(source,action,**payload):
    script = Path(__file__).resolve()
    rpc_script = script.with_name(script.stem.removesuffix('_sync') + '_rpc.py')
    p=subprocess.run(['docker','exec','-i',CONTAINERS[source],'python','-c',rpc_script.read_text()],input=json.dumps(dict(source=source,action=action,**payload)),capture_output=True,text=True,timeout=180)
    try:r=json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:raise RuntimeError('rpc_unavailable_'+source) from None
    if p.returncode or r.get('error'):
        error=RuntimeError(r.get('error','rpc_failed'))
        error.retry_after=r.get('retry_after',0)
        error.status_code=r.get('http_status')
        raise error
    return r
def initialize():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS outbox(source TEXT,identity TEXT,message TEXT NOT NULL,result_count INTEGER NOT NULL,state TEXT NOT NULL DEFAULT \'prepared\',receipt TEXT,proof TEXT,PRIMARY KEY(source,identity))')
        c.execute('CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS health(source TEXT PRIMARY KEY,at TEXT,error TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS retry_state(source TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_at REAL NOT NULL)')
        if WEB_SUPPLY_RUN:
            c.execute('CREATE TABLE IF NOT EXISTS web_url_credit(identity TEXT PRIMARY KEY, new_urls INTEGER NOT NULL CHECK(new_urls>=0))')
def discover_web_window(manifest):
    initial=[manifest['started_at'],'00000000-0000-0000-0000-000000000000']
    finished=(STATE/'final.json').exists() and now()>=manifest['deadline']
    with connect() as c:
        row=c.execute("SELECT value FROM cursors WHERE source='google_web'").fetchone()
        cursor=json.loads(row['value']) if row and not finished else initial
        excluded=[r['identity'] for r in c.execute("SELECT identity FROM outbox WHERE source='google_web'")] if finished else []
    rows=rpc('google_web','scan',start=manifest['started_at'],deadline=manifest['deadline'],
             cursor=cursor,ids=[],exclude=excluded,supply_run=WEB_SUPPLY_RUN)['rows']
    with connect() as c:
        for item in rows:
            record=item['row']
            identity=record['request_id']
            payload=message('google_web',identity,record,item['count'],record['completed_at'])
            c.execute("INSERT OR IGNORE INTO outbox(source,identity,message,result_count) VALUES('google_web',?,?,?)",
                      (identity,json.dumps(payload,ensure_ascii=False),item['count']))
            c.execute('INSERT OR IGNORE INTO web_url_credit(identity,new_urls) VALUES(?,?)',
                      (identity,item['new_urls']))
        if rows:
            last=rows[-1]['row']
            c.execute("INSERT OR REPLACE INTO cursors VALUES('google_web',?)",
                      (json.dumps([last['completed_at'],last['request_id']]),))
    return len(rows)

def discover(source):
    manifest=json.loads((STATE/'manifest.json').read_text())
    if WEB_SUPPLY_RUN:
        return discover_web_window(manifest)
    with connect() as c:
        old=c.execute('SELECT value FROM cursors WHERE source=?',(source,)).fetchone()
        cursor=json.loads(old['value']) if old else (-1 if source=='google_maps' else [manifest['started_at'],'00000000-0000-0000-0000-000000000000'])
    with sqlite3.connect(STATE/'results.sqlite',timeout=30) as original:
        if source=='google_maps':
            rows=original.execute('SELECT idx,result FROM tasks WHERE source=? AND idx>? ORDER BY idx LIMIT 50',(source,cursor)).fetchall()
            messages=[]
            for idx,raw in rows:
                r=json.loads(raw)
                if 'result' in r:
                    messages.append((str(idx),message(source,idx,r,len(r['result']['places']),r.get('completed_at')),len(r['result']['places'])))
            new_cursor=rows[-1][0] if rows else cursor
        else:
            ids=[json.loads(r[0])['campaign_id'] for r in original.execute('SELECT result FROM tasks WHERE source=?',(source,)) if 'campaign_id' in json.loads(r[0])]
            exclude=[]
            if source!='google_web' or ((STATE/'final.json').exists() and now()>=manifest['deadline']):
                cursor=[manifest['started_at'],'00000000-0000-0000-0000-000000000000']
                with connect() as c:exclude=[r['identity'] for r in c.execute('SELECT identity FROM outbox WHERE source=?',(source,))]
            found=rpc(source,'scan',start=manifest['started_at'],deadline=manifest['deadline'],cursor=cursor,ids=ids,exclude=exclude)
            rows=found['rows'];messages=[]
            for item in rows:
                r=item['row'];identity=r['request_id']
                messages.append((identity,message(source,identity,r,item['count'],r['completed_at']),item['count']))
            new_cursor=[rows[-1]['row']['completed_at'],rows[-1]['row']['request_id']] if rows else cursor
    with connect() as c:
        c.executemany('INSERT OR IGNORE INTO outbox(source,identity,message,result_count) VALUES(?,?,?,?)',[(source,i,json.dumps(m,ensure_ascii=False),n) for i,m,n in messages])
        c.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',(source,json.dumps(new_cursor)))
    return len(rows)
def batch(c,source,state,limit=BATCH_SIZE):
    rows=[];size=0
    # A slow receipt should wait behind other rows that have not been polled.
    order="coalesce(json_extract(receipt,'$.polled_at'),''),rowid" if state=='submitted' else 'rowid'
    for r in c.execute(f'SELECT * FROM outbox WHERE source=? AND state=? ORDER BY {order} LIMIT ?',(source,state,limit)):
        if rows and size+len(r['message'].encode())>3*1024**2:break
        rows.append(dict(r));size+=len(r['message'].encode())
    return rows
def upload_prepared(source,rows):
    identities=[(source,r['identity']) for r in rows]
    messages=[json.loads(r['message']) for r in rows]
    # A process may die during the RPC. Persist the attempted state first
    # so a restart queries receipts before retrying any ambiguous submission.
    with connect() as c:
        c.executemany("UPDATE outbox SET state='submitted' WHERE source=? AND identity=?",identities)
    try:
        return rpc(source,'upload',messages=messages)
    except Exception as error:
        if getattr(error,'status_code',None)==429:
            with connect() as c:
                c.executemany("UPDATE outbox SET state='prepared' WHERE source=? AND identity=?",identities)
        raise


def advance(source, *, upload_limit=BATCH_SIZE):
    with connect() as c:rows=batch(c,source,'submitted')
    if rows:
        data=rpc(source,'poll',messages=[json.loads(r['message']) for r in rows],receipts=[json.loads(r['receipt']) if r['receipt'] else {} for r in rows])
        checked=now()
        with connect() as c:
            for r,receipt in zip(rows,data['receipts'],strict=True):
                status=receipt['status']
                state={'unknown':'prepared','failed':'rejected'}.get(status,'submitted')
                if status in ('accepted','duplicate') and receipt.get('document_id') and receipt.get('version_id'):
                    state='accepted'
                c.execute('UPDATE outbox SET state=?,receipt=? WHERE source=? AND identity=?',
                          (state,json.dumps(dict(receipt,polled_at=checked)),source,r['identity']))
    with connect() as c:rows=batch(c,source,'accepted')
    if rows:
        proofs=rpc(source,'verify',messages=[json.loads(r['message']) for r in rows],receipts=[json.loads(r['receipt']) for r in rows])['proofs']
        if len(proofs)!=len(rows):raise ValueError('proof_count_mismatch')
        with connect() as c:
            for r,proof in zip(rows,proofs,strict=True):
                receipt=json.loads(r['receipt'])
                if proof['document_id']!=receipt['document_id'] or proof['version_id']!=receipt['version_id']:raise ValueError('proof_identity_mismatch')
                c.execute("UPDATE outbox SET state='verified',proof=? WHERE source=? AND identity=?",(json.dumps(dict(proof,verified_at=now())),source,r['identity']))
    with connect() as c:
        limit=upload_limit
        if WEB_SUPPLY_RUN:
            pending=c.execute("SELECT count(*) FROM outbox WHERE source=? AND state IN('submitted','accepted')",
                              (source,)).fetchone()[0]
            limit=max(0,min(limit,WEB_MAX_UNCONFIRMED-pending))
        rows=batch(c,source,'prepared',limit=limit)
    if rows:
        upload_prepared(source,rows)
    return bool(rows)
def report():
    value=dict(at=now(),run_id=RUN_ID,
               definition='One original search/Maps observation per document; counts are occurrences, not unique URLs or businesses. accepted=terminal receipt; verified=full pinned payload SHA256 readback.')
    with connect() as c:
        c.execute('BEGIN')
        value['counts']=[dict(r) for r in c.execute('SELECT source,state,count(*) documents,sum(result_count) result_occurrences FROM outbox GROUP BY source,state')]
        value['health']=[dict(r) for r in c.execute('SELECT * FROM health')]
        if WEB_SUPPLY_RUN:
            c.create_function('utc_before',2,lambda stamp,deadline: bool(
                stamp and datetime.fromisoformat(stamp)<datetime.fromisoformat(deadline)),deterministic=True)
            deadline=json.loads((STATE/'manifest.json').read_text())['deadline']
            value['web_historical_new_urls']=dict(c.execute("""
                SELECT coalesce(sum(w.new_urls),0) AS discovered,
                       coalesce(sum(w.new_urls) FILTER (WHERE o.state='verified'),0) AS verified,
                       coalesce(sum(w.new_urls) FILTER (
                           WHERE o.state='verified' AND
                           utc_before(json_extract(o.proof,'$.verified_at'),?)),0) AS verified_before_deadline
                FROM outbox o JOIN web_url_credit w USING(identity) WHERE o.source='google_web'
            """,(deadline,)).fetchone())
    p=STATE/'middle-report.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(p)
    return value
def worker(source, *, drain_prepared=False):
    with connect() as c:
        previous=c.execute('SELECT failures,retry_at FROM retry_state WHERE source=?',(source,)).fetchone()
    failures=previous['failures'] if previous else 0
    if previous and (remaining:=previous['retry_at']-time.time())>0:
        time.sleep(remaining)
    while True:
        try:
            # Scan even while uploads are backlogged; all identities are committed before sending.
            scanned=0 if drain_prepared else discover(source)
            if WEB_SUPPLY_RUN and failures:
                # Resume with one authorized snapshot after an error so a
                # recovery attempt places less load on the ingest endpoint.
                advance(source,upload_limit=1)
            else:
                advance(source)
            with connect() as c:
                if failures:
                    c.execute('DELETE FROM retry_state WHERE source=?',(source,))
                c.execute('INSERT OR REPLACE INTO health VALUES(?,?,NULL)',(source,now()))
            failures=0
            settled=(STATE/'final.json').exists() and now()>=json.loads((STATE/'manifest.json').read_text())['deadline']
            if drain_prepared or (settled and scanned==0):
                with connect() as c:pending=c.execute("SELECT count(*) FROM outbox WHERE source=? AND state NOT IN ('verified','rejected')",(source,)).fetchone()[0]
                if pending==0:return
            time.sleep(2)
        except Exception as e:
            failures+=1
            delay=max(min(300,5*2**min(failures,6)),getattr(e,'retry_after',0))
            with connect() as c:
                c.execute('INSERT OR REPLACE INTO health VALUES(?,?,?)',(source,now(),str(e)[:200]))
                c.execute('INSERT OR REPLACE INTO retry_state VALUES(?,?,?)',(source,failures,time.time()+delay))
            time.sleep(delay)
def require_audited_web_queue():
    final=json.loads((STATE/'final.json').read_text())
    audit=json.loads((STATE/'audit.json').read_text())['cutoff']
    if final['anomalies'] or final['inflight']:
        raise ValueError('Web collection has not settled cleanly')
    with connect() as c:
        queued=c.execute("""
            SELECT count(*) AS documents,count(w.identity) AS credits,
                   coalesce(sum(w.new_urls),0) AS urls
            FROM outbox o LEFT JOIN web_url_credit w ON w.identity=o.identity AND o.source='google_web'
            """).fetchone()
    if queued['documents']!=audit['successful'] or queued['credits']!=queued['documents'] or queued['urls']!=audit['new_urls']:
        raise ValueError('Web outbox disagrees with the cutoff audit')


def main():
    global STATE,RUN_ID,WEB_SUPPLY_RUN
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',nargs='?',choices=SOURCES)
    parser.add_argument('--web-window',type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only',action='store_true')
    mode.add_argument('--drain-prepared',action='store_true')
    parser.add_argument('--supply-run')
    args=parser.parse_args()
    if args.web_window:
        STATE=args.web_window.resolve(strict=True)
        RUN_ID=STATE.name
        WEB_SUPPLY_RUN=args.supply_run or json.loads((STATE/'manifest.json').read_text()).get('supply_run')
        if not WEB_SUPPLY_RUN:
            parser.error('--web-window requires --supply-run or manifest.supply_run')
        if args.source:
            parser.error('--web-window is already scoped to Google Web')
    elif args.prepare_only or args.drain_prepared or args.supply_run:
        parser.error('Web outbox options require --web-window')
    active_sources=['google_web'] if WEB_SUPPLY_RUN else SOURCES
    initialize()
    lock=(STATE/'middle-sync.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.prepare_only:
        while discover('google_web'):
            pass
        print(json.dumps(report()))
        return
    if args.drain_prepared:
        require_audited_web_queue()
    if args.source:
        source=args.source;discover(source)
        with connect() as c:
            rows=c.execute("SELECT * FROM outbox WHERE source=? AND state='prepared' ORDER BY rowid LIMIT 1",(source,)).fetchall()
        if rows:
            print(json.dumps(upload_prepared(source,rows)))
        print(json.dumps(report()));return
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_sources)) as executor:
        futures=[executor.submit(worker,source,drain_prepared=args.drain_prepared) for source in active_sources]
        while True:
            value=report();print(json.dumps(value),flush=True)
            if all(f.done() for f in futures):
                for f in futures:f.result()
                value['status']='incomplete' if any(row['state']=='rejected' for row in value['counts']) else 'complete'
                (STATE/'middle-final.json').write_text(json.dumps(value,indent=2))
                if value['status']=='incomplete':
                    raise SystemExit(1)
                return
            time.sleep(30)
if __name__=='__main__':main()
