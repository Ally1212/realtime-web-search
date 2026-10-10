"""Repair SERP tracking aliases only; default to a rolled-back rehearsal."""
import argparse
import json
from datetime import datetime

from realtime.config import Config
from realtime.serp_store import SCHEMA, SerpStore


def repair(*, apply=False, expected_updated_at=None):
    if apply and expected_updated_at is None:
        raise ValueError('apply requires the maintenance settings timestamp')
    s=SerpStore(Config().database_url)
    with s.store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(20261010,1)')
        settings=db.execute('SELECT * FROM serp_collector_settings WHERE id=true FOR UPDATE').fetchone()
        cfg=settings.get('runtime_config') or {}
        if cfg.get('continuous_enabled') is not False or settings['content_fetch_enabled'] is not False or cfg.get('continuous_sources')!=['google_web']:
            raise RuntimeError('pause pure Web collection and keep body disabled before repair')
        if expected_updated_at and settings['updated_at']!=datetime.fromisoformat(expected_updated_at.replace('Z','+00:00')):
            raise RuntimeError('operator changed settings; do not repair or automatically resume')
        running=db.execute("SELECT count(*) n FROM google_page_frontier WHERE state='running' AND lease_expires_at>now()").fetchone()['n']
        manual=db.execute("SELECT count(*) n FROM campaigns c WHERE c.status='active' AND NOT EXISTS(SELECT 1 FROM serp_supply_campaigns s WHERE s.campaign_id=c.id)").fetchone()['n']
        if running or manual:
            raise RuntimeError('in-flight or manual tasks remain')
        pages=db.execute('SELECT count(*) n,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
        before=db.execute('SELECT count(*) n FROM serp_urls').fetchone()['n']
        requests=db.execute('SELECT count(*) n FROM serp_requests').fetchone()['n']
        db.execute('CREATE TEMP TABLE retained_serp_url_ids ON COMMIT DROP AS SELECT url_hash FROM serp_urls')
        db.execute(SCHEMA)
        changes=s.reconcile_tracking_aliases(db)
        missing=db.execute('SELECT count(*) n FROM retained_serp_url_ids old WHERE NOT EXISTS(SELECT 1 FROM serp_urls u WHERE u.url_hash=old.url_hash)').fetchone()['n']
        after=db.execute('SELECT count(*) n FROM serp_urls').fetchone()['n']
        after_requests=db.execute('SELECT count(*) n FROM serp_requests').fetchone()['n']
        after_pages=db.execute('SELECT count(*) n,max(id) max_id,max(fetched_at) latest FROM pages').fetchone()
        if missing or after<before or requests!=after_requests or pages!=after_pages:
            raise RuntimeError('retention/body invariant failed; rollback repair')
        windows={}
        for name,start,end in [
            ('baseline','2026-10-10T09:44:01.698555+00:00','2026-10-10T11:14:45.134250+00:00'),
            ('optimized_before_pause','2026-10-10T11:14:45.134250+00:00','2026-10-10T12:29:29.206724+00:00')]:
            windows[name]=db.execute("SELECT count(*) n FROM serp_urls u JOIN serp_requests r ON r.request_id=u.first_request_id WHERE NOT u.historical AND u.canonical_url_hash IS NULL AND r.source='google_web' AND u.first_seen>=%s AND u.first_seen<%s",(start,end)).fetchone()['n']
        aliases=db.execute('SELECT count(*) n FROM serp_urls WHERE canonical_url_hash IS NOT NULL').fetchone()['n']
        backed_up_urls=db.execute('SELECT count(*) n FROM serp_urls WHERE normalization_original IS NOT NULL').fetchone()['n']
        backed_up_requests=db.execute('SELECT count(*) n FROM serp_requests WHERE normalization_original_response IS NOT NULL').fetchone()['n']
        report=dict(apply=apply,dry_run_rolled_back=not apply,changes=changes,
            retained_ids_missing=missing,url_rows_before=before,url_rows_after=after,
            requests_before=requests,requests_after=after_requests,pages_before=pages,pages_after=after_pages,
            aliases=aliases,backed_up_urls=backed_up_urls,backed_up_requests=backed_up_requests,
            corrected_windows=windows,settings_updated_at=settings['updated_at'])
        if not apply:
            db.rollback()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--expected-settings-updated-at')
    args=parser.parse_args()
    print(json.dumps(repair(apply=args.apply,expected_updated_at=args.expected_settings_updated_at),default=str))
