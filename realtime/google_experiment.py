"""Bounded Google-only experiments with separate storage, runtime and Whale outbox."""
from __future__ import annotations

import concurrent.futures
import fcntl
import json
import os
import re
import shutil
import sqlite3
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, SoupStrainer

from .campaign_store import CampaignStore
from .config import Config
from .discovery import GoogleBlocked, SearchDiscovery, SearchResult
from .experiment_audit import write_audit_snapshot
from .experiment_store import ExperimentStore, digest
from .fetcher import LiveFetcher, MAX_TEXT_CHARS, normalize_url
from .keyword_catalog import base_keyword_specs, has_economy_context
from .locales import (
    LOCALES, SearchLocale, default_locales_for_languages, locale_for_language,
    parse_locales, runner_locales,
)
from .markdown_export import literal, quality_warnings
from .proxy_pool import ProxyPool, ProxySynchronizer
from .whale_collector import WhaleClient, whale_message

FAMILIES = ('topic', 'event', 'site', 'recent')
EXCLUDE = (
    ' -site:youtube.com -site:youtu.be -site:linkedin.com -site:facebook.com'
    ' -site:reddit.com -site:x.com -site:instagram.com -site:tiktok.com'
    ' -inurl:scholar.google'
)
FINAL_STATES = {'complete', 'storage_stopped', 'stopped'}
SUPPORTED_LANGUAGES = ('zh', 'en')
BODY_WORKERS = 12
SEARCH_BACKLOG_LIMIT = 300


def archive_audit(store: ExperimentStore) -> Path | None:
    """Persist a small strict audit outside the replaceable experiment ledger."""
    try:
        target = write_audit_snapshot(store.path)
        store.set('audit_archive_path', str(target))
        return target
    except Exception as exc:
        try:
            store.set('audit_archive_error', type(exc).__name__)
        except Exception:
            pass
        return None


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def normalize_languages(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return SUPPORTED_LANGUAGES
    raw = value.split(',') if isinstance(value, str) else value
    languages = tuple(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    if not languages or any(language not in SUPPORTED_LANGUAGES for language in languages):
        raise ValueError('languages must contain zh and/or en')
    return languages


def validate_locale_matrix(
    locales: tuple[SearchLocale, ...], languages: tuple[str, ...], providers: list[str] | tuple[str, ...],
) -> None:
    """Keep a locale experiment attributable to one free SERP provider."""
    if len(providers) != 1 or providers[0] not in {"wml", "wml_direct"}:
        raise ValueError("--locale-matrix requires exactly one --google-providers value: wml or wml_direct")
    locale_languages = {locale.language for locale in locales}
    if locale_languages != set(languages):
        raise ValueError("locale languages must exactly match --languages")
    unknown = {locale.label for locale in locales} - set(LOCALES)
    if unknown:
        raise ValueError(f"unknown locales: {','.join(sorted(unknown))}")


def resolve_experiment_locales(
    languages: tuple[str, ...], *, locale_matrix: bool, locales: str | list[str] | None,
    providers: list[str] | tuple[str, ...] = ('wml',),
) -> tuple[SearchLocale, ...]:
    """Resolve explicit labels, or the documented language-aware defaults."""
    if not locale_matrix:
        return tuple(locale_for_language(language) for language in languages)
    value = str(locales or '').strip()
    selected = (
        default_locales_for_languages(languages)
        if value.lower() == 'auto' else parse_locales(value)
    )
    validate_locale_matrix(selected, languages, providers)
    return selected


def quality(document: dict | None) -> list[str]:
    if not document:
        return ['no_body']
    text = document['content']
    warnings = quality_warnings(dict(document, content_at_limit=len(text) >= MAX_TEXT_CHARS), text)
    if len(text) < 500:
        warnings.append('less_than_500_characters')
    if len(text) >= MAX_TEXT_CHARS:
        warnings.append('possibly_truncated')
    if document.get('language') not in {'zh', 'en'}:
        warnings.append('unsupported_language')
    if not has_economy_context(document.get('title', ''), text):
        warnings.append('no_economy_context')
    title = document.get('title', '').casefold()
    if any(marker in title for marker in ('just a moment', 'access denied', 'sign in', 'log in', 'security check')):
        warnings.append('login_or_challenge')
    return sorted(set(warnings))


def publication_metadata(raw: bytes, *, parser: str = 'html.parser') -> tuple[str | None, str | None]:
    """Only explicit publication fields with timezone; never infer from crawl time."""
    soup = BeautifulSoup(raw, parser, parse_only=SoupStrainer(['meta', 'time', 'script']))
    candidates = []
    for tag in soup.select(
        'meta[property="article:published_time"],meta[property="article:published"],'
        'meta[property="og:article:published_time"],meta[property="og:published_time"],'
        'meta[itemprop="datePublished"],time[itemprop="datePublished"]'
    ):
        candidates.append((tag.get('content') or tag.get('datetime'), 'html:datePublished'))
    for tag in soup.select('meta[name],meta[property],time[pubdate][datetime]'):
        field = (tag.get('name') or tag.get('property') or '').casefold()
        if field in {'pubdate', 'publishdate', 'publish_date', 'publication_date', 'datepublished',
                     'dc.date.issued', 'dcterms.issued', 'parsely-pub-date', 'sailthru.date',
                     'og:article:published_time', 'og:published_time'}:
            candidates.append((tag.get('content'), 'html:' + field))
        elif tag.name == 'time':
            candidates.append((tag.get('datetime'), 'html:time.pubdate'))
    def walk(value):
        if isinstance(value, dict):
            kind = value.get('@type', '')
            if any(t in str(kind) for t in ('Article','BlogPosting','NewsArticle','ScholarlyArticle')):
                candidates.append((value.get('datePublished'), 'jsonld:datePublished'))
            elif 'VideoObject' in str(kind):
                candidates.append((value.get('uploadDate'), 'jsonld:uploadDate'))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            # Accept literal control characters in otherwise valid JSON-LD;
            # publication values still pass the explicit timezone check below.
            walk(json.loads(tag.string or tag.get_text() or '', strict=False))
        except (ValueError, RecursionError):
            continue
    soup.decompose()
    for value, source in candidates:
        try:
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
            except ValueError:
                parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is not None and parsed.timestamp() <= time.time()+86400:
                return parsed.isoformat(), source
        except (ValueError, OverflowError, TypeError):
            continue
    return None, None


class DatedFetcher(LiveFetcher):
    publication = (None, None)

    def _request(self, url, accepted):
        response, raw = super()._request(url, accepted)
        if response.status_code < 400 and 'html' in response.headers.get('Content-Type','').lower():
            self.publication = publication_metadata(raw, parser='lxml' if self.lean_metadata else 'html.parser')
        return response, raw


def seed_queries(
    store: ExperimentStore, config: Config, now: float, preflight: bool = False,
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES,
    locales: tuple[SearchLocale, ...] | None = None,
):
    languages = normalize_languages(languages)
    locales = tuple(locales or tuple(locale_for_language(language) for language in languages))
    selected_languages = {locale.language for locale in locales}
    if not selected_languages or not selected_languages <= set(languages):
        raise ValueError('locales must match selected languages')
    topics = [
        spec for spec in base_keyword_specs()
        if spec.key.endswith(':topic') and spec.language in languages
    ]
    if preflight:
        for query, language in (('AI agents release open source update', 'en'), ('人工智能智能体 发布 开源 更新', 'zh')):
            if language in languages:
                for locale in locales:
                    if locale.language == language:
                        store.add_query('event', query + EXCLUDE, language, query, pages=1, locale_label=locale.label)
        store.set('preflight_search_target', len(languages))
        store.set('catalog_seeded', True)
        return
    if not store.get('catalog_seeded'):
        for spec in base_keyword_specs():
            if spec.language not in languages:
                continue
            family = 'topic' if spec.key.endswith(':topic') else 'event'
            for locale in locales:
                if locale.language == spec.language:
                    store.add_query(family, spec.query + EXCLUDE, spec.language, spec.aliases[0], locale_label=locale.label)
        for query in config.continuous_ai_keywords:
            language = 'zh' if re.search('[\u3400-\u9fff]', query) else 'en'
            if language in languages:
                for locale in locales:
                    if locale.language == language:
                        store.add_query('topic', query + EXCLUDE, language, query, locale_label=locale.label)
        store.set('catalog_seeded', True)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    if store.get('recent_date') != str(today):
        store.db.execute("UPDATE queries SET enabled=0 WHERE family='recent'")
        for spec in topics:
            for days in (1, 7):
                text = f'{spec.query} after:{today - timedelta(days=days)}{EXCLUDE}'
                for locale in locales:
                    if locale.language == spec.language:
                        store.add_query('recent', text, spec.language, spec.query, locale_label=locale.label)
        store.set('recent_date', str(today))


def site_queries(store: ExperimentStore, locales: tuple[SearchLocale, ...] | None = None):
    locales = locales or tuple(locale_for_language(language) for language in normalize_languages(None))
    candidates = {}
    for row in store.db.execute("SELECT d.canonical,d.hash,q.topic,q.language FROM documents d JOIN discoveries x ON d.url=x.url "
                                "JOIN queries q ON q.id=x.query_id WHERE d.quality='[]'"):
        host = (urlsplit(row['canonical']).hostname or '').lower()
        if not host or any(host == d or host.endswith('.' + d) for d in ('youtube.com', 'youtu.be', 'facebook.com', 'instagram.com', 'tiktok.com')):
            continue
        item = candidates.setdefault(host, {'hashes': set(), 'topics': set()})
        item['hashes'].add(row['hash'])
        item['topics'].add((row['topic'], row['language']))
    store.db.execute("UPDATE queries SET enabled=0 WHERE family='site'")
    selected = sorted((h for h, v in candidates.items() if len(v['hashes']) >= 2), key=lambda h: (-len(candidates[h]['hashes']), h))[:20]
    for host in selected:
        for topic, language in sorted(candidates[host]['topics'])[:5]:
            for locale in (locale for locale in locales if locale.language == language):
                store.add_query('site', f'site:{host} {topic}{EXCLUDE}', language, topic, locale_label=locale.label)
    store.db.commit()
    return len(selected)


def child_fetch():
    request = json.load(sys.stdin)
    config = Config()
    fetcher = DatedFetcher(config.user_agent, timeout=20, use_trafilatura=config.trafilatura_enabled)
    result = fetcher.fetch(SearchResult(request['url'], request['title'], ('google_web',)), request['query'], iso(request['first_seen']))
    payload = asdict(result)
    if payload.get('document'):
        payload['document']['published_at'], payload['document']['publication_source'] = fetcher.publication
    json.dump(payload, sys.stdout, ensure_ascii=False)


def fetch_one(row: dict, domain_lock: threading.Semaphore):
    tick = time.monotonic()
    with domain_lock:
        try:
            response = subprocess.run([sys.executable, '-m', 'realtime.google_experiment', 'fetch'],
                                      input=json.dumps(row), capture_output=True, text=True, timeout=65, check=True)
            result = json.loads(response.stdout)
        except subprocess.TimeoutExpired:
            result = {'status': 'failed', 'error': 'body_hard_deadline', 'document': None}
        except Exception as exc:
            result = {'status': 'failed', 'error': type(exc).__name__, 'document': None}
    result.update(requested_url=row['url'], finished=time.time(), seconds=round(time.monotonic() - tick, 3))
    if result.get('document'):
        doc = result['document']
        doc['content'] = ' '.join(doc['content'].split())
        doc['content_hash'] = digest(doc['content'])
    return result


def experiment_message(doc: dict, run_id: str, query: str, families: list[str], config: Config):
    item = dict(doc, campaign_id=run_id, query=query)
    task = {'task_id': run_id, 'dataset_id': config.whale_dataset_id,
            'source_platform': config.whale_source_platform, 'task_type': 'keyword_search'}
    _, message = whale_message(item, task, config)
    original_publication = doc.get('published_at')
    collector_fallback = not original_publication
    publication_source = doc.get('publication_source')
    if not collector_fallback:
        message['content']['published_at'] = original_publication
    else:
        publication_source = 'collector:fetched_at'
    message['discovery']['metadata'].update(experiment_id=run_id, query_families=families,
                                           publication_time_known=bool(original_publication),
                                           original_publication_time_known=bool(original_publication),
                                           publication_source=publication_source,
                                           published_at_is_collector_fallback=collector_fallback)
    return message


def import_baselines(store: ExperimentStore, production: CampaignStore, args):
    with production.connect() as connection:
        rows = connection.execute('SELECT url,content_hash FROM pages').fetchall()
    store.baseline((normalize_url(r['url']) for r in rows), (r['content_hash'] for r in rows))
    for directory in args.baseline_run:
        if getattr(args, 'executor', 'legacy') == 'pipeline':
            path = (Path(directory) / 'experiment.sqlite3').resolve()
            other = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                rows = other.execute("SELECT url,canonical,hash FROM documents WHERE quality='[]' AND hash<>''").fetchall()
                store.baseline((normalize_url(url) for row in rows for url in row[:2] if url), (row[2] for row in rows))
            finally:
                other.close()
            continue
        other = ExperimentStore(Path(directory))
        store.baseline((r[0] for r in other.db.execute('SELECT url FROM urls')),
                       (r[0] for r in other.db.execute("SELECT hash FROM documents WHERE hash<>''")))
        other.db.close()
    for directory in args.baseline_export:
        for path in (Path(directory) / 'documents').glob('*.md'):
            data = json.JSONDecoder().raw_decode(path.read_text(encoding='utf-8').split('```text\n', 1)[1])[0]
            store.baseline([data.get('requested_url'), data.get('url')], [data.get('content_hash')])
    store.set('baseline_note', '本地已有 URL/哈希与指定预检/导出快照；不保证覆盖被清空前的 Whale 全部历史。')
    store.set('baseline_ready', True)


def export_report(store: ExperimentStore, output: Path, *, full: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    counts = store.counts()
    header = {'experiment_id': store.get('id'), 'state': store.get('state'),
              'run_kind': 'preflight' if store.get('preflight') else '24h_experiment',
              'started_at': iso(store.get('started_at')), 'deadline': iso(store.get('deadline')),
              'last_heartbeat': iso(store.get('heartbeat', store.get('started_at'))),
              'stop_reason': store.get('stop_reason'), 'whale_enabled': store.get('whale'),
              'baseline': store.get('baseline_note'), **counts}
    text = '# Google 24 小时实验\n\n' + literal(json.dumps(header, ensure_ascii=False, indent=2))
    text += ('\n## 口径\n\n主指标 `new` 是本地基线之外、按 URL 和正文哈希去重、通过自动质量规则的首次有效正文；'
             '不是人工验收的完整文章，也不是当天发布的文章。`update`、`baseline`、`duplicate`、`invalid` 和 `late_*` 不计入主指标。'
             '相似改写可能未被精确哈希去重。Whale accepted/duplicate 表示接口接收/幂等重复回执，不代表平台最终索引完成。\n\n'
             'Whale 的 content.published_at 为协议必填字段：优先使用原站明确带时区的发布时间；缺失时使用正文采集时间兜底，'
             '并在 metadata.publication_source 标记 collector:fetched_at，不把兜底值写入本地原站发布时间。\n\n'
             '仅 Google 发现链接；主题/事件/站点/时间四组轮询。after 条件不保证真实发布时间。网页内部链接不进入发现队列。'
             '复用全球限速和冷却，实验请求上限 0.5 RPS；正文 4 并发、每域最多 2。'
             '使用独立缓存：前 3 页 1 小时，深页 6 小时。已发现 URL 最多每 6 小时复查，非新增单列。\n\n'
             '统计采用连续墙钟 24 小时，暂停、冷却、离线不延长截止时间。截止后结果单列，最多 5 分钟收尾。'
             '各组覆盖量可重合，独有量按完整发现关系计算，不按先抓到者归属。实验并非严格随机 A/B，不声称因果提升。\n\n'
             '详细证据在 search-pages/、documents/ 和小时统计.md；state 中的 SQLite 保留可重建记录。'
             '原始页面和机器人提示是外部不可信数据，正文用代码块保存。\n')
    _atomic(output / 'README.md', text)
    hourly = '# 每小时累计采样\n\n'
    hourly += '| UTC 时间 | 墙钟秒数 | 新增有效正文 | 搜索请求 | 候选 URL | Whale 接收 |\n|---|---:|---:|---:|---:|---:|\n'
    previous_hour = -1
    for row in store.db.execute('SELECT * FROM samples ORDER BY at'):
        hour = int((row['at'] - store.get('started_at')) / 3600)
        if hour == previous_hour and not full:
            continue
        previous_hour = hour
        m = json.loads(row['metrics'])
        hourly += f"| {iso(row['at'])} | {m['elapsed_seconds']} | {m.get('new', 0)} | {m['google_requests']} | {m['unique_urls']} | {m.get('whale_accepted',0)} |\n"
    _atomic(output / '小时统计.md', hourly)
    if full:
        for row in store.db.execute('SELECT * FROM documents'):
            export_document(store, output, row['id'])
        for row in store.db.execute('SELECT * FROM searches'):
            export_search(store, output, row['id'])
        paths = sorted((output / 'documents').glob('*.md'))
        extra_bytes = sum(p.stat().st_size for p in paths)
        used = sum(p.stat().st_size for root in (output, store.path.parent) for p in root.rglob('*') if p.is_file())
        if used+extra_bytes < 10*1024**3 and shutil.disk_usage(output).free-extra_bytes >= 2*1024**3:
            with (output / '全部正文.md').open('w', encoding='utf-8') as combined:
                combined.write('# 全部版本、正文及失败记录\n\n非合格记录不计入新增有效正文；详见各文件质量字段。\n\n')
                for path in paths:
                    combined.write(path.read_text(encoding='utf-8') + '\n---\n\n')
        else:
            _atomic(output / '合并文件未生成.md', '# 存储保护\n\n为遵守 10 GiB/剩余 2 GiB 限制，未额外复制合并全文。原始逐篇记录仍在 documents/。\n')
    return header


def _atomic(path: Path, text: str):
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def export_document(store, output, document_id):
    row = dict(store.db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone())
    doc = json.loads(row.pop('document'))
    occurrences = [dict(r) for r in store.db.execute('SELECT x.*,q.query,q.family,q.language FROM discoveries x JOIN queries q ON q.id=x.query_id WHERE x.url=?', (row['url'],))]
    row['occurrences'] = occurrences
    if doc:
        row['metadata'] = {k:v for k,v in doc.items() if k != 'content'}
    (output / 'documents').mkdir(exist_ok=True)
    _atomic(output / 'documents' / f'{document_id:07d}.md', '# 网页版本与采集记录\n\n' + literal(json.dumps(row, ensure_ascii=False, indent=2)) + '\n## 抽取正文\n\n' + (literal(doc['content']) if doc else '未取得正文。\n'))


def export_search(store, output, search_id):
    row = dict(store.db.execute('SELECT s.*,q.query,q.family,q.language FROM searches s JOIN queries q ON q.id=s.query_id WHERE s.id=?', (search_id,)).fetchone())
    row['results'] = json.loads(row['results'])
    row['attempts'] = json.loads(row['attempts'])
    (output / 'search-pages').mkdir(exist_ok=True)
    _atomic(output / 'search-pages' / f'{search_id:07d}.md', '# Google 分页记录\n\nresults 数组保留提取顺序；不是经过验证的桌面排名。\n\n' + literal(json.dumps(row, ensure_ascii=False, indent=2)))


class Runner:
    def __init__(self, store, config, production, output):
        self.store, self.config, self.production, self.output = store, config, production, output
        self.clients = {}
        self.pool = ProxyPool(config)
        self.whale = WhaleClient(config) if store.get('whale') else None
        self.remote_only = bool(store.get('remote_only'))
        self.registered = False
        self.next_whale = 0
        self.next_request = 0.0
        self.stop = False
        self.domain_locks = {}
        self.locales = runner_locales(store)

    def _locale_for(self, language):
        matches = [locale for locale in self.locales if locale.language == language]
        if not matches:
            raise ValueError(f'no locale configured for language: {language}')
        return matches[0]

    def locale_for_query(self, row):
        matches = [locale for locale in self.locales if locale.label == row['locale_label']]
        if matches:
            return matches[0]
        return self._locale_for(row['language'])

    def slot(self, source, initial_rps):
        if time.time() >= self.store.get('deadline') or self.store.get('state') != 'running':
            return {'allowed': False}
        limit = float(self.store.get('search_rps', 2)) if self.store.get('executor') == 'pipeline' else 2.0
        if limit == 2:
            slot = self.production.acquire_discovery_slot(
                source, min(initial_rps, limit),
                probe_rps=self.config.google_web_circuit_probe_rps,
            )
        else:
            slot = self.production.acquire_discovery_slot(
                source, min(initial_rps, limit), maximum_rps=limit,
                probe_rps=self.config.google_web_circuit_probe_rps,
            )
        if source == 'google_web' and not slot.get('allowed'):
            self.store.set('search_cooling_until', time.time() + max(1, float(slot.get('wait') or 60)))
        if source == 'google_web' and slot.get('allowed'):
            now = time.monotonic()
            wait = max(float(slot.get('wait', 0)), self.next_request-now, 0)
            self.next_request = now + wait + 1/limit
            if time.time() + wait >= self.store.get('deadline'):
                return {'allowed': False}
            slot['wait'] = wait
            with self.store.db:
                self.store.db.execute("INSERT INTO runtime VALUES('search_pacing_wait',?) ON CONFLICT(kind) DO UPDATE SET seconds=seconds+excluded.seconds", (wait,))
        return slot

    def client(self, language):
        return self.client_for_locale(self._locale_for(language))

    def client_for_locale(self, locale):
        key = locale.label
        if key not in self.clients:
            limit = float(self.store.get('search_rps', 2)) if self.store.get('executor') == 'pipeline' else 2.0
            self.clients[key] = SearchDiscovery(
                timeout=20, proxy_pool=self.pool, proxy_profile=self.store.get('proxy_profile', 'private'), language=locale.language,
                search_locale=locale,
                parse_mode=self.store.get('serp_parse_mode', 'light'),
                time_filter=self.store.get('time_filter', ''),
                proxy_profiles=self.config.google_proxy_profiles,
                providers=tuple(self.store.get('google_providers', ['wml','wml_direct','searxng'])), searxng_url=self.config.searxng_url,
                source_slot_acquirer=self.slot,
                source_result_recorder=lambda *args, **kwargs: self.production.record_discovery_result(
                    *args, circuit_max_seconds=self.config.google_web_circuit_max_seconds, **kwargs
                ),
                serp_attempt_recorder=self.production.record_google_serp_attempt,
                proxy_reserver=self.production.reserve_google_proxy, proxy_group_reserver=self.production.reserve_google_proxy_group,
                proxy_result_recorder=lambda *args, **kwargs: self.production.record_google_proxy_result(
                    *args, cooldown_cap_seconds=self.config.google_web_proxy_cooldown_cap_seconds, **kwargs
                ),
                google_web_initial_rps=limit if limit > 2 else 1.0, google_web_max_rps=limit,
                proxy_cooldown_seconds=self.config.google_web_proxy_cooldown_seconds,
                proxy_sticky_seconds=self.config.google_proxy_sticky_seconds,
                source_cooldown_seconds=self.config.google_web_source_cooldown_seconds,
                proxy_provider_attempts=0,
                session_id='')
        return self.clients[key]

    def client_for_query(self, row):
        locale = self.locale_for_query(row)
        if self.store.get('session_policy') != 'query':
            return self.client_for_locale(locale)
        key = (locale.label, row['query'])
        if key not in self.clients:
            current = self.client_for_locale(locale)
            client = SearchDiscovery(
                timeout=current.timeout, proxy_pool=current.proxy_pool,
                proxy_profile=current.proxy_profile,
                proxy_profiles=current.proxy_profiles, language=current.language,
                search_locale=current.search_locale,
                global_concurrency=current.discovery_global_concurrency,
                query_concurrency=current.query_concurrency,
                google_web_enabled=current.google_web_enabled,
                google_web_initial_rps=current.google_web_initial_rps,
                google_web_max_rps=current.google_web_max_rps,
                google_web_max_pages=current.google_web_max_pages,
                google_web_pages_per_batch=current.google_web_pages_per_batch,
                query_cache_seconds=current.query_cache_seconds,
                proxy_min_interval_seconds=current.proxy_min_interval_seconds,
                proxy_sticky_seconds=current.proxy_sticky_seconds,
                proxy_cooldown_seconds=current.proxy_cooldown_seconds,
                source_cooldown_seconds=current.source_cooldown_seconds,
                captcha_threshold=current.captcha_threshold,
                cache_get=current.cache_get, cache_put=current.cache_put,
                source_slot_acquirer=current.source_slot_acquirer,
                source_result_recorder=current.source_result_recorder,
                proxy_reserver=current.proxy_reserver,
                proxy_group_reserver=current.proxy_group_reserver,
                proxy_result_recorder=current.proxy_result_recorder,
                novelty_counter=current.novelty_counter,
                page_batch_acquirer=current.page_batch_acquirer,
                page_result_recorder=current.page_result_recorder,
                providers=current.providers, searxng_url=current.transport.searxng_url,
                persistent_browser_enabled=current.transport.persistent_browser_enabled,
                persistent_browser_profile_root=str(current.transport.persistent_browser_profile_root),
                persistent_browser_max_contexts=current.transport.persistent_browser_max_contexts,
                persistent_browser_request_interval_seconds=current.transport.persistent_browser_request_interval_seconds,
                persistent_browser_max_requests_per_context=current.transport.persistent_browser_max_requests_per_context,
                persistent_browser_max_context_lifetime_seconds=current.transport.persistent_browser_max_context_lifetime_seconds,
                persistent_browser_failure_threshold=current.transport.persistent_browser_failure_threshold,
                google_serp_save_html=current.transport.google_serp_save_html,
                google_serp_evidence_dir=str(current.transport.google_serp_evidence_dir),
                serp_attempt_recorder=current.serp_attempt_recorder,
                proxy_provider_attempts=current.proxy_provider_attempts,
                parse_mode=current.parse_mode, time_filter=current.time_filter,
                session_id=f"{locale.label}:{row['query']}",
            )
            self.clients[key] = client
        return self.clients[key]

    def search(self, row):
        cached = self.store.cached(row['id'], row['page'], time.time())
        results, attempts, error = [], [], ''
        started = time.time()
        retry_after = None
        if cached:
            results = json.loads(cached['results'])
        else:
            client = self.client_for_query(row)
            before = len(client.attempts)
            try:
                fields = ('rank', 'description', 'display_link', 'source', 'date', 'serp_module')
                results = [{
                    'url': normalize_url(r.url), 'raw_url': r.url, 'title': r.title,
                    **{field: getattr(r, field, None) for field in fields},
                } for r in client._discover_google_page(row['query'], row['page'])]
            except GoogleBlocked as exc:
                error = exc.reason
                retry_after = exc.retry_after
            attempts = client.attempts[before:]
            raw_metadata = client.last_search_evidence
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            metadata.pop('cache_key', None)
            # An empty response from a healthy primary may need confirmation
            # from a cooling fallback. Delay that page, not unrelated searches.
            if error == 'google_provider_cooling' and not any(a.get('success') for a in attempts):
                self.store.set('search_cooling_until', time.time() + 60)
            elif error == 'google_proxy_unavailable':
                # Proxy groups have a durable per-exit interval. Pause global
                # dispatch until the earliest group is reusable instead of
                # turning untouched pages into hundreds of avoidable failures.
                delay = min(60.0, max(0.25, float(retry_after or 1.0)))
                self.store.set(
                    'search_cooling_until',
                    max(self.store.get('search_cooling_until', 0), time.time() + delay),
                )
        search_id = self.store.search(
            row, row['page'], started, results, attempts, error, bool(cached),
            metadata=metadata,
        )
        if not self.remote_only:
            export_search(self.store, self.output, search_id)

    def save_body(self, record):
        doc = record.get('document')
        document_id, classification = self.store.save_document(record, quality(doc), self.store.get('deadline'))
        if self.whale and classification == 'new':
            rows = self.store.db.execute('SELECT DISTINCT q.family,q.query FROM discoveries x JOIN queries q ON q.id=x.query_id WHERE x.url=?', (record['requested_url'],)).fetchall()
            message = experiment_message(doc, self.store.get('id'), rows[0]['query'], sorted({r['family'] for r in rows}), self.config)
            with self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO outbox(document_id,payload,status) VALUES(?,?,?)',
                                      (document_id, json.dumps(message, ensure_ascii=False), 'pending'))
        if self.remote_only:
            with self.store.db:
                self.store.db.execute("UPDATE documents SET document='null' WHERE id=?", (document_id,))
        else:
            export_document(self.store, self.output, document_id)

    def flush_whale(self):
        if not self.whale or time.time() < self.next_whale:
            return
        self.next_whale = time.time() + self.store.get('upload_interval', 20)
        try:
            if not self.registered:
                self.whale.register()
                self.registered = True
            if time.time() >= getattr(self, 'next_heartbeat', 0):
                self.whale.heartbeat(1)
                self.next_heartbeat = time.time() + 20
            rows = self.store.db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? LIMIT ?", (time.time(), self.store.get('upload_batch_size', 25))).fetchall()
            if not rows:
                return
            try:
                receipts = self.whale.bulk_ingest([json.loads(r['payload']) for r in rows])
                if len(receipts) != len(rows):
                    raise RuntimeError('receipt_count_mismatch')
            except Exception as exc:
                http = getattr(exc, 'status_code', 0)
                permanent = bool(http and http not in {408,425,429,500,502,503,504})
                with self.store.db:
                    for row in rows:
                        delay = min(300, 5 * 2 ** min(row['attempts'], 6))
                        self.store.db.execute("UPDATE outbox SET status=?,attempts=attempts+1,next_attempt=?,error=? WHERE document_id=?",
                            ('rejected' if permanent else 'pending', time.time()+delay, f'{type(exc).__name__}:HTTP{http}', row['document_id']))
                        if permanent and self.remote_only:
                            self.store.db.execute('UPDATE outbox SET payload=NULL WHERE document_id=?', (row['document_id'],))
                self.store.set('whale_last_error', f'{type(exc).__name__}:HTTP{http}')
                return
            with self.store.db:
                for row, receipt in zip(rows, receipts):
                    status = receipt.get('receipt_status')
                    state = 'accepted' if status in {'queued','accepted'} else 'duplicate' if status == 'duplicate' else 'rejected'
                    self.store.db.execute('UPDATE outbox SET status=?,finished=?,attempts=attempts+1,error=? WHERE document_id=?',
                        (state, time.time(), '' if state != 'rejected' else 'receipt_rejected', row['document_id']))
                    if self.remote_only:
                        self.store.db.execute('UPDATE outbox SET payload=NULL WHERE document_id=?', (row['document_id'],))
            self.store.set('whale_last_error', None)
        except Exception as exc:
            self.registered = False
            self.store.set('whale_last_error', type(exc).__name__)

    def storage_ok(self):
        paths = {p.resolve() for root in (self.output, self.store.path.parent) for p in root.rglob('*') if p.is_file()}
        used = sum(p.stat().st_size for p in paths)
        return used < self.store.get('storage_budget_gib', 10) * 1024**3 and shutil.disk_usage(self.output).free >= 2 * 1024**3

    def run(self):
        futures = {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=BODY_WORKERS)
        last_sample = last_sync = 0.0
        last_tick = self.store.get('heartbeat', time.time())
        family_index = int(self.store.get('family_index', 0))
        drain_until = None
        self.store.db.execute("UPDATE urls SET state='pending' WHERE state='fetching'")
        self.store.db.commit()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: setattr(self, 'stop', True))
        try:
            while True:
                now = time.time()
                state = self.store.get('state')
                interval = max(0, now-last_tick)
                kind = 'offline' if interval > 120 else 'paused' if state == 'paused' else 'google_cooling' if self.store.get('search_cooling_until',0)>now else 'active'
                with self.store.db:
                    self.store.db.execute('INSERT INTO runtime VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET seconds=seconds+excluded.seconds', (kind, interval))
                self.store.set('heartbeat', now)
                last_tick = now
                for future in list(futures):
                    if future.done():
                        self.save_body(future.result())
                        del futures[future]
                preflight_target = int(self.store.get('preflight_search_target', 2))
                preflight_done = self.store.get('preflight') and self.store.db.execute('SELECT count(*) FROM searches').fetchone()[0] >= preflight_target
                pending = self.store.db.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0]
                complete_early = preflight_done and not pending and not futures
                if drain_until is not None or now >= self.store.get('deadline') or self.stop or state in FINAL_STATES or complete_early:
                    if drain_until is None:
                        drain_until = min(now+300, self.store.get('deadline')+300)
                        self.store.set('state', 'draining')
                        self.store.set('stop_reason', self.store.get('stop_reason') or ('preflight_complete' if complete_early else 'interrupted' if self.stop else 'deadline'))
                    if time.time() + self.config.request_timeout * 3 < drain_until:
                        self.flush_whale()
                    outbox = self.store.db.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0]
                    if (not futures and not outbox) or time.time() >= drain_until:
                        break
                elif state == 'running':
                    if now-last_sync >= 300:
                        try:
                            syncer = ProxySynchronizer(self.config)
                            for profile in self.config.google_proxy_profiles:
                                syncer.sync(profile)
                            last_sync = now
                        except Exception as exc:
                            self.store.set('proxy_last_error', type(exc).__name__)
                            last_sync = now-240
                    today = str(datetime.fromtimestamp(now, timezone.utc).date())
                    if not self.store.get('catalog_seeded') or (not self.store.get('preflight') and self.store.get('recent_date') != today):
                        seed_queries(
                            self.store, self.config, now, self.store.get('preflight'),
                            normalize_languages(self.store.get('languages')),
                            self.locales,
                        )
                    if now >= self.store.get('site_due', 0) and not self.store.get('preflight'):
                        selected = site_queries(self.store, self.locales)
                        self.store.set('site_due', now + (21600 if selected else 60))
                    for row in self.store.due_urls(BODY_WORKERS-len(futures), now):
                        lock = self.domain_locks.setdefault(urlsplit(row['url']).hostname, threading.BoundedSemaphore(2))
                        self.store.db.execute("UPDATE urls SET state='fetching' WHERE url=?", (row['url'],))
                        self.store.db.commit()
                        futures[executor.submit(fetch_one, dict(row), lock)] = row['url']
                    backlog = self.store.db.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0]
                    if backlog < SEARCH_BACKLOG_LIMIT and not preflight_done and now >= self.store.get('search_cooling_until',0):
                        for offset in range(4):
                            index = (family_index+offset) % 4
                            row = self.store.due_query(FAMILIES[index], now)
                            if row:
                                self.search(row)
                                family_index = (index+1) % 4
                                self.store.set('family_index', family_index)
                                break
                    self.flush_whale()
                if now-last_sample >= 60:
                    metrics = self.store.counts()
                    with self.store.db:
                        self.store.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?)', (now, json.dumps(metrics)))
                    if not self.remote_only:
                        export_report(self.store, self.output)
                    print(json.dumps({'experiment': self.store.get('id'), 'state': self.store.get('state'),
                                      'new': metrics.get('new',0), 'requests': metrics['google_requests'],
                                      'whale_accepted': metrics.get('whale_accepted',0)}, ensure_ascii=False), flush=True)
                    last_sample = now
                    if not self.storage_ok():
                        self.store.set('state', 'storage_stopped')
                        self.store.set('stop_reason', 'disk_budget_or_free_space')
                time.sleep(.25 if futures else 1)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            for future in list(futures):
                if not future.cancelled():
                    self.save_body(future.result())
            for client in self.clients.values():
                client._close_browser()
            reason = self.store.get('stop_reason')
            self.store.set('state', 'storage_stopped' if reason == 'disk_budget_or_free_space' else 'paused' if self.stop else 'complete' if drain_until is not None else 'interrupted')
            self.store.set('finished_at', time.time())
            with self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?)', (time.time(), json.dumps(self.store.counts())))
            archive_audit(self.store)
            if not self.remote_only:
                export_report(self.store, self.output, full=True)


def command(args):
    directory = Path(args.directory)
    store = ExperimentStore(directory, create=args.action == 'start')
    if args.action in {'pause','resume'}:
        if store.get('state') in FINAL_STATES or time.time() >= store.get('deadline'):
            store.db.close()
            raise ValueError('experiment is finished; cannot extend its deadline')
        store.set('state', 'paused' if args.action == 'pause' else 'running')
        if args.action == 'pause':
            archive_audit(store)
        print(json.dumps({'state': store.get('state'), 'deadline': iso(store.get('deadline'))}))
        store.db.close()
        return
    if args.action == 'status':
        print(json.dumps({'id': store.get('id'), 'state': store.get('state'), 'deadline': iso(store.get('deadline')),
                          'languages': list(normalize_languages(store.get('languages'))),
                          'heartbeat_age_seconds': round(time.time()-store.get('heartbeat',0),1),
                          'whale_last_error': store.get('whale_last_error'), **store.counts()}, ensure_ascii=False, indent=2))
        store.db.close()
        return
    output = Path(store.get('output') or args.output)
    if args.action == 'export':
        print(json.dumps(export_report(store, output, full=True), ensure_ascii=False, indent=2))
        store.db.close()
        return
    if args.hours <= 0 or args.hours > 24:
        raise ValueError('hours must be >0 and <=24')
    if not 0 < getattr(args, 'search_rps', 2) <= 8:
        raise ValueError('search_rps must be >0 and <=8')
    requested_languages = normalize_languages(getattr(args, 'languages', None))
    selected_locales = resolve_experiment_locales(
        requested_languages,
        locale_matrix=bool(getattr(args, 'locale_matrix', False)),
        locales=getattr(args, 'locales', 'auto'),
        providers=getattr(args, 'google_providers', ['wml', 'wml_direct', 'searxng']),
    )
    if getattr(args, 'locale_matrix', False):
        validate_locale_matrix(selected_locales, requested_languages, getattr(args, 'google_providers', []))
    if not 0 < getattr(args, 'storage_budget_gib', 10) <= 128:
        raise ValueError('storage_budget_gib must be >0 and <=128')
    remote_only = bool(getattr(args, 'remote_only', False))
    if remote_only and not args.whale:
        raise ValueError('--remote-only requires --whale')
    output.mkdir(parents=True, exist_ok=True)
    lock = (directory / 'runner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = Config()
    if args.whale and not config.whale_collector_api_key:
        raise ValueError('Whale credentials are missing')
    production = CampaignStore(config.database_url)
    try:
        syncer = ProxySynchronizer(config)
        for profile in config.google_proxy_profiles:
            syncer.sync(profile)
    except Exception as exc:
        print(f'initial proxy sync failed: {type(exc).__name__}')
    if not store.get('id'):
        if any(output.iterdir()):
            raise ValueError('new experiment requires an empty output directory')
        store.set('id', 'google-experiment-' + directory.name)
        store.set('output', str(output))
        store.set('whale', bool(args.whale))
        store.set('remote_only', remote_only)
        store.set('preflight', bool(args.preflight))
        store.set('languages', list(requested_languages))
        locale_matrix = bool(getattr(args, 'locale_matrix', False))
        store.set('locales', [locale.label for locale in selected_locales] if locale_matrix else None)
        store.set('locale_matrix', locale_matrix)
        store.set('executor', getattr(args, 'executor', 'legacy'))
        store.set('body_workers', getattr(args, 'body_workers', 24))
        store.set('body_max_rss_mib', getattr(args, 'body_max_rss_mib', 192))
        store.set('search_workers', getattr(args, 'search_workers', 3))
        store.set('proxy_profile', getattr(args, 'proxy_profile', 'private'))
        store.set('google_providers', getattr(args, 'google_providers', ['wml','wml_direct','searxng']))
        store.set('search_rps', getattr(args, 'search_rps', 2.0))
        store.set('storage_budget_gib', getattr(args, 'storage_budget_gib', 10))
        store.set('query_plan', getattr(args, 'query_plan', 'balanced'))
        store.set('time_filter', getattr(args, 'time_filter', ''))
        store.set('serp_parse_mode', getattr(args, 'parse_mode', 'light'))
        store.set('session_policy', getattr(args, 'session_policy', 'thread'))
        store.set('baseline_run_paths', list(args.baseline_run))
        store.set('state', 'initializing')
    if not store.get('baseline_ready'):
        import_baselines(store, production, args)
    if not store.get('started_at'):
        now = time.time()
        store.set('started_at', now)
        store.set('deadline', now + args.hours * 3600)
        store.set('state', 'running')
    if store.get('state') in FINAL_STATES:
        archive_audit(store)
        print('Experiment already finished; deadline is unchanged.')
        production.pool.close()
        return
    if store.get('state') == 'interrupted':
        store.set('state', 'running')
    try:
        if store.get('executor') == 'pipeline':
            from .fast_experiment import PipelineRunner
            PipelineRunner(store, config, production, output).run()
        else:
            Runner(store, config, production, output).run()
    finally:
        production.pool.close()
        store.db.close()
        lock.close()


if __name__ == '__main__' and sys.argv[1:] == ['fetch']:
    child_fetch()
