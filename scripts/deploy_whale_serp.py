"""Freeze additive Whale link integration over the exact paused production code."""
import argparse
import hashlib
import json
import shutil
import socket
import subprocess
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp')
PACKAGE=ROOT/'state/whale-serp-20261011'
CURRENT=ROOT/'state/google-web-longrun-20261010/current-compose.json'
PROJECT='realtime-web-search-openserp'


def run(args,source=None,timeout=180):
    r=subprocess.run(args,cwd=ROOT,input=source,text=True,capture_output=True,timeout=timeout)
    if r.returncode:
        # Preserve diagnostics on the server; Compose can contain secrets, never print it.
        (PACKAGE/'last-command-error.txt').write_text(r.stderr)
        raise RuntimeError('deployment command failed: '+str(args[:3]))
    return r.stdout


def save(name,value):
    (PACKAGE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def api(path):
    with urllib.request.urlopen('http://127.0.0.1:8093'+path,timeout=30) as r:return json.load(r)


PROBE='''import json,os
from realtime.campaign_store import CampaignStore
s=CampaignStore(os.environ['DATABASE_URL'],initialize=False)
with s.connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY')
 pages=db.execute('SELECT count(*) n,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
 requests=db.execute('SELECT count(*) n,max(completed_at) latest FROM serp_requests').fetchone()
 supply=db.execute('SELECT * FROM serp_supply_runs WHERE run_id=%s',('google-serp-20261010',)).fetchone()
 inflight=db.execute("SELECT count(*) n FROM google_page_frontier WHERE state='running' AND lease_expires_at>now()").fetchone()['n']
 manual=db.execute("SELECT count(*) n FROM campaigns c WHERE status='active' AND NOT EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id) AND NOT EXISTS(SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id)").fetchone()['n']
print(json.dumps(dict(pages=pages,requests=requests,supply=supply,inflight=inflight,active_manual=manual),default=str))
'''


def probe():return json.loads(run(['docker','exec','-i',PROJECT+'-web-1','python','-'],PROBE))


def inventory():
    ids=run(['docker','ps','-q','--filter','label=com.docker.compose.project='+PROJECT]).split()
    rows=json.loads(run(['docker','inspect',*ids]))
    return {r['Config']['Labels']['com.docker.compose.service']:dict(id=r['Id'],image=r['Image'],running=r['State']['Running'],oom=r['State']['OOMKilled'],restarts=r['RestartCount']) for r in rows}


def hashes(directory):return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*.py')}


def prepare():
    if socket.gethostname()!='vm-ubuntu-4c8g-us':raise RuntimeError('wrong production host')
    if (PACKAGE/'prepared.json').exists():raise RuntimeError('already prepared; inspect rather than overwrite frozen code')
    if (PACKAGE/'mutation-started.json').exists():raise RuntimeError('prior mutation; inspect before retry')
    base=json.loads(CURRENT.read_text());old={};new=json.loads(CURRENT.read_text())
    for kind,service in [('worker','local-worker'),('web','web')]:
        volume=base['services'][service]['volumes'][0]
        source=Path(volume.split(':/app/realtime:ro')[0]);target=PACKAGE/'code'/kind/'realtime'
        if not source.is_dir() or not volume.endswith(':/app/realtime:ro'):raise RuntimeError('unexpected source mount')
        old[kind]=dict(path=str(source),hashes=hashes(source))
        shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for patch in ['lease-release.patch','features.patch']:
            result=run(['patch','--batch','--forward','-p1','--directory',str(target.parent),'-i',str(PACKAGE/'inputs'/patch)])
            (PACKAGE/f'{kind}-{patch}.log').write_text(result)
        for name in ['whale_serp.py','whale_serp_http.py','serp_page.py']:
            shutil.copyfile(PACKAGE/'inputs'/name,target/name)
        # Prepare Python bytecode in memory; leave immutable sources untouched.
        for path in target.rglob('*.py'):compile(path.read_bytes(),str(path),'exec')
        new['services'][service]['volumes']=[str(target)+':/app/realtime:ro']
        env=new['services'][service].setdefault('environment',{})
        env.update(WHALE_SERP_ENABLED='true',CONTENT_FETCH_ENABLED='false',SERP_CONTINUOUS_ENABLED='false',
                   WHALE_SUPPORTED_TASK_TYPES='${WHALE_SUPPORTED_TASK_TYPES:-keyword_search}')
        if service=='local-worker':
            env.update(WHALE_ENABLED='false',WHALE_BASE_URL='${WHALE_BASE_URL:-http://20.169.21.11}',
                       WHALE_COLLECTOR_API_KEY='${WHALE_COLLECTOR_API_KEY:-}',WHALE_AGENT_ID='${WHALE_AGENT_ID:-realtime-web-search-01}',
                       WHALE_DATASET_ID='${WHALE_DATASET_ID:-social_media_raw}',WHALE_SOURCE_PLATFORM='${WHALE_SOURCE_PLATFORM:-google_search}',
                       WHALE_SOURCE_NAME='${WHALE_SOURCE_NAME:-realtime-web-search}',WHALE_HEARTBEAT_SECONDS='5')
    save('previous-current-compose.json',base);shutil.copyfile(ROOT/'compose.override.yaml',PACKAGE/'previous-compose.override.yaml')
    save('compose.whale.json',new)
    run(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(PACKAGE/'compose.whale.json'),'config','--quiet'])
    save('manifest.json',dict(code=hashes(PACKAGE/'code'),original=old))
    save('prepared.json',dict(at=datetime.now(timezone.utc),scope='link-only Whale; no article schema migration'))
    print('PREPARED',flush=True)


def check():
    if socket.gethostname()!='vm-ubuntu-4c8g-us':raise RuntimeError('wrong production host')
    if (PACKAGE/'mutation-started.json').exists():raise RuntimeError('prior mutation; inspect before retry')
    m=json.loads((PACKAGE/'manifest.json').read_text())
    if hashes(PACKAGE/'code')!=m['code']:raise RuntimeError('new frozen code changed')
    for row in m['original'].values():
        if hashes(Path(row['path']))!=row['hashes']:raise RuntimeError('original frozen code changed')
    if CURRENT.read_bytes()!=(PACKAGE/'previous-current-compose.json').read_bytes():
        if json.loads(CURRENT.read_text())!=json.loads((PACKAGE/'previous-current-compose.json').read_text()):raise RuntimeError('current Compose changed')
    settings=api('/api/collector-settings');state=probe()
    if settings['continuous_enabled'] or settings['content_fetch_enabled'] or state['inflight'] or state['active_manual']:
        raise RuntimeError('collection/body must remain paused and drained')
    return settings,state,inventory()


def apply():
    settings,state,containers=check();save('before.json',dict(settings=settings,state=state,containers=containers))
    save('mutation-started.json',dict(at=datetime.now(timezone.utc)))
    run(['docker','stop','--time','30',PROJECT+'-local-worker-1',PROJECT+'-web-1'])
    # Explicitly execute only this independent additive schema, never CampaignStore.initialize().
    command=['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(PACKAGE/'compose.whale.json'),'run','--rm','-T','--no-deps','--entrypoint','python','local-worker','-c',
             'from realtime.config import Config; from realtime.whale_serp import WhaleSerpStore; WhaleSerpStore(Config().database_url).initialize(); print("Whale link schema ready")']
    run(command)
    shutil.copyfile(PACKAGE/'compose.whale.json',CURRENT);shutil.copyfile(PACKAGE/'compose.whale.json',ROOT/'compose.override.yaml')
    run(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(CURRENT),'up','-d','--no-build','--no-deps','web','local-worker'])
    save('applied.json',dict(at=datetime.now(timezone.utc)))
    print('APPLIED: verify HTTP, code hashes and paused state',flush=True)


def verify():
    before=json.loads((PACKAGE/'before.json').read_text());settings=api('/api/collector-settings');state=probe();containers=inventory()
    if settings!=before['settings'] or state!=before['state']:raise RuntimeError('paused settings, requests, pages or cursor changed')
    for name in ('openserp','proxy-relay','postgres','valkey'):
        if containers[name]!=before['containers'][name]:raise RuntimeError('unrelated service changed')
    status=api('/api/whale/status')
    if status['settings']['uploads_enabled'] or status['settings']['claims_enabled']:raise RuntimeError('Whale switches must remain paused')
    if not status['configured'] or not status['runtime_available']:raise RuntimeError('runtime/credentials unavailable')
    manifest=json.loads((PACKAGE/'manifest.json').read_text())
    if hashes(PACKAGE/'code')!=manifest['code']:raise RuntimeError('frozen source hash mismatch')
    for row in manifest['original'].values():
        if hashes(Path(row['path']))!=row['hashes']:raise RuntimeError('original frozen code changed')
    compose=json.loads(CURRENT.read_text())
    for service in ('web','local-worker'):
        expected=compose['services'][service]['volumes'][0].split(':/app/realtime:ro')[0]
        container=json.loads(run(['docker','inspect',containers[service]['id']]))[0]
        actual=[m for m in container['Mounts'] if m['Destination']=='/app/realtime']
        if len(actual)!=1 or actual[0]['Source']!=expected or actual[0]['RW']:
            raise RuntimeError('live code mount mismatch')
    if CURRENT.read_bytes()!=(ROOT/'compose.override.yaml').read_bytes():raise RuntimeError('startup Compose differs')
    revision=PACKAGE/'revisions/r2'
    if revision.exists() and str(revision) in compose['services']['web']['volumes'][0]:
        if hashes(revision/'code')!=json.loads((revision/'manifest.json').read_text()):raise RuntimeError('page revision hash mismatch')
        prior=json.loads((revision/'before.json').read_text())
        for name,row in containers.items():
            if name!='web' and row!=prior['containers'][name]:raise RuntimeError('page-only deployment changed another service')
    save('verified.json',dict(at=datetime.now(timezone.utc),settings=settings,state=state,containers=containers,whale=status,code_hashes_verified=True))
    if revision.exists() and str(revision) in compose['services']['web']['volumes'][0]:
        shutil.copyfile(PACKAGE/'verified.json',revision/'verified.json')
    print('VERIFIED: paused; data/cursor retained; unrelated services unchanged',flush=True)


def page_fix():
    """Create a new immutable web revision; retain the original worker and code."""
    verify()
    revision=PACKAGE/'revisions/r2'
    if revision.exists():raise RuntimeError('page revision already exists; inspect before retry')
    before=dict(settings=api('/api/collector-settings'),state=probe(),containers=inventory())
    revision.mkdir(parents=True)
    compose=json.loads(CURRENT.read_text())
    original=Path(compose['services']['web']['volumes'][0].split(':/app/realtime:ro')[0])
    target=revision/'code/web/realtime'
    shutil.copytree(original,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(PACKAGE/'inputs/serp_page-r2.py',target/'serp_page.py')
    compile((target/'serp_page.py').read_bytes(),str(target/'serp_page.py'),'exec')
    (revision/'before.json').write_text(json.dumps(before,indent=2,default=str))
    (revision/'previous-compose.json').write_text(CURRENT.read_text())
    compose['services']['web']['volumes']=[str(target)+':/app/realtime:ro']
    next_compose=revision/'compose.json'
    next_compose.write_text(json.dumps(compose,indent=2)+'\n')
    (revision/'manifest.json').write_text(json.dumps(hashes(revision/'code'),indent=2)+'\n')
    run(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(next_compose),'config','--quiet'])
    (revision/'mutation-started.json').write_text(json.dumps(dict(at=str(datetime.now(timezone.utc)))))
    shutil.copyfile(next_compose,CURRENT);shutil.copyfile(next_compose,ROOT/'compose.override.yaml')
    run(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(CURRENT),'up','-d','--no-build','--no-deps','web'])
    (revision/'applied.json').write_text(json.dumps(dict(at=str(datetime.now(timezone.utc)))))
    print('PAGE REVISION APPLIED; verify after HTTP readiness',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','check','apply','verify','page_fix']);args=p.parse_args()
    PACKAGE.mkdir(parents=True,exist_ok=True)
    if args.action=='check':check();print('CHECKED')
    else:globals()[args.action]()
