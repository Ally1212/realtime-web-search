"""Durable WML-only gardening run. Execute ticks inside the web container."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

RUN = "wml-gardening-20261006"
DIRECTORY = Path("/app/state") / RUN
ACTIVE_LIMIT = 1408
WORDS_PER_CAMPAIGN = 8

PLANTS = [
    ("番茄", "tomato"), ("黄瓜", "cucumber"), ("草莓", "strawberry"),
    ("蓝莓", "blueberry"), ("辣椒", "pepper"), ("茄子", "eggplant"),
    ("生菜", "lettuce"), ("菠菜", "spinach"), ("甘蓝", "kale"),
    ("萝卜", "radish"), ("胡萝卜", "carrot"), ("土豆", "potato"),
    ("红薯", "sweet potato"), ("洋葱", "onion"), ("大蒜", "garlic"),
    ("韭菜", "chives"), ("豌豆", "peas"), ("豆角", "green beans"),
    ("南瓜", "pumpkin"), ("西葫芦", "zucchini"), ("西兰花", "broccoli"),
    ("芦笋", "asparagus"), ("芹菜", "celery"), ("秋葵", "okra"),
    ("罗勒", "basil"), ("薄荷", "mint"), ("迷迭香", "rosemary"),
    ("百里香", "thyme"), ("香菜", "cilantro"), ("欧芹", "parsley"),
    ("鼠尾草", "sage"), ("牛至", "oregano"), ("莳萝", "dill"),
    ("薰衣草", "lavender"), ("玫瑰", "rose"), ("绣球", "hydrangea"),
    ("兰花", "orchid"), ("向日葵", "sunflower"), ("郁金香", "tulip"),
    ("牡丹", "peony"), ("菊花", "chrysanthemum"), ("茉莉", "jasmine"),
    ("栀子花", "gardenia"), ("天竺葵", "geranium"), ("矮牵牛", "petunia"),
    ("多肉", "succulent"), ("仙人掌", "cactus"), ("绿萝", "pothos"),
    ("龟背竹", "monstera"), ("虎皮兰", "snake plant"), ("吊兰", "spider plant"),
    ("橡皮树", "rubber plant"), ("琴叶榕", "fiddle leaf fig"),
    ("柠檬", "lemon tree"), ("橙子", "orange tree"), ("无花果", "fig tree"),
    ("苹果", "apple tree"), ("桃树", "peach tree"), ("葡萄", "grape vine"),
    ("覆盆子", "raspberry"), ("黑莓", "blackberry"), ("石榴", "pomegranate"),
]
FACETS = [
    ("种植指南", "growing guide"), ("播种", "seed starting"),
    ("育苗", "seedling care"), ("移栽", "transplanting"),
    ("浇水", "watering"), ("施肥", "fertilizing"),
    ("土壤配方", "soil mix"), ("修剪", "pruning"),
    ("扦插繁殖", "propagation"), ("病虫害防治", "pest control"),
    ("叶子发黄", "yellow leaves"), ("烂根处理", "root rot"),
    ("光照要求", "sunlight requirements"), ("换盆", "repotting"),
    ("冬季养护", "winter care"), ("夏季养护", "summer care"),
    ("春季养护", "spring care"), ("秋季养护", "fall care"),
    ("有机种植", "organic gardening"), ("堆肥", "compost"),
    ("覆盖物", "mulching"), ("伴生种植", "companion planting"),
    ("滴灌", "drip irrigation"), ("自制肥料", "homemade fertilizer"),
    ("种植时间", "planting calendar"), ("收获技巧", "harvesting"),
    ("品种选择", "variety selection"), ("常见问题", "troubleshooting"),
    ("新手教程", "beginner tips"), ("低成本种植", "budget gardening"),
]
SETTINGS = [
    ("", ""), ("阳台", "balcony"), ("容器", "container"),
    ("花园", "garden"), ("小空间", "small space"),
    ("家庭", "home"), ("温室", "greenhouse"),
    ("高架种植床", "raised bed"), ("庭院", "backyard"),
    ("花盆", "pot"), ("城市", "urban"), ("农村", "rural"),
]
BASE_CATALOG_SIZE = len(PLANTS) * len(FACETS) * len(SETTINGS)
CONDITIONS = [
    ("", ""), ("沙质土壤", "sandy soil"), ("黏土土壤", "clay soil"),
    ("湿润气候", "humid climate"), ("干燥气候", "dry climate"),
]
CATALOG_SIZE = BASE_CATALOG_SIZE * len(CONDITIONS)


def keyword(index: int, language: int, cycle_date: str) -> str:
    cycle, offset = divmod(index, CATALOG_SIZE)
    condition, offset = divmod(offset, BASE_CATALOG_SIZE)
    # Spread early work across plants, intents and settings, without duplicates.
    stride = 7919
    assert math.gcd(stride, BASE_CATALOG_SIZE) == 1
    value = offset * stride % BASE_CATALOG_SIZE
    value, setting = divmod(value, len(SETTINGS))
    plant, facet = divmod(value, len(FACETS))
    phrase = " ".join(x for x in (
        PLANTS[plant][language], FACETS[facet][language], SETTINGS[setting][language],
        CONDITIONS[condition][language],
    ) if x)
    if cycle:
        phrase += f" after:{cycle_date}"
    return phrase


def save(name: str, value: object) -> None:
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = DIRECTORY / (name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.replace(DIRECTORY / name)


def materialize_catalog() -> None:
    """Persist the actual keyword bank, not just the number of possible combinations."""
    metadata = DIRECTORY / "catalog.json"
    if metadata.exists() and json.loads(metadata.read_text()).get("count") == CATALOG_SIZE*2:
        return
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = DIRECTORY / "keywords.jsonl.tmp"
    with temporary.open("w") as stream:
        for index in range(CATALOG_SIZE):
            for language in (0,1):
                stream.write(json.dumps({"language":"zh" if language==0 else "en",
                                         "keyword":keyword(index,language,"unused")},ensure_ascii=False)+"\n")
    temporary.replace(DIRECTORY / "keywords.jsonl")
    save("catalog.json",dict(count=CATALOG_SIZE*2,initial_queue_keywords=ACTIVE_LIMIT*WORDS_PER_CAMPAIGN,
                             refresh="new dated keyword combinations continuously added",version=2))


def tick() -> dict:
    from realtime.campaign_store import CampaignStore
    from realtime.campaign_queue import CampaignQueue
    from realtime.config import Config

    config = Config()
    if config.google_free_providers != ("wml",):
        raise RuntimeError("This run requires GOOGLE_FREE_PROVIDERS=wml")
    materialize_catalog()
    store = CampaignStore(config.database_url)
    queue = CampaignQueue(config.valkey_url)
    created = []
    now = datetime.now(timezone.utc)
    with store.connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(610062026)")
        db.execute("CREATE TABLE IF NOT EXISTS wml_continuous_runs (id text PRIMARY KEY, "
                   "started_at timestamptz NOT NULL DEFAULT now(),baseline_page_id bigint NOT NULL,"
                   "cursor bigint NOT NULL DEFAULT 0,cycle_date date NOT NULL DEFAULT CURRENT_DATE)")
        db.execute("CREATE TABLE IF NOT EXISTS wml_continuous_campaigns (run_id text NOT NULL "
                   "REFERENCES wml_continuous_runs(id),campaign_id uuid PRIMARY KEY REFERENCES campaigns(id),"
                   "sequence bigint NOT NULL,queued boolean NOT NULL DEFAULT false,UNIQUE(run_id,sequence))")
        row = db.execute("SELECT * FROM wml_continuous_runs WHERE id=%s FOR UPDATE", (RUN,)).fetchone()
        if row is None:
            active = db.execute("SELECT count(*) n FROM campaigns WHERE status='active'").fetchone()["n"]
            if active:
                raise RuntimeError("Another campaign is active; isolate this experiment before starting")
            row = db.execute("INSERT INTO wml_continuous_runs(id,baseline_page_id) "
                             "SELECT %s,coalesce(max(id),0) FROM pages RETURNING *", (RUN,)).fetchone()
        # Retire finished batches and rotate old unsuccessful batches, keeping backlog bounded.
        db.execute("UPDATE campaigns c SET status='stopped',updated_at=now() "
                   "FROM wml_continuous_campaigns r WHERE r.campaign_id=c.id AND r.run_id=%s "
                   "AND c.status='active' AND EXISTS(SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id) "
                   "AND NOT EXISTS(SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id "
                   "AND f.state='running' AND f.lease_expires_at>now()) "
                   "AND (c.created_at<now()-interval '4 hours' OR "
                   "(EXISTS(SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id) AND "
                   "NOT EXISTS(SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id "
                   "AND f.state<>'completed') AND c.created_at<now()-interval '30 minutes'))", (RUN,))
        active = db.execute("SELECT count(*) n FROM campaigns c JOIN wml_continuous_campaigns r "
                            "ON r.campaign_id=c.id WHERE r.run_id=%s AND c.status='active'", (RUN,)).fetchone()["n"]
        cursor = row["cursor"]
        for _ in range(ACTIVE_LIMIT - active):
            language = cursor % 2
            word_start = (cursor // 2) * WORDS_PER_CAMPAIGN
            # A dated catalog refresh is available each day, with no future dates
            # and no repeated keyword strings if a catalog finishes very quickly.
            cycle = word_start // CATALOG_SIZE
            refresh_date = row["cycle_date"] + timedelta(days=max(0,cycle-1))
            if cycle and refresh_date > now.date():
                break
            cycle_date = refresh_date.isoformat()
            words = [keyword(word_start + j, language, cycle_date) for j in range(WORDS_PER_CAMPAIGN)]
            cid = str(uuid4())
            db.execute("INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) "
                       "VALUES(%s,%s,%s::jsonb,1000000,'private','[\"google_web\"]'::jsonb,'active')",
                       (cid, words[0], json.dumps(words[1:], ensure_ascii=False)))
            db.execute("INSERT INTO wml_continuous_campaigns(run_id,campaign_id,sequence) VALUES(%s,%s,%s)", (RUN,cid,cursor))
            created.append(cid)
            cursor += 1
        db.execute("UPDATE wml_continuous_runs SET cursor=%s WHERE id=%s", (cursor,RUN))
    # Persist enqueue receipts separately, so a crash after database commit can
    # retry the enqueue without moving delayed or currently running campaigns.
    with store.connect() as db:
        campaigns = db.execute("SELECT c.id FROM campaigns c JOIN wml_continuous_campaigns r "
                               "ON c.id=r.campaign_id WHERE r.run_id=%s AND c.status='active' AND NOT r.queued", (RUN,)).fetchall()
    for c in campaigns:
        cid = str(c["id"])
        queue.enqueue(cid)
    if campaigns:
        with store.connect() as db:
            db.execute("UPDATE wml_continuous_campaigns SET queued=true WHERE campaign_id=ANY(%s::uuid[])",
                       ([str(c["id"]) for c in campaigns],))
    return report(store, row, cursor, created)


def report(store, run: dict, cursor: int, created: list[str]) -> dict:
    with store.connect() as db:
        # Each page appears once across the whole run, assigned to its first association.
        sql = ("WITH admitted AS (SELECT p.id,min(cp.first_seen) first_seen FROM pages p "
               "JOIN campaign_pages cp ON cp.page_id=p.id JOIN wml_continuous_campaigns r "
               "ON r.campaign_id=cp.campaign_id WHERE r.run_id=%s AND p.id>%s "
               "AND p.fetched_at>=%s AND length(p.content)>=500 GROUP BY p.id) ")
        params = (RUN,run["baseline_page_id"],run["started_at"])
        totals = db.execute(sql + "SELECT count(*) total,max(first_seen) last_body_at,"
                            "count(*) FILTER(WHERE first_seen>=now()-interval '1 hour') last_hour "
                            "FROM admitted",params).fetchone()
        hours = db.execute(sql + "SELECT date_trunc('hour',first_seen) AS hour_at,count(*) new_bodies "
                           "FROM admitted GROUP BY 1 ORDER BY 1 DESC LIMIT 48",params).fetchall()
        searches = db.execute("SELECT provider,classification,count(*) n,sum(result_count) urls "
                              "FROM google_serp_attempts WHERE created_at>=%s GROUP BY 1,2",
                              (run["started_at"],)).fetchall()
        recent = db.execute("SELECT count(*) n,count(*) FILTER(WHERE classification IN ('results','empty')) ok,"
                            "count(*) FILTER(WHERE classification='captcha') captcha,max(created_at) last_at "
                            "FROM google_serp_attempts WHERE created_at>=greatest(%s,now()-interval '1 hour')",
                            (run["started_at"],)).fetchone()
        errors = db.execute("SELECT error_code,count(*) n FROM google_serp_attempts "
                            "WHERE created_at>=greatest(%s,now()-interval '1 hour') "
                            "AND error_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC",
                            (run["started_at"],)).fetchall()
        active = db.execute("SELECT count(*) n FROM campaigns c JOIN wml_continuous_campaigns r "
                            "ON r.campaign_id=c.id WHERE r.run_id=%s AND c.status='active'", (RUN,)).fetchone()["n"]
    now = datetime.now(timezone.utc)
    alerts = []
    if recent["n"]>=20 and recent["ok"]/recent["n"]<0.05:
        alerts.append("Search success rate below 5% in the last hour")
    if (now-run["started_at"]).total_seconds()>=3600:
        if not totals["last_hour"]:
            alerts.append("No new qualified body in the last hour")
        if not recent["n"]:
            alerts.append("No search attempt in the last hour")
        elif recent["ok"]/recent["n"]<0.05 and not alerts:
            alerts.append("Search success rate below 5% in the last hour")
    result = dict(run=RUN,theme="园艺、阳台种植与植物养护",provider="wml",sources=["google_web"],
                  started_at=run["started_at"],as_of=now,deadline=None,body_limit=None,
                  active_campaigns=active,generated_keywords=cursor*WORDS_PER_CAMPAIGN,
                  evergreen_keywords=CATALOG_SIZE*2,new_campaigns=len(created),
                  new_qualified_bodies=totals["total"],last_hour_bodies=totals["last_hour"],
                  last_body_at=totals["last_body_at"],hours=hours,searches=searches,recent_searches=recent,
                  recent_errors=errors,search_success_rate=recent["ok"]/recent["n"] if recent["n"] else None,
                  captcha_rate=recent["captcha"]/recent["n"] if recent["n"] else None,
                  alerts=alerts,definition="globally unique new pages, >=500 characters; each page counted once per run")
    save("report.json",result)
    save("alerts.json",dict(as_of=now,alerts=alerts))
    return result


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=="preview":
        print(json.dumps(dict(catalog_size=CATALOG_SIZE*2,samples=[keyword(i,lang,"2026-10-06")
                          for lang in (0,1) for i in range(8)]),ensure_ascii=False))
    else:
        result=tick()
        print(json.dumps({k:v for k,v in result.items() if k not in ("hours","searches")},ensure_ascii=False,default=str))
