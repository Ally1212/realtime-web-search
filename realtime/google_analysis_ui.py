"""Unify the saved-search analysis and its underlying evidence."""
from pathlib import Path

SECTION = '''
<div id="googleAnalysis">
  <header class="ga-intro">
    <h2>Google 分析</h2>
    <p>输入关键词或域名，看看 Google 返回过什么、发生了哪些变化，以及哪些结果值得继续关注。</p>
  </header>
  <form id="gaForm">
    <label>关键词或域名<input id="gaInput" required maxlength="300" placeholder="例如 solar energy 或 example.com"></label>
    <button type="submit">查看分析</button>
  </form>
  <div class="ga-entry">
    <button type="button" class="secondary mini" id="gaNewsExample">试用真实样本 · 钠离子电池</button>
    <button type="button" class="secondary mini" id="gaWebExample">选一个已存网页关键词</button>
  </div>
  <details class="ga-options">
    <summary>地区、时间与竞品</summary>
    <div class="ga-filters">
      <label>观测时间<select id="gaDays"><option value="30">近 30 天</option><option value="7">近 7 天</option><option value="90">近 90 天</option></select></label>
      <label>地区<select id="gaRegion"></select></label>
      <label>语言<select id="gaLanguage"><option value="">全部</option><option value="en">英语</option><option value="zh">中文</option></select></label>
      <label>目标域名<input id="gaDomain" maxlength="253" placeholder="example.com"></label>
      <label>竞品域名<input id="gaCompetitors" maxlength="2600" placeholder="多个域名用逗号分隔"></label>
    </div>
  </details>
  <p id="gaStatus" class="ga-meta" role="status"></p>
  <div id="gaResult"></div>
</div>
'''

CSS = '''
#googleAnalysis{margin:0 0 24px;color:var(--ink,#171717)}
#googleAnalysis h2,#googleAnalysis h3,#googleAnalysis h4{margin:0;line-height:1.35}
.ga-intro h2{font-size:clamp(1.4rem,3vw,2rem)}
#gaForm{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:10px;max-width:760px;margin-block:20px 10px}
#gaForm button{margin:0;min-height:42px}
.ga-entry,.ga-facts{display:flex;flex-wrap:wrap;align-items:center;gap:8px 18px}
.ga-entry button{margin:0}
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
@media(max-width:450px){#gaForm{grid-template-columns:minmax(0,1fr)}#gaForm button{justify-self:start}}
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
    page = page.replace('</style>', CSS + '</style>', 1)
    marker = "window.addEventListener('hashchange',showView);showView();"
    script = Path(__file__).with_name('assets').joinpath('google-analysis.js').read_text()
    assert marker in page
    return page.replace(marker, script + '\n' + marker, 1)
