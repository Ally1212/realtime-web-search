"""Deploy the reviewed website package over r2; only additive SERP schema changes."""
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
PACKAGE = ROOT/'state/serp-website-20261010'
PROJECT = 'realtime-web-search-openserp'


def command(args, source=None, timeout=180):
    return subprocess.run(args, cwd=ROOT, input=source, text=True, capture_output=True, check=True, timeout=timeout).stdout


def save(name, value):
    (PACKAGE/name).write_text(json.dumps(value, indent=2, default=str)+'\n')


def event(phase):
    row=dict(at=datetime.now(timezone.utc).isoformat(),phase=phase)
    with (PACKAGE/'deployment-events.jsonl').open('a') as f: f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)


def inventory():
    ids=command(['docker','ps','-q','--filter','label=com.docker.compose.project='+PROJECT]).split()
    rows=json.loads(command(['docker','inspect',*ids]))
    return [dict(id=r['Id'],service=r['Config']['Labels'].get('com.docker.compose.service'),image=r['Image'],restarts=r['RestartCount'],state=r['State']['Status']) for r in rows]


def probe():
    return json.loads(command(['docker','exec','-i',PROJECT+'-local-worker-1','python','-'], '''import json,os
from realtime.serp_store import SerpStore
s=SerpStore(os.environ['DATABASE_URL'])
with s.store.connect() as db:
 p=db.execute('SELECT count(*) count,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
 r=db.execute('SELECT * FROM serp_supply_runs').fetchall()
print(json.dumps(dict(pages=p,supply=r,content_fetch_enabled=s.content_fetch_enabled()),default=str))
'''))


def check():
    if socket.gethostname()!='vm-ubuntu-4c8g-us': raise RuntimeError('wrong host')
    if (PACKAGE/'mutation-started.json').exists(): raise RuntimeError('prior deployment; inspect evidence before retry')
    m=json.loads((PACKAGE/'manifest.json').read_text())
    observed={str(p.relative_to(PACKAGE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PACKAGE/'code').rglob('*.py')}
    if observed!=m['source_sha256']: raise RuntimeError('source hashes differ')
    for name,sha in m['deployment_sha256'].items():
        if hashlib.sha256((PACKAGE/name).read_bytes()).hexdigest()!=sha: raise RuntimeError('deployment input hashes differ')
    old=ROOT/'state/google-sources-20261010/revisions/r2/compose.sources.json'
    if (ROOT/'compose.override.yaml').read_bytes()!=old.read_bytes(): raise RuntimeError('unexpected production override')
    rows=inventory()
    if sorted(r['service'] for r in rows)!=sorted(['web','local-worker','openserp','postgres','valkey','proxy-relay']): raise RuntimeError('unexpected services')
    state=probe()
    if state['content_fetch_enabled'] is not False: raise RuntimeError('body must remain off')
    command(['docker','compose','-f','compose.yaml','-f',str(PACKAGE/'compose.website.json'),'config','--quiet'])
    command(['sudo','-n','systemd-analyze','verify',str(PACKAGE/'realtime-google-serp.service')])
    if os.statvfs(PACKAGE).f_bavail*os.statvfs(PACKAGE).f_frsize<1024**3: raise RuntimeError('insufficient backup space')
    return rows,state


def apply():
    rows,state=check(); save('inventory-before.json',rows);save('baseline.json',state)
    (PACKAGE/'previous-compose.override.yaml').write_bytes((ROOT/'compose.override.yaml').read_bytes())
    (PACKAGE/'previous-systemd.service').write_bytes(Path('/etc/systemd/system/realtime-google-serp.service').read_bytes())
    save('mutation-started.json',dict(at=datetime.now(timezone.utc).isoformat()))
    command(['docker','stop','--time','20',PROJECT+'-local-worker-1',PROJECT+'-web-1'])
    event('writers_stopped')
    backup=PACKAGE/'before-website-serp.dump'
    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:
        subprocess.run(['docker','exec',PROJECT+'-postgres-1','pg_dump','-U','realtime','-d','realtime','-Fc','-t','serp_*'],cwd=ROOT,stdout=f,stderr=subprocess.PIPE,check=True,timeout=600)
    with backup.open('rb') as f:
        listing=subprocess.run(['docker','exec','-i',PROJECT+'-postgres-1','pg_restore','--list'],stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=120).stdout
    save('backup-check.json',dict(bytes=backup.stat().st_size,toc_lines=len(listing.splitlines()),scope='serp_*',restoration_tested=False))
    event('backup_ready')
    source='''import json,os
from realtime.config import Config
from realtime.serp_console import ConsoleStore
c=Config();s=ConsoleStore(c.database_url)
assert not s.content_fetch_enabled()
with s.store.connect() as db:
 before=db.execute('SELECT count(*) count,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
s.initialize_console(c)
with s.store.connect() as db:
 after=db.execute('SELECT count(*) count,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
assert before==after
print(json.dumps(dict(before=before,after=after,settings=s.settings(c)),default=str))
'''
    output=command(['docker','compose','-f','compose.yaml','-f',str(PACKAGE/'compose.website.json'),'run','--rm','--no-deps','-T','-w','/tmp','-e','PYTHONPATH=/app','--entrypoint','python','local-worker','-'],source,timeout=180)
    save('schema-result.json',json.loads(output));event('console_schema_ready')
    (ROOT/'compose.override.yaml').write_bytes((PACKAGE/'compose.website.json').read_bytes())
    command(['sudo','-n','install','-m','644',str(PACKAGE/'realtime-google-serp.service'),'/etc/systemd/system/realtime-google-serp.service'])
    command(['sudo','-n','systemctl','daemon-reload'])
    command(['sudo','-n','systemctl','restart','realtime-google-serp.service'])
    save('inventory-after.json',inventory());event('website_started')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    if args.apply: apply()
    else:
        rows,state=check();print(json.dumps(dict(checks_passed=True,inventory=rows,baseline=state),default=str))
