"""Trusted subscription metadata erasure, preserving facts and monetary ledgers.

No customer delete route; v3 joint checkpoints also restore these markers.
"""
from .data_capability_jobs import digest, job_id
from .data_capability_responses import safe_code
from .product import ProductError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_subscription_erasures (
 subscription_id uuid PRIMARY KEY, tenant_id text NOT NULL, request_hash text NOT NULL,
 idempotency_digest text NOT NULL, erased_at timestamptz NOT NULL, reason text NOT NULL,
 UNIQUE(tenant_id,idempotency_digest)
);
"""


def assert_live(db, tenant, sid):
    if db.execute('SELECT 1 FROM sf_capability_subscription_erasures WHERE subscription_id=%s AND tenant_id=%s',
                  (job_id(sid),str(tenant))).fetchone():
        raise ProductError('expired','订阅内容已清除',410)


def clear_row(db,row):
    """Caller holds the owned subscription row lock and has validated identity."""
    db.execute("UPDATE sf_capability_subscriptions SET name='',filters='{}',active=false,next_poll_at=NULL,"
               'idempotency_key=%s WHERE id=%s',('erased:'+str(row['id']),row['id']))
    # Delivery references/checksums and seen membership are not needed
    # after this subscription is permanently inaccessible.
    batches=db.execute('DELETE FROM sf_capability_batches WHERE subscription_id=%s',(row['id'],)).rowcount
    seen=db.execute('DELETE FROM sf_capability_subscription_seen WHERE subscription_id=%s',(row['id'],)).rowcount
    return batches,seen


class SubscriptionRetention:
    def __init__(self, service):self.service=service

    def erase(self, tenant, sid, *, reason):
        safe_code(reason);tenant=str(tenant);sid=job_id(sid)
        with self.service.connect() as db:
            # Same ordering as recovery, then subscription creation/row locks.
            db.execute('SELECT pg_advisory_xact_lock(20261110,30)')
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261112))',(tenant,))
            row=db.execute('SELECT id,tenant_id,request_hash,idempotency_key FROM sf_capability_subscriptions '
                           'WHERE id=%s AND tenant_id=%s FOR UPDATE',(sid,tenant)).fetchone()
            if row is None:raise ProductError('not_found','订阅不存在',404)
            marker=db.execute('SELECT * FROM sf_capability_subscription_erasures WHERE subscription_id=%s',(sid,)).fetchone()
            if marker is not None and (marker['tenant_id']!=tenant or marker['request_hash']!=row['request_hash']):
                raise ProductError('erasure_checkpoint_conflict','订阅墓碑身份不一致；整批回滚',409)
            if marker is None:
                db.execute('INSERT INTO sf_capability_subscription_erasures VALUES(%s,%s,%s,%s,%s,%s)',
                           (sid,tenant,row['request_hash'],digest(row['idempotency_key']),self.service.now(),reason))
            batches,seen=clear_row(db,row)
            return dict(subscription_id=str(sid),created=marker is None,batches_removed=batches,seen_removed=seen,
                        facts_removed=0,source_calls=0)
