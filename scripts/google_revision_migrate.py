"""One-shot stopped-writer migration. Output contains metadata only."""
import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def page_snapshot(db):
    return db.execute("""WITH fingerprints AS MATERIALIZED (
        SELECT id,trim(content_hash) stored,content<>'' nonempty,
               encode(sha256(convert_to(content,'UTF8')),'hex') actual FROM pages)
        SELECT count(*) pages,count(*) FILTER (WHERE nonempty AND actual<>stored) current_hash_mismatches,
          encode(sha256(convert_to(coalesce(string_agg(id::text||':'||stored||':'||actual,'|' ORDER BY id),''),
                                   'UTF8')),'hex') rows_digest FROM fingerprints""").fetchone()


def main():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.conninfo import make_conninfo
    import realtime.campaign_store as module
    from realtime.config import Config
    expected = sys.argv[1]
    predecessor_run = sys.argv[2]
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != expected:
        raise RuntimeError('migration package identity differs')
    dsn = make_conninfo(Config().database_url,
        options='-c lock_timeout=15000 -c statement_timeout=600000')
    # Probe before opening the store pool so our own idle pool members cannot
    # be mistaken for a surviving old writer.
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as db:
        other_clients = db.execute("SELECT count(*) n FROM pg_stat_activity WHERE datname=current_database() "
                                  "AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()['n']
        if other_clients:
            raise RuntimeError('old database clients still connected')
        external_active = db.execute("SELECT count(*) n FROM campaigns c WHERE c.status='active' AND NOT EXISTS "
            "(SELECT 1 FROM wml_continuous_campaigns r WHERE r.campaign_id=c.id AND r.run_id=%s)",
            (predecessor_run,)).fetchone()['n']
        if external_active:
            raise RuntimeError('unexpected active campaign ownership')
        before = page_snapshot(db)
        admission_before = db.execute('SELECT count(*) n FROM page_admission_audit').fetchone()['n']
        retired = db.execute("UPDATE campaigns c SET status='stopped',updated_at=now() WHERE c.status='active' "
            "AND EXISTS (SELECT 1 FROM wml_continuous_campaigns r WHERE r.campaign_id=c.id AND r.run_id=%s)",
            (predecessor_run,)).rowcount
    # All old writers must have been stopped by the host before invoking this.
    store = module.CampaignStore(dsn, initialize=False)
    store.initialize()
    with store.connect() as db:
        after = page_snapshot(db)
        admission_after = db.execute('SELECT count(*) n FROM page_admission_audit').fetchone()['n']
        history = db.execute('SELECT count(*) n FROM page_content_hashes').fetchone()['n']
        missing_stored = db.execute('SELECT count(*) n FROM pages p WHERE NOT EXISTS '
            '(SELECT 1 FROM page_content_hashes h WHERE h.content_hash=p.content_hash)').fetchone()['n']
        missing_original = db.execute('SELECT count(*) n FROM page_admission_audit a WHERE NOT EXISTS '
            '(SELECT 1 FROM page_content_hashes h WHERE h.content_hash=a.original_content_hash)').fetchone()['n']
        marker_present = module.CampaignStore._schema_is_current(db, store._schema_marker())
    # A distinct DSN and connection prove the cold constructor needs no DDL.
    readonly = module.CampaignStore(make_conninfo(Config().database_url,
        options='-c default_transaction_read_only=on'))
    with readonly.connect() as db:
        readonly_state = db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
    checks = dict(old_clients_absent=other_clients == 0, unrelated_active_absent=external_active == 0,
        old_page_counts_unchanged=before['pages'] == after['pages'],
        old_page_content_and_hash_digests_unchanged=before['rows_digest'] == after['rows_digest'],
        old_current_hash_mismatch_count_unchanged=before['current_hash_mismatches'] == after['current_hash_mismatches'],
        old_admission_counts_unchanged=admission_before == admission_after,
        retained_stored_hashes_covered=missing_stored == 0,
        retained_original_hashes_covered=missing_original == 0,
        new_schema_marker_committed=marker_present, cold_readonly_constructor_succeeded=readonly_state == 'on')
    result = dict(at=datetime.now(timezone.utc).isoformat(), predecessor_run=predecessor_run,
        imported_store_file=module.__file__, imported_store_sha256=expected, before=before, after=after,
        retired_active_campaigns=retired, original_admission_count=admission_after,
        retained_history_hashes=history, readonly_constructor_state=readonly_state,
        checks=checks, all_checks_passed=all(checks.values()),
        limits='Retained hashes are backfilled without modifying old page contents/hash or merging legacy collisions. '
               'Unavailable intermediate historical body versions cannot be restored. '
               'Old current hash mismatches remain until a verifiable future refresh.')
    print(json.dumps(result, indent=2), flush=True)
    if not result['all_checks_passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(migration_error=type(exc).__name__,
            frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name)
                    for f in traceback.extract_tb(exc.__traceback__)])), flush=True)
        raise SystemExit(1)
