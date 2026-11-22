"""Server-side Marsyoo live-query transport, deliberately not a local-job fallback.

This adapter does not enable collectors or uploads, migrate databases, or assert
persistence from a successful collection status. Callers must retain tenant/job
ownership and verify pinned Consumer versions before offering downloads.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from urllib.parse import quote, urlencode

import requests

from .google_whale_readback import WhaleReadbackClient
from .product import ProductError
from .serp_console import REGIONS

STATUSES = {'pending', 'collecting', 'partial', 'succeeded', 'failed', 'canceled'}
STAGES = {'pending': 'submitted_to_middle', 'collecting': 'collecting',
          'partial': 'ingest_pending', 'succeeded': 'ingest_pending',
          'failed': 'failed', 'canceled': 'cancelled'}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ProductError('invalid_parameter', '中台资源编号无效')
    return value


def web_request(payload, *, dataset_id='social_media_raw'):
    """Only the documented Web contract is admitted; never silently drop a source."""
    if not isinstance(payload, dict) or set(payload) - {'query', 'source', 'region', 'language'}:
        raise ProductError('invalid_parameter', '中台请求参数无效')
    if payload.get('source', 'google_web') != 'google_web':
        raise ProductError('middle_source_not_ready', '此来源尚未完成中台链路验收', 409)
    query = payload.get('query')
    region, language = payload.get('region', 'US'), payload.get('language', 'en')
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ProductError('invalid_query', '关键词需要 1–300 个字符')
    if region not in REGIONS or language not in ('en', 'zh'):
        raise ProductError('invalid_parameter', '中台请求地区或语言无效')
    return dict(dataset_id=_identifier(dataset_id), resource_kind='content',
                source_platforms=['google_search'], query=query.strip(), region=region,
                language=language, max_items=10, deadline_seconds=300)



SOURCE_PLATFORMS = {'google_web': 'google_search', 'google_news': 'google_news', 'google_trends': 'google_trends', 'google_images': 'google_search'}


def search_request(payload, *, dataset_id='social_media_raw'):
    """Preserve the requested source instead of converting feeds into Web."""
    if not isinstance(payload, dict) or payload.get('source', 'google_web') not in SOURCE_PLATFORMS:
        raise ProductError('middle_source_not_ready', '中台尚不支持此搜索来源', 409)
    source = payload.get('source', 'google_web')
    mode = payload.get('trends_mode', 'keyword' if source == 'google_trends' else None)
    if source == 'google_trends' and mode not in ('regional_hotlist','keyword'):
        raise ProductError('invalid_trends_mode', '趋势模式仅支持 keyword 或 regional_hotlist', 422)
    if source != 'google_trends' and mode is not None:
        raise ProductError('invalid_parameter', 'trends_mode 仅适用于 Google Trends')
    request = web_request({**{k: v for k, v in payload.items() if k not in ('trends_mode', 'trends_time_range', 'max_items')}, 'source': 'google_web'}, dataset_id=dataset_id)
    if source == 'google_trends':
        from .middle_collector import trends_conditions
        request['conditions'] = trends_conditions(mode,payload.get('trends_time_range','today 3-m'))
    request['source_platforms'] = [SOURCE_PLATFORMS[source]]
    request['max_items'] = 100 if source == 'google_web' else 1 if source == 'google_trends' and mode == 'keyword' else 10
    if source == 'google_images':
        from .middle_collector import image_conditions
        request['max_items'] = 20
        request['conditions'] = image_conditions()
    if 'max_items' in payload:
        count = payload['max_items']
        if type(count) is not int or not 1 <= count <= (20 if source == 'google_images' else 100):
            raise ProductError('invalid_parameter', 'max_items 超出来源支持范围')
        request['max_items'] = count
    return request

def upstream_idempotency_key(account_id, idempotency_key):
    """Scope a browser retry key to its owner without disclosing the owner ID."""
    if not isinstance(account_id, str) or not account_id:
        raise ProductError('invalid_parameter', '缺少请求所属空间')
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256 or not idempotency_key.strip():
        raise ProductError('idempotency_required', '请提供有效的幂等请求编号')
    encoded = json.dumps([account_id, idempotency_key], separators=(',', ':')).encode()
    return 'sf-middle-' + hashlib.sha256(encoded).hexdigest()


class MiddlePlatformClient:
    """No automatic POST retries, redirect following, or credentials in errors."""
    source_platform = 'google_search'

    def __init__(self, base_url, consumer_key, *, dataset_id='social_media_raw', session=None):
        # Reuse the existing HTTPS-only, credential-free endpoint validation.
        validated = WhaleReadbackClient(base_url, consumer_key)
        self.base_url = validated.base_url
        self._key = consumer_key
        self.dataset_id = _identifier(dataset_id)
        self._session = session or requests.Session()
        self._next_request_at = 0.0

    def _request(self, method, path, *, payload=None, idem=None):
        delay = getattr(self, '_next_request_at', 0.0) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_request_at = time.monotonic() + 2.1
        headers = {'Authorization': 'Bearer ' + self._key, 'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if idem is not None:
            headers['Idempotency-Key'] = idem
        try:
            response = self._session.request(method, self.base_url + path, headers=headers,
                                             json=payload, timeout=20, allow_redirects=False)
        except requests.RequestException:
            raise ProductError('middle_transport_unknown',
                               '中台暂未响应；重试提交必须沿用原幂等编号', 503) from None
        failures = {
            401: ('middle_unauthorized', '中台服务端凭据无效', 503),
            402: ('middle_credit_required', '中台额度不足', 503),
            403: ('middle_entitlement_denied', '中台尚未授权此数据集的 Google 实时查询', 503),
            404: ('not_found', '中台请求不存在或无权访问', 404),
            409: ('middle_not_ready', '中台结果尚未准备完成或幂等参数冲突', 409),
            429: ('middle_rate_limited', '中台请求频率或并发已达上限', 429),
        }
        if response.status_code in failures:
            code, message, status = failures[response.status_code]
            raise ProductError(code, message, status)
        if response.status_code not in (200, 202):
            raise ProductError('middle_unavailable', '中台请求未成功；未切换为本地采集', 503)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
        except ValueError:
            raise ProductError('middle_invalid_response', '中台响应格式无效', 503) from None
        return data

    def _live(self, data, *, expected_id=None, expected_request=None):
        try:
            live, runs = data['live_query'], data['runs']
            if (not isinstance(live, dict) or not isinstance(runs, list)
                    or live['status'] not in STATUSES or live['dataset_id'] != self.dataset_id
                    or live['resource_kind'] != 'content'):
                raise ValueError()
            live_id = _identifier(live['id'])
            if expected_id is not None and live_id != expected_id:
                raise ValueError()
            if expected_request is not None and any(live.get(k) != expected_request[k]
                                                     for k in ('query', 'max_items')):
                raise ValueError()
            if getattr(self, 'source', 'google_web') == 'google_images':
                from .middle_collector import image_conditions
                if live.get('conditions') != image_conditions():
                    raise ValueError()
            if getattr(self, 'source', 'google_web') == 'google_trends':
                if expected_request is not None and live.get('conditions') != expected_request.get('conditions'):
                    raise ValueError()
                from .middle_collector import trends_conditions
                if not any(live.get('conditions') == trends_conditions(m,w) for m in ('regional_hotlist','keyword') for w in ('now 7-d','today 1-m','today 3-m','today 12-m','today 5-y')):
                    raise ValueError()
            if len(runs) != 1:
                raise ValueError()
            run = runs[0]
            if (run['live_query_id'] != live_id or run['source_platform'] != self.source_platform
                    or run['status'] not in STATUSES):
                raise ValueError()
            task_id = _identifier(run['collection_task_id'])
            _identifier(run['id'])
            # Upstream errors/results are intentionally not exposed verbatim.
            result = dict(middle_query_id=live_id, platform_task_ids=[task_id],
                        platform_status=live['status'], stage=STAGES[live['status']],
                        download_ready=False, readback_verified=False)
            deadline = live.get('deadline_at')
            if isinstance(deadline, str):
                try:
                    parsed = datetime.fromisoformat(deadline)
                except ValueError:
                    parsed = None
                if parsed is not None and parsed.tzinfo is not None:
                    result['deadline_at'] = deadline
            return result
        except (KeyError, TypeError, ValueError, ProductError):
            raise ProductError('middle_invalid_response', '中台响应身份、范围或状态不匹配', 503) from None

    def create(self, account_id, payload, idempotency_key):
        request = web_request(payload, dataset_id=self.dataset_id)
        idem = upstream_idempotency_key(account_id, idempotency_key)
        return self._live(self._request('POST', '/v1/live-queries', payload=request, idem=idem),
                          expected_request=request)

    def status(self, middle_query_id):
        middle_query_id = _identifier(middle_query_id)
        return self._live(self._request('GET', '/v1/live-queries/' + quote(middle_query_id, safe='')),
                          expected_id=middle_query_id)

    def collection_deadline(self, middle_query_id, task_id):
        status = self.status(middle_query_id)
        if status['platform_task_ids'] != [task_id]:
            raise ProductError('middle_invalid_response', '中台任务标识不匹配', 503)
        return datetime.fromisoformat(status['deadline_at']) if status.get('deadline_at') else None

    def cancel(self, middle_query_id):
        middle_query_id = _identifier(middle_query_id)
        return self._live(self._request('POST', '/v1/live-queries/' + quote(middle_query_id, safe='') + '/cancel'),
                          expected_id=middle_query_id)

    def results_page(self, middle_query_id, *, cursor=None, limit=20):
        """Authorized Consumer documents, still NOT a verified downloadable run."""
        middle_query_id = _identifier(middle_query_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProductError('invalid_parameter', '中台分页数量需要 1–100')
        params = {'limit': limit}
        if cursor is not None:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ProductError('invalid_parameter', '中台分页游标无效')
            params['cursor'] = cursor
        data = self._request('GET', '/v1/live-queries/' + middle_query_id + '/results?' + urlencode(params))
        try:
            if data['live_query_id'] != middle_query_id or not isinstance(data['items'], list) or len(data['items']) > limit:
                raise ValueError()
            if not isinstance(data.get('next_cursor', ''), str):
                raise ValueError()
            for item in data['items']:
                document = item['document']
                # Live results currently serialize Go models in PascalCase.
                if 'ID' in document:
                    document = dict(id=document.get('ID'), dataset_id=document.get('DatasetID'),
                                    source_platform=document.get('SourcePlatform'))
                if document['dataset_id'] != self.dataset_id or document['source_platform'] != self.source_platform:
                    raise ValueError()
                _identifier(document['id'])
        except (KeyError, TypeError, ValueError, ProductError):
            raise ProductError('middle_invalid_response', '中台结果身份或范围不匹配', 503) from None
        return data


    def read_snapshot(self, document_id, version_id, *, expected_payload, expected_hash):
        from .middle_maps_contract import verify_snapshot
        document_id, version_id = _identifier(document_id), _identifier(version_id)
        data = self._request('GET', '/v1/documents/' + document_id + '/snapshots?limit=500')
        rows = data.get('snapshots')
        if not isinstance(rows, list):
            raise ProductError('middle_invalid_response', '中台快照响应无效', 503)
        selected = list({(json.dumps(row, sort_keys=True) if row.get('id') else str(index)): row
                         for index, row in enumerate(rows)
                         if isinstance(row, dict) and row.get('version_id') == version_id}.values())
        if len(selected) != 1:
            raise ProductError('middle_snapshot_unavailable', '本次查询固定版本快照缺失或不唯一', 503)
        snapshot = selected[0]
        if (snapshot.get('document_id') != document_id or snapshot.get('dataset_id') != self.dataset_id
                or snapshot.get('source_platform') != self.source_platform):
            raise ProductError('middle_snapshot_mismatch', '中台快照归属不一致', 503)
        if not isinstance(snapshot.get('platform_payload'), dict):
            # Some deployments expose metric rows here but hydrate raw payloads
            # only through the documented, explicitly read-only batch endpoint.
            snapshot_id = _identifier(snapshot.get('id'))
            observed = snapshot.get('snapshot_at')
            if not isinstance(observed, str) or not observed:
                raise ProductError('middle_snapshot_mismatch', '快照缺少固定身份', 503)
            versions = self._request('GET', '/v1/documents/' + document_id + '/versions').get('versions')
            if not isinstance(versions, list):
                raise ProductError('middle_invalid_response', '固定版本响应无效', 503)
            fixed = [v for v in versions if v.get('version_id') == version_id and v.get('document_id') == document_id]
            if len(fixed) != 1 or fixed[0].get('raw_payload_hash') != expected_hash:
                raise ProductError('middle_snapshot_mismatch', '固定版本载荷摘要不一致', 503)
            response = self._request('POST', '/v1/snapshots/payloads/batch-get', payload={
                'items': [dict(document_id=document_id, snapshot_id=snapshot_id, snapshot_at=observed)]})
            hydrated = response.get('snapshots')
            if (not isinstance(hydrated, list) or len(hydrated) != 1
                    or hydrated[0].get('document_id') != document_id
                    or hydrated[0].get('snapshot_id') != snapshot_id or hydrated[0].get('error')):
                raise ProductError('middle_snapshot_mismatch', '原始载荷快照身份不一致', 503)
            snapshot = {**snapshot, 'platform_payload': hydrated[0].get('platform_payload'),
                        'payload_hash': fixed[0]['raw_payload_hash']}
        return verify_snapshot(snapshot, version_id=version_id, expected_payload=expected_payload,
                               expected_hash=expected_hash)


class MapsMiddlePlatformClient(MiddlePlatformClient):
    """Maps uses the same live-query lifecycle with explicit conditions."""
    source_platform = 'google_maps'

    def __init__(self, base_url, consumer_key, *, session=None):
        super().__init__(base_url, consumer_key, dataset_id='google_maps_raw', session=session)

    def create(self, account_id, payload, idempotency_key):
        from .middle_maps_contract import maps_request
        from .google_local_jobs import parameters
        request = maps_request(parameters(payload))
        scoped_key = upstream_idempotency_key(account_id, idempotency_key)
        data = self._request('POST', '/v1/live-queries', payload=request,
                             idem='maps-' + scoped_key)
        # The current platform advertises a legacy Maps authorization cap of 21
        # even though one query snapshot may contain up to 100 businesses. It
        # silently clamps max_items. Accept that exact clamp only for Maps and
        # retain the customer's requested entity count in our claimed-task and
        # immutable snapshot validation; all spatial conditions remain strict.
        admitted=dict(request,max_items=min(request['max_items'],21))
        result = self._live(data, expected_request=admitted)
        self.check_conditions(data['live_query'], request)
        result['platform_max_items']=admitted['max_items']
        result['requested_max_items']=request['max_items']
        return result

    @staticmethod
    def check_conditions(live, expected):
        # Fail closed if the platform silently ignores a requested condition.
        from .google_data import digest
        # max_items is deliberately excluded: the platform's legacy Maps cap
        # is an authorization envelope for one query document, not the number
        # of business entities inside that immutable document.
        fields = ('query', 'region', 'language', 'conditions')
        if any(digest((live.get(key) or []) if key=='conditions' else live.get(key)) != digest(expected[key]) for key in fields):
            raise ProductError('middle_conditions_mismatch', '中台查询条件与本次地图请求不一致', 503)

    def status_for_request(self, middle_query_id, payload):
        from .middle_maps_contract import maps_request
        from .google_local_jobs import parameters
        expected = maps_request(parameters(payload))
        admitted=dict(expected,max_items=min(expected['max_items'],21))
        ident = _identifier(middle_query_id)
        data = self._request('GET', '/v1/live-queries/' + ident)
        result = self._live(data, expected_id=ident, expected_request=admitted)
        self.check_conditions(data['live_query'], expected)
        result['platform_max_items']=admitted['max_items']
        result['requested_max_items']=expected['max_items']
        return result



class SearchMiddlePlatformClient(MiddlePlatformClient):
    """One source per client/runner; never share mutable source identity."""
    def __init__(self, base_url, consumer_key, *, source='google_web', dataset_id='social_media_raw', session=None):
        if source not in SOURCE_PLATFORMS:
            raise ProductError('middle_source_not_ready', '中台尚不支持此搜索来源', 409)
        self.source = source
        self.source_platform = SOURCE_PLATFORMS[source]
        super().__init__(base_url, consumer_key, dataset_id=dataset_id, session=session)

    def create(self, account_id, payload, idempotency_key):
        if payload.get('source', 'google_web') != self.source:
            raise ProductError('middle_source_mismatch', '请求来源与中台运行器不一致', 409)
        request = search_request(payload, dataset_id=self.dataset_id)
        return self._live(self._request('POST', '/v1/live-queries', payload=request,
            idem=upstream_idempotency_key(account_id, idempotency_key)), expected_request=request)
