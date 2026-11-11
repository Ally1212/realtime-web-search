"""Trusted bounded job/retry-content erasure; preserve monetary audit state."""
from .data_capability_jobs import digest, job_id, TERMINAL
from .data_capability_responses import safe_code
from .product import ProductError

MAX_JOBS = 1000


def assert_live(db, tenant, jid):
    if db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE job_id=%s AND tenant_id=%s',(job_id(jid),str(tenant))).fetchone():
        raise ProductError('expired','任务查询内容已清除',410)


def clear_row(db, row, now, reason):
    """Row is locked; called by explicit erasure or signed checkpoint replay."""
    marker=db.execute('SELECT * FROM sf_capability_job_erasures WHERE job_id=%s',(row['id'],)).fetchone()
    if marker is not None and (marker['tenant_id']!=row['tenant_id'] or marker['request_hash']!=row['request_hash']):
        raise ProductError('erasure_checkpoint_conflict','任务墓碑身份不一致，整批回滚',409)
    if marker is None:
        db.execute('INSERT INTO sf_capability_job_erasures VALUES(%s,%s,%s,%s,%s,%s)',
                   (row['id'],row['tenant_id'],row['request_hash'],digest(row['idempotency_key']),now,reason))
    if row['status']=='queued':
        if row['actual_cost'] is not None:
            raise ProductError('erasure_checkpoint_conflict','未执行任务含已有费用，拒绝改写',409)
        db.execute("UPDATE sf_capability_jobs SET status='canceled',cancel_requested=true,actual_cost=0,completed_at=%s WHERE id=%s",(now,row['id']))
    elif row['status'] not in TERMINAL:
        db.execute('UPDATE sf_capability_jobs SET cancel_requested=true WHERE id=%s',(row['id'],))
    db.execute("UPDATE sf_capability_jobs SET parameters='{}',idempotency_key=%s,reconciliation_reason=NULL WHERE id=%s",
               ('erased:'+str(row['id']),row['id']))
    db.execute("UPDATE sf_capability_job_events SET detail='{}' WHERE job_id=%s",(row['id'],))
    db.execute("UPDATE sf_capability_retries SET plan='{}' WHERE tenant_id=%s AND (parent_job_id=%s OR child_job_id=%s)",
               (row['tenant_id'],row['id'],row['id']))
    if db.execute("SELECT to_regclass('sf_capability_responses') AS t").fetchone()['t'] is not None:
        from .data_capability_retention import record
        response=db.execute('SELECT id,job_id,tenant_id FROM sf_capability_responses WHERE job_id=%s FOR UPDATE',(row['id'],)).fetchone()
        if response:
            if response['tenant_id']!=row['tenant_id']:
                raise ProductError('erasure_checkpoint_conflict','响应租户身份不一致，整批回滚',409)
            record(db,response,now,reason)


class JobRetention:
    def __init__(self, service):self.service=service

    def _closure(self, db, tenant, jid):
        jobs={job_id(jid)};observations=set()
        while True:
            before=(len(jobs),len(observations))
            links=db.execute('SELECT a.observation_id,o.tenant_id FROM sf_capability_job_observations a '
                             'JOIN sf_capability_observations o ON o.id=a.observation_id WHERE a.job_id=ANY(%s::uuid[])',
                             (list(jobs),)).fetchall()
            if any(r['tenant_id']!=tenant for r in links):
                raise ProductError('erasure_checkpoint_conflict','任务关联了其他租户事实',409)
            observations.update(str(r['observation_id']) for r in links)
            if observations:
                aliases=db.execute('SELECT j.id,j.tenant_id FROM sf_capability_jobs j JOIN sf_capability_job_observations a '
                                   'ON a.job_id=j.id WHERE a.observation_id=ANY(%s::uuid[]) LIMIT %s',(list(observations),MAX_JOBS+1)).fetchall()
                if any(r['tenant_id']!=tenant for r in aliases):
                    raise ProductError('erasure_checkpoint_conflict','缓存别名租户不一致',409)
                jobs.update(str(r['id']) for r in aliases)
            children=db.execute('SELECT child_job_id FROM sf_capability_retries WHERE tenant_id=%s AND '
                                '(parent_job_id=ANY(%s::uuid[]) OR parent_observation_id=ANY(%s::uuid[])) LIMIT %s',
                                (tenant,list(jobs),list(observations),MAX_JOBS+1)).fetchall()
            jobs.update(str(r['child_job_id']) for r in children)
            if len(jobs)>MAX_JOBS or len(observations)>MAX_JOBS:
                raise ProductError('erasure_checkpoint_limit','任务/别名/派生重试超过单次清除上限；整批回滚',409)
            if before==(len(jobs),len(observations)):return sorted(jobs),sorted(observations)

    def erase(self, tenant, jid, *, reason):
        from .data_capability_fact_retention import FactRetention
        safe_code(reason);tenant=str(tenant)
        with self.service.connect() as db:
            # Same recovery ordering; retry creation also takes tenant lock.
            db.execute('SELECT pg_advisory_xact_lock(20261110,30)')
            db.execute('SELECT pg_advisory_xact_lock(20261110,29)')
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))',(tenant,))
            self.service._get(db,tenant,jid)
            jobs,observations=self._closure(db,tenant,jid)
            now=self.service.now();created=0
            for value in jobs:
                row=self.service._get(db,tenant,value,lock=True)
                marker=db.execute('SELECT 1 FROM sf_capability_job_erasures WHERE job_id=%s',(row['id'],)).fetchone()
                created+=int(marker is None)
                clear_row(db,row,now,reason)
            for oid in observations:
                row=db.execute('SELECT id,tenant_id,event_key FROM sf_capability_observations WHERE id=%s FOR UPDATE',(oid,)).fetchone()
                marker=db.execute('SELECT * FROM sf_capability_fact_erasures WHERE observation_id=%s',(oid,)).fetchone()
                FactRetention._clear(db,row,marker['erased_at'] if marker else now,marker['reason'] if marker else reason)
            return dict(jobs=len(jobs),new_job_markers=created,observations=len(observations),
                        scope='job_content_cache_aliases_descendant_retries_and_facts')
