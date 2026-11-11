"""Versioned text analysis: immutable inputs, exact evidence and deterministic counts."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import html
import io
import json
import math
import re
from urllib.parse import urlsplit

VERSION = 'text-insights-1'
SENTIMENTS = ('positive', 'negative', 'neutral', 'mixed', 'unknown')
LEVELS = {'voc': ('人群场景', '功能价值', '保障价值', '体验价值'),
          'search': ('主题议题', '品牌产品', '内容类型', '需求线索')}
STOP = set('the and for with this that from your have are was were will not but you our their they into has its can more about also what how who when where which www com http https'.split())


class InsightError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def dumps(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        raise InsightError('invalid_json', '记录包含非有限数字或不能保存的JSON值') from None


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def text(value, name, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise InsightError('invalid_parameter', f'{name} 不能为空，且最多 {maximum} 字符')
    return value


def fields(payload, allowed):
    if not isinstance(payload, dict) or set(payload)-set(allowed):
        raise InsightError('invalid_parameter', '包含未知字段或输入不是对象')


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InsightError('invalid_parameter', f'{name} 必须为 {low}–{high} 的整数')
    return value


def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        return value if parts.scheme in ('https', 'http') and parts.hostname and not parts.username and not parts.password else None
    except ValueError:
        return None


def prepare_records(rows, mode, thread_cap=50):
    if mode not in LEVELS or not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
        raise InsightError('invalid_dataset', '请选择分析模式，并提供 1–5000 条记录')
    integer(thread_cap, '每线程上限', 1, 500)
    output, seen, ids, threads = [], {}, {}, Counter()
    excluded = Counter()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InsightError('invalid_dataset', '每条记录必须是对象')
        raw = row.get('text_raw')
        if not isinstance(raw, str) or not raw.strip():
            excluded['empty_text'] += 1
            continue
        if len(raw) > 20000:
            raise InsightError('text_too_long', '单条文本超过 20000 字符，请拆分并保留父记录关联')
        if raw.strip() in ('[deleted]', '[removed]'):
            excluded['deleted_removed'] += 1
            continue
        source = text(row.get('source', 'import'), 'source', 100)
        native = row.get('native_id')
        if native is not None:
            text(native, 'native_id', 300)
        source_id = row.get('source_id') or digest([source, native or raw])
        text(source_id, 'source_id', 300)
        if source_id in ids:
            if ids[source_id] != digest(row):
                raise InsightError('conflicting_source_id', '同一 source_id 对应不同输入，请保留独立版本ID')
            excluded['duplicate_source_id'] += 1
            continue
        ids[source_id] = digest(row)
        # Never collapse two distinct native comments merely because they say "good".
        identity = (source, native) if native else (source, raw)
        duplicate = seen.get(identity)
        if duplicate:
            excluded['duplicate_identity'] += 1
        else:
            seen[identity] = source_id
        thread = row.get('parent_id')
        if thread is not None:
            text(thread, 'parent_id', 300)
        thread_key = (source, thread)
        capped = bool(thread and not duplicate and threads[thread_key] >= thread_cap)
        if capped:
            excluded['thread_cap'] += 1
        if thread and not duplicate:
            threads[thread_key] += 1
        rating = row.get('rating')
        if rating is not None and (type(rating) not in (int, float) or not math.isfinite(rating) or not 1 <= rating <= 5):
            raise InsightError('invalid_rating', '星级必须为 1–5 或 null')
        def optional(key, maximum=300):
            v = row.get(key)
            return text(v, key, maximum) if v not in (None, '') else None
        output.append(dict(source_id=source_id, source=source, native_id=native,
            text_raw=raw, text_hash=digest(raw), url=safe_url(row.get('url')),
            request_id=optional('request_id'), occurrence_id=optional('occurrence_id'),
            parent_id=thread, brand=optional('brand', 100), market=optional('market', 100),
            language=optional('language', 50), published_at=optional('published_at', 100),
            rating=rating, content_kind=optional('content_kind', 100) or ('review' if mode == 'voc' else 'search_snippet'),
            included=not duplicate and not capped, duplicate_of=duplicate,
            exclusion='duplicate_identity' if duplicate else 'thread_cap' if capped else None, source_fields=row))
    if not output or not any(x['included'] for x in output):
        raise InsightError('empty_dataset', '没有可分析的有效文本')
    if len(dumps(output).encode()) > 8_000_000:
        raise InsightError('dataset_too_large', '数据集超过 8 MB，请分批导入')
    return output, dict(input_rows=len(rows), saved_rows=len(output),
        eligible_rows=sum(x['included'] for x in output), excluded=dict(excluded),
        thread_cap=thread_cap, cap_selection='first_observed_in_frozen_input_order',
        largest_thread=max(threads.values(), default=0), independent_threads=len(threads))


def taxonomy(value, mode):
    if not isinstance(value, list) or not 1 <= len(value) <= 60:
        raise InsightError('invalid_taxonomy', '标签数量需为 1–60')
    result, seen = [], set()
    for row in value:
        fields(row, ('id', 'level1', 'level2', 'name', 'description', 'keywords'))
        tid = text(row.get('id'), '标签 ID', 50)
        if tid in seen or not re.fullmatch(r'[A-Za-z0-9_-]+', tid):
            raise InsightError('invalid_taxonomy', '标签 ID 必须唯一，仅支持字母、数字、下划线和连字符')
        seen.add(tid)
        if row.get('level1') not in LEVELS[mode]:
            raise InsightError('invalid_taxonomy', '一级标签不属于当前分析模式')
        keywords = row.get('keywords', [])
        if not isinstance(keywords, list) or len(keywords) > 30:
            raise InsightError('invalid_taxonomy', '每个标签最多 30 个关键词')
        result.append(dict(id=tid, level1=row['level1'], level2=text(row.get('level2'), '二级标签', 30),
            name=text(row.get('name'), '三级标签', 30), description=text(row.get('description'), '判定说明', 400),
            keywords=[text(x, '关键词', 80) for x in keywords]))
    return result


def draft_taxonomy(records, mode):
    counts = Counter()
    for r in records:
        if not r['included']:
            continue
        counts.update(set(w.casefold() for w in re.findall(r'[A-Za-z][A-Za-z-]{3,25}', r['text_raw']) if w.casefold() not in STOP))
    terms = [k for k, _ in counts.most_common(12)]
    if not terms:
        terms = ['使用', '效果', '安全', '体验'] if mode == 'voc' else ['产品', '技术', '市场', '政策']
    return [dict(id=f't{i+1}', level1=LEVELS[mode][i % 4], level2='待校准主题', name=word,
                 description=f'原文明确出现“{word}”；仅作候选主题，需人工校准归属与判定。', keywords=[word])
            for i, word in enumerate(terms)]


def validate_annotations(values, record, tags, *, method='manual'):
    if not isinstance(values, list) or len(values) > 60:
        raise InsightError('invalid_annotation', '标注必须为数组，最多 60 项')
    known, seen, result = {x['id'] for x in tags}, set(), []
    for a in values:
        fields(a, ('tag_id', 'sentiment', 'quote', 'start'))
        tag = a.get('tag_id')
        quote = text(a.get('quote'), '证据原文', 2000)
        sentiment = a.get('sentiment', 'unknown')
        if tag not in known or sentiment not in SENTIMENTS or tag in seen:
            raise InsightError('invalid_annotation', '标签重复、不存在或情感值无效')
        seen.add(tag)
        start = a.get('start', record['text_raw'].find(quote))
        if type(start) is not int or start < 0 or record['text_raw'][start:start+len(quote)] != quote:
            raise InsightError('invalid_evidence', '证据必须逐字出现在该记录原文中')
        result.append(dict(tag_id=tag, sentiment=sentiment, quote=quote, start=start,
                           end=start+len(quote), method=method))
    return result


def rule_annotations(record, tags):
    values = []
    for tag in tags:
        for word in tag['keywords']:
            pattern = (r'(?<!\w)' + re.escape(word) + r'(?!\w)') if word.isascii() and re.search(r'\w', word) else re.escape(word)
            match = re.search(pattern, record['text_raw'], flags=re.IGNORECASE)
            if match:
                values.append(dict(tag_id=tag['id'], sentiment='unknown', quote=match.group(), start=match.start()))
                break
    return validate_annotations(values, record, tags, method='rule')


def observed_summary(records):
    """Only count stored fields; legacy snapshots have explicit unknowns."""
    domains,queries=Counter(),Counter()
    collected=[];rows=[];with_snippet=0
    for r in records:
        fields=r.get('source_fields') or {}
        title=fields.get('title');snippet=fields.get('snippet')
        query=fields.get('query');at=fields.get('collected_at')
        if isinstance(query,str) and query:queries[query]+=1
        if isinstance(at,str) and at:collected.append(at)
        with_snippet+=bool(snippet)
        u=urlsplit(r.get('url') or '')
        host=(u.hostname or '').lower().removeprefix('www.')
        native=fields.get('source_record') or {}
        if host and host not in ('news.google.com','trends.google.com') and native.get('resolution_status')!='unresolved_wrapper':
            domains[host]+=1
        rows.append(dict(source_id=r['source_id'],title=title,snippet=snippet,query=query,
                         collected_at=at,url=r.get('url'),source=r['source']))
    return dict(unique_domains=len(domains),with_snippet=with_snippet if any('snippet' in (r.get('source_fields') or {}) for r in records) else None,
                domains=[dict(name=k,count=v) for k,v in sorted(domains.items(),key=lambda x:(-x[1],x[0]))],
                queries=[dict(name=k,count=v) for k,v in sorted(queries.items(),key=lambda x:(-x[1],x[0]))],
                first_collected=min(collected) if collected else None,last_collected=max(collected) if collected else None,
                records=rows[:20],preview_limit=20,available=any(x['title'] is not None for x in rows))


def report(records, tags, annotations, reviews):
    eligible = [r for r in records if r['included']]
    counts, cross, low = Counter(), Counter(), Counter()
    ids, tag_info = defaultdict(list), {t['id']: t for t in tags}
    annotated, reviewed = 0, 0
    for r in eligible:
        sid = r['source_id']
        values = annotations.get(sid, [])
        annotated += bool(values)
        reviewed += sid in reviews
        for a in values:
            counts[a['tag_id'], a['sentiment']] += 1
            cross[r.get('brand') or '未知', a['tag_id'], a['sentiment']] += 1
            ids[a['tag_id']].append(sid)
            if r['rating'] is not None and r['rating'] <= 2 and a['sentiment'] == 'negative':
                low[a['tag_id']] += 1
    n = len(eligible)
    topic = []
    for t in tags:
        total = sum(counts[t['id'], s] for s in SENTIMENTS)
        topic.append(dict(**t, count=total, n=n, rate=total/n if n else None,
            sentiments={s: counts[t['id'], s] for s in SENTIMENTS},
            source_ids=ids[t['id']]))
    topic.sort(key=lambda x: (-x['count'], x['id']))
    brand_n = Counter(r['brand'] or '未知' for r in eligible)
    low_n = sum(r['rating'] is not None and r['rating'] <= 2 for r in eligible)
    return dict(version=VERSION, n=n, observed=observed_summary(eligible), annotated_records=annotated, reviewed_records=reviewed,
        unreviewed_records=n-reviewed, unlabeled_records=n-annotated,
        sources=dict(Counter(r['source'] for r in eligible)),
        markets=dict(Counter(r['market'] or '未知' for r in eligible)),
        topics=topic, brand_cross=[dict(brand=b, tag_id=t, name=tag_info[t]['name'], level2=tag_info[t]['level2'],
            sentiment=s, count=c, n=brand_n[b], rate=c/brand_n[b]) for (b,t,s),c in sorted(cross.items())],
        low_star=dict(n=low_n, maximum_rating=2, negative_mentions=[dict(tag_id=t,name=tag_info[t]['name'],count=c,n=low_n) for t,c in low.most_common()]),
        findings=[dict(metric_id='tag:'+t['id'], text=f'“{t["name"]}”在当前有效样本中提及 {t["count"]}/{n} 条。',
                       source_ids=t['source_ids']) for t in topic[:5] if t['count']],
        methodology='统计单位为有效去重记录；同一记录每标签最多一次。多标签比例之和可超过100%。情感为方面情感；未知不补中性。低星定义为1–2星，仅统计已标负面方面。',
        limitations=['便利样本不代表全市场；线程封顶仍不构成随机抽样。',
                    '搜索标题、摘要与AI回答不等于真实用户体验。',
                    '规则仅做字面命中，不推断情感；AI标注在人工复核前为候选。'])


def csv_bytes(rows, columns):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(columns)
    for r in rows:
        values = [dumps(r.get(k)) if isinstance(r.get(k), (dict,list)) else r.get(k) for k in columns]
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values])
    return ('\ufeff'+out.getvalue()).encode()


def html_report(bundle):
    e = lambda x: html.escape(str(x), quote=True)
    r = bundle['report']
    rows = ''.join(f'<tr><td>{e(t["name"])}</td><td>{t["count"]}/{r["n"]}</td><td>{t["rate"]:.1%}</td></tr>' for t in r['topics'])
    evidence = ''.join(f'<details><summary>{e(x["source_id"])}</summary><pre>{e(x["text_raw"])}</pre></details>' for x in bundle['records'])
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>文本洞察报告</title><style>body{max-width:960px;margin:40px auto;padding:20px;font:16px/1.7 system-ui;color:#182332}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}</style>'
            f'<h1>{e(bundle["dataset"]["name"])}</h1><p>固定报告版本 {e(bundle["report_hash"])}</p><p>{e(r["methodology"])}</p>'
            f'<p>有效样本 {r["n"]}，已复核 {r["reviewed_records"]}，未打标 {r["unlabeled_records"]}。</p>'
            '<ul>'+''.join('<li>'+e(x)+'</li>' for x in r['limitations'])+'</ul>'
            f'<table><thead><tr><th>标签</th><th>记录数 / n</th><th>占比</th></tr></thead><tbody>{rows}</tbody></table><h2>原文证据</h2>{evidence}</html>').encode()
