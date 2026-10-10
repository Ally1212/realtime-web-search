"""Retire a verified full-hour predecessor and launch the audited successor.

Default mode only checks. --wait --apply is an explicit one-shot deployment;
exceptions are recorded without raw command output or credentials. No automatic
retry after a mutation: inspect phase evidence before deciding how to recover.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREDECESSOR = 'google-hourly-20261009'
SUCCESSOR = 'google-hourly-20261009-audited-fresh-v2'
WORKER = 'realtime-web-search-openserp-local-worker-1'
WEB = 'realtime-web-search-openserp-web-1'
PROJECT = 'realtime-web-search-openserp'
OLD_UNIT = 'realtime-google-hourly.service'
OLD_TIMERS = ['realtime-google-hourly-archive.timer', 'realtime-google-resources.timer']
OLD_JOBS = ['realtime-google-hourly-archive.service', 'realtime-google-resources.service']
NEW_UNIT = 'realtime-google-audited.service'
NEW_TIMERS = ['realtime-google-audited-archive.timer', 'realtime-google-audited-resources.timer']


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str) + '\n')
    temporary.replace(path)


def sha(path):
    with path.open('rb') as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def boundary(start, now):
    """Skip an expired handoff window; never stop in the middle of a later hour."""
    index = max(0, int((now - start).total_seconds() // 3600) - 1)
    right = start + timedelta(hours=index + 1)
    if now > right + timedelta(seconds=95):
        index += 1
        right += timedelta(hours=1)
    return index, right


def can_stop(now, right):
    # 60s commit grace, then leave 20s for supervisor and worker shutdown.
    return right + timedelta(seconds=60) <= now <= right + timedelta(seconds=100)


def hour_verified(gate, report, manifest, index, right):
    return (report.get('run') == manifest['run'] and report.get('window_index') == index
        and gate.full_hour(report) and gate.timestamp(report['measured_until']) == right
        and gate.timestamp(manifest['last_verified_running_at']) >= right)


def audit_verified(gate, audit, report, digest):
    keys = gate.COUNT_KEYS
    return (audit.get('matches_frozen_report') is True and audit.get('database_read_only') == 'on'
        and audit.get('frozen_report_sha256') == digest and set(audit.get('checks', {})) == keys
        and set(audit.get('independent_counts', {})) == keys
        and all(audit['checks'][k] is True and audit['independent_counts'][k] == report[k] for k in keys))


class Transition:
    def __init__(self, root):
        self.root = root.resolve()
        self.state = self.root / 'state' / 'google-transition-20261009'
        self.package = self.root / 'state' / SUCCESSOR
        self.phase = 'check'
        spec = importlib.util.spec_from_file_location('transition_gate', self.state / 'gate.py')
        self.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gate)

    def command(self, args, *, source=None, timeout=180):
        return subprocess.run(args, cwd=self.root, input=source, capture_output=True,
            text=True, check=True, timeout=timeout).stdout

    def event(self, phase, **metadata):
        self.phase = phase
        row = dict(at=datetime.now(timezone.utc).isoformat(), phase=phase, **metadata)
        with (self.state / 'transition-events.jsonl').open('a') as handle:
            handle.write(json.dumps(row, default=str) + '\n')
        print(json.dumps(row, default=str), flush=True)

    def manifest(self):
        return json.loads((self.root / 'state' / (PREDECESSOR + '-continuous-best') / 'manifest.json').read_text())

    def inventory(self):
        ids = self.command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT]).split()
        rows = json.loads(self.command(['docker', 'inspect', *ids]))
        return [dict(id=r['Id'], name=r['Name'], service=r['Config']['Labels'].get('com.docker.compose.service'),
            running=r['State']['Running'], paused=r['State']['Paused'], image=r['Image']) for r in rows]

    def verify_package(self):
        series = json.loads((self.package / 'series.json').read_text())
        expected = series['code_sha256']
        actual = {str(p.relative_to(self.package)): sha(p) for p in self.package.rglob('*.py')}
        if actual != expected or series['series'] != SUCCESSOR:
            raise RuntimeError('successor frozen sources differ')
        plans = series['plans']
        if len(plans) != 3 or [p['slots'] for p in plans] != [3, 6, 6] or any(
                p['provider'] != 'openserp' or p['pages'] != 1
                or p.get('code_revision') != 'body-aux-schema-v2'
                or p.get('auxiliary_audit_revision') != 'body-aux-schema-v2' for p in plans):
            raise RuntimeError('successor plan differs')
        all_sources = ['google_web','google_news','google_trends','rss']
        if [p['sources'] for p in plans] != [all_sources, all_sources, ['google_web']]:
            raise RuntimeError('successor sources differ')
        if [p.get('keyword_offset') for p in plans] != [90248,30532,45869] or series.get('continuous_keyword_offset') != 105894:
            raise RuntimeError('successor fresh keyword slices differ')
        revision = self.package / 'revisions' / 'body-aux-schema-v2'
        frozen = json.loads((revision / 'manifest.json').read_text())
        for path, digest in frozen['code_sha256'].items():
            if sha(revision / path) != digest:
                raise RuntimeError('runtime revision differs')
        return series

    def gate_snapshot(self):
        independent = json.loads((self.state / 'predecessor-independent-audits.json').read_text())
        worker, supervisor = self.gate.inspect()
        return self.gate.verify(self.root, independent, worker, supervisor)

    def verify_inputs(self):
        manifest = json.loads((self.state / 'deployment-inputs.json').read_text())
        for relative, expected in manifest['sha256'].items():
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or sha(path) != expected:
                raise RuntimeError('deployment input differs')
        for name in [NEW_UNIT, *NEW_TIMERS, *[n.replace('.timer','.service') for n in NEW_TIMERS]]:
            existing = Path('/etc/systemd/system') / name
            if existing.exists() and sha(existing) != sha(self.package / name):
                raise RuntimeError('existing successor unit differs')
            raw = self.command(['systemctl','show',name,'-p','ActiveState','-p','MainPID'])
            props = dict(line.split('=',1) for line in raw.splitlines())
            if props.get('ActiveState') != 'inactive' or props.get('MainPID','0') != '0':
                raise RuntimeError('successor unit already active')

    def check(self):
        self.verify_inputs()
        series = self.verify_package()
        g = self.gate_snapshot()
        required = ['nine_original_plans','nine_completed','all_nine_exact_hours',
                    'all_nine_independent_sql','recorded_windows_nonoverlapping']
        if not all(g['checks'][k] for k in required):
            raise RuntimeError('predecessor experiment proof incomplete')
        return dict(gate=g, package=series['series'], source_files=len(series['code_sha256']))

    def wait_boundary(self, wait):
        while True:
            m = self.manifest()
            worker, supervisor = self.gate.inspect()
            matches = (worker['Running'] and not worker['Paused'] and not worker['Restarting']
                and worker['Id'] == m['worker_id'] and worker['StartedAt'] == m['docker_started_at']
                and worker['RestartCount'] == m['worker_restart_count'] == 0)
            if not matches or supervisor['ActiveState'] != 'active':
                raise RuntimeError('predecessor live identity not confirmed')
            now = datetime.now(timezone.utc)
            index, right = boundary(self.gate.timestamp(m['worker_started_at']), now)
            self.event('waiting_for_full_boundary', hour_index=index, hour_end=right,
                       worker_id=worker['Id'], worker_running=True)
            if not wait and not can_stop(now, right):
                raise RuntimeError('full-hour handoff window not ready')
            if now < right + timedelta(seconds=60):
                time.sleep(min(45, max(1, (right + timedelta(seconds=60) - now).total_seconds())))
                continue
            self.command(['sudo','-n','systemctl','start','realtime-google-hourly-archive.service'])
            path = self.root / 'state' / PREDECESSOR / 'hourly-archive' / ('continuous-hour-%06d.json' % index)
            if not path.exists():
                # Do not stop when the supervisor has not yet verified the boundary.
                time.sleep(5)
                continue
            report = json.loads(path.read_text())
            m = self.manifest()
            if not hour_verified(self.gate, report, m, index, right):
                raise RuntimeError('continuous hour audit not valid')
            audit_source = (self.state / 'independent-hour-audit.py').read_text()
            audit = json.loads(self.command(['docker','compose','-p',PROJECT,'exec','-T','web','python','-',
                m['run'],'/app/state/' + PREDECESSOR + '/hourly-archive/' + path.name], source=audit_source))
            if not audit_verified(self.gate, audit, report, sha(path)):
                raise RuntimeError('continuous independent SQL failed')
            save(self.state / 'predecessor-boundary-independent-audit.json', audit)
            if audit.get('frozen_report_sha256') != sha(path):
                raise RuntimeError('continuous frozen report changed')
            if not can_stop(datetime.now(timezone.utc), right):
                self.event('handoff_window_expired_without_stop', hour_end=right)
                continue
            # Inspect again immediately before the first stop operation.
            w, unit = self.gate.inspect()
            if not (w['Running'] and not w['Paused'] and not w['Restarting']
                    and w['Id'] == m['worker_id'] and w['StartedAt'] == m['docker_started_at']
                    and w['RestartCount'] == m['worker_restart_count'] == 0
                    and unit['ActiveState'] == 'active'):
                raise RuntimeError('pre-stop identity differs')
            return right, path

    def apply(self, wait):
        self.check()
        if (self.state / 'mutation-started.json').exists():
            raise RuntimeError('prior mutation exists; inspect phases before manual recovery')
        right, hour = self.wait_boundary(wait)
        self.check()
        if not can_stop(datetime.now(timezone.utc), right):
            raise RuntimeError('boundary expired before mutation')
        save(self.state / 'mutation-started.json', dict(at=datetime.now(timezone.utc).isoformat(), hour=str(hour), hour_sha256=sha(hour)))
        self.event('stopping_old_supervisor', verified_hour_end=right)
        self.command(['sudo','-n','systemctl','disable','--now',OLD_UNIT], timeout=35)
        self.command(['docker','stop','--time','10',WORKER], timeout=20)
        g = self.gate_snapshot();save(self.state / 'gate-after-normal-boundary-stop.json',g)
        if not g['ready']:
            raise RuntimeError('stopped boundary gate failed')
        self.command(['sudo','-n','systemctl','disable','--now',*OLD_TIMERS])
        self.command(['sudo','-n','systemctl','stop',*OLD_JOBS])
        inventory = self.inventory();save(self.state / 'inventory-before-migration.json',inventory)
        allowed = {'web','openserp','proxy-relay','postgres','valkey'}
        if any(r['running'] and r['service'] not in allowed for r in inventory):
            raise RuntimeError('additional collection writer running')
        self.command(['docker','stop','--time','10',WEB], timeout=20)
        self.event('old_writers_stopped')
        # pg_dump is read-only and stored locally on the server, never printed.
        if shutil.disk_usage(self.state).free < 8 * 1024**3:
            raise RuntimeError('insufficient backup headroom')
        backup = self.state / 'pre-body-aux-schema-v2.dump'
        if backup.exists():
            raise RuntimeError('backup already exists; inspect before proceeding')
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd,'wb') as handle:
            subprocess.run(['docker','exec',PROJECT+'-postgres-1','pg_dump','-U','realtime','-d','realtime','-Fc'],
                cwd=self.root, stdout=handle, stderr=subprocess.PIPE, check=True, timeout=1800)
        if backup.stat().st_size == 0:
            raise RuntimeError('empty database backup')
        # Verify the custom archive directory using pg_restore inside PostgreSQL.
        with backup.open('rb') as handle:
            subprocess.run(['docker','exec','-i',PROJECT+'-postgres-1','pg_restore','--list'],
                stdin=handle, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=60)
        save(self.state / 'database-backup-metadata.json',dict(at=datetime.now(timezone.utc).isoformat(),
            file=backup.name, bytes=backup.stat().st_size, sha256=sha(backup), custom_archive_list_verified=True))
        self.event('database_backup_verified', bytes=backup.stat().st_size)
        self.verify_package()
        g = self.gate_snapshot();save(self.state / 'gate-immediately-before-migration.json',g)
        if not g['ready'] or any(r['running'] and r['service'] in {'local-worker','web','collector'} for r in self.inventory()):
            raise RuntimeError('old writer reappeared before migration')
        expected = sha(self.package / 'code/realtime/campaign_store.py')
        self.event('schema_migration_started')
        try:
            output = self.command(['docker','compose','-p',PROJECT,'run','--rm','--no-deps','-T','-w','/tmp',
                '-e','PYTHONPATH=/app/state/'+SUCCESSOR+'/code', '--entrypoint','python','web','-',
                expected, PREDECESSOR+'-continuous-best'], source=(self.state/'migrate.py').read_text(), timeout=900)
        except subprocess.CalledProcessError as exc:
            # migrate.py has a metadata-only JSON output contract.
            try:save(self.state/'migration-failure-metadata.json',json.loads(exc.stdout))
            except (ValueError,TypeError):pass
            raise
        result = json.loads(output);save(self.state/'migration-result.json',result)
        if not result.get('all_checks_passed'):
            raise RuntimeError('migration integrity failed')
        self.event('schema_migration_verified')
        m = self.manifest();m.update(status='retired_after_completed_hour',retired_at=datetime.now(timezone.utc).isoformat(),
                                     retired_after_hour=str(hour),successor_series=SUCCESSOR)
        save(self.root/'state'/(PREDECESSOR+'-continuous-best')/'manifest.json',m)
        series=self.verify_package()
        override=self.state/'web-frozen-code-override.json'
        save(override,dict(services={'web':dict(image=series['image'],volumes=[str(self.package/'code/realtime')+':/app/realtime:ro'])}))
        self.command(['docker','compose','-p',PROJECT,'-f','compose.yaml','-f',str(override),
            'up','-d','--no-deps','--no-build','web'])
        self.event('new_web_started')
        for _ in range(60):
            w=json.loads(self.command(['docker','inspect',WEB]))[0]
            if w['State']['Running'] and w['State'].get('Health',{}).get('Status')=='healthy':break
            time.sleep(2)
        else:raise RuntimeError('new web health not confirmed')
        probe = '''import json,hashlib,pathlib
import realtime.campaign_store as module
from realtime.config import Config
from psycopg.conninfo import make_conninfo
p=pathlib.Path(module.__file__)
s=module.CampaignStore(make_conninfo(Config().database_url,options='-c default_transaction_read_only=on'))
with s.connect() as db:ro=db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
print(json.dumps(dict(file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),readonly_constructor_state=ro)))
'''
        actual=json.loads(self.command(['docker','compose','-p',PROJECT,'exec','-T','web','python','-'],source=probe))
        save(self.state/'new-web-cold-readonly-probe.json',actual)
        if actual['sha256']!=expected or actual['readonly_constructor_state']!='on':
            raise RuntimeError('actual new web cold package not verified')
        for name in [NEW_UNIT,*[n.replace('.timer','.service') for n in NEW_TIMERS],*NEW_TIMERS]:
            self.command(['sudo','-n','install','-m','644',str(self.package/name),'/etc/systemd/system/'+name])
        self.command(['sudo','-n','systemctl','daemon-reload'])
        self.command(['sudo','-n','systemctl','enable','--now',NEW_UNIT])
        self.event('new_series_started')
        first=self.root/'state'/(SUCCESSOR+'-openserp-google-all-s3')/'manifest.json'
        for _ in range(80):
            if first.exists():
                m=json.loads(first.read_text());w=json.loads(self.command(['docker','inspect',WORKER]))[0]
                if m.get('status')=='running' and w['State']['Running'] and w['Id']==m.get('worker_id'):
                    self.command(['sudo','-n','systemctl','enable','--now',*NEW_TIMERS])
                    self.event('new_first_hour_running',run=m['run'],worker_id=w['Id'],pid=w['State']['Pid'],deadline=m['deadline'])
                    return
            time.sleep(5)
        raise RuntimeError('new first hour start not confirmed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    transition=Transition(args.project_root)
    with (transition.state/'transition.lock').open('w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if args.apply:transition.apply(args.wait)
            else:print(json.dumps(transition.check(),indent=2))
        except Exception as exc:
            transition.event('failed',failed_phase=transition.phase,error_type=type(exc).__name__,
                returncode=getattr(exc,'returncode',None),automatic_retry=False)
            return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
