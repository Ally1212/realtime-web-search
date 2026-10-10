"""Container-side fixed-window controller. Invoked by the durable host series."""
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def url_hash(url):
    from realtime.fetcher import normalize_url
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def link_metrics(attempts, baseline):
    urls = {url_hash(url) for row in attempts for url in row['result_urls']}
    return dict(unique_result_links=len(urls), valid_new_links=len(urls - baseline),
                result_urls_recorded=sum(len(row['result_urls']) for row in attempts),
                result_url_audit_complete=all(
                    row['result_count'] == 0 or bool(row['result_urls']) for row in attempts))


def is_captcha_attempt(row):
    # Older isolated browsers returned the explicit block code, but omitted
    # page evidence, causing discovery to store classification='error'.
    return row['classification'] == 'captcha' or row.get('error_code') == 'google_captcha'


def controller(state, manifest):
    module = load(state / 'controller.py', 'hour_controller')
    variant = load(state / 'world_keywords.py', 'hour_keywords')
    variant.configure(module)
    module.RUN = manifest['run']
    module.PROVIDER = manifest['provider']
    module.SOURCES = manifest['sources']
    module.DIRECTORY = state
    module.DEADLINE = datetime.fromisoformat(manifest['deadline'])
    module.LANGUAGE_INDICES = (1,)
    module.KEYWORD_OFFSET = manifest['keyword_offset']
    original = module.keyword
    def keyword(index, language, date):
        cycle, position = divmod(index, module.CATALOG_SIZE)
        shifted = cycle * module.CATALOG_SIZE + (position + module.KEYWORD_OFFSET) % module.CATALOG_SIZE
        return original(shifted, language, date)
    module.keyword = keyword
    # Avoid writing 120,000 words repeatedly; retain the frozen deterministic catalog
    # and the exact queued campaign queries in PostgreSQL.
    module.materialize_catalog = lambda: None
    return module


def baseline_urls(db, state):
    # Old attempts did not retain URLs; include the retained discovery cache,
    # all existing pages and crawl events as the historical baseline.
    hashes = set()
    for row in db.execute(
        "SELECT url FROM pages UNION SELECT url FROM crawl_events WHERE url LIKE 'http%' "
        "UNION SELECT item->>'url' FROM discovery_query_cache, jsonb_array_elements(payload) item "
        "UNION SELECT jsonb_array_elements_text(result_urls) FROM google_serp_attempts"
    ):
        if row['url']:
            try:
                hashes.add(url_hash(row['url']))
            except ValueError:
                pass
    (state / 'baseline-url-hashes.txt').write_text('\n'.join(sorted(hashes)) + '\n')
    return len(hashes)


def metrics(db, state, manifest, now):
    run = db.execute('SELECT * FROM wml_continuous_runs WHERE id=%s', (manifest['run'],)).fetchone()
    start = run['started_at']
    end = min(now, datetime.fromisoformat(manifest['deadline']))
    if manifest.get('continuous'):
        start = max(start, end - timedelta(hours=1))
    attempts = db.execute(
        'SELECT provider,classification,result_count,result_urls,error_code FROM google_serp_attempts '
        'WHERE created_at>=%s AND created_at<%s ORDER BY id', (start, end)
    ).fetchall()
    # First association is computed over the entire run, before filtering an hour.
    # Mutable pages.fetched_at/content/source_engines cannot reconstruct original admission.
    admitted = (
        "WITH first_admission AS (SELECT cp.page_id,min(cp.first_seen) admitted_at "
        "FROM campaign_pages cp JOIN wml_continuous_campaigns c ON c.campaign_id=cp.campaign_id "
        "WHERE c.run_id=%s AND cp.page_id>%s GROUP BY cp.page_id) ")
    missing_bodies = db.execute(admitted +
        "SELECT count(*) n FROM first_admission f LEFT JOIN page_admission_audit a ON a.page_id=f.page_id "
        "WHERE f.admitted_at>=%s AND f.admitted_at<%s AND a.page_id IS NULL",
        (manifest['run'], run['baseline_page_id'], start, end)).fetchone()['n']
    sources = db.execute(admitted +
        "SELECT a.original_source_engines source_engines,count(*) n FROM first_admission f "
        "JOIN page_admission_audit a ON a.page_id=f.page_id "
        "WHERE f.admitted_at>=%s AND f.admitted_at<%s AND a.original_fetched_at>=%s "
        "AND a.original_fetched_at<%s AND a.original_body_chars>=500 "
        "GROUP BY a.original_source_engines",
        (manifest['run'], run['baseline_page_id'], start, end, start, end)).fetchall()
    baseline = set((state / 'baseline-url-hashes.txt').read_text().splitlines())
    urls = list({url for row in attempts for url in row['result_urls']})
    if urls:
        baseline.update(url_hash(row['url']) for row in db.execute(
            'SELECT url FROM google_known_result_urls WHERE url=ANY(%s) AND first_seen<%s', (urls, start)))
    errors = {}
    for row in attempts:
        if row['error_code']:
            errors[row['error_code']] = errors.get(row['error_code'], 0) + 1
    captcha = sum(is_captcha_attempt(row) for row in attempts)
    report = dict(run=manifest['run'], provider=manifest['provider'], sources=manifest['sources'],
                  run_started_at=run['started_at'], rolling_hour=manifest.get('continuous', False),
                  started_at=start, measured_until=end, elapsed_seconds=(end-start).total_seconds(),
                  full_hour_observed=(end-start).total_seconds() >= 3600,
                  google_searches=len(attempts), successful_searches=sum(
                      row['classification'] == 'results' for row in attempts),
                  captcha_attempts=captcha, captcha_rate=captcha/len(attempts) if attempts else None,
                  raw_result_count=sum(row['result_count'] for row in attempts), errors=errors,
                  new_qualified_bodies=sum(row['n'] for row in sources), source_combinations=sources,
                  body_admission_audit_complete=missing_bodies == 0,
                  missing_body_admission_rows=missing_bodies,
                  exclusive_provider=all(row['provider'] == manifest['provider'] for row in attempts),
                  baseline_known_urls=len(baseline), **link_metrics(attempts, baseline),
                  definition='Completed Google SERP calls in [worker start, worker start+3600s); '
                  'links are eligible public HTTP(S) result URLs normalized and deduplicated against retained '
                  'historical links/pages/events; bodies use immutable first-insert metadata and '
                  'first run association, globally URL/hash unique and >=500 chars at initial admission. '
                  'Historical URLs absent from retained records cannot be excluded; links do not imply fetch success.')
    report['alerts'] = []
    if len(attempts) >= 20 and report['successful_searches']/len(attempts) < .05:
        report['alerts'].append('Google result success below 5%')
    if report['full_hour_observed'] and not report['new_qualified_bodies']:
        report['alerts'].append('No qualified new bodies in the measured hour')
    if manifest.get('auxiliary_audit'):
        from realtime.discovery_audit_metrics import read_google_discovery_window
        from realtime.google_run_audit import read_first_body_window
        campaign_ids = [str(row['campaign_id']) for row in db.execute(
            'SELECT campaign_id FROM wml_continuous_campaigns WHERE run_id=%s', (manifest['run'],))]
        report['first_body_integrity'] = read_first_body_window(
            db, manifest['run'], start, end, report['new_qualified_bodies'], missing_bodies)
        report['body_integrity_valid'] = report['first_body_integrity']['all_checks_passed']
        try:
            enabled = datetime.fromisoformat(manifest['auxiliary_audit']['enabled_since'])
            enablement_valid = bool(manifest['auxiliary_audit']['revision'] and enabled.tzinfo and enabled <= start)
        except (KeyError, TypeError, ValueError):
            enablement_valid = False
        report['auxiliary_enablement_valid'] = enablement_valid
        if enablement_valid:
            report['google_source_audit'] = read_google_discovery_window(db, campaign_ids, start, end, baseline)
            report['completed_google_keyword_searches'] = report['google_source_audit']['completed_google_keyword_search_calls']
            report['all_source_valid_new_links'] = report['google_source_audit']['combined_valid_new_discovery_links']
            auxiliary = report['google_source_audit']['auxiliary']
            report['extended_audit_complete'] = bool(
                auxiliary['request_audit_complete'] and auxiliary['discovery_batch_audit_complete']
                and report['body_integrity_valid'])
        else:
            report['google_source_audit'] = dict(status='unavailable', metrics=None)
            report['extended_audit_complete'] = False
        if not report['extended_audit_complete']:
            report['alerts'].append('Incomplete auxiliary evidence or body integrity failure')
    return report


def controller_store(dsn, action):
    """Reuse verified schema for live control; never run collector DDL on every tick."""
    from realtime.campaign_store import CampaignStore, _INITIALIZED_DSNS, _INITIALIZE_LOCK

    if action not in {'prepare', 'anchor', 'tick', 'finish'}:
        raise ValueError('Unknown action')
    if action == 'prepare':
        return CampaignStore(dsn)
    store = CampaignStore(dsn, initialize=False)
    # Fail before writes if this process has not inherited the prepared schema.
    # These zero-row reads validate the actual columns without taking DDL locks.
    with store.connect() as db:
        for table, columns in (
            ('campaigns', 'id,query,aliases,sources,status,created_at,updated_at'),
            ('pages', 'id,url,content_hash,content,fetched_at,source_engines,indexed_at'),
            ('campaign_pages', 'campaign_id,page_id,first_seen'),
            ('crawl_events', 'campaign_id,url,status,created_at'),
            ('google_page_frontier', 'campaign_id,state,lease_expires_at'),
            ('google_serp_attempts', 'provider,classification,result_count,result_urls,error_code,created_at'),
            ('google_known_result_urls', 'url,first_seen'),
            ('page_admission_audit', 'page_id,original_content_hash,original_body_chars,original_fetched_at,original_source_engines'),
            ('wml_continuous_runs', 'id,started_at,baseline_page_id,cursor,cycle_date'),
            ('wml_continuous_campaigns', 'run_id,campaign_id,sequence,queued'),
        ):
            db.execute(f'SELECT {columns} FROM {table} LIMIT 0')
    # The frozen run controller constructs CampaignStore again inside tick/finish.
    # Register only this process's verified DSN using the class's normal cache/lock.
    # No frozen collector or scheduling code needs modification.
    with _INITIALIZE_LOCK:
        _INITIALIZED_DSNS.add(dsn)
    return store


def main():
    from realtime.config import Config
    state = Path('/app/state') / sys.argv[1]
    action = sys.argv[2]
    manifest = json.loads((state / 'manifest.json').read_text())
    module = controller(state, manifest)
    store = controller_store(Config().database_url, action)
    if action == 'prepare':
        with store.connect() as db:
            if db.execute("SELECT count(*) n FROM campaigns WHERE status='active'").fetchone()['n']:
                raise RuntimeError('Another campaign is active')
            baseline_urls(db, state)
        module.tick()
    elif action == 'anchor':
        start = datetime.fromisoformat(sys.argv[3])
        manifest['deadline'] = (start + timedelta(seconds=manifest['duration_seconds'])).isoformat()
        manifest['worker_started_at'] = start.isoformat()
        if manifest.get('auxiliary_audit'):
            manifest['auxiliary_audit']['enabled_since'] = start.isoformat()
        with store.connect() as db:
            db.execute('UPDATE wml_continuous_runs SET started_at=%s WHERE id=%s', (start, manifest['run']))
        module.save('manifest.json', manifest)
    elif action == 'finish':
        module.finish()
    elif action == 'tick':
        module.tick()
    else:
        raise ValueError('Unknown action')
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report = metrics(db, state, manifest, datetime.now(timezone.utc))
    module.save('hourly-report.json', report)
    if action == 'finish':
        module.save('hourly-final.json', report)
    print(json.dumps(report, default=str, ensure_ascii=False))


if __name__ == '__main__':
    main()
