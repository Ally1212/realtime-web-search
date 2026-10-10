"""Read-only host/container resource evidence for the running Google series."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


WORKER = 'realtime-web-search-openserp-local-worker-1'
OPENSERP = 'realtime-web-search-openserp-openserp-1'
CPU_MODES = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout


def worker_state():
    info = json.loads(command(['docker', 'inspect', WORKER]))[0]
    return dict(id=info['Id'], pid=info['State']['Pid'], running=info['State']['Running'],
                paused=info['State']['Paused'], restarting=info['State']['Restarting'],
                started_at=info['State']['StartedAt'], restart_count=info['RestartCount'])


def cpu_ticks():
    return [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]


def observe(root, series):
    state = root / 'state' / series
    heartbeat = json.loads((state / 'heartbeat.json').read_text())
    run = heartbeat['run']
    # Never let stale or malformed observer data point outside this series.
    if not isinstance(run, str) or not run.startswith(series + '-') or Path(run).name != run:
        raise ValueError('Invalid run identity')
    manifest = json.loads((root / 'state' / run / 'manifest.json').read_text())
    before = worker_state()
    first = cpu_ticks()
    started = time.monotonic()
    time.sleep(2)
    second = cpu_ticks()
    duration = time.monotonic() - started
    deltas = [right - left for left, right in zip(first, second)]
    total = sum(deltas)
    if total <= 0 or any(value < 0 for value in deltas):
        raise ValueError('Invalid host CPU interval')
    modes = {name: value / total * 100 for name, value in zip(CPU_MODES, deltas)}
    stats = []
    for line in command(['docker', 'stats', '--no-stream', '--format', '{{json .}}',
                         WORKER, OPENSERP]).splitlines():
        row = json.loads(line)
        # Keep only explicitly allowed fields, never environment or command arguments.
        stats.append(dict(name=row['Name'], cpu_percent=float(row['CPUPerc'].rstrip('%')),
                          memory_usage=row['MemUsage'], memory_percent=float(row['MemPerc'].rstrip('%'))))
    after = worker_state()
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    matches = (before == after and after['running'] and not after['paused'] and not after['restarting']
               and after['id'] == manifest.get('worker_id')
               and after['started_at'] == manifest.get('docker_started_at')
               and after['restart_count'] == manifest.get('worker_restart_count'))
    return dict(at=datetime.now(timezone.utc).isoformat(), series=series, run=run,
                worker=after, worker_matches_manifest=matches,
                worker_changed_during_probe=before != after, deadline=manifest['deadline'],
                manifest_slots=manifest['slots'], manifest_pages=manifest['pages'],
                manifest_sources=manifest['sources'], host_cpu_count=os.cpu_count(),
                host_cpu_interval_seconds=duration, host_cpu_modes_percent=modes,
                host_compute_percent=sum(modes[name] for name in ('user', 'nice', 'system', 'irq', 'softirq')),
                host_loadavg=list(os.getloadavg()),
                host_mem_available_bytes=int(memory['MemAvailable'].split()[0]) * 1024,
                container_stats=stats, database_access=False,
                definition='Host CPU and Docker samples have different short windows. '
                'Manifest configuration is reported only as declared, not re-read from Config. '
                'A mismatching container is transition/observation evidence, not a restart instruction. '
                'No collection changes or automatic remediation.')


def main():
    root = Path(os.getenv('GOOGLE_PROJECT_ROOT', str(Path(__file__).resolve().parents[1])))
    series = os.getenv('GOOGLE_HOURLY_SERIES', 'google-hourly-20261009')
    try:
        record = observe(root, series)
    except Exception as exc:
        # Do not print raw Docker stderr or exception messages.
        print(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
                              observer_error=type(exc).__name__)), flush=True)
        raise SystemExit(1)
    path = root / 'state' / series / 'resource-observations.jsonl'
    encoded = (json.dumps(record) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
