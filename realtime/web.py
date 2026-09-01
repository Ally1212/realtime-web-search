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


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>实时网页搜索爬虫</title><style>
:root{color-scheme:dark;--bg:#080c13;--panel:#111925;--line:#273449;--text:#edf4fb;--muted:#8d9bae;--blue:#60a5fa;--green:#4ade80;--red:#fb7185}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#163158,transparent 40%),var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1050px;margin:auto;padding:42px 20px}h1{font-size:30px;margin:0}.sub{color:var(--muted);margin:4px 0 22px}.crawl{display:grid;grid-template-columns:1fr 100px 130px;gap:9px}.crawl input,.crawl select{padding:13px 15px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--text);font-size:15px}.crawl button,.local button{border:0;border-radius:10px;background:var(--blue);color:#07101e;font-weight:700;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}.card{background:#101824bb;border:1px solid var(--line);border-radius:11px;padding:13px}.card span{display:block;color:var(--muted);font-size:12px}.card b{font-size:22px}.status{color:var(--muted);min-height:24px}.local{display:flex;gap:8px;margin:25px 0 8px}.local input{flex:1;padding:11px 14px;background:var(--panel);border:1px solid var(--line);border-radius:9px;color:var(--text)}.local button{padding:0 20px}.result{padding:17px 0;border-top:1px solid var(--line)}.result a{font-size:18px;color:#86b9ff;text-decoration:none}.url{font-size:12px;color:var(--green);overflow:hidden;text-overflow:ellipsis}.snippet{color:#c6d0de;margin-top:4px}.snippet em{font-style:normal;background:#684f10;color:#fff}.meta{font-size:11px;color:var(--muted)}.empty{color:var(--muted);padding:26px 0}.warn{color:#fbbf24;font-size:12px;margin-top:16px}@media(max-width:700px){.crawl{grid-template-columns:1fr}.crawl button{height:44px}.cards{grid-template-columns:repeat(2,1fr)}}
</style></head><body><main><h1>每日 5 万网页采集</h1><div class="sub">多引擎发现 → Scrapy 持久队列 → 代理池 → 去重与全文索引</div><form class="crawl" id="crawlForm"><input id="query" placeholder="输入关键词，例如：新加坡 人工智能 最新政策" required><select id="proxyProfile"><option value="private" selected>私有代理</option><option value="public">公共代理</option><option value="direct">直接连接</option></select><button>创建 Campaign</button></form><div class="cards"><div class="card"><span>索引网页总数</span><b id="total">0</b></div><div class="card"><span>今日唯一正文</span><b id="today">0</b></div><div class="card"><span>预计每日</span><b id="projected">0</b></div><div class="card"><span>失败</span><b id="failed">0</b></div><div class="card"><span>重复</span><b id="duplicates">0</b></div></div><div class="status" id="jobStatus">等待任务</div><form class="local" id="searchForm"><input id="searchQuery" placeholder="搜索已经实时抓取的正文"><button>搜索索引</button></form><div id="results" class="empty">抓取完成后会显示当前网页内容</div><div class="warn">目标为每个关键词每日 50,000 个相关且去重的有效正文；实际数量取决于来源供给和代理池健康度。</div></main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let active='';
async function stats(){const d=await fetch('/api/stats').then(r=>r.json());total.textContent=Number(d.indexed_documents||0).toLocaleString();const j=active?d.jobs.find(x=>x.id===active):d.jobs[0];if(j){today.textContent=Number(j.today||0).toLocaleString();projected.textContent=Number(j.projected_daily||0).toLocaleString();failed.textContent=Number(j.failed||0).toLocaleString();duplicates.textContent=Number(j.duplicates||0).toLocaleString();jobStatus.textContent=`${j.status} · “${j.query}” · 今日 ${j.today}/${j.daily_target} · ${j.rate_per_second} 篇/秒 · ${j.proxy_profile}`}}
async function runSearch(q){if(!q)return;results.innerHTML='<div class="empty">搜索中…</div>';const d=await fetch('/api/search?q='+encodeURIComponent(q)).then(r=>r.json());results.innerHTML=d.results.length?d.results.map(x=>`<div class="result"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a><div class="url">${esc(x.url)}</div><div class="snippet">${x.snippet}</div><div class="meta">实时抓取 ${new Date(x.fetched_at).toLocaleString()} · HTTP ${x.http_status} · ${esc((x.source_engines||[]).join(', '))}</div></div>`).join('):'<div class="empty">没有匹配结果</div>'}
crawlForm.onsubmit=async e=>{e.preventDefault();const q=query.value.trim();const r=await fetch('/api/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,daily_target:50000,proxy_profile:proxyProfile.value})});const d=await r.json();if(!r.ok){jobStatus.textContent=d.error;return}active=d.campaign_id;jobStatus.textContent='Campaign 已创建，正在发现 URL…';searchQuery.value=q};searchForm.onsubmit=e=>{e.preventDefault();runSearch(searchQuery.value.trim())};stats();setInterval(stats,2000);
</script></body></html>'''

SIMPLE_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>实时抓取统计</title><style>
:root{color-scheme:light;--bg:#f6f8fb;--panel:#fff;--line:#e3e8ef;--text:#172033;--muted:#718096;--blue:#2563eb;--green:#22c55e}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#fff 0,#f6f8fb 360px);color:var(--text);font-family:system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:900px;margin:auto;padding:64px 22px}h1{font-size:30px;margin:0;letter-spacing:-.5px}.sub{color:var(--muted);margin:6px 0 30px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{min-height:145px;padding:23px;background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 28px rgba(23,32,51,.06)}.label{color:var(--muted);font-size:14px}.value{color:var(--blue);font-size:34px;font-weight:750;margin-top:12px}.detail{color:var(--muted);font-size:12px;margin-top:6px}.foot{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px;margin-top:22px}.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px rgba(34,197,94,.12)}@media(max-width:650px){main{padding-top:36px}.cards{grid-template-columns:1fr}.card{min-height:120px}}
</style></head><body><main><h1>实时抓取统计</h1><div class="sub">实时网页抓取运行情况</div><div class="cards"><div class="card"><div class="label">统计时间</div><div class="value" id="elapsed">—</div><div class="detail" id="started">尚未开始</div></div><div class="card"><div class="label">累计成功抓取</div><div class="value" id="fetched">0</div><div class="detail">按成功访问次数统计</div></div><div class="card"><div class="label">刷新频率</div><div class="value">2 秒</div><div class="detail">页面自动刷新</div></div></div><div class="foot"><span class="dot"></span><span id="updated">正在读取统计…</span></div></main><script>
const duration=ms=>{const s=Math.max(0,Math.floor(ms/1000)),d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),x=s%60;return d?`${d}天 ${h}小时 ${m}分`:(h?`${h}小时 ${m}分 ${x}秒`:`${m}分 ${x}秒`)};
async function refresh(){try{const d=await fetch('/api/stats').then(r=>r.json());fetched.textContent=Number(d.totals.fetched||0).toLocaleString();const jobs=d.jobs||[];if(jobs.length){const first=jobs.reduce((a,b)=>new Date(a.created_at)<new Date(b.created_at)?a:b);const began=new Date(first.created_at);elapsed.textContent=duration(Date.now()-began);started.textContent=`开始于 ${began.toLocaleString()}`}updated.textContent=`运行正常 · 最近刷新 ${new Date().toLocaleTimeString()}`}catch(e){updated.textContent='统计读取失败，正在重试'}}refresh();setInterval(refresh,2000);
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
            ]
            for campaign in payload["campaigns"]:
                campaign_id = str(campaign["id"])
                lines.append(f'realtime_campaign_today{{campaign_id="{campaign_id}"}} {campaign["today"]}')
                lines.append(
                    f'realtime_campaign_rate_per_second{{campaign_id="{campaign_id}"}} '
                    f'{campaign["rate_per_second"]}'
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
