"""Server experiment control and exact hourly PostgreSQL report (run in web container)."""
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from realtime.config import Config
from realtime.campaign_store import CampaignStore
from realtime.campaign_queue import CampaignQueue

ROOT = Path('/app/state/google-web-travel-6h-20261005')
store = CampaignStore(Config().database_url)

def save(name, obj):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / (name + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + '\n')
    tmp.replace(ROOT / name)

if sys.argv[1] == 'init':
    if (ROOT / 'manifest.json').exists():
        raise SystemExit('Experiment already exists; inspect before restarting')
    # Multiword destinations are written explicitly to preserve search intent.
    cities = ['Paris','London','Rome','Barcelona','Madrid','Lisbon','Amsterdam','Berlin','Prague','Vienna','Budapest','Athens','Istanbul','Tokyo','Kyoto','Osaka','Seoul','Taipei','Singapore','Bangkok','Hanoi','Bali','Sydney','Melbourne','Auckland','New York','Boston','Chicago','Seattle','San Francisco','Los Angeles','Vancouver','Toronto','Montreal','Mexico City','Buenos Aires','Lima','Santiago','Cape Town','Marrakech','Cairo','Dubai','Abu Dhabi','Muscat','Doha','Mumbai','Delhi','Jaipur','Kathmandu','Colombo','Maldives','Manila','Cebu','Hong Kong','Macau','Beijing','Shanghai','Chengdu','Chongqing','Xian','Guilin','Kunming','Lijiang','Dali','Hangzhou','Suzhou','Nanjing','Qingdao','Xiamen','Sanya','Harbin','Shenzhen','Guangzhou']
    facets = ['travel guide','things to do','itinerary','budget travel','food guide','day trips','family travel','solo travel','historic attractions','public transport guide','best neighborhoods','hiking guide']
    ids = []
    with store.connect() as db:
        old = db.execute("SELECT id,query,status,sources FROM campaigns WHERE status IN ('active','paused')").fetchall()
        db.execute("UPDATE campaigns SET status='stopped',updated_at=now() WHERE status IN ('active','paused')")
        baseline = db.execute('SELECT COALESCE(max(id),0) AS id FROM pages').fetchone()['id']
        for facet in facets:
            for city in cities:
                cid = str(uuid4())
                query = f'{city} {facet}'
                db.execute("INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) VALUES(%s,%s,%s::jsonb,1000000,'private','[\"google_web\"]'::jsonb,'active')", (cid,query,json.dumps([])))
                ids.append(cid)
    start = datetime.now(timezone.utc)
    manifest = dict(theme='全球旅行与目的地攻略', sources=['google_web'],providers=['openserp'],campaign_ids=ids,keyword_count=len(ids),quota='unlimited: locally created campaigns set spider.daily_target=0',target_per_hour=5000,minimum_body_chars=500,baseline_max_page_id=baseline,started_at=start.isoformat(),deadline=(start+timedelta(hours=6)).isoformat(),stopped_campaigns=old)
    save('manifest.json',manifest)
    queue = CampaignQueue(Config().valkey_url)
    for cid in ids:
        queue.recover(cid)
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('campaign_ids','stopped_campaigns')},ensure_ascii=False))
elif sys.argv[1] in ('report','finish'):
    m = json.loads((ROOT/'manifest.json').read_text())
    start = datetime.fromisoformat(m['started_at'])
    deadline = datetime.fromisoformat(m['deadline'])
    now = datetime.now(timezone.utc)
    end = min(now,deadline)
    ids = m['campaign_ids']
    with store.connect() as db:
        if sys.argv[1] == 'finish':
            db.execute("UPDATE campaigns SET status='stopped',updated_at=now() WHERE id=ANY(%s::uuid[])",(ids,))
        rows = []
        for h in range(6):
            a,b = start+timedelta(hours=h), min(start+timedelta(hours=h+1),end)
            if a >= end: break
            n = db.execute("SELECT count(DISTINCT p.id) AS n FROM pages p JOIN campaign_pages cp ON cp.page_id=p.id WHERE cp.campaign_id=ANY(%s::uuid[]) AND p.id>%s AND cp.first_seen>=%s AND cp.first_seen<%s AND length(p.content)>=500",(ids,m['baseline_max_page_id'],a,b)).fetchone()['n']
            counts = db.execute("SELECT classification,count(*) AS n FROM google_serp_attempts WHERE created_at>=%s AND created_at<%s GROUP BY 1",(a,b)).fetchall()
            c = {r['classification']:r['n'] for r in counts}
            total = sum(c.values()); failed = sum(v for k,v in c.items() if k not in ('results','empty'))
            rows.append(dict(hour=h+1,start=a.isoformat(),end=b.isoformat(),complete=b>=start+timedelta(hours=h+1),new_qualified_bodies=n,search_attempts=total,search_failures=failed,captcha=c.get('captcha',0),search_failure_rate=failed/total if total else None,captcha_rate=c.get('captcha',0)/total if total else None,classifications=c))
        active = db.execute("SELECT count(*) AS n FROM campaigns WHERE status='active' AND id=ANY(%s::uuid[])",(ids,)).fetchone()['n']
        other = db.execute("SELECT count(*) AS n FROM campaigns WHERE status='active' AND NOT(id=ANY(%s::uuid[]))",(ids,)).fetchone()['n']
        provider = db.execute('SELECT provider,count(*) AS n FROM google_serp_attempts WHERE created_at>=%s AND created_at<%s GROUP BY 1',(start,end)).fetchall()
    report = dict(as_of=now.isoformat(),started_at=start.isoformat(),deadline=deadline.isoformat(),elapsed_seconds=(end-start).total_seconds(),status='finished' if sys.argv[1]=='finish' else 'running',active_experiment_campaigns=active,other_active_campaigns=other,providers=provider,total_new_qualified_bodies=sum(r['new_qualified_bodies'] for r in rows),hours=rows,rate_definition='physical Google SERP attempts; empty responses successful; failed=all other classifications; new bodies require page id above pre-run baseline, experiment membership, >=500 characters, globally unique URL and hash')
    save('report.json',report)
    csv_buffer = io.StringIO()
    fields = ['hour','start','end','complete','new_qualified_bodies','search_attempts','search_failures','search_failure_rate','captcha','captcha_rate']
    writer = csv.DictWriter(csv_buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    (ROOT/'hourly.csv.tmp').write_text(csv_buffer.getvalue())
    (ROOT/'hourly.csv.tmp').replace(ROOT/'hourly.csv')
    print(json.dumps(report,ensure_ascii=False,default=str))
else:
    raise SystemExit('init/report/finish')
