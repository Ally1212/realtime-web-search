"""Trusted local observation erasure; audit hashes survive, fact content does not.

Associated response spools are erased if installed; job parameters, platform and
backup checkpoint replay remain separate responsibilities.
"""
from .data_capability_jobs import digest, job_id
from .data_capability_responses import safe_code
from .product import ProductError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_fact_erasures (
 observation_id uuid PRIMARY KEY, tenant_id text NOT NULL,
 event_digest text NOT NULL, erased_at timestamptz NOT NULL, reason text NOT NULL,
 UNIQUE(tenant_id,event_digest)
);
"""


def assert_live(db, tenant, *, oid=None, event_key=None):
    column, value = ('observation_id', job_id(oid)) if oid is not None else ('event_digest', digest(event_key))
    if db.execute('SELECT 1 FROM sf_capability_fact_erasures WHERE tenant_id=%s AND '+column+'=%s',
                  (tenant, value)).fetchone():
        raise ProductError('expired', '事实已清除，不能读取、复用或重新发布', 410)


class FactRetention:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _clear(db, row, now, reason):
        db.execute('INSERT INTO sf_capability_fact_erasures VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                   (row['id'], row['tenant_id'], digest(row['event_key']), now, reason))
        if db.execute("SELECT to_regclass('sf_capability_responses') AS table_name").fetchone()['table_name'] is not None:
            from .data_capability_retention import record
            responses=db.execute('SELECT r.id,r.job_id,r.tenant_id FROM sf_capability_responses r '
                                 'JOIN sf_capability_job_observations a ON a.job_id=r.job_id '
                                 'WHERE a.observation_id=%s AND r.tenant_id=%s FOR UPDATE OF r',
                                 (row['id'],row['tenant_id'])).fetchall()
            for response in responses:
                record(db,response,now,reason)
        db.execute('DELETE FROM sf_capability_fact_rows WHERE observation_id=%s', (row['id'],))
        db.execute("UPDATE sf_capability_observations SET payload='{}',parameters='{}',revision=NULL,event_key=%s WHERE id=%s",
                   ('erased:'+str(row['id']), row['id']))

    def erase(self, tenant, oid, *, reason):
        safe_code(reason)
        with self.service.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            # Same event lock as completion prevents an old event being inserted
            # while its identity is being replaced by an opaque erasure marker.
            row=db.execute('SELECT id,tenant_id,event_key FROM sf_capability_observations WHERE id=%s AND tenant_id=%s',
                           (job_id(oid), tenant)).fetchone()
            if not row:
                raise ProductError('not_found', '观测不存在', 404)
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))', (tenant+':'+row['event_key'],))
            row=db.execute('SELECT id,tenant_id,event_key FROM sf_capability_observations WHERE id=%s AND tenant_id=%s FOR UPDATE',
                           (job_id(oid),tenant)).fetchone()
            if db.execute('SELECT 1 FROM sf_capability_fact_erasures WHERE observation_id=%s',(row['id'],)).fetchone():
                return False
            self._clear(db,row,self.service.now(),reason)
            return True

    def purge_expired(self, tenant, *, limit=100):
        if type(limit) is not int or not 1<=limit<=1000:
            raise ProductError('invalid_parameter', '清理批次范围为1到1000')
        # No payloads selected. Individual transactions bound locks, preserve
        # the event/completion ordering and allow crash-safe continuation.
        with self.service.connect() as db:
            rows=db.execute('SELECT o.id FROM sf_capability_observations o WHERE tenant_id=%s AND expires_at<=%s '
                            'AND NOT EXISTS(SELECT 1 FROM sf_capability_fact_erasures e WHERE e.observation_id=o.id) '
                            'ORDER BY expires_at,id LIMIT %s',(tenant,self.service.now(),limit+1)).fetchall()
        removed=sum(self.erase(tenant,str(row['id']),reason='expired') for row in rows[:limit])
        return dict(erased=removed,has_more=len(rows)>limit)
