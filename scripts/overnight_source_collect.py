"""One bounded source task, invoked in the existing production container."""
import json, sys, signal
from datetime import datetime, timezone

DEADLINE = datetime.fromisoformat('2026-11-18T01:00:00+00:00')
def allowed():
    return datetime.now(timezone.utc) < DEADLINE

source, index = sys.argv[1], int(sys.argv[2])
if not allowed():
    raise SystemExit(0)
def timeout_handler(*_):
    raise TimeoutError('bounded_source_task_timeout')
signal.signal(signal.SIGALRM,timeout_handler)
signal.alarm(210)
if source == 'google_maps':
    from realtime.maps_provider import search, CITIES
    CITIES=dict(CITIES, **{'Paris':(48.8566,2.3522),'Toronto':(43.6532,-79.3832),'Vancouver':(49.2827,-123.1207),'Los Angeles':(34.0522,-118.2437),'San Francisco':(37.7749,-122.4194),'Chicago':(41.8781,-87.6298),'Boston':(42.3601,-71.0589),'Seattle':(47.6062,-122.3321),'Seoul':(37.5665,126.9780),'Mumbai':(19.0760,72.8777),'Delhi':(28.6139,77.2090),'Bangkok':(13.7563,100.5018),'Jakarta':(-6.2088,106.8456),'Kuala Lumpur':(3.1390,101.6869),'Dubai':(25.2048,55.2708),'Istanbul':(41.0082,28.9784),'Madrid':(40.4168,-3.7038),'Rome':(41.9028,12.4964),'Amsterdam':(52.3676,4.9041),'Warsaw':(52.2297,21.0122),'Sao Paulo':(-23.5505,-46.6333),'Mexico City':(19.4326,-99.1332),'Buenos Aires':(-34.6037,-58.3816),'Cape Town':(-33.9249,18.4241),'Nairobi':(-1.2921,36.8219),'Cairo':(30.0444,31.2357),'Lisbon':(38.7223,-9.1393),'Stockholm':(59.3293,18.0686),'Vienna':(48.2082,16.3738),'Prague':(50.0755,14.4378)})
    cities = list(CITIES)
    kinds = ['restaurant','cafe','dentist','pharmacy','hotel','supermarket','bakery','gym','hospital','school','bank','car repair','florist','bookstore','furniture store','electrician','plumber','veterinarian','hair salon','museum']
    city = cities[index % len(cities)]
    kind = kinds[(index // len(cities)) % len(kinds)]
    lat,lng = CITIES[city]
    # Rotate a bounded city grid after the initial category/city coverage.
    grid = index // (len(cities)*len(kinds))
    if grid:
        lat += (((grid-1) % 11)-5)*0.018
        lng += ((((grid-1)//11)%11)-5)*0.018
    p = dict(mode='text',query=kind,city=city,location=dict(lat=lat,lng=lng),radius_m=5000,language='en',max_places=21)
    result = search(p)
    print(json.dumps(dict(source=source,index=index,parameters=p,result=result,completed_at=datetime.now(timezone.utc).isoformat()),default=str))
else:
    from realtime.config import Config
    from realtime.campaign_store import CampaignStore
    from realtime.serp_console import ConsoleStore
    from realtime.serp_store import SerpStore
    from realtime.serp_worker import make_discovery
    from realtime.serp_keywords import keyword, TOPICS, REGIONS
    c=Config(); store=CampaignStore(c.database_url,initialize=False)
    console=ConsoleStore(c.database_url); serp=SerpStore(c.database_url)
    query=keyword(50000+index,'2026-11-17')
    opts={}
    if source=='google_news':
        query=TOPICS[index%len(TOPICS)]+' '+REGIONS[(index//len(TOPICS))%len(REGIONS)]
        opts['news_locale']='US:en'
    if source=='google_trends':
        opts['trends_geo']=['US','GB','SG','HK','TW','JP','DE','FR','IN','AU','CA','BR','KR'][index%13]
    task=console.create_task(dict(query=query,sources=[source],start=False,mode='once',upload_to_whale=False,options=opts),c)
    cid=task['campaign_id']
    # The task remains paused, never entering the legacy worker's queue.
    campaign=store.campaign(cid); campaign['serp_options']=task['options']
    discovery=make_discovery(c,store,serp,campaign); discovery.should_continue=allowed
    if source in ('google_news','google_trends'):
        from realtime.google_news_links import NewsLinkResolver
        discovery.news_link_resolver=NewsLinkResolver(discovery,store,cid,budget_seconds=min(10,max(0,(DEADLINE-datetime.now(timezone.utc)).total_seconds())),max_links=10)
    try:
        if source=='google_images':
            from realtime.google_images import collect_images
            rows,errors=collect_images(discovery,store,serp,cid,[query],c)
        else:
            from realtime.google_feeds import collect_sources
            rows,errors=collect_sources(discovery,store,serp,cid,[query],[source],c)
        with store.connect() as db:
            db.execute("UPDATE campaigns SET status=%s,updated_at=now() WHERE id=%s AND status='paused'",('failed' if errors else 'completed',cid))
            counts=db.execute("SELECT count(*) requests,count(*) FILTER(WHERE NOT cr.reused) uncached,coalesce(sum((s.response->>'new_unique_links')::int) FILTER(WHERE NOT cr.reused),0) reported_new_links FROM serp_campaign_requests cr JOIN serp_requests s USING(request_id) WHERE cr.campaign_id=%s",(cid,)).fetchone()
        print(json.dumps(dict(source=source,index=index,campaign_id=cid,query=query,count=len(rows),errors=errors,counts=counts),default=str))
    finally:
        discovery.close()
