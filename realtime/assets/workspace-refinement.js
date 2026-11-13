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
const developer=document.createElement('section');developer.id='developerView';developer.hidden=true;
developer.innerHTML=`<div class="panel sf-dev-intro"><h2>接入 SearchFlow 数据 API</h2><p>从你的应用创建任务、轮询结果，再下载结构化数据。</p><code class="sf-api-base"></code><div class="sf-dev-auth"><strong>请求凭据</strong><p><code>X-SearchFlow-Access-Key</code>：站点访问凭据</p><p><code>Authorization: Bearer &lt;API_KEY&gt;</code>：客户 API 密钥</p><p>创建任务时使用 <code>Idempotency-Key</code>；相同参数重试沿用原值。请在服务端保存凭据。</p></div><a href="/v1/openapi.json?catalog=data" target="_blank" rel="noopener">下载完整 OpenAPI ↗</a></div><div id="sfApiCatalog" class="sf-api-workspace"><div class="loading">正在读取接口文档…</div></div>`;
$('apiView').after(developer);developer.querySelector('.sf-api-base').textContent=location.origin;
let schemaDoc=null,docsLoading=false;
const resolveSchema=s=>s?.$ref?schemaDoc.components?.schemas?.[s.$ref.split('/').at(-1)]||s:s||{};
function schemaRows(schema,prefix='',depth=0){schema=resolveSchema(schema);if(depth>3)return [];return Object.entries(schema.properties||{}).flatMap(([name,value])=>{const s=resolveSchema(value),key=prefix+name;return [{name:key,required:(schema.required||[]).includes(name),schema:s},...(s.type==='object'?schemaRows(s,key+'.',depth+1):[])];});}
function schemaType(s){return (s.type|| (s.properties?'object':'—'))+(s.nullable?' / null':'')+(s.enum?' · '+s.enum.join(' / '):'');}
function parameterTable(rows){return rows.length?'<div class="table-scroll"><table><thead><tr><th>参数</th><th>类型 / 取值</th><th>说明</th></tr></thead><tbody>'+rows.map(r=>'<tr><td><code>'+esc(r.name)+'</code><small class="sf-api-required">'+(r.required?'必填':'可选')+'</small></td><td>'+esc(schemaType(r.schema))+'</td><td>'+esc(r.schema.description||'')+(r.schema.default!==undefined?'<div class="help">默认 '+esc(JSON.stringify(r.schema.default))+'</div>':'')+'</td></tr>').join('')+'</tbody></table></div>':'<p class="help">无额外参数。</p>';}
function sample(schema,depth=0){const s=resolveSchema(schema);if(depth>3)return null;if(s.example!==undefined)return s.example;if(s.default!==undefined)return s.default;if(s.enum)return s.enum[0];if(s.type==='object'||s.properties)return Object.fromEntries(Object.entries(s.properties||{}).filter(([k])=>(s.required||[]).includes(k)||['source','region','language','mode','city','radius_m','location'].includes(k)).map(([k,v])=>[k,sample(v,depth+1)]));if(s.type==='array')return [sample(s.items,depth+1)];if(s.type==='integer'||s.type==='number')return s.minimum??1;if(s.type==='boolean')return false;return '<'+(s.format||'value')+'>';}
function showEndpoint(index){const {path,method,op}=schemaDoc.entries[index],root=$('sfApiDetail'),body=op.requestBody?.content?.['application/json']?.schema;
 const parameters=(op.parameters||[]).map(p=>({name:p.name+' ('+p.in+')',required:p.required,schema:resolveSchema(p.schema)}));
 root.innerHTML='<h2>'+esc(op.summary||path)+'</h2><div class="sf-api-path"><b>'+esc(method.toUpperCase())+'</b><code>'+esc(path)+'</code></div>'+(op.description?'<p class="help">'+esc(op.description)+'</p>':'')+'<h3>请求参数</h3>'+parameterTable(parameters)+(body?'<h3>JSON 请求体</h3>'+parameterTable(schemaRows(body)):'')+'<div class="section-head"><h3>调用示例</h3><button type="button" class="secondary mini" id="sfCopyApi">复制</button></div><pre class="product-code" id="sfApiExample"></pre><h3>响应状态</h3>'+parameterTable(Object.entries(op.responses||{}).map(([code,r])=>({name:code,required:false,schema:{type:r.description||'响应'}})))+'<details><summary>完整响应结构</summary><pre class="product-code" id="sfApiResponses"></pre></details>';
 const query=new URLSearchParams((op.parameters||[]).filter(p=>p.in==='query'&&(p.required||p.schema?.default!==undefined)).map(p=>[p.name,String(sample(p.schema))]));
 const examplePath=path.replace(/\{([^}]+)\}/g,(_,name)=>'YOUR_'+name.toUpperCase())+(query.size?'?'+query:'');
 let example=`curl --fail-with-body -X ${method.toUpperCase()} '${location.origin}${examplePath}' \\\n  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \\\n  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"`;
 if(method==='post')example+=' \\\n  -H "Idempotency-Key: YOUR_UNIQUE_REQUEST_KEY"';
 if(body)example+=' \\\n  -H "Content-Type: application/json" \\\n  --data '+"'"+JSON.stringify(sample(body),null,2).replaceAll("'","'\\''")+"'";
 $('sfApiExample').textContent=example;$('sfApiResponses').textContent=JSON.stringify(op.responses,null,2);$('sfCopyApi').onclick=async e=>{const button=e.currentTarget;try{await navigator.clipboard.writeText(example);button.textContent='已复制'}catch{notice('请选中示例文本复制',true)}};
 for(const b of developer.querySelectorAll('[data-api-endpoint]'))b.setAttribute('aria-current',Number(b.dataset.apiEndpoint)===index?'true':'false');
}
async function loadDeveloper(){if(schemaDoc||docsLoading)return;docsLoading=true;try{const r=await fetch('/v1/openapi.json?catalog=data');if(!r.ok)throw Error('接口文档暂时无法读取');schemaDoc=await r.json();schemaDoc.entries=Object.entries(schemaDoc.paths).flatMap(([path,ops])=>Object.entries(ops).filter(([method])=>['get','post','patch','put','delete'].includes(method)).map(([method,op])=>({path,method,op})));$('sfApiCatalog').innerHTML='<nav class="sf-api-endpoints" aria-label="选择数据接口">'+schemaDoc.entries.map((x,i)=>'<button type="button" data-api-endpoint="'+i+'"><b>'+esc(x.method.toUpperCase())+'</b><span>'+esc(x.path)+'</span></button>').join('')+'</nav><article id="sfApiDetail" class="panel"></article>';$('sfApiCatalog').onclick=e=>{const b=e.target.closest('[data-api-endpoint]');if(b)showEndpoint(Number(b.dataset.apiEndpoint))};showEndpoint(0);}catch(e){schemaDoc=null;$('sfApiCatalog').innerHTML='<div class="report-error"><p>'+esc(e.message)+'</p><button type="button" id="sfRetryDocs">重新加载</button></div>';$('sfRetryDocs').onclick=loadDeveloper;}finally{docsLoading=false;}}
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
function moveTaskDetails(){for(const row of $('taskRows').querySelectorAll('[data-cid]')){const nodes=[...row.querySelectorAll('details')];if(!nodes.length)continue;const content=document.createElement('div'),title=row.querySelector('.task-query')?.textContent||'任务详情';for(const node of nodes){const section=document.createElement('section');const h=document.createElement('h3');h.textContent=node.querySelector('summary').textContent;section.append(h,...[...node.children].filter(n=>n.tagName!=='SUMMARY'));
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
