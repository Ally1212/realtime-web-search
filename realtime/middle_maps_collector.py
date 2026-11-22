"""Dedicated Maps Collector credentials and capability registration."""
import requests
from .google_whale_readback import WhaleReadbackClient
from .middle_maps_contract import CAPABILITIES
from .middle_ingest import task_conflict
from .product import ProductError


class MapsCollector:
    def __init__(self, base_url, collector_key, *, agent_id='', session=None):
        self.base_url = WhaleReadbackClient(base_url, collector_key).base_url
        self._key = collector_key
        self.agent_id = agent_id
        self.session = session or requests.Session()

    def post(self, path, payload, *, realtime=False):
        if not path.startswith('/v1/') or '..' in path or '?' in path:
            raise ProductError('invalid_parameter', '中台采集接口路径无效')
        headers = {'Authorization': 'Bearer ' + self._key, 'Accept': 'application/json'}
        if realtime:
            headers['X-Ingest-Class'] = 'realtime'
        try:
            response = self.session.post(self.base_url + path, json=payload, headers=headers,
                                         timeout=20, allow_redirects=False)
        except requests.RequestException:
            raise ProductError('middle_transport_unknown', '中台采集请求结果未知，保留原事件等待核验', 503) from None
        if response.status_code in (401, 403):
            raise ProductError('middle_entitlement_denied', '地图采集端未获授权', 503)
        if response.status_code == 429:
            error = ProductError('middle_rate_limited', '地图采集端请求限流', 429)
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone
            import math
            value = response.headers.get('Retry-After', '5')
            try:
                delay = float(value)
            except (TypeError, ValueError):
                try:
                    delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 5
            error.retry_after = max(1, delay) if math.isfinite(delay) else 5
            raise error
        if response.status_code not in (200, 201, 202):
            error = ProductError('middle_collector_failed', '地图采集端请求未成功', 503)
            if response.status_code == 409:
                raise task_conflict(response, error)
            error.status_code = response.status_code
            raise error
        try:
            data = response.json()
        except ValueError:
            raise ProductError('middle_invalid_response', '中台采集响应无效', 503) from None
        if not isinstance(data, (dict, list)):
            raise ProductError('middle_invalid_response', '中台采集响应无效', 503)
        return data

    def register(self):
        data = self.post('/v1/collectors/register', dict(name='SearchFlow Maps Collector',
            agent_type='google_maps', runtime='python', version='maps-query-1',
            supported_platforms=['google_maps'], supported_task_types=['live_query'],
            declared_capabilities=CAPABILITIES, max_concurrency=1, current_load=0,
            metadata=dict(mode='maps_query_snapshot', datasets=['google_maps_raw'], body=False)))
        if not isinstance(data, dict):
            raise ProductError('middle_invalid_response', '地图采集器注册响应无效', 503)
        ident = data.get('agent_id') or (data.get('agent') or {}).get('id') or (data.get('data') or {}).get('agent_id')
        if not isinstance(ident, str) or not ident or any(c in ident for c in '/?#'):
            raise ProductError('middle_invalid_response', '地图采集器注册缺少身份', 503)
        self.agent_id = ident

    def heartbeat(self, load):
        return self.post('/v1/collectors/heartbeat', dict(agent_id=self.agent_id, current_load=load))

    def upload_realtime(self, messages):
        data = self.post('/v1/documents/bulk', messages, realtime=True)
        if not isinstance(data, list) or len(data) != len(messages):
            raise ProductError('middle_invalid_response', '地图入库接收回执不完整', 503)
        # Bulk acceptance is not persistence. The phase runner polls receipts.
        return data
