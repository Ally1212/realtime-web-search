"""Presentation for the workbench data analysis results."""

OVERVIEW = '''
<section id="ti-observedTab" class="panel ti-results" role="tabpanel" aria-label="数据概览">
  <div class="ti-result-heading">
    <div><h2 id="ti-overviewTitle">数据概览</h2><p id="ti-observedScope"></p></div>
    <div class="ti-result-actions"><button type="button" class="secondary" id="ti-openOriginals">查看全部原文</button><button id="ti-quickExport" type="button" class="secondary">导出原文 CSV</button></div>
  </div>
  <dl id="ti-observedStats" class="ti-metrics"></dl>
  <p class="help">统计只计算纳入样本的记录。查看或导出全部原文时，也会保留已排除的记录及其状态。</p>
  <section id="ti-overviewSources" class="ti-sources" aria-label="样本来源">
    <div id="ti-domainGroup"><h3>来源网站</h3><div id="ti-observedDomains"></div></div>
    <div id="ti-queryGroup"><h3>搜索关键词</h3><div id="ti-observedQueries"></div></div>
  </section>
  <section id="ti-overviewRecords" class="ti-preview-records">
    <div class="ti-subheading"><h3 id="ti-recordsHeading">搜索结果预览</h3><span id="ti-previewCount"></span></div>
    <div class="table-wrap ti-record-table" tabindex="0" role="region" aria-labelledby="ti-recordsHeading">
      <table><thead><tr><th scope="col">标题与摘要</th><th scope="col">采集来源与时间</th><th scope="col">搜索关键词</th></tr></thead><tbody id="ti-observedTable"></tbody></table>
    </div>
    <p id="ti-recordsEmpty" hidden>该样本没有结构化标题可供预览。可以查看已保存的原文。</p>
  </section>
  <div id="ti-importNote" class="ti-import-note" hidden><strong>评论样本</strong><p>在「主题报告」查看问题分布，或在「原始数据」逐条查看和复核评论。</p></div>
</section>
'''

RENDER_OVERVIEW = r'''
function renderObserved(){
  const {manifest,report,quality,name}=current;
  const {observed={},n,annotated_records,reviewed_records}=report;
  const saved=manifest.kind==='saved_search';
  const filters=manifest.filters||{};
  const number=new Intl.NumberFormat('zh-CN');
  const date=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short',timeZone:'UTC'});
  const formatDate=value=>value&&Number.isFinite(Date.parse(value))?date.format(new Date(value))+' UTC':null;
  const source=code=>({google_web:'Google Web',google_news:'Google News',google_trends:'Google Trends',google_images:'Google Images'}[code]||code);
  const metric=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${value==null?'未记录':esc(number.format(value))}</dd></div>`;
  const dateStart=formatDate(observed.first_collected),dateEnd=formatDate(observed.last_collected);
  $('overviewTitle').textContent=saved?(filters.query||'搜索数据概览'):name;
  const parts=saved?[source(filters.source)||'全部搜索来源',dateStart&&dateEnd?dateStart+' 至 '+dateEnd:null,manifest.has_more_requests?'按最近请求取样':null]:['导入文件 · 统计范围为本批记录'];
  $('observedScope').textContent=parts.filter(Boolean).join(' · ');
  $('observedStats').innerHTML=(saved?[
    ['纳入统计',n],['已存请求',manifest.request_count],['来源网站',observed.unique_domains],['含摘要记录',observed.with_snippet]
  ]:[
    ['纳入统计',n],['导入记录',quality.input_rows],['已打标签',annotated_records],['已复核',reviewed_records]
  ]).map(([label,value])=>metric(label,value)).join('');
  const groups=[['domainGroup','observedDomains',observed.domains],['queryGroup','observedQueries',observed.queries]];
  for(const [group,id,items] of groups){
    $(group).hidden=!items?.length;
    $(id).innerHTML=(items||[]).slice(0,10).map(item=>`<div class="ti-domain"><span>${esc(item.name)}</span><strong>${esc(number.format(item.count))} 条</strong></div>`).join('');
  }
  $('overviewSources').hidden=!saved||groups.every(([id])=>$(id).hidden);
  $('overviewRecords').hidden=!saved;
  $('importNote').hidden=saved;
  const rows=saved?(observed.records||[]).filter(row=>row.title||row.snippet):[];
  $('recordsEmpty').hidden=rows.length>0;
  $('observedTable').closest('.ti-record-table').hidden=rows.length===0;
  $('previewCount').textContent=rows.length?`展示 ${rows.length} 条 · 样本共 ${number.format(n)} 条`:'';
  $('observedTable').innerHTML=rows.map(row=>{
    const title=esc(row.title||'标题未记录');
    const url=typeof row.url==='string'&&/^https?:\/\//i.test(row.url)?row.url:null;
    const time=formatDate(row.collected_at)||'时间未记录';
    return `<tr><td><strong>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${title}</a>`:title}</strong>${row.snippet?`<p>${esc(row.snippet)}</p>`:''}</td><td><span class="ti-field-label">采集来源与时间</span><b>${esc(source(row.source)||'来源未记录')}</b><small>${esc(time)}</small></td><td><span class="ti-field-label">搜索关键词</span>${esc(row.query||'关键词未记录')}</td></tr>`;
  }).join('');
}
$('openOriginals').onclick=()=>{
  switchTab('evidence');
  root.querySelector('[data-tab="evidence"]').focus();
};

'''

CSS = '''
#insightsView .ti-input-panel h2,#insightsView .ti-results h2,#insightsView #ti-reportTab h2,#insightsView #ti-evidenceTab h2{margin:0;font-size:1.25rem;line-height:1.35}
#insightsView .ti-input-panel h2{margin-block-end:1rem}
#insightsView .ti-input-panel form>.row{display:grid;grid-template-columns:minmax(8rem,1fr) minmax(8rem,2fr) minmax(6rem,.6fr) max-content;align-items:end}
#insightsView .ti-input-panel .ti-note{margin-block:.75rem 0}
#insightsView .ti-input-panel details+details{margin-top:.65rem}
#insightsView #ti-status:empty{display:none}
#insightsView #ti-status.error{border:1px solid #dca6a6;border-radius:8px;padding:.6rem .8rem;margin:0 0 1rem;color:#8f2424}
#insightsView #ti-workspace>.tabs{margin-block:1.25rem .65rem;padding:.3rem;background:#f5f5f5;border-radius:.65rem}
#insightsView #ti-workspace>.tabs button{min-height:2.75rem;background:transparent;border-color:transparent}
#insightsView #ti-workspace>.tabs button[aria-selected=true]{background:#fff;color:#171717;border-color:#c9c9c9;font-weight:650}
#insightsView .ti-results{container-type:inline-size}
#insightsView .ti-result-heading{display:flex;justify-content:space-between;gap:.8rem 1rem;align-items:start;flex-wrap:wrap}
#insightsView .ti-result-heading p{margin:.4rem 0 0;color:#525252;font-size:.8125rem}
#insightsView .ti-result-actions{display:flex;flex-wrap:wrap;gap:.5rem}
#insightsView .ti-result-actions button{min-height:2.75rem}
#insightsView .ti-metrics,#insightsView #ti-reportStats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;border-block:1px solid #dedede;margin:1.2rem 0;padding:0}
#insightsView .ti-metrics>div,#insightsView #ti-reportStats>div{padding:.8rem .9rem;min-width:0;border:0;border-radius:0;box-shadow:none}
#insightsView .ti-metrics>div+div,#insightsView #ti-reportStats>div+div{border-inline-start:1px solid #dedede}
#insightsView .ti-metrics dt{color:#525252;font-size:.8125rem}
#insightsView .ti-metrics dd,#insightsView #ti-reportStats strong{margin:0;color:#171717;font-size:1.5rem;font-weight:650;line-height:1.4;font-variant-numeric:tabular-nums}
#insightsView .ti-sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1.5rem;margin-block:1.25rem}
#insightsView .ti-sources h3,#insightsView .ti-subheading h3{margin:0;font-size:1rem}
#insightsView .ti-domain{gap:1rem;overflow-wrap:anywhere;color:#414141}
#insightsView .ti-domain strong{flex-shrink:0;font-variant-numeric:tabular-nums}
#insightsView .ti-subheading{display:flex;align-items:baseline;justify-content:space-between;gap:.5rem 1rem;flex-wrap:wrap;margin:0 0 .6rem}
#insightsView .ti-subheading span,#insightsView .ti-record-table small{color:#525252;font-size:.75rem}
#insightsView .ti-record-table{border:1px solid #dedede;border-radius:.7rem;overflow:auto;max-height:32rem}
#insightsView .ti-record-table table{width:100%;table-layout:fixed}
#insightsView .ti-record-table thead{position:sticky;top:0;background:#f6f6f6}
#insightsView .ti-record-table th:first-child{width:55%}
#insightsView .ti-record-table th:nth-child(2){width:25%}
#insightsView .ti-record-table th,#insightsView .ti-record-table td{padding:.8rem;vertical-align:top;overflow-wrap:anywhere}
#insightsView .ti-record-table tbody tr:last-child td{border-bottom:0}
#insightsView .ti-record-table td p{margin:.3rem 0 0;font-size:.8125rem;color:#525252}
#insightsView .ti-record-table td small{display:block;margin-top:.25rem}
#insightsView .ti-field-label{display:none}
#insightsView .ti-import-note{padding:.75rem 0;color:#414141}
#insightsView .ti-import-note p{margin:.3rem 0 0}
#insightsView #ti-reportWarning{margin:.4rem 0;color:#525252;font-size:.8125rem}
#insightsView #ti-findings{display:flex;gap:.4rem 1.2rem;flex-wrap:wrap;margin-bottom:1.1rem}
#insightsView #ti-findings p{margin:0;font-size:.875rem}
#insightsView #ti-topicBars .bar{grid-template-columns:minmax(7.2rem,1fr) minmax(0,3fr) minmax(6.8rem,auto);padding:.55rem 0;border-bottom:1px solid #ededed;margin:0;gap:.75rem}
#insightsView #ti-topicBars button{text-decoration:underline;text-underline-offset:3px;min-height:2.75rem;color:#171717;font-weight:600;justify-self:start;text-align:start}
#insightsView #ti-topicBars .track{background:#ededed}
#insightsView #ti-topicBars .fill{background:#282828}
#insightsView #ti-topicBars span{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap;color:#404040;font-size:.8125rem}
#insightsView .ti-report-details{border-top:1px solid #dedede;margin-top:1.25rem;padding:.8rem 0}
#insightsView #ti-evidenceList .record{padding:.85rem 0;max-width:80ch}
#insightsView #ti-evidenceList .record>p{margin:.5rem 0;line-height:1.65}
#insightsView #ti-evidenceList .record-actions button{min-height:2.75rem;margin-top:.3rem}
@media (max-width:720px){
  #insightsView .ti-input-panel form>.row{grid-template-columns:repeat(2,minmax(0,1fr))}
  #insightsView .ti-input-panel form>.row>label:nth-child(2){grid-row:1;grid-column:1/-1}
}
@media (max-width:520px){
  #insightsView .ti-metrics,#insightsView #ti-reportStats{grid-template-columns:repeat(2,minmax(0,1fr))}
  #insightsView .ti-metrics>div:nth-child(odd),#insightsView #ti-reportStats>div:nth-child(odd){border-inline-start:0}
  #insightsView .ti-metrics>div:nth-child(n+3),#insightsView #ti-reportStats>div:nth-child(n+3){border-top:1px solid #dedede}
  #insightsView .ti-sources{grid-template-columns:1fr;gap:1rem}
  #insightsView #ti-topicBars .bar{grid-template-columns:minmax(0,1fr) auto;gap:.1rem .5rem;padding:.35rem 0 .65rem}
  #insightsView #ti-topicBars .track{grid-row:2;grid-column:1/-1}
}
@container (max-width:36rem){
  #insightsView .ti-record-table{max-height:none}
  #insightsView .ti-record-table table,#insightsView .ti-record-table tbody{display:block;min-width:0}
  #insightsView .ti-record-table thead{display:none}
  #insightsView .ti-record-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:.7rem .8rem;gap:.4rem .8rem;border-bottom:1px solid #dedede}
  #insightsView .ti-record-table tr:last-child{border-bottom:0}
  #insightsView .ti-record-table td{display:block;border:0;padding:0;min-width:0}
  #insightsView .ti-record-table td:first-child{grid-column:1/-1}
  #insightsView .ti-field-label{display:block;color:#525252;font-size:.75rem}
}
'''


def extend(page):
    start = page.index('<section id="ti-observedTab"')
    end = page.index('<section id="ti-taxonomyTab"', start)
    page = page[:start] + OVERVIEW + page[end:]
    start = page.index('function renderObserved(){')
    end = page.index("$('quickExport').onclick", start)
    page = page[:start] + RENDER_OVERVIEW + page[end:]
    page = page.replace('<section class="panel" data-screen-label="分析已采集数据"><form id="ti-createForm">', '<section class="panel ti-input-panel" data-screen-label="分析已采集数据"><h2>选择分析数据</h2><form id="ti-createForm">', 1)
    page = page.replace('<h2>有数字，也有原文</h2>', '<h2>主题报告</h2>', 1)
    page = page.replace('点击主题回查全部对应原文；同一记录每个标签最多计一次。', '点主题查看对应原文。每条记录在同一主题只计一次。「未计入」的原文会保留，统计时跳过。', 1)
    page = page.replace('<h2>让每条判断落到原文</h2>', '<h2>原始记录与复核</h2>', 1)
    start = page.index('<div id="ti-topicBars"')
    start = page.index('</div>', start) + len('</div>')
    end = page.index('<details style="margin-top:20px"><summary>统计口径与限制', start)
    page = page[:start] + '<details class="ti-report-details"><summary>情感、品牌与样本来源</summary>' + page[start:end] + '</details>' + page[end:]
    return page.replace('</head>', '<style id="sfInsightsResultsStyles">' + CSS + '</style></head>', 1)
