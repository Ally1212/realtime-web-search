"""Validate the frozen four-file release; never starts services or source jobs.

Run on the target host. `check` is read-only; `install` and `rollback` require
an operator-supplied fresh database snapshot and create a private journal.
Service restarts and post-release checks remain separate explicit operations.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from datetime import datetime, timezone

BUNDLE_SHA256 = '882cf3d7a93dc02db9859669f60cc3ff2118f22372c9d9daa455c34a637f76f7'
ROOTS = {
    'web': Path('/home/azureuser/projects/google-workbench/code/realtime'),
    'worker': Path('/home/azureuser/projects/realtime-web-search-openserp/state/field-expansion-20261016/code-r2/local-worker/realtime'),
}
FILES = {'web': {'whale_serp.py', 'google_data_export.py', 'google_exports.py'},
         'worker': {'whale_serp.py'}}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_bundle(path):
    raw = Path(path).read_bytes()
    require(digest(raw) == BUNDLE_SHA256, 'bundle digest mismatch')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        require(len({m.name for m in members}) == len(members), 'duplicate archive member')
        require(all(m.isfile() and m.size <= 1024 * 1024 for m in members), 'invalid archive member')
        content = {m.name: archive.extractfile(m).read() for m in members}
    manifest = json.loads(content.pop('manifest.json'))
    require(set(content) == {f'{kind}/realtime/{name}' for kind, names in FILES.items() for name in names}, 'unexpected file set')
    require(len(manifest['files']) == 4, 'unexpected manifest file count')
    require({f['archive_path'] for f in manifest['files']} == set(content), 'manifest member mismatch')
    for entry in manifest['files']:
        kind = entry['host_kind']
        name = Path(entry['relative_path']).name
        require(name in FILES[kind], 'unapproved target')
        require(entry['target'] == str(ROOTS[kind] / name), 'target path mismatch')
        require(digest(content[entry['archive_path']]) == entry['after'], 'payload digest mismatch')
        compile(content[entry['archive_path']], entry['archive_path'], 'exec')
    return manifest, content


def check_snapshot(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(snapshot['checked_at'])
    require(stamp.utcoffset() is not None and 0 <= (now - stamp).total_seconds() <= 90,
            'database preflight is stale or from future')
    require(snapshot['scope'] == 'repeatable_read_read_only_preflight', 'invalid snapshot scope')
    require(snapshot['settings']['content_fetch_enabled'] is False, 'body collection enabled')
    require(snapshot['settings']['continuous_enabled'] == 'false', 'continuous collection enabled or unknown')
    require(all(snapshot['whale_settings'][k] is False for k in ('uploads_enabled', 'claims_enabled')), 'Whale enabled')
    for key in ('active_manual_serp_tasks', 'live_request_leases', 'live_discovery_query_leases',
                'live_whale_upload_leases', 'active_customer_api_jobs'):
        require(type(snapshot[key]) is int and snapshot[key] == 0, f'live work: {key}')
    require(snapshot['monitor_counts'] and all(type(v) is int and v == 0 for v in snapshot['monitor_counts'].values()), 'enabled or unknown monitors')
    require(not any(row['status'] in {'active', 'paused', 'running', 'queued'} and row['count'] for row in snapshot['whale_job_states']), 'active Whale jobs')
    require(snapshot['outbox_states'] == [{'state': 'queued', 'count': 9}], 'old outbox changed')
    return digest(json.dumps(snapshot, sort_keys=True).encode())


def read_regular(path):
    require(not path.is_symlink() and path.is_file(), f'not a regular file: {path.name}')
    require(path.resolve() == path.absolute(), f'symlink ancestor: {path.name}')
    return path.read_bytes()


def atomic_write(path, raw, mode):
    fd, temp = tempfile.mkstemp(prefix='.sf-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def verify_files(manifest, kind, root, expected):
    for name, sha in manifest['protected_files'][kind].items():
        require(digest(read_regular(root / name)) == sha, f'protected file changed: {name}')
    entries = [f for f in manifest['files'] if f['host_kind'] == kind]
    for entry in entries:
        name = Path(entry['relative_path']).name
        require(digest(read_regular(root / name)) == entry[expected], f'target changed: {name}')
    return entries


def operate(action, manifest, content, kind, root, journal, snapshot):
    """File transaction only. Injection of root is for isolated filesystem tests."""
    snapshot_hash = check_snapshot(snapshot)
    if action == 'rollback':
        # An interrupted install can leave a mix of old/new files. Only these
        # two pinned hashes may be restored; any third-party edit stops all work.
        entries = [f for f in manifest['files'] if f['host_kind'] == kind]
        for name, sha in manifest['protected_files'][kind].items():
            require(digest(read_regular(root / name)) == sha, f'protected file changed: {name}')
        for entry in entries:
            require(digest(read_regular(root / Path(entry['relative_path']).name)) in
                    {entry['before'], entry['after']}, 'rollback target changed')
    else:
        entries = verify_files(manifest, kind, root, 'before')
    if action == 'check':
        return {'status': 'checked', 'files': len(entries), 'database_snapshot_sha256': snapshot_hash}
    if action == 'install':
        journal.mkdir(mode=0o700, parents=False, exist_ok=False)
        # Complete and verify every backup before changing any target file.
        for entry in entries:
            name = Path(entry['relative_path']).name
            raw = read_regular(root / name)
            atomic_write(journal / name, raw, 0o600)
            require(digest(read_regular(journal / name)) == entry['before'], 'backup digest mismatch')
        state = {'kind': kind, 'bundle_sha256': BUNDLE_SHA256, 'snapshot_sha256': snapshot_hash,
                 'phase': 'backed_up', 'replaced': []}
        atomic_write(journal / 'journal.json', json.dumps(state).encode(), 0o600)
        for entry in entries:
            name = Path(entry['relative_path']).name
            # Refuse concurrent modifications even after the initial full check.
            require(digest(read_regular(root / name)) == entry['before'], f'target changed during install: {name}')
            require(digest(read_regular(journal / name)) == entry['before'], 'backup changed')
            atomic_write(root / name, content[entry['archive_path']], (root / name).stat().st_mode & 0o777)
            state['replaced'].append(name)
            atomic_write(journal / 'journal.json', json.dumps(state).encode(), 0o600)
        verify_files(manifest, kind, root, 'after')
        state['phase'] = 'installed_requires_restart_and_postcheck'
    else:
        state = json.loads(read_regular(journal / 'journal.json'))
        require(state['kind'] == kind and state['bundle_sha256'] == BUNDLE_SHA256, 'journal identity mismatch')
        require(state['phase'] in {'backed_up', 'installed_requires_restart_and_postcheck'}, 'journal not installed')
        # Verify ALL backups before restoring any file.
        for entry in entries:
            require(digest(read_regular(journal / Path(entry['relative_path']).name)) == entry['before'], 'backup changed')
        for entry in entries:
            name = Path(entry['relative_path']).name
            require(digest(read_regular(root / name)) in {entry['before'], entry['after']}, f'target changed during rollback: {name}')
            atomic_write(root / name, read_regular(journal / name), (root / name).stat().st_mode & 0o777)
        verify_files(manifest, kind, root, 'before')
        state['phase'] = 'restored_requires_restart_and_postcheck'
    atomic_write(journal / 'journal.json', json.dumps(state).encode(), 0o600)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'rollback'))
    parser.add_argument('--kind', choices=tuple(ROOTS), required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    manifest, content = load_bundle(args.bundle)
    result = operate(args.action, manifest, content, args.kind, ROOTS[args.kind], args.journal,
                     json.loads(args.snapshot.read_text()))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
