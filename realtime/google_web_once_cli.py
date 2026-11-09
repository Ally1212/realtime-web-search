"""Explicit one-shot CLI. Default prints a plan and performs no I/O to services."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict

from .config import Config
from .google_data import digest
from .google_web_once import WebOnce,web_plan,_write
from .google_web_once_source import SingleWebSource
from .google_web_once_platform import SingleRunPlatform,_endpoint


class IdleGuard:
    def __init__(self,dsn,stop_path):
        self.dsn,self.stop_path=dsn,Path(stop_path)
        self.last_snapshot=None

    def __call__(self,plan):
        if self.stop_path.exists() or self.stop_path.is_symlink():
            raise ValueError('operator stop requested')
        with psycopg.connect(self.dsn,row_factory=dict_row) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            db.execute("SET LOCAL statement_timeout='10s'")
            settings=db.execute("SELECT content_fetch_enabled,runtime_config->>'continuous_enabled' continuous FROM serp_collector_settings WHERE id=true").fetchone()
            whale=db.execute('SELECT uploads_enabled,claims_enabled FROM whale_serp_settings WHERE id=true').fetchone()
            if (not settings or settings['content_fetch_enabled'] is not False or settings['continuous']!='false'
                    or not whale or whale['uploads_enabled'] is not False or whale['claims_enabled'] is not False):
                raise ValueError('automatic controls are enabled or unknown')
            checks={
                'manual_tasks':"SELECT count(*) n FROM campaigns c WHERE c.status='active' AND EXISTS(SELECT 1 FROM serp_task_options o WHERE o.campaign_id=c.id) AND NOT EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id)",
                'page_leases':'SELECT count(*) n FROM google_page_frontier WHERE lease_token IS NOT NULL AND lease_expires_at>clock_timestamp()',
                'query_leases':'SELECT count(*) n FROM discovery_query_leases WHERE expires_at>clock_timestamp()',
                'upload_leases':'SELECT count(*) n FROM whale_serp_outbox WHERE lease_token IS NOT NULL AND lease_expires_at>clock_timestamp()',
                'platform_jobs':"SELECT count(*) n FROM whale_serp_jobs WHERE status IN ('active','paused','queued','running') OR (kind='platform' AND status='stopped' AND NOT reported)",
                'customer_jobs':"SELECT count(*) n FROM sf_jobs WHERE status IN ('queued','running')",
                'monitors':'SELECT count(*) n FROM sf_monitors WHERE enabled',
            }
            counts={name:db.execute(query).fetchone()['n'] for name,query in checks.items()}
            if any(counts.values()):raise ValueError('unrelated work or monitors are live')
            self.last_snapshot=dict(checked_at=datetime.now(timezone.utc).isoformat(),
                settings=settings,whale_settings=whale,live_work=counts,source_read_only=True)
        if self.stop_path.exists() or self.stop_path.is_symlink():
            raise ValueError('operator stop requested')
        return self.last_snapshot


def manifest(config,args):
    root=Path(args.journal).absolute()
    if root.resolve()!=root:raise ValueError('journal must not contain symlink ancestors')
    modules=sorted(p.name for p in Path(__file__).parent.glob('*.py'))
    connection=conninfo_to_dict(config.database_url)
    return dict(version='web-once-execution-1',plan=web_plan(dataset_id=args.dataset_id,
        source_name=args.source_name,collector_id=args.collector_id),journal=str(root),
        platform_origin=_endpoint(args.platform_origin,False),
        source_origin=config.openserp_url,proxy_profile=config.default_proxy_profile,
        source_timeout_seconds=config.openserp_request_timeout_seconds,
        database_target_hash=digest({k:connection.get(k) for k in ('host','hostaddr','port','dbname','user','options')}),
        code_hashes={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in modules})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','preflight','advance'),nargs='?',default='plan')
    for name in ('journal','dataset-id','source-name','collector-id','platform-origin'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--authorized-manifest-hash')
    args=parser.parse_args(argv)
    try:
        config=Config()
        execution=manifest(config,args)
        execution_hash=digest(execution)
        if args.mode=='plan':
            print(json.dumps(dict(execution=execution,manifest_hash=execution_hash,executed=False)))
            return 0
        guard=IdleGuard(config.database_url,Path(args.journal)/'STOP')
        if args.mode=='preflight':
            print(json.dumps(dict(manifest_hash=execution_hash,preflight=guard(execution['plan']),executed=False)))
            return 0
        if args.authorized_manifest_hash!=execution_hash:
            raise ValueError('exact execution manifest requires explicit authorization')
        # Authorization must exist in the operator conversation; a hash alone
        # is not approval. Never infer consent from plan/preflight execution.
        guard(execution['plan'])
        root=Path(execution['journal']);root.mkdir(mode=0o700,exist_ok=True)
        if root.stat().st_mode & 0o077:raise ValueError('private journal required')
        bound=root/'execution.json'
        if bound.is_symlink():raise ValueError('invalid execution binding')
        if bound.exists():
            if json.loads(bound.read_text())!=execution:raise ValueError('execution binding changed')
        else:
            # Exclusive binding prevents accidentally rebinding the same journal.
            fd=os.open(bound,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(execution,stream);stream.flush();os.fsync(stream.fileno())
        _write(root/'preflight.json',guard.last_snapshot)
        state_exists=(root/'state.json').exists()
        source=SingleWebSource(config,idle_guard=guard,
            evidence_path=None if state_exists else root/'source-proof.json')
        platform=SingleRunPlatform(execution['platform_origin'],
            writer_key=config.whale_collector_api_key,
            consumer_key=os.environ.get('SEARCHFLOW_ONCE_CONSUMER_KEY',''))
        class Adapter:
            assert_idle=source.assert_idle
            acquire_once=source.acquire_once
            saved_public_request=source.saved_public_request
            validate=platform.validate
            bulk_once=platform.bulk_once
            receipt=platform.receipt
            readback=platform.readback
        try:
            runner=WebOnce(root,execution['plan'],Adapter())
            state=runner.advance(authorized_plan_hash=digest(execution['plan']))
            print(json.dumps(dict(manifest_hash=execution_hash,phase=state['phase'],
                receipt_not_before=state.get('receipt_not_before'),formal_admission=False)))
            return 0 if state['phase']=='verified' else 3
        finally:platform.close()
    except Exception:
        # Never echo exception messages: database/network errors may contain keys.
        print(json.dumps(dict(status='stopped_or_incomplete',executed_scope='inspect_private_journal')))
        return 2


if __name__=='__main__':raise SystemExit(main())
