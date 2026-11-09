"""Bounded requests and recoverable states, without changing collection policy."""

CSS = r'''
.read-status{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:36px;margin:8px 0 12px;font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.read-status[data-state=error]{color:var(--red)}
.read-status[data-state=loading]::before{content:'';width:10px;height:10px;flex-shrink:0;border:2px solid #d3e3fd;border-top-color:var(--blue);border-radius:50%;animation:spin .9s linear infinite}
.read-status button{flex-shrink:0}
.archive-summary[data-stale=true]{opacity:.72}
@media(prefers-reduced-motion:reduce){.read-status[data-state=loading]::before{animation:none}}
'''

RESULT_SINGLE_FLIGHT_JS = r'''
// Automatic refresh and manual search may overlap. Reuse the current read only
// while the exact filters/page remain current; changing A -> B -> A starts A anew.
let pendingResultRead=null,pendingResultKey=null;
const concurrentSafeResults=loadResults;
loadResults=function(...args){
 const key=resultMode+'|'+resultOffset+'|'+resultParams().toString();
 if(pendingResultRead&&pendingResultKey===key)return pendingResultRead;
 pendingResultKey=key;
 const read=concurrentSafeResults(...args);pendingResultRead=read;
 read.finally(()=>{if(pendingResultRead===read){pendingResultRead=null;pendingResultKey=null;}}).catch(()=>{});
 return read;
};
'''

TASK_PROGRESS_JS = r'''
function taskProgressText(p){
 const labels={succeeded:'已成功',cache_reused:'复用已保存结果',waiting_retry:'冷却等待',pending_retry:'等待重试',pending:'尚未执行',inflight:'请求处理中',paused:'已暂停',ended_without_success:'已结束，未成功'};
 const lines=[`真实请求 ${p.fresh_attempts} 次，成功 ${p.successful_attempts} 次；${p.recovered_query_pages} 个查询页失败后恢复。`];
 if(p.retry_window_seconds)lines.push(`本次重试窗口最多 ${Math.round(p.retry_window_seconds/60)} 分钟，从首次执行开始。`);
 if(p.stop_reason==='retry_window_exhausted')lines.push('重试窗口已结束，仍有查询未成功；可手动重新启动。');
 for(const r of p.queries.slice(0,20))lines.push(`${names[r.source]||r.source} · ${r.query} · ${labels[r.state]||r.state}${r.source==='google_web'?' · 第 '+r.page+' 页':''}${r.next_retry_at?' · 最早重试 '+time(r.next_retry_at):''}`);
 if(p.queries.length>20)lines.push('其余查询可通过任务进度接口查看。');
 if(p.truncated)lines.push('历史记录过多，当前统计已截断，不能用于判定全部完成。');
 return lines.join('\n');
}
$('taskRows').addEventListener('toggle',async e=>{
 const detail=e.target;
 if(detail.tagName!=='DETAILS'||!detail.open||detail.querySelector('summary')?.textContent!=='任务详情')return;
 const cid=detail.closest('[data-cid]')?.dataset.cid;if(!cid)return;
 let text=detail.querySelector('[data-task-progress]');if(text)return;
 text=document.createElement('p');text.className='help';text.dataset.taskProgress='';text.style.whiteSpace='pre-line';text.setAttribute('role','status');text.textContent='正在读取采集进度…';detail.append(text);
 try{const p=await api('/api/serp/tasks/'+encodeURIComponent(cid)+'/progress');if(text.isConnected)text.textContent=taskProgressText(p);}
 catch(error){if(text.isConnected){text.textContent='进度读取失败：'+error.message+' ';const retry=document.createElement('button');retry.className='mini secondary';retry.textContent='重试读取';retry.onclick=()=>{text.remove();detail.open=false;detail.open=true;};text.append(retry);}}
},true);
'''

TASK_IDEMPOTENCY_JS = r'''
async function creationIdentity(body){
 const bytes=new TextEncoder().encode(JSON.stringify(body));
 const digest=await crypto.subtle.digest('SHA-256',bytes);
 const fingerprint=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
 const storageKey='searchflow-pending-create:'+fingerprint;
 let key=sessionStorage.getItem(storageKey);
 if(!key){key=crypto.randomUUID();sessionStorage.setItem(storageKey,key);}
 return {key,storageKey};
}
function confirmCreation(identity){if(identity&&sessionStorage.getItem(identity.storageKey)===identity.key)sessionStorage.removeItem(identity.storageKey);}
'''

JS = r'''
// A timed-out write may already have committed. Never automatically replay it.
api=async function(url,body){
 const controller=new AbortController(),writing=body!==undefined;
 const creation=writing&&url==='/api/serp/tasks'?await creationIdentity(body):null;
 const timer=setTimeout(()=>controller.abort(),url.includes('/analytics/overview')?30000:20000);
 try{
  const response=await fetch(url,{signal:controller.signal,...(writing?{method:'POST',headers:{'Content-Type':'application/json',...(operatorKey?{'Authorization':'Bearer '+operatorKey}:{}),...(creation?{'Idempotency-Key':creation.key}:{})},body:JSON.stringify(body)}:{})});
  if(response.status===401)throw Error('访问已过期或缺少操作权限，请重新验证访问 Key 或连接管理密钥。');
  if(response.redirected&&new URL(response.url,location.href).pathname==='/_access/login')throw Error('访问已过期，请刷新页面重新验证访问 Key。');
  let data;
  try{data=await response.json();}catch{if(controller.signal.aborted)throw Error(writing?'提交超时，操作可能已保存；请先刷新核对，不要重复提交。':'读取超时，已保存的数据不受影响，请重试。');throw Error('服务返回了非预期响应，请稍后重试。');}
  if(!response.ok)throw Error(data.error||'请求失败 '+response.status);
  confirmCreation(creation);
  return data;
 }catch(e){
  if(controller.signal.aborted)throw Error(writing?'提交超时，操作可能已保存；请先刷新核对，不要重复提交。':'读取超时，已保存的数据不受影响，请重试。');
  if(e instanceof TypeError)throw Error(writing?'网络中断，操作结果尚未确认；请先刷新核对，不要重复提交。':'网络连接失败，已保存的数据不受影响，请重试。');
  throw e;
 }finally{clearTimeout(timer);}
};

function readStatus(id,state,text,retry){
 const root=$(id);root.dataset.state=state;root.replaceChildren();
 const message=document.createElement('span');message.textContent=text;root.append(message);
 if(retry){const button=document.createElement('button');button.type='button';button.className='mini secondary';button.textContent='重新读取';button.onclick=()=>retry().catch(e=>notice(e.message,true));root.append(button);}
}
function clearReadNotice(message){if(message&&$('notice').querySelector('.notice-text')?.textContent===message)notice('');}
let reliableTasksSeq=0,reliableResultsSeq=0,tasksShown=false,resultsShown=false,lastTaskReadError='',lastResultReadError='';
const reliableLoadTasks=loadTasks,reliableLoadResults=loadResults;
loadTasks=async function(...args){
 const expandedTaskIds=new Set([...document.querySelectorAll('#taskRows tr[data-cid]')].filter(row=>[...row.querySelectorAll('details[open]')].some(d=>d.querySelector('summary')?.textContent==='任务详情')).map(row=>row.dataset.cid));
 const seq=++reliableTasksSeq;readStatus('taskReadStatus','loading','正在读取任务状态…');$('taskRows').setAttribute('aria-busy','true');
 try{await reliableLoadTasks(...args);if(seq!==reliableTasksSeq)return;for(const row of document.querySelectorAll('#taskRows tr[data-cid]'))if(expandedTaskIds.has(row.dataset.cid)){const detail=[...row.querySelectorAll('details')].find(d=>d.querySelector('summary')?.textContent==='任务详情');if(detail)detail.open=true;}tasksShown=true;clearReadNotice(lastTaskReadError);lastTaskReadError='';readStatus('taskReadStatus','ready','任务状态读取于 '+new Date().toLocaleTimeString('zh-CN')+'；搜索异常可在任务详情中查看。');}
 catch(e){if(seq!==reliableTasksSeq)return;lastTaskReadError=e.message;readStatus('taskReadStatus','error',e.message+(tasksShown?' 当前保留上次成功读取的列表，不代表最新条件。':''),()=>loadTasks());if(!tasksShown)$('taskRows').innerHTML='<tr><td colspan="6" class="empty">暂时无法读取任务，请重试；这不表示没有任务。</td></tr>';throw e;}
 finally{if(seq===reliableTasksSeq)$('taskRows').setAttribute('aria-busy','false');}
};
loadResults=async function(...args){
 const seq=++reliableResultsSeq;readStatus('resultReadStatus','loading','正在读取当前筛选的数据…');
 try{await reliableLoadResults(...args);if(seq!==reliableResultsSeq)return;resultsShown=true;clearReadNotice(lastResultReadError);lastResultReadError='';readStatus('resultReadStatus','ready','数据读取于 '+new Date().toLocaleTimeString('zh-CN')+'；导出包含全部符合已应用筛选条件的结果，不限当前页。');}
 catch(e){if(seq!==reliableResultsSeq)return;lastResultReadError=e.message;readStatus('resultReadStatus','error',e.message+(resultsShown?' 当前保留上次成功读取的数据，不代表最新条件。':''),()=>loadResults());if(!resultsShown)$('resultRows').innerHTML='<div class="panel empty">暂时无法读取数据，请重试；这不表示没有结果。</div>';throw e;}
 finally{if(seq===reliableResultsSeq)$('resultRows').setAttribute('aria-busy','false');}
};
$('overviewView').addEventListener('click',e=>{if(e.target.closest('[data-archive-retry]'))loadArchiveOverview(true);});

// Export the last applied filters, not unsubmitted edits in the filter form.
let appliedResultFilters=null;
const reliableResultParams=resultParams;
const reliableReadResults=loadResults;
loadResults=async function(...args){
 const filters=reliableResultParams().toString(),seq=resultSeq+1;
 await reliableReadResults(...args);
 if(seq===resultSeq&&$('resultReadStatus').dataset.state==='ready')appliedResultFilters=filters;
};
downloadExport=async function(format){
 if(exportController)return;
 if(appliedResultFilters===null){notice('请先成功读取数据，再导出当前筛选结果。',true);return;}
 if(reliableResultParams().toString()!==appliedResultFilters||$('resultReadStatus').dataset.state!=='ready'){
  notice('筛选条件尚未成功应用，请先点击“应用”或重新读取，再导出。',true);return;
 }
 const p=new URLSearchParams(appliedResultFilters);p.set('mode','results');p.set('format',format);if(format==='csv')p.set('view','user');
 const controller=new AbortController();exportController=controller;
 $('exportCsv').disabled=$('exportJson').disabled=true;$('cancelExport').hidden=false;
 try{
  notice('正在准备导出全部筛选数据，数据较多时需要等待。你可以取消导出。');
  const response=await fetch('/api/serp/export?'+p,{signal:controller.signal});
  if(response.status===401||(response.redirected&&new URL(response.url,location.href).pathname==='/_access/login'))throw Error('访问已过期，请刷新页面重新验证访问 Key，再导出。');
  if(!response.ok){let message='导出失败 '+response.status;try{message=(await response.json()).error||message;}catch{}throw Error(message);}
  const expected=format==='csv'?'text/csv':'application/x-ndjson';
  if(!(response.headers.get('content-type')||'').toLowerCase().includes(expected))throw Error('导出返回了非预期文件，未保存；请重新验证访问状态后重试。');
  saveBlob(await response.blob(),`google-results.${format}`);notice('导出完成：全部符合筛选的数据');
 }catch(e){notice(e.name==='AbortError'?'导出已取消，已采集的数据保留。':e.message,e.name!=='AbortError');}
 finally{exportController=null;$('exportCsv').disabled=$('exportJson').disabled=false;$('cancelExport').hidden=true;}
};
''' + RESULT_SINGLE_FLIGHT_JS + TASK_PROGRESS_JS + TASK_IDEMPOTENCY_JS


def extend(page):
    page = page.replace('Object.entries(x.options||{}).map', "Object.entries(x.options||{}).filter(([key])=>!key.startsWith('_')).map")
    page = page.replace("trends_geo:'Trends 地区'", "trends_geo:'Trends 地区',retry_window_seconds:'重试窗口（秒）'")
    page = page.replace('</style>', CSS + '</style>', 1)
    page = page.replace('<p class="archive-note">',
                        '<div id="archiveReadStatus" class="read-status" role="status" aria-live="polite"></div><p class="archive-note">', 1)
    page = page.replace('<div id="resultRows"',
                        '<div id="resultReadStatus" class="read-status" role="status" aria-live="polite"></div><div id="resultRows"', 1)
    page = page.replace('<tbody id="taskRows">',
                        '<tbody id="taskRows" aria-busy="false">', 1)
    page = page.replace('<form class="filters" id="taskFilters"',
                        '<div id="taskReadStatus" class="read-status" role="status" aria-live="polite"></div><form class="filters" id="taskFilters"', 1)
    marker = "\nwindow.addEventListener('hashchange',showView);showView();setInterval(refreshView,10000);"
    return page.replace(marker, '\n' + JS + marker, 1)
