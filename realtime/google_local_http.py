"""Customer-authenticated HTTP surface for on-demand Google Places data."""
from __future__ import annotations

import re
import os
import hmac
from urllib.parse import parse_qs, unquote, urlparse

from .google_local_jobs import LocalJobStore
from .maps_provider import availability
from .product import ProductError, ProductStore


def handle(handler, method):
    from .product_http import body, secure
    parsed = urlparse(handler.path)
    path = parsed.path
    website = path.startswith('/api/maps/')
    if website:
        path = path.replace('/api/maps/', '/v1/google/local/', 1)
        path = path.replace('/v1/google/local/places/', '/v1/google/places/', 1)
    if not (path in ("/v1/google/local/capabilities", "/v1/google/local/jobs")
            or re.fullmatch(r"/v1/google/local/jobs/[^/]+(?:/(?:results|export))?", path)
            or re.fullmatch(r"/v1/google/places/[^/]+", path)):
        return False
    try:
        if os.getenv("SEARCHFLOW_PRODUCT_ENABLED") != "true":
            raise ProductError("unavailable","地图查询暂不可用",503)
        if not secure(handler):
            raise ProductError("https_required", "请通过 HTTPS 使用客户密钥", 403)
        auth = handler.headers.get("Authorization", "")
        store = ProductStore(handler.config.database_url)
        site_key = None
        if website:
            from .quick_request_http import secret, receipt
            origin = handler.headers.get('Origin')
            if handler.headers.get('X-SearchFlow-Quick') != '1' or (origin and origin != 'https://'+handler.headers.get('Host','')):
                raise ProductError('forbidden','请从本站地图页面操作',403)
            site_key = secret()
            if not site_key:
                raise ProductError('unavailable','地图查询正在准备',503)
            account = store.authenticate(site_key)
        else:
            account = store.authenticate(auth[7:] if auth.startswith("Bearer ") else "")
        jobs = LocalJobStore(store.base)
        if parsed.query and not parsed.path.endswith('/export'):
            raise ProductError('invalid_parameter', '此操作不接受查询参数')
        if path == "/v1/google/local/capabilities" and method == "GET":
            handler.send_json(availability())
            return True
        if path == "/v1/google/local/jobs" and method == "POST":
            jid, reused = jobs.submit(account, body(handler, reject_duplicates=True),
                                      handler.headers.get("Idempotency-Key"))
            job = jobs.job(account['id'], jid)
            token = {"token": receipt(site_key, 'maps:'+jid)} if website else {}
            prefix = '/api/maps/jobs/' if website else '/v1/google/local/jobs/'
            handler.send_json({**token,"id": jid, "reused": reused, "status": job['status'],
                               "status_url": prefix + jid,
                               "results_url": prefix + jid + "/results",
                               "export_url": prefix + jid + "/export",
                               "poll_after_seconds": 3}, 202)
            return True
        match = re.fullmatch(r"/v1/google/local/jobs/([^/]+)(/(?:results|export))?", path)
        if match and method == "GET":
            if website and not hmac.compare_digest(handler.headers.get('X-Quick-Token',''), receipt(site_key,'maps:'+match.group(1))):
                raise ProductError('not_found','Maps 任务不存在',404)
            if match.group(2)=='/export':
                query=parse_qs(parsed.query,keep_blank_values=True)
                if set(query)!={'format'} or len(query['format'])!=1 or query['format'][0] not in ('csv','jsonl'):
                    raise ProductError('invalid_parameter','下载格式仅支持 CSV 或 JSONL')
                job=jobs.job(account['id'],match.group(1),include_results=True)
                if job['status']!='succeeded':raise ProductError('job_not_ready','地图查询尚未完成',409)
                from .maps_export import export
                export(handler,job,query['format'][0])
            else:
                if parsed.query:raise ProductError('invalid_parameter','此操作不接受查询参数')
                handler.send_json(jobs.job(account["id"], match.group(1), include_results=bool(match.group(2))))
            return True
        match = re.fullmatch(r"/v1/google/places/([^/]+)", path)
        if match and method == "GET":
            raise ProductError("maps_details_unavailable","自建快速模式不提供独立地点详情，请查看本次商家结果",409)
        raise ProductError("method_not_allowed", "此方法不受支持", 405)
    except ProductError as exc:
        handler.send_json({"error": str(exc), "code": exc.code}, exc.status)
        return True
    except (ValueError,TypeError):
        handler.send_json({'error':'地图请求参数无效','code':'invalid_parameter'},400)
        return True
