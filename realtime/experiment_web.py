"""Read-only unified experiment dashboard; independent of collector lifecycle."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .experiment_store import ExperimentStore

TERMINAL = {'complete', 'storage_stopped', 'stopped'}
HEARTBEAT_TTL = 120


class ReadOnlyStore(ExperimentStore):
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.db.execute('BEGIN')


def timing(settings: dict, runtime: dict, now: float) -> dict:
    start = float(settings.get('started_at') or now)
    deadline = float(settings.get('deadline') or start)
    state = settings.get('state', 'initializing')
    finished = settings.get('finished_at')
    heartbeat = settings.get('heartbeat')
    age = max(0, now-float(heartbeat)) if heartbeat else None
    fresh = age is not None and age <= HEARTBEAT_TTL
    end = min(now, deadline)
    if state in TERMINAL and finished is not None:
        end = min(end, float(finished))
    elapsed = max(0, end-start)
    online = float(runtime.get('active', 0)) + float(runtime.get('google_cooling', 0))
    if state == 'running' and fresh and now < deadline:
        online += age
    if state in TERMINAL:
        display = state
    elif state == 'initializing':
        display = 'initializing'
    elif not fresh:
        display = 'offline'
    elif now >= deadline or state == 'draining':
        display = 'draining'
    elif state == 'paused':
        display = 'paused'
    elif state != 'running':
        display = 'interrupted'
    elif float(settings.get('search_cooling_until') or 0) > now:
        display = 'cooling'
    else:
        display = 'running'
    return {'started_at': start, 'deadline': deadline, 'finished_at': finished,
            'heartbeat_at': heartbeat, 'heartbeat_age_seconds': age, 'heartbeat_fresh': fresh,
            'elapsed_seconds': elapsed, 'online_seconds': min(elapsed, max(0, online)),
            'remaining_seconds': 0 if state in TERMINAL else max(0, deadline-now),
            'state': state, 'display_state': display, 'heartbeat_ttl': HEARTBEAT_TTL,
            'cooling_until': settings.get('search_cooling_until', 0)}


class ExperimentDashboard:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = threading.Lock()
        self.cache = {}

    def path(self, key: str) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
            raise ValueError('invalid experiment identifier')
        path = self.root / key / 'experiment.sqlite3'
        if path.is_symlink() or path.parent.is_symlink() or path.resolve().parent.parent != self.root or not path.is_file():
            raise FileNotFoundError('experiment not found')
        return path.resolve()

    def catalog(self) -> dict:
        rows, unavailable = [], 0
        for directory in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not directory.is_dir() or directory.is_symlink():
                continue
            store = None
            try:
                store = ReadOnlyStore(self.path(directory.name))
                settings = {r['key']: json.loads(r['value']) for r in store.db.execute('SELECT * FROM settings')}
                if not settings.get('started_at'):
                    continue
                rows.append({'key': directory.name, 'id': settings.get('id'),
                             'preflight': bool(settings.get('preflight')), 'started_at': settings['started_at'],
                             'state': settings.get('state'), 'deadline': settings.get('deadline')})
            except (OSError, ValueError, sqlite3.Error):
                unavailable += 1
            finally:
                if store:
                    store.db.close()
        rows.sort(key=lambda r: (r['preflight'], -float(r['started_at'])))
        return {'experiments': rows, 'default_key': rows[0]['key'] if rows else None, 'unavailable': unavailable}

    def detail(self, key: str) -> dict:
        path = self.path(key)
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic()-cached[0] < 3:
                return cached[1]
            store = ReadOnlyStore(path)
            try:
                settings = {r['key']: json.loads(r['value']) for r in store.db.execute('SELECT * FROM settings')}
                if not settings.get('started_at'):
                    raise ValueError('experiment is initializing')
                now = time.time()
                metrics = store.counts()
                recent = store.db.execute("SELECT count(*) FROM documents WHERE classification='new' AND finished>? AND finished<=?",
                                          (now-60, min(now, settings['deadline']))).fetchone()[0]
                latest = [dict(r) for r in store.db.execute(
                    'SELECT q.family,q.query,q.language,s.page,s.status,s.error,s.finished,json_array_length(s.results) results '
                    'FROM searches s JOIN queries q ON q.id=s.query_id ORDER BY s.id DESC LIMIT 15')]
                counts = dict(store.db.execute('SELECT count(*) queries,sum(enabled) enabled FROM queries').fetchone())
                data = {'key': key, 'id': settings.get('id'), 'preflight': bool(settings.get('preflight')),
                        'server_now': now, 'timing': timing(settings, metrics['runtime_seconds'], now),
                        'metrics': metrics, 'recent_new_per_minute': recent,
                        'query_counts': counts, 'recent_searches': latest,
                        'whale_enabled': settings.get('whale', False),
                        'whale_last_error': settings.get('whale_last_error'),
                        'stop_reason': settings.get('stop_reason'), 'baseline_note': settings.get('baseline_note')}
            finally:
                store.db.close()
            if len(self.cache) > 20:
                self.cache.clear()
            self.cache[key] = (time.monotonic(), data)
            return data


DASHBOARD_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>统一采集统计 · Google 实验</title><style>
:root{color-scheme:light;--bg:#f4f6f8;--text:#17212b;--muted:#627081;--line:#dbe2e9;--blue:#1769e0;--green:#137653}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1180px;margin:auto;padding:24px 20px 48px}nav{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:25px}a{color:var(--blue);text-decoration:none}nav a[aria-current]{font-weight:750;color:var(--text)}header{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}h1{margin:0;font-size:28px}h2{font-size:19px;margin:0 0 12px}p{margin:6px 0}.muted{color:var(--muted)}.badge{border-radius:20px;padding:5px 13px;white-space:nowrap;background:#e9eef4}.badge.good{background:#e1f3eb;color:var(--green)}.badge.warn{background:#fff0c7;color:#845b00}.badge.bad{background:#ffe5e2;color:#a02219}.controls{margin:20px 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center}select{font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:7px;max-width:100%;background:white}.hero,.card,section.panel{background:white;border:1px solid var(--line);border-radius:10px}.hero{padding:25px;display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:14px}.big{font-size:62px;font-weight:800;line-height:1.1;letter-spacing:-1px}.unit{font-size:20px;margin-left:8px}.row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0}.card{padding:17px;min-width:0}.label{font-size:13px;color:var(--muted)}.value{font-size:26px;font-weight:760;margin:5px 0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.clock{font-size:30px;white-space:nowrap}.hint{font-size:12px;color:var(--muted)}.date{font-size:14px;font-variant-numeric:tabular-nums;margin-top:7px}progress{width:100%;height:9px;accent-color:var(--blue);margin:13px 0 4px}section.panel{margin-top:20px;padding:20px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:620px}th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted);font-size:12px}td.query{max-width:400px;overflow-wrap:anywhere}.sources{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.sources .card{background:#f9fbfd}.source-title{font-weight:750;font-size:16px}.alert{padding:12px 15px;background:#fff1d5;border-radius:7px;margin:14px 0}.hidden{display:none!important}footer{color:var(--muted);font-size:12px;margin-top:18px}.mini{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}.mini span{white-space:nowrap}@media(max-width:900px){.row,.sources{grid-template-columns:repeat(2,minmax(0,1fr))}.hero{grid-template-columns:1fr}.clock{font-size:27px}}@media(max-width:480px){main{padding:18px 12px}.row,.sources{grid-template-columns:1fr}.big{font-size:48px}header{display:block}.badge{display:inline-block;margin-top:10px}.hero{padding:18px}nav{gap:14px}}
</style></head><body><main>
<nav aria-label="采集网站导航"><a href="/stats" aria-current="page">实验统计</a><a href="/stats/legacy">旧任务统计</a><a href="/">本地关键词采集</a></nav>
<header><div><h1>统一采集统计</h1><p class="muted">Google 限定来源 · 独立日量实验，与旧任务分别统计</p></div><span id="status" class="badge">正在连接</span></header>
<div class="controls"><label for="experiment">查看实验</label><select id="experiment"><option>正在读取…</option></select><span id="identity" class="hint"></span></div>
<div id="error" class="alert hidden" role="status"></div><div id="empty" class="alert hidden">暂无独立实验。可以查看<a href="/stats/legacy">旧任务统计</a>。</div>
<div id="content" class="hidden">
<section class="hero"><div><div class="label">本实验新增有效正文</div><div class="big"><span id="newCount">0</span><span class="unit">条</span></div><p class="muted">本地基线之外、去重且通过自动质量规则；不是当天发布量，也不是人工验收量。</p></div><div><h2 id="stateHeading">正在读取实验</h2><p id="stateHelp" class="muted"></p><progress id="progress" max="100" value="0" aria-label="实验时间进度"></progress><div id="progressText" class="hint"></div></div></section>
<div class="row" aria-label="实验时间">
<div class="card"><div class="label">实验已持续</div><div class="value clock" id="elapsed">00:00:00</div><div class="hint">含暂停、冷却、离线；不重置起点</div></div>
<div class="card"><div class="label">累计在线运行</div><div class="value clock" id="online">00:00:00</div><div class="hint">按心跳估算，含搜索冷却</div></div>
<div class="card"><div class="label">距离停止新请求</div><div class="value clock" id="remaining">00:00:00</div><div class="hint">到期后最多另留 5 分钟收尾</div></div>
<div class="card"><div class="label">开始 / 计划截止（新加坡 UTC+8）</div><div id="started" class="date">—</div><div id="deadline" class="date">—</div><div class="hint" id="heartbeat">最后心跳：—</div></div>
</div>
<div class="row">
<div class="card"><div class="label">最近一分钟新增</div><div class="value" id="speed">0 条/分钟</div><div class="hint">实际新增正文，不是日量预测</div></div>
<div class="card"><div class="label">Google 唯一候选 URL</div><div class="value" id="urls">0</div><div class="hint" id="requests">实际搜索请求 0 次</div></div>
<div class="card"><div class="label">Whale 接收回执</div><div class="value" id="accepted">0</div><div class="hint" id="whaleDetail">幂等重复 / 待投递 / 拒收：0 / 0 / 0</div></div>
<div class="card"><div class="label">缺少发布时间 · 仅本地保存</div><div class="value" id="missingDate">0</div><div class="hint">Whale 要求发布时间，不填造日期上传</div></div>
</div>
<section class="panel"><h2>四类查询贡献</h2><p class="hint">覆盖量可以重合，不能相加当总量；独有量按完整来源关系计算。</p><div class="sources" id="sources"></div><div class="mini" id="quality"></div></section>
<section class="panel"><h2>每小时新增正文</h2><div class="table-wrap"><table><thead><tr><th>实验小时</th><th>新增有效正文</th><th>累计</th></tr></thead><tbody id="hourRows"></tbody></table></div><p id="phaseRates" class="hint"></p></section>
<section class="panel"><h2>最近实际搜索</h2><div class="table-wrap"><table><thead><tr><th>查询类型</th><th>真实查询文本</th><th>页码</th><th>结果数</th><th>状态</th></tr></thead><tbody id="searchRows"></tbody></table></div></section>
</div><footer id="updated">统计每 5 秒刷新，计时每秒更新；页面只读，不会启动、暂停或重置采集任务。</footer>
</main><script>
const $=id=>document.getElementById(id), n=v=>Number(v||0), fmt=v=>n(v).toLocaleString('zh-CN');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration=v=>{const s=Math.max(0,Math.floor(n(v)));return [Math.floor(s/3600),Math.floor(s%3600/60),s%60].map(x=>String(x).padStart(2,'0')).join(':')};
const date=v=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(v*1000)):'—';
const names={topic:'主题查询',event:'事件查询',site:'站点查询',recent:'时间查询'};
const states={running:'正在运行',cooling:'搜索冷却 · 正文可继续',paused:'已暂停',offline:'心跳失联',draining:'已到截止 / 正在收尾',complete:'已完成',storage_stopped:'存储保护停止',stopped:'已停止',interrupted:'已中断',initializing:'初始化中'};
let snapshot=null,received=0,selected='',loading=false,catalogLoading=false;
function tick(){if(!snapshot)return;const t=snapshot.timing,now=snapshot.server_now+(performance.now()-received)/1000,terminal=['complete','storage_stopped','stopped'].includes(t.state),total=Math.max(0,t.deadline-t.started_at),end=terminal&&t.finished_at!=null?Math.min(t.deadline,t.finished_at):Math.min(now,t.deadline),elapsed=Math.max(0,end-t.started_at),age=t.heartbeat_at==null?Infinity:Math.max(0,now-t.heartbeat_at),fresh=age<=t.heartbeat_ttl;
let state=terminal?t.state:t.state==='initializing'?'initializing':!fresh?'offline':now>=t.deadline||t.state==='draining'?'draining':t.state==='paused'?'paused':t.state!=='running'?'interrupted':t.cooling_until>now?'cooling':'running';
let online=t.online_seconds;if(['running','cooling'].includes(state)&&now<t.deadline)online+=Math.max(0,now-snapshot.server_now);online=Math.min(elapsed,online);
$('elapsed').textContent=duration(elapsed);$('online').textContent=duration(online);$('remaining').textContent=duration(terminal?0:Math.max(0,t.deadline-now));$('status').textContent=states[state]||state;$('status').className='badge '+(['running','complete'].includes(state)?'good':['offline','interrupted','storage_stopped'].includes(state)?'bad':'warn');$('stateHeading').textContent=(snapshot.preflight?'预检 · ':'正式实验 · ')+(states[state]||state);
$('stateHelp').textContent=state==='offline'?'未收到新鲜心跳，不能确认采集器正在运行；实验截止时间仍不顺延。':state==='paused'?'采集已暂停，实验墙钟继续计时，累计在线时间不再递增。':terminal?'本轮已结束，运行时长已冻结；下方保留本实验结果。':state==='cooling'?'Google 搜索处于冷却，已发现网页仍可继续处理；不把等待误计为新增数据。':state==='draining'?'已停止新请求或正在收尾，截止之后完成的记录不计入主日量。':'正在轮换 Google 查询、抓取正文并投递符合条件的新内容。';
$('progress').value=total?Math.min(100,elapsed/total*100):0;$('progressText').textContent=`已持续 ${duration(elapsed)} / 计划 ${duration(total)} · ${snapshot.preflight?'预检不计正式日量':'不包含旧任务数据'}`;$('heartbeat').textContent=Number.isFinite(age)?`最后心跳：${Math.floor(age)} 秒前`:'尚无心跳';}
function render(d){snapshot=d;received=performance.now();$('content').classList.remove('hidden');$('identity').textContent=d.id;$('started').textContent='开始 '+date(d.timing.started_at);$('deadline').textContent='截止 '+date(d.timing.deadline);const m=d.metrics;$('newCount').textContent=fmt(m.new);$('speed').textContent=fmt(d.recent_new_per_minute)+' 条/分钟';$('urls').textContent=fmt(m.unique_urls);$('requests').textContent=`实际请求 ${fmt(m.google_requests)} 次 · 验证码 ${fmt(m.google_captchas)} 次`;$('accepted').textContent=fmt(m.whale_accepted);$('whaleDetail').textContent=`幂等重复 / 待投递 / 拒收：${fmt(m.whale_duplicate)} / ${fmt(m.whale_pending)} / ${fmt(m.whale_rejected)}`;$('missingDate').textContent=fmt(m.whale_blocked_missing_publication);
$('sources').innerHTML=Object.entries(names).map(([k,label])=>{const r=m.by_family[k]||{};return `<div class="card"><div class="source-title">${label}</div><div class="value">${fmt(r.new_content_covered)}</div><div class="hint">独有新正文 ${fmt(r.exclusive_new_content)} 条</div><div class="hint">请求 ${fmt(r.attempts)} 次 · 失败页 ${fmt(r.failures)}</div></div>`}).join('');
$('quality').innerHTML=[['重复正文',m.duplicate],['内容更新',m.update],['已知历史',m.baseline],['质量不合格/抓取失败',m.invalid],['截止后结果',m.late_results],['启用查询',d.query_counts.enabled]].map(([label,value])=>`<span>${label}：${fmt(value)}</span>`).join('');
const hours=Math.max(1,Math.min(24,Math.ceil(d.timing.elapsed_seconds/3600))),map=new Map((m.hourly||[]).map(r=>[r.hour,r.new_documents]));let cumulative=0;$('hourRows').innerHTML=Array.from({length:hours},(_,i)=>{const count=n(map.get(i+1));cumulative+=count;return `<tr><td>第 ${i+1} 小时${i===hours-1&&!['complete','stopped','storage_stopped'].includes(d.timing.state)?'（当前）':''}</td><td>${fmt(count)}</td><td>${fmt(cumulative)}</td></tr>`}).join('');$('phaseRates').textContent=`前 6 小时已观察均速：${n(m.first_6h_new_per_minute).toFixed(2)} 条/分钟；后 18 小时：${m.remaining_18h_new_per_minute==null?'尚未进入':n(m.remaining_18h_new_per_minute).toFixed(2)+' 条/分钟'}。不是全天产量预测。`;
$('searchRows').innerHTML=(d.recent_searches||[]).map(r=>`<tr><td>${names[r.family]||esc(r.family)}</td><td class="query">${esc(r.query)}</td><td>${r.page}</td><td>${fmt(r.results)}</td><td>${r.status==='success'?(r.results?'有结果':'明确空结果'):esc(r.error||'失败')}</td></tr>`).join('')||'<tr><td colspan="5">尚无搜索记录</td></tr>';$('updated').textContent=`统计每 5 秒刷新 · 计时每秒更新 · 数据时间 ${date(d.server_now)} · 只读展示，不影响实验`;tick();}
async function refresh(){if(!selected||loading)return;loading=true;const key=selected;try{const r=await fetch('/api/experiments/'+encodeURIComponent(key),{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();if(key!==selected)return;render(d);$('error').classList.add('hidden')}catch(e){if(key===selected){$('error').textContent='实验统计读取失败，保留上次快照；正在重试。此提示不代表采集器已停止。';$('error').classList.remove('hidden')}}finally{loading=false;if(key!==selected)refresh()}}
async function catalog(){if(catalogLoading)return;catalogLoading=true;try{const r=await fetch('/api/experiments');if(!r.ok)throw Error();const d=await r.json();$('experiment').innerHTML=d.experiments.map(row=>`<option value="${esc(row.key)}">${row.preflight?'预检':'正式实验'} · ${date(row.started_at)} · ${esc(row.key)}</option>`).join('')||'<option value="">暂无实验</option>';if(!d.experiments.some(r=>r.key===selected))selected=d.default_key||'';$('experiment').value=selected;$('empty').classList.toggle('hidden',!!selected);if(!selected){snapshot=null;$('content').classList.add('hidden');$('status').textContent='暂无实验'}await refresh()}catch(e){$('error').textContent='实验列表读取失败，正在重试。';$('error').classList.remove('hidden')}finally{catalogLoading=false}}
$('experiment').addEventListener('change',()=>{selected=$('experiment').value;snapshot=null;$('content').classList.add('hidden');refresh()});catalog();setInterval(refresh,5000);setInterval(catalog,30000);setInterval(tick,1000);
</script></body></html>'''
