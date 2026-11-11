"""Private, lossless observation candidates and opt-in pinned Consumer readback.

These are LOCAL candidate contracts, never registered Whale ingest schemas.
No dataset/content type selection, upload, outbox, source call or table creation.
"""
from collections import Counter
import copy
import json
import re
import time
from uuid import UUID

import requests

from .data_capability_export import rows, reconstruct
from .data_capability_facts import validate_result,timestamp
from .data_capability_jobs import digest,required_text
from .google_domain import DomainError
from .google_whale_readback import WhaleReadbackClient
from .product import ProductError

VERSION='searchflow-capability-observation-envelope-1'
CONTRACT='searchflow-capability-platform-candidate-1'
MAX_BYTES=64*1024*1024
OBSERVATION_FIELDS={'observation_id','entity_id','capability','provider','license_id','parameters',
                    'revision','supersedes','payload_hash','raw_hash','hash_encoding','stored_at',
                    'expires_at','payload','platform_delivery','snapshot_hash'}


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _snapshot(observation):
    if not isinstance(observation,dict) or set(observation)!=OBSERVATION_FIELDS:raise ValueError('snapshot fields differ')
    if str(UUID(observation['observation_id']))!=observation['observation_id']:raise ValueError('observation identity differs')
    if observation['supersedes'] is not None and str(UUID(observation['supersedes']))!=observation['supersedes']:raise ValueError('revision identity differs')
    for field in ('entity_id','payload_hash','snapshot_hash'):
        if not isinstance(observation[field],str) or not re.fullmatch('[0-9a-f]{64}',observation[field]):raise ValueError('invalid digest')
    if observation['revision'] is not None:required_text(observation['revision'],'revision')
    required_text(observation['license_id'],'license_id')
    if (timestamp(observation['stored_at'])>=timestamp(observation['expires_at'])
            or observation['hash_encoding']!='sha256-canonical-json-utf8-not-source-bytes'
            or observation['platform_delivery']!='not_submitted'):raise ValueError('snapshot metadata differs')
    payload=validate_result(observation['parameters'],observation['payload'])
    if (observation['capability']!=observation['parameters']['capability']
            or payload['source']['provider']!=observation['provider']
            or digest(payload)!=observation['payload_hash']
            or digest({k:v for k,v in observation.items() if k!='snapshot_hash'})!=observation['snapshot_hash']
            or observation['raw_hash']!=(digest(payload['raw']['payload']) if payload['raw']['payload'] is not None else None)):
        raise ValueError('snapshot hashes or channel differ')
    # The same normalized projection oracle as the complete JSONL/CSV exporter.
    # Reconstructing checks that the parent hierarchy remains representable.
    records=list(rows(observation))
    if digest(reconstruct(records))!=digest(observation):raise ValueError('parent reconstruction differs')
    return dict(Counter(r['record_type'] for r in records))


def observation_candidate(tenant, observation, *, source_platform):
    """Pure builder. A tenant string here is NOT an ownership authorization."""
    try:
        required_text(tenant,'tenant_id',1024)
        if not isinstance(source_platform,str) or not re.fullmatch('[a-z][a-z0-9_.-]{0,99}',source_platform):raise ValueError()
        counts=_snapshot(observation)
        payload=dict(schema_version=VERSION,tenant_id=tenant,source_platform=source_platform,
                     observation=copy.deepcopy(observation))
        if len(encode(payload))>MAX_BYTES:raise ProductError('delivery_too_large','完整平台候选超过64MiB，未发布',413)
        identity=dict(tenant_id=tenant,source_platform=source_platform,observation_id=observation['observation_id'])
        return dict(contract=CONTRACT,admission_status='candidate_not_approved',
                    source_platform=source_platform,object_key='capability-observation:'+digest(identity),
                    event_key='capability-observation-event:'+digest(dict(identity,snapshot_hash=observation['snapshot_hash'])),
                    payload_hash='sha256:'+digest(payload),record_counts=counts,payload=payload)
    except ProductError:raise
    except Exception:raise ProductError('invalid_delivery_candidate','观测候选字段、身份或完整性无效',409) from None


def validate_candidate(candidate):
    try:
        payload=candidate['payload']
        if not isinstance(payload,dict) or set(payload)!={'schema_version','tenant_id','source_platform','observation'} or payload['schema_version']!=VERSION:raise ValueError()
        expected=observation_candidate(payload['tenant_id'],payload['observation'],source_platform=payload['source_platform'])
        if digest(candidate)!=digest(expected):raise ValueError()
        observation=payload['observation']
        return dict(tenant_id=payload['tenant_id'],capability=observation['capability'],
                    observation_id=observation['observation_id'],snapshot_hash=observation['snapshot_hash'],
                    payload_hash=observation['payload_hash'],record_counts=expected['record_counts'],
                    is_partial=observation['payload']['is_partial'],is_truncated=observation['payload']['is_truncated'])
    except Exception:raise ProductError('invalid_delivery_candidate','平台候选身份、数量或校验不一致',409) from None


def prepare_candidate(runtime, tenant, observation_id, *, source_platform):
    """Authenticated service boundary; unlike the pure builder, checks ownership."""
    observation=runtime.observation(str(tenant),observation_id)
    candidate=observation_candidate(str(tenant),observation,source_platform=source_platform)
    current=runtime.observation(str(tenant),observation_id)
    if current['snapshot_hash']!=observation['snapshot_hash']:
        raise ProductError('version_conflict','构建期间观测版本已改变，未交付',409)
    return candidate


class CapabilityReadbackClient(WhaleReadbackClient):
    """Existing document/version protocol with bounded streaming JSON bodies.

    Call only after explicit Consumer/dataset/source/field approval. No uploader
    or real call on construction. Exact canonical JSON is the comparison rule.
    """
    def _request(self, method, path, *, body=None):
        if self._last_request is not None and self._interval:
            wait=self._interval-(time.monotonic()-self._last_request)
            if wait>0:time.sleep(wait)
        self._last_request=time.monotonic();response=None
        limit=MAX_BYTES+65536 if path=='/v1/versions/batch-get' else 1024*1024
        cutoff=time.monotonic()+30
        try:
            response=self._session.request(method,self.base_url+path,
                headers={'Authorization':'Bearer '+self._key,'Accept':'application/json',
                         **({'Content-Type':'application/json'} if body is not None else {})},
                json=body,timeout=(5,15),allow_redirects=False,stream=True)
            if response.status_code in (401,403):raise DomainError('not_authorized','Consumer无该固定版本读取权限',403)
            if response.status_code==404:raise DomainError('not_covered','固定文档或版本不可读取',404)
            if response.status_code!=200:raise DomainError('source_error','固定版本读取失败',503)
            headers={str(k).lower():v for k,v in response.headers.items()}
            if 'content-length' in headers and int(headers['content-length'])>limit:raise ValueError()
            data=bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if time.monotonic()>cutoff or not isinstance(chunk,bytes):raise ValueError()
                if len(data)+len(chunk)>limit:raise ValueError()
                data.extend(chunk)
            if time.monotonic()>cutoff:raise ValueError()
            def unique(pairs):
                result={}
                for name,value in pairs:
                    if name in result:raise ValueError()
                    result[name]=value
                return result
            def invalid_constant(_):raise ValueError()
            value=json.loads(data.decode('utf-8'),object_pairs_hook=unique,parse_constant=invalid_constant)
            if not isinstance(value,dict):raise ValueError()
            return value
        except DomainError:raise
        except (requests.RequestException,ValueError,TypeError,UnicodeError,RecursionError):
            raise DomainError('source_error','固定版本响应无效、超时或超过资源上限',503) from None
        finally:
            if response is not None:
                try:response.close()
                except Exception:pass

    def read_observation(self, target, *, expected_candidate, tenant_id):
        summary=validate_candidate(expected_candidate)
        if summary['tenant_id']!=str(tenant_id) or target.source_platform!=expected_candidate['source_platform']:
            raise ProductError('not_found','平台候选不属于当前租户或来源',404)
        document,item=self._read_pair(target)
        proof=self._proof(target,document,item)
        payload=item.get('platform_payload')
        if not isinstance(payload,dict):raise DomainError('not_covered','固定版本未提供完整观测载荷',404)
        actual=dict(expected_candidate,payload=payload)
        try:actual_summary=validate_candidate(actual)
        except ProductError:raise DomainError('source_error','固定版本观测载荷与本地候选不符',503) from None
        if (actual_summary!=summary or digest(payload)!=digest(expected_candidate['payload'])
                or document.get('external_content_id')!=expected_candidate['object_key']
                or str(item['version'].get('raw_payload_hash') or '').removeprefix('sha256:')!=expected_candidate['payload_hash'][7:]):
            raise DomainError('source_error','固定版本身份、数量或载荷摘要不一致',503)
        proof.update(**summary,payload_binding='pinned_version',payload_roundtrip_verified=True,
                     candidate_contract_only=True,platform_schema_admitted=False,terminal_receipt_verified=False)
        return dict(proof=proof,payload=payload)


def read_authorized_observation(runtime, tenant, observation_id, client, target, *, source_platform):
    """Read-only entry; recheck current source rights/tombstones after readback."""
    candidate=prepare_candidate(runtime,tenant,observation_id,source_platform=source_platform)
    result=client.read_observation(target,expected_candidate=candidate,tenant_id=str(tenant))
    current=runtime.observation(str(tenant),observation_id)
    if current['snapshot_hash']!=candidate['payload']['observation']['snapshot_hash']:
        raise ProductError('version_conflict','读回期间本地固定观测已改变，未交付证明',409)
    result['proof']['current_local_rights_rechecked']=True
    return result
