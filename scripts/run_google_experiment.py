"""Launch a frozen Google experiment using the existing collector environment."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('key')
    parser.add_argument('--hours', type=float, default=24)
    parser.add_argument('--query-plan', choices=['balanced', 'yield', 'dense'], default='yield')
    parser.add_argument('--body-workers', type=int, default=24)
    parser.add_argument('--body-max-rss-mib', type=int, default=192)
    parser.add_argument('--search-workers', type=int, default=4)
    parser.add_argument('--search-rps', type=float, default=2.0)
    parser.add_argument('--proxy-profile', choices=['private', 'public_google'], default='private')
    parser.add_argument('--google-providers', nargs='+', choices=['wml','wml_direct','searxng'], default=['wml','wml_direct','searxng'])
    parser.add_argument('--storage-budget-gib', type=float, default=10)
    parser.add_argument('--baseline', action='append', required=True)
    parser.add_argument('--environment-container', default='realtime-google-pipeline-2h-20260915')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', args.key) or not 0 < args.hours <= 24:
        raise SystemExit('Invalid experiment key or duration')
    root = Path(__file__).resolve().parents[1]
    container_name = 'realtime-google-' + args.key
    old = json.loads(subprocess.check_output(['docker', 'inspect', args.environment_container]))[0]
    snapshot = root/'state/benchmarks'/f'{args.key}-code'
    snapshot.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for source in sorted((root/'realtime').glob('*.py')):
        target = snapshot/source.name
        shutil.copy2(source, target)
        hashes[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {'experiment': args.key, 'source_snapshot': str(snapshot), 'hashes': hashes}
    (root/'docs/research'/f'{args.key}-code.json').write_text(json.dumps(manifest, indent=2)+'\n')
    cmd = ['docker', 'create', '--name', container_name, '--restart', 'on-failure:3',
           '--network', old['HostConfig']['NetworkMode'], '--entrypoint', 'python']
    for mount in old['Mounts']:
        source = str(snapshot) if mount['Destination'] == '/app/realtime' else mount.get('Name') if mount['Type']=='volume' else mount['Source']
        cmd += ['--mount', f"type={mount['Type']},source={source},target={mount['Destination']}" + (',readonly' if not mount['RW'] else '')]
    directory = '/app/state/experiments/' + args.key
    with tempfile.NamedTemporaryFile(mode='w', prefix='realtime-experiment-env-', delete=True) as env_file:
        for value in old['Config']['Env']:
            if '\n' in value:
                raise SystemExit('Environment contains a multiline value; cannot use an env file')
            env_file.write(value+'\n')
        env_file.flush()
        cmd += ['--env-file', env_file.name, old['Config']['Image'], '-m', 'realtime.cli',
                'benchmark-google', 'start', '--directory', directory, '--output', directory+'/export',
                '--hours', str(args.hours), '--languages', 'zh', '--executor', 'pipeline',
                '--query-plan', args.query_plan, '--body-workers', str(args.body_workers),
                '--body-max-rss-mib', str(args.body_max_rss_mib),
                '--search-workers', str(args.search_workers), '--search-rps', str(args.search_rps),
                '--proxy-profile', args.proxy_profile, '--storage-budget-gib', str(args.storage_budget_gib), '--google-providers', *args.google_providers, '--whale', '--remote-only']
        for baseline in args.baseline:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', baseline):
                raise SystemExit('Invalid baseline key')
            cmd += ['--baseline-run', '/app/state/experiments/'+baseline]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    subprocess.run(['docker', 'start', container_name], check=True, capture_output=True, text=True)
    print(json.dumps({'container': container_name, 'experiment': args.key,
                      'hours': args.hours, 'query_plan': args.query_plan, 'snapshot': str(snapshot)}))


if __name__ == '__main__':
    main()
