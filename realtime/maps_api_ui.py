"""Reuse the Maps workflow inside the API tool; one form and one job state."""

from .ui_icons import icon, source_icon

TASK_CARD = (
    '<a id="mapsTaskSource" class="maps-task-source" href="#maps" aria-describedby="mapsTaskSourceDescription">'
    '<strong translate="no">' + source_icon('google_maps', 32) + 'Google Maps</strong>'
    '<span id="mapsTaskSourceDescription">按关键词或位置查找商家，下载地址、电话与评分。</span>'
    + icon('chevron_right') + '</a>'
)

TASK_CARD_CSS = '''
.source-cards .maps-task-source{position:relative;display:block;background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:18px 36px 18px 18px;color:#171717;text-decoration:none}
.source-cards .maps-task-source:hover{background:#fafafa;border-color:#a3a3a3}
.source-cards .maps-task-source:focus-visible{outline:2px solid currentColor;outline-offset:2px}
.source-cards .maps-task-source strong{display:flex;align-items:center;gap:8px;margin:0 0 4px}
.source-cards .maps-task-source .google-source-icon{flex:none}
.source-cards .maps-task-source .mi{position:absolute;top:22px;right:14px;width:18px;height:18px;color:#646464}
'''

DEVELOPER = '''<details class="panel" id="mapsDeveloper"><summary>将 Maps 数据接入你的产品</summary>
<p class="help">1 创建任务 → 2 轮询状态 → 3 读取结果或下载。以下示例使用上方条件，不含任何真实密钥。</p>
<p class="help">外部调用需配置 SEARCHFLOW_ACCESS_KEY（网站访问凭据）和 SEARCHFLOW_API_KEY（客户 Bearer）。网络中断时沿用原 Idempotency-Key，避免重复任务。状态为 failed 时查看 error_code；429 请降低频率，勿不断新建任务。</p>
<pre id="mapsDeveloperExample" class="product-code"></pre><button type="button" id="mapsCopyExample" class="secondary mini">复制接入示例</button>
<p class="help">每次最多100家，22家以上使用深度采集；实际数量取决于地图当前结果并附覆盖证明。结果保留24小时，缺失字段返回null，不提供完整评论。</p>
<a href="/v1/openapi.json?catalog=data" target="_blank" rel="noopener">参数、字段与错误说明（OpenAPI）</a></details>'''

JS = r'''
const mapsOriginalParent=$('mapsView');
const mapsWorkspaceNodes=[...mapsOriginalParent.children];
$('mapsTaskSource')?.addEventListener('click',event=>{
 if(!quickBusy)return;
 event.preventDefault();
 notice('当前快速请求正在运行，请在请求结束后打开 Google Maps。',true);
});
function mapsMountInAPI(){
 const workspace=$('quickMapsWorkspace');
 if(!workspace)return;
 const active=view==='api'&&quickMode==='maps';
 for(const n of mapsWorkspaceNodes)(active?workspace:mapsOriginalParent).append(n);
 workspace.hidden=!active;
 $('quickForm').hidden=active;
 document.querySelector('#apiView .quick-steps').hidden=active;
 $('quickStatus').hidden=active;
 if(active){$('quickContinue').hidden=true;$('quickCancel').hidden=true;$('quickQueue').hidden=true;$('quickOutput').hidden=true;loadMaps();}
}
function mapsDeveloperExample(){
 const example=$('mapsDeveloperExample');
 if(!example)return;
 try {
  const p=mapsFormPayload();
  const shell=s=>"'"+s.replaceAll("'","'\\''")+"'";
  example.textContent=`BASE=https://searchflow.20-150-145-162.sslip.io
# 配置上文所述的两个环境变量；每个新查询使用新的幂等键
IDEMPOTENCY_KEY="maps-$(date +%s)-$RANDOM"

# 1. 创建任务，保存响应的 id
curl --fail-with-body "$BASE/v1/google/local/jobs" \\
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \\
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY" \\
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \\
  -H 'Content-Type: application/json' \\
  --data ${shell(JSON.stringify(p))}

# 2. 将 JOB_ID 替换为响应 id；约每3秒查询，直到 succeeded 或 failed
JOB_ID='<响应中的id>'
curl --fail-with-body "$BASE/v1/google/local/jobs/$JOB_ID" \\
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \\
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"

# 3. succeeded 后读取完整结果；返回值 result_cache.places 是商家数组
curl --fail-with-body "$BASE/v1/google/local/jobs/$JOB_ID/results" \\
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \\
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"

# 下载 CSV；format=jsonl 可下载完整任务信封
curl --fail-with-body "$BASE/v1/google/local/jobs/$JOB_ID/export?format=csv" \\
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \\
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY" -o maps.csv`;
 }catch(e){example.textContent='填写有效查询条件后生成示例：'+e.message;}
}
const mapsDeveloper=$('mapsDeveloper');
if(mapsDeveloper){
 mapsOriginalParent.append(mapsDeveloper);
 mapsWorkspaceNodes.push(mapsDeveloper);
 $('mapsForm').addEventListener('input',mapsDeveloperExample);
 $('mapsForm').addEventListener('change',mapsDeveloperExample);
 const copy=$('mapsCopyExample'),example=$('mapsDeveloperExample');
 if(copy&&example)copy.onclick=async()=>{try{await navigator.clipboard.writeText(example.textContent);copy.textContent='已复制'}catch(e){copy.textContent='请选中示例复制'}};
}
for(const b of document.querySelectorAll('[data-quick-mode]')){
 const original=b.onclick;b.onclick=()=>{if(quickBusy)return;original();try{sessionStorage.setItem('sf-api-mode',quickMode)}catch(e){}mapsMountInAPI();if(quickMode==='maps')mapsDeveloperExample()};
}
const mapsAPIShowView=showView;showView=function(){mapsAPIShowView();mapsMountInAPI();};
mapsDeveloperExample();
try{if(sessionStorage.getItem('sf-api-mode')==='maps')document.querySelector('[data-quick-mode="maps"]').click()}catch(e){}
'''


def extend(page):
    source_cards = '<div class="source-cards">'
    page = page.replace(source_cards, source_cards + TASK_CARD, 1)
    page = page.replace('</style>', TASK_CARD_CSS + '</style>', 1)
    anchor='<button type="button" data-quick-mode="history"'
    assert anchor in page
    page=page.replace(anchor,'<button type="button" data-quick-mode="maps" aria-pressed="false">Google Maps<span>商家资料、附近搜索与导出</span></button>'+anchor,1)
    page=page.replace('</form>\n<p id="quickStatus"','</form>\n<div id="quickMapsWorkspace" hidden></div>\n<p id="quickStatus"',1)
    page=page.replace('<details class="panel"><summary>开发者接口文档</summary>',DEVELOPER+'<details class="panel"><summary>开发者接口文档</summary>',1)
    page=page.replace('</style>','\n.quick-modes{grid-template-columns:repeat(4,minmax(0,1fr))}#mapsDeveloperExample{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}#quickMapsWorkspace>.panel{padding:0;border:0;box-shadow:none;margin-top:22px}#quickMapsWorkspace>.panel+.panel{border-top:1px solid var(--line);padding-top:20px}@media(max-width:1000px){.quick-modes{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.quick-modes{grid-template-columns:1fr}}\n</style>',1)
    marker="window.addEventListener('hashchange',showView);showView();setInterval(refreshView,10000);"
    assert marker in page
    return page.replace(marker,JS+'\n'+marker,1)
