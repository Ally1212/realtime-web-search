"""Source-neutral fact validation for the four new data capabilities.

These contracts validate assertions made by a separately approved source
adapter; they do not establish source truth or replace real response oracles.
No interpolation, inferred citation mapping, or fabricated missing numeric zero.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .data_capability_contract import validate_request
from .google_domain import DomainError, parse_time
from .google_observations import _safe_config
from .product import ProductError

VERSION = 'searchflow-facts-1'
MAX_BYTES = 64 * 1024 * 1024
VALUE_STATES = ('available', 'true_zero', 'unknown', 'not_observed', 'not_covered',
                'outside_observed_depth', 'source_error', 'expired', 'below_threshold')


def invalid(message):
    raise ProductError('invalid_fact', message)


def obj(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        invalid('事实字段缺失或包含未知字段')
    if 'source_fields' in value and 'source_fields' in set(required) | set(optional) and not isinstance(value['source_fields'], dict):
        invalid('source_fields必须为对象')
    return value


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        invalid(name + ' 必须为非空文本')
    return value


def array(value, name, maximum=1000000):
    if not isinstance(value, list) or len(value) > maximum:
        invalid(name + ' 必须为有界数组')
    return value


def boolean(value, name):
    if type(value) is not bool:
        invalid(name + ' 必须为布尔值')


def timestamp(value):
    try:
        return parse_time(value)
    except DomainError:
        invalid('时间必须包含有效时区')


def window(value):
    obj(value, ('start', 'end'))
    start, end = timestamp(value['start']), timestamp(value['end'])
    if start >= end:
        invalid('时间区间必须为非空的左闭右开区间')
    return start, end


def number(value):
    # JSON decimals are strings, never IEEE-754 rounded source quantities.
    if not isinstance(value, str) or len(value) > 100:
        invalid('数值必须为有限十进制字符串')
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        invalid('数值必须为有限非负值')


def metric(value):
    obj(value, ('value', 'status'), ('reason', 'range_min', 'range_max', 'rounded', 'censored', 'source_fields'))
    if value['status'] not in VALUE_STATES:
        invalid('未知字段可用性状态')
    if value['status'] in ('available', 'true_zero'):
        n = number(value['value'])
        if value['status'] == 'true_zero' and n != 0:
            invalid('true_zero 与数值必须一致')
    else:
        if value['value'] is not None:
            invalid('未知、低量或失败不得补数值')
        nonempty(value.get('reason'), 'reason')
    for field in ('rounded', 'censored'):
        if field in value:
            boolean(value[field], field)
    if ('range_min' in value) != ('range_max' in value):
        invalid('范围上下界必须同时给出')
    if 'range_min' in value:
        low, high = number(value['range_min']), number(value['range_max'])
        if low > high or value['value'] is not None and not low <= number(value['value']) <= high:
            invalid('范围值不一致')


def unique(rows, name='id'):
    ids = [nonempty(row.get(name), name) for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids) or any(len(v) > 1024 for v in ids):
        invalid('记录身份必须非空且在本结果内唯一')
    return set(ids)


def mapping(rows, inputs, ids):
    array(rows, 'input_mapping', 100)
    indexes = []
    for row in rows:
        obj(row, ('input_index', 'record_id', 'status', 'reason'))
        index = row['input_index']
        if type(index) is not int or not 0 <= index < len(inputs):
            invalid('输入映射越界')
        indexes.append(index)
        if row['record_id'] is not None:
            if not isinstance(row['record_id'], str) or row['record_id'] not in ids or row['status'] != 'available' or row['reason'] is not None:
                invalid('输入映射与返回记录不一致')
        elif row['status'] not in VALUE_STATES or row['status'] in ('available', 'true_zero'):
            invalid('未返回输入必须有无数据状态')
        else:
            nonempty(row['reason'], '无数据原因')
    if sorted(indexes) != list(range(len(inputs))):
        invalid('每个输入必须且只能映射一次，重复输入也须保留')
    if {row['record_id'] for row in rows if row['record_id'] is not None} != ids:
        invalid('返回记录缺少输入映射')


def trends(data, parameters):
    obj(data, ('series', 'input_mapping'))
    rows = array(data['series'], 'series', 5)
    ids = unique(rows)
    mapping(data['input_mapping'], parameters['terms'], ids)
    for series in rows:
        obj(series, ('id', 'term_or_topic', 'resolved_subject', 'effective_window', 'granularity',
                     'source_timezone', 'scale_id', 'measurement_type', 'normalization',
                     'comparison_group', 'revision', 'points', 'source_fields'),
            ('regional_interest', 'related_top', 'related_rising'))
        for name in ('term_or_topic', 'granularity', 'scale_id', 'normalization', 'revision'):
            nonempty(series[name], name)
        if series['comparison_group'] is not None:
            nonempty(series['comparison_group'], 'comparison_group')
        try:
            ZoneInfo(series['source_timezone'])
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            invalid('source_timezone 必须为 IANA 时区')
        if series['measurement_type'] not in ('relative_index', 'same_scale_index', 'estimated_absolute'):
            invalid('热度测量性质无效')
        start, end = window(series['effective_window'])
        requested_start = date.fromisoformat(parameters['start_date'])
        requested_end = date.fromisoformat(parameters['end_date'])
        # Compare source-local calendar boundaries, not naive UTC dates.
        zone = ZoneInfo(series['source_timezone'])
        from datetime import timedelta
        if start.astimezone(zone).date() < requested_start or (end.astimezone(zone) - timedelta(microseconds=1)).date() > requested_end:
            invalid('实际时间窗口超出批准的请求窗口')
        points = array(series['points'], 'points')
        unique(points)
        previous = None
        for point in points:
            obj(point, ('id', 'period_start', 'period_end', 'metric', 'partial', 'revision', 'source_ref', 'source_fields'))
            a, b = window({'start': point['period_start'], 'end': point['period_end']})
            if a < start or b > end or previous is not None and a < previous:
                invalid('时间点重叠、乱序或超出实际窗口')
            previous = b
            metric(point['metric'])
            if series['measurement_type'] != 'estimated_absolute' and point['metric']['value'] is not None and number(point['metric']['value']) > 100:
                invalid('相对指数超出0–100')
            boolean(point['partial'], 'partial')
            nonempty(point['revision'], 'revision')
            nonempty(point['source_ref'], 'source_ref')
        # Optional extensions remain independent source datasets; unsupported
        # sets must explicitly declare not_covered rather than inventing rows.
        for name in ('regional_interest', 'related_top', 'related_rising'):
            if name in series:
                obj(series[name], ('status', 'rows', 'reason'))
                array(series[name]['rows'], name)
                if series[name]['status'] == 'available':
                    if series[name]['reason'] is not None:
                        invalid('可用扩展集不得带缺失原因')
                elif series[name]['status'] not in VALUE_STATES or series[name]['rows']:
                    invalid('不可用扩展集不得携带数据')
                else:
                    nonempty(series[name]['reason'], '扩展集缺失原因')


def month(value):
    if not isinstance(value, str) or len(value) != 7:
        invalid('月份必须为YYYY-MM')
    try:
        parsed = date.fromisoformat(value + '-01')
        if parsed.strftime('%Y-%m') != value:
            raise ValueError()
        return parsed
    except ValueError:
        invalid('月份无效')


def volumes(data, parameters):
    obj(data, ('groups', 'input_mapping'))
    groups = array(data['groups'], 'groups', 100)
    ids = unique(groups)
    mapping(data['input_mapping'], parameters['keywords'], ids)
    for group in groups:
        obj(group, ('id', 'returned_text', 'close_variant_group', 'measurement_type', 'unit',
                    'statistic_window', 'average', 'monthly', 'commercial_fields', 'source_fields'))
        nonempty(group['returned_text'], 'returned_text')
        if group['close_variant_group'] is not None:
            nonempty(group['close_variant_group'], 'close_variant_group')
        if group['measurement_type'] not in ('source_reported', 'estimated', 'rounded', 'range', 'thresholded', 'unknown'):
            invalid('搜索量测量性质无效')
        if group['unit'] != 'searches':
            invalid('搜索量单位必须为searches，不能使用相对指数')
        statistic = group['statistic_window']
        obj(statistic, ('start_month', 'end_month'), ('reason',))
        if statistic['start_month'] is None or statistic['end_month'] is None:
            if statistic['start_month'] is not None or statistic['end_month'] is not None:
                invalid('未知均值窗口须两个边界均为空')
            nonempty(statistic.get('reason'), '未知均值窗口原因')
        else:
            if 'reason' in statistic:
                invalid('已知均值窗口不得带未知原因')
            if month(statistic['start_month']) > month(statistic['end_month']):
                invalid('均值统计窗口倒置')
        metric(group['average'])
        seen = set()
        for point in array(group['monthly'], 'monthly', 1200):
            obj(point, ('year', 'month', 'metric', 'source_fields'))
            if type(point['year']) is not int or type(point['month']) is not int:
                invalid('year/month必须为整数')
            m = month(f"{point['year']:04d}-{point['month']:02d}")
            if m in seen or not month(parameters['start_month']) <= m <= month(parameters['end_month']):
                invalid('月份重复或超出请求范围')
            seen.add(m)
            metric(point['metric'])
        commercial = group['commercial_fields']
        if not isinstance(commercial, dict):
            invalid('commercial_fields必须为对象')
        # Monetary values require explicit currency and micros/unit, not a
        # display-time guess. Competition and SEO difficulty stay distinct.
        for field, value in commercial.items():
            if field not in ('competition', 'competition_index', 'cpc', 'bid_low', 'bid_high'):
                invalid('商业字段未在当前合同中开放')
            if field in ('cpc', 'bid_low', 'bid_high'):
                obj(value, ('metric', 'currency', 'unit'))
                import re
                if not isinstance(value['currency'], str) or not re.fullmatch('[A-Z]{3}', value['currency']) or value['unit'] not in ('currency', 'micros'):
                    invalid('商业金额必须明确币种及单位')
                metric(value['metric'])
            elif field == 'competition_index':
                metric(value)
                if value['value'] is not None and number(value['value']) > 100:
                    invalid('competition_index超出0–100')
            else:
                nonempty(value, 'competition')


def http_url(value):
    nonempty(value, 'url')
    try:
        u = urlsplit(value)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
    except ValueError:
        invalid('URL必须为不含凭据的HTTP(S)链接')


def answer(data, parameters):
    obj(data, ('answer',))
    a = data['answer']
    obj(a, ('id', 'channel', 'state', 'answer_text', 'blocks', 'citations', 'links', 'model', 'expansion', 'source_fields'))
    nonempty(a['id'], 'answer_id')
    if len(a['id']) > 1024:
        invalid('answer_id超过1024字符')
    if a['channel'] != ('aio' if parameters['capability'] == 'aio_answer' else 'ai_mode'):
        invalid('AIO与AI Mode不得互相替代')
    if a['state'] not in ('complete', 'partial', 'not_observed', 'refused', 'expired', 'expansion_failed', 'source_error'):
        invalid('未知回答状态')
    blocks, citations, links = (array(a[k], k) for k in ('blocks', 'citations', 'links'))
    block_ids = unique(blocks)
    unique(citations)
    obj(a['expansion'], ('state', 'end_reason'))
    if a['expansion']['state'] not in ('not_required', 'complete', 'partial', 'expired', 'failed'):
        invalid('未知展开状态')
    nonempty(a['expansion']['end_reason'], '展开结束依据')
    if a['model'] is not None:
        nonempty(a['model'], 'model')
    if a['state'] in ('complete', 'partial'):
        nonempty(a['answer_text'], 'answer_text')
    elif a['state'] == 'refused':
        if a['answer_text'] is not None:
            nonempty(a['answer_text'], 'refusal text')
    elif a['answer_text'] is not None or blocks or citations:
        invalid('没有取得回答时不得编造全文、块或引用')
    if a['state'] == 'complete' and a['expansion']['state'] not in ('not_required', 'complete'):
        invalid('未完成展开不得宣称完整回答')
    if ((a['state'] in ('not_observed', 'refused') and a['expansion']['state'] not in ('not_required', 'complete'))
            or a['state'] == 'expired' and a['expansion']['state'] != 'expired'
            or a['state'] == 'expansion_failed' and a['expansion']['state'] != 'failed'):
        invalid('回答状态必须与未出现、拒答或展开失败的实际状态一致')
    last = -1
    for block in blocks:
        obj(block, ('id', 'position', 'type', 'text', 'structure', 'source_fields'))
        if type(block['position']) is not int or block['position'] <= last:
            invalid('回答块必须保持源顺序，不得重复位置')
        last = block['position']
        nonempty(block['type'], 'block type')
        if not isinstance(block['text'], str) or not isinstance(block['structure'], dict):
            invalid('回答块须保留文本及结构')
    for citation in citations:
        obj(citation, ('id', 'url', 'title', 'role', 'source_ref', 'mapping_status', 'block_ids', 'text_ranges', 'source_fields'))
        http_url(citation['url'])
        if citation['role'] != 'citation':
            invalid('普通链接不得冒充明确引用')
        nonempty(citation['source_ref'], 'citation source_ref')
        if citation['title'] is not None:
            nonempty(citation['title'], 'citation title')
        refs = array(citation['block_ids'], 'citation blocks')
        ranges = array(citation['text_ranges'], 'citation ranges')
        if any(not isinstance(ref, str) or ref not in block_ids for ref in refs) or len(set(refs)) != len(refs):
            invalid('引用指向未知或重复的回答块')
        if citation['mapping_status'] not in ('source_explicit', 'not_covered'):
            invalid('引用映射必须有源依据或明确未覆盖')
        if citation['mapping_status'] == 'not_covered' and (refs or ranges):
            invalid('源未提供映射时不得猜测引用关系')
        for span in ranges:
            obj(span, ('start', 'end'))
            if type(span['start']) is not int or type(span['end']) is not int or not 0 <= span['start'] < span['end'] <= len(a['answer_text'] or ''):
                invalid('引用文本范围越界；偏移以Unicode码点计')
    for link in links:
        obj(link, ('url', 'title', 'source_fields'))
        http_url(link['url'])
        if link['title'] is not None:
            nonempty(link['title'], 'link title')


def validate_result(parameters, value):
    """Return a detached, secret-free envelope; never silently drop fields."""
    p = validate_request(parameters)
    try:
        pending = [(value, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 64:
                invalid('事实JSON嵌套超过64层')
            if isinstance(node, dict):
                if any(not isinstance(k, str) or '\x00' in k for k in node):
                    invalid('JSON对象键必须为不含NUL的文本')
                pending.extend((v, depth + 1) for v in node.values())
            elif isinstance(node, list):
                pending.extend((v, depth + 1) for v in node)
            elif isinstance(node, str):
                if '\x00' in node:
                    invalid('事实文本不能包含NUL')
            elif node is not None and type(node) not in (int, float, bool):
                invalid('事实必须为原生JSON类型')
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > MAX_BYTES:
            encoded = None
        value = json.loads(encoded) if encoded is not None else None
        _safe_config(value)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        invalid('事实必须为有限、无凭据或短效token的JSON')
    if encoded is None:
        raise ProductError('response_too_large', '标准事实与许可内原响应超过64MiB', 413)
    obj(value, ('schema_version', 'source', 'requested_at', 'received_at', 'effective_parameters',
                'is_partial', 'is_truncated', 'termination_reason', 'warnings', 'raw', 'data'))
    if (value['schema_version'] != VERSION
            or json.dumps(value['effective_parameters'], sort_keys=True, ensure_ascii=False) != json.dumps(p, sort_keys=True, ensure_ascii=False)):
        invalid('schema版本或实际生效参数不符；当前不支持静默降级')
    source = obj(value['source'], ('provider', 'api_version', 'parser_version', 'source_request_id',
                                   'source_observed_at', 'unknown_time_reason'))
    for name in ('provider', 'api_version', 'parser_version'):
        nonempty(source[name], name)
    if source['source_request_id'] is not None:
        nonempty(source['source_request_id'], 'source_request_id')
    if source['source_observed_at'] is None:
        nonempty(source['unknown_time_reason'], 'unknown_time_reason')
    elif source['unknown_time_reason'] is not None:
        invalid('已知源时间不应同时标记未知原因')
    else:
        timestamp(source['source_observed_at'])
    if timestamp(value['requested_at']) > timestamp(value['received_at']):
        invalid('接收时间不得早于请求时间')
    for name in ('is_partial', 'is_truncated'):
        boolean(value[name], name)
    nonempty(value['termination_reason'], 'termination_reason')
    for warning in array(value['warnings'], 'warnings', 100):
        nonempty(warning, 'warning')
    raw = obj(value['raw'], ('status', 'payload', 'redacted_paths', 'reason'))
    array(raw['redacted_paths'], 'redacted_paths')
    for path in raw['redacted_paths']:
        nonempty(path, 'redacted path')
    if raw['status'] == 'stored':
        if raw['payload'] is None or raw['redacted_paths'] or raw['reason'] is not None:
            invalid('原响应保存状态不一致')
    elif raw['status'] == 'redacted':
        if raw['payload'] is None or not raw['redacted_paths']:
            invalid('脱敏响应须标明删除字段路径')
        nonempty(raw['reason'], '脱敏原因')
    elif raw['status'] == 'not_retained':
        if raw['payload'] is not None or raw['redacted_paths']:
            invalid('未保存原响应不能携带原响应数据')
        nonempty(raw['reason'], '未保留原因')
    else:
        invalid('未知原响应保留状态')
    if p['capability'] == 'trends_history':
        trends(value['data'], p)
        partial = any(point['partial'] or point['metric']['status'] == 'source_error'
                      for s in value['data']['series'] for point in s['points'])
    elif p['capability'] == 'keyword_volume':
        volumes(value['data'], p)
        partial = any(group['average']['status'] == 'source_error' or
                      any(point['metric']['status'] == 'source_error' for point in group['monthly'])
                      for group in value['data']['groups'])
    else:
        answer(value['data'], p)
        partial = value['data']['answer']['state'] in ('partial', 'expired', 'expansion_failed', 'source_error')
        if value['data']['answer']['state'] == 'complete' and (value['is_partial'] or value['is_truncated']):
            invalid('截断或部分响应不得宣称完整回答')
    if p['capability'] in ('trends_history', 'keyword_volume'):
        partial = partial or any(m['status'] == 'source_error' for m in value['data']['input_mapping'])
    if (partial or value['is_truncated']) and not value['is_partial']:
        invalid('部分周期、展开失败或截断必须标记is_partial')
    return value
