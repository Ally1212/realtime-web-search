"""Website tasks, live settings and filtered structured data. No article migrations."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from .serp_store import SerpStore
from .serp_fields import GOOGLE_SOURCES

NEWS_LOCALES = ('US:en', 'GB:en', 'SG:en', 'SG:zh-Hans', 'HK:zh-Hant', 'TW:zh-Hant', 'CN:zh-Hans', 'JP:ja', 'DE:de', 'FR:fr', 'IN:en', 'AU:en', 'CA:en')
REGIONS = ('US', 'GB', 'SG', 'HK', 'TW', 'CN', 'JP', 'DE', 'FR', 'IN', 'AU', 'CA', 'BR', 'KR')
CONSOLE_SCHEMA = """
ALTER TABLE serp_collector_settings ADD COLUMN IF NOT EXISTS runtime_config jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS serp_task_options (
 campaign_id uuid PRIMARY KEY REFERENCES campaigns(id), options jsonb NOT NULL DEFAULT '{}'::jsonb,
 started boolean NOT NULL DEFAULT true
);
ALTER TABLE serp_task_options ADD COLUMN IF NOT EXISTS started boolean NOT NULL DEFAULT true;
"""


def sources(value):
    if not isinstance(value, list) or not value or any(s not in GOOGLE_SOURCES for s in value):
        raise ValueError('至少选择一个有效 Google 来源')
    return list(dict.fromkeys(value))


def options(value):
    if not isinstance(value, dict) or set(value) - {'web_region', 'web_language', 'news_locale', 'trends_geo'}:
        raise ValueError('无效地区参数')
    out = dict(value)
    for key in ('web_region', 'trends_geo'):
        if key in out and out[key] not in REGIONS:
            raise ValueError(key + ' 地区不支持')
    if 'web_language' in out and out['web_language'] not in ('auto', 'en', 'zh'):
        raise ValueError('Web 语言仅支持 auto/en/zh')
    if 'news_locale' in out and out['news_locale'] not in NEWS_LOCALES:
        raise ValueError('News edition 不支持')
    return out


class ConsoleStore(SerpStore):
    def initialize_console(self, config):
        with self.store.connect() as db:
            db.execute(CONSOLE_SCHEMA)
            # Only seed once from the worker's effective deployment configuration.
            db.execute("UPDATE serp_collector_settings SET runtime_config=%s WHERE id=true AND runtime_config='{}'::jsonb", (Jsonb(self.defaults(config)),))

    @staticmethod
    def defaults(c):
        return dict(continuous_enabled=c.serp_continuous_enabled,
                    continuous_sources=list(c.serp_continuous_sources), continuous_keywords=[],
                    web_region='US', web_language='auto', news_locales=list(c.google_news_locales or ('US:en', 'SG:zh-Hans')),
                    trends_geos=list(c.google_trends_geos), web_max_pages=c.google_web_max_pages,
                    news_interval_seconds=c.google_news_base_interval_seconds,
                    trends_interval_seconds=c.google_trends_interval_seconds)

    def settings(self, config):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM serp_collector_settings WHERE id=true').fetchone()
        return dict(self.defaults(config), **(row.get('runtime_config', {}) if row else {}),
                    content_fetch_enabled=bool(row['content_fetch_enabled']) if row else False,
                    updated_at=row['updated_at'] if row else None)

    def save_settings(self, payload, config):
        allowed = set(self.defaults(config)) | {'content_fetch_enabled'}
        if not isinstance(payload, dict) or not payload or set(payload) - allowed:
            raise ValueError('无效设置字段')
        for key in ('content_fetch_enabled', 'continuous_enabled'):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError(key + ' 必须是布尔值')
        if 'continuous_sources' in payload:
            sources(payload['continuous_sources'])
        for key, choices in (('news_locales', NEWS_LOCALES), ('trends_geos', REGIONS)):
            if key in payload and (not isinstance(payload[key], list) or not payload[key] or any(x not in choices for x in payload[key])):
                raise ValueError(key + ' 请选择有效地区')
        options({k: payload[k] for k in ('web_region', 'web_language') if k in payload})
        for key, low, high in (('web_max_pages', 1, 11), ('news_interval_seconds', 300, 86400), ('trends_interval_seconds', 60, 86400)):
            if key in payload and (type(payload[key]) is not int or not low <= payload[key] <= high):
                raise ValueError(f'{key} 必须为 {low}–{high} 的整数')
        if 'continuous_keywords' in payload:
            words = payload['continuous_keywords']
            if not isinstance(words, list) or len(words) > 1000 or any(not isinstance(x, str) or not x.strip() or len(x) > 300 for x in words):
                raise ValueError('持续关键词最多 1000 条，每条 1–300 字符')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM serp_collector_settings WHERE id=true FOR UPDATE').fetchone()
            cfg = dict(self.defaults(config), **row['runtime_config'])
            cfg.update({k: v for k, v in payload.items() if k != 'content_fetch_enabled'})
            db.execute('UPDATE serp_collector_settings SET runtime_config=%s,content_fetch_enabled=%s,updated_at=now() WHERE id=true',
                       (Jsonb(cfg), payload.get('content_fetch_enabled', row['content_fetch_enabled'])))
            # Existing automatic tasks follow the saved sources; manual choices stay task-specific.
            db.execute("UPDATE campaigns c SET sources=%s,updated_at=now() FROM serp_supply_campaigns s WHERE s.campaign_id=c.id AND s.run_id=%s AND c.status='active'", (Jsonb(cfg['continuous_sources']), config.serp_run_id))
        return self.settings(config)

    def effective_config(self, config):
        s = self.settings(config)
        return replace(config, serp_continuous_enabled=s['continuous_enabled'],
                       serp_continuous_sources=tuple(s['continuous_sources']),
                       google_news_locales=tuple(s['news_locales']), google_trends_geos=tuple(s['trends_geos']),
                       google_web_max_pages=s['web_max_pages'], google_news_base_interval_seconds=s['news_interval_seconds'],
                       google_trends_interval_seconds=s['trends_interval_seconds'])

    def task_options(self, cid):
        with self.store.connect() as db:
            row = db.execute('SELECT options FROM serp_task_options WHERE campaign_id=%s', (str(UUID(cid)),)).fetchone()
        return row['options'] if row else {}

    def runnable(self, cid):
        with self.store.connect() as db:
            row = db.execute("SELECT c.status,EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id) automatic, (SELECT runtime_config FROM serp_collector_settings WHERE id=true) cfg FROM campaigns c WHERE c.id=%s", (str(UUID(cid)),)).fetchone()
        return bool(row and row['status'] == 'active' and (not row['automatic'] or row['cfg'].get('continuous_enabled', True)))

    def create_task(self, payload, config):
        if not isinstance(payload, dict) or set(payload) - {'query', 'keywords', 'sources', 'options', 'start'}:
            raise ValueError('无效任务字段')
        selected = sources(payload.get('sources', ['google_web']))
        words = payload.get('keywords', [payload.get('query', '')])
        if not isinstance(words, list) or len(words) > 50 or any(not isinstance(x, str) or len(x) > 300 for x in words):
            raise ValueError('每个任务最多 50 个关键词，每条最多 300 字符')
        words = list(dict.fromkeys(x.strip() for x in words if x.strip()))
        if not words:
            if selected != ['google_trends']:
                raise ValueError('Web 和 News 需要关键词')
            words = ['当前热门趋势']
        start = payload.get('start', True)
        if type(start) is not bool:
            raise ValueError('start 必须是布尔值')
        opts = options(payload.get('options', {}))
        cid = str(uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) VALUES(%s,%s,%s,1000000,%s,%s,%s)',
                       (cid, words[0], Jsonb(words[1:]), config.default_proxy_profile, Jsonb(selected), 'active' if start else 'paused'))
            db.execute('INSERT INTO serp_task_options(campaign_id,options,started) VALUES(%s,%s,%s)', (cid, Jsonb(opts), start))
        return dict(campaign_id=cid, status='active' if start else 'paused', sources=selected, options=opts)

    def tasks(self, *, kind='manual', status=None, q='', limit=20, offset=0):
        if kind not in ('manual', 'automatic', 'all') or (status and status not in ('active', 'paused', 'stopped', 'failed')):
            raise ValueError('无效任务筛选')
        limit, offset = min(max(int(limit), 1), 100), max(int(offset), 0)
        clauses = ["NOT EXISTS(SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id)",
                   "NOT (c.sources ? 'rss')",
                   "(EXISTS(SELECT 1 FROM serp_task_options o WHERE o.campaign_id=c.id) OR EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id) OR EXISTS(SELECT 1 FROM serp_campaign_requests cr WHERE cr.campaign_id=c.id))"]
        params = []
        if kind != 'all':
            clauses.append(('NOT ' if kind == 'manual' else '') + 'EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id)')
        if status:
            clauses.append('c.status=%s'); params.append(status)
        if q:
            clauses.append('c.query ILIKE %s'); params.append('%'+q+'%')
        where = ' AND '.join(clauses)
        with self.store.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            total = db.execute('SELECT count(*) n FROM campaigns c WHERE '+where, params).fetchone()['n']
            rows = db.execute("SELECT c.id,c.query,c.aliases,c.sources,c.status,c.last_error,c.created_at,c.updated_at,coalesce(o.options,'{}'::jsonb) options,coalesce(o.started,true) started,EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id) automatic FROM campaigns c LEFT JOIN serp_task_options o ON o.campaign_id=c.id WHERE "+where+' ORDER BY c.created_at DESC,c.id DESC LIMIT %s OFFSET %s', (*params, limit, offset)).fetchall()
            for r in rows:
                counts = db.execute("SELECT count(*) requests,count(*) FILTER(WHERE s.classification IN ('results','empty')) successful,count(*) FILTER(WHERE cr.reused) cached,coalesce(sum(CASE WHEN NOT cr.reused THEN (s.response->>'new_unique_links')::int ELSE 0 END),0) new_links,max(s.completed_at) last_request_at FROM serp_campaign_requests cr JOIN serp_requests s USING(request_id) WHERE cr.campaign_id=%s", (r['id'],)).fetchone()
                r.update(counts)
        return dict(tasks=rows, total=total, limit=limit, offset=offset)

    def action(self, cid, operation):
        cid = str(UUID(cid))
        if operation not in ('start', 'pause', 'resume', 'stop'):
            raise ValueError('无效任务操作')
        with self.store.connect() as db:
            row = db.execute("SELECT status FROM campaigns c WHERE id=%s AND NOT EXISTS(SELECT 1 FROM whale_task_runs w WHERE w.campaign_id=c.id) AND NOT (c.sources ? 'rss') AND (EXISTS(SELECT 1 FROM serp_task_options o WHERE o.campaign_id=c.id) OR EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id) OR EXISTS(SELECT 1 FROM serp_campaign_requests cr WHERE cr.campaign_id=c.id)) FOR UPDATE", (cid,)).fetchone()
            if not row:
                return None
            state = row['status']
            valid = {'start': ('paused', 'stopped', 'failed'), 'resume': ('paused',), 'pause': ('active',), 'stop': ('active', 'paused', 'failed')}
            if state not in valid[operation]:
                raise ValueError('当前状态不能执行此操作')
            status = {'start': 'active', 'resume': 'active', 'pause': 'paused', 'stop': 'stopped'}[operation]
            db.execute('UPDATE campaigns SET status=%s,last_error=NULL,updated_at=now() WHERE id=%s', (status, cid))
            if status == 'active':
                db.execute('UPDATE serp_task_options SET started=true WHERE campaign_id=%s', (cid,))
        return dict(campaign_id=cid, status=status)

    @staticmethod
    def filters(f):
        clauses, params = [], []
        if f.get('source'):
            if f['source'] not in GOOGLE_SOURCES:
                raise ValueError('无效来源')
            clauses.append('r.source=%s'); params.append(f['source'])
        if f.get('campaign_id'):
            clauses.append('EXISTS(SELECT 1 FROM serp_campaign_requests c WHERE c.request_id=r.request_id AND c.campaign_id=%s)'); params.append(str(UUID(f['campaign_id'])))
        if f.get('status'):
            if f['status'] not in ('success', 'error'):
                raise ValueError('无效请求状态')
            clauses.append("r.classification " + ('IN' if f['status'] == 'success' else 'NOT IN') + " ('results','empty')")
        for key, operator in (('since', '>='), ('until', '<')):
            if f.get(key):
                d = datetime.fromisoformat(f[key].replace('Z', '+00:00'))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                clauses.append('r.completed_at'+operator+'%s'); params.append(d)
        return clauses, params

    def browse_query(self, f, mode):
        clauses, params = self.filters(f)
        if mode == 'requests':
            if f.get('q'):
                clauses.append('r.query ILIKE %s'); params.append('%'+f['q']+'%')
            if f.get('new_only') == 'true':
                clauses.append("(r.response->>'new_unique_links')::int>0")
            projection = "r.request_id id,r.query,r.source,r.classification status,r.completed_at,r.http_status,r.error_code,r.response"
            tail = ' FROM serp_requests r'
        else:
            # Trends are topics, including topics without any article link.
            tail = " FROM serp_requests r CROSS JOIN LATERAL jsonb_array_elements(CASE r.source WHEN 'google_news' THEN coalesce(r.response->'news_results','[]'::jsonb) WHEN 'google_trends' THEN coalesce(r.response->'trending_searches','[]'::jsonb) ELSE coalesce(r.response->'organic_results','[]'::jsonb) END) WITH ORDINALITY AS x(item,ordinal)"
            projection = 'r.request_id id,r.query,r.source,r.completed_at,x.item data,x.ordinal'
            if f.get('q'):
                clauses.append("(r.query ILIKE %s OR x.item->>'title' ILIKE %s OR x.item->>'keyword' ILIKE %s OR x.item->>'snippet' ILIKE %s OR x.item->>'link' ILIKE %s)")
                params.extend(['%'+f['q']+'%']*5)
            if f.get('new_only') == 'true':
                clauses.append("(x.item->>'is_new_url'='true' OR EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(x.item->'articles','[]'::jsonb)) a WHERE a->>'is_new_url'='true'))")
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        order = ' ORDER BY r.completed_at DESC,r.request_id DESC' + (',x.ordinal' if mode == 'results' else '')
        return 'SELECT '+projection+tail+where, params, order, tail+where

    def browse(self, f, mode='results'):
        limit, offset = min(max(int(f.get('limit', 25)), 1), 100), max(int(f.get('offset', 0)), 0)
        query, params, order, tail = self.browse_query(f, mode)
        with self.store.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            total = db.execute('SELECT count(*) n'+tail, params).fetchone()['n']
            rows = db.execute(query+order+' LIMIT %s OFFSET %s', (*params, limit, offset)).fetchall()
        return dict(rows=rows, total=total, limit=limit, offset=offset)

    def export(self, f, mode='results'):
        query, params, order, _ = self.browse_query(f, mode)
        with self.store.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            with db.cursor(name='serp_export') as cursor:
                cursor.execute(query+order, params)
                while rows := cursor.fetchmany(200):
                    yield from rows

    def overview(self):
        stats = self.stats()
        for counts in [stats['requests'], *stats['sources'].values()]:
            counts['failed'] = counts['attempts'] - counts['successful']
            counts['success_rate'] = counts['successful'] / counts['attempts'] if counts['attempts'] else None
        with self.store.connect() as db:
            stats['errors'] = db.execute("SELECT source,classification,error_code,count(*) count FROM serp_requests WHERE completed_at>=now()-interval '24 hours' AND classification NOT IN ('results','empty') GROUP BY source,classification,error_code ORDER BY count(*) DESC").fetchall()
            stats['recent_errors'] = db.execute("SELECT request_id id,source,query,classification,error_code,http_status,completed_at FROM serp_requests WHERE classification NOT IN ('results','empty') ORDER BY completed_at DESC LIMIT 15").fetchall()
            stats['task_statuses'] = db.execute('SELECT status,count(*) count FROM campaigns GROUP BY status').fetchall()
        return stats
