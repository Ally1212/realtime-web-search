"""Durable WML-only gardening run. Execute ticks inside the web container."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

RUN = "google-kitchen-20261006"
PROVIDER = "wml"
SOURCES = ["google_web"]
DEADLINE = datetime.fromisoformat("2026-10-07T01:00:00+00:00")
THEME = "烹饪、食材与家庭厨房"
DIRECTORY = Path("/app/state") / RUN
ACTIVE_LIMIT = 1408
WORDS_PER_CAMPAIGN = 8
LANGUAGE_INDICES = (0, 1)

PLANTS = [
    ("鸡肉", "chicken"), ("牛肉", "beef"), ("猪肉", "pork"), ("羊肉", "lamb"),
    ("鱼", "fish"), ("三文鱼", "salmon"), ("鳕鱼", "cod"), ("金枪鱼", "tuna"),
    ("虾", "shrimp"), ("蟹", "crab"), ("扇贝", "scallops"), ("贻贝", "mussels"),
    ("鸡蛋", "eggs"), ("豆腐", "tofu"), ("豆豉", "fermented black beans"),
    ("土豆", "potato"), ("红薯", "sweet potato"), ("胡萝卜", "carrot"),
    ("洋葱", "onion"), ("番茄", "tomato"), ("黄瓜", "cucumber"),
    ("茄子", "eggplant"), ("西葫芦", "zucchini"), ("南瓜", "pumpkin"),
    ("西兰花", "broccoli"), ("花椰菜", "cauliflower"), ("菠菜", "spinach"),
    ("甘蓝", "kale"), ("白菜", "napa cabbage"), ("芹菜", "celery"),
    ("蘑菇", "mushroom"), ("香菇", "shiitake"), ("玉米", "corn"),
    ("豌豆", "peas"), ("扁豆", "lentils"), ("鹰嘴豆", "chickpeas"),
    ("黑豆", "black beans"), ("芸豆", "kidney beans"), ("藜麦", "quinoa"),
    ("燕麦", "oats"), ("米饭", "rice"), ("糙米", "brown rice"),
    ("意大利面", "pasta"), ("面条", "noodles"), ("面包", "bread"),
    ("披萨", "pizza"), ("饺子", "dumplings"), ("馄饨", "wontons"),
    ("煎饼", "pancakes"), ("华夫饼", "waffles"), ("蛋糕", "cake"),
    ("曲奇", "cookies"), ("布朗尼", "brownies"), ("松饼", "muffins"),
    ("苹果", "apple"), ("香蕉", "banana"), ("蓝莓", "blueberry"),
    ("草莓", "strawberry"), ("桃子", "peach"), ("梨", "pear"),
    ("芒果", "mango"), ("菠萝", "pineapple"), ("柠檬", "lemon"),
    ("橙子", "orange"), ("鳄梨", "avocado"), ("椰子", "coconut"),
    ("奶酪", "cheese"), ("酸奶", "yogurt"), ("牛奶", "milk"),
    ("黄油", "butter"), ("花生", "peanuts"), ("杏仁", "almonds"),
    ("核桃", "walnuts"), ("芝麻", "sesame"), ("巧克力", "chocolate"),
    ("咖啡", "coffee"), ("绿茶", "green tea"), ("红茶", "black tea"),
    ("姜", "ginger"), ("蒜", "garlic"), ("辣椒", "chili pepper"),
    ("罗勒", "basil"), ("薄荷", "mint"), ("迷迭香", "rosemary"),
    ("蜂蜜", "honey"), ("酸面包", "sourdough"), ("牛排", "steak"),
    ("火鸡", "turkey"), ("鸭肉", "duck"), ("香肠", "sausage"),
    ("培根", "bacon"), ("豆芽", "bean sprouts"), ("秋葵", "okra"),
    ("芦笋", "asparagus"), ("甜菜", "beetroot"), ("萝卜", "radish"),
    ("荞麦", "buckwheat"), ("大麦", "barley"), ("小米", "millet"),
    ("年糕", "rice cakes"),
]
FACETS = [
    ("家常食谱", "home cooking recipes"), ("新手做法", "beginner recipes"),
    ("烹饪技巧", "cooking tips"), ("快手做法", "quick recipes"),
    ("备餐指南", "meal prep guide"), ("保存方法", "storage guide"),
    ("冷冻方法", "freezing tips"), ("解冻方法", "defrosting guide"),
    ("调味技巧", "seasoning tips"), ("酱汁搭配", "sauce pairings"),
    ("食材搭配", "ingredient pairings"), ("购买指南", "buying guide"),
    ("挑选方法", "selection tips"), ("切配技巧", "preparation tips"),
    ("剩菜利用", "leftover recipes"), ("一锅料理", "one pot recipes"),
    ("烤箱做法", "oven recipes"), ("空气炸锅做法", "air fryer recipes"),
    ("电饭锅做法", "rice cooker recipes"), ("慢炖锅做法", "slow cooker recipes"),
    ("压力锅做法", "pressure cooker recipes"), ("平底锅做法", "skillet recipes"),
    ("蒸煮做法", "steaming recipes"), ("烧烤做法", "grilling recipes"),
    ("焯水技巧", "blanching tips"), ("腌制方法", "marinating tips"),
    ("餐前准备", "make ahead recipes"), ("常见错误", "common cooking mistakes"),
    ("食材替代", "ingredient substitutions"), ("分量换算", "portion guide"),
]
SETTINGS = [
    ("", ""), ("家庭厨房", "home kitchen"), ("小厨房", "small kitchen"),
    ("节日餐桌", "holiday meals"), ("工作日", "weeknight"),
    ("周末", "weekend"), ("午餐", "lunch"), ("晚餐", "dinner"),
    ("野餐", "picnic"), ("聚餐", "potluck"), ("便当", "lunchbox"),
    ("露营", "camping"),
]
BASE_CATALOG_SIZE = len(PLANTS) * len(FACETS) * len(SETTINGS)
CONDITIONS = [
    ("", ""), ("低成本", "budget friendly"), ("少油", "less oil"),
    ("少盐", "less salt"), ("简易步骤", "simple steps"),
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
    if metadata.exists():
        previous = json.loads(metadata.read_text())
        if previous.get("count") == CATALOG_SIZE*len(LANGUAGE_INDICES) and tuple(
            previous.get("language_indices", (0, 1))
        ) == LANGUAGE_INDICES and previous.get("keyword_offset", 0) == globals().get("KEYWORD_OFFSET", 0):
            return
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = DIRECTORY / "keywords.jsonl.tmp"
    with temporary.open("w") as stream:
        for index in range(CATALOG_SIZE):
            for language in LANGUAGE_INDICES:
                stream.write(json.dumps({"language":"zh" if language==0 else "en",
                                         "keyword":keyword(index,language,"unused")},ensure_ascii=False)+"\n")
    temporary.replace(DIRECTORY / "keywords.jsonl")
    save("catalog.json",dict(count=CATALOG_SIZE*len(LANGUAGE_INDICES),initial_queue_keywords=ACTIVE_LIMIT*WORDS_PER_CAMPAIGN,
                             refresh="new dated keyword combinations continuously added",version=2,
                             language_indices=LANGUAGE_INDICES,
                             keyword_offset=globals().get("KEYWORD_OFFSET", 0)))


def tick() -> dict:
    from realtime.campaign_store import CampaignStore
    from realtime.campaign_queue import CampaignQueue
    from realtime.config import Config

    config = Config()
    if config.google_free_providers != (PROVIDER,):
        raise RuntimeError("Configured search provider does not match this run")
    if datetime.now(timezone.utc) >= DEADLINE:
        return finish()
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
            language = LANGUAGE_INDICES[cursor % len(LANGUAGE_INDICES)]
            word_start = (cursor // len(LANGUAGE_INDICES)) * WORDS_PER_CAMPAIGN
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
                       "VALUES(%s,%s,%s::jsonb,1000000,'private',%s::jsonb,'active')",
                       (cid, words[0], json.dumps(words[1:], ensure_ascii=False), json.dumps(SOURCES)))
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
               "AND p.fetched_at>=%s AND p.fetched_at<%s AND cp.first_seen<%s AND length(p.content)>=500 GROUP BY p.id) ")
        params = (RUN,run["baseline_page_id"],run["started_at"],DEADLINE,DEADLINE)
        totals = db.execute(sql + "SELECT count(*) total,max(first_seen) last_body_at,"
                            "count(*) FILTER(WHERE first_seen>=least(now(),%s)-interval '1 hour') last_hour "
                            "FROM admitted",(*params,DEADLINE)).fetchone()
        hours = db.execute(sql + "SELECT date_trunc('hour',first_seen) AS hour_at,count(*) new_bodies "
                           "FROM admitted GROUP BY 1 ORDER BY 1 DESC LIMIT 48",params).fetchall()
        searches = db.execute("SELECT provider,classification,count(*) n,sum(result_count) urls "
                              "FROM google_serp_attempts WHERE created_at>=%s AND created_at<%s GROUP BY 1,2",
                              (run["started_at"],DEADLINE)).fetchall()
        recent = db.execute("SELECT count(*) n,count(*) FILTER(WHERE classification IN ('results','empty')) ok,"
                            "count(*) FILTER(WHERE classification='captcha') captcha,max(created_at) last_at "
                            "FROM google_serp_attempts WHERE created_at>=greatest(%s,least(now(),%s)-interval '1 hour') "
                            "AND created_at<%s", (run["started_at"],DEADLINE,DEADLINE)).fetchone()
        errors = db.execute("SELECT error_code,count(*) n FROM google_serp_attempts "
                            "WHERE created_at>=greatest(%s,least(now(),%s)-interval '1 hour') AND created_at<%s "
                            "AND error_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC",
                            (run["started_at"],DEADLINE,DEADLINE)).fetchall()
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
    result = dict(run=RUN,theme=THEME,provider=PROVIDER,sources=SOURCES,
                  started_at=run["started_at"],as_of=now,deadline=DEADLINE,body_limit=None,status="finished" if now>=DEADLINE else "running",
                  active_campaigns=active,generated_keywords=cursor*WORDS_PER_CAMPAIGN,
                  evergreen_keywords=CATALOG_SIZE*len(LANGUAGE_INDICES),new_campaigns=len(created),
                  new_qualified_bodies=totals["total"],last_hour_bodies=totals["last_hour"],
                  last_body_at=totals["last_body_at"],hours=hours,searches=searches,recent_searches=recent,
                  recent_errors=errors,search_success_rate=recent["ok"]/recent["n"] if recent["n"] else None,
                  captcha_rate=recent["captcha"]/recent["n"] if recent["n"] else None,
                  alerts=alerts,definition="globally unique new pages, >=500 characters; each page counted once per run")
    save("report.json",result)
    save("alerts.json",dict(as_of=now,alerts=alerts))
    return result


def finish():
    from realtime.campaign_store import CampaignStore
    from realtime.config import Config
    store=CampaignStore(Config().database_url)
    with store.connect() as db:
        run=db.execute("SELECT * FROM wml_continuous_runs WHERE id=%s",(RUN,)).fetchone()
        if not run:
            return {"run":RUN,"status":"not_started","deadline":str(DEADLINE)}
        db.execute("UPDATE campaigns SET status='stopped',updated_at=now() WHERE id IN "
                   "(SELECT campaign_id FROM wml_continuous_campaigns WHERE run_id=%s)",(RUN,))
    result=report(store,run,run["cursor"],[])
    result["status"]="finished"
    save("report.json",result)
    save("final-report.json",result)
    return result


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=="preview":
        print(json.dumps(dict(count=CATALOG_SIZE*len(LANGUAGE_INDICES),deadline=str(DEADLINE),
                              samples=[keyword(i,l,"unused") for l in (0,1) for i in range(5)]),ensure_ascii=False))
    else:
        result=finish() if len(sys.argv)>1 and sys.argv[1]=="finish" else tick()
        print(json.dumps({k:v for k,v in result.items() if k not in ("hours","searches")},ensure_ascii=False,default=str))
