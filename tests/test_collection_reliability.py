from datetime import datetime, timedelta, timezone
from realtime.collection_reliability import summarize

NOW=datetime(2026,11,9,tzinfo=timezone.utc)

def task(**changes):
    return dict(id='id',query='q',aliases=[],sources=['google_web'],status='active',options={'web_pages':2},**changes)

def test_cooling_empty_batch_cannot_complete():
    f=dict(query='q',state='cooling',next_run_at=NOW+timedelta(minutes=30),last_error='http_429')
    r=summarize(task(),[],[f],now=NOW)
    assert not r['scope_complete']
    assert all(x['state']=='waiting_retry' for x in r['queries'])
    assert r['query_page_success_rate'] is None

def test_short_result_frontier_completes_without_fake_page_success():
    f=dict(query='q',state='completed',next_run_at=NOW+timedelta(hours=1))
    r=summarize(task(),[],[f],now=NOW)
    assert r['scope_complete']
    assert r['fresh_attempts']==0

def test_recovery_and_cache_denominators():
    t=task();t['options']['web_pages']=1;t['aliases']=['cached']
    def req(q,classification,delta,reused=False):
        return dict(query=q,source='google_web',page=1,classification=classification,completed_at=NOW+timedelta(seconds=delta),reused=reused)
    r=summarize(t,[req('q','http_429',0),req('q','results',1800),req('cached','results',-200,True)],[],now=NOW)
    assert r['fresh_attempts']==2 and r['successful_attempts']==1
    assert r['attempted_query_pages']==1 and r['query_page_success_rate']==1
    assert r['recovered_query_pages']==1 and r['queries'][0]['recovery_seconds']==1800
    assert r['queries'][1]['state']=='cache_reused'

def test_partial_evidence_never_proves_completion():
    assert not summarize(task(),[],[dict(query='q',state='completed')],truncated=True,now=NOW)['scope_complete']

def test_task_progress_ui_explains_wait_and_recovery_without_html():
    import subprocess
    from realtime.reliability_ui import TASK_PROGRESS_JS
    code=TASK_PROGRESS_JS.split("$('taskRows').addEventListener")[0]
    program="const names={google_web:'Google Web'}; const time=x=>x;\n"+code+"\n"+r'''
    const text=taskProgressText({fresh_attempts:3,successful_attempts:1,recovered_query_pages:1,retry_window_seconds:3600,queries:[{source:'google_web',query:'<q>',page:1,state:'waiting_retry',next_retry_at:'2026-11-09T08:00:00Z'}]});
    if(!text.includes('冷却等待')||!text.includes('最早重试 2026-11-09T08:00:00Z')||!text.includes('1 个查询页失败后恢复')||!text.includes('60 分钟'))throw Error(text);
    '''
    result=subprocess.run(['node','--input-type=module'],input=program,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr

def test_ui_reuses_creation_key_until_success_without_storing_request_text():
    import subprocess
    from realtime.reliability_ui import TASK_IDEMPOTENCY_JS
    program=r'''
    import assert from 'node:assert/strict';
    import {webcrypto} from 'node:crypto';
    const crypto=webcrypto;const values=new Map();
    const sessionStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
    '''+TASK_IDEMPOTENCY_JS+r'''
    const body={query:'private text',sources:['google_web']};
    const first=await creationIdentity(body),retry=await creationIdentity(body);
    assert.equal(first.key,retry.key);assert.equal(values.size,1);
    assert.ok(![...values.keys(),...values.values()].some(x=>x.includes('private text')));
    confirmCreation(first);const next=await creationIdentity(body);assert.notEqual(first.key,next.key);
    const changed=await creationIdentity({...body,query:'changed'});assert.notEqual(next.key,changed.key);
    '''
    result=subprocess.run(['node','--input-type=module'],input=program,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
