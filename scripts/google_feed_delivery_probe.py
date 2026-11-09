"""Prepare saved public feed candidates or verify one explicit pinned version.

No ingest, registration, configuration write or new source query exists here.
Prepared candidates do not select a Dataset/content_type or grant admission.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from realtime.google_data import digest
from realtime.google_domain import DomainError
from realtime.google_feed_delivery import (
    FEED_CONTRACTS, prepare_public_feed_run, validate_feed_candidate)
from realtime.google_whale_readback import ReadbackTarget, WhaleReadbackClient

MAX_CANDIDATE_BYTES = 64 * 1024 * 1024


def load_candidate(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CANDIDATE_BYTES:
        raise DomainError('invalid_argument', 'Invalid candidate file')
    candidate = json.loads(path.read_text(encoding='utf-8'))
    validate_feed_candidate(candidate)
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare = actions.add_parser('prepare', help='Read one existing public request; create a new private candidate file')
    prepare.add_argument('--source', required=True, choices=tuple(FEED_CONTRACTS))
    prepare.add_argument('--request-id', required=True)
    prepare.add_argument('--output', required=True)
    prepare.add_argument('--database-url-env', default='DATABASE_URL')
    verify = actions.add_parser('verify', help='Compare a candidate to one immutable Consumer version; never upload')
    verify.add_argument('--candidate', required=True)
    verify.add_argument('--target', required=True, help='JSON object accepted by ReadbackTarget')
    verify.add_argument('--base-url', required=True)
    verify.add_argument('--consumer-key-env', default='WHALE_CONSUMER_KEY')
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            target = Path(args.output)
            if target.exists() or target.is_symlink():
                raise DomainError('invalid_argument', 'Output already exists')
            candidate = prepare_public_feed_run(os.environ[args.database_url_env], args.request_id, source=args.source)
            encoded = (json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
            if len(encoded) > MAX_CANDIDATE_BYTES:
                raise DomainError('invalid_argument', 'Candidate exceeds size limit')
            # Write privately, then atomically publish without overwriting any
            # earlier evidence. A failed write never exposes a partial target.
            fd, staging = tempfile.mkstemp(prefix='.feed-candidate-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(staging, target)
            finally:
                Path(staging).unlink(missing_ok=True)
            summary = validate_feed_candidate(candidate)
            print(json.dumps(dict(status='prepared', **summary,
                admission_status='candidate_not_approved', candidate_sha256=digest(candidate), uploads=0)))
            return 0
        candidate = load_candidate(args.candidate)
        target = ReadbackTarget(**json.loads(Path(args.target).read_text(encoding='utf-8')))
        key = os.environ.get(args.consumer_key_env)
        if not key:
            raise DomainError('not_authorized', 'Consumer key is not configured')
        client = WhaleReadbackClient(args.base_url, key, min_interval_seconds=1)
        result = client.read_full_feed_run(target, expected_candidate=candidate)
        print(json.dumps(dict(status='sample_roundtrip_verified', **result['proof'], uploads=0)))
        return 0
    except DomainError as error:
        print(json.dumps(dict(status='incomplete', reason=error.code, uploads=0)))
        return 2
    except Exception:
        # DB/network exceptions can carry a DSN or key. Never emit their text.
        print(json.dumps(dict(status='incomplete', reason='candidate_probe_failed', uploads=0)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
