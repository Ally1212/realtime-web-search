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
</style></head><body><main><header class="top"><div><h1 id="title">AI Google 数据采集</h1><div class="sub" id="campaignId">正在读取任务</div></div><div class="pill"><span class="dot" id="statusDot"></span><span id="runStatus">读取中</span></div></header><section class="hero"><div><div class="big"><span id="today">0</span><span class="unit">条</span></div><div class="caption" id="mainCaption">今天已经采集到的有效 Google 内容</div></div><div class="explain"><h2 id="plainStatus">正在检查</h2><p id="plainHelp">系统会自动沿着 AI 关键词持续搜索、抓取正文，并上传到 Whale。</p></div></section><section class="metrics"><div class="metric"><div class="label">最近速度</div><div class="value" id="speed">0 条/分钟</div></div><div class="metric"><div class="label">Google 发现量</div><div class="value" id="discovered">0</div></div><div class="metric"><div class="label">已上传 Whale</div><div class="value" id="uploaded">0</div></div><div class="metric"><div class="label">运行状态</div><div class="value" id="jobStatus">读取中</div></div></section><section class="section"><h2>采集来源</h2><div class="cards" id="sourceCards"><div class="card"><div class="card-head"><h3>Google 搜索</h3><span class="tag wait">读取中</span></div><p>正在读取采集数据。</p></div></div></section><section class="section"><h2>为什么会慢</h2><div class="cards" id="bottleneckCards"><div class="card"><div class="card-head"><h3>正在分析</h3><span class="tag wait">读取中</span></div><p>系统会自动判断是重复内容、站点限制、正文太短，还是搜索暂时没有新内容。</p></div></div></section><details class="details"><summary>查看技术详情</summary><div class="table-wrap"><table><thead><tr><th>采集器</th><th>健康状态</th><th>抓取失败</th><th>最近异常</th><th>索引数量</th></tr></thead><tbody><tr><td id="collector">—</td><td id="health">—</td><td id="failed">0</td><td id="anomaly">—</td><td id="indexed">0</td></tr></tbody></table></div><div class="table-wrap"><table><thead><tr><th>代理来源</th><th>可用节点</th><th>HTTP</th><th>SOCKS5</th><th>最近同步</th><th>状态</th></tr></thead><tbody id="proxyRows"><tr><td colspan="6">读取中</td></tr></tbody></table></div></details><div class="foot" id="updated">每 2 秒自动刷新</div></main><script>
const n=v=>Number(v||0),fmt=v=>n(v).toLocaleString(),statusText={active:'正在采集',paused:'已暂停',stopped:'已停止',failed:'异常'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago=value=>{if(!value)return'—';const sec=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return sec<60?`${sec} 秒前`:sec<3600?`${Math.floor(sec/60)} 分钟前`:`${Math.floor(sec/3600)} 小时前`};
const sourceName=value=>String(value||'').toLowerCase()==='google'?'Google 搜索':String(value||'Google 搜索');
function renderSourceCards(sources,j){if(!sources.length){return'<div class="card"><div class="card-head"><h3>Google 搜索</h3><span class="tag wait">等待数据</span></div><p>采集器正在搜索和抓取，拿到有效内容后这里会自动更新。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">0</div></div><div><div class="small-label">当前状态</div><div class="small-value">运行中</div></div></div></div>'}return sources.map(row=>`<div class="card"><div class="card-head"><h3>${esc(sourceName(row.source))}</h3><span class="tag ok">采集中</span></div><p>只采集 Google 发现的数据，系统会自动去重、抓取正文，并持续上传到 Whale。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">${fmt(row.today)}</div></div><div><div class="small-label">累计发现</div><div class="small-value">${fmt(j.discovered)}</div></div><div><div class="small-label">已抓正文</div><div class="small-value">${fmt(j.fetched)}</div></div><div><div class="small-label">关键词数</div><div class="small-value">${fmt(j.keyword_count||1)}</div></div></div></div>`).join('')}
const reasonText={already_processed:['重复旧链接','Google 经常返回之前见过的链接，系统已自动跳过并切换到新的日期范围。'],blocked_by_site_rules:['站点限制','部分请求被目标站拒绝，系统会更换代理后重试。'],short_content:['正文太短','系统会更换代理和提取方式重试一次。'],google_discovery_error:['搜索暂时受限','Google 超时或验证码会触发代理切换与退避。'],fetch_failed:['抓取失败','临时失败会保留重试资格，不再永久跳过。']};
function renderBottlenecks(j){const items=Object.entries(j.bottlenecks||{});if(!items.length){return'<div class="card"><div class="card-head"><h3>等待新内容</h3><span class="tag ok">正常</span></div><p>最近没有明显异常。速度为 0 时，通常是在等待 Google 返回新的可采集内容。</p></div>'}return items.slice(0,3).map(([key,count])=>{const info=reasonText[key]||['其他原因','系统会继续重试可恢复的采集任务。'];return`<div class="card"><div class="card-head"><h3>${info[0]}</h3><span class="tag wait">${fmt(count)} 次</span></div><p>${info[1]}</p></div>`}).join('')}
function ensureKeywordPanel(){if(document.getElementById('keywordCards'))return;const section=document.createElement('section');section.className='section';section.innerHTML='<h2>关键词池</h2><div class="cards" id="keywordCards"><div class="card"><p>正在读取关键词调度状态。</p></div></div><div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>搜索词</th><th>语言</th><th>分类</th><th>状态</th><th>评分</th><th>最近入库</th></tr></thead><tbody id="keywordRows"><tr><td colspan="6">读取中</td></tr></tbody></table></div>';document.querySelector('.details').before(section)}
function renderKeywords(pool){const s=pool.summary||{},cards=document.getElementById('keywordCards'),rows=document.getElementById('keywordRows');cards.innerHTML=[['基础搜索词',s.base||0,'中英双语分类词库'],['趋势搜索词',s.trend||0,`试采 ${fmt(s.probation||0)} 个`],['语言与冷却',`${fmt(s.zh||0)} 中 / ${fmt(s.en||0)} 英`,`冷却 ${fmt(s.cooldown||0)} 个`]].map(([name,value,help])=>`<div class="card"><div class="card-head"><h3>${name}</h3><span class="tag ok">${typeof value==='number'?fmt(value):esc(value)}</span></div><p>${help}</p></div>`).join('');rows.innerHTML=(pool.top||[]).slice(0,12).map(row=>`<tr><td>${esc(row.query)}</td><td>${row.language==='zh'?'中文':'英文'}</td><td>${esc(row.category)}</td><td>${esc(row.state)}</td><td>${Number(row.score||0).toFixed(1)}</td><td>${fmt(row.last_delivered)}</td></tr>`).join('')||'<tr><td colspan="6">等待第一轮调度</td></tr>'}
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
async function refreshRuntime(){try{const response=await fetch('/api/stats');if(!response.ok)return;const d=await response.json(),a=d.adaptive_concurrency||{},sources=d.discovery_health||[],runtime=(d.google_sources||[]).reduce((m,row)=>(m[row.source]=row,m),{}),web=runtime.google_web||{},ph=d.google_proxy_health||{},cards=document.getElementById('runtimeCards');if(!cards)return;const source=sources.reduce((m,row)=>(m[row.source]=row,m),{}),gs=source.google_search||{},gn=source.google_news||{},open=web.state==='circuit_open';cards.innerHTML=`<div class="card"><div class="card-head"><h3>采集并发</h3><span class="tag ok">${fmt(a.current_concurrency||0)} / ${fmt(a.max_concurrency||0)}</span></div><p>正文抓取、News 和上传并发；不再被 Google Web 验证码连带降速。</p><div class="split"><div><div class="small-label">上传成功率</div><div class="small-value">${(n(a.success_rate)*100).toFixed(1)}%</div></div><div><div class="small-label">Outbox</div><div class="small-value">${fmt(a.outbox_pending)}</div></div></div></div><div class="card"><div class="card-head"><h3>Google Web</h3><span class="tag ${open?'wait':'ok'}">${open?'已熔断':'运行中'}</span></div><p>${open?`暂停到 ${new Date(web.circuit_until).toLocaleTimeString()}，News 仍继续。`:'全局低速发现，第一页有新链接才请求第二页。'}</p><div class="split"><div><div class="small-label">当前 RPS</div><div class="small-value">${n(web.current_rps).toFixed(2)}</div></div><div><div class="small-label">验证码 / 请求</div><div class="small-value">${fmt(web.captcha_window)} / ${fmt(web.requests_window)}</div></div></div></div><div class="card"><div class="card-head"><h3>Google News / Trends</h3><span class="tag ${n(gn.limited)?'wait':'ok'}">持续运行</span></div><p>覆盖全球中英文区域，独立于 Web 熔断。</p><div class="split"><div><div class="small-label">近 5 分钟有效内容</div><div class="small-value">${fmt(gn.accepted)}</div></div><div><div class="small-label">健康 / 冷却代理</div><div class="small-value">${fmt(ph.healthy)} / ${fmt(ph.cooling)}</div></div></div></div>`}catch(e){}}
ensureRuntimePanel();refreshRuntime();setInterval(refreshRuntime,5000);
</script>'''
HTML = HTML.replace("</body>", RUNTIME_HTML + "</body>")


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
        if path not in {"/api/crawl", "/api/campaigns"}:
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
            daily_target = min(max(int(payload.get("daily_target", 50_000)), 1), 1_000_000)
            proxy_profile = str(payload.get("proxy_profile", self.config.default_proxy_profile))
            if proxy_profile not in {"private", "public", "direct"}:
                raise ValueError("proxy_profile 必须是 private、public 或 direct")
            campaign_id = self.store.create_campaign(query, aliases, daily_target, proxy_profile)
            self.queue.enqueue(campaign_id)
            self.send_json(
                {"job_id": campaign_id, "campaign_id": campaign_id, "status": "active"}, 202
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
            for field in ("base", "trend", "probation", "cooldown", "en", "zh"):
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
