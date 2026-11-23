"""Bounded website search flow behind the existing site access gateway."""
import hashlib
import hmac
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .product import ProductError, ProductStore, middle_search_payload
from .google_domain import DomainError


def secret():
    try:
        return Path(os.getenv('SEARCHFLOW_QUICK_REQUEST_KEY_FILE',
                              '/app/state/private/quick-request-key')).read_text().strip()
    except OSError:
        return ''


def receipt(key, job_id):
    return hmac.new(key.encode(), ('quick-request:'+job_id).encode(), hashlib.sha256).hexdigest()


def handle(handler, method, *, public=None):
    from .product_http import body, secure
    parsed = urlparse(handler.path)
    if public is not None:
        parsed = parsed._replace(path=parsed.path.replace('/api/account/search', '/api/quick-request', 1))
    if not (parsed.path == '/api/quick-request' or parsed.path.startswith('/api/quick-request/')):
        return False
    try:
        if os.getenv('SEARCHFLOW_PRODUCT_ENABLED') != 'true' or not secure(handler):
            raise ProductError('unavailable', '快速请求暂不可用', 503)
        if public is None and handler.headers.get('X-SearchFlow-Quick') != '1':
            raise ProductError('forbidden', '请从网站快速请求页面操作', 403)
        origin = handler.headers.get('Origin')
        if origin and origin != 'https://' + handler.headers.get('Host', ''):
            raise ProductError('forbidden', '请求来源不匹配', 403)
        if public is not None:
            store, account = public.store, public.account
            key = None
        else:
            key = secret()
            if not key:
                raise ProductError('unavailable', '快速请求正在准备，请稍后重试', 503)
            store = ProductStore(handler.config.database_url)
            account = store.authenticate(key)
        if parsed.path == '/api/quick-request/catalog' and method == 'GET' and not parsed.query:
            from .serp_console import REGIONS, NEWS_LOCALES
            from .customer_data import field_catalog
            from .maps_provider import availability
            handler.send_json(dict(regions=list(REGIONS), news_locales=list(NEWS_LOCALES),
                                   fields=field_catalog(), maps=availability()))
            return True
        if parsed.path in ('/api/quick-request/history', '/api/quick-request/record'):
            if method != 'POST' or parsed.query:
                raise ProductError('method_not_allowed', '请提交读取条件', 405)
            from .customer_data import CustomerDataStore
            data = CustomerDataStore(store, account['id'])
            payload = body(handler)
            if parsed.path.endswith('/history'):
                allowed = {'query', 'query_match', 'source', 'domain', 'from', 'to',
                           'classification', 'country', 'language', 'device', 'page',
                           'new_only', 'limit', 'cursor'}
                if set(payload) - allowed or any(not isinstance(v, str) for v in payload.values()):
                    raise ProductError('invalid_parameter', '历史筛选参数无效')
                result = data.requests(dict(payload, scope='public', field_contract='prd-v1'))
                result['receipts'] = {r['request_id']: receipt(key, 'public:'+r['request_id'])
                                      for r in result['requests']}
            else:
                if set(payload) != {'request_id'} or not isinstance(payload['request_id'], str):
                    raise ProductError('invalid_parameter', '请填写请求 ID')
                record = data.request(payload['request_id'], {'scope': 'public', 'field_contract': 'prd-v1'})
                result = dict(request=record, token=receipt(key, 'public:'+record['request_id']))
            handler.send_json(result)
            return True
        public_export = re.fullmatch(r'/api/quick-request/records/([0-9a-f-]{36})/export', parsed.path)
        if public_export:
            rid = public_export.group(1)
            if method != 'GET':
                raise ProductError('method_not_allowed', '请使用下载操作', 405)
            if not hmac.compare_digest(handler.headers.get('X-Quick-Token', ''), receipt(key, 'public:'+rid)):
                raise ProductError('not_found', '记录不存在', 404)
            params = parse_qs(parsed.query, keep_blank_values=True)
            if set(params) != {'format'} or len(params['format']) != 1 or params['format'][0] not in ('csv', 'jsonl'):
                raise ProductError('invalid_parameter', '下载格式仅支持 CSV 或 JSONL')
            from .customer_data import CustomerDataStore
            from .customer_export import export_record
            record = CustomerDataStore(store, account['id']).request(rid, {'scope': 'public', 'field_contract': 'prd-v1'})
            export_record(handler, record, params['format'][0])
            return True
        if parsed.path == '/api/quick-request':
            if method != 'POST' or parsed.query:
                raise ProductError('method_not_allowed', '请提交一次搜索', 405)
            payload = body(handler)
            if set(payload) - {'query', 'source', 'region', 'language', 'trends_mode', 'trends_time_range', 'max_items'}:
                raise ProductError('invalid_parameter', '只支持关键词、来源、地区和语言')
            idem = handler.headers.get('Idempotency-Key')
            if public is not None:
                from .self_service import SEARCH_ITEMS
                limit = payload.setdefault('max_items', SEARCH_ITEMS)
                if type(limit) is not int or not 1 <= limit <= SEARCH_ITEMS:
                    raise ProductError('invalid_parameter', f'Choose 1–{SEARCH_ITEMS} results per search.')
                payload = middle_search_payload(payload)
                jid, reused = public.submit(lambda db: store.submit(account, payload, idem, handler.config, _db=db))
            else:
                payload = middle_search_payload(payload)
                jid, reused = store.submit(account, payload, idem, handler.config)
            result = {'id': jid, 'reused': reused}
            if key:
                result['token'] = receipt(key, jid)
            handler.send_json(result, 202)
            return True
        if parsed.path == '/api/quick-request/cancel-submission':
            if method != 'POST' or parsed.query:
                raise ProductError('method_not_allowed', '请使用停止操作', 405)
            if body(handler):
                raise ProductError('invalid_parameter', '停止操作仅接受请求标识')
            idem = handler.headers.get('Idempotency-Key', '')
            if not re.fullmatch(r'quick-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', idem):
                raise ProductError('invalid_parameter', '快速请求标识无效')
            job = store.cancel_quick_submission(account, idem)
            jid = str(job['id'])
            handler.send_json({'id': jid, 'status': job['status'], 'token': receipt(key, jid)})
            return True
        match = re.fullmatch(r'/api/quick-request/([0-9a-f-]{36})(?:/(export|cancel|data))?', parsed.path)
        if not match:
            raise ProductError('not_found', '任务不存在', 404)
        jid, action = match.groups()
        if key and not hmac.compare_digest(handler.headers.get('X-Quick-Token', ''), receipt(key, jid)):
            raise ProductError('not_found', '任务不存在', 404)
        if action == 'cancel' and method == 'POST' and not parsed.query:
            handler.send_json(store.cancel(account['id'], jid))
            return True
        if method != 'GET':
            raise ProductError('method_not_allowed', '请求方法不受支持', 405)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if action == 'export':
            if set(params) != {'format'} or len(params['format']) != 1 or params['format'][0] not in ('csv', 'jsonl'):
                raise ProductError('invalid_parameter', '下载格式仅支持 CSV 或 JSONL')
            job = store.job(account['id'], jid)
            if job['status'] != 'succeeded':
                raise ProductError('job_not_ready', '任务还未完成', 409)
            from .customer_data import CustomerDataStore
            from .customer_export import export_record
            record = CustomerDataStore(store, account['id']).job_data(jid, 'prd-v1')
            export_record(handler, record, params['format'][0])
            return True
        if params:
            raise ProductError('invalid_parameter', '此操作不接受查询参数')
        if action == 'data':
            from .customer_data import CustomerDataStore
            handler.send_json(CustomerDataStore(store, account['id']).job_data(jid, 'prd-v1'))
        else:
            job = store.job(account['id'], jid)
            if public is not None:
                job = {name: job[name] for name in ('id', 'status', 'error_code')}
            handler.send_json(job)
    except ProductError as exc:
        handler.send_json({'error': str(exc), 'code': exc.code}, exc.status)
    except DomainError as exc:
        handler.send_json({'error': str(exc), 'code': exc.code}, exc.status)
    except (ValueError, TypeError):
        handler.send_json({'error': '请求参数无效', 'code': 'invalid_parameter'}, 400)
    except Exception:
        handler.send_json({'error': '服务暂不可用，请稍后重试', 'code': 'unavailable'}, 503)
    return True
