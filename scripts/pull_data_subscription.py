"""Pull one saved-data batch, verify and atomically save files, then acknowledge.

Schedule this client using cron/systemd if needed. It never resumes a paused
subscription and never creates searches. A network failure can be retried by
running the same command: the server retains unacknowledged batches.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

import requests


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pull(base, subscription_id, output, *, site_key, customer_key, limit=20, session=None):
    from uuid import UUID
    sid = str(UUID(subscription_id))
    url = urlsplit(base)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('base URL must be an HTTPS origin without credentials')
    client = session or requests.Session()
    headers = {'X-SearchFlow-Access-Key': site_key, 'Authorization': 'Bearer ' + customer_key}
    root = base.rstrip('/') + '/v1/data/subscriptions/' + sid
    def call(path, payload=None):
        response = client.request('GET' if payload is None else 'POST', root + path,
                                  headers=headers, json=payload, timeout=35, allow_redirects=False)
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise ValueError('Unexpected redirect or response status')
        return response
    envelope = call('/pull?limit=' + str(limit), {}).json()
    if not envelope.get('batch_id'):
        return envelope
    bid = str(UUID(envelope['batch_id']))
    batch_path = '/batches/' + bid
    manifest_raw = call(batch_path + '/export?format=manifest').content
    manifest = json.loads(manifest_raw)
    if set(manifest['files']) != {'jsonl', 'csv'}:
        raise ValueError('Unexpected export file list')
    payload_raw = json.dumps(envelope['payload'], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    if digest(payload_raw) != envelope['sha256'] or manifest['batch_sha256'] != envelope['sha256'] or manifest['batch_id'] != bid:
        raise ValueError('Batch payload checksum mismatch')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = output / bid
    if destination.exists():
        for kind, info in manifest['files'].items():
            raw = (destination / ('data.' + kind)).read_bytes()
            if digest(raw) != info['sha256'] or len(raw) != info['bytes']:
                raise ValueError('Previously saved batch checksum mismatch')
    else:
        temp = Path(tempfile.mkdtemp(prefix='.batch-', dir=output))
        try:
            for kind, info in manifest['files'].items():
                if kind not in ('csv', 'jsonl'):
                    raise ValueError('Unknown export format')
                raw = call(batch_path + '/export?format=' + kind).content
                if digest(raw) != info['sha256'] or len(raw) != info['bytes']:
                    raise ValueError('Downloaded file checksum mismatch')
                with (temp / ('data.' + kind)).open('xb') as f:
                    os.chmod(f.name, 0o600)
                    f.write(raw); f.flush(); os.fsync(f.fileno())
            for name, value in [('manifest.json', manifest), ('batch.json', envelope)]:
                with (temp / name).open('x') as f:
                    os.chmod(f.name, 0o600)
                    json.dump(value, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
            temp_fd = os.open(temp, os.O_RDONLY)
            try: os.fsync(temp_fd)
            finally: os.close(temp_fd)
            os.rename(temp, destination)
            # Persist the directory rename before advancing the server ledger.
            fd = os.open(output, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    confirmation = call(batch_path + '/ack', {'sha256': envelope['sha256']}).json()
    return dict(batch_id=bid, saved=str(destination), verified=True, acknowledged=confirmation['acknowledged'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--subscription-id', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=20, choices=range(1, 51))
    p.add_argument('--site-key-file', type=Path, default=Path.home() / '.config/searchflow/site-access-key')
    p.add_argument('--customer-key-file', type=Path, default=Path.home() / '.config/searchflow/customer-key')
    args = p.parse_args()
    result = pull(args.base_url, args.subscription_id, args.output,
                  site_key=args.site_key_file.read_text().strip(), customer_key=args.customer_key_file.read_text().strip(), limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
