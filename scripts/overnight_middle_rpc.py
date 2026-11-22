"""Credential-local RPC for the authorized overnight archive; stdin JSON only."""
import json, re, sys
from pathlib import Path
from datetime import datetime

def main(p):
    source=p['source']; action=p['action']
    from realtime.config import Config
    from realtime.middle_ingest import collector_endpoint, receipt_query, processing_receipts, retry_delay
    from realtime.google_whale_readback import WhaleReadbackClient, ReadbackTarget
    from realtime.google_data import digest
    if action=='scan':
        from realtime.campaign_store import CampaignStore
        from realtime.serp_fields import result_items
        c=Config()
        store=CampaignStore(c.database_url,initialize=False)
        with store.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout='30s'")
            rows=db.execute("""
                SELECT r.* FROM serp_requests r
                WHERE r.source=%s AND r.classification IN('results','empty')
                  AND r.completed_at>=%s AND r.completed_at<%s
                  AND (r.completed_at,r.request_id)>(%s::timestamptz,%s::uuid)
                  AND r.request_id<>ALL(%s::uuid[])
                  AND (EXISTS(SELECT 1 FROM serp_supply_campaigns x
                       WHERE x.campaign_id=r.campaign_id AND x.run_id=%s)
                       OR r.campaign_id=ANY(%s::uuid[]))
                ORDER BY r.completed_at,r.request_id LIMIT 50
                """,(source,p['start'],p['deadline'],p['cursor'][0],p['cursor'][1],
                     p.get('exclude',[]),p.get('supply_run','google-serp-20261010'),p['ids'])).fetchall()
            credit={}
            if rows and p.get('supply_run'):
                credit={row['first_request_id']:row['new_urls'] for row in db.execute("""
                    SELECT first_request_id,count(*) AS new_urls FROM serp_urls
                    WHERE first_request_id=ANY(%s::uuid[]) AND NOT historical AND canonical_url_hash IS NULL
                    GROUP BY first_request_id
                    """,([r['request_id'] for r in rows],)).fetchall()}
        return dict(rows=[dict(row=r,count=len(result_items(r['response'])),
                               new_urls=credit.get(r['request_id'],0)) for r in rows],
                    dataset=c.whale_dataset_id,platform=c.whale_source_platform)
    if source=='google_maps':
        from realtime.maps_provider import config
        c=config();base=c['middle_base_url']
        collector_key=Path(c.get('collector_key_file','/app/state/private/maps-middle-collector-key')).read_text().strip()
        consumer_key=Path(c.get('consumer_key_file','/app/state/private/maps-middle-consumer-key')).read_text().strip()
        platform='google_maps';dataset='google_maps_raw'
    else:
        c=Config();base=collector_endpoint(c);collector_key=c.whale_collector_api_key
        consumer_key=Path('/app/state/private/middle-consumer-key').read_text().strip()
        platform=c.whale_source_platform;dataset=c.whale_dataset_id
    messages=p['messages']
    assert all(m['dataset_id']==dataset and m['source']['source_platform']==platform for m in messages)
    import requests
    session=requests.Session()

    def http_error(response):
        detail={}
        if response.status_code==400:
            try: detail={k:v for k,v in response.json().items() if k in ('error','message','details')}
            except (ValueError,AttributeError):pass
        error=RuntimeError('middle_http_'+str(response.status_code)+' '+json.dumps(detail)[:1200])
        error.status_code=response.status_code
        if response.status_code==429:
            error.retry_after=retry_delay(0,response.headers.get('Retry-After'))
            metadata={'retry_after_seconds':round(error.retry_after,2)}
            try:
                value=response.json()
                code=value.get('code') or value.get('error_code') or value.get('error')
                if isinstance(code,dict):
                    code=code.get('code')
                if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',code):
                    metadata['code']=code
            except (ValueError,AttributeError):
                pass
            error.args=('middle_http_429 '+json.dumps(metadata),)
        return error

    if action in ('upload','poll'):
        headers = {'Authorization': 'Bearer '+collector_key, 'Accept': 'application/json'}
        if action=='upload':
            path='/v1/documents/bulk'
            body=messages
            headers['X-Ingest-Class'] = 'backfill'
        else:
            path='/v1/ingest/receipts'
            rows=[dict(payload=m,dataset_id=m['dataset_id'],record_key=m['source']['source_record_key'],receipt=r) for m,r in zip(messages,p.get('receipts',[{}]*len(messages)),strict=True)]
            body=[receipt_query(r) for r in rows]
        try:r=session.post(base+path,json=body,headers=headers,timeout=30,allow_redirects=False)
        except requests.RequestException:raise RuntimeError('middle_transport_unknown') from None
        if r.status_code not in (200,201,202):
            raise http_error(r)
        data=r.json()
        if action=='poll':return dict(receipts=processing_receipts(rows,data))
        if not isinstance(data,list) or len(data)!=len(messages):raise ValueError('bulk_count_mismatch')
        return dict(submitted=len(data))
    if action!='verify':raise ValueError('unknown_action')

    def defer_throttled_readback(response, *args, **kwargs):
        # The coordinator stores Retry-After across subprocesses and restarts.
        if response.status_code==429:
            raise http_error(response)

    session.hooks['response'].append(defer_throttled_readback)
    reader=WhaleReadbackClient(base,consumer_key,session=session,min_interval_seconds=.25)
    receipts=p['receipts']
    targets=[]
    for m,r in zip(messages,receipts,strict=True):
        if r['status'] not in ('accepted','duplicate'):raise ValueError('nonterminal_receipt')
        targets.append(ReadbackTarget(r['document_id'],r['version_id'],dataset,platform,expected_platform_payload_hash=digest(m['content']['platform_payload'])))
    docs=reader._request('POST','/v1/documents/batch-get',body={'document_ids':list(dict.fromkeys(t.document_id for t in targets)),'include_content':False})['documents']
    documents={d['document_id']:d.get('document') for d in docs if not d.get('error')}
    versions=reader._request('POST','/v1/versions/batch-get',body={'items':[dict(document_id=t.document_id,version_id=t.version_id) for t in targets],'include_body':False})['versions']
    versions={(v.get('document_id'),v.get('version_id')):v for v in versions}
    hydrate=[];missing=[]
    for i,(t,m) in enumerate(zip(targets,messages,strict=True)):
        doc=documents.get(t.document_id);reader._check_document(t,doc)
        if doc.get('external_content_id')!=m['content']['external_content_id']:raise ValueError('external_identity_mismatch')
        v=versions[(t.document_id,t.version_id)]
        if not isinstance(v.get('platform_payload'),dict):
            if str(v.get('version',{}).get('raw_payload_hash','')).removeprefix('sha256:')!=t.expected_platform_payload_hash:raise ValueError('fixed_version_hash_mismatch')
            snapshots=reader._request('GET','/v1/documents/'+t.document_id+'/snapshots?limit=500')['snapshots']
            matches=[s for s in snapshots if s.get('version_id')==t.version_id and s.get('document_id')==t.document_id and s.get('dataset_id')==dataset and s.get('source_platform')==platform]
            if len(matches)!=1:raise ValueError('snapshot_identity_mismatch')
            s=matches[0]
            if isinstance(s.get('platform_payload'),dict):v['platform_payload']=s['platform_payload']
            else:
                hydrate.append(dict(document_id=t.document_id,snapshot_id=s['id'],snapshot_at=s['snapshot_at']));missing.append(i)
    if hydrate:
        result=reader._request('POST','/v1/snapshots/payloads/batch-get',body={'items':hydrate})['snapshots']
        if len(result)!=len(hydrate):raise ValueError('incomplete_hydration')
        for i,want,got in zip(missing,hydrate,result,strict=True):
            if got.get('error') or any(got.get(k)!=want[k] for k in ('document_id','snapshot_id')):raise ValueError('hydration_identity_mismatch')
            t=targets[i];versions[(t.document_id,t.version_id)]['platform_payload']=got.get('platform_payload')
    def semantic(value):
        if type(value) is float and value.is_integer():return int(value)
        if isinstance(value,list):return [semantic(v) for v in value]
        if isinstance(value,dict):return {k:semantic(v) for k,v in value.items()}
        return value
    proofs=[]
    for t,m in zip(targets,messages,strict=True):
        v=versions[(t.document_id,t.version_id)]
        actual_hash=digest(v['platform_payload'])
        normalized=actual_hash!=t.expected_platform_payload_hash
        if normalized and digest(semantic(v['platform_payload']))!=digest(semantic(m['content']['platform_payload'])):
            raise ValueError('payload_mismatch:'+str(m['content']['platform_payload']['observation_id']))
        from dataclasses import replace
        checked=replace(t,expected_raw_payload_hash=m['source']['payload_hash'],expected_platform_payload_hash=actual_hash)
        reader._proof(checked,documents[t.document_id],v)
        proofs.append(dict(document_id=t.document_id,version_id=t.version_id,sha256=t.expected_platform_payload_hash,readback_sha256=actual_hash,numeric_encoding_normalized=normalized))
    return dict(proofs=proofs)

if __name__=='__main__':
    try: print(json.dumps(main(json.load(sys.stdin)),default=str))
    except Exception as e:
        # No server response bodies, auth headers, URLs with credentials or tracebacks.
        print(json.dumps(dict(retry_after=getattr(e,'retry_after',0),http_status=getattr(e,'status_code',None),error=(getattr(e,'code')+': '+str(e) if getattr(e,'code',None) else None) or (str(e) if isinstance(e,(RuntimeError,ValueError,KeyError)) else type(e).__name__))))
        sys.exit(1)
