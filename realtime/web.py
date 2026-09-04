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
from .search import SearchIndex
from .proxy_pool import ProxyCache


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Google 数据采集</title><style>
:root{color-scheme:light;--bg:#f4f6f8;--panel:#fff;--line:#d8dee6;--text:#171a20;--muted:#5f6b7a;--soft:#eef2f6;--green:#12805c;--green-bg:#e4f4ec;--yellow:#946200;--yellow-bg:#fff3c4;--red:#b42318;--red-bg:#ffe7e2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:18px 0 44px}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;padding-bottom:24px;border-bottom:1px solid var(--line)}h1{font-size:28px;line-height:1.15;margin:0 0 8px;font-weight:780}.sub{color:var(--muted);font-size:13px}.pill{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid #cbd3dc;border-radius:7px;padding:6px 10px;white-space:nowrap}.dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px var(--green-bg)}.dot.bad{background:var(--red);box-shadow:0 0 0 3px var(--red-bg)}.hero{display:grid;grid-template-columns:minmax(0,1fr) 438px;gap:34px;align-items:center;padding:30px 0 24px}.big{font-size:76px;line-height:.95;font-weight:820;letter-spacing:-1px}.unit{font-size:.58em;margin-left:8px}.caption{color:var(--muted);font-size:16px;margin-top:12px}.explain{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:28px 20px}.explain h2{font-size:20px;margin:0 0 6px}.explain p{margin:0;color:#344051;font-size:15px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:7px;background:var(--panel);overflow:hidden}.metric{padding:18px}.metric+.metric{border-left:1px solid var(--line)}.label{color:var(--muted);font-size:13px;margin-bottom:6px}.value{font-size:26px;line-height:1.15;font-weight:760}.section{margin-top:26px;padding-top:26px;border-top:1px solid var(--line)}.section h2{font-size:20px;margin:0 0 14px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:18px}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:14px}.card h3{font-size:17px;margin:0}.tag{border-radius:999px;padding:3px 9px;font-size:12px;white-space:nowrap}.tag.ok{background:var(--green-bg);color:var(--green)}.tag.wait{background:var(--yellow-bg);color:var(--yellow)}.card p{min-height:45px;margin:0 0 16px;color:#465365}.split{display:grid;grid-template-columns:1fr 1fr;gap:13px 20px}.small-label{color:var(--muted);font-size:12px}.small-value{font-size:19px;font-weight:760}.details{margin-top:28px;background:var(--panel);border:1px solid var(--line);border-radius:7px}.details summary{cursor:pointer;padding:15px 18px;font-weight:720}.table-wrap{overflow-x:auto;border-top:1px solid var(--line)}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:11px 14px}th{color:var(--muted);font-size:12px;background:#fafbfc}.ok-text{color:var(--green)}.bad-text{color:var(--red)}.foot{color:var(--muted);font-size:12px;margin-top:16px}@media(max-width:1160px){main{padding-left:20px;padding-right:20px}}@media(max-width:820px){.top,.hero{display:block}.pill{display:inline-flex;margin-top:14px}.hero{padding-top:24px}.explain{margin-top:20px}.big{font-size:54px}.metrics,.cards{grid-template-columns:1fr}.metric+.metric{border-left:0;border-top:1px solid var(--line)}}@media(max-width:420px){main{padding-left:14px;padding-right:14px}.big{font-size:44px}.unit{display:block;margin:8px 0 0}.metrics{border-radius:6px}}
</style></head><body><main><header class="top"><div><h1 id="title">AI Google 数据采集</h1><div class="sub" id="campaignId">正在读取任务</div></div><div class="pill"><span class="dot" id="statusDot"></span><span id="runStatus">读取中</span></div></header><section class="hero"><div><div class="big"><span id="today">0</span><span class="unit">条</span></div><div class="caption" id="mainCaption">今天已经采集到的有效 Google 内容</div></div><div class="explain"><h2 id="plainStatus">正在检查</h2><p id="plainHelp">系统会自动沿着 AI 关键词持续搜索、抓取正文，并上传到 Whale。</p></div></section><section class="metrics"><div class="metric"><div class="label">最近速度</div><div class="value" id="speed">0 条/分钟</div></div><div class="metric"><div class="label">总抓取次数</div><div class="value" id="discovered">0</div></div><div class="metric"><div class="label">重复内容</div><div class="value" id="duplicateRate">0%</div></div><div class="metric"><div class="label">运行状态</div><div class="value" id="jobStatus">读取中</div></div></section><section class="section"><h2>采集来源</h2><div class="cards" id="sourceCards"><div class="card"><div class="card-head"><h3>Google 搜索</h3><span class="tag wait">读取中</span></div><p>正在读取采集数据。</p></div></div></section><details class="details"><summary>查看技术详情</summary><div class="table-wrap"><table><thead><tr><th>采集器</th><th>健康状态</th><th>抓取失败</th><th>最近异常</th><th>索引数量</th></tr></thead><tbody><tr><td id="collector">—</td><td id="health">—</td><td id="failed">0</td><td id="anomaly">—</td><td id="indexed">0</td></tr></tbody></table></div><div class="table-wrap"><table><thead><tr><th>代理来源</th><th>可用节点</th><th>HTTP</th><th>SOCKS5</th><th>最近同步</th><th>状态</th></tr></thead><tbody id="proxyRows"><tr><td colspan="6">读取中</td></tr></tbody></table></div></details><div class="foot" id="updated">每 2 秒自动刷新</div></main><script>
const n=v=>Number(v||0),fmt=v=>n(v).toLocaleString(),statusText={active:'正在采集',paused:'已暂停',stopped:'已停止',failed:'异常'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago=value=>{if(!value)return'—';const sec=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return sec<60?`${sec} 秒前`:sec<3600?`${Math.floor(sec/60)} 分钟前`:`${Math.floor(sec/3600)} 小时前`};
const sourceName=value=>String(value||'').toLowerCase()==='google'?'Google 搜索':String(value||'Google 搜索');
function renderSourceCards(sources,j){if(!sources.length){return'<div class="card"><div class="card-head"><h3>Google 搜索</h3><span class="tag wait">等待数据</span></div><p>采集器正在搜索和抓取，拿到有效内容后这里会自动更新。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">0</div></div><div><div class="small-label">当前状态</div><div class="small-value">运行中</div></div></div></div>'}return sources.map(row=>`<div class="card"><div class="card-head"><h3>${esc(sourceName(row.source))}</h3><span class="tag ok">采集中</span></div><p>只采集 Google 发现的数据，系统会自动去重、抓取正文，并持续上传到 Whale。</p><div class="split"><div><div class="small-label">今日贡献</div><div class="small-value">${fmt(row.today)}</div></div><div><div class="small-label">累计发现</div><div class="small-value">${fmt(j.discovered)}</div></div><div><div class="small-label">已抓正文</div><div class="small-value">${fmt(j.fetched)}</div></div><div><div class="small-label">关键词数</div><div class="small-value">${fmt(j.keyword_count||1)}</div></div></div></div>`).join('')}
async function refresh(){try{const response=await fetch('/api/stats');if(!response.ok)throw Error(response.status);const d=await response.json(),j=d.continuous_job||(d.jobs||[])[0];if(!j){runStatus.textContent='空闲';statusDot.className='dot bad';plainStatus.textContent='还没有任务';plainHelp.textContent='启动采集器后，这里会显示采集进度。';return}const done=n(j.today),rate=n(j.rate_per_second),dupes=n(j.duplicates),continuous=!!j.continuous;title.textContent=continuous?'AI Google 数据采集':`${j.query} 数据采集`;campaignId.textContent=continuous?`长期任务 · ${fmt(j.keyword_count)} 个关键词 · 只采集 Google 来源`:j.id;runStatus.textContent=statusText[j.status]||j.status;statusDot.className=j.status==='active'?'dot':'dot bad';today.textContent=fmt(done);mainCaption.textContent='今天已经采集到的有效 Google 内容';plainStatus.textContent=j.status==='active'?'正在正常采集':j.status==='failed'?'采集异常':'当前没有持续采集';plainHelp.textContent=j.status==='active'?'Google 搜索结果会持续进入系统，去重后上传到 Whale。刷新页面不会影响后台采集。':'后台任务没有处于运行状态，需要查看技术详情。';discovered.textContent=fmt(j.discovered);duplicateRate.textContent=`${((dupes/Math.max(1,done+dupes))*100).toFixed(1)}%`;speed.textContent=`${(rate*60).toFixed(1)} 条/分钟`;jobStatus.textContent=statusText[j.status]||j.status;collector.textContent=continuous?'google-search':(j.proxy_profile==='private'?'private-proxy':j.proxy_profile);health.textContent=j.status==='failed'?'异常':'正常';health.className=j.status==='failed'?'bad-text':'ok-text';failed.textContent=fmt(j.failed);indexed.textContent=fmt(d.indexed_documents);const event=(d.events||[])[0];anomaly.textContent=event?`${event.error_code||event.status} · ${ago(event.created_at)}`:'—';const sources=continuous?(d.continuous_source_stats||[]):(d.source_stats||[]).filter(row=>String(row.campaign_id)===String(j.id));document.getElementById('sourceCards').innerHTML=renderSourceCards(sources,j);const pools=d.proxy_pools||{};proxyRows.innerHTML=['private','public'].map(name=>{const p=pools[name]||{},usable=!!p.usable;return`<tr><td>${name==='private'?'私有代理池':'公共代理池'}</td><td>${fmt(p.fresh)}</td><td>${fmt(p.http)}</td><td>${fmt(p.socks5)}</td><td>${ago(p.synced_at)}</td><td class="${usable?'ok-text':'bad-text'}">${usable?'正常':'不可用'}</td></tr>`}).join('');updated.textContent=`每 2 秒自动刷新 · 最近刷新 ${new Date().toLocaleTimeString()}`}catch(e){runStatus.textContent='离线';statusDot.className='dot bad';plainStatus.textContent='统计读取失败';plainHelp.textContent='后台可能还在运行，页面会继续自动重试。';updated.textContent='统计读取失败，正在重试'}}refresh();setInterval(refresh,2000);
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
        index = SearchIndex(self.config.opensearch_url, self.config.index_name, self.config.request_timeout)
        if parsed.path == "/api/stats":
            payload = self.store.stats()
            payload["jobs"] = payload["campaigns"]
            payload["indexed_documents"] = index.count()
            cache = ProxyCache(self.config.proxy_cache_dir)
            payload["proxy_pools"] = {
                profile: cache.stats(profile) for profile in ("private", "public")
            }
            self.send_json(payload)
            return
        if parsed.path == "/metrics":
            payload = self.store.stats()
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
            self.send_body(("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4")
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not query:
                self.send_json({"error": "q is required"}, 400)
                return
            self.send_json(index.search(query))
            return
        self.send_body(b"not found", "text/plain", 404)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve() -> None:
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8091"))
    print(f"Realtime web search listening on http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
