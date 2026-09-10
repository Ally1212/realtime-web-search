"""Bounded Google-only experiments with separate storage, runtime and Whale outbox."""
from __future__ import annotations

import concurrent.futures
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .campaign_store import CampaignStore
from .config import Config
from .discovery import GoogleBlocked, SearchDiscovery, SearchResult
from .experiment_store import ExperimentStore, digest
from .fetcher import LiveFetcher, MAX_TEXT_CHARS, normalize_url
from .keyword_catalog import AI_ANCHORS, base_keyword_specs
from .markdown_export import literal, quality_warnings
from .proxy_pool import ProxyPool, ProxySynchronizer
from .whale_collector import WhaleClient, whale_message

FAMILIES = ('topic', 'event', 'site', 'recent')
EXCLUDE = ' -site:youtube.com -site:youtu.be'
FINAL_STATES = {'complete', 'storage_stopped', 'stopped'}


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


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
    if not AI_ANCHORS.search(document.get('title', '') + ' ' + text):
        warnings.append('no_ai_context')
    title = document.get('title', '').casefold()
    if any(marker in title for marker in ('just a moment', 'access denied', 'sign in', 'log in', 'security check')):
        warnings.append('login_or_challenge')
    return sorted(set(warnings))


def publication_metadata(raw: bytes) -> tuple[str | None, str | None]:
    """Only explicit publication fields with timezone; never infer from crawl time."""
    soup = BeautifulSoup(raw, 'html.parser')
    candidates = []
    for tag in soup.select('meta[property="article:published_time"],meta[itemprop="datePublished"],time[itemprop="datePublished"]'):
        candidates.append((tag.get('content') or tag.get('datetime'), 'html:datePublished'))
    def walk(value):
        if isinstance(value, dict):
            kind = value.get('@type', '')
            if any(t in str(kind) for t in ('Article','BlogPosting','NewsArticle','ScholarlyArticle')):
                candidates.append((value.get('datePublished'), 'jsonld:datePublished'))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(tag.string or tag.get_text() or ''))
        except (ValueError, RecursionError):
            continue
    for value, source in candidates:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is not None and parsed.timestamp() <= time.time()+86400:
                return parsed.isoformat(), source
        except (ValueError, OverflowError):
            continue
    return None, None


class DatedFetcher(LiveFetcher):
    publication = (None, None)

    def _request(self, url, accepted):
        response, raw = super()._request(url, accepted)
        if response.status_code < 400 and 'html' in response.headers.get('Content-Type','').lower():
            self.publication = publication_metadata(raw)
        return response, raw


def seed_queries(store: ExperimentStore, config: Config, now: float, preflight: bool = False):
    topics = [s for s in base_keyword_specs() if s.key.endswith(':topic')]
    if preflight:
        for query, language in (('AI agents release open source update', 'en'), ('人工智能智能体 发布 开源 更新', 'zh')):
            store.add_query('event', query + EXCLUDE, language, query, pages=1)
        store.set('catalog_seeded', True)
        return
    if not store.get('catalog_seeded'):
        for spec in base_keyword_specs():
            family = 'topic' if spec.key.endswith(':topic') else 'event'
            store.add_query(family, spec.query + (EXCLUDE if family == 'event' else ''), spec.language, spec.aliases[0])
        for query in config.continuous_ai_keywords:
            language = 'zh' if re.search('[\u3400-\u9fff]', query) else 'en'
            store.add_query('topic', query, language, query)
        store.set('catalog_seeded', True)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    if store.get('recent_date') != str(today):
        store.db.execute("UPDATE queries SET enabled=0 WHERE family='recent'")
        for spec in topics:
            for days in (1, 7):
                text = f'{spec.query} after:{today - timedelta(days=days)}{EXCLUDE}'
                store.add_query('recent', text, spec.language, spec.query)
        store.set('recent_date', str(today))


def site_queries(store: ExperimentStore):
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
            store.add_query('site', f'site:{host} {topic}{EXCLUDE}', language, topic)
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
    # The original helper substitutes fetched_at for published_at. Experiments
    # must never represent a crawl timestamp as a publication timestamp.
    message['content'].pop('published_at', None)
    if doc.get('published_at'):
        message['content']['published_at'] = doc['published_at']
    message['discovery']['metadata'].update(experiment_id=run_id, query_families=families,
                                           publication_time_known=bool(doc.get('published_at')),
                                           publication_source=doc.get('publication_source'))
    return message


def import_baselines(store: ExperimentStore, production: CampaignStore, args):
    with production.connect() as connection:
        rows = connection.execute('SELECT url,content_hash FROM pages').fetchall()
    store.baseline((normalize_url(r['url']) for r in rows), (r['content_hash'] for r in rows))
    for directory in args.baseline_run:
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
             'Whale 当前强制 published_at：只投递取得网页明确带时区发布时间的合格新正文；未知时间的正文保留本地，'
             '以 whale_blocked_missing_publication 单列，不填造日期。日期仅为原站声明，未独立证实。\n\n'
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
        self.registered = False
        self.next_whale = 0
        self.next_request = 0.0
        self.stop = False
        self.domain_locks = {}

    def slot(self, source, initial_rps):
        if time.time() >= self.store.get('deadline') or self.store.get('state') != 'running':
            return {'allowed': False}
        slot = self.production.acquire_discovery_slot(source, min(initial_rps, .5))
        if source == 'google_web' and not slot.get('allowed'):
            self.store.set('search_cooling_until', time.time() + max(1, float(slot.get('wait') or 60)))
        if source == 'google_web' and slot.get('allowed'):
            now = time.monotonic()
            wait = max(float(slot.get('wait', 0)), self.next_request-now, 0)
            self.next_request = now + wait + 2
            if time.time() + wait >= self.store.get('deadline'):
                return {'allowed': False}
            slot['wait'] = wait
            with self.store.db:
                self.store.db.execute("INSERT INTO runtime VALUES('search_pacing_wait',?) ON CONFLICT(kind) DO UPDATE SET seconds=seconds+excluded.seconds", (wait,))
        return slot

    def client(self, language):
        if language not in self.clients:
            self.clients[language] = SearchDiscovery(
                timeout=20, proxy_pool=self.pool, proxy_profile='private', language=language,
                providers=('wml','wml_direct','searxng'), searxng_url=self.config.searxng_url,
                source_slot_acquirer=self.slot, source_result_recorder=self.production.record_discovery_result,
                proxy_reserver=self.production.reserve_google_proxy, proxy_result_recorder=self.production.record_google_proxy_result,
                google_web_initial_rps=.5, google_web_max_rps=.5)
        return self.clients[language]

    def search(self, row):
        cached = self.store.cached(row['id'], row['page'], time.time())
        results, attempts, error = [], [], ''
        started = time.time()
        if cached:
            results = json.loads(cached['results'])
        else:
            client = self.client(row['language'])
            before = len(client.attempts)
            try:
                results = [{'url': normalize_url(r.url), 'raw_url': r.url, 'title': r.title} for r in client._discover_google_page(row['query'], row['page'])]
            except GoogleBlocked as exc:
                error = exc.reason
            attempts = client.attempts[before:]
        search_id = self.store.search(row, row['page'], started, results, attempts, error, bool(cached))
        export_search(self.store, self.output, search_id)

    def save_body(self, record):
        doc = record.get('document')
        document_id, classification = self.store.save_document(record, quality(doc), self.store.get('deadline'))
        if self.whale and classification == 'new':
            rows = self.store.db.execute('SELECT DISTINCT q.family,q.query FROM discoveries x JOIN queries q ON q.id=x.query_id WHERE x.url=?', (record['requested_url'],)).fetchall()
            message = experiment_message(doc, self.store.get('id'), rows[0]['query'], sorted({r['family'] for r in rows}), self.config)
            with self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO outbox(document_id,payload,status) VALUES(?,?,?)',
                                      (document_id, json.dumps(message, ensure_ascii=False), 'pending' if doc.get('published_at') else 'blocked_missing_publication'))
        export_document(self.store, self.output, document_id)

    def flush_whale(self):
        if not self.whale or time.time() < self.next_whale:
            return
        self.next_whale = time.time() + 20
        try:
            if not self.registered:
                self.whale.register()
                self.registered = True
            self.whale.heartbeat(1)
            rows = self.store.db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? LIMIT 25", (time.time(),)).fetchall()
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
                self.store.set('whale_last_error', f'{type(exc).__name__}:HTTP{http}')
                return
            with self.store.db:
                for row, receipt in zip(rows, receipts):
                    status = receipt.get('receipt_status')
                    state = 'accepted' if status in {'queued','accepted'} else 'duplicate' if status == 'duplicate' else 'rejected'
                    self.store.db.execute('UPDATE outbox SET status=?,finished=?,attempts=attempts+1,error=? WHERE document_id=?',
                        (state, time.time(), '' if state != 'rejected' else 'receipt_rejected', row['document_id']))
        except Exception as exc:
            self.registered = False
            self.store.set('whale_last_error', type(exc).__name__)

    def storage_ok(self):
        used = sum(p.stat().st_size for root in (self.output, self.store.path.parent) for p in root.rglob('*') if p.is_file())
        return used < 10 * 1024**3 and shutil.disk_usage(self.output).free >= 2 * 1024**3

    def run(self):
        futures = {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
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
                preflight_done = self.store.get('preflight') and self.store.db.execute('SELECT count(*) FROM searches').fetchone()[0] >= 2
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
                    if now-last_sync >= 1800:
                        try:
                            ProxySynchronizer(self.config).sync('private')
                            last_sync = now
                        except Exception as exc:
                            self.store.set('proxy_last_error', type(exc).__name__)
                            last_sync = now-1740
                    today = str(datetime.fromtimestamp(now, timezone.utc).date())
                    if not self.store.get('catalog_seeded') or (not self.store.get('preflight') and self.store.get('recent_date') != today):
                        seed_queries(self.store, self.config, now, self.store.get('preflight'))
                    if now >= self.store.get('site_due', 0) and not self.store.get('preflight'):
                        selected = site_queries(self.store)
                        self.store.set('site_due', now + (21600 if selected else 60))
                    for row in self.store.due_urls(4-len(futures), now):
                        lock = self.domain_locks.setdefault(urlsplit(row['url']).hostname, threading.BoundedSemaphore(2))
                        self.store.db.execute("UPDATE urls SET state='fetching' WHERE url=?", (row['url'],))
                        self.store.db.commit()
                        futures[executor.submit(fetch_one, dict(row), lock)] = row['url']
                    backlog = self.store.db.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0]
                    if backlog < 100 and not preflight_done and now >= self.store.get('search_cooling_until',0):
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
            export_report(self.store, self.output, full=True)


def command(args):
    directory = Path(args.directory)
    store = ExperimentStore(directory, create=args.action == 'start')
    if args.action in {'pause','resume'}:
        if store.get('state') in FINAL_STATES or time.time() >= store.get('deadline'):
            store.db.close()
            raise ValueError('experiment is finished; cannot extend its deadline')
        store.set('state', 'paused' if args.action == 'pause' else 'running')
        print(json.dumps({'state': store.get('state'), 'deadline': iso(store.get('deadline'))}))
        store.db.close()
        return
    if args.action == 'status':
        print(json.dumps({'id': store.get('id'), 'state': store.get('state'), 'deadline': iso(store.get('deadline')),
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
    output.mkdir(parents=True, exist_ok=True)
    lock = (directory / 'runner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = Config()
    if args.whale and not config.whale_collector_api_key:
        raise ValueError('Whale credentials are missing')
    production = CampaignStore(config.database_url, initialize=False)
    if not store.get('id'):
        if any(output.iterdir()):
            raise ValueError('new experiment requires an empty output directory')
        store.set('id', 'google-experiment-' + directory.name)
        store.set('output', str(output))
        store.set('whale', bool(args.whale))
        store.set('preflight', bool(args.preflight))
        store.set('state', 'initializing')
    if not store.get('baseline_ready'):
        import_baselines(store, production, args)
    if not store.get('started_at'):
        now = time.time()
        store.set('started_at', now)
        store.set('deadline', now + args.hours * 3600)
        store.set('state', 'running')
    if store.get('state') in FINAL_STATES:
        print('Experiment already finished; deadline is unchanged.')
        production.pool.close()
        return
    if store.get('state') == 'interrupted':
        store.set('state', 'running')
    try:
        Runner(store, config, production, output).run()
    finally:
        production.pool.close()
        store.db.close()
        lock.close()


if __name__ == '__main__' and sys.argv[1:] == ['fetch']:
    child_fetch()
