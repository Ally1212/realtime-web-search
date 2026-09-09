from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Config
from .campaign_queue import CampaignQueue
from .campaign_store import CampaignStore
from .proxy_pool import ProxyCache


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Google 数据采集</title><style>
:root{color-scheme:light;--bg:#f4f6f8;--panel:#fff;--line:#d8dee6;--text:#171a20;--muted:#5f6b7a;--soft:#eef2f6;--green:#12805c;--green-bg:#e4f4ec;--yellow:#946200;--yellow-bg:#fff3c4;--red:#b42318;--red-bg:#ffe7e2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:18px 0 44px}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;padding-bottom:24px;border-bottom:1px solid var(--line)}h1{font-size:28px;line-height:1.15;margin:0 0 8px;font-weight:780}.sub{color:var(--muted);font-size:13px}.pill{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid #cbd3dc;border-radius:7px;padding:6px 10px;white-space:nowrap}.dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px var(--green-bg)}.dot.bad{background:var(--red);box-shadow:0 0 0 3px var(--red-bg)}.hero{display:grid;grid-template-columns:minmax(0,1fr) 438px;gap:34px;align-items:center;padding:30px 0 24px}.big{font-size:76px;line-height:.95;font-weight:820;letter-spacing:-1px}.unit{font-size:.58em;margin-left:8px}.caption{color:var(--muted);font-size:16px;margin-top:12px}.explain{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:28px 20px}.explain h2{font-size:20px;margin:0 0 6px}.explain p{margin:0;color:#344051;font-size:15px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:7px;background:var(--panel);overflow:hidden}.metric{padding:18px}.metric+.metric{border-left:1px solid var(--line)}.label{color:var(--muted);font-size:13px;margin-bottom:6px}.value{font-size:26px;line-height:1.15;font-weight:760}.section{margin-top:26px;padding-top:26px;border-top:1px solid var(--line)}.section h2{font-size:20px;margin:0 0 14px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:18px}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:14px}.card h3{font-size:17px;margin:0}.tag{border-radius:999px;padding:3px 9px;font-size:12px;white-space:nowrap}.tag.ok{background:var(--green-bg);color:var(--green)}.tag.wait{background:var(--yellow-bg);color:var(--yellow)}.card p{min-height:45px;margin:0 0 16px;color:#465365}.split{display:grid;grid-template-columns:1fr 1fr;gap:13px 20px}.small-label{color:var(--muted);font-size:12px}.small-value{font-size:19px;font-weight:760}.details{margin-top:28px;background:var(--panel);border:1px solid var(--line);border-radius:7px}.details summary{cursor:pointer;padding:15px 18px;font-weight:720}.table-wrap{overflow-x:auto;border-top:1px solid var(--line)}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:11px 14px}th{color:var(--muted);font-size:12px;background:#fafbfc}.ok-text{color:var(--green)}.bad-text{color:var(--red)}.foot{color:var(--muted);font-size:12px;margin-top:16px}@media(max-width:1160px){main{padding-left:20px;padding-right:20px}}@media(max-width:820px){.top,.hero{display:block}.pill{display:inline-flex;margin-top:14px}.hero{padding-top:24px}.explain{margin-top:20px}.big{font-size:54px}.metrics,.cards{grid-template-columns:1fr}.metric+.metric{border-left:0;border-top:1px solid var(--line)}}@media(max-width:420px){main{padding-left:14px;padding-right:14px}.big{font-size:44px}.unit{display:block;margin:8px 0 0}.metrics{border-radius:6px}}
</style></head><body><main><header class="top"><div><h1 id="title">AI Google 数据采集</h1><div class="sub" id="campaignId">正在读取任务</div></div><div class="pill"><span class="dot" id="statusDot"></span><span id="runStatus">读取中</span></div></header><section class="hero"><div><div class="big"><span id="today">0</span><span class="unit">条</span></div><div class="caption" id="mainCaption">今天已经采集到的有效 Google 内容</div></div><div class="explain"><h2 id="plainStatus">正在检查</h2><p id="plainHelp">系统会自动沿着 AI 关键词持续搜索、抓取正文，并上传到 Whale。</p></div></section><section class="metrics"><div class="metric"><div class="label">最近速度</div><div class="value" id="speed">0 条/分钟</div></div><div class="metric"><div class="label">Google 发现量</div><div class="value" id="discovered">0</div></div><div class="metric"><div class="label">已上传 Whale</div><div class="value" id="uploaded">0</div></div><div class="metric"><div class="label">运行状态</div><div class="value" id="jobStatus">读取中</div></div></section><section class="section"><h2>采集来源</h2><div class="cards" id="sourceCards"><div class="card"><div class="card-head"><h3>Google Web 网页搜索</h3><span class="tag wait">读取中</span></div><p>正在读取采集数据。</p></div></div></section><section class="section"><h2>为什么会慢</h2><div class="cards" id="bottleneckCards"><div class="card"><div class="card-head"><h3>正在分析</h3><span class="tag wait">读取中</span></div><p>系统会自动判断是重复内容、站点限制、正文太短，还是搜索暂时没有新内容。</p></div></div></section><details class="details"><summary>查看技术详情</summary><div class="table-wrap"><table><thead><tr><th>采集器</th><th>健康状态</th><th>抓取失败</th><th>最近异常</th><th>索引数量</th></tr></thead><tbody><tr><td id="collector">—</td><td id="health">—</td><td id="failed">0</td><td id="anomaly">—</td><td id="indexed">0</td></tr></tbody></table></div><div class="table-wrap"><table><thead><tr><th>代理来源</th><th>可用节点</th><th>HTTP</th><th>SOCKS5</th><th>最近同步</th><th>状态</th></tr></thead><tbody id="proxyRows"><tr><td colspan="6">读取中</td></tr></tbody></table></div></details><div class="foot" id="updated">每 2 秒自动刷新</div></main><script>
const n=v=>Number(v||0),fmt=v=>n(v).toLocaleString(),statusText={active:'正在采集',paused:'已暂停',stopped:'已停止',failed:'异常'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago=value=>{if(!value)return'—';const sec=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return sec<60?`${sec} 秒前`:sec<3600?`${Math.floor(sec/60)} 分钟前`:`${Math.floor(sec/3600)} 小时前`};
const sourceName=value=>['google','google_web'].includes(String(value||'').toLowerCase())?'Google Web 网页搜索':String(value||'Google Web 网页搜索');
function renderSourceCards(sources,j){if(!sources.length){return'<div class="card"><div class="card-head"><h3>Google Web 网页搜索</h3><span class="tag wait">等待数据</span></div><p>采集器正在搜索和抓取，拿到有效内容后这里会自动更新。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">0</div></div><div><div class="small-label">当前状态</div><div class="small-value">运行中</div></div></div></div>'}return sources.map(row=>`<div class="card"><div class="card-head"><h3>${esc(sourceName(row.source))}</h3><span class="tag ok">采集中</span></div><p>只采集 Google Web 网页搜索发现的数据，系统会自动去重、抓取正文，并持续上传到 Whale。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">${fmt(row.today)}</div></div><div><div class="small-label">累计发现</div><div class="small-value">${fmt(j.discovered)}</div></div><div><div class="small-label">已抓正文</div><div class="small-value">${fmt(j.fetched)}</div></div><div><div class="small-label">关键词数</div><div class="small-value">${fmt(j.keyword_count||1)}</div></div></div></div>`).join('')}
const reasonText={already_processed:['重复旧链接','Google 经常返回之前见过的链接，系统已自动跳过并切换到新的日期范围。'],blocked_by_site_rules:['站点限制','部分请求被目标站拒绝，系统会更换代理后重试。'],short_content:['正文太短','系统会更换代理和提取方式重试一次。'],google_discovery_error:['搜索暂时受限','Google 超时或验证码会触发代理切换与退避。'],fetch_failed:['抓取失败','临时失败会保留重试资格，不再永久跳过。']};
function renderBottlenecks(j){const items=Object.entries(j.bottlenecks||{});if(!items.length){return'<div class="card"><div class="card-head"><h3>等待新内容</h3><span class="tag ok">正常</span></div><p>最近没有明显异常。速度为 0 时，通常是在等待 Google 返回新的可采集内容。</p></div>'}return items.slice(0,3).map(([key,count])=>{const info=reasonText[key]||['其他原因','系统会继续重试可恢复的采集任务。'];return`<div class="card"><div class="card-head"><h3>${info[0]}</h3><span class="tag wait">${fmt(count)} 次</span></div><p>${info[1]}</p></div>`}).join('')}
function ensureKeywordPanel(){if(document.getElementById('keywordCards'))return;const section=document.createElement('section');section.className='section';section.innerHTML='<h2>关键词池</h2><div class="cards" id="keywordCards"><div class="card"><p>正在读取关键词调度状态。</p></div></div><div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>搜索词</th><th>语言</th><th>分类</th><th>状态</th><th>评分</th><th>最近入库</th></tr></thead><tbody id="keywordRows"><tr><td colspan="6">读取中</td></tr></tbody></table></div>';document.querySelector('.details').before(section)}
function renderKeywords(pool){const s=pool.summary||{},cards=document.getElementById('keywordCards'),rows=document.getElementById('keywordRows');cards.innerHTML=[['基础搜索词',s.base||0,'中英双语分类词库'],['中文搜索词',s.zh||0,'仅通过 Google Web 搜索'],['英文与冷却',`${fmt(s.en||0)} 英`,`冷却 ${fmt(s.cooldown||0)} 个`]].map(([name,value,help])=>`<div class="card"><div class="card-head"><h3>${name}</h3><span class="tag ok">${typeof value==='number'?fmt(value):esc(value)}</span></div><p>${help}</p></div>`).join('');rows.innerHTML=(pool.top||[]).slice(0,12).map(row=>`<tr><td>${esc(row.query)}</td><td>${row.language==='zh'?'中文':'英文'}</td><td>${esc(row.category)}</td><td>${esc(row.state)}</td><td>${Number(row.score||0).toFixed(1)}</td><td>${fmt(row.last_delivered)}</td></tr>`).join('')||'<tr><td colspan="6">等待第一轮调度</td></tr>'}
async function refreshKeywords(){try{const response=await fetch('/api/stats');if(response.ok)renderKeywords((await response.json()).keyword_pool||{})}catch(e){}}
async function refresh(){try{const response=await fetch('/api/stats');if(!response.ok)throw Error(response.status);const d=await response.json(),j=d.continuous_job||(d.jobs||[])[0];if(!j){runStatus.textContent='空闲';statusDot.className='dot bad';plainStatus.textContent='还没有任务';plainHelp.textContent='启动采集器后，这里会显示采集进度。';return}const done=n(j.today),rate=n(j.rate_per_second),continuous=!!j.continuous;title.textContent=continuous?'AI Google 数据采集':`${j.query} 数据采集`;campaignId.textContent=continuous?`长期任务 · ${fmt(j.keyword_count)} 个关键词 · 今日总目标 ${fmt(j.daily_target)}`:j.id;runStatus.textContent=statusText[j.status]||j.status;statusDot.className=j.status==='active'?'dot':'dot bad';today.textContent=fmt(done);mainCaption.textContent='今天 Whale 已确认接收的唯一 Google 内容';plainStatus.textContent=j.status==='active'?'正在正常采集':j.status==='failed'?'采集异常':'当前没有持续采集';plainHelp.textContent=j.status==='active'?(rate>0?'系统正按日期切片持续发现并上传新内容。':'系统正在切换查询时间窗口或等待代理恢复。'):'后台任务没有处于运行状态，需要查看技术详情。';discovered.textContent=fmt(j.discovered);uploaded.textContent=fmt(j.whale_delivered_today||0);speed.textContent=`${(rate*60).toFixed(1)} 条/分钟`;jobStatus.textContent=statusText[j.status]||j.status;collector.textContent=continuous?'google-search':(j.proxy_profile==='private'?'private-proxy':j.proxy_profile);health.textContent=j.status==='failed'?'异常':'正常';health.className=j.status==='failed'?'bad-text':'ok-text';failed.textContent=fmt(j.failed);indexed.textContent=fmt(d.indexed_documents);const event=(d.events||[])[0];anomaly.textContent=event?`${event.error_code||event.status} · ${ago(event.created_at)}`:'—';const sources=continuous?(d.continuous_source_stats||[]):(d.source_stats||[]).filter(row=>String(row.campaign_id)===String(j.id));document.getElementById('sourceCards').innerHTML=renderSourceCards(sources,j);document.getElementById('bottleneckCards').innerHTML=renderBottlenecks(j);const pools=d.proxy_pools||{};proxyRows.innerHTML=['private','public'].map(name=>{const p=pools[name]||{},usable=!!p.usable;return`<tr><td>${name==='private'?'私有代理池':'公共代理池'}</td><td>${fmt(p.fresh)}</td><td>${fmt(p.http)}</td><td>${fmt(p.socks5)}</td><td>${ago(p.synced_at)}</td><td class="${usable?'ok-text':'bad-text'}">${usable?'正常':'不可用'}</td></tr>`}).join('');updated.textContent=`每 2 秒自动刷新 · 最近刷新 ${new Date().toLocaleTimeString()}`}catch(e){runStatus.textContent='离线';statusDot.className='dot bad';plainStatus.textContent='统计读取失败';plainHelp.textContent='后台可能还在运行，页面会继续自动重试。';updated.textContent='统计读取失败，正在重试'}}refresh();setInterval(refresh,2000);
ensureKeywordPanel();refreshKeywords();setInterval(refreshKeywords,10000);
</script></body></html>'''
HTML = HTML.replace("索引数量", "待上传 Outbox").replace(
    "fmt(d.indexed_documents)", "fmt(j.queue_depth||0)"
)
HTML = HTML.replace(
    "今日总目标 ${fmt(j.daily_target)}",
    "采集目标 ${j.daily_target>0?fmt(j.daily_target):'不限量'}",
)
HTML = HTML.replace(
    "runStatus.textContent=statusText[j.status]||j.status;statusDot.className=j.status==='active'?'dot':'dot bad';",
    "const externalWait=j.collector_state==='waiting_external';runStatus.textContent=externalWait?'等待 Whale':(statusText[j.status]||j.status);statusDot.className=j.status==='active'&&!externalWait?'dot':'dot bad';",
).replace(
    "plainStatus.textContent=j.status==='active'?'正在正常采集':j.status==='failed'?'采集异常':'当前没有持续采集';",
    "plainStatus.textContent=externalWait?'Whale 暂时不可用':j.status==='active'?'正在正常采集':j.status==='failed'?'采集异常':'当前没有持续采集';",
).replace(
    "jobStatus.textContent=statusText[j.status]||j.status;",
    "jobStatus.textContent=externalWait?'等待外部服务':(statusText[j.status]||j.status);",
).replace(
    "plainHelp.textContent=j.status==='active'?(rate>0?'系统正按日期切片持续发现并上传新内容。':'系统正在切换查询时间窗口或等待代理恢复。'):'后台任务没有处于运行状态，需要查看技术详情。';",
    "plainHelp.textContent=externalWait?'采集器仍在运行并自动重试，Whale 恢复后会自动继续。':j.status==='active'?(rate>0?'系统正按日期切片持续发现并上传新内容。':'系统正在切换查询时间窗口或等待代理恢复。'):'后台任务没有处于运行状态，需要查看技术详情。';",
)
RUNTIME_HTML = r'''<script>
const adaptiveReason={startup:'启动预热',healthy_window:'健康观察',healthy_scale_up:'健康升档',google_limited:'Google 限流降档',outbox_backpressure:'上传积压降档',whale_upload_errors:'Whale 上传异常',whale_register_unavailable:'等待 Whale 恢复连接',scale_down_cooldown:'降档冷却',quality_or_limit_guard:'质量或限流保护',adaptive_disabled:'固定并发'};
function ensureRuntimePanel(){if(document.getElementById('runtimeCards'))return;const section=document.createElement('section');section.className='section';section.innerHTML='<h2>放量控制</h2><div class="cards" id="runtimeCards"><div class="card"><p>正在读取自适应并发状态。</p></div></div>';document.querySelector('.details').before(section)}
function ensureFrontierPanel(){if(document.getElementById('frontierRows'))return;const section=document.createElement('section');section.className='section';section.innerHTML='<h2>Google 前 11 页进度</h2><div class="cards" id="frontierCards"><div class="card"><p>正在读取分页任务。</p></div></div><div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>搜索词</th><th>状态</th><th>页码进度</th><th>当前批次</th><th>每页候选 / 唯一 / 新链接</th></tr></thead><tbody id="frontierRows"><tr><td colspan="5">读取中</td></tr></tbody></table></div>';document.querySelector('.details').before(section)}
async function refreshRuntime(){try{const response=await fetch('/api/stats');if(!response.ok)return;const d=await response.json(),a=d.adaptive_concurrency||{},runtime=(d.google_sources||[]).reduce((m,row)=>(m[row.source]=row,m),{}),web=runtime.google_web||{},ph=d.google_proxy_health||{},frontier=d.google_page_frontier||{},fs=frontier.summary||{},tasks=frontier.tasks||[],cards=document.getElementById('runtimeCards');if(!cards)return;const open=web.state==='circuit_open';cards.innerHTML=`<div class="card"><div class="card-head"><h3>采集并发</h3><span class="tag ok">${fmt(a.current_concurrency||0)} / ${fmt(a.max_concurrency||0)}</span></div><p>正文抓取和上传并发；Google Web 使用独立限速。</p><div class="split"><div><div class="small-label">上传成功率</div><div class="small-value">${(n(a.success_rate)*100).toFixed(1)}%</div></div><div><div class="small-label">Outbox</div><div class="small-value">${fmt(a.outbox_pending)}</div></div></div></div><div class="card"><div class="card-head"><h3>Google Web</h3><span class="tag ${open?'wait':'ok'}">${open?'已熔断':'运行中'}</span></div><p>${open?`暂停到 ${new Date(web.circuit_until).toLocaleTimeString()}，分页任务保留等待恢复。`:'每批抓取 2～3 页，固定覆盖前 11 页。'}</p><div class="split"><div><div class="small-label">当前 RPS</div><div class="small-value">${n(web.current_rps).toFixed(2)}</div></div><div><div class="small-label">验证码 / 请求</div><div class="small-value">${fmt(web.captcha_window)} / ${fmt(web.requests_window)}</div></div></div></div><div class="card"><div class="card-head"><h3>Google Web 代理</h3><span class="tag ${n(ph.cooling)?'wait':'ok'}">${n(ph.cooling)?'部分冷却':'正常'}</span></div><p>只用于 Google Web，请求受 Session 间隔与 CAPTCHA 冷却保护。</p><div class="split"><div><div class="small-label">健康代理</div><div class="small-value">${fmt(ph.healthy)}</div></div><div><div class="small-label">冷却代理</div><div class="small-value">${fmt(ph.cooling)}</div></div></div></div>`;const fc=document.getElementById('frontierCards'),fr=document.getElementById('frontierRows');if(fc)fc.innerHTML=`<div class="card"><div class="card-head"><h3>页面覆盖</h3><span class="tag ok">${fmt(fs.covered_pages)} / ${fmt(fs.total_pages)}</span></div><p>覆盖页数与最终有效内容数是两个不同指标。</p></div><div class="card"><div class="card-head"><h3>排队状态</h3><span class="tag ok">${fmt(fs.queries)} 个查询</span></div><p>等待 ${fmt(fs.pending)} · 运行 ${fmt(fs.running)} · 完成 ${fmt(fs.completed)}</p></div><div class="card"><div class="card-head"><h3>限流恢复</h3><span class="tag ${n(fs.cooling)+n(fs.failed)?'wait':'ok'}">${fmt(n(fs.cooling)+n(fs.failed))}</span></div><p>冷却任务保留失败页，恢复后从该页继续。</p></div>`;if(fr)fr.innerHTML=tasks.map(row=>{const stats=Object.entries(row.page_stats||{}).sort((x,y)=>n(x[0])-n(y[0])).map(([page,s])=>`${page}: ${fmt(s.candidates)}/${fmt(s.unique_urls)}/${fmt(s.novel_urls)}${s.captcha?' CAPTCHA':''}`).join(' · ')||'—';return`<tr><td>${esc(row.query)}</td><td>${esc(row.state)}</td><td>${fmt(Math.min(n(row.next_page)-1,n(row.max_page)))} / ${fmt(row.max_page)}</td><td>${row.batch_start?`${fmt(row.batch_start)}–${fmt(row.batch_end)}`:'—'}</td><td>${esc(stats)}</td></tr>`}).join('')||'<tr><td colspan="5">尚无分页任务</td></tr>'}catch(e){}}
ensureRuntimePanel();ensureFrontierPanel();refreshRuntime();setInterval(refreshRuntime,5000);
</script>'''
HTML = HTML.replace("</body>", RUNTIME_HTML + "</body>")

HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Google Web 本地采集</title><style>
:root{color-scheme:light;--bg:#f4f6f8;--panel:#fff;--line:#d8dee6;--text:#171a20;--muted:#5f6b7a;--green:#12805c;--green-bg:#e4f4ec;--yellow:#946200;--yellow-bg:#fff3c4;--red:#b42318;--red-bg:#ffe7e2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1180px;margin:auto;padding:24px 20px 48px}h1{font-size:28px;margin:0 0 6px}.sub,.hint{color:var(--muted)}.search{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:10px;margin:24px 0 12px}.search input,.search button,select,.action{font:inherit;border:1px solid var(--line);border-radius:8px;padding:12px 14px}.search input{background:#fff;font-size:16px}.search button,.action{border:0;background:#1769e0;color:#fff;font-weight:700;cursor:pointer}.search button:disabled,.action:disabled{opacity:.5;cursor:not-allowed}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}.toolbar select{min-width:280px;background:#fff}.action{padding:8px 12px}.action.secondary{background:#5f6b7a}.notice{min-height:24px;color:var(--muted)}.notice.error{color:var(--red)}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:20px 0}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px}.card{padding:16px}.label{color:var(--muted);font-size:12px}.value{font-size:25px;font-weight:760;margin-top:4px}.tag{display:inline-block;border-radius:999px;padding:3px 9px;font-size:12px;background:var(--green-bg);color:var(--green)}.tag.wait{background:var(--yellow-bg);color:var(--yellow)}.tag.bad{background:var(--red-bg);color:var(--red)}.panel{margin-top:18px;overflow:hidden}.panel h2{font-size:18px;margin:0;padding:15px 16px;border-bottom:1px solid var(--line)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:900px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:11px 13px}th{background:#fafbfc;color:var(--muted);font-size:12px}td a{color:#1769e0;text-decoration:none}.preview{max-width:520px;color:#465365;white-space:normal}.empty{text-align:center;color:var(--muted);padding:30px}.foot{margin-top:16px;color:var(--muted);font-size:12px}@media(max-width:850px){.cards{grid-template-columns:repeat(2,1fr)}.search{grid-template-columns:1fr}.toolbar select{width:100%}}@media(max-width:480px){main{padding:16px 12px}.cards{grid-template-columns:1fr}}
</style></head><body><main><header><h1>Google Web 本地采集</h1><div class="sub">输入一个关键词，固定覆盖 Google Web 前 11 页，抓取结果仅保存在本地，不上传 Whale。</div></header>
<form class="search" id="searchForm"><input id="queryInput" maxlength="300" autocomplete="off" placeholder="例如：AI Agent 开源项目" required><button id="startButton" type="submit">开始尽可能采集</button></form><div class="notice" id="notice">采集会持续去重；Google 限流或验证码时会保留页码并自动重试。</div>
<div class="toolbar"><select id="campaignSelect" aria-label="本地采集任务"><option value="">尚无本地任务</option></select><button class="action secondary" id="pauseButton" type="button">暂停</button><button class="action" id="resumeButton" type="button">继续</button><span class="tag wait" id="statusTag">空闲</span></div>
<section class="cards"><div class="card"><div class="label">Google 候选</div><div class="value" id="discoveredValue">0</div></div><div class="card"><div class="label">已抓取</div><div class="value" id="fetchedValue">0</div></div><div class="card"><div class="label">本地保存</div><div class="value" id="savedValue">0</div></div><div class="card"><div class="label">失败 / 过滤</div><div class="value" id="failedValue">0</div></div><div class="card"><div class="label">Whale 上传</div><div class="value">0</div><div class="hint">已禁用</div></div></section>
<section class="panel"><h2 id="resultTitle">采集结果</h2><div class="table-wrap"><table><thead><tr><th>标题 / URL</th><th>正文预览</th><th>语言</th><th>抓取时间</th></tr></thead><tbody id="resultRows"><tr><td colspan="4" class="empty">输入关键词后，结果会在这里自动刷新。</td></tr></tbody></table></div></section>
<section class="panel"><h2>Google 前 11 页进度</h2><div class="cards" style="padding:14px;margin:0"><div class="card"><div class="label">已覆盖页数</div><div class="value" id="coveredValue">0 / 11</div></div><div class="card"><div class="label">分页状态</div><div class="value" id="frontierValue">等待</div></div></div></section><div class="foot" id="updated">每 2 秒自动刷新</div>
</main><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>Number(v||0).toLocaleString();let currentId=localStorage.getItem('localCampaignId')||'';
function setNotice(text,error=false){notice.textContent=text;notice.className=error?'notice error':'notice'}
function statusText(status){return({active:'正在采集',paused:'已暂停',stopped:'已停止',failed:'采集异常'})[status]||'空闲'}
async function loadCampaigns(){try{const response=await fetch('/api/local-campaigns');if(!response.ok)return;const data=await response.json(),rows=data.campaigns||[];if(!currentId&&rows.length){currentId=String(rows[0].id);localStorage.setItem('localCampaignId',currentId)}campaignSelect.innerHTML=rows.map(row=>`<option value="${esc(row.id)}" ${String(row.id)===currentId?'selected':''}>${esc(row.query)} · ${statusText(row.status)} · ${fmt(row.saved_count)} 条</option>`).join('')||'<option value="">尚无本地任务</option>'}catch(e){setNotice('本地任务列表读取失败',true)}}
async function refreshCurrent(){if(!currentId)return;try{const response=await fetch(`/api/local-campaigns/${currentId}?limit=200`);if(response.status===404){currentId='';localStorage.removeItem('localCampaignId');await loadCampaigns();return}if(!response.ok)throw Error(response.status);const d=await response.json(),pages=d.pages||[],f=d.frontier||{};discoveredValue.textContent=fmt(d.discovered);fetchedValue.textContent=fmt(d.fetched);savedValue.textContent=fmt(d.saved_count);failedValue.textContent=`${fmt(d.failed)} / ${fmt(d.irrelevant)}`;statusTag.textContent=statusText(d.status);statusTag.className=`tag ${d.status==='active'?'':'wait'}`;pauseButton.disabled=d.status!=='active';resumeButton.disabled=d.status==='active';resultTitle.textContent=`采集结果：${d.query} （最新 ${pages.length} 条）`;resultRows.innerHTML=pages.map(row=>`<tr><td><a href="${esc(row.url)}" target="_blank" rel="noopener noreferrer">${esc(row.title||row.url)}</a><div class="hint">${esc(row.url)}</div></td><td class="preview">${esc(row.preview||'—')}</td><td>${esc(row.language||'—')}</td><td>${esc(row.fetched_at||'—')}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">任务已入队，正在等待 Google 返回结果。</td></tr>';coveredValue.textContent=`${fmt(f.covered_pages)} / ${fmt(f.total_pages||11)}`;frontierValue.textContent=f.waiting?'冷却重试':f.running?'正在请求':f.pending?'下一批已排队':f.completed?'本轮完成':'等待';updated.textContent=`每 2 秒自动刷新 · ${new Date().toLocaleTimeString()}`}catch(e){setNotice('任务详情读取失败，页面会自动重试',true)}}
searchForm.addEventListener('submit',async event=>{event.preventDefault();const query=queryInput.value.trim();if(!query)return;startButton.disabled=true;setNotice('正在创建本地采集任务…');try{const response=await fetch('/api/local-campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})}),data=await response.json();if(!response.ok)throw Error(data.error||response.status);currentId=String(data.campaign_id);localStorage.setItem('localCampaignId',currentId);queryInput.value='';setNotice('任务已启动：只保存本地，Whale 上传为 0。');await loadCampaigns();await refreshCurrent()}catch(e){setNotice(`创建失败：${e.message}`,true)}finally{startButton.disabled=false}});
campaignSelect.addEventListener('change',async()=>{currentId=campaignSelect.value;if(currentId)localStorage.setItem('localCampaignId',currentId);await refreshCurrent()});
async function action(name){if(!currentId)return;const response=await fetch(`/api/campaigns/${currentId}/${name}`,{method:'POST'});if(response.ok){await loadCampaigns();await refreshCurrent()}}
pauseButton.addEventListener('click',()=>action('pause'));resumeButton.addEventListener('click',()=>action('resume'));
loadCampaigns().then(refreshCurrent);setInterval(refreshCurrent,2000);setInterval(loadCampaigns,10000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    config = Config()
    store = CampaignStore(config.database_url)
    queue = CampaignQueue(config.valkey_url)

    def send_body(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        self.send_body(
            json.dumps(payload, ensure_ascii=False, default=str).encode(),
            "application/json; charset=utf-8", status,
        )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        action = re.fullmatch(r"/api/campaigns/([0-9a-f-]+)/(pause|resume|stop)", path)
        if action:
            campaign_id, operation = action.groups()
            status = {"pause": "paused", "resume": "active", "stop": "stopped"}[operation]
            if not self.store.set_status(campaign_id, status):
                self.send_json({"error": "campaign not found"}, 404)
                return
            if status == "active":
                self.queue.enqueue(campaign_id)
            self.send_json({"campaign_id": campaign_id, "status": status})
            return
        local_mode = path == "/api/local-campaigns"
        if path not in {"/api/crawl", "/api/campaigns", "/api/local-campaigns"}:
            self.send_body(b"not found", "text/plain", 404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16_384)
            payload = json.loads(self.rfile.read(length))
            query = str(payload.get("query", "")).strip()
            if not query or len(query) > 300:
                raise ValueError("关键词不能为空且不能超过 300 字符")
            aliases_value = payload.get("aliases", [])
            if not isinstance(aliases_value, list):
                raise ValueError("aliases 必须是字符串数组")
            aliases = [str(value).strip() for value in aliases_value if str(value).strip()][:20]
            daily_target = (
                1_000_000 if local_mode
                else min(max(int(payload.get("daily_target", 50_000)), 1), 1_000_000)
            )
            proxy_profile = str(payload.get("proxy_profile", self.config.default_proxy_profile))
            if proxy_profile not in {"private", "public", "direct"}:
                raise ValueError("proxy_profile 必须是 private、public 或 direct")
            campaign_id = self.store.create_campaign(query, aliases, daily_target, proxy_profile)
            self.queue.enqueue(campaign_id)
            self.send_json(
                {
                    "job_id": campaign_id,
                    "campaign_id": campaign_id,
                    "status": "active",
                    "upload_to_whale": False,
                },
                202,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_body(HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/healthz":
            self.send_body(b"ok", "text/plain")
            return
        if parsed.path == "/favicon.ico":
            self.send_body(b"", "image/x-icon")
            return
        if parsed.path == "/api/local-campaigns":
            self.send_json({"campaigns": self.store.local_campaigns()})
            return
        local_campaign = re.fullmatch(
            r"/api/local-campaigns/([0-9a-f-]+)", parsed.path
        )
        if local_campaign:
            values = parse_qs(parsed.query)
            try:
                limit = int((values.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            detail = self.store.local_campaign_detail(local_campaign.group(1), limit)
            if not detail:
                self.send_json({"error": "local campaign not found"}, 404)
                return
            self.send_json(detail)
            return
        if parsed.path == "/api/stats":
            payload = self.store.stats(
                self.config.continuous_daily_target,
                self.config.continuous_proxy_profile,
            )
            payload["jobs"] = payload["campaigns"]
            payload["indexed_documents"] = None
            payload["local_search_enabled"] = False
            cache = ProxyCache(self.config.proxy_cache_dir)
            payload["proxy_pools"] = {
                profile: cache.stats(profile) for profile in ("private", "public")
            }
            self.send_json(payload)
            return
        if parsed.path == "/metrics":
            payload = self.store.stats(
                self.config.continuous_daily_target,
                self.config.continuous_proxy_profile,
            )
            lines = [
                "# HELP realtime_campaign_today Unique relevant pages associated today.",
                "# TYPE realtime_campaign_today gauge",
                "# HELP realtime_campaign_rate_per_second Unique relevant pages per second over the recent window.",
                "# TYPE realtime_campaign_rate_per_second gauge",
                "# HELP realtime_campaign_projected_daily Projected unique relevant pages per day.",
                "# TYPE realtime_campaign_projected_daily gauge",
            ]
            for field in ("discovered", "fetched", "failed", "duplicates", "irrelevant"):
                lines.extend([
                    f"# HELP realtime_campaign_{field}_total Campaign {field} total.",
                    f"# TYPE realtime_campaign_{field}_total counter",
                ])
            for campaign in payload["campaigns"]:
                campaign_id = str(campaign["id"])
                lines.append(f'realtime_campaign_today{{campaign_id="{campaign_id}"}} {campaign["today"]}')
                lines.append(
                    f'realtime_campaign_rate_per_second{{campaign_id="{campaign_id}"}} '
                    f'{campaign["rate_per_second"]}'
                )
                lines.append(
                    f'realtime_campaign_projected_daily{{campaign_id="{campaign_id}"}} '
                    f'{campaign["projected_daily"]}'
                )
                for field in ("discovered", "fetched", "failed", "duplicates", "irrelevant"):
                    lines.append(
                        f'realtime_campaign_{field}_total{{campaign_id="{campaign_id}"}} '
                        f'{campaign[field]}'
                    )
            continuous = payload.get("continuous_job") or {}
            if continuous:
                lines.extend([
                    f'realtime_continuous_whale_delivered_today {continuous.get("whale_delivered_today", 0)}',
                    f'realtime_continuous_required_rate {continuous.get("required_rate", 0)}',
                    f'realtime_continuous_projected_daily {continuous.get("projected_daily", 0)}',
                    f'realtime_continuous_daily_target {continuous.get("daily_target", 0)}',
                    f'realtime_continuous_novelty_ratio {continuous.get("novelty_ratio", 0)}',
                    f'realtime_continuous_fetch_success_rate {continuous.get("fetch_success_rate", 0)}',
                    f'realtime_continuous_queue_depth {continuous.get("queue_depth", 0)}',
                ])
            keyword_summary = (payload.get("keyword_pool") or {}).get("summary") or {}
            for field in ("base", "cooldown", "en", "zh"):
                lines.append(
                    f'realtime_continuous_keywords{{kind="{field}"}} '
                    f'{keyword_summary.get(field, 0)}'
                )
            browser = payload.get("browser_fallback") or {}
            lines.append(
                f'realtime_browser_fallback_attempts_hour {browser.get("attempts_hour", 0)}'
            )
            adaptive = payload.get("adaptive_concurrency") or {}
            lines.extend([
                f'realtime_adaptive_concurrency_current {adaptive.get("current_concurrency", 0)}',
                f'realtime_adaptive_concurrency_max {adaptive.get("max_concurrency", 0)}',
                f'realtime_adaptive_limited_ratio {adaptive.get("limited_ratio", 0)}',
                f'realtime_adaptive_success_rate {adaptive.get("success_rate", 0)}',
            ])
            for source in payload.get("discovery_health") or []:
                name = re.sub(r"[^a-zA-Z0-9_]", "_", str(source.get("source") or "unknown"))
                lines.append(f'realtime_discovery_accepted_5m{{source="{name}"}} {source.get("accepted", 0)}')
                lines.append(f'realtime_discovery_errors_5m{{source="{name}"}} {source.get("errors", 0)}')
                lines.append(f'realtime_discovery_limited_5m{{source="{name}"}} {source.get("limited", 0)}')
            for proxy in payload.get("proxy_utilization") or []:
                profile = re.sub(r"[^a-zA-Z0-9_]", "_", str(proxy.get("profile") or "unknown"))
                lines.append(f'realtime_proxy_active_5m{{profile="{profile}"}} {proxy.get("active", 0)}')
            for source in payload.get("google_sources") or []:
                name = re.sub(r"[^a-zA-Z0-9_]", "_", str(source.get("source") or "unknown"))
                lines.append(f'realtime_google_source_rps{{source="{name}"}} {source.get("current_rps", 0)}')
                lines.append(f'realtime_google_source_requests_total{{source="{name}"}} {source.get("requests_total", 0)}')
                lines.append(f'realtime_google_source_captcha_total{{source="{name}"}} {source.get("captcha_total", 0)}')
                lines.append(f'realtime_google_source_results_total{{source="{name}"}} {source.get("result_count", 0)}')
                lines.append(f'realtime_google_source_novel_total{{source="{name}"}} {source.get("novel_count", 0)}')
                lines.append(f'realtime_google_source_circuit_open{{source="{name}"}} {int(source.get("state") == "circuit_open")}')
            google_proxy = payload.get("google_proxy_health") or {}
            for field in ("total", "healthy", "cooling", "active"):
                lines.append(f'realtime_google_proxy_sessions{{state="{field}"}} {google_proxy.get(field, 0)}')
            frontier = (payload.get("google_page_frontier") or {}).get("summary") or {}
            lines.append(
                f'realtime_google_frontier_pages_covered {frontier.get("covered_pages", 0)}'
            )
            lines.append(
                f'realtime_google_frontier_pages_total {frontier.get("total_pages", 0)}'
            )
            for state in ("pending", "running", "cooling", "failed", "completed"):
                lines.append(
                    f'realtime_google_frontier_queries{{state="{state}"}} {frontier.get(state, 0)}'
                )
            for stage in payload.get("stage_metrics") or []:
                name = re.sub(r"[^a-zA-Z0-9_]", "_", str(stage.get("stage") or "unknown"))
                observations = int(stage.get("observations") or 0)
                average = float(stage.get("total_seconds") or 0) / max(observations, 1)
                lines.append(f'realtime_stage_duration_seconds_avg{{stage="{name}"}} {average}')
                lines.append(f'realtime_stage_observations_total{{stage="{name}"}} {observations}')
            self.send_body(("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4")
            return
        if parsed.path == "/api/search":
            self.send_json({"error": "local_search_disabled", "storage": "whale"}, 410)
            return
        self.send_body(b"not found", "text/plain", 404)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve() -> None:
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8091"))
    print(f"Realtime web search listening on http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
