"""Collector workbench. Every operation is backed by a persistent HTTP API."""
PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SearchFlow · 搜索数据工作台</title><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 32 32%22%3E%3Crect width=%2232%22 height=%2232%22 rx=%228%22 fill=%22%23315fdb%22/%3E%3Cpath d=%22M11 22l5-5m7-5a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z%22 fill=%22none%22 stroke=%22white%22 stroke-width=%222%22/%3E%3C/svg%3E">
<style>
:root{color-scheme:light;--ink:#18243b;--muted:#66738b;--line:#e1e7f0;--blue:#315fdb;--bg:#f5f7fb;--red:#ba3b50;--green:#18765b;--green-bg:#e7f5ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 "Avenir Next","PingFang SC","Microsoft YaHei",sans-serif}
button,input,select,textarea{font:inherit}
a{color:var(--blue);overflow-wrap:anywhere}
button,.button-link{cursor:pointer;border:1px solid var(--blue);background:var(--blue);color:white;border-radius:9px;padding:10px 18px;font-weight:600;white-space:nowrap;text-decoration:none;display:inline-block}
button:hover,.button-link:hover{filter:brightness(.94)}
button.secondary,.button-link.secondary{background:white;color:var(--ink);border-color:var(--line)}
button.danger{background:#fff;color:var(--red);border-color:#e7b9c2}
button:disabled{cursor:default;opacity:.45}
button,a,input,select,textarea,summary{outline-offset:4px}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #729bf5}
input:not([type=checkbox]):not([type=radio]),select,textarea{width:100%;border:1px solid #cdd6e4;border-radius:8px;padding:10px 12px;background:#fff;color:var(--ink)}
textarea{resize:vertical;min-height:140px}
input[type=checkbox],input[type=radio]{accent-color:var(--blue);width:17px;height:17px;vertical-align:middle;margin-right:8px}
label{display:block;font-weight:600;margin-bottom:7px}
fieldset{border:0;margin:22px 0;padding:0}
legend{font-weight:650;margin-bottom:10px}
h1{font-size:28px;letter-spacing:-.8px;margin:0 0 5px}
h2{font-size:20px;margin:0 0 16px}
h3{font-size:16px;margin:0 0 10px}
p{max-width:80ch;margin:8px 0 16px}
.muted,.help{color:var(--muted)}
.help{font-size:12px;margin:6px 0 0}
.shell{display:grid;grid-template-columns:224px minmax(0,1fr);min-height:100vh}
aside{padding:28px 18px;background:#14263f;color:#c4cede;position:sticky;top:0;height:100vh;display:flex;flex-direction:column}
.brand{color:white;font-size:22px;font-weight:750;letter-spacing:-.7px;margin:0 12px 36px}
.brand small{font-size:12px;color:#9facbf;display:block;font-weight:400;letter-spacing:0;margin-top:6px}
.brand-mark{display:inline-grid;place-items:center;background:#4776ee;width:30px;height:30px;border-radius:8px;margin-right:8px;color:white;font-size:18px}
nav{display:grid;gap:7px}
nav a{text-decoration:none;padding:12px 14px;border-radius:8px;color:#c4cede;display:flex;align-items:center;gap:12px}
nav a[aria-current=page]{background:#2d4362;color:white}
nav a:hover{background:#223954}
.nav-icon{width:20px;text-align:center;font-size:18px}
.aside-foot{margin-top:auto;padding:22px 12px 0;font-size:12px;color:#9facbf}
.aside-foot a{color:#ccd9f1}
main{padding:30px 38px 50px;max-width:1500px;width:100%;margin:0 auto;min-width:0}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px;gap:18px}
.run-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.run{padding:7px 12px;border:1px solid var(--line);border-radius:20px;background:white;font-size:12px}
.run::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:#9aa7ba;margin-right:8px}
.notice{padding:12px 16px;background:#e9f0ff;border-left:3px solid var(--blue);margin-bottom:18px;border-radius:6px;overflow-wrap:anywhere}
.notice.error{background:#fbe8eb;border-color:var(--red);color:var(--red)}
.notice:empty{display:none}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:24px;margin-bottom:22px}
.section-head,.dialog-top{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:18px}
.section-head h2{margin:0}
.welcome{display:grid;grid-template-columns:1.5fr 1fr;gap:32px;background:#eaf0fc;border:1px solid #d6e2fb;border-radius:16px;padding:32px;margin-bottom:24px}
.welcome h2{font-size:32px;line-height:1.35;letter-spacing:-1px;margin-bottom:12px}
.welcome p{color:#526585;margin-bottom:20px}
.journey{display:grid;align-content:center;gap:15px}
.journey div{display:flex;align-items:center;gap:13px}
.journey b{background:white;border:1px solid #ccdafa;border-radius:50%;width:30px;height:30px;display:grid;place-items:center;color:var(--blue)}
.journey span{font-weight:600}
.journey small{display:block;font-weight:400;color:var(--muted)}
.hero{display:grid;grid-template-columns:1fr auto;align-items:center;padding:22px 26px;background:white;border:1px solid var(--line);border-radius:12px;margin-bottom:16px}
.hero-kicker{font-size:13px;color:var(--muted)}
.hero-number{display:flex;align-items:baseline;gap:12px}
.hero-number strong{font-size:42px;letter-spacing:-1px;line-height:1.5;font-variant-numeric:tabular-nums}
.hero-number span{color:var(--muted)}
.hero-actions{display:flex;gap:10px;align-items:center}
.hero-actions a{display:none}
.auto-help{margin:0 0 20px}
.quick-metrics,.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid var(--line);background:white;border-radius:12px;margin-bottom:22px;overflow:hidden}
.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}
.quick-metrics>div,.metric{padding:18px 22px;border-right:1px solid var(--line)}
.quick-metrics>div:last-child,.metric:last-child{border:0}
.quick-metrics strong,.metric strong{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.quick-metrics span,.metric span{color:var(--muted)}
.sources,.source-cards,.run-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.run-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
.source-cards label,.run-cards label{font-weight:400;border:1px solid var(--line);border-radius:10px;padding:18px;background:#fff;cursor:pointer}
.source-cards label:has(input:checked),.run-cards label:has(input:checked){border-color:var(--blue);background:#f0f5ff;box-shadow:inset 0 0 0 1px var(--blue)}
.source-cards strong,.run-cards strong{display:block;margin:8px 0 4px}
.source-cards span,.run-cards span{display:block;color:var(--muted);font-size:12px}
.source{padding:20px;border-top:3px solid var(--blue);background:white;border-radius:8px;margin-bottom:20px}
.source.news{border-color:var(--green)}
.source.trends{border-color:#9567d7}
.source dl{display:grid;grid-template-columns:1fr auto;gap:8px}
.source dt{color:var(--muted)}
.source dd{margin:0}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 24px}
.wide{grid-column:1/-1}
.checks{display:flex;gap:18px;flex-wrap:wrap}
.checks label{font-weight:400}
.actions,.filters,.pager{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.actions{margin-top:18px}
.filters{margin-bottom:16px;align-items:flex-end}
.filters label{font-size:12px}
.filters>div{min-width:130px;flex:1}
.filters .query-filter{flex:2}
.table-scroll{overflow:auto}
table{border-collapse:collapse;width:100%;min-width:720px}
th,td{text-align:left;padding:15px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);background:#f7f9fc;font-weight:600}
td{font-size:13px}
.nowrap{white-space:nowrap}
.badge{display:inline-block;font-size:11px;padding:3px 9px;border-radius:6px;background:#edf1f7;color:#5b6c84}
.badge.ok{background:var(--green-bg);color:var(--green)}
.badge.err{background:#fce9ed;color:var(--red)}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}
.mini{font-size:12px;padding:5px 9px}
.pager{justify-content:flex-end;margin-top:18px}
.pager>span{margin-right:auto;color:var(--muted);font-size:12px}
.result-list{display:grid;gap:12px}
.result{background:white;border:1px solid var(--line);border-radius:10px;padding:20px 24px;min-width:0}
.result h3{font-size:17px;margin:10px 0 8px;line-height:1.55}
.result h3 a{color:#234889;text-decoration:none}
.result h3 a:hover{text-decoration:underline}
.result p{margin:10px 0;color:#526079}
.result-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.result-meta button{margin-left:auto}
.result details{margin-top:14px}
.result pre,pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;background:#f3f6fa;border-radius:6px;padding:16px;max-height:440px;overflow:auto}
.result ul{padding-left:20px}
.empty{padding:38px 20px;text-align:center;color:var(--muted)}
.empty a{display:inline-block;margin-top:10px}
.loading{padding:30px;color:var(--muted);background:white;border:1px solid var(--line);border-radius:10px}
.loading::before{content:"";display:inline-block;width:12px;height:12px;border:2px solid #c7d5f6;border-top-color:var(--blue);border-radius:50%;margin-right:10px;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}
}
.tabs{display:flex;gap:7px}
.tabs button[aria-pressed=true]{background:var(--blue);color:white}
.advanced{margin:20px 0}
.advanced>summary,details>summary{cursor:pointer;color:var(--muted);font-size:13px;padding:10px 0}
.advanced[open]>summary{margin-bottom:12px}
.foot{color:var(--muted);font-size:12px;margin-top:26px}
.json-dialog{width:min(850px,92vw);max-height:88vh;border:1px solid var(--line);border-radius:14px;padding:24px;color:var(--ink)}
dialog::backdrop{background:#14263f88;backdrop-filter:blur(4px)}
.switch-row{padding:16px 0;border-bottom:1px solid var(--line)}
.limitations{color:var(--muted);font-size:13px}
.wizard-layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:24px;align-items:start}
.wizard-note{position:sticky;top:24px;background:#eaf0fc;border:1px solid #d6e2fb;border-radius:12px;padding:24px}
.wizard-note h3{margin-bottom:14px}
.field-tags{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}
.field-tags span{background:white;border-radius:5px;padding:4px 9px;font-size:12px;color:#466084}
.stepper{display:flex;gap:8px;list-style:none;padding:0;margin:0 0 28px}
.stepper li{flex:1;padding-bottom:12px;border-bottom:3px solid var(--line);color:var(--muted);font-size:13px}
.stepper li[aria-current=step]{color:var(--blue);border-color:var(--blue);font-weight:650}
.wizard-controls{display:flex;justify-content:space-between;align-items:center;padding-top:20px;border-top:1px solid var(--line);margin-top:24px}
.wizard-controls .actions{margin:0}
.review-box{padding:18px;background:#f7f9fc;border-radius:8px;line-height:2}
.template-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.template-buttons button{font-size:12px;padding:7px 12px}
.check{margin-top:20px;font-weight:400}
.data-guide{padding:14px 18px;background:#f0f5ff;border-radius:8px;margin-bottom:20px;font-size:13px;color:#496284}
.result-list[aria-busy=true]{opacity:.6}
.toast-dismiss{float:right;background:transparent;color:inherit;border:0;padding:0 4px}
.task-link{border:0;background:none;color:var(--blue);padding:0;text-align:left;white-space:normal;font-size:14px}
.preview-banner{padding:10px 38px;background:#fff0d6;color:#7b5618;font-size:12px}
[hidden]{display:none!important}
@media(max-width:1100px){main{padding:25px}
.wizard-layout{grid-template-columns:minmax(0,1fr)}
.wizard-note{position:static}
.welcome{gap:20px;padding:25px}
.welcome h2{font-size:27px}
}
@media(max-width:760px){.shell{display:block}
aside{height:auto;position:static;padding:16px 14px}
.brand{font-size:19px;margin:0 0 12px}
.brand small,.aside-foot{display:none}
nav{display:flex;gap:4px;overflow:auto}
nav a{font-size:12px;padding:8px 10px;white-space:nowrap}
.nav-icon{display:none}
main{padding:22px 14px}
.top{align-items:flex-start;flex-direction:column}
.top h1{font-size:25px}
.run-actions{width:100%}
.run-actions button{padding:7px 10px;font-size:12px}
.welcome{grid-template-columns:1fr;padding:23px}
.welcome h2{font-size:27px}
.journey{display:none}
.hero{padding:18px;gap:10px}
.hero-number{display:block}
.hero-number strong{font-size:35px}
.hero-number span{display:block;font-size:12px}
.quick-metrics>div,.metric{padding:12px}
.quick-metrics strong,.metric strong{font-size:20px}
.quick-metrics span,.metric span{font-size:11px}
.sources,.source-cards,.run-cards,.grid{grid-template-columns:1fr}
.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
.wide{grid-column:auto}
.panel{padding:18px}
.section-head{flex-wrap:wrap}
.filters>div{min-width:calc(50% - 12px)}
.result{padding:16px}
.result-meta button{margin-left:0}
.wizard-controls{flex-wrap:wrap;gap:10px}
.wizard-note{padding:18px}
.preview-banner{padding:10px 14px}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;scroll-behavior:auto!important}
}

.result .help,.result p,.record-facts span{overflow-wrap:anywhere;min-width:0}.field-caption{display:block;font-size:12px;color:var(--muted);font-weight:500;margin-bottom:5px}.record-facts{display:flex;gap:28px;flex-wrap:wrap;padding:12px 0 18px;color:#445a79;font-size:13px}.record-facts small{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.saved-table{min-width:0;table-layout:fixed;margin-top:12px}.saved-table th:first-child{width:23%}.saved-table td:nth-child(2){width:40%;overflow-wrap:anywhere}.saved-table th,.saved-table td{white-space:normal;overflow-wrap:anywhere}.saved-table tbody th{background:transparent;color:var(--ink)}@media(max-width:600px){.saved-table{table-layout:auto}.saved-table thead{display:none}.saved-table tr{display:block;padding:12px 0;border-bottom:1px solid var(--line)}.saved-table th,.saved-table td{display:block;width:100%!important;border:0;padding:4px 0}.json-dialog{padding:18px}}
</style></head><body>
<div class="shell"><aside><div class="brand"><span class="brand-mark" aria-hidden="true">⌕</span>SearchFlow<small>把搜索变成可用的数据</small></div><nav aria-label="主导航"><a href="#overview" data-view="overview"><span class="nav-icon" aria-hidden="true">▦</span>概览</a><a href="#tasks" data-view="tasks"><span class="nav-icon" aria-hidden="true">☷</span>采集任务</a><a href="#results" data-view="results"><span class="nav-icon" aria-hidden="true">▤</span>数据集</a><a href="#whale" data-view="whale"><span class="nav-icon" aria-hidden="true">⇄</span>平台同步</a><a href="#settings" data-view="settings"><span class="nav-icon" aria-hidden="true">⚙</span>设置</a></nav><div class="aside-foot">搜索、新闻与热门话题<br>统一保存，随时导出。<br><a href="#create">＋ 新建采集任务</a></div></aside><main><header class="top"><div><h1 id="pageTitle">工作台</h1><div class="muted" id="pageSubtitle">看结果，不用先理解系统</div></div><a class="button-link" href="#create">＋ 新建任务</a></header><div id="notice" class="notice" role="status" aria-live="polite"></div>

<section id="overviewView"><div class="welcome"><div><h2>你关心的搜索，<br>整理成一个数据集。</h2><p>收集 Google 网页、新闻和热门话题，保留来源与发现时间。用关键词开始，拿到可筛选、可导出的数据。</p><a class="button-link" href="#create">＋ 新建采集任务</a> <a class="button-link secondary" href="#results">浏览数据集</a></div><div class="journey"><div><b>1</b><span>选择采集内容<small>网页、新闻或当前热门话题</small></span></div><div><b>2</b><span>创建采集任务<small>采集一批，或持续跟踪</small></span></div><div><b>3</b><span>使用你的数据<small>筛选、导出，或同步到 Whale</small></span></div></div></div><div class="hero"><div><div class="hero-kicker">最近 24 小时</div><div class="hero-number"><strong id="newLinks">—</strong><span>新增唯一链接</span></div><div class="help">已与全部历史 URL 去重；重复结果仍保留查询与排名记录。</div></div><div class="hero-actions"><a href="#tasks">创建采集任务</a><a href="#results" class="secondary-link">查看与导出</a><button type="button" id="refreshLatest" class="secondary">刷新</button></div></div><details class="advanced"><summary>查看采集健康度</summary><div class="quick-metrics"><div><strong id="attempts">—</strong><span>请求</span></div><div><strong id="successRate">—</strong><span>成功率</span></div><div><strong id="failures">—</strong><span>失败</span><small id="captchaHint" class="muted">—</small></div></div></details><div class="section-head"><h2>最近收集的数据</h2><div class="actions" style="margin:0"><a href="#results">全部数据</a></div></div><div id="latestRows" class="result-list latest-list"><div class="loading">正在读取已采集数据…</div></div>
<details class="advanced"><summary>运营统计、来源拆解与异常</summary><div class="sources" id="sourceStats"></div>
<div class="panel"><h2>异常分布</h2><div class="table-scroll"><table><thead><tr><th>来源</th><th>分类</th><th>错误码</th><th>最近 24 小时请求数</th></tr></thead><tbody id="errorGroups"></tbody></table></div></div>
<div class="panel"><h2>最近异常</h2><div class="help">显示最新 15 条留存异常；可能早于最近 24 小时。</div><div class="table-scroll"><table><thead><tr><th>时间</th><th>来源 / 查询</th><th>异常</th><th>HTTP</th><th>数据</th></tr></thead><tbody id="recentErrors"></tbody></table></div></div><p class="foot">各来源的链接可能重叠；首次发现贡献之和等于全局新增。新闻包装跳转地址仍可能与出版社地址分别计数。</p></details></section>

<section id="createView" hidden><div class="wizard-layout"><div class="panel"><div class="section-head"><h2>新建采集任务</h2><a href="#tasks">返回任务列表</a></div><ol class="stepper" aria-label="创建任务步骤"><li aria-current="step">1 采集内容</li><li>2 搜索范围</li><li>3 确认并创建</li></ol><form id="taskForm"><div id="wizardStep0"><div class="template-buttons" aria-label="快捷用途"><button type="button" class="secondary" data-template="web">网页资料</button><button type="button" class="secondary" data-template="news">新闻追踪</button><button type="button" class="secondary" data-template="trends">发现热点</button></div><fieldset><legend>选择数据源</legend><div class="source-cards"><label><input type="checkbox" name="taskSource" value="google_web" checked><strong>Google Web</strong><span>自然结果、标题、摘要、链接和排名</span></label><label><input type="checkbox" name="taskSource" value="google_news"><strong>Google News</strong><span>新闻搜索、媒体和发布时间</span></label><label><input type="checkbox" name="taskSource" value="google_trends"><strong>Google Trends</strong><span>当前热门话题及相关报道</span></label></div></fieldset>
</div><div id="wizardStep1" hidden><label for="keywords">你想搜索什么？</label><textarea id="keywords" placeholder="每行一个关键词，例如：&#10;solar energy research&#10;人工智能政策" maxlength="15500"></textarea><p class="help">Web 和 News 需要关键词；纯 Trends 可以留空。</p>
<fieldset><legend>运行方式</legend><div class="run-cards"><label><input type="radio" name="taskMode" value="once" checked><strong>采集一次</strong><span>适合拿一批结果；跑完自动结束</span></label><label><input type="radio" name="taskMode" value="scheduled"><strong>持续采集</strong><span>适合监控；按系统间隔重复</span></label></div></fieldset>

<details class="advanced"><summary>高级参数：页数、地区、语言、预算</summary><div class="grid"><div id="webPagesField"><label for="taskPages">Google Web 页数</label><select id="taskPages"><option value="1">1 页，最快</option><option value="3">3 页</option><option value="5">5 页</option><option value="10">10 页</option><option value="11">11 页，范围更广</option></select></div><div><label for="taskBudget">最大成功搜索次数</label><input id="taskBudget" type="number" min="1" max="10000" placeholder="不限制"><p class="help">仅“采集一次”生效。按批次检查，不保证严格限制请求总数。</p></div><div id="webRegionField"><label for="webRegion">Web 搜索地区</label><select id="webRegion"></select></div><div id="webLanguageField"><label for="webLanguage">Web 语言</label><select id="webLanguage"><option value="auto">自动</option><option value="en">英语</option><option value="zh">中文</option></select></div><div id="newsLocaleField" hidden><label for="newsLocale">News 地区及语言</label><select id="newsLocale"></select></div><div id="trendsGeoField" hidden><label for="trendsGeo">Trends 地区</label><select id="trendsGeo"></select></div></div><p class="help">默认页数、地区与预算即可满足大多数任务，不用改就跳过这里。</p></details>
</div><div id="wizardStep2" hidden><h3>确认你的采集任务</h3><div id="taskReview" class="review-box"></div><label class="check"><input type="checkbox" id="taskWhaleUpload">结果同时加入 Whale 上传队列</label><p class="help">上传开关关闭时，链接会保留在队列等待。</p></div><p class="help" id="taskEstimate">读取搜索范围…</p><div class="wizard-controls"><button type="button" id="wizardBack" class="secondary" hidden>上一步</button><button type="button" id="wizardNext">下一步</button><div class="actions" id="wizardSubmit" hidden><button type="submit" id="createDraft" value="draft" class="secondary" formnovalidate>保存待启动</button><button type="submit" id="createStart" value="start">创建并开始采集</button></div></div></form></div><div class="wizard-note"><h3>你会获得什么？</h3><p id="sourceDescription">搜索结果的标题、链接、摘要和自然排名。</p><div id="outputFields" class="field-tags"></div><p class="help">保留查询与采集时间。缺失字段显示“未知”；新链接按历史去重，不把重复结果当新增。</p><hr style="border:0;border-top:1px solid #d6e2fb;margin:20px 0"><strong>如何使用结果</strong><p class="help">到“数据集”筛选结果，下载 CSV 供表格使用，或同步到 Whale。</p></div></div></section><section id="tasksView" hidden><div class="data-guide">在这里查看任务进展、暂停或继续采集。结果会保存在数据集，可随时导出。</div>
<div class="panel"><div class="section-head"><h2>采集任务</h2><div class="actions" style="margin:0"><button id="refreshTasks" class="secondary" type="button">刷新</button><a class="button-link" href="#create">＋ 新建任务</a></div></div><form class="filters" id="taskFilters"><input type="hidden" id="taskKind" value="manual"><div><label for="taskStatus">状态</label><select id="taskStatus"><option value="">全部</option><option value="active">运行中</option><option value="paused">暂停 / 待启动</option><option value="stopped">已停止</option><option value="completed">已完成</option><option value="failed">失败</option></select></div><div class="query-filter"><label for="taskQ">搜索任务</label><input id="taskQ" type="search"></div><button type="submit" class="secondary">筛选</button></form><div class="table-scroll"><table><thead><tr><th>任务</th><th>来源</th><th>状态</th><th>采集进展</th><th>新增链接</th><th>操作</th></tr></thead><tbody id="taskRows"></tbody></table></div><div class="pager"><span id="taskCount"></span><button id="taskPrev" class="secondary" type="button">上一页</button><button id="taskNext" class="secondary" type="button">下一页</button></div></div></section>

<section id="resultsView" hidden><div class="data-guide">这里保存任务收集到的搜索结果。先筛选你需要的数据，再导出 CSV；每条数据包括内容字段与采集条件，缺失字段明确显示未知。热门话题与链接数量分开理解。</div><div class="panel"><div class="section-head"><h2>数据结果</h2><div class="tabs" role="group" aria-label="数据视图"><button id="resultTab" aria-pressed="true">结果</button><button id="requestTab" class="secondary" aria-pressed="false">采集记录</button></div></div><form id="resultFilters"><div class="filters"><div><label for="resultSource">来源</label><select id="resultSource"><option value="">全部</option><option value="google_web">Google Web</option><option value="google_news">Google News</option><option value="google_trends">Google Trends</option></select></div><div class="query-filter"><label for="resultQ">搜索</label><input type="search" id="resultQ" placeholder="关键词、标题或链接"></div><button type="submit">应用</button><button id="resetFilters" class="secondary" type="button">重置</button></div><details class="advanced"><summary>高级筛选：任务、状态、日期、新链接</summary><div class="filters"><div><label for="resultCampaign">采集任务</label><select id="resultCampaign"><option value="">全部任务</option></select><p class="help">最近 100 个任务；更早的任务可从任务列表进入结果。</p></div><div><label for="resultStatus">采集状态</label><select id="resultStatus"><option value="">全部</option><option value="success">成功</option><option value="error">异常</option></select></div></div><div class="filters"><div><label for="resultSince">开始日期（UTC）</label><input type="date" id="resultSince"></div><div><label for="resultUntil">结束日期（UTC）</label><input type="date" id="resultUntil"></div><label><input type="checkbox" id="newOnly">仅首次发现</label></div></details></form><div class="actions"><button type="button" id="exportJson" class="secondary">导出数据文件（JSONL）</button><button type="button" id="exportCsv" class="secondary">导出 CSV</button><button type="button" id="cancelExport" class="secondary" hidden>取消导出</button></div><p class="help" id="resultHelp">保存的是搜索结果：标题、链接、摘要及排名，不是文章全文。点击“查看已保存数据”了解每个字段。</p></div><div id="resultRows" class="result-list"><div class="loading">正在读取已采集数据…</div></div><div class="pager"><span id="resultCount"></span><button id="resultPrev" class="secondary" type="button">上一页</button><button id="resultNext" class="secondary" type="button">下一页</button></div></section>

<section id="whaleView" hidden><div class="data-guide">选择已有链接加入上传队列，或在创建任务时开启同步。平台接收回执代表接口已接收，不代表最终入库或索引完成。</div><div class="panel"><div class="section-head"><h2>平台同步 · Whale</h2><span class="muted">将搜索链接交给你的数据平台</span></div><p id="whaleConnection" class="muted">读取对接状态…</p><form id="whaleSettingsForm"><div class="checks"><label><input type="checkbox" id="whaleUploadSwitch">允许上传队列数据</label><label><input type="checkbox" id="whaleClaimSwitch">接收平台下发任务</label></div><div class="actions"><button id="saveWhaleSettings">保存集成开关</button></div></form><p class="help" id="whaleRuntime"></p></div>
<div class="metrics"><div class="metric"><strong id="whalePending">0</strong><span>待上传</span></div><div class="metric"><strong id="whaleReceived">0</strong><span>接口已接收</span></div><div class="metric"><strong id="whaleDuplicate">0</strong><span>重复</span></div><div class="metric"><strong id="whaleRejected">0</strong><span>拒收</span></div></div>
<div class="panel"><div class="section-head"><h2>上传已有数据</h2><span class="muted">选择来源和数量，即可加入上传队列</span></div><form id="whaleUploadForm"><div class="filters"><div><label for="whaleSource">来源</label><select id="whaleSource"><option value="">全部</option><option value="google_web">Google Web</option><option value="google_news">Google News</option><option value="google_trends">Google Trends 报道</option></select></div><div><label for="whaleLimit">本次数量</label><input id="whaleLimit" type="number" min="1" max="10000" value="100"></div><div class="query-filter"><label for="whaleQuery">筛选</label><input id="whaleQuery" placeholder="关键词、标题或链接"></div><button id="createWhaleUpload" type="submit" value="start">加入队列</button></div><details class="advanced"><summary>高级筛选：任务、日期、链接状态</summary><div class="filters"><div><label for="whaleCampaign">采集任务</label><select id="whaleCampaign"><option value="">全部任务</option></select></div><div><label for="whaleSince">开始日期</label><input type="date" id="whaleSince"></div><div><label for="whaleUntil">结束日期</label><input type="date" id="whaleUntil"></div><label><input type="checkbox" id="whaleNewOnly" checked>仅首次发现</label></div></details><button class="secondary" type="submit" value="draft" style="margin-top:14px">保存待上传</button></form></div>
<div class="panel"><div class="section-head"><h2>上传与平台任务</h2><button id="refreshWhale" class="secondary">刷新</button></div><div class="table-scroll"><table><thead><tr><th>任务</th><th>状态</th><th>链接数</th><th>已接收 / 重复</th><th>待上传 / 拒收</th><th>操作</th></tr></thead><tbody id="whaleJobs"></tbody></table></div><div class="pager"><span id="whaleJobCount"></span><button id="whalePrev" class="secondary" type="button">上一页</button><button id="whaleNext" class="secondary" type="button">下一页</button></div></div></section>
<dialog id="whaleReceiptDialog" class="json-dialog"><div class="dialog-top"><strong>平台回执与数据</strong><button id="closeWhaleReceipts" class="secondary">关闭</button></div><div class="help">queued 已入队，accepted 接口接受，duplicate 重复；最终入库状态尚未核验。</div><div id="whaleReceiptSummary" class="table-scroll"></div><div class="pager"><button id="whaleReceiptPrev" class="secondary" type="button">上一页</button><button id="whaleReceiptNext" class="secondary" type="button">下一页</button></div></dialog>

<section id="settingsView" hidden><div class="data-guide">了解各来源能提供的数据，并管理可选的文章正文功能。</div><div class="panel"><h2>数据偏好</h2><form id="settingsForm"><div class="switch-row"><label><input type="checkbox" id="bodySwitch">获取文章正文</label><p class="help">默认关闭；搜索链接、标题、摘要与去重不依赖正文。开启后由正文服务处理，已有搜索结果仍可使用。</p></div><div class="actions"><button type="submit" id="saveSettings">保存偏好</button><span class="help" id="settingsSaved"></span></div></form></div><details class="advanced"><summary>数据范围与历史页面</summary><div class="panel limitations"><p>Web：自然网页结果及上游实际提供的字段。News：Google News 搜索 RSS；无法离线解析的 Google 跳转链接明确标记，列表顺序不是 Google 排名。</p><p>Trends：Trending Now 当前热门话题及相关报道；关键词历史热度曲线、区域兴趣、高级商业模块和第三方 RSS 尚未支持。缺失数据为未知 / null。</p><p>旧版归档：<a href="/articles">历史正文</a>　<a href="/stats">历史实验统计</a>（仅供历史数据查阅）</p></div></details></section>
<footer class="foot" id="updated">正在连接后端…</footer></main></div>
<dialog id="savedDialog" class="json-dialog"><div class="dialog-top"><div><strong>已保存的数据</strong><p class="help">这是一条搜索结果记录。摘要来自搜索页面，不是文章全文。</p></div><button id="closeSaved" class="secondary">关闭</button></div><label for="savedRecordSelect">选择已保存的结果</label><select id="savedRecordSelect"></select><div id="savedFields"></div><div class="actions"><button id="downloadSaved" class="secondary">下载这条数据（CSV）</button></div></dialog>
<script>
const $=id=>document.getElementById(id), names={google_web:'Google Web',google_news:'Google News',google_trends:'Google Trends'}, states={active:'运行中',paused:'暂停 / 待启动',stopped:'已停止',completed:'已完成',failed:'失败'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),fmt=v=>Number(v||0).toLocaleString('zh-CN'),rate=v=>v==null?'未知':(v*100).toFixed(1)+'%',time=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'未知';
const safeLink=v=>typeof v==='string'&&/^https?:\/\//i.test(v)?esc(v):'#';
let view='overview',taskOffset=0,resultOffset=0,resultMode='results',taskTotal=0,resultTotal=0,settingsDirty=false,settingsLoaded=false,taskSeq=0,resultSeq=0;
function notice(msg,error=false){$('notice').textContent=msg;$('notice').className='notice'+(error?' error':'')}
async function api(url,body){const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败 '+r.status);return d}
function showView(){const id=location.hash.slice(1);view=['overview','create','tasks','results','whale','settings'].includes(id)?id:'overview';for(const x of ['overview','create','tasks','results','whale','settings']){$(x+'View').hidden=x!==view;const nav=document.querySelector(`[data-view="${x}"]`);if(nav)nav.setAttribute('aria-current',x===view||(x==='tasks'&&view==='create')?'page':'false')}$('pageTitle').textContent={overview:'概览',create:'新建采集任务',tasks:'采集任务',results:'数据集',whale:'平台同步',settings:'数据偏好'}[view];$('pageSubtitle').textContent={overview:'从搜索到数据，让信息有迹可循',create:'告诉我们你想收集什么，其余按步骤完成',tasks:'管理采集进展，随时查看对应结果',results:'找到需要的内容，导出或同步使用',whale:'管理链接上传与平台下发任务',settings:'了解数据范围与可选采集内容'}[view];refreshView()}
function sourceFields(){const selected=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>x.value);for(const [id,s] of [['webPagesField','google_web'],['webRegionField','google_web'],['webLanguageField','google_web'],['newsLocaleField','google_news'],['trendsGeoField','google_trends']])$(id).hidden=!selected.includes(s);updateTaskEstimate();updateOutputGuide()}
function updateTaskEstimate(){$('taskBudget').disabled=document.querySelector('[name=taskMode]:checked').value!=='once';const selected=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>x.value);const words=new Set($('keywords').value.split('\n').map(x=>x.trim()).filter(Boolean)).size;const total=(selected.includes('google_web')?words*Number($('taskPages').value||1):0)+(selected.includes('google_news')?words:0)+(selected.includes('google_trends')?1:0);$('taskEstimate').textContent=selected.length?`一轮计划 ${fmt(total)} 次搜索；失败重试可能增加请求，缓存可减少上游访问。`:'请至少选择一个数据源。'}
document.querySelectorAll('[name=taskSource]').forEach(x=>x.addEventListener('change',sourceFields));document.querySelectorAll('[name=taskMode]').forEach(x=>x.addEventListener('change',updateTaskEstimate));$('keywords').addEventListener('input',updateTaskEstimate);$('taskPages').addEventListener('change',updateTaskEstimate);$('refreshTasks').onclick=()=>loadTasks().catch(e=>notice(e.message,true));
async function loadSettings(force=false){const s=await api('/api/collector-settings');if(!settingsLoaded){for(const id of ['webRegion','trendsGeo'])$(id).innerHTML=s.regions.map(x=>`<option value="${esc(x)}">${esc(regionNames[x]||x)}</option>`).join('');$('newsLocale').innerHTML=s.news_editions.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');$('webRegion').value=s.web_region;$('webLanguage').value=s.web_language;$('trendsGeo').value=s.trends_geos[0];$('newsLocale').value=s.news_locales[0];}if(!settingsDirty||force){$('bodySwitch').checked=s.content_fetch_enabled;$('settingsSaved').textContent=s.updated_at?'上次保存 '+time(s.updated_at):'';settingsDirty=false;}settingsLoaded=true;updateTaskEstimate();return s;}
async function loadOverview(){const s=await api('/api/serp/stats');$('newLinks').textContent=fmt(s.links.new_last_24h);$('attempts').textContent=fmt(s.requests.attempts);$('successRate').textContent=rate(s.requests.success_rate);$('failures').textContent=fmt(s.requests.failed);$('captchaHint').textContent='明确验证码 '+fmt(s.requests.explicit_captcha);const desc={google_web:'自然结果、摘要与实际排名',google_news:'新闻 RSS、媒体与发布时间',google_trends:'当前热门话题与相关报道'};$('sourceStats').innerHTML=Object.entries(s.sources).map(([k,x])=>`<div class="source ${k.replace('google_','')}"><h3>${names[k]}</h3><span class="muted">${desc[k]}</span><dl><dt>首次发现贡献</dt><dd><strong>${fmt(x.first_discovery_contribution)}</strong></dd><dt>本来源首次收录链接</dt><dd>${fmt(x.unique_new_urls)}</dd><dt>请求 / 成功率</dt><dd>${fmt(x.attempts)} / ${rate(x.success_rate)}</dd><dt>异常 / 验证码</dt><dd>${fmt(x.failed)} / ${fmt(x.explicit_captcha)}</dd></dl></div>`).join('');$('errorGroups').innerHTML=s.errors.map(x=>`<tr><td>${names[x.source]}</td><td>${esc(x.classification)}</td><td>${esc(x.error_code||'未知')}</td><td>${fmt(x.count)}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">最近 24 小时没有异常请求。</td></tr>';$('recentErrors').innerHTML=s.recent_errors.map(x=>`<tr><td class="nowrap">${time(x.completed_at)}</td><td>${names[x.source]}<div>${esc(x.query)}</div></td><td>${esc(x.classification)}<div class="help">${esc(x.error_code||'未知')}</div></td><td>${esc(x.http_status??'未知')}</td><td>${esc(requestStates[x.classification]||x.classification)}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">没有留存异常。</td></tr>'}
function taskParams(){return new URLSearchParams({kind:$('taskKind').value,status:$('taskStatus').value,q:$('taskQ').value,offset:taskOffset,limit:20})}
async function loadTasks(){const seq=++taskSeq,d=await api('/api/serp/tasks?'+taskParams());if(seq!==taskSeq)return;taskTotal=d.total;$('taskRows').innerHTML=d.tasks.map(x=>{const optNames={web_region:'Web 地区',web_language:'Web 语言',web_pages:'Web 页数',news_locale:'News 地区',trends_geo:'Trends 地区'};const opts=Object.entries(x.options||{}).map(([k,v])=>`${optNames[k]||k}: ${v}`).join(' / ');const budget=fmt(x.successful)+(x.max_successful_searches==null?'':' / '+fmt(x.max_successful_searches));let buttons=x.status==='active'?'<button class="mini secondary" data-op="pause">暂停</button>':x.status==='paused'?(x.started?'<button class="mini" data-op="resume">继续</button>':'<button class="mini" data-op="start">启动</button>'):x.status==='failed'?'<button class="mini" data-op="start">重新启动</button>':'';if(['active','paused','failed','completed'].includes(x.status))buttons+='<button class="mini danger" data-op="stop">停止</button>';return `<tr data-cid="${esc(x.id)}"><td><div class="task-query">${esc(x.query)}</div>${x.aliases.length?`<div class="help">另有 ${fmt(x.aliases.length)} 个关键词</div><details><summary>查看全部关键词</summary><div>${x.aliases.map(esc).join('<br>')}</div></details>`:''}<div class="help">${x.mode==='once'?'采集一次':'持续采集'}</div><details><summary>任务详情</summary><div class="help">${esc(opts||'使用默认地区')}<br>编号 ${esc(x.id)}<br>创建 ${time(x.created_at)}<br>更新 ${time(x.updated_at)}<br>最近请求 ${time(x.last_request_at)}<br>模式 ${x.mode==='once'?'采集一次':'持续采集'}<br>异常 ${esc(x.last_error||'无')}</div></details></td><td>${x.sources.map(s=>esc(names[s]||s)).join('<br>')}</td><td><span class="badge ${['active','completed'].includes(x.status)?'ok':''}">${x.status==='paused'&&!x.started?'待启动':states[x.status]||esc(x.status)}</span></td><td>${fmt(x.requests)} 次搜索记录<div class="help">成功 ${budget} / 复用 ${fmt(x.cached)}${x.stop_reason?`<br>结束原因：${{scope_completed:'范围采完',max_successful_searches:'达到搜索上限'}[x.stop_reason]||x.stop_reason}`:''}</div></td><td>${fmt(x.new_links)}</td><td><div class="row-actions">${buttons}<button class="mini secondary" data-results="${esc(x.id)}">查看结果</button></div></td></tr>`}).join('')||'<tr><td colspan="6" class="empty">还没有符合条件的任务。<br><a href="#create">创建第一个采集任务</a></td></tr>';$('taskCount').textContent=`第 ${d.total?taskOffset+1:0}–${Math.min(taskOffset+20,d.total)} 个 / 共 ${fmt(d.total)} 个任务`;$('taskPrev').disabled=taskOffset===0;$('taskNext').disabled=taskOffset+20>=d.total}
$('taskForm').addEventListener('submit',async e=>{e.preventDefault();if(wizardStep!==2){wizardAdvance();return;}if(!validateScope())return;const start=e.submitter?.value!=='draft',sources=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>x.value),opts={};if(sources.includes('google_web'))Object.assign(opts,{web_pages:Number($('taskPages').value),web_region:$('webRegion').value,web_language:$('webLanguage').value});if(sources.includes('google_news'))opts.news_locale=$('newsLocale').value;if(sources.includes('google_trends'))opts.trends_geo=$('trendsGeo').value;$('createStart').disabled=$('createDraft').disabled=true;try{const d=await api('/api/serp/tasks',{keywords:$('keywords').disabled?[]:[...new Set($('keywords').value.split('\n').map(x=>x.trim()).filter(Boolean))],sources,options:opts,mode:document.querySelector('[name=taskMode]:checked').value,max_successful_searches:!$('taskBudget').disabled&&$('taskBudget').value?Number($('taskBudget').value):null,start,upload_to_whale:$('taskWhaleUpload').checked});notice(start?'任务已创建，正在等待采集结果。':'任务已保存，点击“启动”开始采集。');location.hash='tasks';$('taskKind').value='manual';$('taskStatus').value='';$('taskQ').value='';taskOffset=0;await loadTasks()}catch(e){notice(e.message,true)}finally{$('createStart').disabled=$('createDraft').disabled=false}});
$('taskFilters').addEventListener('submit',e=>{e.preventDefault();taskOffset=0;loadTasks().catch(e=>notice(e.message,true))});
$('taskRows').addEventListener('click',async e=>{const b=e.target.closest('button[data-op]');if(!b)return;const cid=b.closest('[data-cid]').dataset.cid;b.disabled=true;try{const op=b.dataset.op;await api(`/api/serp/tasks/${cid}/${op}`,{});notice({pause:'任务已暂停',stop:'任务已停止',start:'任务已启动',resume:'任务已继续'}[op]);await loadTasks()}catch(e){notice(e.message,true)}finally{b.disabled=false}});
$('taskPrev').onclick=()=>{taskOffset=Math.max(0,taskOffset-20);loadTasks().catch(e=>notice(e.message,true))};$('taskNext').onclick=()=>{taskOffset+=20;loadTasks().catch(e=>notice(e.message,true))};
function resultParams(){const f={source:$('resultSource').value,campaign_id:$('resultCampaign').value.trim(),q:$('resultQ').value.trim(),status:$('resultStatus').value,new_only:String($('newOnly').checked)};if($('resultSince').value)f.since=$('resultSince').value+'T00:00:00Z';if($('resultUntil').value){const end=new Date($('resultUntil').value+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+1);f.until=end.toISOString()}return new URLSearchParams(f)}
async function loadResults(){const seq=++resultSeq,p=resultParams();p.set('offset',resultOffset);p.set('limit',25);p.set('count_total','false');$('resultRows').setAttribute('aria-busy','true');const d=await api('/api/serp/'+(resultMode==='requests'?'browse-requests':'results')+'?'+p);if(seq!==resultSeq)return;$('resultRows').setAttribute('aria-busy','false');if(resultMode==='requests'){$('resultRows').innerHTML=`<div class="panel table-scroll"><table><thead><tr><th>采集时间</th><th>来源 / 搜索内容</th><th>采集状态</th><th>已保存结果 / 新链接</th><th>保存的数据</th></tr></thead><tbody>${d.rows.map(x=>{const items=x.response?.[sourceKey(x.source)]||[],keys=items.map((a,i)=>rememberRecord({id:x.id,ordinal:i+1,query:x.query,source:x.source,completed_at:x.completed_at,data:a,search_parameters:x.response.search_parameters}));return `<tr><td>${time(x.completed_at)}</td><td>${names[x.source]}<div>${esc(x.query)}</div></td><td><span class="badge ${['results','empty'].includes(x.status)?'ok':'err'}">${esc(requestStates[x.status]||x.status)}</span><div class="help">${esc(x.error_code||'')}</div></td><td>${fmt(items.length)} 条 / ${fmt(x.response.new_unique_links)} 个</td><td>${keys.length?`<button class="mini secondary" data-saved-group="${esc(keys.join('|'))}">查看已保存结果</button>`:'没有保存结果'}</td></tr>`}).join('')||'<tr><td colspan="5" class="empty">没有符合条件的采集记录。</td></tr>'}</tbody></table></div>`}else{$('resultRows').innerHTML=d.rows.map(renderResult).join('')||'<div class="panel empty">没有符合筛选条件的数据。<br><a href="#create">创建采集任务</a>，或扩大筛选范围。</div>'}$('resultCount').textContent=`显示第 ${d.rows.length?resultOffset+1:0}–${resultOffset+d.rows.length} ${resultMode==='requests'?'条采集记录':'条数据'}${d.has_more?'，还有更多':''}`;$('resultPrev').disabled=resultOffset===0;$('resultNext').disabled=!d.has_more;}
$('resultFilters').addEventListener('submit',e=>{e.preventDefault();resultOffset=0;loadResults().catch(e=>notice(e.message,true))});$('resetFilters').onclick=()=>{$('resultFilters').reset();resultOffset=0;loadResults().catch(e=>notice(e.message,true))};
function switchMode(mode){resultMode=mode;resultOffset=0;$('resultTab').setAttribute('aria-pressed',mode==='results');$('requestTab').setAttribute('aria-pressed',mode==='requests');$('resultHelp').textContent=mode==='results'?'保存的是搜索结果，不是文章全文。热门话题包含相关报道，话题数与链接数分开。':'采集记录用于查看成功、无结果及失败情况；导出始终提供最终保存的结果数据。';loadResults().catch(e=>notice(e.message,true))}$('resultTab').onclick=()=>switchMode('results');$('requestTab').onclick=()=>switchMode('requests');
$('resultPrev').onclick=()=>{resultOffset=Math.max(0,resultOffset-25);loadResults().catch(e=>notice(e.message,true))};$('resultNext').onclick=()=>{resultOffset+=25;loadResults().catch(e=>notice(e.message,true))};
let exportController=null;
async function downloadExport(format){if(exportController)return;const p=resultParams();p.set('mode','results');p.set('format',format);if(format==='csv')p.set('view','user');const controller=new AbortController();exportController=controller;const mode='results';$('exportCsv').disabled=$('exportJson').disabled=true;$('cancelExport').hidden=false;try{notice('正在准备导出全部筛选数据，数据较多时需要等待。你可以取消导出。');const r=await fetch('/api/serp/export?'+p,{signal:controller.signal});if(!r.ok){const d=await r.json();throw Error(d.error||'导出失败')}saveBlob(await r.blob(),`google-${mode}.${format}`);notice('导出完成：全部符合筛选的数据')}catch(e){notice(e.name==='AbortError'?'导出已取消，已采集的数据保留。':e.message,e.name!=='AbortError')}finally{exportController=null;$('exportCsv').disabled=$('exportJson').disabled=false;$('cancelExport').hidden=true}}
$('cancelExport').onclick=()=>exportController?.abort();
function saveBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}$('exportJson').onclick=()=>downloadExport('jsonl');$('exportCsv').onclick=()=>downloadExport('csv');
document.addEventListener('click',async e=>{const result=e.target.closest('[data-results]');if(result){selectTaskFilter('resultCampaign',result.dataset.results);resultOffset=0;location.hash='results';if(view==='results')loadResults().catch(e=>notice(e.message,true))}const b=e.target.closest('[data-saved],[data-saved-group]');if(b)await openSaved((b.dataset.savedGroup||b.dataset.saved).split('|'));});$('closeSaved').onclick=()=>$('savedDialog').close();$('savedRecordSelect').onchange=()=>renderSavedFields(savedRecords.get($('savedRecordSelect').value));$('downloadSaved').onclick=()=>downloadSavedRecord();
$('settingsForm').addEventListener('input',()=>{settingsDirty=true;$('settingsSaved').textContent='有未保存修改'});$('settingsForm').addEventListener('change',()=>{settingsDirty=true;$('settingsSaved').textContent='有未保存修改'});
$('settingsForm').addEventListener('submit',async e=>{e.preventDefault();$('saveSettings').disabled=true;try{await api('/api/collector-settings',{content_fetch_enabled:$('bodySwitch').checked});settingsDirty=false;await loadSettings(true);notice('数据偏好已保存，后续任务批次生效。')}catch(e){notice(e.message,true)}finally{$('saveSettings').disabled=false}});
async function loadLatest(){const d=await api('/api/serp/results?limit=10&count_total=false');$('latestRows').innerHTML=d.rows.map(renderResult).join('')||'<div class="panel empty">还没有保存数据。<a href="#create">创建任务，收集第一批数据</a></div>';}
$('refreshLatest').onclick=async()=>{$('refreshLatest').disabled=true;try{await loadLatest();notice('最新数据已刷新')}catch(e){notice(e.message,true)}finally{$('refreshLatest').disabled=false}};

let whaleOffset=0,whaleTotal=0,whaleDirty=false,whaleReceiptJob=null,whaleReceiptOffset=0;
const whaleStates={active:'处理中',paused:'已暂停',stopped:'已停止',succeeded:'上传流程完成',failed:'失败 / 需处理'};const whaleKind={platform:'平台下发',automatic:'任务同步',manual:'手动上传'};
async function loadWhale(){const [s,d]=await Promise.all([api('/api/whale/status'),api('/api/whale/jobs?limit=20&offset='+whaleOffset)]);const cfg=s.settings;
$('whaleConnection').textContent=(s.configured?'已配置平台凭据':'尚未配置平台凭据')+' / 数据集 '+s.dataset_id+' / 来源 '+s.source_platform;
if(!whaleDirty){$('whaleUploadSwitch').checked=cfg.uploads_enabled;$('whaleClaimSwitch').checked=cfg.claims_enabled}
$('whaleRuntime').textContent=(cfg.runtime.connected_at?'最近连接 '+time(cfg.runtime.connected_at):'尚未连接，开关默认关闭')+(cfg.runtime.last_error?' / 异常 '+cfg.runtime.last_error:'');
$('whalePending').textContent=fmt(s.counts.pending);$('whaleReceived').textContent=fmt((s.counts.queued||0)+(s.counts.accepted||0));$('whaleDuplicate').textContent=fmt(s.counts.duplicate);$('whaleRejected').textContent=fmt(s.counts.rejected);whaleTotal=d.total;
$('whaleJobs').innerHTML=d.jobs.map(x=>{const c=x.counts;let actions=x.status==='active'?'<button class="mini secondary" data-whale-op="pause">暂停</button>':x.status==='paused'?'<button class="mini" data-whale-op="resume">继续</button>':x.status==='failed'?'<button class="mini" data-whale-op="retry">重试</button>':'';if(['active','paused','failed'].includes(x.status))actions+='<button class="mini danger" data-whale-op="stop">停止</button>';const waiting=x.status==='active'&&!cfg.uploads_enabled?' / 上传开关已关闭':'';return `<tr data-whale-id="${esc(x.id)}"><td>${whaleKind[x.kind]||(x.automatic?'任务同步上传':'手动上传')}<div class="help">${esc(x.platform_task_id||x.id)}<br>${time(x.created_at)}</div><details><summary>同步范围</summary><div class="help">${esc(x.specification?.q||x.specification?.keyword||'全部符合条件的数据')} / ${esc(names[x.specification?.source]||'全部来源')}</div></details></td><td>${esc(whaleStates[x.status]||x.status)}${waiting}<div class="help">${esc(x.last_error||'')}</div></td><td>${fmt(x.collected)}</td><td>${fmt(x.received)} / ${fmt(x.duplicates)}</td><td>${fmt(c.pending)} / ${fmt(c.rejected)}</td><td><div class="row-actions">${actions}<button class="mini secondary" data-whale-receipts="${esc(x.id)}">数据与回执</button></div></td></tr>`}).join('')||'<tr><td colspan="6" class="empty">还没有上传任务。可选择已采集数据加入队列。</td></tr>';
$('whaleJobCount').textContent=`第 ${d.total?whaleOffset+1:0}–${Math.min(whaleOffset+20,d.total)} 个 / 共 ${fmt(d.total)} 个任务`;$('whalePrev').disabled=whaleOffset===0;$('whaleNext').disabled=whaleOffset+20>=d.total}
$('whaleSettingsForm').addEventListener('change',()=>whaleDirty=true);
$('whaleSettingsForm').addEventListener('submit',async e=>{e.preventDefault();$('saveWhaleSettings').disabled=true;try{await api('/api/whale/settings',{uploads_enabled:$('whaleUploadSwitch').checked,claims_enabled:$('whaleClaimSwitch').checked});whaleDirty=false;await loadWhale();notice('平台同步偏好已保存')}catch(e){notice(e.message,true)}finally{$('saveWhaleSettings').disabled=false}});
$('whaleUploadForm').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{const p={limit:Number($('whaleLimit').value),new_only:$('whaleNewOnly').checked,start:b.value!=='draft'};for(const [field,id] of [['source','whaleSource'],['q','whaleQuery'],['campaign_id','whaleCampaign']])if($(id).value.trim())p[field]=$(id).value.trim();if($('whaleSince').value)p.since=$('whaleSince').value+'T00:00:00Z';if($('whaleUntil').value){const end=new Date($('whaleUntil').value+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+1);p.until=end.toISOString()}const job=await api('/api/whale/jobs',p);whaleOffset=0;await loadWhale();notice('已保存 '+fmt(job.collected)+' 个链接。上传开关关闭时，数据保留在队列等待。')}catch(e){notice(e.message,true)}finally{b.disabled=false}});
async function loadWhaleReceipts(){const d=await api(`/api/whale/jobs/${whaleReceiptJob}/receipts?limit=50&offset=${whaleReceiptOffset}`);$('whaleReceiptSummary').innerHTML=`<table><thead><tr><th>链接</th><th>回执状态</th><th>尝试次数</th><th>失败原因</th></tr></thead><tbody>${d.receipts.map(x=>{const c=x.payload?.content||{},state=x.receipt?.receipt_status||x.state;return `<tr><td><a href="${safeLink(c.source_url)}" target="_blank" rel="noopener noreferrer">${esc(c.title||c.source_url||'未知链接')}</a></td><td>${esc(receiptStates[state]||state||'未知')}</td><td>${fmt(x.attempts)}</td><td>${esc(x.last_error||'无')}</td></tr>`}).join('')||'<tr><td colspan="4" class="empty">暂无回执，链接可能仍在等待上传。</td></tr>'}</tbody></table>`;$('whaleReceiptPrev').disabled=whaleReceiptOffset===0;$('whaleReceiptNext').disabled=d.receipts.length<50}
$('whaleJobs').addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;b.disabled=true;try{if(b.dataset.whaleOp){await api(`/api/whale/jobs/${b.closest('[data-whale-id]').dataset.whaleId}/${b.dataset.whaleOp}`,{});await loadWhale();notice('任务操作已保存')}else if(b.dataset.whaleReceipts){whaleReceiptJob=b.dataset.whaleReceipts;whaleReceiptOffset=0;await loadWhaleReceipts();$('whaleReceiptDialog').showModal()}}catch(e){notice(e.message,true)}finally{b.disabled=false}});
$('closeWhaleReceipts').onclick=()=>$('whaleReceiptDialog').close();$('whaleReceiptPrev').onclick=()=>{whaleReceiptOffset=Math.max(0,whaleReceiptOffset-50);loadWhaleReceipts().catch(e=>notice(e.message,true))};$('whaleReceiptNext').onclick=()=>{whaleReceiptOffset+=50;loadWhaleReceipts().catch(e=>notice(e.message,true))};
$('refreshWhale').onclick=()=>loadWhale().catch(e=>notice(e.message,true));$('whalePrev').onclick=()=>{whaleOffset=Math.max(0,whaleOffset-20);loadWhale().catch(e=>notice(e.message,true))};$('whaleNext').onclick=()=>{whaleOffset+=20;loadWhale().catch(e=>notice(e.message,true))};

const refreshing=new Set();async function refreshView(){const current=view;if(refreshing.has(current))return;refreshing.add(current);try{const jobs=[loadSettings()];if(current==='overview')jobs.push(loadLatest(),loadOverview());if(current==='tasks')jobs.push(loadTasks());if(current==='results')jobs.push(loadResults(),loadTaskChoices());if(current==='whale')jobs.push(loadWhale(),loadTaskChoices());await Promise.all(jobs);if(view===current)$('updated').textContent='每 10 秒刷新 / 最近读取 '+new Date().toLocaleTimeString('zh-CN')}catch(e){notice(e.message,true);$('resultRows').setAttribute('aria-busy','false');if(current==='overview'&&$('latestRows').querySelector('.loading'))$('latestRows').innerHTML='<div class="panel empty">数据读取失败，请点击“刷新数据”重试。</div>';$('updated').textContent='连接失败，10 秒后重试'}finally{refreshing.delete(current)}}

function selectTaskFilter(id,value){const el=$(id);if(value&&![...el.options].some(x=>x.value===value))el.add(new Option('当前任务',value));el.value=value;}
async function loadTaskChoices(){const d=await api('/api/serp/tasks?kind=manual&limit=100');for(const id of ['resultCampaign','whaleCampaign']){const el=$(id),selected=el.value;el.innerHTML='<option value="">全部任务</option>'+d.tasks.map(x=>`<option value="${esc(x.id)}">${esc(x.query)} · ${esc(states[x.status]||x.status)}</option>`).join('');selectTaskFilter(id,selected);}}
const regionNames={US:'美国',GB:'英国',SG:'新加坡',HK:'中国香港',TW:'中国台湾',JP:'日本',DE:'德国',FR:'法国',IN:'印度',AU:'澳大利亚',CA:'加拿大',BR:'巴西',KR:'韩国'};
const receiptStates={pending:'待上传',sending:'上传中',queued:'平台已入队',accepted:'接口已接收',duplicate:'重复链接',rejected:'平台拒收',failed:'上传失败'};
const requestStates={results:'采集成功',empty:'未找到结果',captcha:'需要验证码',http_error:'请求失败',timeout:'请求超时',error:'采集异常',rate_limited:'访问受限'};

const savedRecords=new Map(),savedContexts=new Map();
function sourceKey(source){return {google_web:'organic_results',google_news:'news_results',google_trends:'trending_searches'}[source];}
function rememberRecord(x){const key=x.id+':'+(x.ordinal||1)+':'+x.source;savedRecords.set(key,x);return key;}
function resultLink(a){return a.normalized_link||a.link;}
function linkHost(link){try{return new URL(link).hostname;}catch{return '未知';}}
function displayValue(v){if(v==null||v==='')return '未知';if(typeof v==='boolean')return v?'是':'否';if(Array.isArray(v))return v.length?v.map(displayValue).join('；'):'未提供';if(typeof v==='object')return Object.entries(v).map(([k,x])=>(fieldLabels[k]?.[0]||k)+'：'+displayValue(x)).join('；');return String(v);}
function renderResult(x){const a=x.data,tr=x.source==='google_trends',key=rememberRecord(x),link=resultLink(a);return `<article class="result ${x.source.replace('google_','')}"><div class="result-meta"><span class="badge">${names[x.source]}</span><span>采集于 ${time(x.completed_at)}</span><span>${tr?'当前热门话题':'关键词：'+esc(x.query)}</span>${a.is_new_url?'<span class="badge ok">历史首次发现</span>':''}</div>${tr?`<h3>${esc(a.keyword||'未知热门话题')}</h3><p>搜索量标记：${esc(a.approx_traffic??'未知')}</p><ul>${(a.articles||[]).map(b=>`<li><a href="${safeLink(resultLink(b))}" target="_blank" rel="noopener noreferrer">${esc(b.title||resultLink(b)||'未知标题')}</a></li>`).join('')||'<li>话题已保存，上游未提供相关报道。</li>'}</ul>`:`<h3><a href="${safeLink(link)}" target="_blank" rel="noopener noreferrer">${esc(a.title||link||'未知标题')}</a></h3><div class="help">${esc(link||'链接未知')}</div><p><strong class="field-caption">搜索摘要</strong>${esc(a.snippet??'上游未提供摘要')}</p><div class="record-facts"><span><small>链接所属网站</small>${esc(linkHost(link))}</span><span><small>${x.source==='google_web'?'自然排名':'新闻列表顺序'}</small>${esc(x.source==='google_web'?displayValue(a.position):displayValue(a.feed_position))}</span><span><small>发布时间</small>${esc(displayValue(a.date))}</span></div>`}<button class="mini secondary" data-saved="${esc(key)}">查看已保存数据</button></article>`;}
const fieldLabels={title:['标题','搜索结果或报道显示的标题。'],link:['结果链接','已保存的可访问网址。'],normalized_link:['去重后的链接','移除已知跟踪参数，用于识别历史重复链接。'],snippet:['搜索摘要','Google 搜索或新闻列表提供的摘要，不是文章全文。'],publisher:['媒体','上游明确提供的媒体名称；不会从网址猜测媒体。'],position:['自然排名','该搜索结果在本次 Google 自然结果列表中的位置，不是评分。'],rank_absolute:['上游绝对排名','上游实际提供的跨页排名。'],feed_position:['新闻列表顺序','RSS 返回的顺序，不代表 Google 搜索排名。'],page:['搜索页码','发现这条结果时访问的搜索页。'],date:['发布时间','上游提供的发布时间；不会从摘要日期推断。'],author:['作者','上游提供的作者。'],source:['上游来源标记','上游明确提供的来源字段。'],domain:['上游网站标记','原始上游字段，可能为 Google 跳转站点，不能当作媒体。'],displayed_link:['搜索页面显示的网址','搜索页面显示文本，可能包含 Google 跳转地址。'],redirect_link:['搜索跳转链接','搜索引擎返回的跳转入口，不是最终内容网址。'],link_status:['链接解析情况','标记是否仍是未解析的 Google 新闻跳转链接。'],thumbnail:['缩略图','上游提供的图片网址。'],favicon:['网站图标','上游提供的图标网址。'],sitelinks:['站内链接','上游返回的相关站内页面。'],snippet_highlighted_words:['摘要强调词','上游标记的摘要重点词。'],rich_snippet:['补充信息','上游返回的富摘要字段。'],cited_by:['引用标记','上游返回的引用信息。'],extracted_cited_by:['提取的引用标记','上游提供的提取信息。'],rating:['评分','上游提供的评分，非系统自行打分。'],reviews:['评价数量','上游提供的评价信息。'],extensions:['补充标签','上游提供的其他标签。'],cached_page_link:['缓存页面链接','上游提供的缓存页面。'],about_page_link:['关于页面链接','上游提供的相关页面。'],related_pages_link:['关联页面链接','上游提供的相关页面。'],keyword:['热门话题','地区当前热门话题，不等于用户关键词。'],approx_traffic:['搜索量标记','上游提供的近似量级，不是精确搜索次数。'],published_at:['话题发布时间','热门话题上游提供的时间。'],articles:['相关报道','该话题下已保存的报道标题、网址与媒体。'],is_new_url:['历史首次发现','发现时是否为历史 URL registry 中未收录的链接。'],upstream_position:['上游排名信息','搜索引擎实际提供的排名字段。'],type:['结果类型','已保存结果的来源类型。']};
function fieldTable(rows){return `<div class="table-scroll"><table class="saved-table"><thead><tr><th>字段</th><th>已保存的值</th><th>这个字段是什么</th></tr></thead><tbody>${rows.map(([name,value,help])=>`<tr><th scope="row">${esc(name)}</th><td>${esc(displayValue(value))}</td><td class="help">${esc(help)}</td></tr>`).join('')}</tbody></table></div>`;}
function savedFieldRows(x){const a=x.data,p=x.search_parameters||savedContexts.get(x.id)||{},tr=x.source==='google_trends';return [[tr?'热门话题':'标题',tr?a.keyword:a.title,tr?'该地区当前热搜话题。':'搜索结果显示的标题。'],['结果链接',resultLink(a),'最终保存的网址；热门话题的报道链接见下方。'],['搜索摘要',a.snippet,'搜索页面提供的摘要，不是下载的文章正文。'],['链接所属网站',tr?null:linkHost(resultLink(a)),'根据最终保存网址解析的主机名，不是媒体名称。'],['媒体',a.publisher,'只有上游明确提供时才显示媒体名称。'],[tr?'搜索量标记':x.source==='google_news'?'新闻列表顺序':'自然排名',tr?a.approx_traffic:x.source==='google_news'?a.feed_position:a.position,tr?'上游近似量级，不是精确次数。':x.source==='google_news'?'RSS 顺序，不代表 Google 搜索排名。':'本次查询的自然结果位置，不是相关性评分。'],['搜索关键词',tr?null:x.query,tr?'当前热门话题按地区采集，不使用用户关键词。':'创建任务时输入、实际用于搜索的文本。'],['搜索地区',regionNames[(p.gl||p.geo||'').toUpperCase()]||p.gl||p.geo,'发送给 Google 的搜索地区，不代表文章来自该国家。'],['搜索语言',{zh:'中文',en:'英语'}[p.hl]||p.hl,'发送给上游的语言偏好，不保证每条结果都使用该语言。'],['采集时间',x.completed_at,'这条结果保存对应的采集完成时间，不是发布时间。'],['发布时间',a.date||a.published_at,'上游明确提供的内容或话题发布时间。'],['搜索页码',a.page,'发现此结果时查询的页码。'],['历史首次发现',a.is_new_url,'发现时是否为历史中尚未收录的网址；重复结果也会保存。']];}
function renderSavedFields(x){if(!x)return;const a=x.data,base=new Set(['title','link','normalized_link','snippet','publisher','position','feed_position','page','date','keyword','approx_traffic','published_at','articles','is_new_url']);const extra=Object.entries(a).filter(([k,v])=>!base.has(k)&&v!=null&&v!=='').map(([k,v])=>[fieldLabels[k]?.[0]||'其他保存字段（'+k+'）',v,fieldLabels[k]?.[1]||'上游实际提供的补充数据。']);$('savedFields').innerHTML='<h3 style="margin-top:22px">这条记录的字段</h3>'+fieldTable(savedFieldRows(x))+(a.articles?.length?'<h3 style="margin-top:22px">已保存的相关报道</h3>'+fieldTable(a.articles.map(b=>[b.title||'标题未知',resultLink(b),b.publisher||'媒体未知'])):'')+(extra.length?'<details class="advanced"><summary>其他已保存的补充字段</summary>'+fieldTable(extra)+'</details>':'')+'<p class="help">“未知”表示上游未提供该字段。没有用标题、摘要或网址推断作者、媒体及发布时间。</p>';}
async function openSaved(keys){const records=keys.map(k=>savedRecords.get(k)).filter(Boolean);if(!records.length)return;$('savedRecordSelect').innerHTML=records.map(x=>`<option value="${esc(rememberRecord(x))}">${esc(x.data.title||x.data.keyword||'未知标题')}</option>`).join('');renderSavedFields(records[0]);$('savedDialog').showModal();const x=records[0];if(!x.search_parameters&&!savedContexts.has(x.id)){try{const response=await api('/api/serp/requests/'+x.id);savedContexts.set(x.id,response.search_parameters||{});renderSavedFields(savedRecords.get($('savedRecordSelect').value));}catch{notice('内容字段已显示，采集地区和语言暂时无法读取。',true);}}}
function downloadSavedRecord(){const x=savedRecords.get($('savedRecordSelect').value);if(!x)return;const csvCell=v=>{let t=displayValue(v);if(/^[=+@-]/.test(t.trimStart()))t="'"+t;return '"'+t.replace(/"/g,'""')+'"';};const rows=savedFieldRows(x),header=rows.map(r=>csvCell(r[0])).join(','),values=rows.map(r=>csvCell(r[1])).join(',');saveBlob(new Blob(['\uFEFF'+header+'\r\n'+values+'\r\n'],{type:'text/csv;charset=utf-8'}),'搜索结果.csv');}
let wizardStep=0;window.addEventListener('hashchange',()=>{if(location.hash==='#create'){setWizardStep(0);notice('');}});
function updateOutputGuide(){const selected=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>x.value);const descriptions={google_web:'网页：标题、链接、摘要与自然排名。',google_news:'新闻：标题、链接、媒体与发布时间；列表顺序不代表 Google 排名。',google_trends:'热门话题：当前热搜、搜索量标记及相关报道。不提供按关键词的历史热度曲线。'};const fields={google_web:['标题','链接','摘要','自然排名'],google_news:['标题','新闻链接','媒体','发布时间'],google_trends:['热门话题','搜索量标记','报道链接']};$('sourceDescription').textContent=selected.map(x=>descriptions[x]).join(' ')||'先选择至少一个数据源。';$('outputFields').innerHTML=[...new Set(selected.flatMap(x=>fields[x]))].map(x=>`<span>${esc(x)}</span>`).join('');$('keywords').disabled=selected.length===1&&selected[0]==='google_trends';$('keywords').placeholder=$('keywords').disabled?'当前热门话题按地区采集，无需关键词':'每行一个关键词，例如：\nsolar energy research\n人工智能政策';}
function validateScope(){const selected=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>x.value);if(!selected.length){notice('请选择至少一个数据源。',true);setWizardStep(0);return false;}if(selected.some(x=>x!=='google_trends')&&!$('keywords').value.trim()){notice('请输入至少一个关键词，每行一个。',true);setWizardStep(1);$('keywords').focus();return false;}return true;}
function setWizardStep(n){wizardStep=n;for(let i=0;i<3;i++){$('wizardStep'+i).hidden=i!==n;document.querySelectorAll('.stepper li')[i].setAttribute('aria-current',i===n?'step':'false');}$('wizardBack').hidden=n===0;$('wizardNext').hidden=n===2;$('wizardSubmit').hidden=n!==2;if(n===2){const selected=[...document.querySelectorAll('[name=taskSource]:checked')].map(x=>names[x.value]).join('、');const words=[...new Set($('keywords').value.split('\n').map(x=>x.trim()).filter(Boolean))];const mode=document.querySelector('[name=taskMode]:checked').value;$('taskReview').innerHTML=`<strong>数据源</strong>：${esc(selected)}<br><strong>关键词</strong>：${$('keywords').disabled?'无需关键词，按地区采集热门话题':esc(words.join('、'))}<br><strong>运行方式</strong>：${mode==='once'?'采集一次':'持续采集'}<br><strong>搜索范围</strong>：${esc($('taskEstimate').textContent)}<br><strong>地区与语言</strong>：${document.querySelector('[name=taskSource][value=google_web]').checked?`网页 ${esc(regionNames[$('webRegion').value]||$('webRegion').value)} / ${esc($('webLanguage').selectedOptions[0]?.textContent||'自动')} / ${esc($('taskPages').value)} 页；`:''}${document.querySelector('[name=taskSource][value=google_news]').checked?`新闻 ${esc($('newsLocale').value)}；`:''}${document.querySelector('[name=taskSource][value=google_trends]').checked?`热点 ${esc(regionNames[$('trendsGeo').value]||$('trendsGeo').value)}`:''}<br><strong>保存方式</strong>：自动保存到数据集，可导出 CSV / JSONL`;}}
function wizardAdvance(){if(wizardStep===0){if(!document.querySelector('[name=taskSource]:checked')){notice('请选择至少一个数据源。',true);return;}notice('');setWizardStep(1);}else if(validateScope()&&$('taskForm').reportValidity()){notice('');setWizardStep(2);}}
$('wizardNext').onclick=wizardAdvance;$('wizardBack').onclick=()=>setWizardStep(Math.max(0,wizardStep-1));
document.querySelectorAll('[data-template]').forEach(b=>b.onclick=()=>{const source={web:'google_web',news:'google_news',trends:'google_trends'}[b.dataset.template];document.querySelectorAll('[name=taskSource]').forEach(x=>x.checked=x.value===source);sourceFields();setWizardStep(1);});
sourceFields();setWizardStep(0);

window.addEventListener('hashchange',showView);showView();setInterval(refreshView,10000);
</script></body></html>'''


def page(existing_html: str = '') -> str:
    # `existing_html` is the legacy article page. The SERP workbench is a
    # standalone console and must not be spliced into that JavaScript.
    return PAGE
