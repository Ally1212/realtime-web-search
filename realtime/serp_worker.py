"""Google SERP campaigns without starting Scrapy or downloading result pages."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .campaign_queue import CampaignQueue
from .campaign_store import CampaignStore
from .config import Config
from .discovery import SearchDiscovery
from .proxy_pool import ProxyPool
from .serp_store import SerpStore
from .serp_console import ConsoleStore
from .serp_fields import GOOGLE_SOURCES


def make_discovery(config: Config, store: CampaignStore, serp: SerpStore,
                   campaign: dict, *, proxy_pool: ProxyPool | None = None) -> SearchDiscovery:
    cid = str(campaign['id'])
    language = 'zh' if any('\u4e00' <= c <= '\u9fff' for c in campaign['query']) else 'en'
    opts = campaign.get('serp_options') or {}
    if opts.get('web_language') in ('en', 'zh'):
        language = opts['web_language']
    discovery = SearchDiscovery(
        config.request_timeout, proxy_pool=proxy_pool if proxy_pool is not None else ProxyPool(config),
        proxy_profile=campaign['proxy_profile'],
        google_web_enabled=config.google_web_enabled,
        language=language, global_concurrency=config.discovery_global_concurrency,
        query_concurrency=config.discovery_query_concurrency,
        providers=('openserp',), openserp_url=config.openserp_url,
        openserp_request_timeout_seconds=config.openserp_request_timeout_seconds,
        serp_features_enabled=config.serp_features_enabled,
        validate_target_dns=False,
        google_web_max_pages=max(int(opts.get('web_pages') or config.google_web_max_pages), 1),
        google_web_pages_per_batch=config.google_web_pages_per_batch,
        google_web_initial_rps=config.google_web_initial_rps,
        google_web_max_rps=config.google_web_max_rps,
        proxy_min_interval_seconds=config.google_web_proxy_min_interval_seconds,
        proxy_cooldown_seconds=config.google_web_proxy_cooldown_seconds,
        check_local_proxy_readiness=proxy_pool is not None,
        source_cooldown_seconds=config.google_web_source_cooldown_seconds,
        captcha_threshold=config.google_web_captcha_threshold,
        query_cache_seconds=config.google_web_query_cache_seconds,
        cache_get=store.get_discovery_cache, cache_put=store.put_discovery_cache,
        source_slot_acquirer=lambda source, initial_rps: store.acquire_discovery_slot(
            source, initial_rps, maximum_rps=config.google_web_max_rps),
        source_result_recorder=store.record_discovery_result,
        proxy_reserver=store.reserve_google_proxy,
        proxy_result_recorder=store.record_google_proxy_result,
        proxy_usage_recorder=store.record_proxy_usage,
        page_batch_acquirer=lambda query, locale, max_page, batch_size: store.acquire_google_page_batch(cid, query, locale, max_page, batch_size, config.google_web_query_cache_seconds),
        page_result_recorder=store.record_google_page_result,
        page_batch_releaser=store.release_google_page_batch,
        singleflight_acquirer=store.acquire_query_lease, singleflight_releaser=store.release_query_lease,
        serp_response_recorder=lambda **event: serp.record(cid, **event),
    )
    # Old caches lack snippet/rank; do not reuse them as enriched SERP records.
    discovery.locale += ':serp-v1'
    if opts.get('web_region'):
        discovery.transport.region = opts['web_region']
        if opts['web_region'] != 'US':
            discovery.locale += ':' + opts['web_region']
    discovery.task_options = opts
    discovery.news_link_resolution = True
    discovery.serp_cache_campaign_id = cid
    from .serp_console import ConsoleStore
    console = ConsoleStore(config.database_url)
    discovery.should_continue = lambda: console.runnable(cid)
    return discovery


def complete_once_task(console: ConsoleStore, campaign_id: str, reason: str) -> None:
    with console.store.connect() as db:
        row = db.execute("SELECT o.mode FROM serp_task_options o JOIN campaigns c ON c.id=o.campaign_id WHERE o.campaign_id=%s AND c.status='active' FOR UPDATE OF c", (campaign_id,)).fetchone()
        if not row or row['mode'] != 'once':
            return
        db.execute("UPDATE campaigns SET status='completed',updated_at=now() WHERE id=%s", (campaign_id,))
        db.execute("UPDATE serp_task_options SET stop_reason=%s WHERE campaign_id=%s", (reason, campaign_id))


def run_serp_campaign(campaign_id: str, config: Config | None = None,
                      store: CampaignStore | None = None, serp: SerpStore | None = None,
                      *, should_continue=None, proxy_pool: ProxyPool | None = None,
                      skip_web_pages=frozenset(), on_proxy_switch=None, collection_stop_at=None) -> tuple[str, ...]:
    config = config or Config()
    store = store or CampaignStore(config.database_url, initialize=False)
    serp = serp or SerpStore(config.database_url)
    campaign = store.campaign(campaign_id)
    if not campaign or campaign['status'] != 'active':
        return ()
    # Optional parameters live outside the legacy article schema.
    console = None
    if isinstance(serp, SerpStore):
        console = ConsoleStore(config.database_url)
        if not console.runnable(campaign_id):
            return ()
        if console.once_retry_expired(campaign_id):
            return ()
        campaign['serp_options'] = console.task_options(campaign_id)
        settings = console.settings(config)
        campaign['serp_options'] = {'web_region': settings['web_region'], 'web_language': settings['web_language'], **campaign['serp_options']}
    task = store.whale_task_for_campaign(campaign_id)
    if task and task.get('task_type') == 'content_detail':
        store.record_event(campaign_id, '', 'skipped', error_code='content_fetch_disabled')
        return ()
    queries = tuple(dict.fromkeys((campaign['query'], *(campaign.get('aliases') or []))))
    sources = campaign.get('sources') or ['google_web']
    opts = campaign.get('serp_options') or {}
    discovery = make_discovery(config, store, serp, campaign, proxy_pool=proxy_pool)
    discovery.on_proxy_switch = on_proxy_switch
    if opts.get('customer_private') and int(opts.get('web_pages') or config.google_web_max_pages) == 11:
        # Private live queries must attempt later pages even if one page fails.
        # This bounded invocation owns its page budget; no shared frontier lease.
        discovery.page_batch_acquirer = None
        discovery.continue_on_page_error = True
        discovery.proxy_provider_attempts = 2
        discovery.pause_on_proxy_unavailable = True
        discovery.completed_web_pages = skip_web_pages
        if 'google_web' in sources:
            discovery.live_collection_stop_at = collection_stop_at
            if collection_stop_at is not None:
                discovery.query_concurrency = 1
            from .web_live_admission import reserve_live_web
            discovery.live_web_reserver = lambda proxy_hash, locale, interval: reserve_live_web(
                store, config, proxy_hash, locale, interval)
    if should_continue is not None:
        original_continue = discovery.should_continue
        discovery.should_continue = lambda: should_continue() and (original_continue is None or original_continue())
    try:
        results,errors = [],[]
        if 'google_web' in sources:
            store.reconcile_google_queries(campaign_id, queries, discovery.locale)
            results, errors = discovery.discover_many(queries, int(opts.get('web_pages') or config.google_web_max_pages))
        if 'google_images' in sources:
            from .google_images import collect_images
            extra, failures = collect_images(discovery,store,serp,campaign_id,queries,config)
            results.extend(extra); errors.extend(failures)
        if any(s in sources for s in ('google_news','google_trends')):
            from .google_feeds import collect_sources
            extra,failures = collect_sources(discovery,store,serp,campaign_id,queries,sources,config)
            results.extend(extra); errors.extend(failures)
        store.increment(campaign_id, discovered=len({r.url for r in results}))
        if errors:
            store.record_event(campaign_id, '', 'discovery_failed', error_code='serp_batch_incomplete')
            if opts.get('customer_private') and 'google_web' in sources:
                from .live_collection import collection_error
                store.record_event(campaign_id, '', 'web_collection_interrupted',
                                   error_code=collection_error((), errors))
        if opts.get('mode') == 'once' and console is not None:
            counts = console.task_usage(campaign_id)
            if opts.get('max_successful_searches') and counts['successful'] >= opts['max_successful_searches']:
                complete_once_task(console, campaign_id, 'max_successful_searches')
            elif not errors and console.collection_progress(campaign_id, public_only=False)['scope_complete']:
                complete_once_task(console, campaign_id, 'scope_completed')
            elif 'google_images' in sources and errors != ['images_busy'] and not opts.get('retry_window_seconds'):
                with console.store.connect() as db:
                    db.execute("UPDATE campaigns SET status='failed',last_error=%s,updated_at=now() WHERE id=%s AND status='active'", ('images_once_failed:'+','.join(errors)[:400],campaign_id))
        return tuple(errors)
    finally:
        discovery.close()


class SerpSupply:
    """Keep a bounded set of active continuous campaigns."""
    def __init__(self, config: Config, store: CampaignStore, queue: CampaignQueue):
        self.config, self.store, self.queue = config, store, queue

    def tick(self) -> None:
        from .serp_keywords import keyword, CATALOG_SIZE
        from .serp_keyword_pool import active_pool, claim_queries, refill_capacity
        from .serp_console import ConsoleStore
        console = ConsoleStore(self.config.database_url)
        settings = console.settings(self.config)
        if not settings['continuous_enabled']:
            return
        run = self.config.serp_run_id
        sources = settings['continuous_sources']
        if not sources:
            raise ValueError('SERP_CONTINUOUS_SOURCES must include a supported Google source')
        with self.store.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261010, 2)')
            db.execute('INSERT INTO serp_supply_runs(run_id,keyword_offset) VALUES(%s,%s) ON CONFLICT DO NOTHING', (run, self.config.serp_keyword_offset))
            row = db.execute('SELECT * FROM serp_supply_runs WHERE run_id=%s FOR UPDATE', (run,)).fetchone()
            if row['keyword_offset'] != self.config.serp_keyword_offset:
                raise RuntimeError('existing SERP run keyword offset differs')
            pool = active_pool(db) if sources == ['google_web'] else None
            db.execute(
                """UPDATE campaigns c SET status='stopped', updated_at=now()
                   FROM serp_supply_campaigns r
                   WHERE r.campaign_id=c.id AND r.run_id=%s AND c.status='active'
                   AND NOT EXISTS (
                     SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id
                     AND f.state='running' AND f.lease_expires_at>now()
                   )
                   AND (
                     (%s AND c.created_at<now()-interval '4 hours')
                     OR (
                       EXISTS(SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id)
                       AND NOT EXISTS (
                         SELECT 1 FROM google_page_frontier f WHERE f.campaign_id=c.id
                         AND (NOT %s OR f.enabled) AND (f.state<>'completed' OR (%s AND f.max_page<11))
                       )
                     )
                   )""",
                (run, pool is None, pool is not None, pool is not None),
            )
            active = db.execute("SELECT count(*) n FROM campaigns c JOIN serp_supply_campaigns r ON r.campaign_id=c.id WHERE r.run_id=%s AND c.status='active'", (run,)).fetchone()['n']
            cursor = row['cursor']
            custom = settings['continuous_keywords']
            target = min(256, (len(custom)+7)//8) if custom else 256
            if pool is not None:
                if settings['web_max_pages'] != 11:
                    raise RuntimeError('random Web keyword supply requires the first 11 pages')
                capacity = refill_capacity(db, run, self.config.crawler_slots)
                batches = ([query] for query in claim_queries(db, pool, capacity))
            else:
                batches = self.legacy_batches(row, cursor, max(0, target-active), custom, keyword, CATALOG_SIZE)
            for words in batches:
                cid = str(uuid4())
                db.execute("INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) VALUES(%s,%s,%s::jsonb,1000000,%s,%s::jsonb,'active')", (cid,words[0],json.dumps(words[1:]),self.config.default_proxy_profile,json.dumps(sources)))
                db.execute('INSERT INTO serp_supply_campaigns(run_id,campaign_id,sequence) VALUES(%s,%s,%s)', (run,cid,cursor))
                cursor += 1
            db.execute('UPDATE serp_supply_runs SET cursor=%s WHERE run_id=%s',(cursor,run))
            rows = db.execute("SELECT c.id FROM campaigns c JOIN serp_supply_campaigns r ON r.campaign_id=c.id WHERE r.run_id=%s AND c.status='active' AND NOT r.queued", (run,)).fetchall()
        for item in rows:
            self.queue.enqueue(str(item['id']))
        if rows:
            with self.store.connect() as db:
                db.execute('UPDATE serp_supply_campaigns SET queued=true WHERE campaign_id=ANY(%s::uuid[])', ([str(r['id']) for r in rows],))

    def legacy_batches(self, row, cursor, count, custom, keyword, catalog_size):
        for sequence in range(cursor, cursor + count):
            start = sequence * 8 + self.config.serp_keyword_offset
            cycle = (start + 7) // catalog_size
            refresh = row['started_at'].date() + timedelta(days=max(0, cycle - 1))
            if not custom and cycle and refresh > datetime.now(timezone.utc).date():
                break
            if custom:
                yield [custom[(start + j) % len(custom)] for j in range(min(8, len(custom)))]
            else:
                yield [
                    keyword(start + j, (row['started_at'].date() + timedelta(
                        days=max(0, (start + j) // catalog_size - 1))).isoformat())
                    for j in range(8)
                ]
