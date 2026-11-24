/* Presentation and navigation only. Existing data contracts own all requests. */
(()=>{
'use strict';
const removed=['capability','whale','settings'];
for(const id of removed){const el=$(id+'View');el.replaceChildren();el.inert=true;el.setAttribute('aria-hidden','true');}
for(const link of document.querySelectorAll('a[href="#capability"],a[href="#whale"],a[href="#settings"]'))link.remove();
const upload=$('taskWhaleUpload');if(upload){upload.checked=false;upload.disabled=true;const label=upload.closest('label');const note=label.nextElementSibling;if(note?.classList.contains('help'))note.remove();label.hidden=true;}
// Retain inert ids used by original routing; remove all interactive feature UI.
loadWhale=async()=>{};
for(const id of removed)document.querySelector(`[data-view="${id}"]`)?.remove();
loadSettings=async function(){
 if(settingsLoaded)return;
 const s=await api('/api/collector-settings');
 for(const id of ['webRegion','trendsGeo'])$(id).replaceChildren(...s.regions.map(x=>new Option(regionNames[x]||x,x)));
 $('newsLocale').replaceChildren(...s.news_editions.map(x=>new Option(x,x)));
 $('webRegion').value=s.web_region;$('webLanguage').value=s.web_language;$('trendsGeo').value=s.trends_geos[0];$('newsLocale').value=s.news_locales[0];settingsLoaded=true;updateTaskEstimate();return s;
};
loadTaskChoices=async function(){const d=await api('/api/serp/tasks?kind=manual&limit=100'),el=$('resultCampaign'),selected=el.value;el.replaceChildren(new Option('全部任务',''),...d.tasks.map(x=>new Option(x.query+' · '+(states[x.status]||x.status),x.id)));selectTaskFilter('resultCampaign',selected);};
// Hidden legacy stats no longer trigger an extra database report on each refresh.
loadOverview=loadArchiveOverview;
document.querySelector('.workbench-health')?.remove();
$('sourceStats')?.closest('details')?.remove();
document.querySelector('.archive-note')?.remove();
const developer=document.createElement('section');
developer.id='developerView';
developer.hidden=true;
developer.innerHTML=`
  <div class="sf-data-reference">
    <p class="sf-data-lead">从第一个接口开始，复制命令到终端，即可读取一条已有记录。</p>
    <div class="sf-data-key" id="sfDataKey" hidden>
      <p id="sfDataAuth" role="status"></p>
      <button id="sfRetryDemoKey" class="secondary mini" type="button" hidden>重试读取 Key</button>
    </div>
    <nav class="sf-data-toc" aria-label="数据 API 目录"></nav>
    <div id="sfApiStatus" role="status" class="sf-doc-status"></div>
    <div id="sfApiCatalog" role="region" aria-label="接口文档"></div>
    <section class="sf-data-rules" aria-labelledby="sfDataRules">
      <h2 id="sfDataRules">调用规则</h2>
      <p>命令中出现 <code>REQUEST_ID</code>、<code>TASK_ID</code>、<code>SUBSCRIPTION_ID</code> 或 <code>BATCH_ID</code> 时，替换为上一步返回的编号。创建接口的同一笔请求重试时沿用原 <code>Idempotency-Key</code>。</p>
      <p><code>401</code> 检查 Key，<code>404</code> 检查编号和空间，<code>409</code> 检查任务是否就绪或幂等键是否冲突，<code>429</code> 按 <code>Retry-After</code> 等待。</p>
      <div class="sf-data-download"><a id="sfMarkdownGuide" href="/v1/docs" download="searchflow-api-guide.md">下载 Markdown 指南</a><a href="/v1/openapi.json?catalog=data" download="searchflow-openapi.json">下载 OpenAPI</a></div>
    </section>
  </div>`;
$('apiView').after(developer);
let docsLoading=false;
let docsLoaded=false;
let demoKey='';
let keyLoading=false;
let updateCommands=[];
const docsSkeleton='<div class="sf-doc-skeleton" aria-hidden="true"><i></i><i></i><i></i><i></i></div>';

async function readDeveloperJson(path,timeout){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const response=await fetch(path,{signal:controller.signal,cache:'no-store'});
    if(!response.ok)throw Error('HTTP '+response.status);
    return await response.json();
  }finally{
    clearTimeout(timer);
  }
}

async function loadDemoKey(){
  if(keyLoading)return;
  keyLoading=true;
  const status=$('sfDataAuth'), retry=$('sfRetryDemoKey');
  retry.hidden=true;
  status.textContent='正在读取演示 Key，可以先浏览接口。';
  try{
    const {key}=await readDeveloperJson('/api/site/data-demo-key',5000);
    if(typeof key!=='string'||!/^sf_access_[A-Za-z0-9_-]+$/.test(key))throw Error('No demo key');
    demoKey=key;
    for(const update of updateCommands) update(key);
    status.textContent='示例命令已填入当前登录 Key。';
  }catch{
    status.textContent='演示 Key 暂不可用。命令可复制，请先替换其中的 Key。';
    retry.hidden=false;
  }finally{
    keyLoading=false;
    if(docsLoaded)$('sfDataKey').hidden=false;
  }
}
$('sfRetryDemoKey').onclick=loadDemoKey;
function apiFieldRows(schema, prefix='', level=0){
  if(level>2)return [];
  return Object.entries(schema?.properties||{}).flatMap(([name,value])=>{
    const path=prefix+name;
    return [{name:path,schema:value,required:(schema.required||[]).includes(name)},
            ...apiFieldRows(value,path+'.',level+1)];
  });
}
function apiFieldTable(rows){
  if(!rows.length)return '';
  return '<div class="table-scroll sf-data-table"><table><thead><tr><th scope="col">字段</th><th scope="col">类型与取值</th><th scope="col">说明</th></tr></thead><tbody>'+
    rows.map(({name,schema,required})=>'<tr><td><code>'+esc(name)+'</code><span>'+ (required?'必填':'可选')+'</span></td><td>'+
      esc([schema.type,(schema.enum||[]).join(' · '),schema.default===undefined?'':'默认 '+JSON.stringify(schema.default)].filter(Boolean).join(' / '))+
      '</td><td>'+esc(schema.description||'')+'</td></tr>').join('')+'</tbody></table></div>';
}
function resolveApiSchema(documentation,schema){
  if(!schema?.$ref)return schema||{};
  return documentation.components?.schemas?.[schema.$ref.split('/').at(-1)]||{};
}
function sample(documentation,schema,depth=0){const s=resolveApiSchema(documentation,schema);if(depth>3)return null;if(s.example!==undefined)return s.example;if(s.default!==undefined)return s.default;if(s.enum)return s.enum[0];if(s.type==='object'||s.properties)return Object.fromEntries(Object.entries(s.properties||{}).filter(([k])=>(s.required||[]).includes(k)||['source','region','language','mode','city','radius_m','location'].includes(k)).map(([k,v])=>[k,sample(documentation,v,depth+1)]));if(s.type==='array')return [sample(documentation,s.items,depth+1)];if(s.type==='integer'||s.type==='number')return s.minimum??1;if(s.type==='boolean')return false;return '<'+(s.format||'value')+'>';}
function apiExample(documentation,path,method,op,key,siteGuide){
  const requestBody=op.requestBody?.content?.['application/json'];
  const query=new URLSearchParams((op.parameters||[]).filter(p=>p.in==='query'&&(p.required||(!siteGuide&&p.schema?.default!==undefined)))
    .map(p=>[p.name,p.schema?.enum?.[0]??p.schema?.default??p.name.toUpperCase()]));
  Object.entries(op['x-searchflow-example-query']||{}).forEach(([name,value])=>query.set(name,value));
  if(path.endsWith('/export')&&!query.has('format'))query.set('format','jsonl');
  const id=path.startsWith('/v1/data/requests/')?'REQUEST_ID':path.startsWith('/v1/data/subscriptions/')?'SUBSCRIPTION_ID':'TASK_ID';
  const url=location.origin+path.replace('{id}',id).replace('{batch_id}','BATCH_ID')+(query.size?'?'+query:'');
  const lines=[`curl -fsS${method==='get'?'':' -X '+method.toUpperCase()} '${url}'`,
    '  -H "Authorization: Bearer '+(key||'$SEARCHFLOW_API_KEY')+'"'];
  if(!siteGuide)lines.splice(1,0,'  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY"');
  if((!siteGuide&&method==='post')||op.parameters?.some(p=>p.in==='header'&&p.name.toLowerCase()==='idempotency-key')){
    lines.push("  -H 'Idempotency-Key: REPLACE_WITH_REQUEST_KEY'");
  }
  if(requestBody){
    const body=JSON.stringify(requestBody.example??sample(documentation,requestBody.schema)).replaceAll("'","'\\''");
    lines.push("  -H 'Content-Type: application/json'", "  -d '"+body+"'");
  }
  if(path.endsWith('/export'))lines.push("  -o results."+query.get('format'));
  return lines.join(' '+String.fromCharCode(92)+'\n');
}
function renderDataApi(documentation,groups,siteGuide){
  const root=$('sfApiCatalog');
  const nav=developer.querySelector('.sf-data-toc');
  root.replaceChildren();
  nav.replaceChildren();
  updateCommands=[];
  for(const group of groups){
    const section=document.createElement('section');
    section.className='sf-data-topic';
    section.tabIndex=-1;
    section.innerHTML='<h2>'+esc(group.title)+'</h2>'+(group.description?'<p>'+esc(group.description)+'</p>':'');
    const link=document.createElement('button');
    link.type='button';
    link.className='secondary mini';
    link.textContent=group.title;
    link.onclick=()=>{section.focus({preventScroll:true});section.scrollIntoView({block:'start'});};
    nav.append(link);
    for(const [path,operations] of Object.entries(documentation.paths)){
      for(const [method,op] of Object.entries(operations)){
        if(!['get','post','patch','put','delete'].includes(method)|| (siteGuide&&op['x-searchflow-group']!==group.id))continue;
        const article=document.createElement('article');
        const body=op.requestBody?.content?.['application/json']?.schema;
        const params=(op.parameters||[]).map(p=>({name:p.name,schema:resolveApiSchema(documentation,p.schema),required:p.required}));
        article.className='sf-data-operation';
        article.innerHTML='<h3>'+esc(op.summary||path)+'</h3><div class="sf-data-path"><b>'+esc(method.toUpperCase())+'</b><code>'+esc(path)+'</code></div>'+
          '<p>'+esc(op['x-searchflow-result']||op.description||'')+'</p>'+
          '<div class="sf-data-example"><div class="sf-data-code-head"><strong>curl 示例</strong><button type="button" class="secondary mini">复制命令</button></div><pre><code></code></pre><span role="status"></span></div>'+
          (params.length?'<h4>请求参数</h4>'+apiFieldTable(params):'')+
          (body?'<h4>JSON 请求体</h4>'+(apiFieldTable(apiFieldRows(resolveApiSchema(documentation,body)))||'<p>发送空对象 <code>{}</code>。</p>'):'');
        const command=article.querySelector('pre code');
        const feedback=article.querySelector('[role=status]');
        const button=article.querySelector('button');
        button.setAttribute('aria-label','复制命令：'+(op.summary||path));
        const update=key=>{command.textContent=apiExample(documentation,path,method,op,key,siteGuide);};
        update(demoKey);
        updateCommands.push(update);
        button.onclick=async()=>{
          try {
            await navigator.clipboard.writeText(command.textContent);
            feedback.textContent='已复制。';
          } catch {
            const range=document.createRange();
            range.selectNodeContents(command);
            const selection=window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            feedback.textContent='命令已选中，可使用系统快捷键复制。';
          }
        };
        section.append(article);
      }
    }
    root.append(section);
  }
}
async function loadDeveloper(){
  if(docsLoaded||docsLoading)return;
  docsLoading=true;
  const root=$('sfApiCatalog'), status=$('sfApiStatus');
  status.textContent='正在加载接口文档…';
  root.innerHTML=docsSkeleton;
  root.setAttribute('aria-busy','true');
  // The gateway's optional demonstration credential never delays document rendering.
  try{
    const documentation=await readDeveloperJson('/v1/openapi.json?catalog=data',10000);
    if(!documentation.paths||!Object.keys(documentation.paths).length)throw Error('No API paths');
    const groups=documentation['x-searchflow-groups'];
    const siteGuide=Array.isArray(groups)&&groups.length>0;
    renderDataApi(documentation,siteGuide?groups:[{title:'数据接口',description:''}],siteGuide);
    $('sfMarkdownGuide').hidden=!siteGuide;
    docsLoaded=true;
    status.textContent='';
    if(siteGuide){
      $('sfDataKey').hidden=false;
      loadDemoKey();
    }
  }catch{
    status.textContent='接口文档暂时无法加载。';
    root.innerHTML='<button type="button" class="secondary">重新加载文档</button>';
    root.querySelector('button').onclick=loadDeveloper;
  }finally{
    docsLoading=false;
    root.setAttribute('aria-busy','false');
  }
}
// Maps is a request mode, never a separate screen.
mapsDeveloperExample=()=>{};mapsWorkspaceNodes.splice(mapsWorkspaceNodes.indexOf($('mapsDeveloper')),1);$('mapsDeveloper')?.remove();
for(const d of [...$('apiView').children])if(d.tagName==='DETAILS'&&d.querySelector('summary')?.textContent==='开发者接口文档')d.remove();
$('apiView').querySelector('.product-panel-head h2').textContent='快速使用';
const previousShow=showView;
showView=function(){
 const id=location.hash.slice(1);document.body.dataset.screen=id||'overview';if(removed.includes(id)){location.replace('#overview');return;}
 if(id==='maps'){const b=document.querySelector('[data-quick-mode="maps"]');if(!quickBusy)b.click();location.replace('#api');return;}
 for(const modal of document.querySelectorAll('dialog[open]'))modal.close();
 developer.hidden=id!=='developer';
 if(id==='developer'){view='developer';for(const el of document.querySelectorAll('main section[id$="View"]'))if(el!==developer)el.hidden=true;for(const a of document.querySelectorAll('aside [data-view]'))a.removeAttribute('aria-current');document.querySelector('[data-view="developer"]')?.setAttribute('aria-current','page');$('pageTitle').textContent='API 工具';$('pageSubtitle').textContent='面向开发者的接口、参数和调用示例';notice('');loadDeveloper();return;}
 previousShow();if(view==='api'){$('pageTitle').textContent='快速使用';$('pageSubtitle').textContent='选择来源、填写条件，获取并下载数据';}
};
// Refresh once on entry; background polling only for task progress. Preserve data while refreshing.
const viewRead=new Map();
refreshView=async function(){
 const current=view;if(removed.includes(current)||current==='developer'||current==='insights'||refreshing.has(current))return;
 if(current==='tasks'&&$('taskRows').querySelector(':focus, .task-stop[open]'))return;
 const ttl=current==='tasks'?10000:60000;if(Date.now()-(viewRead.get(current)||0)<ttl)return;
 refreshing.add(current);const section=$(current+'View'),first=!viewRead.has(current);if(first&&current!=='overview')section?.classList.add('sf-view-loading');section?.setAttribute('aria-busy','true');
 try{const jobs=[];if(current==='create')jobs.push(loadSettings());if(productViews.includes(current))jobs.push(loadProductView());if(current==='overview')jobs.push(loadLatest(),loadArchiveOverview());if(current==='tasks')jobs.push(loadTasks());if(current==='results')jobs.push(loadResults(),loadTaskChoices());await Promise.all(jobs);viewRead.set(current,Date.now());$('updated').textContent='最近读取 '+new Date().toLocaleTimeString('zh-CN');}
 catch(e){notice(e.message,true);$('resultRows')?.setAttribute('aria-busy','false');if(first&&current==='overview')$('latestRows').innerHTML='<div class="report-error">数据读取失败，请刷新重试。</div>';}
 finally{refreshing.delete(current);section?.classList.remove('sf-view-loading');section?.setAttribute('aria-busy','false');}
};
// One task drawer; data stays outside table rows and refreshes with the selected task.
const drawer=document.createElement('dialog');drawer.id='taskDetailsDrawer';drawer.className='sf-task-drawer';drawer.setAttribute('aria-labelledby','sfTaskDrawerTitle');drawer.innerHTML='<header><div><small>采集任务</small><h2 id="sfTaskDrawerTitle">任务详情</h2></div><button type="button" class="secondary mini" aria-label="关闭任务详情">关闭 ×</button></header><div id="sfTaskDrawerBody"></div>';
document.body.append(drawer);drawer.querySelector('button').onclick=()=>drawer.close();drawer.addEventListener('click',e=>{if(e.target===drawer){const r=drawer.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right)drawer.close();}});
const details=new Map();let selectedTask=null,drawerTrigger=null;
function moveTaskDetails(){for(const row of $('taskRows').querySelectorAll('[data-cid]')){const nodes=[...row.querySelectorAll('td:first-child details')];if(!nodes.length)continue;const content=document.createElement('div'),title=row.querySelector('.task-query')?.textContent||'任务详情';for(const node of nodes){const section=document.createElement('section');const h=document.createElement('h3');h.textContent=node.querySelector('summary').textContent;section.append(h,...[...node.children].filter(n=>n.tagName!=='SUMMARY'));
 const help=section.querySelector('.help');if(help){const copy=help.cloneNode(true);for(const br of copy.querySelectorAll('br'))br.replaceWith('\n');const splitOptions=line=>{let depth=0,start=0,parts=[];for(let i=0;i<line.length;i++){if('（('.includes(line[i]))depth++;else if('）)'.includes(line[i]))depth=Math.max(0,depth-1);if(!depth&&line.slice(i,i+3)===' / '){parts.push(line.slice(start,i));start=i+3;i+=2;}}parts.push(line.slice(start));return parts;};const lines=copy.textContent.split('\n').flatMap((line,i)=>i===0?splitOptions(line):[line]);const dl=document.createElement('dl');dl.className='sf-task-facts';for(const line of lines){const match=line.match(/^(编号|创建|更新|最近请求|模式|异常)\s+(.*)$/)||line.match(/^([^:：]+)[:：]\s*(.*)$/);const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=match?match[1]:'说明';dd.textContent=match?match[2]:line;dl.append(dt,dd);}help.replaceWith(dl);}content.append(section);node.remove();}details.set(row.dataset.cid,{title,content});const button=document.createElement('button');button.type='button';button.className='mini secondary sf-task-detail-button';button.dataset.taskDetail=row.dataset.cid;button.textContent='任务详情 ↗';row.querySelector('td').append(button);if(drawer.open&&selectedTask===row.dataset.cid){$('sfTaskDrawerTitle').textContent=title;$('sfTaskDrawerBody').replaceChildren(content.cloneNode(true));}}}
new MutationObserver(moveTaskDetails).observe($('taskRows'),{childList:true});
$('taskRows').addEventListener('click',e=>{const b=e.target.closest('[data-task-detail]');if(!b)return;selectedTask=b.dataset.taskDetail;drawerTrigger=b;const item=details.get(selectedTask);$('sfTaskDrawerTitle').textContent=item.title;$('sfTaskDrawerBody').replaceChildren(item.content.cloneNode(true));drawer.showModal();});drawer.addEventListener('close',()=>{if(drawerTrigger?.isConnected)drawerTrigger.focus()});
// Small actions keep their labels centered; large sections use skeletons.
const userActions=new WeakSet();
const trackAction=button=>{if(!button||button.disabled)return;userActions.add(button);queueMicrotask(()=>{if(!button.disabled)userActions.delete(button);});};
document.addEventListener('click',e=>trackAction(e.target.closest('button')),true);
document.addEventListener('submit',e=>trackAction(e.submitter||e.target.querySelector('button[type=submit]')),true);
const buttonState=button=>{const loading=button.disabled&&(userActions.has(button)||/正在|读取中|加载中|导出中|获取中|提交中|处理中/.test(button.textContent));if(!button.disabled)userActions.delete(button);button.classList.toggle('sf-button-loading',loading);if(loading)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');};
const buttons=new MutationObserver(records=>{const found=new Set();for(const r of records){const el=r.target.nodeType===1?r.target:r.target.parentElement;const b=el?.closest('button');if(b)found.add(b);}for(const b of found)buttonState(b);});buttons.observe($('mainContent'),{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled']});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const modal=document.querySelector('dialog[open]');if(modal){e.preventDefault();modal.close();}}},true);
window.sfWorkspaceRefinement={version:'simplify-1'};
})();
