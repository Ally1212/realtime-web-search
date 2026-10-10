"""Durable SERP responses and a URL registry independent of article storage."""
from __future__ import annotations

import hashlib
import copy
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .campaign_store import CampaignStore
from .serp_fields import SCHEMA_VERSION, MODULES, GOOGLE_SOURCES, result_items, normalize_serp_url, public_serp_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS serp_collector_settings (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), content_fetch_enabled boolean NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS serp_requests (
 request_id uuid PRIMARY KEY, campaign_id uuid NOT NULL REFERENCES campaigns(id),
 provider text NOT NULL, query text NOT NULL, page integer NOT NULL,
 classification text NOT NULL, http_status integer, error_code text,
 completed_at timestamptz NOT NULL DEFAULT clock_timestamp(), response jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS serp_requests_campaign_time ON serp_requests(campaign_id,completed_at DESC);
CREATE INDEX IF NOT EXISTS serp_requests_time ON serp_requests(completed_at DESC);
ALTER TABLE serp_requests ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'google_web';
CREATE INDEX IF NOT EXISTS serp_requests_source_time ON serp_requests(source,completed_at DESC);
CREATE TABLE IF NOT EXISTS serp_urls (
 url_hash char(64) PRIMARY KEY, normalized_url text,
 first_seen timestamptz NOT NULL DEFAULT clock_timestamp(), last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
 first_request_id uuid REFERENCES serp_requests(request_id), historical boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS serp_urls_first_seen ON serp_urls(first_seen) WHERE NOT historical;
CREATE TABLE IF NOT EXISTS serp_url_sources (
 url_hash char(64) NOT NULL REFERENCES serp_urls(url_hash), source text NOT NULL,
 first_seen timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(url_hash,source)
);
CREATE TABLE IF NOT EXISTS serp_campaign_requests (
 campaign_id uuid NOT NULL REFERENCES campaigns(id), request_id uuid NOT NULL REFERENCES serp_requests(request_id),
 reused boolean NOT NULL DEFAULT false, PRIMARY KEY(campaign_id,request_id)
);
CREATE TABLE IF NOT EXISTS serp_feed_cache (
 cache_key char(64) PRIMARY KEY, request_id uuid NOT NULL REFERENCES serp_requests(request_id),
 expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS serp_supply_runs (
 run_id text PRIMARY KEY, cursor bigint NOT NULL DEFAULT 0,
 keyword_offset integer NOT NULL, started_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS serp_supply_campaigns (
 run_id text NOT NULL REFERENCES serp_supply_runs(run_id), campaign_id uuid PRIMARY KEY REFERENCES campaigns(id),
 sequence bigint NOT NULL, queued boolean NOT NULL DEFAULT false, UNIQUE(run_id,sequence)
);
CREATE TABLE IF NOT EXISTS serp_schema_versions(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
"""


class SerpStore:
    def __init__(self, dsn: str, *, initialize: bool = False):
        # Do not run pending article/hash migrations from this independent pipeline.
        self.store = CampaignStore(dsn, initialize=False)
        if initialize:
            self.initialize()

    def initialize(self, default_content_fetch: bool = False) -> None:
        with self.store.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261010, 1)")
            db.execute(SCHEMA)
            db.execute("INSERT INTO serp_collector_settings(id,content_fetch_enabled) VALUES(true,%s) ON CONFLICT DO NOTHING", (default_content_fetch,))
            if not db.execute("SELECT 1 FROM serp_schema_versions WHERE name='sources-v1'").fetchone():
                db.execute("INSERT INTO serp_campaign_requests(campaign_id,request_id) SELECT campaign_id,request_id FROM serp_requests ON CONFLICT DO NOTHING")
                # Legacy responses are all Web; retain their original observation time.
                db.execute("INSERT INTO serp_url_sources(url_hash,source,first_seen) SELECT url_hash,'google_web',first_seen FROM serp_urls WHERE NOT historical ON CONFLICT DO NOTHING")
                db.execute("INSERT INTO serp_schema_versions(name) VALUES('sources-v1')")
            if db.execute("SELECT 1 FROM serp_schema_versions WHERE name='history-seed-v1'").fetchone():
                return
            # A one-time retained-history baseline. No articles are changed or fetched.
            rows = db.execute("SELECT url FROM pages UNION SELECT url FROM crawl_events WHERE url LIKE 'http%' "
                              "UNION SELECT url FROM google_known_result_urls "
                              "UNION SELECT item->>'url' FROM discovery_query_cache,jsonb_array_elements(payload) item").fetchall()
            db.execute("CREATE TEMP TABLE serp_history_import(url_hash char(64),normalized_url text) ON COMMIT DROP")
            with db.cursor().copy("COPY serp_history_import(url_hash,normalized_url) FROM STDIN") as copy:
                for row in rows:
                    try:
                        url = normalize_serp_url(row['url'])
                        if not url.startswith(('http://', 'https://')):
                            continue
                        copy.write_row((hashlib.sha256(url.encode()).hexdigest(), url))
                    except (ValueError, TypeError, AttributeError):
                        continue
            db.execute("INSERT INTO serp_urls(url_hash,normalized_url,historical) SELECT DISTINCT url_hash,normalized_url,true FROM serp_history_import ON CONFLICT DO NOTHING")
            db.execute("INSERT INTO serp_schema_versions(name) VALUES('history-seed-v1')")

    def content_fetch_enabled(self, default: bool = False) -> bool:
        with self.store.connect() as db:
            row = db.execute("SELECT content_fetch_enabled FROM serp_collector_settings WHERE id=true").fetchone()
            return bool(row['content_fetch_enabled']) if row else default

    def set_content_fetch(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError('content_fetch_enabled must be a JSON boolean')
        with self.store.connect() as db:
            row = db.execute("UPDATE serp_collector_settings SET content_fetch_enabled=%s,updated_at=now() WHERE id=true RETURNING id", (enabled,)).fetchone()
            if not row:
                raise RuntimeError('SERP settings not initialized')
        return {'content_fetch_enabled': enabled, 'effective': 'next campaign; running body workers cancel when disabled'}

    def record(self, campaign_id: str, **event) -> str:
        response = event.get('response')
        response = copy.deepcopy(response) if isinstance(response, dict) else {}
        try:
            request_id = str(UUID(str(event.get('upstream_request_id'))))
        except (ValueError, TypeError, AttributeError):
            request_id = str(uuid4())
        classification = event['classification']
        source = event.get('source', 'google_web')
        if source not in GOOGLE_SOURCES:
            raise ValueError('unsupported Google source')
        successful = classification in {'results', 'empty'}
        response.setdefault('schema_version', SCHEMA_VERSION)
        response['source'] = source
        response.setdefault('search_parameters', {'engine': 'google', 'q': event['query'], 'hl': event['language'], 'gl': event.get('region', 'US').lower(), 'start': (event['page']-1)*10, 'num': 10, 'device': None})
        # These are observed request results, not made-up empty successes on failure.
        organic = response.get('organic_results') if successful else []
        organic = organic if isinstance(organic, list) else []
        response['organic_results'] = organic
        if not successful:
            response['news_results'] = []
            response['trending_searches'] = []
        items = result_items(response)
        response['search_metadata'] = {'id': request_id, 'status': 'Success' if successful else 'Error',
                                       'provider': event['provider'], 'http_status': event.get('http_status'),
                                       'error_code': event.get('error_code'), 'classification': classification,
                                       'total_time_taken': round(event['elapsed_seconds'], 3),
                                       'cache_status': event.get('upstream_cache_status'),
                                       'upstream_version': event.get('upstream_version')}
        response['search_metadata']['source'] = source
        if source != 'google_web':
            response['search_metadata']['collection_method'] = 'google_rss'
        normalized = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('link'), str):
                continue
            if not public_serp_url(item['link']):
                continue
            url = normalize_serp_url(item['link'])
            item['normalized_link'] = url
            normalized[hashlib.sha256(url.encode()).hexdigest()] = url
        with self.store.connect() as db:
            inserted = db.execute("INSERT INTO serp_requests(request_id,campaign_id,provider,query,page,classification,http_status,error_code,response,source) "
                                  "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_id",
                                  (request_id, campaign_id, event['provider'], event['query'], event['page'], classification,
                                   event.get('http_status'), event.get('error_code'), Jsonb(response), source)).fetchone()
            if not inserted:
                existing = db.execute('SELECT campaign_id,query,page,provider,response,source,classification,http_status,error_code FROM serp_requests WHERE request_id=%s', (request_id,)).fetchone()
                observed = [{k: v for k, v in item.items() if k != 'is_new_url'} for item in result_items(existing['response'])]
                if (str(existing['campaign_id']) != campaign_id or existing['query'] != event['query']
                        or existing['page'] != event['page'] or existing['provider'] != event['provider']
                        or existing['classification'] != classification or existing['http_status'] != event.get('http_status') or existing['error_code'] != event.get('error_code')
                        or existing['source'] != source or observed != items
                        or self._identity_response(existing['response']) != self._identity_response(response)):
                    raise ValueError('conflicting SERP request identity')
                return request_id
            db.execute('INSERT INTO serp_campaign_requests(campaign_id,request_id) VALUES(%s,%s)', (campaign_id,request_id))
            new_hashes = set()
            for digest, url in sorted(normalized.items()):
                row = db.execute("INSERT INTO serp_urls(url_hash,normalized_url,first_request_id) VALUES(%s,%s,%s) "
                                 "ON CONFLICT DO NOTHING RETURNING url_hash", (digest, url, request_id)).fetchone()
                if row:
                    new_hashes.add(digest)
                else:
                    db.execute('UPDATE serp_urls SET last_seen=clock_timestamp() WHERE url_hash=%s', (digest,))
                db.execute('INSERT INTO serp_url_sources(url_hash,source) VALUES(%s,%s) ON CONFLICT DO NOTHING', (digest,source))
            for item in items:
                if item.get('normalized_link'):
                    digest = hashlib.sha256(item['normalized_link'].encode()).hexdigest()
                    item['is_new_url'] = digest in new_hashes
            response['new_unique_links'] = len(new_hashes)
            response['search_metadata']['created_at'] = db.execute('SELECT completed_at FROM serp_requests WHERE request_id=%s', (request_id,)).fetchone()['completed_at'].isoformat()
            db.execute('UPDATE serp_requests SET response=%s WHERE request_id=%s', (Jsonb(response), request_id))
        return request_id

    @staticmethod
    def _identity_response(response: dict) -> dict:
        value = copy.deepcopy(response)
        value.pop('search_metadata', None)
        value.pop('new_unique_links', None)
        for item in result_items(value):
            item.pop('is_new_url', None)
        return value

    def cached_feed(self, key: str, campaign_id: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute('SELECT r.request_id,r.response FROM serp_feed_cache c JOIN serp_requests r USING(request_id) WHERE c.cache_key=%s AND c.expires_at>now()', (key,)).fetchone()
            if row:
                db.execute('INSERT INTO serp_campaign_requests(campaign_id,request_id,reused) VALUES(%s,%s,true) ON CONFLICT DO NOTHING', (campaign_id,row['request_id']))
                return row['response']
        return None

    def cache_feed(self, key: str, request_id: str, ttl: int) -> None:
        with self.store.connect() as db:
            db.execute("INSERT INTO serp_feed_cache(cache_key,request_id,expires_at) VALUES(%s,%s,now()+%s*interval '1 second') ON CONFLICT(cache_key) DO UPDATE SET request_id=EXCLUDED.request_id,expires_at=EXCLUDED.expires_at", (key,request_id,max(30,ttl)))

    def request(self, request_id: str) -> dict | None:
        request_id = str(UUID(request_id))
        with self.store.connect() as db:
            row = db.execute('SELECT response FROM serp_requests WHERE request_id=%s', (request_id,)).fetchone()
            return row['response'] if row else None

    def recent(self, campaign_id: str | None = None, limit: int = 50, source: str | None = None) -> list[dict]:
        limit = min(max(int(limit), 1), 200)
        if source is not None and source not in GOOGLE_SOURCES:
            raise ValueError('unsupported Google source')
        with self.store.connect() as db:
            clauses,params = [],[]
            if campaign_id:
                clauses.append('EXISTS(SELECT 1 FROM serp_campaign_requests c WHERE c.request_id=r.request_id AND c.campaign_id=%s)')
                params.append(str(UUID(campaign_id)))
            if source:
                clauses.append('r.source=%s'); params.append(source)
            where = ' WHERE '+' AND '.join(clauses) if clauses else ''
            rows = db.execute('SELECT r.* FROM serp_requests r'+where+' ORDER BY completed_at DESC LIMIT %s', (*params,limit)).fetchall()
            return [dict(id=str(r['request_id']), query=r['query'], page=r['page'], status=r['classification'], completed_at=r['completed_at'],
                         source=r['source'], organic_count=len(r['response'].get('organic_results', [])),
                         result_count=len(result_items(r['response'])), topic_count=len(r['response'].get('trending_searches') or []),
                         new_unique_links=r['response'].get('new_unique_links', 0)) for r in rows]

    def stats(self) -> dict:
        with self.store.connect() as db:
            # All counters must describe the same moment while collectors commit.
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            requests = db.execute("SELECT count(*) attempts,count(*) FILTER(WHERE classification IN ('results','empty')) successful,"
                                  "count(*) FILTER(WHERE classification='captcha') explicit_captcha FROM serp_requests WHERE completed_at>=now()-interval '24 hours'").fetchone()
            urls = db.execute("SELECT count(*) total_new,count(*) FILTER(WHERE first_seen>=now()-interval '24 hours') new_last_24h FROM serp_urls WHERE NOT historical").fetchone()
            by_source = {}
            for source in GOOGLE_SOURCES:
                counts = db.execute("SELECT count(*) attempts,count(*) FILTER(WHERE classification IN ('results','empty')) successful,count(*) FILTER(WHERE classification='captcha') explicit_captcha FROM serp_requests WHERE source=%s AND completed_at>=now()-interval '24 hours'", (source,)).fetchone()
                links = db.execute("SELECT count(*) unique_new_urls FROM serp_url_sources s JOIN serp_urls u USING(url_hash) WHERE s.source=%s AND NOT u.historical AND s.first_seen>=now()-interval '24 hours'", (source,)).fetchone()
                contribution = db.execute("SELECT count(*) first_discovery_contribution FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE r.source=%s AND NOT u.historical AND u.first_seen>=now()-interval '24 hours'", (source,)).fetchone()
                by_source[source] = dict(counts,**links,**contribution)
        return {'schema_version': SCHEMA_VERSION, 'scope': 'google_web_news_trends', 'window': 'last_24_hours',
                'requests': dict(requests), 'links': dict(urls), 'content_fetch_enabled': self.content_fetch_enabled(),
                'sources': by_source,
                'advanced_modules': {name: 'not_implemented' for name in MODULES}}
