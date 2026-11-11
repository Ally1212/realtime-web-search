"""Explicit failed-input retries, immutable parent facts, separately billed child.

No automatic source call/retry, table initialization or service wiring on import.
"""
from __future__ import annotations
import copy
import re
from psycopg.types.json import Jsonb
from .data_capability_contract import validate_request,admit_request,VERSION
from .data_capability_facts import validate_result
from .data_capability_jobs import digest,amount,required_text
from .product import ProductError

SCHEMA="""
CREATE TABLE IF NOT EXISTS sf_capability_retries (
 parent_job_id uuid PRIMARY KEY REFERENCES sf_capability_jobs(id),
 child_job_id uuid NOT NULL UNIQUE REFERENCES sf_capability_jobs(id),
 tenant_id text NOT NULL, parent_observation_id uuid NOT NULL REFERENCES sf_capability_observations(id),
 parent_snapshot_hash text NOT NULL, plan_hash text NOT NULL,
 plan jsonb NOT NULL, created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sf_capability_retry_observation_once ON sf_capability_retries(tenant_id,parent_observation_id);
"""


def plan(snapshot,parent_job_id):
    """Only explicit unbound source_error inputs, never inferred missing data.

    A group with any saved metrics is not eligible for a whole-keyword rerun.
    A contradictory duplicate input is blocked rather than resent alongside a
    successful copy. Source-side merging is kept in the immutable parent.
    """
    p=validate_request(snapshot['parameters'])
    if p['capability']!='keyword_volume':raise ProductError('retry_not_supported','此重试合同仅支持关键词量的明确失败输入',409)
    f=validate_result(p,snapshot['payload']);rows={m['input_index']:m for m in f['data']['input_mapping']}
    eligible={m['input_index'] for m in rows.values() if m['status']=='source_error' and m['record_id'] is None}
    returned={g['returned_text'] for g in f['data']['groups']}
    selected=[i for i in range(len(p['keywords'])) if i in eligible and p['keywords'][i] not in returned and all(j in eligible for j,w in enumerate(p['keywords']) if w==p['keywords'][i])]
    excluded=[dict(input_index=i,status=rows[i]['status'],reason=('returned_group_has_same_query_text' if p['keywords'][i] in returned and i in eligible else 'source_error_with_saved_group_requires_narrower_source_retry' if rows[i]['status']=='source_error' and rows[i]['record_id'] is not None else 'duplicate_input_has_nonfailed_copy' if i in eligible else 'not_explicit_unbound_source_error')) for i in range(len(p['keywords'])) if i not in selected]
    child=copy.deepcopy(p);child['keywords']=[p['keywords'][i] for i in selected]
    result=dict(parent_job_id=str(parent_job_id),parent_observation_id=snapshot['observation_id'],parent_snapshot_hash=snapshot['snapshot_hash'],parent_payload_hash=snapshot['payload_hash'],
        capability=p['capability'],eligible=bool(selected),parameters=child if selected else None,
        input_mapping=[dict(child_input_index=j,parent_input_index=i,requested_query=p['keywords'][i]) for j,i in enumerate(selected)],excluded=excluded,
        policy='explicit_unbound_source_error_only;no_missing_or_successful_input_replay',
        billing='new_child_budget_and_confirmed_source_bill;parent_bill_unchanged',parent_facts='immutable_separate_observation;no_implicit_merge_or_revision')
    result['plan_hash']=digest(result);return result


def _parent(runtime,db,tenant,jid):
    row=runtime.service._get(db,str(tenant),jid,lock=True)
    grant=runtime._authorize_job(tenant,row)
    if row['status']!='partial' or row['actual_cost'] is None or row['cancel_requested']:
        raise ProductError('retry_not_eligible','仅已结算且未取消的部分成功任务可重试明确失败输入',409)
    link=db.execute('SELECT observation_id FROM sf_capability_job_observations WHERE job_id=%s',(row['id'],)).fetchone()
    if not link:raise ProductError('retry_not_eligible','父任务没有已保存的固定事实',409)
    source=runtime.service._observation(db,str(tenant),str(link['observation_id']),grant)
    snapshot=runtime.service._public_observation(source)
    return row,grant,plan(snapshot,jid)


def preview(runtime,tenant,jid):
    runtime.maintain(tenant)
    with runtime.service.connect() as db:
        row,grant,p=_parent(runtime,db,tenant,jid)
        previous=db.execute('SELECT child_job_id FROM sf_capability_retries WHERE tenant_id=%s AND (parent_job_id=%s OR parent_observation_id=%s)',(str(tenant),row['id'],p['parent_observation_id'])).fetchone()
        p['existing_child_job_id']=str(previous['child_job_id']) if previous else None
        p['creation_available']=p['eligible'] and not previous
        p['reservation_created']=False
        return p


def submit(runtime,tenant,jid,body,key):
    if not isinstance(body,dict) or set(body)!={'plan_hash','budget'}:raise ProductError('invalid_parameter','重试仅接受plan_hash和新的budget')
    if not isinstance(body['plan_hash'],str) or not re.fullmatch('[a-f0-9]{64}',body['plan_hash']):raise ProductError('invalid_parameter','plan_hash必须为64位小写摘要')
    budget=body['budget']
    if not isinstance(budget,dict) or set(budget)!={'maximum_cost','currency'}:raise ProductError('invalid_parameter','需要显式新的budget')
    ceiling=amount(budget['maximum_cost']);currency=budget['currency'];required_text(key,'Idempotency-Key',120)
    runtime.maintain(tenant)
    with runtime.service.connect() as db:
        # Same lock order as ordinary create/settlement, then parent row. The
        # child, its reservation and unique lineage commit in ONE transaction.
        db.execute('SELECT pg_advisory_xact_lock_shared(20261110,30)')
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))',(str(tenant),))
        row,grant,p=_parent(runtime,db,tenant,jid)
        if body['plan_hash']!=p['plan_hash']:raise ProductError('version_conflict','重试范围或父固定版本不符',409)
        if not p['eligible']:raise ProductError('retry_not_eligible','没有可安全重试的明确失败关键词',409)
        previous=db.execute('SELECT * FROM sf_capability_retries WHERE tenant_id=%s AND (parent_job_id=%s OR parent_observation_id=%s)',(str(tenant),row['id'],p['parent_observation_id'])).fetchone()
        if previous:
            child=runtime.service._get(db,str(tenant),str(previous['child_job_id']))
            runtime._authorize_job(tenant,child)
            if child['idempotency_key']!=key or previous['plan_hash']!=p['plan_hash']:
                raise ProductError('retry_already_created','此父版本已创建重试，请读取已有子任务，不重复收费',409)
        else:
            # Ordinary idempotency keys cannot be repurposed to attach an
            # already-created unrelated job, even if its parameters match.
            used=db.execute('SELECT id FROM sf_capability_jobs WHERE tenant_id=%s AND idempotency_key=%s',(str(tenant),key)).fetchone()
            if used:raise ProductError('idempotency_conflict','此幂等键属于其他任务',409)
            admitted=admit_request(p['parameters'],grant,ceiling,currency,now=runtime.service.now())
            if runtime.worker_ready(str(tenant),'keyword_volume',admitted['provider']) is not True:
                raise ProductError('executor_unavailable','来源无可用执行器，未创建重试或预留费用',503)
        child=runtime.service.create(str(tenant),p['parameters'],key,grant,ceiling,currency,_db=db)
        if not previous:
            db.execute('INSERT INTO sf_capability_retries VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],child['id'],str(tenant),p['parent_observation_id'],p['parent_snapshot_hash'],p['plan_hash'],Jsonb(p),runtime.service.now()))
        return dict(**child,domain='data',schema_version=VERSION,status_url='/v1/jobs/'+child['id'],data_url='/v1/jobs/'+child['id']+'/data',queue_timeout_seconds=runtime.queue_timeout,retry=p)


def lineage(runtime,tenant,jid):
    runtime.job(tenant,jid)
    with runtime.service.connect() as db:
        row=db.execute('SELECT * FROM sf_capability_retries WHERE tenant_id=%s AND child_job_id=%s',(str(tenant),jid)).fetchone()
        if not row:return dict(is_retry=False)
        # Parent may have expired/revoked since the child was created. Never
        # return saved parent terms from the lineage without current rights.
        parent=runtime.service._get(db,str(tenant),str(row['parent_job_id']))
        grant=runtime._authorize_job(tenant,parent)
        runtime.service._observation(db,str(tenant),str(row['parent_observation_id']),grant)
        return dict(is_retry=True,**row['plan'])
