"""Trusted synchronous sample port; no initialization, threads or source on import."""
from .data_capability_executor import CapabilityExecutor
from .data_capability_export import export_job
from .data_capability_jobs import amount, job_id, required_text
from .product import ProductError


def run_sample_step(executor, tenant_id, observation_job_id, *, maximum_cost, currency):
    """One target-only step for an already approved, queued sample.

    Caller supplies an initialized executor, current grants, configured budget
    and any required restore admission. This port grants none of these and is
    not a customer authentication endpoint. No polling or automatic retry.
    """
    if not isinstance(executor, CapabilityExecutor) or executor.is_alive():
        raise ValueError('explicit stopped sample executor required')
    required_text(tenant_id, 'tenant_id')
    jid = job_id(observation_job_id)
    ceiling = amount(maximum_cost)
    row = executor.service.get(tenant_id, jid)
    if (row['capability'], row['provider']) != (executor.capability, executor.provider):
        raise ProductError('sample_scope_mismatch', '样本任务与执行器来源不符', 409)
    if row['currency'] != currency or amount(row['reserved']) > ceiling:
        raise ProductError('sample_budget_mismatch', '样本币种或预留费用超过本次批准上界', 409)
    if row['status'] != 'queued':
        raise ProductError('sample_not_queued', '仅执行明确排队的样本，不重放已执行任务', 409)
    stepped = executor.run_once(target_tenant_id=tenant_id, target_job_id=jid)
    row = executor.service.get(tenant_id, jid)
    report = dict(job_id=jid, capability=row['capability'], status=row['status'],
                  step_entered=stepped, actual_cost=row['actual_cost'], currency=row['currency'],
                  reservation_status=row['reservation_status'], error_code=row['error_code'],
                  source_calls_not_inferred_from_step=True, platform_delivery='not_submitted')
    if row['status'] in ('succeeded', 'partial'):
        content, proof = export_job(executor.service, tenant_id, jid,
                                   executor.grant_for(tenant_id, row['capability']), 'zip')
        return dict(report=report, bundle=content, proof=proof)
    # In-flight uncertainty stays held for reconciliation; never refund or
    # manufacture an artifact. A throttled step remains queued for an operator.
    return dict(report=report, bundle=None, proof=None)


def run_sample_file(executor, tenant_id, observation_job_id, *, maximum_cost, currency, output):
    """Publish one private ZIP without overwriting any existing pathname."""
    import hashlib
    import os
    from pathlib import Path
    import stat
    import tempfile
    from .data_capability_export import verify_bundle

    destination = Path(output)
    parent = destination.parent
    # Refuse an unsuitable destination before entering a possibly paid source.
    if not parent.is_dir() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise ValueError('existing private output directory required')
    if os.path.lexists(destination):
        raise FileExistsError('sample output already exists')
    sample = run_sample_step(executor, tenant_id, observation_job_id,
                             maximum_cost=maximum_cost, currency=currency)
    if sample['bundle'] is None:
        return dict(report=sample['report'], proof=None, published=False)
    proof, content = sample['proof'], sample['bundle']
    verify_bundle(content, expected_observation_id=proof['observation_id'],
                  expected_payload_hash=proof['payload_hash'], expected_snapshot_hash=proof['snapshot_hash'])
    if len(content) != proof['bytes'] or hashlib.sha256(content).hexdigest() != proof['sha256']:
        raise ProductError('corrupt_bundle', '样本导出文件摘要不符', 503)
    descriptor, name = tempfile.mkstemp(prefix='.sample-', dir=parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        current = executor.service.job_data(tenant_id, observation_job_id,
                    executor.grant_for(tenant_id, sample['report']['capability']))
        if current['observation']['snapshot_hash'] != proof['snapshot_hash']:
            raise ProductError('version_conflict', '发布前样本版本变化', 409)
        # Hard-link creation is exclusive even if a competing writer appeared
        # after preflight. No rename-overwrite and no partially visible ZIP.
        os.link(name, destination)
        try:
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # A complete file may already exist: never suggest rerunning source.
            raise ProductError('sample_publication_unconfirmed', '文件已完整发布但目录持久化未确认，须先核对既有文件，不重放来源', 503) from None
    finally:
        os.unlink(name)
    return dict(report=sample['report'], proof=proof, published=True)


def main(argv=None):
    """Operator CLI: factory is trusted local code, never customer input."""
    import argparse
    import importlib
    import json
    import re

    parser = argparse.ArgumentParser(description='执行一个已有获准样本任务；不初始化、采购或自动重试')
    parser.add_argument('--factory', required=True, help='可信本地 module:callable，返回已初始化且线程停止的执行器')
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--maximum-cost', required=True)
    parser.add_argument('--currency', required=True)
    parser.add_argument('--output', required=True, help='现有私有目录内的新ZIP路径；绝不覆盖')
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', args.factory):
            raise ValueError('trusted factory name required')
        job_id(args.job); amount(args.maximum_cost); required_text(args.tenant, 'tenant')
        if not re.fullmatch('[A-Z]{3}', args.currency):
            raise ValueError('currency required')
        module, function = args.factory.split(':')
        executor = getattr(importlib.import_module(module), function)()
        result = run_sample_file(executor, args.tenant, args.job, maximum_cost=args.maximum_cost,
                                 currency=args.currency, output=args.output)
        # Do not print factory exceptions, response content or arbitrary errors.
        print(json.dumps(dict(published=result['published'], job_id=args.job,
                              status=result['report']['status'], proof=result['proof']), ensure_ascii=False))
        return 0 if result['published'] else 2
    except KeyboardInterrupt:
        print('{"error":"sample_interrupted_check_existing_job_before_retry"}')
        return 130
    except Exception:
        print('{"error":"sample_not_confirmed_check_existing_job_and_output_before_retry"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
