"""Tenant-owned Maps jobs with reserved quota and expiring result snapshots."""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from datetime import datetime, timezone
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .maps_provider import availability, prepare, TTL
from .product import ProductError
from .serp_console import REGIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_local_jobs (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id), key_id uuid REFERENCES sf_keys(id),
 idempotency_key text NOT NULL, fingerprint char(64) NOT NULL, parameters jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
 result_cache jsonb, result_expires_at timestamptz, provider_request_id text, response_hash char(64),
 field_mask text, sku text, error_code text, quota_month date NOT NULL, charged integer NOT NULL DEFAULT 0 CHECK(charged IN(0,1)),
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 UNIQUE(account_id,idempotency_key)
);
ALTER TABLE sf_google_local_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE sf_google_local_jobs ADD COLUMN IF NOT EXISTS middle_state jsonb;
CREATE INDEX IF NOT EXISTS sf_google_local_jobs_owner_time ON sf_google_local_jobs(account_id,created_at DESC);
CREATE INDEX IF NOT EXISTS sf_google_local_jobs_pending ON sf_google_local_jobs(status) WHERE status IN('queued','running');
CREATE TABLE IF NOT EXISTS sf_google_local_refs (
 account_id uuid NOT NULL REFERENCES sf_accounts(id), place_id text NOT NULL,
 first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,place_id)
);
CREATE TABLE IF NOT EXISTS sf_google_local_audit (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES sf_accounts(id), job_id uuid,
 operation text NOT NULL, status text NOT NULL, provider_request_id text, response_hash char(64),
 field_mask text NOT NULL, sku text NOT NULL, error_code text, created_at timestamptz NOT NULL DEFAULT now()
);
"""


def _id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ProductError("invalid_id", "无效的 Maps 任务编号") from None


def parameters(payload):
    allowed = {"mode", "query", "region", "language", "city", "location", "radius_m", "included_type", "max_places"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ProductError("invalid_parameter", "Maps 查询包含不支持的参数")
    mode = payload.get("mode", "text")
    query = payload.get("query", "")
    region, language = payload.get("region", "US"), payload.get("language", "en")
    city, included_type = payload.get("city", ""), payload.get("included_type", "")
    if (not all(isinstance(v, str) for v in (mode, region, language))
            or mode not in ("text", "nearby") or region not in REGIONS or language not in ("en", "zh")):
        raise ProductError("invalid_parameter", "不支持此 Maps 模式、地区或语言")
    if not isinstance(query, str) or (mode == "text" and not 1 <= len(query.strip()) <= 300):
        raise ProductError("invalid_query", "文本搜索需要 1–300 个字符的关键词")
    if not isinstance(city, str) or len(city.strip()) > 120 or not isinstance(included_type, str) or len(included_type.strip()) > 80:
        raise ProductError("invalid_parameter", "城市或商家类型无效")
    location = payload.get("location")
    if location is not None:
        if not isinstance(location, dict) or set(location) != {"lat", "lng"} or type(location["lat"]) not in (int, float) or type(location["lng"]) not in (int, float):
            raise ProductError("invalid_parameter", "中心点必须包含数字 lat/lng")
        if not -90 <= location["lat"] <= 90 or not -180 <= location["lng"] <= 180:
            raise ProductError("invalid_parameter", "中心点超出经纬度范围")
        location = {"lat": float(location["lat"]), "lng": float(location["lng"])}
    if mode == "nearby" and (not included_type.strip() or (not location and not city.strip())):
        raise ProductError("invalid_parameter", "附近搜索必须提供商家类型，以及城市或中心点")
    radius = payload.get("radius_m", 5000)
    if type(radius) is not int or not 1 <= radius <= 50000:
        raise ProductError("invalid_parameter", "半径必须为 1–50000 米的整数")
    if any(ord(ch)<32 for ch in query+city+included_type):
        raise ProductError('invalid_query','关键词、城市或类型不能包含控制字符')
    max_places=payload.get('max_places')
    if max_places is not None and (type(max_places) is not int or not 1<=max_places<=100):
        raise ProductError('invalid_parameter','商家数量必须为1–100之间的整数')
    result = {"mode": mode, "query": query.strip(), "region": region, "language": language, "city": city.strip(), "location": location, "radius_m": radius, "included_type": included_type.strip()}
    if max_places is not None:result["max_places"]=max_places
    return result


class LocalJobStore:
    def __init__(self, base):
        self.base = base

    def submit(self, account, payload, idem, *, _db=None):
        if not availability()["enabled"]:
            raise ProductError("google_places_disabled", "地图自建查询尚未启用", 503)
        p = parameters(payload)
        from .maps_provider import CITIES
        if p.get('location') or p.get('city') in CITIES:
            p = prepare(p)
        if not isinstance(idem, str) or not 1 <= len(idem) <= 120 or re.search(r"[\x00-\x1f]", idem):
            raise ProductError("idempotency_required", "请提供 1–120 字符的 Idempotency-Key")
        fingerprint = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        aid, jid = account["id"], str(uuid4())
        with (nullcontext(_db) if _db is not None else self.base.connect()) as db:
            a = db.execute("SELECT * FROM sf_accounts WHERE id=%s AND enabled FOR UPDATE", (aid,)).fetchone()
            if not a:
                raise ProductError("unauthorized", "空间已停用", 401)
            old = db.execute("SELECT id,fingerprint FROM sf_google_local_jobs WHERE account_id=%s AND idempotency_key=%s", (aid, idem)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ProductError("idempotency_conflict", "此幂等键已用于另一组 Maps 参数", 409)
                return str(old["id"]), True
            # Single Maps runner: reject excess queueing before reserving quota.
            # Four admitted jobs can reach execution inside the five-minute
            # queue deadline even at the 75-second provider timeout.
            db.execute('SELECT pg_advisory_xact_lock(20261112,22)')
            global_active = db.execute("SELECT count(*) n FROM sf_google_local_jobs WHERE status IN('queued','running')").fetchone()['n']
            if global_active >= 4:
                raise ProductError('maps_queue_full', '地图队列繁忙，请稍后沿用原幂等键重试', 429)
            active = db.execute("SELECT count(*) n FROM sf_google_local_jobs WHERE account_id=%s AND status IN('queued','running')", (aid,)).fetchone()["n"]
            web_active = db.execute("SELECT count(*) n FROM sf_jobs WHERE account_id=%s AND status IN('queued','running')", (aid,)).fetchone()["n"]
            if active >= 2 or active+web_active >= a["concurrency"]:
                raise ProductError("concurrency_limit", "Maps 实时查询并发已满", 429)
            month = db.execute("SELECT date_trunc('month',now() AT TIME ZONE 'UTC')::date m").fetchone()["m"]
            db.execute("INSERT INTO sf_platform_months(month) VALUES(%s) ON CONFLICT DO NOTHING", (month,))
            if not db.execute("UPDATE sf_platform_months SET accepted=accepted+1 WHERE month=%s AND accepted<100000 RETURNING accepted", (month,)).fetchone():
                raise ProductError("platform_request_budget", "平台本月新任务已达上限", 429)
            db.execute("INSERT INTO sf_months(account_id,month) VALUES(%s,%s) ON CONFLICT DO NOTHING", (aid, month))
            if not db.execute("UPDATE sf_months SET reserved=reserved+1 WHERE account_id=%s AND month=%s AND used+reserved<%s RETURNING reserved", (aid, month, a["monthly_limit"])).fetchone():
                raise ProductError("quota_exhausted", "本月数据请求额度已用尽", 429)
            db.execute("INSERT INTO sf_google_local_jobs(id,account_id,key_id,idempotency_key,fingerprint,parameters,status,quota_month) VALUES(%s,%s,%s,%s,%s,%s,'queued',%s)", (jid, aid, account.get("key_id"), idem, fingerprint, Jsonb(p), month))
        return jid, False

    def job(self, aid, jid, *, include_results=False):
        columns = "id,parameters,status,error_code,sku,field_mask,created_at,completed_at,result_expires_at,charged,provider_request_id,response_hash"
        if include_results:
            columns += ",result_cache"
        with self.base.connect() as db:
            row = db.execute(f"SELECT {columns} FROM sf_google_local_jobs WHERE id=%s AND account_id=%s", (_id(jid), aid)).fetchone()
        if not row:
            raise ProductError("not_found", "Maps 任务不存在", 404)
        result = dict(row)
        result["id"] = str(result["id"])
        if include_results and result["status"] == "succeeded":
            if not result.get("result_cache") or result["result_expires_at"] <= datetime.now(timezone.utc):
                raise ProductError("results_expired", "实时 Maps 结果已过期，请重新查询", 410)
        return result

    def save_middle_state(self, job, state):
        """Private durable protocol state; never included in customer job output."""
        with self.base.connect() as db:
            row = db.execute("UPDATE sf_google_local_jobs SET middle_state=%s WHERE id=%s AND account_id=%s AND status='running' RETURNING id",
                             (Jsonb(state), job['id'], job['account_id'])).fetchone()
            if not row:
                raise ProductError('middle_job_not_active', '地图任务已结束，不能继续中台操作', 409)

    def finish(self, job, result=None, error=None):
        status, charge = ("succeeded", 1) if result else ("failed", 0)
        with self.base.connect() as db:
            row = db.execute("SELECT * FROM sf_google_local_jobs WHERE id=%s AND status='running' FOR UPDATE", (job["id"],)).fetchone()
            if not row:
                return
            db.execute("UPDATE sf_months SET reserved=reserved-1,used=used+%s WHERE account_id=%s AND month=%s", (charge, row["account_id"], row["quota_month"]))
            if result:
                cache = {k:result[k] for k in ('places','provider','provider_version','ordering','middle_verified','notice')}
                for key in ('location_resolution','search_area'):
                    if result.get(key) is not None:cache[key]=result[key]
                cache['attribution'] = 'Google Maps'
                db.execute("UPDATE sf_google_local_jobs SET status=%s,result_cache=%s,result_expires_at=now()+%s*interval '1 second',provider_request_id=%s,response_hash=%s,field_mask=%s,sku=%s,charged=1,completed_at=now() WHERE id=%s", (status, Jsonb(cache), TTL, result["provider_request_id"], result["response_hash"], result["field_mask"], result["sku"], row["id"]))
            else:
                db.execute("UPDATE sf_google_local_jobs SET status='failed',error_code=%s,charged=0,completed_at=now() WHERE id=%s", (error, row["id"]))
            db.execute("INSERT INTO sf_google_local_audit(id,account_id,job_id,operation,status,provider_request_id,response_hash,field_mask,sku,error_code) VALUES(%s,%s,%s,'search',%s,%s,%s,%s,%s,%s)", (str(uuid4()), row["account_id"], row["id"], status, result.get("provider_request_id") if result else None, result.get("response_hash") if result else None, result.get("field_mask") if result else "places-list-v1", result.get("sku") if result else "unknown", error))

