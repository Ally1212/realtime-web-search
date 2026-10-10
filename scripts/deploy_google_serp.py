"""One-shot switch from the frozen article run to the SERP-only collector.

Only --apply changes production. Never rerun after mutation-started without
inspecting phase evidence; an error does not automatically restore article work.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/azureuser/projects/realtime-web-search-openserp')
PACKAGE = ROOT/'state/google-serp-20261010'
PROJECT = 'realtime-web-search-openserp'
OLD_WORKER = '1cf4a9bb7e474cb4bb2a9ad4746b2d4bde63887d42cc4291e0a9791a9f55405b'


def command(args, source=None, timeout=180):
    return subprocess.run(args,cwd=ROOT,input=source,text=True,capture_output=True,check=True,timeout=timeout).stdout


def save(name, value):
    p=PACKAGE/name
    p.write_text(json.dumps(value,indent=2,default=str)+'\n')


def event(phase, **values):
    row=dict(at=datetime.now(timezone.utc).isoformat(),phase=phase,**values)
    with (PACKAGE/'deployment-events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)


def properties(unit):
    return dict(line.split('=',1) for line in command(['systemctl','show',unit,'-p','MainPID','-p','ActiveState']).splitlines())


def inventory():
    ids=command(['docker','ps','-q','--filter','label=com.docker.compose.project='+PROJECT]).split()
    rows=json.loads(command(['docker','inspect',*ids]))
    return [dict(id=r['Id'],service=r['Config']['Labels'].get('com.docker.compose.service'),image=r['Image'],
                 started_at=r['State']['StartedAt'],pid=r['State']['Pid'],restarts=r['RestartCount']) for r in rows]


def check():
    if socket.gethostname()!='vm-ubuntu-4c8g-us' or not ROOT.is_dir():raise RuntimeError('wrong deployment host')
    m=json.loads((PACKAGE/'manifest.json').read_text())
    files={str(p.relative_to(PACKAGE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PACKAGE/'code').rglob('*.py')}
    if files!=m['source_sha256']:raise RuntimeError('frozen sources differ')
    if not all(hashlib.sha256((PACKAGE/p).read_bytes()).hexdigest()==v for p,v in m['deployment_sha256'].items()):raise RuntimeError('deployment inputs differ')
    if (PACKAGE/'mutation-started.json').exists():raise RuntimeError('prior mutation exists; inspect phases')
    rows=inventory()
    worker=[r for r in rows if r['service']=='local-worker']
    if len(worker)!=1 or worker[0]['id']!=OLD_WORKER or worker[0]['restarts']!=0:raise RuntimeError('old worker identity differs')
    if any(r['service'] not in {'local-worker','web','openserp','proxy-relay','postgres','valkey'} for r in rows):raise RuntimeError('additional production writer')
    if properties('realtime-google-hourly.service')['ActiveState']!='active':raise RuntimeError('old supervisor not active')
    if properties('realtime-google-revision-transition-20261009.service')['MainPID']!='0':raise RuntimeError('old automatic migration is still alive')
    if properties('realtime-google-serp.service')['ActiveState']!='inactive':raise RuntimeError('new service already active')
    command(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(PACKAGE/'compose.serp.json'),'config','--quiet'])
    command(['sudo','-n','systemd-analyze','verify',str(PACKAGE/'realtime-google-serp.service')])
    return rows


def apply():
    rows=check();save('inventory-before.json',rows)
    if os.statvfs(PACKAGE).f_bavail*os.statvfs(PACKAGE).f_frsize<8*1024**3:raise RuntimeError('insufficient backup space')
    # Preserve all completed hour reports before honoring the explicit feature change.
    command(['sudo','-n','systemctl','start','realtime-google-hourly-archive.service'])
    save('mutation-started.json',dict(at=datetime.now(timezone.utc).isoformat(),reason='user explicitly requested content off and Google SERP focus'))
    event('stopping_article_writers')
    command(['sudo','-n','systemctl','disable','--now','realtime-google-hourly.service','realtime-google-hourly-archive.timer','realtime-google-resources.timer'])
    command(['sudo','-n','systemctl','stop','realtime-google-hourly-archive.service','realtime-google-resources.service'])
    command(['docker','stop','--time','10',PROJECT+'-local-worker-1',PROJECT+'-web-1'])
    event('article_writers_stopped')
    backup=PACKAGE/'before-serp-schema.dump'
    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:
        subprocess.run(['docker','exec',PROJECT+'-postgres-1','pg_dump','-U','realtime','-d','realtime','-Fc'],cwd=ROOT,stdout=f,stderr=subprocess.PIPE,check=True,timeout=1800)
    with backup.open('rb') as f:
        listing=subprocess.run(['docker','exec','-i',PROJECT+'-postgres-1','pg_restore','--list'],cwd=ROOT,stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=120).stdout
    save('backup-check.json',dict(path=str(backup),bytes=backup.stat().st_size,mode=oct(backup.stat().st_mode&0o777),toc_lines=len(listing.splitlines()),restoration_tested=False))
    event('backup_verified',bytes=backup.stat().st_size)
    source='''import json,os
from realtime.serp_store import SerpStore
store=SerpStore(os.environ['DATABASE_URL'])
with store.store.connect() as db:
 other=db.execute("SELECT count(*) n FROM campaigns c WHERE status='active' AND NOT EXISTS(SELECT 1 FROM wml_continuous_campaigns r WHERE r.campaign_id=c.id AND r.run_id=%s)",('google-hourly-20261009-continuous-best',)).fetchone()['n']
 if other:raise RuntimeError('unrelated active campaign')
 before=db.execute('SELECT count(*) n,max(id) max_id FROM pages').fetchone()
store.initialize(False)
store.set_content_fetch(False)
with store.store.connect() as db:
 after=db.execute('SELECT count(*) n,max(id) max_id FROM pages').fetchone()
 if before!=after:raise RuntimeError('article pages changed during SERP migration')
 db.execute("UPDATE campaigns c SET status='stopped',updated_at=now() FROM wml_continuous_campaigns r WHERE r.campaign_id=c.id AND r.run_id=%s AND c.status='active'",('google-hourly-20261009-continuous-best',))
 historical=db.execute('SELECT count(*) n FROM serp_urls WHERE historical').fetchone()['n']
print(json.dumps(dict(pages_before=before,pages_after=after,historical_url_baseline=historical,content_fetch_enabled=store.content_fetch_enabled())))
'''
    output=command(['docker','compose','-p',PROJECT,'run','--rm','--no-deps','-T','-w','/tmp','-e','PYTHONPATH=/app/state/google-serp-20261010/code','--entrypoint','python','web','-'],source=source,timeout=900)
    result=json.loads(output);save('schema-result.json',result);event('serp_schema_ready',historical_url_baseline=result['historical_url_baseline'])
    old=ROOT/'state/google-hourly-20261009-continuous-best/manifest.json'
    m=json.loads(old.read_text());m['status']='stopped_for_serp_only_transition';m['finished_at']=datetime.now(timezone.utc).isoformat();m['transition_to']='google-serp-20261010'
    old.write_text(json.dumps(m,indent=2,default=str)+'\n')
    hours=sorted((ROOT/'state/google-hourly-20261009/hourly-archive').glob('continuous-hour-*.json'))
    right=json.loads(hours[-1].read_text())['measured_until'] if hours else m['worker_started_at']
    save('old-partial-window-excluded.json',dict(start=right,end=m['finished_at'],valid_for_comparison=False,reason='explicit user SERP-only feature change'))
    command(['sudo','-n','install','-m','644',str(PACKAGE/'realtime-google-serp.service'),'/etc/systemd/system/realtime-google-serp.service'])
    command(['sudo','-n','systemctl','daemon-reload'])
    command(['sudo','-n','systemctl','enable','--now','realtime-google-serp.service'])
    event('serp_services_started')
    save('inventory-after.json',inventory())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        if args.apply:apply()
        else:print(json.dumps(dict(checks_passed=True,inventory=check())))
    except Exception as exc:
        event('failed',error_type=type(exc).__name__)
        raise SystemExit(1)
