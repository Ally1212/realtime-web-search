"""Launch the bounded recovery sidecar without restarting the main collector."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('key')
    parser.add_argument('--experiment',required=True)
    parser.add_argument('--hosts',type=int,choices=range(1,25),default=12)
    parser.add_argument('--max-requests',type=int,choices=range(1,10001),default=600)
    parser.add_argument('--proxy-cooldown-seconds',type=int,choices=range(30,21601),default=300)
    args=parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v) for v in [args.key,args.experiment]):
        raise SystemExit('invalid run key')
    root=Path(__file__).resolve().parents[1]
    parent='realtime-google-'+args.experiment
    old=json.loads(subprocess.check_output(['docker','inspect',parent]))[0]
    if not old['State']['Running']:raise SystemExit('main collector must remain running')
    name='realtime-google-'+args.key
    snapshot=root/'state/benchmarks'/f'{args.key}-code';snapshot.mkdir(parents=True,exist_ok=False)
    hashes={}
    for source in sorted((root/'realtime').glob('*.py')):
        shutil.copy2(source,snapshot/source.name);hashes[source.name]=hashlib.sha256(source.read_bytes()).hexdigest()
    cmd=['docker','create','--name',name,'--restart','on-failure:3','--network',old['HostConfig']['NetworkMode'],'--entrypoint','python']
    for mount in old['Mounts']:
        source=str(snapshot) if mount['Destination']=='/app/realtime' else mount.get('Name') if mount['Type']=='volume' else mount['Source']
        cmd+=['--mount',f"type={mount['Type']},source={source},target={mount['Destination']}"+(',readonly' if not mount['RW'] else '')]
    with tempfile.NamedTemporaryFile('w',prefix='recovery-env-') as env:
        for value in old['Config']['Env']:
            if '\n' in value:raise SystemExit('multiline environment unsupported')
            env.write(value+'\n')
        env.flush()
        cmd+=['--env-file',env.name,'--env',f'GOOGLE_WEB_PROXY_COOLDOWN_SECONDS={args.proxy_cooldown_seconds}',old['Config']['Image'],'-m','realtime.google_recovery',
              '--directory','/app/state/experiments/'+args.experiment,'--hosts',str(args.hosts),'--max-requests',str(args.max_requests)]
        subprocess.run(cmd,check=True,capture_output=True,text=True)
    manifest={'sidecar':name,'experiment':args.experiment,'snapshot':str(snapshot),'hashes':hashes,
              'hosts':args.hosts,'max_requests':args.max_requests,'proxy_cooldown_seconds':args.proxy_cooldown_seconds,'main_collector_restarted':False}
    (root/'docs/research'/f'{args.key}-code.json').write_text(json.dumps(manifest,indent=2)+'\n')
    subprocess.run(['docker','start',name],check=True,capture_output=True,text=True)
    print(json.dumps({k:v for k,v in manifest.items() if k!='hashes'}))


if __name__=='__main__':main()
