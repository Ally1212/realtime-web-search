"""Local result fixture for store/export tests; not a production execution path."""
from realtime.google_local_jobs import LocalJobStore
from realtime.maps_provider import search as fetch_search
from realtime.product import ProductError


def process_pending(base, limit=1):
    import os
    if os.getenv("SEARCHFLOW_MAPS_WORKER") != "true": return
    store = LocalJobStore(base)
    with base.connect() as db:
        stale = db.execute("SELECT * FROM sf_google_local_jobs WHERE (status='running' AND coalesce(started_at,created_at)<now()-interval '3 minutes') OR (status='queued' AND created_at<now()-interval '5 minutes') FOR UPDATE").fetchall()
        for row in stale:
            db.execute("UPDATE sf_months SET reserved=greatest(0,reserved-1) WHERE account_id=%s AND month=%s", (row["account_id"], row["quota_month"]))
            db.execute("UPDATE sf_google_local_jobs SET status='failed',error_code='worker_interrupted',completed_at=now() WHERE id=%s", (row["id"],))
        rows = db.execute("UPDATE sf_google_local_jobs SET status='running',started_at=now() WHERE id IN (SELECT id FROM sf_google_local_jobs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s) RETURNING *", (limit,)).fetchall()
        db.execute("UPDATE sf_google_local_jobs SET result_cache=NULL WHERE result_expires_at<=now() AND result_cache IS NOT NULL")
    for row in rows:
        try:
            store.finish(row, result=fetch_search(row["parameters"]))
        except ProductError as exc:
            store.finish(row, error=exc.code)
        except Exception:
            store.finish(row, error="maps_worker_error")
