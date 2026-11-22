"""Seed pre-migration private jobs to test retained history and retirement.

Never imported by production. No collector, queue or remote API is executed.
"""
from uuid import uuid4
from psycopg.types.json import Jsonb


def legacy_private_job(store, account, payload, idem, config):
    original = dict(payload, provider='openserp')
    admitted = {**payload, 'provider': 'middle'}
    if admitted.get('source') == 'google_images':
        admitted['source'] = 'google_web'
    jid, _ = store.submit(account, admitted, idem, config)
    cid = str(uuid4())
    with store.base.connect() as db:
        db.execute("INSERT INTO campaigns(id,query,aliases,daily_target,proxy_profile,sources,status) VALUES(%s,%s,'[]',1000000,%s,%s,'active')", (cid, payload['query'], config.default_proxy_profile, Jsonb([payload.get('source', 'google_web')])))
        db.execute("INSERT INTO serp_task_options(campaign_id,options,started,mode,max_successful_searches) VALUES(%s,%s,true,'once',1)", (cid, Jsonb({'customer_private': True})))
        saved = db.execute('SELECT parameters FROM sf_jobs WHERE id=%s', (jid,)).fetchone()['parameters']
        db.execute('UPDATE sf_jobs SET campaign_id=%s,parameters=%s WHERE id=%s', (cid, Jsonb({**saved, **original}), jid))
    return jid, cid
