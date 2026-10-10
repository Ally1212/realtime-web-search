"""One-shot additive News/Trends upgrade; --apply is the only mutation mode."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/azureuser/projects/realtime-web-search-openserp')
PACKAGE = ROOT/'state/google-sources-20261010'
PROJECT = 'realtime-web-search-openserp'


def command(args, source=None, timeout=180):
    return subprocess.run(args,cwd=ROOT,input=source,text=True,capture_output=True,
                          check=True,timeout=timeout).stdout


def save(name,value):
    (PACKAGE/name).write_text(json.dumps(value,indent=2,default=str)+'\n')


def event(phase,**values):
    row=dict(at=datetime.now(timezone.utc).isoformat(),phase=phase,**values)
    with (PACKAGE/'deployment-events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)


def inventory():
    ids=command(['docker','ps','-q','--filter','label=com.docker.compose.project='+PROJECT]).split()
    rows=json.loads(command(['docker','inspect',*ids]))
    return [dict(id=r['Id'],service=r['Config']['Labels'].get('com.docker.compose.service'),
                 image=r['Image'],restarts=r['RestartCount'],started_at=r['State']['StartedAt']) for r in rows]


def check():
    if socket.gethostname()!='vm-ubuntu-4c8g-us':raise RuntimeError('wrong host')
    if (PACKAGE/'mutation-started.json').exists():raise RuntimeError('prior mutation; inspect phase evidence')
    m=json.loads((PACKAGE/'manifest.json').read_text())
    if {str(p.relative_to(PACKAGE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PACKAGE/'code').rglob('*.py')}!=m['source_sha256']:
        raise RuntimeError('source hashes differ')
    for name,sha in m['deployment_sha256'].items():
        if hashlib.sha256((PACKAGE/name).read_bytes()).hexdigest()!=sha:raise RuntimeError('deployment inputs differ')
    if (ROOT/'compose.override.yaml').read_bytes()!=(ROOT/'state/google-serp-20261010/compose.serp.json').read_bytes():
        raise RuntimeError('unexpected current compose override')
    rows=inventory()
    if sorted(r['service'] for r in rows)!=sorted(['web','local-worker','openserp','postgres','valkey','proxy-relay']):
        raise RuntimeError('unexpected production services')
    with urllib.request.urlopen('http://127.0.0.1:8093/api/collector-settings',timeout=30) as response:
        settings=json.load(response)
    if settings['content_fetch_enabled'] is not False:raise RuntimeError('body switch changed')
    command(['docker','compose','-f','compose.yaml','-f',str(PACKAGE/'compose.sources.json'),'config','--quiet'])
    command(['sudo','-n','systemd-analyze','verify',str(PACKAGE/'realtime-google-serp.service')])
    return rows


def apply():
    rows=check();save('inventory-before.json',rows)
    if os.statvfs(PACKAGE).f_bavail*os.statvfs(PACKAGE).f_frsize<1024**3:raise RuntimeError('insufficient backup space')
    # Retain a snapshot of all existing SERP tables; article tables are unchanged.
    backup=PACKAGE/'before-sources-serp.dump'
    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:
        subprocess.run(['docker','exec',PROJECT+'-postgres-1','pg_dump','-U','realtime','-d','realtime','-Fc','-t','serp_*'],
                       cwd=ROOT,stdout=f,stderr=subprocess.PIPE,check=True,timeout=600)
    with backup.open('rb') as f:
        listing=subprocess.run(['docker','exec','-i',PROJECT+'-postgres-1','pg_restore','--list'],cwd=ROOT,
                               stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=120).stdout
    save('backup-check.json',dict(bytes=backup.stat().st_size,toc_lines=len(listing.splitlines()),mode=oct(backup.stat().st_mode&0o777),scope='serp_*',restoration_tested=False))
    save('mutation-started.json',dict(at=datetime.now(timezone.utc).isoformat(),reason='user requested News and Trends link collection'))
    (PACKAGE/'previous-compose.override.yaml').write_bytes((ROOT/'compose.override.yaml').read_bytes())
    event('stopping_serp_worker')
    command(['docker','stop','--time','15',PROJECT+'-local-worker-1',PROJECT+'-web-1'])
    event('old_services_stopped')
    source='''import json,os
from realtime.serp_store import SerpStore
s=SerpStore(os.environ['DATABASE_URL'])
if s.content_fetch_enabled():raise RuntimeError('body must remain off')
with s.store.connect() as db:
 before=dict(pages=db.execute('SELECT count(*) n,max(id) max_id FROM pages').fetchone(),requests=db.execute('SELECT count(*) n FROM serp_requests').fetchone())
s.initialize(False)
with s.store.connect() as db:
 after=dict(pages=db.execute('SELECT count(*) n,max(id) max_id FROM pages').fetchone(),requests=db.execute('SELECT count(*) n FROM serp_requests').fetchone())
 if before!=after:raise RuntimeError('existing pages or requests changed')
 n=db.execute("UPDATE campaigns c SET sources='[\\"google_web\\",\\"google_news\\",\\"google_trends\\"]'::jsonb,updated_at=now() FROM serp_supply_campaigns r WHERE r.campaign_id=c.id AND r.run_id='google-serp-20261010' AND c.status='active' RETURNING c.id").fetchall()
print(json.dumps(dict(before=before,after=after,updated_supply_campaigns=len(n),content_fetch_enabled=s.content_fetch_enabled())))
'''
    result=command(['docker','compose','-f','compose.yaml','-f',str(PACKAGE/'compose.sources.json'),'run','--rm','--no-deps','-T','-w','/tmp','-e','PYTHONPATH=/app','--entrypoint','python','web','-'],source,timeout=300)
    save('schema-result.json',json.loads(result));event('sources_schema_ready')
    (ROOT/'compose.override.yaml').write_bytes((PACKAGE/'compose.sources.json').read_bytes())
    command(['docker','compose','config','--quiet'])
    command(['sudo','-n','install','-m','644',str(PACKAGE/'realtime-google-serp.service'),'/etc/systemd/system/realtime-google-serp.service'])
    command(['sudo','-n','systemctl','daemon-reload'])
    command(['sudo','-n','systemctl','restart','realtime-google-serp.service'])
    save('inventory-after.json',inventory());event('web_news_trends_services_started')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        if args.apply:apply()
        else:print(json.dumps(dict(checks_passed=True,inventory=check())))
    except Exception as exc:
        event('failed',error_type=type(exc).__name__)
        raise SystemExit(1)
