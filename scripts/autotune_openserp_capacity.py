"""Bounded overnight slot experiments; compare full hours without weakening quality."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2))
    temporary.replace(path)


def headroom():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return (int(memory['MemAvailable'].split()[0]) >= 2 * 1024 * 1024
            and os.getloadavg()[1] <= (os.cpu_count() or 1) * 1.5)


def worker_slots(root):
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'local-worker',
                             'printenv', 'CRAWLER_SLOTS'], cwd=root, text=True,
                            capture_output=True, check=True, timeout=20)
    return int(result.stdout.strip())


def apply_slots(root, directory, state, deadline):
    """Recover a persisted transition after a supervisor restart."""
    pending = state.get('pending')
    if not pending or datetime.now(timezone.utc) >= deadline:
        return
    slots = pending['slots']
    if worker_slots(root) != slots:
        environment = root / '.env'
        original = environment.read_text()
        backup = directory / 'env-before-autotune'
        if not backup.exists():
            backup.write_text(original)
            backup.chmod(0o600)
        changed, count = re.subn(r'^CRAWLER_SLOTS=.*$', f'CRAWLER_SLOTS={slots}', original, flags=re.M)
        if not count:
            changed = original.rstrip() + f'\nCRAWLER_SLOTS={slots}\n'
        environment.write_text(changed)
        if datetime.now(timezone.utc) >= deadline:
            return
        subprocess.run(['docker', 'compose', 'up', '-d', '--no-deps',
                        '--force-recreate', 'local-worker'], cwd=root, check=True, timeout=120)
        if worker_slots(root) != slots:
            raise RuntimeError('worker slot change not applied')
    if datetime.now(timezone.utc) >= deadline:
        subprocess.run(['docker', 'compose', 'stop', '-t', '10', 'local-worker'],
                       cwd=root, check=True, timeout=60)
        return
    phase_path = directory / 'phases.jsonl'
    previous = json.loads(phase_path.read_text().splitlines()[-1])
    if previous.get('phase') != pending['phase'] or previous.get('crawler_slots') != slots:
        phase = {'at': datetime.now(timezone.utc).isoformat(),
                 'phase': pending['phase'], 'crawler_slots': slots}
        with phase_path.open('a') as stream:
            stream.write(json.dumps(phase) + '\n')
    state['current_slots'] = slots
    state.pop('pending')
    save(directory / 'autotune.json', state)


def choose(state, bodies, safe):
    """Sequential evidence selects a configuration, not a causal proof."""
    current = state['current_slots']
    if state.get('best_bodies') is None or bodies > state['best_bodies']:
        state.update(best_bodies=bodies, best_slots=current)
    if bodies >= 2000:
        state['mode'] = 'hold'
        return None
    while state['candidates']:
        candidate = state['candidates'].pop(0)
        if candidate > current and not safe:
            state.setdefault('skipped', []).append({'slots': candidate, 'reason': 'host load or memory'})
            continue
        if candidate != current:
            return candidate
    state['mode'] = 'hold'
    return state['best_slots'] if current != state['best_slots'] else None


def tick(root, directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    deadline = datetime.fromisoformat(manifest['deadline'])
    if datetime.now(timezone.utc) >= deadline or manifest.get('finished_at') or manifest.get('paused_at'):
        return
    path = directory / 'autotune.json'
    state = json.loads(path.read_text()) if path.exists() else dict(
        mode='experiment', current_slots=worker_slots(root), candidates=[3, 6], measurements=[])
    if not path.exists():
        save(path, state)
    if state.get('pending'):
        apply_slots(root, directory, state, deadline)
        return
    phase = json.loads((directory / 'phases.jsonl').read_text().splitlines()[-1])
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(phase['at'])).total_seconds()
    measured = sum(item['phase_start'] == phase['at'] for item in state['measurements'])
    if elapsed < (measured + 1) * 3600:
        return
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'web', 'python', '-', manifest['run']],
                            input=(root / 'scripts/verify_openserp_capacity.py').read_text(),
                            cwd=root, text=True, capture_output=True, check=True, timeout=90)
    evidence = json.loads(result.stdout)
    if not evidence['exclusive_openserp_attempts'] or any(
            hour['non_exclusive_web'] for hour in evidence['complete_hours']):
        raise RuntimeError('autotune source verification failed')
    hours = evidence['complete_hours']
    if len(hours) <= measured:
        return
    for hour in hours[measured:]:
        state['measurements'].append(dict(phase_start=phase['at'], slots=state['current_slots'], **hour))
    save(directory / 'latest-hour-verification.json', evidence)
    if state['mode'] != 'hold':
        candidate = choose(state, hours[-1]['new_bodies'], headroom())
        # Leave a full measurement hour plus operational margin before the hard stop.
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if candidate is not None and remaining > (180 if state['mode'] == 'hold' else 3900):
            state['pending'] = dict(slots=candidate, phase=f'english-only-autotune-slots-{candidate}')
        elif candidate is not None:
            state['mode'] = 'hold'
    save(path, state)
    if state.get('pending'):
        apply_slots(root, directory, state, deadline)
