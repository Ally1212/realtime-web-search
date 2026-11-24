"""Unify the saved-search analysis and its underlying evidence."""
from pathlib import Path

SECTION = '''
<div id="googleAnalysis">
  <div class="ga-modes" role="group" aria-label="分析入口">
    <button type="button" class="secondary" data-ga-mode="keyword" aria-pressed="true">查关键词</button>
    <button type="button" class="secondary" data-ga-mode="website" aria-pressed="false">看网站表现</button>
  </div>
  <form id="gaForm">
    <label class="ga-query"><span id="gaInputLabel">关键词</span><input id="gaInput" required maxlength="300" placeholder="例如 solar energy"></label>
    <label>时间<select id="gaDays"><option value="30">近 30 天</option><option value="7">近 7 天</option><option value="90">近 90 天</option></select></label>
    <label>地区<select id="gaRegion"></select></label>
    <button type="submit">查看分析</button>
  </form>
  <div class="ga-entry" id="gaExamples">
    <span>试试已有记录</span>
    <button type="button" class="secondary mini" id="gaMixedExample" aria-pressed="false">AI coding assistants</button>
    <button type="button" class="secondary mini" id="gaWebExample" aria-pressed="false">china</button>
  </div>
  <details class="ga-options">
    <summary>更多筛选</summary>
    <div class="ga-filters">
      <label>语言<select id="gaLanguage"><option value="">全部</option><option value="en">英语</option><option value="zh">中文</option></select></label>
      <label>网页设备<select id="gaDevice"><option value="">自动选择</option><option value="desktop">桌面端</option><option value="mobile">移动端</option></select></label>
      <label data-ga-keyword-filter>目标域名<input id="gaDomain" maxlength="253" placeholder="example.com"></label>
      <label data-ga-keyword-filter>竞品域名<input id="gaCompetitors" maxlength="2600" placeholder="多个域名用逗号分隔"></label>
    </div>
  </details>
  <div class="ga-feedback"><progress id="gaProgress" aria-label="正在读取已保存的数据" hidden></progress><p id="gaStatus" class="ga-meta" role="status"></p></div>
  <div id="gaResult"></div>
</div>
'''

CSS = '''
#googleAnalysis{margin:0 0 24px;color:var(--ink,#171717)}
#googleAnalysis h2,#googleAnalysis h3,#googleAnalysis h4{margin:0;line-height:1.35}
.ga-intro h2{font-size:clamp(1.4rem,3vw,2rem)}
#gaForm{display:grid;grid-template-columns:minmax(0,1fr) minmax(112px,auto) minmax(100px,auto) auto;align-items:end;gap:10px;margin-block:20px 10px}
#gaForm label{min-width:0}
#gaForm button{margin:0;min-height:42px}
.ga-entry,.ga-facts{display:flex;flex-wrap:wrap;align-items:center;gap:8px 18px}
.ga-entry button{margin:0}
.ga-entry>span{color:var(--muted,#525252);font-size:.82rem}
.ga-entry button[aria-pressed="true"]{border-color:#276657;background:#edf5f2;color:#164b3d}
.ga-options,.ga-disclosure{margin-block:14px}
#gaStatus:empty{display:none}
.ga-options summary,.ga-disclosure summary,#googleAnalysis summary{cursor:pointer;font-weight:600;min-height:36px;align-content:center}
.ga-options summary:focus-visible,.ga-disclosure summary:focus-visible,#googleAnalysis :focus-visible{outline:2px solid currentColor;outline-offset:3px}
.ga-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:12px}
.ga-section{border-top:1px solid var(--line,#dedede);padding-block:20px;overflow-wrap:anywhere}
.ga-section h3{font-size:1.12rem}
.ga-meta,.ga-intro p{color:var(--muted,#525252);margin:.45rem 0 .7rem;max-width:78ch}
.ga-facts{margin:.6rem 0 .9rem;font-variant-numeric:tabular-nums}
.ga-evidence,.ga-ranks{list-style:none;padding:0;margin:.5rem 0}
.ga-evidence li,.ga-ranks li{padding:.55rem 0;border-top:1px solid var(--line,#dedede)}
.ga-evidence p,.ga-ranks p{margin:.15rem 0}
.ga-disclosure{border-top:1px solid var(--line,#dedede);padding-block:6px}
.ga-overview{display:grid;gap:18px}
.ga-findings{list-style:none;padding:0;margin:0;border-block:1px solid var(--line,#dedede)}
.ga-findings li{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:8px 16px;padding:13px 0}
.ga-findings li+li{border-top:1px solid var(--line,#dedede)}
.ga-findings li p{margin:.25rem 0 0}
.ga-view-heading{display:flex;flex-wrap:wrap;align-items:baseline;gap:5px 12px}
.ga-view-heading p{margin:0}
.ga-heading-icon{display:inline-flex;align-items:center;gap:.45rem}
.ga-heading-icon svg{width:18px;height:18px;flex:none;color:#276657}
.ga-tag{margin-inline-end:8px;display:inline-block;font-size:.75rem;font-weight:650;color:#334155}
.ga-tag[data-kind="opportunity"]{color:#17594a}
.ga-charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:14px}
.ga-chart-group{display:contents}
.ga-chart{border:1px solid var(--line,#dedede);border-radius:12px;padding:14px;min-width:0;overflow-wrap:anywhere}
.ga-chart .ga-meta{font-size:.82rem}
.ga-chart figure{margin:10px 0 0}
.ga-chart figcaption{color:var(--muted,#525252);font-size:.8rem}
.ga-chart svg{display:block;width:100%;max-height:205px}
.ga-bars{list-style:none;padding:0;margin:10px 0}
.ga-bars li+li{margin-block-start:8px}
.ga-bar-label{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.ga-bar-number{font-variant-numeric:tabular-nums;flex-shrink:0}
.ga-bar-track{display:block;height:6px;border-radius:6px;background:#e7edf1;margin-block-start:4px}
.ga-bar-fill{display:block;height:100%;border-radius:inherit;background:#276657}
.ga-table{overflow:auto;max-height:320px}
.ga-table table{min-width:max-content}
.ga-table thead{position:sticky;top:0;background:#fff}
.ga-records{max-block-size:300px;overflow:auto;overscroll-behavior:contain}
.ga-records .ga-evidence{margin-block-end:.35rem}
.ga-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ga-tools button,.ga-findings button{margin:0}
@media(max-width:650px){#gaForm{grid-template-columns:repeat(2,minmax(0,1fr))}.ga-query{grid-column:1/-1}#gaForm button{justify-self:start}.ga-findings li{grid-template-columns:1fr}.ga-findings button{justify-self:start}}
'''


CARD_CSS = '''
#googleAnalysis{--ga-paper:#fff;--ga-mist:#f2f6f4;--ga-edge:#dce7e1;--ga-accent:#205b4b;--ga-text:#152620;--ga-secondary:#465952;color:var(--ga-text)}
#googleAnalysis .ga-meta{color:var(--ga-secondary)}
#googleAnalysis #gaForm{gap:12px;margin-block:14px 10px}
#googleAnalysis .ga-query input{background:var(--ga-paper)}
#googleAnalysis #gaForm>button{background:var(--ga-accent);color:#fff;border-color:var(--ga-accent)}
#googleAnalysis .ga-overview{gap:20px}
#googleAnalysis .ga-view-heading{justify-content:space-between;padding-block-start:12px;border-block-start:1px solid var(--ga-edge)}
#googleAnalysis .ga-view-heading h3{font-size:clamp(1.2rem,2.4vw,1.65rem);letter-spacing:-.02em;overflow-wrap:anywhere}
#googleAnalysis .ga-charts{grid-template-columns:repeat(3,minmax(0,1fr));gap:13px;align-items:start}
#googleAnalysis .ga-chart{border:1px solid var(--ga-edge);border-radius:14px;background:var(--ga-paper);padding:15px 16px;min-height:182px}
.ga-card-header{display:flex;flex-wrap:wrap;gap:6px 12px;justify-content:space-between;align-items:center}
.ga-card-header h3{font-size:1rem}
.ga-card-source{font-size:.73rem;color:var(--ga-accent);font-weight:600;white-space:nowrap}
#googleAnalysis .ga-chart .ga-meta{margin-block:6px 10px;line-height:1.45}
#googleAnalysis .ga-bar-track{background:#e4ede8;height:5px}
#googleAnalysis .ga-bar-fill{background:var(--ga-accent)}
#googleAnalysis .ga-bars{margin-block:8px 0}
#googleAnalysis .ga-bars .ga-bar-label>button{background:none;border:0;text-align:start;color:var(--ga-accent);padding:0;text-decoration:underline;text-underline-offset:3px;min-width:0}
#googleAnalysis .ga-chart figure{margin-block-start:8px}
#googleAnalysis .ga-findings{border-block-start:1px solid var(--ga-edge);border-block-end:0}
#googleAnalysis .ga-findings li{padding-block:8px}
#googleAnalysis{container-type:inline-size}
#googleAnalysis .ga-card-header h3>svg{width:18px;height:18px;flex:0 0 18px}
@container (width<970px){#googleAnalysis .ga-charts{grid-template-columns:repeat(2,minmax(0,1fr))}}
@container (width<610px){#googleAnalysis .ga-charts{grid-template-columns:minmax(0,1fr)}}
#googleAnalysis .ga-chart .ga-table{max-height:320px;margin-block-start:9px}
#googleAnalysis .ga-chart .ga-table table{width:100%;min-width:0;table-layout:fixed;font-size:.79rem;font-variant-numeric:tabular-nums}
#googleAnalysis .ga-chart .ga-table caption{text-align:start;font-size:.78rem;color:var(--ga-secondary);padding:0 0 6px}
#googleAnalysis .ga-chart .ga-table th,
#googleAnalysis .ga-chart .ga-table td{white-space:normal;overflow-wrap:anywhere;padding:7px 6px;line-height:1.35}
#googleAnalysis .ga-chart .ga-table thead th:first-child{width:24%}
#googleAnalysis .ga-chart .ga-table td{text-align:center}
#googleAnalysis .ga-chart .ga-table:not(:has(tbody tr:nth-child(7))){max-height:none}

/* Conclusions lead the report. Supporting charts and request data expand in place. */
#googleAnalysis {--ga-accent:#225b47;--ga-wash:#e8f1eb;min-width:0;max-width:1150px}
#googleAnalysis .ga-modes{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
#googleAnalysis .ga-modes button{margin:0;min-height:42px;border:0;background:transparent;color:#43594b}
#googleAnalysis .ga-modes button[aria-pressed=true]{background:var(--ga-wash);color:#164735}
#googleAnalysis .ga-feedback{display:flex;align-items:center;gap:10px;min-height:27px}
#googleAnalysis .ga-feedback:has(#gaStatus:empty){min-height:0}
#googleAnalysis progress{width:30px;height:8px;flex:none;accent-color:var(--ga-accent)}
#googleAnalysis .ga-report{margin-top:20px;padding:clamp(16px,3vw,28px);min-width:0;background:#fff;border:1px solid #dbe4db;border-radius:15px;overflow-wrap:anywhere}
#googleAnalysis .ga-report .ga-view-heading{display:block;padding:0;border:0}
#googleAnalysis .ga-report h2{font-size:clamp(1.3rem,2.8vw,1.8rem);line-height:1.25;letter-spacing:-.025em}
#googleAnalysis .ga-report h3{font-size:1.05rem}
#googleAnalysis .ga-keyword-layout,#googleAnalysis .ga-site-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,.72fr);gap:22px}
#googleAnalysis .ga-keyword-layout:has(#gaLeadChart[hidden]){grid-template-columns:minmax(0,1fr)}
#googleAnalysis .ga-site-layout{grid-template-columns:minmax(0,1fr) minmax(0,.48fr)}
#googleAnalysis .ga-findings{border:0;margin-top:6px}
#googleAnalysis .ga-findings li{padding-block:13px}
#googleAnalysis .ga-findings .ga-pending-source{font-size:.85rem;color:#4b6254}
#googleAnalysis #gaLeadChart .ga-chart{background:#edf4ee;border-color:#edf4ee;box-shadow:none}
#googleAnalysis .ga-site-highlight{padding:13px 15px;border-radius:10px;background:var(--ga-wash);margin-bottom:16px}
#googleAnalysis .ga-site-highlight .ga-meta{margin-bottom:0}
#googleAnalysis .ga-site-summary{min-width:0;align-self:start;padding-top:7px}
#googleAnalysis .ga-site-summary dl{margin:4px 0 12px}
#googleAnalysis .ga-site-summary dl div{display:flex;justify-content:space-between;flex-wrap:wrap;gap:5px;padding-block:10px;border-bottom:1px solid #dbe4db}
#googleAnalysis .ga-site-summary dd{margin:0;font-weight:700}
#googleAnalysis .ga-source-details{border-top:1px solid #e1e8e1}
#googleAnalysis .ga-report-actions{margin-bottom:2px}
#googleAnalysis .ga-previous{margin:0 0 5px;color:#704b29}
#googleAnalysis .ga-skeleton{min-height:290px}
#googleAnalysis .ga-skeleton div{height:14px;border-radius:4px;background:#e8f0e9;margin-block:12px;width:min(100%,480px)}
#googleAnalysis .ga-skeleton div:first-child{width:min(42%,200px);height:20px}
#googleAnalysis .ga-skeleton div:last-child{height:100px}
@media(max-width:820px){#googleAnalysis .ga-keyword-layout,#googleAnalysis .ga-site-layout{grid-template-columns:minmax(0,1fr)}}
@media(max-width:500px){#googleAnalysis #gaForm{grid-template-columns:repeat(2,minmax(0,1fr))}
#googleAnalysis .ga-query,#googleAnalysis #gaForm button{grid-column:1/-1;justify-self:stretch}
#googleAnalysis .ga-findings li{display:block}
#googleAnalysis .ga-findings button{margin-top:6px}}
'''

def extend(page):
    analysis = '<section id="analysisView" class="analysis-workspace" hidden>'
    start = page.index(analysis) + len(analysis)
    end = page.index('<section id="monitorsView"', start)
    close = page.rindex('</section>', start, end)
    existing = page[start:close]
    page = (page[:start] + SECTION
            + '<details class="ga-disclosure" id="gaReports"><summary>高级分析与数据总览</summary>'
            + existing + '</details><details class="ga-disclosure" id="gaHistory"><summary>搜索历史与证据</summary></details>'
            + page[close:])
    page = page.replace('<a href="#research" data-view="research"', '<a hidden href="#research" data-view="research"', 1)
    page = page.replace('id="insightsView" hidden data-screen-label="数据分析"',
                        'id="insightsView" hidden data-screen-label="文本分析"', 1)
    page = page.replace('</style>', CSS + CARD_CSS + '</style>', 1)
    marker = "window.addEventListener('hashchange',showView);showView();"
    script = Path(__file__).with_name('assets').joinpath('google-analysis.js').read_text()
    assert marker in page
    return page.replace(marker, script + '\n' + marker, 1)
