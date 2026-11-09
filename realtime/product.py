"""Customer quotas, durable search jobs and opt-in keyword monitors.

Only sf_* tables are migrated here. Legacy article migrations and collector
settings are deliberately not initialized or changed by this product layer.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .campaign_store import CampaignStore
from .serp_console import NEWS_LOCALES, REGIONS
from .serp_fields import MODULES, normalize_serp_url, public_fields

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_platform_months (
 month date PRIMARY KEY, accepted integer NOT NULL DEFAULT 0 CHECK(accepted>=0)
);
CREATE TABLE IF NOT EXISTS sf_accounts (
 id uuid PRIMARY KEY, name text NOT NULL, monthly_limit integer NOT NULL CHECK(monthly_limit BETWEEN 1 AND 100000),
 concurrency integer NOT NULL DEFAULT 3 CHECK(concurrency BETWEEN 1 AND 10),
 enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sf_keys (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id), name text NOT NULL,
 digest char(64) NOT NULL UNIQUE, prefix text NOT NULL, revoked boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), last_used_at timestamptz
);
CREATE TABLE IF NOT EXISTS sf_months (
 account_id uuid NOT NULL REFERENCES sf_accounts(id), month date NOT NULL,
 used integer NOT NULL DEFAULT 0 CHECK(used>=0), reserved integer NOT NULL DEFAULT 0 CHECK(reserved>=0),
 PRIMARY KEY(account_id,month)
);
CREATE TABLE IF NOT EXISTS sf_rate_windows (
 account_id uuid NOT NULL REFERENCES sf_accounts(id), minute timestamptz NOT NULL,
 requests integer NOT NULL DEFAULT 0, PRIMARY KEY(account_id,minute)
);
CREATE TABLE IF NOT EXISTS sf_monitors (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id), name text NOT NULL,
 parameters jsonb NOT NULL, domain text NOT NULL, competitors jsonb NOT NULL DEFAULT '[]',
 interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 3600 AND 604800),
 enabled boolean NOT NULL DEFAULT false, next_run_at timestamptz NOT NULL DEFAULT now(),
 last_error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sf_jobs (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id),
 monitor_id uuid REFERENCES sf_monitors(id), key_id uuid REFERENCES sf_keys(id),
 idempotency_key text NOT NULL, fingerprint char(64) NOT NULL, parameters jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 campaign_id uuid REFERENCES campaigns(id), response jsonb, error_code text,
 quota_month date NOT NULL, charged integer NOT NULL DEFAULT 0 CHECK(charged IN(0,1)),
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, enqueued_at timestamptz,
 deadline_at timestamptz NOT NULL DEFAULT (now()+interval '15 minutes'),
 UNIQUE(account_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS sf_jobs_owner_time ON sf_jobs(account_id,created_at DESC);
CREATE INDEX IF NOT EXISTS sf_jobs_pending ON sf_jobs(status) WHERE status IN('queued','running');
CREATE INDEX IF NOT EXISTS sf_jobs_monitor_time ON sf_jobs(monitor_id,created_at DESC);
CREATE INDEX IF NOT EXISTS sf_monitors_due ON sf_monitors(next_run_at) WHERE enabled;
"""


class ProductError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.status = code, status
        super().__init__(message)


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError):
        raise ProductError('invalid_id', '无效的记录编号') from None


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ProductError('invalid_parameter', f'{label}必须为 {low}–{high} 的整数')
    return value


def domain_name(value):
    if not isinstance(value, str):
        raise ProductError('invalid_domain', '请输入域名，例如 example.com')
    try:
        p = urlsplit(value if '://' in value else 'https://' + value)
        host = (p.hostname or '').rstrip('.').lower().encode('idna').decode()
        if p.username or p.password or p.port or '.' not in host or len(host) > 253:
            raise ValueError()
        if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in host.split('.')):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP is not a domain')
        return host.removeprefix('www.')
    except (ValueError, UnicodeError):
        raise ProductError('invalid_domain', '请输入有效域名，例如 example.com') from None


def parameters(payload):
    if not isinstance(payload, dict):
        raise ProductError('invalid_parameter', '请求必须是 JSON 对象')
    if set(payload) - {'query', 'source', 'region', 'language', 'provider'}:
        raise ProductError('unsupported_parameter', '当前支持 query、source、region、language、provider；城市、设备和深度扩展尚未开放')
    query = payload.get('query', '')
    source = payload.get('source', 'google_web')
    region = payload.get('region', 'US')
    language = payload.get('language', 'en')
    provider = payload.get('provider', 'openserp')
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ProductError('invalid_query', '关键词必须为 1–300 个字符')
    if source not in ('google_web', 'google_news', 'google_trends', 'google_images') or region not in REGIONS or language not in ('en', 'zh'):
        raise ProductError('invalid_parameter', '不支持此数据源、地区或语言')
    if provider not in ('openserp', 'serper') or (provider == 'serper' and source != 'google_web'):
        raise ProductError('unsupported_provider', '当前提供方不支持所选数据源')
    if source == 'google_news' and (region, language) not in {(x.split(':')[0], 'zh' if ':zh' in x else 'en') for x in NEWS_LOCALES if x.endswith(':en') or ':zh' in x}:
        raise ProductError('unsupported_locale', '当前新闻 RSS 不支持此地区和语言组合')
    return dict(query=query.strip(), source=source, region=region, language=language, provider=provider, depth=20 if source == 'google_images' else 10)


def canonical_parameters(payload):
    # Stored parameters include the explicit fixed depth, not an advertised option.
    return parameters({k: v for k, v in payload.items() if k != 'depth'})


def capability_payload():
    return {
        'schema_version': 'searchflow-v1', 'search_mode': 'asynchronous',
        'regions': list(REGIONS), 'languages': ['en', 'zh'], 'depth': [10],
        'sources': [
            {'id':'google_images','label':'Google Images','method':'openserp_google_images','depth':[20],'rank_type':'image_grid_order','media_download':False},
            {'id': 'google_web', 'label': 'Google Web', 'method': 'openserp', 'rank_type': 'observed_organic'},
            {'id': 'google_news', 'label': 'Google News', 'method': 'google_news_rss', 'rank_type': 'feed_order'},
            {'id': 'google_trends', 'label': 'Trending Now', 'method': 'trending_now_rss', 'keyword_filter': False},
        ],
        'providers': [{'id': 'openserp', 'enabled': True}, {'id': 'serper', 'enabled': bool(os.getenv('SERPER_API_KEY'))}],
        'modules': {m: 'upstream_if_provided' for m in MODULES},
        'unsupported': ['city_targeting', 'device_selection', 'ai_mode', 'historical_trends', 'search_volume', 'live_sla'],
        'freshness': '保留原始采样时间与缓存标记；OpenSERP 使用既有共享缓存，不保证每次新采。',
        'quota': '每个成功搜索任务扣 1 次；失败和取消释放预留；这是用量额度，不是支付账单。',
        'rate_limit_per_minute': 60, 'platform_new_jobs_per_utc_month': 100000,
        'data_api': {'version': 'searchflow-data-1', 'field_contracts': ['legacy', 'prd-v1'],
                     'fields_url': '/v1/data/fields', 'requests_url': '/v1/data/requests',
                     'job_data_url': '/v1/jobs/{id}/data', 'snapshot_export_url': '/v1/data/export',
                     'scopes': ['own', 'public', 'accessible'], 'full_export': ['csv', 'jsonl', 'zip'],
                     'quality_url': '/v1/data/quality', 'analytics_scope': 'public_history',
                     'source_admission': 'legacy_saved_data_not_formal_whale_reader'},
    }


class ProductStore:
    def __init__(self, dsn):
        self.base = CampaignStore(dsn, initialize=False)

    def initialize(self):
        with self.base.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261027,1)')
            db.execute(SCHEMA)
            from .analytics import initialize_projection
            initialize_projection(db)
            db.execute("INSERT INTO sf_platform_months(month,accepted) SELECT quota_month,count(*) FROM sf_jobs GROUP BY quota_month ON CONFLICT(month) DO UPDATE SET accepted=greatest(sf_platform_months.accepted,EXCLUDED.accepted)")

    @staticmethod
    def _key(db, account_id, name):
        secret = 'sf_' + secrets.token_urlsafe(32)
        kid = str(uuid4())
        db.execute('INSERT INTO sf_keys(id,account_id,name,digest,prefix) VALUES(%s,%s,%s,%s,%s)',
                   (kid, account_id, name, digest(secret), secret[:11]))
        return dict(id=kid, api_key=secret, prefix=secret[:11])

    def create_account(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'name', 'monthly_limit', 'concurrency'}:
            raise ProductError('invalid_parameter', '无效的空间参数')
        name = payload.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ProductError('invalid_name', '空间名称必须为 1–80 字符')
        limit = integer(payload.get('monthly_limit', 1000), 1, 100000, '月额度')
        concurrency = integer(payload.get('concurrency', 3), 1, 10, '并发数')
        aid = str(uuid4())
        with self.base.connect() as db:
            # One platform budget, including all customers; historical internal jobs stay separate.
            db.execute('SELECT pg_advisory_xact_lock(20261027,2)')
            total = db.execute('SELECT coalesce(sum(monthly_limit),0) n FROM sf_accounts WHERE enabled').fetchone()['n']
            if total + limit > 100000:
                raise ProductError('platform_budget', '空间分配总额度不能超过每月 100,000 次', 409)
            db.execute('INSERT INTO sf_accounts(id,name,monthly_limit,concurrency) VALUES(%s,%s,%s,%s)', (aid, name.strip(), limit, concurrency))
            key = self._key(db, aid, '初始密钥')
        return dict(account_id=aid, name=name.strip(), monthly_limit=limit, concurrency=concurrency, **key)

    def accounts(self):
        with self.base.connect() as db:
            return db.execute("SELECT a.*,coalesce(m.used,0) used,coalesce(m.reserved,0) reserved FROM sf_accounts a LEFT JOIN sf_months m ON m.account_id=a.id AND m.month=date_trunc('month',now() AT TIME ZONE 'UTC')::date ORDER BY a.created_at DESC").fetchall()

    def authenticate(self, secret, rate_limit=True):
        if not isinstance(secret, str) or not secret or len(secret) > 200:
            raise ProductError('unauthorized', '请提供有效的 API 密钥', 401)
        with self.base.connect() as db:
            row = db.execute('SELECT a.*,k.id key_id FROM sf_keys k JOIN sf_accounts a ON a.id=k.account_id WHERE k.digest=%s AND NOT k.revoked AND a.enabled', (digest(secret),)).fetchone()
            if not row:
                raise ProductError('unauthorized', '密钥无效、已撤销或空间已停用', 401)
            if rate_limit:
                count = db.execute("INSERT INTO sf_rate_windows(account_id,minute,requests) VALUES(%s,date_trunc('minute',now()),1) ON CONFLICT(account_id,minute) DO UPDATE SET requests=sf_rate_windows.requests+1 RETURNING requests", (row['id'],)).fetchone()['requests']
                if count > 60:
                    # Commit the rate window rather than rolling it back with the error.
                    db.commit()
                    raise ProductError('rate_limited', '每个空间每分钟最多 60 次 API 请求，请稍后重试', 429)
            db.execute('UPDATE sf_keys SET last_used_at=now() WHERE id=%s', (row['key_id'],))
            return row

    def keys(self, aid):
        with self.base.connect() as db:
            return db.execute('SELECT id,name,prefix,revoked,created_at,last_used_at FROM sf_keys WHERE account_id=%s ORDER BY created_at DESC', (aid,)).fetchall()

    def issue_key(self, aid, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ProductError('invalid_name', '密钥名称必须为 1–80 字符')
        with self.base.connect() as db:
            return self._key(db, aid, name.strip())

    def revoke(self, aid, kid):
        with self.base.connect() as db:
            row = db.execute('UPDATE sf_keys SET revoked=true WHERE id=%s AND account_id=%s RETURNING id', (identifier(kid), aid)).fetchone()
            if not row:
                raise ProductError('not_found', '密钥不存在', 404)

    def usage(self, aid):
        with self.base.connect() as db:
            a = db.execute('SELECT name,monthly_limit,concurrency FROM sf_accounts WHERE id=%s', (aid,)).fetchone()
            m = db.execute("SELECT used,reserved FROM sf_months WHERE account_id=%s AND month=date_trunc('month',now() AT TIME ZONE 'UTC')::date", (aid,)).fetchone() or dict(used=0, reserved=0)
            states = db.execute("SELECT status,count(*) n FROM sf_jobs WHERE account_id=%s AND created_at>=date_trunc('month',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' GROUP BY status", (aid,)).fetchall()
            forecast = db.execute('SELECT coalesce(sum(ceil(2592000.0/interval_seconds)),0) n FROM sf_monitors WHERE account_id=%s AND enabled', (aid,)).fetchone()['n']
            return dict(**a, **m, remaining=max(0,a['monthly_limit']-m['used']-m['reserved']), month=now().strftime('%Y-%m'),
                        statuses={r['status']:r['n'] for r in states}, monitor_monthly_estimate=int(forecast))

    def submit(self, account, payload, idem, config, monitor_id=None, *, _db=None):
        p = canonical_parameters(payload) if monitor_id else parameters(payload)
        if p['provider'] == 'serper' and not os.getenv('SERPER_API_KEY'):
            raise ProductError('provider_not_configured', 'Serper 尚未配置服务端密钥', 409)
        if not isinstance(idem, str) or not 1 <= len(idem) <= 120 or re.search(r'[\x00-\x1f]', idem):
            raise ProductError('idempotency_required', '请提供 1–120 字符的 Idempotency-Key')
        fingerprint = digest(json.dumps(p, sort_keys=True))
        aid = account['id']
        jid, cid = str(uuid4()), str(uuid4()) if p['provider'] == 'openserp' else None
        with (nullcontext(_db) if _db is not None else self.base.connect()) as db:
            a = db.execute('SELECT * FROM sf_accounts WHERE id=%s AND enabled FOR UPDATE', (aid,)).fetchone()
            if not a:
                raise ProductError('unauthorized', '空间已停用', 401)
            if monitor_id:
                monitor = db.execute('SELECT enabled FROM sf_monitors WHERE id=%s AND account_id=%s FOR UPDATE', (monitor_id, aid)).fetchone()
                if not monitor or not monitor['enabled']:
                    raise ProductError('monitor_paused', '监测已暂停', 409)
            existing = db.execute('SELECT id,fingerprint FROM sf_jobs WHERE account_id=%s AND idempotency_key=%s', (aid, idem)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise ProductError('idempotency_conflict', '此幂等键已用于另一组搜索参数', 409)
                return str(existing['id']), True
            active = db.execute("SELECT count(*) n FROM sf_jobs WHERE account_id=%s AND status IN('queued','running')", (aid,)).fetchone()['n']
            if active >= a['concurrency']:
                raise ProductError('concurrency_limit', '空间并发任务已满，请等待现有任务结束', 429)
            month = db.execute("SELECT date_trunc('month',now() AT TIME ZONE 'UTC')::date m").fetchone()['m']
            db.execute('INSERT INTO sf_platform_months(month) VALUES(%s) ON CONFLICT DO NOTHING',(month,))
            budget=db.execute('UPDATE sf_platform_months SET accepted=accepted+1 WHERE month=%s AND accepted<100000 RETURNING accepted',(month,)).fetchone()
            if not budget:
                raise ProductError('platform_request_budget','平台本月 API 新任务已达 100,000 次验证上限',429)
            db.execute('INSERT INTO sf_months(account_id,month) VALUES(%s,%s) ON CONFLICT DO NOTHING', (aid,month))
            reserved = db.execute('UPDATE sf_months SET reserved=reserved+1 WHERE account_id=%s AND month=%s AND used+reserved<%s RETURNING reserved', (aid,month,a['monthly_limit'])).fetchone()
            if not reserved:
                raise ProductError('quota_exhausted', '本月搜索额度已用尽', 429)
            if cid:
                opts = dict(customer_private=True, web_region=p['region'], web_language=p['language'], web_pages=1, trends_geo=p['region'])
                candidates = [x for x in NEWS_LOCALES if x.startswith(p['region']+':') and ((p['language']=='zh' and ':zh' in x) or (p['language']=='en' and x.endswith(':en')))]
                if candidates:
                    opts['news_locale'] = candidates[0]
                # Campaign and ownership commit together: no unowned in-flight task window.
                db.execute("INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) VALUES(%s,%s,'[]',1000000,%s,%s,'active')", (cid,p['query'],config.default_proxy_profile,Jsonb([p['source']])))
                db.execute("INSERT INTO serp_task_options(campaign_id,options,started,mode,max_successful_searches) VALUES(%s,%s,true,'once',1)", (cid,Jsonb(opts)))
            db.execute("INSERT INTO sf_jobs(id,account_id,monitor_id,key_id,idempotency_key,fingerprint,parameters,status,campaign_id,quota_month) VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s)",
                       (jid,aid,monitor_id,account.get('key_id'),idem,fingerprint,Jsonb(p),cid,month))
        return jid, False

    def submit_batch(self, account, payload, idem, config):
        if not isinstance(payload, dict) or set(payload) != {'searches'}:
            raise ProductError('invalid_parameter', '批量请求仅支持 searches 数组')
        searches = payload['searches']
        if not isinstance(searches, list) or not 1 <= len(searches) <= 20:
            raise ProductError('invalid_parameter', '每批 1–20 个搜索，仍受客户并发与额度限制')
        if not isinstance(idem, str) or not 1 <= len(idem) <= 100 or re.search(r'[\x00-\x1f]', idem):
            raise ProductError('idempotency_required', '批量请求需 1–100 字符的 Idempotency-Key')
        normalized = [parameters(p) for p in searches]
        # Bind every child key to the whole payload through its fingerprint.
        # Existing child keys with a changed batch are rejected, even if a
        # particular child retained the same search parameters.
        fingerprint = digest(json.dumps(normalized, sort_keys=True))
        result = []
        with self.base.connect() as db:
            db.execute('SELECT id FROM sf_accounts WHERE id=%s FOR UPDATE', (account['id'],))
            prefix = 'batch:' + digest(idem)[:32] + ':'
            previous = db.execute('SELECT idempotency_key FROM sf_jobs WHERE account_id=%s AND idempotency_key LIKE %s',
                                  (account['id'], prefix + '%')).fetchall()
            if previous and any(not r['idempotency_key'].startswith(prefix + fingerprint + ':') for r in previous):
                raise ProductError('idempotency_conflict', '批量幂等键已用于另一组参数', 409)
            for index, p in enumerate(searches):
                jid, reused = self.submit(account, p, prefix + fingerprint + ':' + str(index), config, _db=db)
                result.append(dict(id=jid, reused=reused, status_url='/v1/jobs/' + jid))
        return dict(jobs=result, atomic=True, note='整批成功保存或整批回滚；不绕过并发或额度限制。')

    def job_action(self, aid, jid, operation):
        if operation not in ('pause', 'resume'):
            raise ProductError('invalid_parameter', '无效任务操作')
        with self.base.connect() as db:
            row = db.execute('SELECT status,campaign_id,deadline_at FROM sf_jobs WHERE id=%s AND account_id=%s FOR UPDATE',
                             (identifier(jid), aid)).fetchone()
            if not row:
                raise ProductError('not_found', '任务不存在', 404)
            if row['status'] not in ('queued', 'running') or row['deadline_at'] <= now():
                raise ProductError('job_not_active', '任务已结束或期限已到，不能暂停或继续', 409)
            if not row['campaign_id']:
                raise ProductError('unsupported_provider', '此提供方不支持暂停或继续', 409)
            campaign = db.execute('SELECT status FROM campaigns WHERE id=%s FOR UPDATE', (row['campaign_id'],)).fetchone()
            if not campaign or campaign['status'] not in ('active', 'paused'):
                raise ProductError('job_not_active', '采集已结束，等待任务结算', 409)
            state = 'paused' if operation == 'pause' else 'active'
            db.execute('UPDATE campaigns SET status=%s,updated_at=now() WHERE id=%s', (state, row['campaign_id']))
        return dict(id=str(jid), status=row['status'], execution_state=state,
                    campaign_id=str(row['campaign_id']), deadline_at=row['deadline_at'],
                    note='暂停不延长 15 分钟任务期限；在途请求仍可完成。')

    def jobs(self, aid, limit=30):
        with self.base.connect() as db:
            return db.execute('SELECT id,monitor_id,parameters,status,error_code,created_at,completed_at,charged FROM sf_jobs WHERE account_id=%s ORDER BY created_at DESC,id DESC LIMIT %s', (aid,limit)).fetchall()

    def job(self, aid, jid):
        with self.base.connect() as db:
            row = db.execute('SELECT id,monitor_id,parameters,status,response,error_code,created_at,completed_at,charged FROM sf_jobs WHERE id=%s AND account_id=%s', (identifier(jid),aid)).fetchone()
        if not row:
            raise ProductError('not_found', '任务不存在', 404)
        return row

    def finish(self, jid, status, response=None, error=None):
        with self.base.connect() as db:
            j = db.execute("SELECT * FROM sf_jobs WHERE id=%s AND status IN('queued','running') FOR UPDATE", (jid,)).fetchone()
            if not j:
                return False
            charge = int(status == 'succeeded')
            db.execute('UPDATE sf_months SET reserved=reserved-1,used=used+%s WHERE account_id=%s AND month=%s', (charge,j['account_id'],j['quota_month']))
            db.execute('UPDATE sf_jobs SET status=%s,response=%s,error_code=%s,charged=%s,completed_at=now() WHERE id=%s', (status,Jsonb(response) if response is not None else None,error,charge,jid))
            if j['monitor_id']:
                db.execute('UPDATE sf_monitors SET last_error=%s WHERE id=%s',(error if status=='failed' else None,j['monitor_id']))
            if j['campaign_id']:
                db.execute("UPDATE campaigns SET status='stopped',updated_at=now() WHERE id=%s AND status IN('active','paused')", (j['campaign_id'],))
            return True

    def cancel(self, aid, jid):
        self.job(aid,jid)
        self.finish(identifier(jid),'cancelled',error='cancelled_by_customer')
        return self.job(aid,jid)

    def create_monitor(self, aid, payload):
        if not isinstance(payload,dict) or set(payload)-{'name','query','region','language','provider','domain','competitors','interval_seconds'}:
            raise ProductError('invalid_parameter','无效的监测参数')
        name = payload.get('name') or payload.get('query','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:
            raise ProductError('invalid_name','监测名称必须为 1–100 字符')
        p = parameters({k:v for k,v in payload.items() if k in ('query','region','language','provider')})
        if p['provider']=='serper' and not os.getenv('SERPER_API_KEY'):
            raise ProductError('provider_not_configured','Serper 尚未配置',409)
        domain = domain_name(payload.get('domain',''))
        competitors = payload.get('competitors',[])
        if not isinstance(competitors,list) or len(competitors)>10:
            raise ProductError('invalid_parameter','最多监测 10 个竞品域名')
        competitors = list(dict.fromkeys(domain_name(x) for x in competitors if x))
        interval = integer(payload.get('interval_seconds',86400),3600,604800,'监测间隔（秒）')
        mid = str(uuid4())
        with self.base.connect() as db:
            db.execute('SELECT id FROM sf_accounts WHERE id=%s FOR UPDATE',(aid,))
            count = db.execute('SELECT count(*) n FROM sf_monitors WHERE account_id=%s',(aid,)).fetchone()['n']
            if count>=100:
                raise ProductError('monitor_limit','每个空间最多 100 个监测',409)
            db.execute('INSERT INTO sf_monitors(id,account_id,name,parameters,domain,competitors,interval_seconds) VALUES(%s,%s,%s,%s,%s,%s,%s)',(mid,aid,name.strip(),Jsonb(p),domain,Jsonb(competitors),interval))
        return dict(id=mid,enabled=False,estimated_searches_per_month=math.ceil(2592000/interval))

    def monitors(self, aid):
        with self.base.connect() as db:
            return db.execute("SELECT m.*,coalesce(s.samples,0) samples,s.last_sample FROM sf_monitors m LEFT JOIN LATERAL (SELECT count(*) FILTER(WHERE status='succeeded') samples,max(completed_at) FILTER(WHERE status='succeeded') last_sample FROM sf_jobs WHERE monitor_id=m.id) s ON true WHERE m.account_id=%s ORDER BY m.created_at DESC",(aid,)).fetchall()

    def monitor_action(self, aid, mid, enabled):
        with self.base.connect() as db:
            db.execute('SELECT id FROM sf_accounts WHERE id=%s FOR UPDATE', (aid,))
            row=db.execute('UPDATE sf_monitors SET enabled=%s,next_run_at=now(),last_error=NULL WHERE id=%s AND account_id=%s RETURNING id,enabled',(enabled,identifier(mid),aid)).fetchone()
            if not row:
                raise ProductError('not_found','监测不存在',404)
            # Stop queued/active owned tasks too; never restore legacy automatic supply.
            jobs=db.execute("SELECT id FROM sf_jobs WHERE monitor_id=%s AND account_id=%s AND status IN('queued','running')",(mid,aid)).fetchall() if not enabled else []
        for j in jobs:
            self.finish(j['id'],'cancelled',error='monitor_paused')
        return row

    def monitor_report(self, aid, mid):
        with self.base.connect() as db:
            monitor=db.execute('SELECT * FROM sf_monitors WHERE id=%s AND account_id=%s',(identifier(mid),aid)).fetchone()
            if not monitor:
                raise ProductError('not_found','监测不存在',404)
            rows=db.execute('SELECT id,status,response,created_at,completed_at FROM sf_jobs WHERE account_id=%s AND monitor_id=%s ORDER BY created_at DESC LIMIT 100',(aid,mid)).fetchall()
        snapshots=[dict(id=str(r['id']),completed_at=r['response'].get('fetched_at'),provider=r['response'].get('provider'),response=r['response'].get('data',{})) for r in rows if r['status']=='succeeded' and r['response']]
        return dict(monitor=monitor,**compare_snapshots(snapshots,[monitor['domain'],*monitor['competitors']]),failed_samples=sum(r['status']=='failed' for r in rows))

    def history_analysis(self, **filters):
        from .analytics import history_report
        return history_report(self.base, **filters)

    def analytics_overview(self, **filters):
        from .analytics import overview_report
        return overview_report(self.base, **filters)


def compare_snapshots(snapshots, domains):
    """Compare only observed natural ranks; absence never invents a rank."""
    unique=[];seen=set()
    for s in snapshots:
        stamp=str(s['completed_at'])
        # Reused cache snapshots do not manufacture additional observations.
        identity=(stamp,s.get('provider'))
        if identity not in seen:
            seen.add(identity);unique.append(s)
    def items(s):
        values={}
        for r in s['response'].get('organic_results') or []:
            if not isinstance(r,dict) or not isinstance(r.get('link'),str):continue
            try:url=normalize_serp_url(r['link'])
            except ValueError:continue
            rank=r.get('position')
            rank=rank if type(rank) is int and rank>0 else None
            if url not in values or (rank is not None and (values[url].get('position') is None or rank<values[url]['position'])):
                values[url]=dict(link=url,title=r.get('title'),position=rank,domain=(urlsplit(url).hostname or '').removeprefix('www.'))
        return values
    latest=items(unique[0]) if unique else {}
    previous=items(unique[1]) if len(unique)>1 else {}
    changes=[]
    if len(unique)>1:
        for url in latest.keys()|previous.keys():
            a,b=previous.get(url),latest.get(url)
            if a and b and a['position']==b['position']:continue
            delta=a['position']-b['position'] if a and b and a['position'] is not None and b['position'] is not None else None
            changes.append(dict(url=url,title=(b or a)['title'],kind='new' if not a else 'missing' if not b else 'moved',previous_rank=a['position'] if a else None,current_rank=b['position'] if b else None,delta=delta))
    domain_counts={}
    timeline=[]
    for s in reversed(unique):
        observed=items(s);ranks={}
        for d in domains:
            rs=[x['position'] for x in observed.values() if (x['domain']==d or x['domain'].endswith('.'+d)) and x['position'] is not None]
            ranks[d]=min(rs) if rs else None
        timeline.append(dict(request_id=str(s['id']),fetched_at=s['completed_at'],ranks=ranks,results=len(observed)))
        for d in {x['domain'] for x in observed.values()}:
            domain_counts[d]=domain_counts.get(d,0)+1
    metrics=[]
    for d in domains:
        values=[x['ranks'][d] for x in timeline if x['ranks'][d] is not None]
        # Denominator includes only samples that actually supplied rank data, or explicit empty results.
        eligible=sum(not items(s) or all(v['position'] is not None for v in items(s).values()) or any((v['domain']==d or v['domain'].endswith('.'+d)) and v['position'] is not None for v in items(s).values()) for s in unique)
        metrics.append(dict(domain=d,observed_samples=len(values),eligible_samples=eligible,appearance_rate=round(len(values)/eligible,4) if eligible else None,best_rank=min(values) if values else None,latest_rank=timeline[-1]['ranks'][d] if timeline else None))
    union=set(latest)|set(previous)
    report = dict(samples=len(unique),latest=unique[0]['completed_at'] if unique else None,previous=unique[1]['completed_at'] if len(unique)>1 else None,
                changes=sorted(changes,key=lambda x:(x['kind'],x['url'])),timeline=timeline,domains=metrics,
                domain_presence=[dict(domain=d,samples=n) for d,n in sorted(domain_counts.items(),key=lambda x:(-x[1],x[0]))[:15]],
                result_overlap=round(len(set(latest)&set(previous))/len(union),4) if len(unique)>1 and union else None,
                latest_results=list(latest.values()),note='仅比较同来源和搜索条件的有效快照；未出现不代表第 11 名或从 Google 消失。历史样本最多 200 条，监测最近 100 次任务。')
    from .analytics import enrich_snapshots
    return enrich_snapshots(unique, domains, report)


class ProductService:
    """Durable reconciliation; one active coordinator via a PostgreSQL lock."""
    def __init__(self, config, queue):
        self.config,self.queue=config,queue
        self.store=ProductStore(config.database_url)

    def tick(self):
        with self.store.base.connect() as db:
            if not db.execute('SELECT pg_try_advisory_xact_lock(20261027,3) acquired').fetchone()['acquired']:
                return
            pending=db.execute("SELECT * FROM sf_jobs WHERE status IN('queued','running') ORDER BY created_at LIMIT 100").fetchall()
            for job in pending:
                if job['deadline_at']<now():
                    self.store.finish(job['id'],'failed',error='search_deadline_exceeded')
                    continue
                if job['parameters']['provider']=='serper':
                    if job['status']=='queued':
                        with self.store.base.connect() as claim:
                            claimed=claim.execute("UPDATE sf_jobs SET status='running' WHERE id=%s AND status='queued' RETURNING id",(job['id'],)).fetchone()
                        if claimed:self.run_serper(job)
                    continue
                response=db.execute("SELECT r.*,cr.reused FROM serp_campaign_requests cr JOIN serp_requests r USING(request_id) WHERE cr.campaign_id=%s AND r.classification IN('results','empty') ORDER BY r.completed_at LIMIT 1",(job['campaign_id'],)).fetchone()
                if response:
                    result=dict(schema_version='searchflow-v1',provider=response['provider'],fetched_at=response['completed_at'].isoformat(),cache_hit=response['reused'],data=public_fields(response['response']),origin_request_id=str(response['request_id']))
                    self.store.finish(job['id'],'succeeded',result)
                elif (campaign:=db.execute('SELECT status FROM campaigns WHERE id=%s',(job['campaign_id'],)).fetchone()) and campaign['status'] in ('completed','failed','stopped'):
                    failure=db.execute('SELECT error_code FROM serp_requests WHERE campaign_id=%s ORDER BY completed_at DESC LIMIT 1',(job['campaign_id'],)).fetchone()
                    self.store.finish(job['id'],'failed',error=(failure or {}).get('error_code') or 'upstream_no_successful_result')
                elif campaign and campaign['status'] == 'paused':
                    continue
                elif job['enqueued_at'] is None or (now()-job['enqueued_at']).total_seconds()>45:
                    self.queue.enqueue(str(job['campaign_id']),priority=True)
                    db.execute("UPDATE sf_jobs SET status='running',enqueued_at=now() WHERE id=%s AND status IN('queued','running')",(job['id'],))
            monitors=db.execute('SELECT m.*,a.enabled account_enabled FROM sf_monitors m JOIN sf_accounts a ON a.id=m.account_id WHERE m.enabled AND a.enabled AND m.next_run_at<=now() ORDER BY m.next_run_at LIMIT 10').fetchall()
            for m in monitors:
                # Serialize by account inside submit, including API traffic.
                try:
                    self.store.submit(dict(id=m['account_id']),m['parameters'],'monitor:'+str(m['id'])+':'+m['next_run_at'].isoformat(),self.config,m['id'])
                    db.execute('UPDATE sf_monitors SET next_run_at=now()+interval \'1 second\'*interval_seconds,last_error=NULL WHERE id=%s AND enabled',(m['id'],))
                except ProductError as exc:
                    db.execute("UPDATE sf_monitors SET next_run_at=now()+interval '5 minutes',last_error=%s WHERE id=%s",(str(exc),m['id']))
            db.execute("DELETE FROM sf_rate_windows WHERE minute<now()-interval '2 hours'")
        from .analytics import sync_projection
        sync_projection(self.store.base, limit=100)

    def run_serper(self, job):
        import requests
        p=job['parameters']
        try:
            r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':os.environ['SERPER_API_KEY']},json=dict(q=p['query'],gl=p['region'].lower(),hl=p['language'],num=10),timeout=(5,35))
            r.raise_for_status();raw=r.json()
            if not isinstance(raw,dict) or not isinstance(raw.get('organic'),list):
                raise ValueError('invalid upstream response')
            organic=[dict(title=x.get('title'),link=x.get('link'),snippet=x.get('snippet'),position=x.get('position')) for x in raw['organic'] if isinstance(x,dict)]
            data=dict(organic_results=organic,search_parameters=dict(q=p['query'],gl=p['region'].lower(),hl=p['language'],device=None),module_status={m:'unavailable' for m in MODULES})
            for upstream,field in [('knowledgeGraph','knowledge_graph'),('peopleAlsoAsk','related_questions'),('relatedSearches','related_searches')]:
                if upstream in raw:
                    data[field]=raw[upstream];data['module_status'][field]='present' if raw[upstream] else 'absent'
            self.store.finish(job['id'],'succeeded',dict(schema_version='searchflow-v1',provider='serper',fetched_at=now().isoformat(),cache_hit=False,data=public_fields(data)))
        except Exception:
            # Never log upstream bodies, headers or credentials; no automatic paid retry.
            self.store.finish(job['id'],'failed',error='upstream_unavailable')


_services={}
_lock=threading.Lock()


def start_service(config,queue):
    if os.getenv('SEARCHFLOW_PRODUCT_ENABLED')!='true':
        return
    with _lock:
        if config.database_url in _services:return
        service=ProductService(config,queue)
        service.store.initialize()
        def run():
            while True:
                try:service.tick()
                except Exception as exc:print('product coordinator: '+type(exc).__name__,flush=True)
                time.sleep(5)
        thread=threading.Thread(target=run,name='searchflow-product',daemon=True)
        _services[config.database_url]=service;thread.start()


def admin_secret():
    value=os.getenv('SEARCHFLOW_ADMIN_KEY')
    if value:return value
    p=Path(os.getenv('SEARCHFLOW_ADMIN_KEY_FILE','state/private/searchflow-admin-key'))
    return p.read_text().strip() if p.is_file() else ''


def provision_admin_key():
    p=Path(os.getenv('SEARCHFLOW_ADMIN_KEY_FILE','state/private/searchflow-admin-key'))
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with open(p,'x',opener=lambda path,flags:os.open(path,flags,0o600)) as f:f.write('sf_admin_'+secrets.token_urlsafe(40)+'\n')
    except FileExistsError:pass
    return p
