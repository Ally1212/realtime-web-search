"""New-domain request contract. No network, SQL, or implicit provider approval.

This module is deliberately separate from legacy search parameters and quotas.
An admitted request is not a queued job or evidence of collection.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import UUID

from .product import ProductError

VERSION = 'searchflow-domain-jobs-1'
CAPABILITIES = ('trends_history', 'keyword_volume', 'aio_answer', 'ai_mode_answer')
COMMON = {'capability', 'country', 'language'}
FIELDS = {
    'trends_history': {'terms', 'start_date', 'end_date', 'category', 'search_type'},
    'keyword_volume': {'keywords', 'start_month', 'end_month', 'network'},
    'aio_answer': {'prompt', 'device'},
    'ai_mode_answer': {'prompt', 'device'},
}


def invalid(message):
    raise ProductError('invalid_parameter', message)


def text(value, name, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        invalid(f'{name} 必须为非空字符串，最多 {maximum} 字符')
    return value


def validate_request(payload):
    if not isinstance(payload, dict) or any(not isinstance(k, str) for k in payload):
        invalid('任务参数必须为对象')
    capability = payload.get('capability')
    if not isinstance(capability, str) or capability not in CAPABILITIES:
        invalid('未知 capability')
    allowed = COMMON | FIELDS[capability]
    if set(payload) != allowed:
        invalid('参数缺失或包含未支持字段：' + ','.join(sorted(set(payload) ^ allowed)))
    p = dict(payload)
    if not isinstance(p['country'], str) or not re.fullmatch('[A-Za-z]{2}', p['country']):
        invalid('country 必须为两位地区代码；提供方仍须验证支持范围')
    p['country'] = p['country'].upper()
    if not isinstance(p['language'], str) or not re.fullmatch('[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', p['language']):
        invalid('language 必须为语言标签')
    if capability in ('trends_history', 'keyword_volume'):
        key, maximum = ('terms', 5) if capability == 'trends_history' else ('keywords', 100)
        if not isinstance(p[key], list) or not 1 <= len(p[key]) <= maximum:
            invalid(f'{key} 数量必须为 1–{maximum}')
        p[key] = [text(v, key) for v in p[key]]
        # Preserve input order and duplicates for the per-input result mapping.
        start, end = ('start_date', 'end_date') if key == 'terms' else ('start_month', 'end_month')
        parsed = []
        for field in (start, end):
            value = p[field]
            pattern = r'\d{4}-\d{2}-\d{2}' if key == 'terms' else r'\d{4}-\d{2}'
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                invalid(field + ' 日期格式无效')
            try:
                parsed.append(date.fromisoformat(value if key == 'terms' else value + '-01'))
            except ValueError:
                invalid(field + ' 日期无效')
        if parsed[0] > parsed[1]:
            invalid('起始时间不得晚于结束时间')
    if capability == 'trends_history':
        if type(p['category']) is not int or p['category'] < 0:
            invalid('category 必须为非负整数')
        if p['search_type'] not in ('web', 'images', 'news', 'shopping', 'youtube'):
            invalid('search_type 不支持')
    elif capability == 'keyword_volume':
        if p['network'] not in ('google', 'google_and_partners'):
            invalid('network 不支持')
    else:
        p['prompt'] = text(p['prompt'], 'prompt', 10000)
        if p['device'] not in ('desktop', 'mobile'):
            invalid('device 不支持')
    return p


def request_digest(payload):
    """Identity of validated conditions, not of an observation or its values."""
    canonical = json.dumps(validate_request(payload), sort_keys=True, ensure_ascii=False,
                           separators=(',', ':'), allow_nan=False)
    return hashlib.sha256((VERSION + '\n' + canonical).encode()).hexdigest()


def admit_request(payload, grant, maximum_cost, currency, *, now=None):
    """Fail closed until a provider-specific executable admission is supplied.

    grant is trusted operator configuration, never customer request JSON. This
    check does not reserve money; callers must atomically reserve before enqueue.
    """
    p = validate_request(payload)
    if not grant or grant.get('capability') != p['capability'] or grant.get('status') != 'approved':
        raise ProductError('source_not_admitted', '此能力尚无已准入来源', 409)
    required = ('provider', 'license_id', 'allowed_use', 'retention_days', 'billing_unit',
                'currency', 'maximum_cost', 'expires_at', 'validate_parameters')
    if (any(grant.get(k) is None for k in required)
            or any(not isinstance(grant.get(k), str) or not grant[k].strip()
                   for k in ('provider', 'license_id', 'allowed_use', 'billing_unit', 'currency', 'expires_at'))
            or not callable(grant['validate_parameters'])):
        raise ProductError('source_not_admitted', '来源准入或费用配置不完整', 409)
    try:
        expiry = datetime.fromisoformat(grant['expires_at'].replace('Z', '+00:00'))
        current = now or datetime.now(timezone.utc)
        if expiry.tzinfo is None or expiry <= current:
            raise ValueError()
        proof_fields={'admission_evidence_ref','admission_verified_at'}
        if proof_fields.intersection(grant):
            if not proof_fields.issubset(grant):raise ValueError()
            reference=grant['admission_evidence_ref']
            if not isinstance(reference,str) or not reference.startswith('approval:') or str(UUID(reference[9:]))!=reference[9:]:raise ValueError()
            verified=datetime.fromisoformat(grant['admission_verified_at'].replace('Z','+00:00'))
            if verified.tzinfo is None or verified>current:raise ValueError()
        if 'source_retry_policy' in grant:
            from .data_capability_backoff import RetryPolicy
            RetryPolicy.parse(grant['source_retry_policy'])
            if not ((p['capability']=='keyword_volume' and grant['provider']=='google_ads') or (p['capability'] in ('trends_history','aio_answer','ai_mode_answer') and grant['provider']=='serpapi')):raise ValueError()
        estimate, ceiling = Decimal(str(grant['maximum_cost'])), Decimal(str(maximum_cost))
        if not estimate.is_finite() or not ceiling.is_finite() or estimate < 0 or ceiling < estimate:
            raise ValueError()
        if type(grant['retention_days']) is not int or grant['retention_days'] <= 0:
            raise ValueError()
    except (ValueError, TypeError, InvalidOperation, AttributeError):
        raise ProductError('admission_invalid', '准入已到期或保留期限/费用上限无效', 409) from None
    if currency != grant['currency']:
        raise ProductError('currency_mismatch', '任务预算币种与来源不符', 409)
    # The adapter must reject unsupported effective parameters, never ignore them.
    grant['validate_parameters'](dict(p))
    return dict(version=VERSION, parameters=p, parameters_hash=request_digest(p),
                provider=grant['provider'], license_id=grant['license_id'],
                maximum_cost=str(estimate), currency=currency, billing_unit=grant['billing_unit'])
