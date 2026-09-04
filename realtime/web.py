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


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>网页采集进度</title><style>
:root{color-scheme:light;--bg:#f5f6f7;--line:#d9dde2;--text:#17191d;--muted:#626b78;--green:#16895d;--red:#b42318}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:18px 0 42px}.top{display:flex;justify-content:space-between;align-items:flex-start;padding:0 0 22px;border-bottom:1px solid var(--line)}h1{font-size:27px;line-height:1.2;margin:0 0 7px;letter-spacing:-.4px}.id{color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.pill{display:flex;align-items:center;gap:7px;background:#fff;border:1px solid #cfd4da;border-radius:9px;padding:7px 11px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px #dcefe7}.dot.bad{background:var(--red);box-shadow:0 0 0 3px #fbe4e1}.progress-head{display:flex;justify-content:space-between;align-items:end;margin:34px 0 10px}.count{font-size:50px;line-height:1;font-weight:760;letter-spacing:-1px}.percent{color:#495260;font-size:18px}.bar{height:16px;background:#dfe2e6;overflow:hidden;border-radius:3px}.bar>span{display:block;height:100%;width:0;background:var(--green);transition:width .35s ease}.metrics{display:grid;grid-template-columns:repeat(4,1fr);margin-top:32px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.metric{padding:24px 20px 25px 0}.metric+.metric{border-left:1px solid var(--line);padding-left:20px}.label{color:var(--muted);font-size:13px;margin-bottom:5px}.value{font-size:26px;font-weight:700}section{margin-top:30px}h2{font-size:17px;margin:0 0 12px}.table-wrap{overflow-x:auto;border:1px solid var(--line);background:#fff}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:12px 14px}th{color:var(--muted);font-size:12px;font-weight:600;background:#fafbfc}tr:last-child td{border-bottom:0}.ok{color:var(--green)}.bad-text{color:var(--red)}.empty{color:var(--muted);text-align:center}.foot{color:var(--muted);font-size:12px;margin-top:14px}@media(max-width:1160px){main{padding-left:20px;padding-right:20px}}@media(max-width:700px){h1{font-size:22px}.count{font-size:36px}.metrics{grid-template-columns:repeat(2,1fr)}.metric:nth-child(3){border-left:0;border-top:1px solid var(--line)}.metric:nth-child(4){border-top:1px solid var(--line)}.pill{padding:6px 8px}}
</style></head><body><main><header class="top"><div><h1 id="title">网页采集进度</h1><div class="id" id="campaignId">等待任务</div></div><div class="pill"><span class="dot" id="statusDot"></span><span id="runStatus">loading</span></div></header><div class="progress-head"><div class="count"><span id="today">0</span> / <span id="target">50,000</span></div><div class="percent" id="percent">0%</div></div><div class="bar"><span id="progress"></span></div><div class="metrics"><div class="metric"><div class="label">发现记录</div><div class="value" id="discovered">0</div></div><div class="metric"><div class="label">重复率</div><div class="value" id="duplicateRate">0%</div></div><div class="metric"><div class="label">最近速度</div><div class="value" id="speed">0 条/分钟</div></div><div class="metric"><div class="label">预计剩余</div><div class="value" id="remaining">—</div></div></div><section><h2>采集器状态</h2><div class="table-wrap"><table><thead><tr><th>采集器</th><th>任务状态</th><th>健康状态</th><th>抓取失败</th><th>最近异常</th></tr></thead><tbody><tr><td id="collector">—</td><td id="jobStatus">—</td><td id="health">—</td><td id="failed">0</td><td id="anomaly">—</td></tr></tbody></table></div></section><section><h2>来源产出</h2><div class="table-wrap"><table><thead><tr><th>发现来源</th><th>今日有效唯一页面</th></tr></thead><tbody id="sourceRows"><tr><td colspan="2" class="empty">暂无数据</td></tr></tbody></table></div></section><section><h2>代理来源状态</h2><div class="table-wrap"><table><thead><tr><th>来源</th><th>可用节点</th><th>HTTP</th><th>SOCKS5</th><th>最近同步</th><th>状态</th></tr></thead><tbody id="proxyRows"><tr><td colspan="6" class="empty">读取中…</td></tr></tbody></table></div><div class="foot" id="updated">每 2 秒自动刷新</div></section></main><script>
const n=v=>Number(v||0),fmt=v=>n(v).toLocaleString(),statusText={active:'running',paused:'paused',stopped:'stopped',failed:'failed'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago=value=>{if(!value)return'—';const sec=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return sec<60?`${sec} 秒前`:sec<3600?`${Math.floor(sec/60)} 分钟前`:`${Math.floor(sec/3600)} 小时前`};
const eta=(left,rate)=>{if(!rate)return'—';const sec=left/rate;if(sec<3600)return`${Math.ceil(sec/60)} 分钟`;if(sec<86400)return`${(sec/3600).toFixed(1)} 小时`;return`${Math.ceil(sec/86400)} 天`};
async function refresh(){try{const response=await fetch('/api/stats');if(!response.ok)throw Error(response.status);const d=await response.json(),j=d.continuous_job||(d.jobs||[])[0];if(!j){runStatus.textContent='idle';statusDot.className='dot bad';return}const done=n(j.today),goal=n(j.daily_target),continuous=!!j.continuous,pct=continuous?100:(goal?Math.min(100,done/goal*100):0),rate=n(j.rate_per_second),dupes=n(j.duplicates);title.textContent=`${j.query} 采集进度`;campaignId.textContent=continuous?`${j.id} · ${fmt(j.keyword_count)} 个关键词`:j.id;runStatus.textContent=statusText[j.status]||j.status;statusDot.className=j.status==='active'?'dot':'dot bad';today.textContent=fmt(done);target.textContent=continuous?'持续':fmt(goal);percent.textContent=continuous?'持续运行':`${pct.toFixed(3)}%`;progress.style.width=`${pct}%`;discovered.textContent=fmt(j.discovered);duplicateRate.textContent=`${((dupes/Math.max(1,done+dupes))*100).toFixed(1)}%`;speed.textContent=`${(rate*60).toFixed(1)} 条/分钟`;remaining.textContent=continuous?'持续采集':eta(Math.max(0,goal-done),rate);collector.textContent=continuous?'google-search':(j.proxy_profile==='private'?'private-proxy':j.proxy_profile);jobStatus.textContent=statusText[j.status]||j.status;health.textContent=j.status==='failed'?'error':'ok';health.className=j.status==='failed'?'bad-text':'ok';failed.textContent=fmt(j.failed);const event=(d.events||[])[0];anomaly.textContent=event?`${event.error_code||event.status} · ${ago(event.created_at)}`:'—';const sources=continuous?(d.continuous_source_stats||[]):(d.source_stats||[]).filter(row=>String(row.campaign_id)===String(j.id));sourceRows.innerHTML=sources.length?sources.map(row=>`<tr><td>${esc(row.source)}</td><td>${fmt(row.today)}</td></tr>`).join(''):'<tr><td colspan="2" class="empty">暂无数据</td></tr>';const pools=d.proxy_pools||{};proxyRows.innerHTML=['private','public'].map(name=>{const p=pools[name]||{},usable=!!p.usable;return`<tr><td>${name==='private'?'私有代理池':'公共代理池'}</td><td>${fmt(p.fresh)}</td><td>${fmt(p.http)}</td><td>${fmt(p.socks5)}</td><td>${ago(p.synced_at)}</td><td class="${usable?'ok':'bad-text'}">${usable?'ok':'unavailable'}</td></tr>`}).join('');updated.textContent=`索引 ${fmt(d.indexed_documents)} 条 · 最近刷新 ${new Date().toLocaleTimeString()}`}catch(e){runStatus.textContent='offline';statusDot.className='dot bad';updated.textContent='统计读取失败，正在重试'}}refresh();setInterval(refresh,2000);
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
