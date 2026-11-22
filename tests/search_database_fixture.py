import os
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from realtime.campaign_store import CampaignStore, _POOLS, _INITIALIZED_DSNS
from realtime.config import Config
from realtime.serp_console import ConsoleStore

class SearchDatabaseFixture:
    def setUp(self):
        self.root=os.environ['TEST_DATABASE_URL'];self.schema='whale_serp_'+uuid4().hex
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn=make_conninfo(self.root,options='-c search_path='+self.schema)
        self.base=CampaignStore(self.dsn)
        self.config=Config(database_url=self.dsn,default_proxy_profile='direct',
                           whale_collector_api_key='test-only',whale_supported_task_types=('keyword_search','backfill','content_detail'))
        self.console=ConsoleStore(self.dsn);self.console.initialize();self.console.initialize_console(self.config)
        self.console.save_settings({'continuous_enabled':False},self.config)
        self.cid=self.console.create_task({'query':'energy','start':False},self.config)['campaign_id']
        self.record(self.cid,'google_web','https://example.org/a','Solar research')
        self.record(self.cid,'google_news','https://example.org/b','Energy news')
        self.record(self.cid,'google_trends','https://example.org/c','Trend report')
    def tearDown(self):
        _INITIALIZED_DSNS.discard(self.dsn)
        for k in list(_POOLS):
            if k[0]==self.dsn:_POOLS.pop(k).close()
        with psycopg.connect(self.root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
    def record(self,cid,source,url,title):
        item=dict(link=url,title=title,snippet='Real saved snippet',position=2)
        response={'organic_results':[item]} if source=='google_web' else {'news_results':[item]} if source=='google_news' else {'trending_searches':[dict(keyword='trend',articles=[item])]}
        return self.console.record(cid,source=source,provider='openserp' if source=='google_web' else 'google_rss',query='energy',page=1,language='en',response=response,elapsed_seconds=.1,classification='results',http_status=200)
