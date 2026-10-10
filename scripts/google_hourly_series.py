"""Sequential frozen one-hour Google experiments, followed by durable production.

No changes to .env or unrelated Compose services. A series owns local-worker only.
The frozen code and per-run Compose overrides are retained for review.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.getenv('GOOGLE_PROJECT_ROOT', str(Path(__file__).resolve().parents[1])))
SERIES = os.getenv('GOOGLE_HOURLY_SERIES', 'google-hourly-20261009')
STATE = ROOT / 'state' / SERIES
WORKER = 'realtime-web-search-openserp-local-worker-1'
GOOGLE_RSS = 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en'
PLANS = [
    dict(key='curl-p1-s3', provider='curl', pages=1, slots=3, sources=['google_web']),
    dict(key='browser-p1-s3', provider='browser', pages=1, slots=3, sources=['google_web']),
    dict(key='wml-p1-s3', provider='wml', pages=1, slots=3, sources=['google_web']),
    dict(key='openserp-p1-s3', provider='openserp', pages=1, slots=3, sources=['google_web']),
    dict(key='openserp-p1-s6', provider='openserp', pages=1, slots=6, sources=['google_web']),
    dict(key='openserp-p11-s3', provider='openserp', pages=11, slots=3, sources=['google_web']),
    dict(key='openserp-google-all-s3', provider='openserp', pages=1, slots=3,
         sources=['google_web', 'google_news', 'google_trends', 'rss']),
]


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str, ensure_ascii=False) + '\n')
    temporary.replace(path)


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 180), **kwargs)


def worker_state():
    return json.loads(command(['docker', 'inspect', WORKER]).stdout)[0]


def docker_time(value):
    # Ubuntu's Python 3.10 accepts at most six fractional digits.
    value = re.sub(r'(\.\d{6})\d+', r'\1', value)
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def compose(state, *args):
    return command(['docker', 'compose', '-f', str(ROOT / 'compose.yaml'),
                    '-f', str(state / 'compose-override.json'), *args])


def control(state, action, *args):
    m = json.loads((state / 'manifest.json').read_text())
    try:
        # python stdin gives cwd precedence over PYTHONPATH. /app contains the
        # live web package; management must import this series' frozen package.
        result = command(['docker', 'compose', 'exec', '-T', '-w', '/tmp',
                          '-e', 'GOOGLE_FREE_PROVIDERS=' + m['provider'],
                          '-e', 'PYTHONPATH=/app/state/' + SERIES + '/code',
                          'web', 'python', '-', m['run'], action, *args],
                         input=(STATE / 'control.py').read_text(), timeout=240)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        summary = control_failure_summary(m['run'], action, exc)
        try:
            path = STATE / 'control-failures.jsonl'
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(json.dumps(summary) + '\n')
        except OSError:
            summary['diagnostic_write_failed'] = True
        print(json.dumps(summary), file=sys.stderr, flush=True)
        raise
    return json.loads(result.stdout)


def control_failure_summary(run, action, exc):
    # Capture location and exception class, never exception messages, command
    # arguments, source lines, stdout or raw stderr (which can contain secrets).
    stderr = getattr(exc, 'stderr', None) or ''
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    frames = []
    types = []
    for line in stderr.splitlines():
        frame = re.fullmatch(r'\s*File "([^"]+)", line (\d+), in ([\w<>]+)', line)
        if frame:
            name = Path(frame[1]).name
            frames.append(dict(file=name if name.endswith('.py') or name == '<stdin>' else '[generated]',
                               line=int(frame[2]), function=frame[3]))
        error = re.match(r'^((?:[A-Za-z_]\w*\.)*[A-Z]\w*):', line)
        if error:
            name = error[1]
            tail = name.rsplit('.', 1)[-1]
            if tail.endswith(('Error', 'Exception', 'Timeout')) or tail in {
                    'DeadlockDetected', 'SerializationFailure', 'UniqueViolation',
                    'ForeignKeyViolation', 'InFailedSqlTransaction', 'LockNotAvailable',
                    'ConnectionDoesNotExist'}:
                types.append(name)
    timeout = getattr(exc, 'timeout', None)
    return dict(at=datetime.now(timezone.utc).isoformat(), run=run, action=action,
                failure_type=type(exc).__name__, returncode=getattr(exc, 'returncode', None),
                timeout_seconds=timeout if isinstance(timeout, (int, float)) else None,
                exception_types=list(dict.fromkeys(types)), frames=frames)


def initialize():
    STATE.mkdir(parents=True, exist_ok=True)
    if (STATE / 'series.json').exists():
        return
    if worker_state()['State']['Running']:
        raise RuntimeError('local-worker already running; refusing overlapping collection')
    shutil.copytree(ROOT / 'realtime', STATE / 'code' / 'realtime',
                    ignore=shutil.ignore_patterns('__pycache__', '*.before-*', '*.tmp*'))
    # All providers use the same private pool. Disable the older optional
    # public-proxy fallback only in this experiment snapshot.
    discovery = STATE / 'code' / 'realtime' / 'discovery.py'
    source = discovery.read_text()
    fallback = '''profiles = (self.proxy_profile,) if provider == "openserp" else (
            self.proxy_profile, "public_google" if self.proxy_profile == "private" else None
        )'''
    if source.count(fallback) != 1:
        raise RuntimeError('Unknown proxy selector; cannot freeze a private-pool-only experiment')
    discovery.write_text(source.replace(fallback, 'profiles = (self.proxy_profile,)'))
    for source, target in [('google_hourly_control.py', 'control.py'),
                           ('google_hourly_series.py', 'supervisor.py'),
                           ('google_kitchen_until_morning.py', 'controller.py'),
                           ('openserp_world_keywords.py', 'world_keywords.py')]:
        shutil.copy2(ROOT / 'scripts' / source, STATE / target)
    hashes = {str(p.relative_to(STATE)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in STATE.rglob('*.py')}
    save(STATE / 'series.json', dict(series=SERIES, created_at=datetime.now(timezone.utc),
         plans=PLANS, code_sha256=hashes, image=worker_state()['Image'],
         status='experiments', comparison_limits='Sequential fresh deterministic catalog slices; '
         'time, proxy health and query mix can affect comparisons. No causal claim from one hour.',
         rss_scope='Google News RSS only; no other search engine or paid search API'))


def run_keyword_offset(plan, index, *, continuous=False, series=None):
    default = (index * 15000) % 120000
    value = ((series or {}).get('continuous_keyword_offset', default) if continuous
             else plan.get('keyword_offset', default))
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 120000:
        raise ValueError('Keyword offset must be an integer inside the frozen catalog')
    return value


def prepare(plan, index, continuous=False):
    key = plan['key'] if not continuous else 'continuous-best'
    state = ROOT / 'state' / (SERIES + '-' + key)
    state.mkdir(exist_ok=True)
    if (state / 'manifest.json').exists():
        return state
    code = collection_code(plan)
    for filename in ('controller.py', 'world_keywords.py'):
        shutil.copy2(STATE / filename, state / filename)
    duration = 3600 if not continuous else int(timedelta(days=365*20).total_seconds())
    series = json.loads((STATE / 'series.json').read_text())
    manifest = dict(run=state.name, provider=plan['provider'],
         sources=plan['sources'], keyword_offset=run_keyword_offset(plan, index,
             continuous=continuous, series=series),
         pages=plan['pages'], slots=plan['slots'], duration_seconds=duration,
         deadline=(datetime.now(timezone.utc)+timedelta(seconds=duration+7200)).isoformat(),
         continuous=continuous, config_frozen=True, status='prepared',
         code_revision=plan.get('code_revision'), collection_code=str(code))
    if plan.get('auxiliary_audit_revision'):
        manifest['auxiliary_audit'] = dict(revision=plan['auxiliary_audit_revision'])
    save(state / 'manifest.json', manifest)
    # Use the exact running image ID, preserving all .env credentials without printing them.
    env = worker_environment(plan)
    save(state / 'compose-override.json', dict(services={'local-worker': dict(
        image=json.loads((STATE / 'series.json').read_text())['image'],
        environment=env, volumes=[str(code) + ':/app/realtime:ro'])}))
    return state


def collection_code(plan):
    revision = plan.get('code_revision')
    if not revision:
        return STATE / 'code' / 'realtime'
    if not isinstance(revision, str) or not re.fullmatch(r'[a-z0-9-]+', revision):
        raise ValueError('Invalid collection revision')
    root = STATE / 'revisions' / revision
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('revision') != revision:
        raise ValueError('Collection revision identity mismatch')
    expected = manifest['code_sha256']
    actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (root / 'code').rglob('*.py')}
    if not actual or actual != expected:
        raise ValueError('Collection revision no longer matches its frozen SHA256 manifest')
    smoke = json.loads((root / 'offline-smoke.json').read_text())
    if smoke.get('revision') != revision or not smoke.get('checks') or not all(
            row.get('passed') and row.get('external_requests') == 0 for row in smoke['checks']):
        raise ValueError('Collection revision offline validation is missing or failed')
    return root / 'code' / 'realtime'


def worker_environment(plan):
    return dict(GOOGLE_FREE_PROVIDERS=plan['provider'], CRAWLER_SLOTS=str(plan['slots']),
               GOOGLE_WEB_MAX_PAGES=str(plan['pages']), GOOGLE_WEB_PAGES_PER_BATCH=str(plan['pages']),
               GOOGLE_WEB_INITIAL_RPS='1', GOOGLE_WEB_MAX_RPS='2',
               GOOGLE_WEB_PROXY_MIN_INTERVAL_SECONDS='90', CRAWLER_MIN_CONTENT_CHARS='500',
               DISCOVERY_FEEDS_JSON=json.dumps([dict(name='google_news_rss', url=GOOGLE_RSS)])
               if 'rss' in plan['sources'] else '[]')


def valid_hour(report, interruptions):
    return (report['full_hour_observed'] and report['exclusive_provider']
            and report['result_url_audit_complete']
            and report.get('body_admission_audit_complete', False)
            and report.get('body_integrity_valid', True)
            and report.get('auxiliary_enablement_valid', True) and not interruptions)


def worker_matches_manifest(info, manifest):
    state = info['State']
    return (state['Running'] and not state.get('Paused', False)
            and not state.get('Restarting', False)
            and info['Id'] == manifest['worker_id']
            and state['StartedAt'] == manifest['docker_started_at']
            and info['RestartCount'] == manifest['worker_restart_count'])


def interruption_evidence(manifest, observed_at, reason):
    # An observation can be delayed by a slow probe or a host supervisor outage.
    # Everything since the last actual matching Docker inspection is uncertain.
    left = manifest.get('last_verified_running_at') or manifest['worker_started_at']
    return dict(at=observed_at.isoformat(), reason=reason,
                affected_from=left, affected_until=observed_at.isoformat())


def best_plan(completed, plans, pure=False):
    candidates = [row for row in completed if row['valid_for_comparison'] and
                  (not pure or plans[row['index']]['sources'] == ['google_web'])]
    if not candidates:
        raise RuntimeError('No valid full-hour evidence; cannot select a production winner')
    best = max(candidates, key=lambda r: (r['new_qualified_bodies'], r['valid_new_links'],
                                         r['successful_searches']))
    return dict(plans[best['index']]), best


def run_one(state):
    m = json.loads((state / 'manifest.json').read_text())
    if (state / 'audited-final.json').exists():
        return json.loads((state / 'audited-final.json').read_text())
    if m['status'] == 'prepared':
        if worker_state()['State']['Running']:
            raise RuntimeError('Existing local-worker is running; refusing to replace a live experiment')
        control(state, 'prepare')
        # Record launching first so a host supervisor restart cannot recreate a live worker.
        m['status'] = 'launching'
        save(state / 'manifest.json', m)
        compose(state, 'up', '-d', '--no-deps', '--no-build', 'local-worker')
    m = json.loads((state / 'manifest.json').read_text())
    if m['status'] == 'launching':
        info = worker_state()
        if not info['State']['Running']:
            raise RuntimeError('Worker launch not confirmed; manual inspection required')
        start = docker_time(info['State']['StartedAt'])
        control(state, 'anchor', start.isoformat())
        m = json.loads((state / 'manifest.json').read_text())
        m.update(status='running', worker_id=info['Id'], worker_restart_count=info['RestartCount'],
                 docker_started_at=info['State']['StartedAt'])
        save(state / 'manifest.json', m)
    deadline = datetime.fromisoformat(m['deadline']).timestamp()
    interruptions = list(m.get('interruptions', []))
    health_failures = 0
    while time.time() < deadline:
        info = worker_state()
        inspected_at = datetime.now(timezone.utc)
        if not worker_matches_manifest(info, m):
            event = interruption_evidence(m, inspected_at, 'worker changed, paused or stopped')
            interruptions.append(event)
            m['interruptions'] = interruptions
            save(state / 'manifest.json', m)
            if not info['State']['Running']:
                compose(state, 'start', 'local-worker')
            info = worker_state()
            inspected_at = datetime.now(timezone.utc)
            event['affected_until'] = inspected_at.isoformat()
            m['worker_id'] = info['Id']
            m['worker_restart_count'] = info['RestartCount']
            m['docker_started_at'] = info['State']['StartedAt']
        if worker_matches_manifest(info, m):
            m['last_verified_running_at'] = inspected_at.isoformat()
        save(state / 'manifest.json', m)
        report = control(state, 'tick')
        save(STATE / 'heartbeat.json', dict(at=datetime.now(timezone.utc), run=state.name,
             worker_pid=info['State']['Pid'], deadline=m['deadline'],
             google_searches=report['google_searches'], new_qualified_bodies=report['new_qualified_bodies'],
             alerts=report['alerts']))
        if m['continuous']:
            # Monitor the locking endpoint; CAPTCHA/429 remain responsive and do not cause restarts.
            import urllib.request
            try:
                with urllib.request.urlopen('http://127.0.0.1:7001/stats/proxy', timeout=5) as response:
                    healthy = isinstance(json.load(response).get('lanes'), dict)
                health_failures = 0 if healthy else health_failures + 1
            except Exception:
                health_failures += 1
            if health_failures >= 3:
                command(['docker', 'restart', 'realtime-web-search-openserp-openserp-1'])
                health_failures = 0
        print(json.dumps(dict(run=state.name, searches=report['google_searches'],
                             bodies=report['new_qualified_bodies'], alerts=report['alerts'])), flush=True)
        time.sleep(max(0, min(60, deadline-time.time())))
    final_state = worker_state()
    if not worker_matches_manifest(final_state, m):
        interruptions.append(interruption_evidence(
            m, datetime.now(timezone.utc), 'worker changed at final audit'))
    else:
        m['last_verified_running_at'] = datetime.now(timezone.utc).isoformat()
    m['interruptions'] = interruptions
    save(state / 'manifest.json', m)
    compose(state, 'stop', '-t', '10', 'local-worker')
    report = control(state, 'finish')
    report['interruptions'] = interruptions
    report['worker_verified_through'] = m.get('last_verified_running_at')
    report['valid_for_comparison'] = valid_hour(report, interruptions)
    save(state / 'audited-final.json', report)
    return report


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'supervisor.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initialize()
        if '--initialize' in sys.argv:
            return
        series = json.loads((STATE / 'series.json').read_text())
        completed = []
        for index, plan in enumerate(series['plans']):
            report = run_one(prepare(plan, index))
            for retry in range(1, 4):
                if report['valid_for_comparison']:
                    break
                retry_plan = dict(plan, key=plan['key'] + '-retry' + str(retry))
                report = run_one(prepare(retry_plan, index))
            if not report['valid_for_comparison']:
                raise RuntimeError('Three retry hours failed the audit; no winner selected')
            report['index'] = index
            completed.append(report)
            save(STATE / 'comparison.json', dict(completed=completed, pending=len(series['plans'])-len(completed)))
        best, evidence = best_plan(completed, series['plans'])
        pure, pure_evidence = best_plan(completed, series['plans'], pure=True)
        save(STATE / 'selection.json', dict(best=best, measured_hour=evidence,
             best_pure_google_web=pure, pure_measured_hour=pure_evidence,
             selected_at=datetime.now(timezone.utc), provisional=True,
             criterion='Most globally deduplicated qualified new bodies in a valid complete hour; '
             'requires subsequent continuous-hour validation'))
        series['status'] = 'continuous'
        save(STATE / 'series.json', series)
        run_one(prepare(best, len(series['plans']), continuous=True))


if __name__ == '__main__':
    main()
