"""New-domain routes mounted after existing customer authentication.

The existing external site-access gateway remains required. No credentials,
providers, SQL schemas, or executors are automatically configured here.
"""
from __future__ import annotations

import re

from .customer_http import parameters
from .data_capability_contract import CAPABILITIES
from .data_capability_export import build_bundle, export_job, sha
from .data_capability_queries import CapabilityRuntime, submission
from .product import ProductError

TYPES = {
    '/v1/data/trends/series': 'series',
    '/v1/data/keyword-volumes': 'volume',
    '/v1/data/ai/answers': 'answer',
}
CHILDREN = {'points': 'point', 'months': 'month', 'blocks': 'block', 'citations': 'citation', 'links': 'link'}
MIMES = {'json': 'application/json; charset=utf-8', 'jsonl': 'application/x-ndjson; charset=utf-8',
         'csv': 'text/csv; charset=utf-8', 'zip': 'application/zip', 'manifest': 'application/json; charset=utf-8'}


def runtime_for(handler):
    candidate = getattr(getattr(handler, 'server', None), 'data_capability_runtime', None)
    return candidate if isinstance(candidate, CapabilityRuntime) else None


def only(p, names):
    if set(p) - set(names):
        raise ProductError('invalid_parameter', '不支持的查询参数')


def send_file(handler, content, proof, format):
    # send_body's existing interface has no extra-header port. The standalone
    # manifest and JSON metadata carry independent version/hash proofs instead.
    handler.send_body(content, MIMES[format])


def handle(handler, method, path, query, account):
    runtime = runtime_for(handler)
    new = path in ('/v1/data/jobs', '/v1/data/jobs/preview') or path == '/v1/data/observations' or path.startswith('/v1/data/observations/')
    matched = None
    for prefix, kind in TYPES.items():
        if path == prefix or path.startswith(prefix + '/'):
            matched = prefix, kind
            new = True
            break
    p = None
    job_match = re.fullmatch(r'/v1/jobs/([^/]+)(?:/(retry|lineage|progress|cancel|pause|resume|data|export|response(?:/export)?))?', path)
    if path == '/v1/jobs':
        p = parameters(query)
        domain = p.pop('domain', 'search')
        if domain not in ('search', 'data'):
            raise ProductError('invalid_parameter', 'domain仅支持search/data')
        new = domain == 'data'
    elif job_match and runtime:
        try:
            runtime.service.get(str(account['id']), job_match[1])
        except ProductError as exc:
            if exc.code != 'not_found':
                raise
            return False
        new = True
    elif path == '/v1/data/export' and 'capability' in query:
        new = True
    if not new:
        return False
    if runtime:runtime.ensure_recovered()
    p = parameters(query) if p is None else p
    tenant = str(account['id'])
    if path in ('/v1/data/jobs', '/v1/data/jobs/preview'):
        if method != 'POST':
            raise ProductError('method_not_allowed', '请使用POST提交数据任务；列表在/v1/jobs?domain=data', 405)
        only(p, ())
        from .product_http import body
        payload = body(handler, reject_duplicates=True)
        if path.endswith('/preview'):
            if not runtime:
                from .data_capability_contract import validate_request
                validate_request(payload)
                raise ProductError('source_not_admitted', '新数据能力尚未配置获准来源', 409)
            handler.send_json(runtime.preview(tenant, payload))
            return True
        if not runtime:
            # Validate capability/parameter shape even when the installation is
            # not configured, but never accept a permanently empty job.
            submission(payload, handler.headers.get('Idempotency-Key'))
            raise ProductError('source_not_admitted', '新数据能力尚未配置获准来源和执行器', 409)
        result = runtime.submit(tenant, payload, handler.headers.get('Idempotency-Key'))
        handler.send_json(result, 202)
        return True
    if not runtime:
        raise ProductError('data_capabilities_disabled', '新数据域尚未配置；现有四来源接口保持可用', 503)
    if path == '/v1/jobs':
        if method != 'GET':
            raise ProductError('method_not_allowed', '请使用GET', 405)
        result = runtime.jobs(tenant, p)
    elif job_match:
        jid, action = job_match.groups()
        if action in ('retry','lineage'):
            only(p, ())
            from . import data_capability_retry as retries
            if action=='lineage' and method=='GET':result=retries.lineage(runtime,tenant,jid)
            elif action=='retry' and method=='GET':result=retries.preview(runtime,tenant,jid)
            elif action=='retry' and method=='POST':
                from .product_http import body
                result=retries.submit(runtime,tenant,jid,body(handler,reject_duplicates=True),handler.headers.get('Idempotency-Key'))
                handler.send_json(result,202);return True
            else:raise ProductError('method_not_allowed','重试仅支持GET/POST，关联仅支持GET',405)
        elif method == 'POST' and action == 'cancel':
            only(p, ())
            from .product_http import body
            if body(handler, reject_duplicates=True) != {}:
                raise ProductError('invalid_parameter', '取消请求须为空对象')
            result = runtime.cancel(tenant, jid)
        elif method != 'GET' or action in ('cancel', 'pause', 'resume'):
            raise ProductError('method_not_allowed', '此操作不适用于新数据任务；在途取消保留实际计费', 405)
        elif action in ('response','response/export'):
            only(p,('format','snapshot_hash') if action.endswith('/export') else ())
            snapshot=runtime.response(tenant,jid)
            if action=='response':
                result=snapshot
            else:
                format=p.get('format','zip')
                if format not in MIMES:
                    raise ProductError('invalid_parameter','无效导出格式')
                if p.get('snapshot_hash') and p['snapshot_hash']!=snapshot['snapshot_hash']:
                    raise ProductError('version_conflict','指定的固定响应版本不符',409)
                from .data_capability_response_export import build_bundle as response_bundle
                bundle=response_bundle(snapshot)
                # Recheck current rights/cancel/retention before publication.
                if runtime.response(tenant,jid)['snapshot_hash']!=snapshot['snapshot_hash']:
                    raise ProductError('version_conflict','响应版本已变化',409)
                name={'json':'response.json','jsonl':'response.jsonl','csv':'response.csv','zip':'bundle.zip','manifest':'manifest.json'}[format]
                send_file(handler,bundle['files'][name],bundle['manifest'],format)
                return True
        elif action == 'export':
            only(p, ('format', 'snapshot_hash'))
            format = p.get('format', 'jsonl')
            if format not in MIMES:
                raise ProductError('invalid_parameter', '无效导出格式')
            saved = runtime.job_data(tenant, jid)
            if p.get('snapshot_hash') and p['snapshot_hash'] != saved['observation']['snapshot_hash']:
                raise ProductError('version_conflict', '指定的固定事实版本不符', 409)
            job = runtime.job(tenant, jid)
            content, proof = export_job(runtime.service, tenant, jid, runtime.grant(tenant, job['capability']), format)
            runtime.job_data(tenant, jid)  # Fresh rights and erasure check after generation.
            send_file(handler, content, proof, format)
            return True
        else:
            only(p, ())
            result = runtime.job_data(tenant, jid) if action == 'data' else runtime.progress(tenant, jid) if action == 'progress' else runtime.job(tenant, jid)
    elif method != 'GET':
        raise ProductError('method_not_allowed', '事实读取仅支持GET', 405)
    elif path == '/v1/data/observations':
        result = runtime.observations(tenant, p)
    elif path.startswith('/v1/data/observations/'):
        only(p, ())
        oid = path.removeprefix('/v1/data/observations/')
        if '/' in oid:
            raise ProductError('not_found', '接口不存在', 404)
        result = runtime.observation(tenant, oid)
    elif path == '/v1/data/export':
        only(p, ('capability', 'observation_id', 'snapshot_hash', 'format'))
        if p.get('capability') not in CAPABILITIES or not p.get('observation_id'):
            raise ProductError('invalid_parameter', '新域导出需要有效capability和observation_id')
        saved = runtime.observation(tenant, p['observation_id'])
        if p.get('capability') != saved['capability']:
            raise ProductError('invalid_parameter', 'capability与观测类型不符')
        if p.get('snapshot_hash') and p['snapshot_hash'] != saved['snapshot_hash']:
            raise ProductError('version_conflict', '指定的固定事实版本不符', 409)
        format = p.get('format', 'zip')
        if format not in MIMES:
            raise ProductError('invalid_parameter', '无效导出格式')
        bundle = build_bundle(saved)
        filename = {'json': 'observation.json', 'jsonl': 'records.jsonl', 'csv': 'records.csv', 'zip': 'bundle.zip', 'manifest': 'manifest.json'}[format]
        content = bundle['files'][filename]
        current = runtime.observation(tenant, p['observation_id'])
        if current['snapshot_hash'] != saved['snapshot_hash']:
            raise ProductError('version_conflict', '导出生成期间事实版本改变', 409)
        send_file(handler, content, dict(sha256=sha(content)), format)
        return True
    elif matched:
        prefix, kind = matched
        if path == prefix:
            result = runtime.observations(tenant, p, kind=kind)
        else:
            remainder = path[len(prefix) + 1:].split('/')
            if len(remainder) == 1:
                only(p, ())
                result = runtime.typed(tenant, remainder[0], kind)
            elif len(remainder) == 2 and remainder[1] in CHILDREN and {'point': 'series', 'month': 'volume', 'block': 'answer', 'citation': 'answer', 'link': 'answer'}[CHILDREN[remainder[1]]] == kind:
                result = runtime.children(tenant, remainder[0], CHILDREN[remainder[1]], p)
            else:
                raise ProductError('not_found', '接口不存在', 404)
    else:
        raise ProductError('not_found', '接口不存在', 404)
    handler.send_json(result)
    return True
