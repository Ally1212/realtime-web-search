"""Text analysis inside the existing workbench shell and session."""
import re

CSS = '''
#insightsView .row{display:flex;gap:12px;align-items:end;flex-wrap:wrap}
#insightsView label{display:flex;flex-direction:column;gap:6px;min-width:0}
#insightsView .form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
#insightsView .check{display:flex;flex-direction:row;align-items:flex-start;gap:9px}
#insightsView .check input{width:auto;flex-shrink:0;margin-top:5px}
#insightsView .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;margin:18px 0}
#insightsView .stat strong{display:block;font-size:25px;color:var(--ink)}
#insightsView .stat small{color:var(--muted)}
#insightsView .tabs{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}
#insightsView .tabs [aria-selected=true]{background:var(--blue);color:white;border-color:var(--blue)}
#insightsView .table-wrap{overflow:auto;max-width:100%}
#insightsView .tag-table{min-width:820px}
#insightsView .tag-table input,#insightsView .tag-table textarea,#insightsView .tag-table select{width:100%}
#insightsView table{width:100%;border-collapse:collapse}
#insightsView td,#insightsView th{padding:11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
#insightsView .proof{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
#insightsView .record{padding:18px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
#insightsView .record-meta{color:var(--muted);font-size:12px}
#insightsView .original{white-space:pre-wrap;padding:16px;background:var(--bg);border-radius:8px}
#insightsView .review-label{padding:14px 0;border-bottom:1px solid var(--line)}
#insightsView .review-label textarea{width:100%}
#insightsView .bar{display:grid;grid-template-columns:minmax(100px,1fr) 2fr auto;gap:12px;align-items:center;margin:10px 0}
#insightsView .bar button{background:transparent;color:var(--blue);border:0;white-space:normal;text-align:left;padding:0}
#insightsView .track{height:7px;background:var(--bg);border-radius:4px;overflow:hidden}
#insightsView .fill{height:100%;background:var(--blue)}
#insightsView .split{display:grid;grid-template-columns:1fr 1fr;gap:24px}
#insightsView .runs{margin-top:14px}#insightsView .runs>.row{margin:8px 0;align-items:center}
#insightsView .status{margin:0 0 14px;color:var(--muted)}#insightsView .status.error{color:var(--red)}
#insightsView details>summary{cursor:pointer;font-weight:600}#insightsView details[open]>summary{margin-bottom:16px}
#insightsView .ti-note{margin:12px 0 0;color:var(--muted);font-size:12px}
#insightsView .ti-saved{margin:10px 0 0}#insightsView #ti-workspace>section:first-child{display:none}#insightsView [hidden]{display:none!important}
#insightsView .ti-domain{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:9px 0}
#insightsView #ti-observedTable td{overflow-wrap:anywhere;max-width:420px}
@media(max-width:760px){#insightsView .form{grid-template-columns:1fr}#insightsView .stats{grid-template-columns:1fr 1fr}#insightsView .split{grid-template-columns:1fr}#insightsView .row>label{flex:1 1 140px}#insightsView .bar{grid-template-columns:1fr 1fr auto}#insightsView .row>button{min-height:44px}}
'''


def components():
    from .insights_ui import PAGE
    content=PAGE[PAGE.index('<div id="status"'):PAGE.index('<footer>')]
    content='<span id="connection" hidden></span>'+content
    a=content.index('<section class="panel" data-screen-label="分析数据集">');b=content.index('<div id="workspace"',a)
    content=content[:a]+'''<section class="panel" data-screen-label="分析已采集数据"><form id="createForm"><div class="row"><label style="flex:1">数据来源<select id="source"><option value="google_web">Google Web</option><option value="google_news">Google News</option><option value="google_trends">Google Trends</option><option value="google_images">Google Images</option><option value="">全部来源</option></select></label><label style="flex:2">关键词<input id="query" maxlength="300" placeholder="留空查看最近采集的数据"></label><label>请求数上限<input type="number" id="requestLimit" min="1" max="100" value="20"></label><button type="submit" id="createButton">读取已采集数据</button></div><p class="ti-note">直接读取本系统已保存的真实记录，保留采集时间、原始链接和来源；不触发新采集。</p><details style="margin-top:16px"><summary>更多筛选与文件导入</summary><div class="form"><label>数据范围<select id="scope"><option value="public">公开历史</option><option value="own">本人任务</option><option value="accessible">本人及公开历史</option></select></label><label>开始日期（UTC）<input id="from" type="date"></label><label>结束日期（UTC，含当日）<input id="to" type="date"></label><label>数据入口<select id="inputType"><option value="saved">系统已采集数据</option><option value="file">导入评论或文本</option></select></label><label>分析类型<select id="analysisMode"><option value="search">搜索内容</option><option value="voc">评论 VOC</option></select></label><label>分析名称（可选）<input id="datasetName" maxlength="120"></label><label>每个母帖最多计入<input id="threadCap" type="number" min="1" max="500" value="50"></label></div><fieldset id="fileFields" disabled hidden style="border:0;padding:16px 0"><label>JSON / JSONL / CSV<input id="importFile" type="file" accept=".json,.jsonl,.csv"></label><p class="help">文件需要 text_raw 原文；可含 source_id、source、url、brand、rating、market。最多5000条、800KB。</p><pre id="filePreview"></pre></fieldset><fieldset id="savedFields" hidden disabled></fieldset></details><input type="checkbox" id="external" hidden><span id="createPanel" hidden></span><button id="cancelCreate" type="button" hidden></button></form><details class="ti-saved"><summary>已保存的分析</summary><div class="row"><label style="flex:1">选择分析<select id="datasetSelect"><option value="">请选择分析</option></select></label><button id="refresh" type="button" class="secondary">刷新</button><button id="newDataset" type="button" class="secondary">新建分析</button></div></details></section>'''+content[b:]
    content=re.sub(r'<div class="step">.*?</div>','',content)
    content=content.replace('标签与执行</button>','高级主题分析</button>').replace('分析报告</button>','主题报告</button>').replace('原文与复核</button>','原始数据</button>')
    content=content.replace('<div class="tabs" role="tablist" aria-label="分析步骤">','<div class="tabs" role="tablist" aria-label="数据分析"><button type="button" data-tab="observed" class="secondary" role="tab">数据概览</button>')
    marker='<section id="taxonomyTab"'
    observed='''<section id="observedTab" class="panel" role="tabpanel"><div class="section-head"><h2 id="overviewTitle">已采集数据概览</h2><button id="quickExport" class="secondary" type="button">导出原始数据</button></div><div id="observedStats" class="stats"></div><p id="observedScope" class="help"></p><div class="split"><div><h3>来源网站</h3><div id="observedDomains"></div></div><div><h3>搜索关键词</h3><div id="observedQueries"></div></div><h3 style="margin-top:24px">最近采集的原始记录</h3><div class="table-wrap"><table><thead><tr><th>标题与摘要</th><th>来源 / 采集完成时间</th><th>关键词</th></tr></thead><tbody id="observedTable"></tbody></table></div><p class="help">以上统计来自当前固定样本。查看全部记录请切换“原始数据”。</p></section>'''
    content=content.replace(marker,observed+marker,1)
    content=content.replace('先确认规则，再开始打标','按主题分析（可选）').replace('保存并确认标签','确认标签并分析')
    content=content.replace('<div class="row" style="margin-bottom:14px"><button id="aiDraft"','<label class="check" style="margin:15px 0"><input type="checkbox" id="allowAI"><span>允许 AI 读取当前样本并辅助分类；可能产生模型调用费用。</span></label><div class="row" style="margin-bottom:14px"><button id="aiDraft"')
    # Keep full taxonomy editing and cost controls available, folded by default.
    content=content.replace('<div class="table-wrap"><table class="tag-table">','<details id="taxonomyEditor"><summary>查看并校准标签</summary><div class="table-wrap"><table class="tag-table">',1)
    content=content.replace('<hr style="border:0;','</details><hr style="border:0;',1)
    content=content.replace('客户连接','数据访问权限').replace('客户 API 密钥','现有客户密钥').replace('沿用工作台客户空间。密钥只保留在当前浏览器会话；AI 服务密钥仅保存在服务器。','沿用 API 工具中已连接的空间，无需另建账号。')
    content=content.replace('<div id="qualityStats" class="stats">','<div id="qualityStats" class="stats" hidden>')
    content=re.sub(r'id="([^"]+)"',lambda m:'id="ti-'+m[1]+'"',content)
    js=PAGE.split('<script>',1)[1].split('</script>',1)[0]
    js=js.replace("document.getElementById(id)","document.getElementById('ti-'+id)")
    js=js.replace('document.querySelectorAll(', 'root.querySelectorAll(').replace('document.querySelector(', 'root.querySelector(')
    js=js.replace("sessionStorage.setItem('searchflowKey',key);","sessionStorage.setItem('searchflowKey',key);if(typeof productKey!=='undefined')productKey=key;")
    js=js.replace("sessionStorage.removeItem('searchflowKey');","sessionStorage.removeItem('searchflowKey');if(typeof productKey!=='undefined')productKey='';")
    js=js.replace("$('newDataset').onclick=()=>{$('createPanel').hidden=false;$('datasetName').focus()}","$('newDataset').onclick=()=>{$('datasetName').value='';$('query').focus();status('选择来源和关键词，再读取已采集数据')}")
    js=js.replace("$('customerKey').value='';status('已连接；仅展示当前客户可访问的分析数据集')","$('customerKey').value='';if(datasets.length){await selectDataset(datasets[0].id);switchTab('observed')}status('已连接当前工作台数据权限')")
    js=js.replace("$('connectPanel').open=false;","$('connectPanel').open=false;$('connectPanel').hidden=true;")
    js=js.replace("$('connectPanel').open=!key;","$('connectPanel').open=!key;$('connectPanel').hidden=false;")
    js=js.replace("current=d;offset=0;reviewing=null;", "current=d;offset=0;reviewing=null;if(d.manifest.kind==='saved_search'){const f=d.manifest.filters||{};$('query').value=f.query||'';$('source').value=f.source||'';$('scope').value=f.scope||'public';$('requestLimit').value=f.request_limit||20;}")
    js=js.replace("tab='taxonomy'","tab='observed'")
    js=js.replace("['taxonomy','report','evidence']","['observed','taxonomy','report','evidence']")
    js=js.replace("switchTab('taxonomy');status('数据集已保存，请校准标签。未发起新采集。')","switchTab('observed');status('已读取并记录真实采集数据')")
    js=js.replace("switchTab('taxonomy');", "switchTab('observed');")
    js=js.replace("name:$('datasetName').value,", "name:analysisName(),")
    js=js.replace("delete intents.dataset;","delete intents.dataset;autoAnalysisName=null;autoAnalysisSelection='';")
    js=js.replace("current=d;render();status('标签已确认，可开始分析')", "current=d;render();await submitRun('tag',$('runMethod').value);switchTab('report');status('标签已确认，正在分析')")
    js=js.replace("if(name==='evidence'&&current)action(evidence)","if(name==='evidence'&&current)action(evidence)")
    js=js.replace("renderTags();renderReport();", "renderTags();renderReport();renderObserved();$('allowAI').checked=!!d.external;")
    js=js.replace("$('analysisMode').disabled=saved;", "$('analysisMode').disabled=saved;$('external').checked=false;")
    # AI consent is captured when creating a snapshot; existing immutable datasets never gain consent implicitly.
    js=js.replace("$('aiDraft').onclick=e=>action(()=>submitRun('draft','ai'),e.target);", "$('aiDraft').onclick=e=>action(async()=>{if(!current.external)throw Error('此样本未授权 AI。请勾选允许 AI 后，重新读取数据生成独立样本。');await submitRun('draft','ai');$('taxonomyEditor').open=true;},e.target);")
    js=js.replace("$('aiDraft').disabled=!model?.configured||!d.external", "$('aiDraft').disabled=!model?.configured")
    js=js.replace("$('aiDraft').disabled=active||!model?.configured||!current.external", "$('aiDraft').disabled=active||!model?.configured")
    js=js.replace("if(!current||busy||document.hidden||", "if(root.hidden||!current||busy||document.hidden||")
    # Do not initialize a second application or perform reads from unrelated screens.
    js=js[:js.index("reset();const query=")]
    extra=EXTRA_JS
    return '<section id="insightsView" hidden data-screen-label="数据分析">'+content+'</section>', '(function(){const root=document.getElementById("insightsView");'+js+extra+'})();'


EXTRA_JS = r'''
let autoAnalysisName=null,autoAnalysisSelection='';
function analysisName(){const explicit=$('datasetName').value.trim();if(explicit)return explicit;const file=$('importFile').files[0];const selection=JSON.stringify(['source','query','scope','from','to','requestLimit','inputType','analysisMode','threadCap'].map(x=>$(x).value).concat([$('external').checked,file?.name,file?.size,file?.lastModified]));if(selection!==autoAnalysisSelection||!autoAnalysisName){autoAnalysisSelection=selection;autoAnalysisName=(($('source').selectedOptions[0]?.textContent||'全部来源')+' · '+($('query').value.trim()||'最近采集')+' · '+new Date().toLocaleString('zh-CN')).slice(0,120)}return autoAnalysisName}
function renderObserved(){const o=current.report.observed||{},d=current;const f=d.manifest.filters||{};$('overviewTitle').textContent=d.manifest.kind==='saved_search'?(({google_web:'Google Web',google_news:'Google News',google_trends:'Google Trends',google_images:'Google Images'}[f.source]||'全部来源')+' · '+(f.query||'已采集数据')):d.name;const known=o.available!==false;$('observedStats').innerHTML=stat(d.report.n,'去重后记录')+stat(o.unique_domains??'—','来源网站')+stat(d.manifest.request_count??'—','已存请求')+stat(o.with_snippet??'—','含摘要记录');$('observedScope').textContent=(d.manifest.kind==='saved_search'?'来自系统已采集数据。':'来自导入文件，真实性需自行核验。')+(d.manifest.has_more_requests?'按最新请求取样，并非全量。':'')+(o.first_collected?' 采集完成时间范围：'+o.first_collected+' 至 '+o.last_collected:' 采集时间缺失时保留未知。');const list=(items)=>items?.length?items.slice(0,10).map(x=>'<div class="ti-domain"><span>'+esc(x.name)+'</span><strong>'+x.count+'</strong></div>').join(''):'<p class="muted">暂无可用字段</p>';$('observedDomains').innerHTML=list(o.domains);$('observedQueries').innerHTML=list(o.queries);$('observedTable').innerHTML=(o.records||[]).map(x=>'<tr><td>'+(x.url?'<a href="'+esc(x.url)+'" target="_blank" rel="noopener noreferrer">'+esc(x.title||'标题未知')+'</a>':esc(x.title||'标题未知'))+'<div class="help">'+esc(x.snippet||'摘要未知')+'</div></td><td>'+esc(x.source)+'<div class="help">'+esc(x.collected_at||'采集完成时间未知')+'</div></td><td>'+esc(x.query||'关键词未知')+'</td></tr>').join('')||'<tr><td colspan="3">旧分析快照未记录结构化标题和采集字段；可按相同条件重新读取真实数据。</td></tr>'}
$('quickExport').onclick=()=>$('exports').querySelector('[data-export="csv"]').click();
$('allowAI').onchange=()=>{$('external').checked=$('allowAI').checked;if(!current?.external&&$('allowAI').checked)status('已允许新样本使用 AI；请点击“读取已采集数据”，保存独立样本后使用。')};
let entering=false;
async function enter(){if(entering)return;const shared=typeof productKey==='string'?productKey:(sessionStorage.getItem('searchflowKey')||'');if(shared!==key){key=shared;reset()}if(!key){$('connectPanel').open=true;status('请使用工作台已有客户密钥连接数据权限。');return}entering=true;try{await loadList();if(!current&&datasets.length){await selectDataset(datasets[0].id);switchTab('observed')}}catch(e){status(e.message,true)}finally{entering=false}}
const originalShowView=showView;
showView=function(){if(location.hash==='#insights'){view='insights';for(const node of document.querySelectorAll('main section[id$="View"]'))node.hidden=true;root.hidden=false;for(const nav of document.querySelectorAll('aside [data-view]'))nav.setAttribute('aria-current',nav.dataset.view==='insights'?'page':'false');document.getElementById('pageTitle').textContent='数据分析';document.getElementById('pageSubtitle').textContent='分析系统已采集的真实数据，随时回查原始记录';action(enter)}else{root.hidden=true;originalShowView()}};
$('observedTab').append($('workspace').querySelector('section details'));
reset();
'''.replace('\n+', '\n')


def extend(page):
    if 'id="insightsView"' in page:
        return page
    section,js=components()
    # Upgrade the old link in an already generated production page as well.
    old=r'<a href="/insights">(.*?)</a>'
    if re.search(old,page,re.S):
        page=re.sub(old,lambda m:'<a href="#insights" data-view="insights">'+m[1].replace('文本洞察','数据分析')+'</a>',page,count=1,flags=re.S)
    else:
        from .ui_icons import icon
        page=page.replace('<a href="#analysis" data-view="analysis">','<a href="#insights" data-view="insights"><span class="nav-icon" aria-hidden="true">'+icon('analysis')+'</span>数据分析</a><a href="#analysis" data-view="analysis">',1)
    marker="window.addEventListener('hashchange',showView);showView();"
    if marker not in page:
        raise ValueError('Workbench routing hook missing')
    page=page.replace('</style>',CSS+'</style>',1)
    page=page.replace('<footer class="foot" id="updated">',section+'<footer class="foot" id="updated">',1)
    if 'id="insightsView"' not in page:
        raise ValueError('Workbench section hook missing')
    return page.replace(marker,js+'\n'+marker,1)
