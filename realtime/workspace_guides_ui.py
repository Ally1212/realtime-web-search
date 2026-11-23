"""In-page guides for analysis and keyword monitoring in the SearchFlow workbench."""

from .insights_results_ui import extend as extend_insights_results


INSIGHTS = '''
<div class="guide-copy"><h2>从一堆文字里找出常见问题</h2>
<p>读取已保存的搜索标题和摘要，或导入顾客评论。先看手上有哪些记录，再按话题分类、计数，最后点回原文核对。</p></div>
<ol class="guide-track">
<li><b>准备一批文字</b><span>读取已存结果，或导入评论</span></li>
<li><b>按主题打标签</b><span>确认要找的问题，再用规则或授权 AI 分类</span></li>
<li><b>看统计，查原文</b><span>点主题查看哪些记录被算进去</span></li>
</ol>
<div class="guide-example"><strong>示例 · 100 条餐厅评论</strong><span>等位太久 30 条</span><span>价格太高 8 条</span></div>
<div class="guide-actions"><button type="button" class="secondary" data-guide-jump="ti-query">读取搜索数据</button><button type="button" class="secondary" data-guide-import>导入评论</button><button type="button" class="secondary" data-guide-report aria-controls="sfInsightsExample" aria-expanded="false">查看示例报告</button><button type="button" class="secondary" data-guide-play>播放流程</button></div>
<section id="sfInsightsExample" class="guide-sample" aria-labelledby="sfInsightsExampleTitle" hidden>
<h3 id="sfInsightsExampleTitle">餐厅评论示例</h3><p>100 条示例评论。展开主题可以看几条对应的示例原文。</p>
<details><summary>等位太久 · 30/100（30%）</summary><ul><li>周末来吃饭，排了快一个小时才入座。</li><li>取号之后等了很久，差点改去别家。</li></ul></details>
<details><summary>价格太高 · 8/100（8%）</summary><ul><li>分量不大，人均价格比预计的高。</li><li>口味不错，但套餐定价超出了我的预算。</li></ul></details></section>
<details><summary>数据从哪来，数字怎么读？</summary><dl>
<dt>选一批要分析的数据</dt><dd>选来源和关键词读取已存搜索标题、摘要，或导入含原文的 CSV、JSON 或 JSONL。读取已存数据不会发起新搜索。</dd>
<dt>用标签说明你想数什么</dt><dd>比如给提到「等位太久」的评论打标签。确认标签后用规则分类，也可授权 AI 辅助。同一条评论可以同时提到等位和价格。</dd>
<dt>按样本读报告</dt><dd>30/100 表示这批 100 条评论里有 30 条提到等位太久。点主题查看原文，必要时校正标签，再导出数据或报告。</dd>
</dl></details>'''

ANALYSIS = '''
<div class="guide-copy"><h2>看过去搜到了什么，排名怎么变了</h2>
<p>用已保存的搜索记录做统计。选一段时间看采集总量，或选一个搜过多次的关键词，看你和竞品的排名变化。</p></div>
<ol class="guide-track">
<li><b>读取记录</b><span>选时间、来源和地区</span></li>
<li><b>对齐条件</b><span>同一关键词、地区、语言等</span></li>
<li><b>按时间比较</b><span>找出升降、新出现和未出现的页面</span></li>
</ol>
<div class="guide-example"><strong>示例 · 新加坡牙医</strong><span>周一 第 8 名</span><span>周三 第 5 名</span><span>周五 第 3 名</span></div>
<div class="guide-actions"><button type="button" class="secondary" data-guide-jump="analysisOverviewTab">看数据总览</button><button type="button" class="secondary" data-guide-jump="analysisRankingTab">查排名与竞品</button><button type="button" class="secondary" data-guide-play>播放流程</button></div>
<details><summary>怎么选，结果怎么读？</summary><dl>
<dt>看采集情况</dt><dd>在「数据总览」选时间和来源，查看请求成功／失败、去重链接、常见网站和关键词。一个链接出现 5 次，算 5 条链接记录、1 个去重链接。</dd>
<dt>查某个关键词的变化</dt><dd>在「排名与竞品」输入关键词和要看的域名。系统对比同条件的 Google 网页快照，并去掉重复缓存。至少两次不同时间的快照才能看趋势。</dd>
<dt>读懂变化</dt><dd>报告会列出实际排名、出现或未出现的页面，以及 Google 展示标题和摘要的变化。未出现时名次未知。查看或更新报告只读取已存数据。</dd>
</dl></details>'''

MONITOR = '''
<div class="guide-copy"><h2>让系统定期查同一个词，积累排名记录</h2>
<p>设好关键词、自己的网站和竞品，开始后系统会按间隔搜索 Google 网页，把每次看到的排名留在同一份报告里。</p></div>
<ol class="guide-track">
<li><b>固定条件</b><span>关键词、网站、地区和语言</span></li>
<li><b>保存后开始</b><span>默认暂停，由你手动开启</span></li>
<li><b>按间隔采样</b><span>到点提交 Google 网页搜索</span></li>
<li><b>查看排名</b><span>对照目标与竞品的历史记录</span></li>
</ol>
<div class="guide-example"><strong>示例 · 每天一次</strong><span>周一 第 8 名</span><span>周二 第 5 名</span><span>周三 未看到</span></div>
<div class="guide-actions"><button type="button" class="secondary" data-guide-jump="monitorQuery">设置监测</button><button type="button" class="secondary" data-guide-jump="monitorList">查看已保存监测</button><button type="button" class="secondary" data-guide-play>播放流程</button></div>
<details><summary>开始、额度和报告怎么理解？</summary><dl>
<dt>保存与开始</dt><dd>先在「快速使用」里连接工作空间。填一个关键词和目标域名，可加最多 10 个竞品。保存后为暂停状态。点击列表中的「开始」，系统才会提交第一轮采样并按所选间隔继续。</dd>
<dt>频率与额度</dt><dd>每天一次约 30 次／月。任务入队预留 1 次额度，成功扣 1 次，失败或取消释放预留。监测与 API 请求共用空间月额度和并发。</dd>
<dt>暂停与读报告</dt><dd>暂停停止后续采样，已保存结果仍可查看。每个监测从启用后积累自己的排名记录。打开「分析报告」可查看目标与竞品的排名时间线，最多查看最近 100 次任务中的成功快照，重复缓存只算一次。没看到的排名留空，至少两次不同时间的快照才能比较变化。</dd>
</dl></details>'''

CSS = '''
.sf-guide{container-type:inline-size;padding:clamp(1rem,2.5vw,1.65rem); margin-bottom:16px;padding-block-end:1rem;}
.sf-guide h2{font-size:clamp(1.125rem,1rem + .75vw,1.625rem);margin:0;color:#171717;line-height:1.28;font-weight:650;text-wrap:balance}
.sf-guide .guide-copy p{margin:.45rem 0 1.05rem;color:#525252;max-width:65ch;font-size:.875rem;line-height:1.65}
.sf-guide .guide-track{counter-reset:guide;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);list-style:none;margin:0;padding:0;border:1px solid #dedede;border-radius:12px;background:#fafafa}
.sf-guide .guide-track li{counter-increment:guide;min-width:0;position:relative;display:grid;grid-template-columns:1.65rem minmax(0,1fr);gap:.15rem .55rem;align-content:start;padding:.8rem .7rem;isolation:isolate}
.sf-guide .guide-track li+li{border-inline-start:1px solid #dedede}
.sf-guide .guide-track li::before{content:counter(guide);grid-row:span 2;display:grid;place-items:center;width:1.65rem;height:1.65rem;border:1px solid #cfcfcf;border-radius:50%;background:#fff;font-size:.75rem;font-weight:650;color:#262626}
.sf-guide .guide-track b{font-size:.875rem;color:#171717}
.sf-guide .guide-track li span{font-size:.75rem;color:#525252;line-height:1.45}
.sf-guide .guide-track li:not(:last-child)::after{content:"";position:absolute;z-index:1;top:50%;right:-.23rem;width:.46rem;height:.46rem;border:1px solid #666;border-radius:50%;background:#fff;transform:translateY(-50%)}
.sf-guide .guide-example{display:flex;align-items:center;flex-wrap:wrap;gap:.25rem .8rem;margin:.8rem 0;color:#525252;font-size:.75rem;font-variant-numeric:tabular-nums}
.sf-guide .guide-example strong{color:#262626}
.sf-guide .guide-actions{display:flex;flex-wrap:wrap;gap:.45rem;margin-bottom:.65rem}
.sf-guide .guide-actions button{min-height:2.75rem;width:auto;margin:0}
.sf-guide details{border-top:1px solid #dedede;padding-top:.6rem;margin:0}
.sf-guide summary{cursor:pointer;color:#262626;font-weight:600;font-size:.8125rem;min-height:2rem;line-height:1.7}
.sf-guide dl{margin:.35rem 0 0;max-width:74ch;font-size:.8125rem;line-height:1.65}
.sf-guide dt{font-weight:650;margin-top:.65rem;color:#262626}
.sf-guide dd{margin:0;color:#525252}
@media (max-width:720px){.sf-guide .guide-track{grid-auto-flow:row;grid-auto-columns:auto}.sf-guide .guide-track li+li{border-inline-start:0;border-top:1px solid #dedede}.sf-guide .guide-track li:not(:last-child)::after{top:auto;right:auto;bottom:-.23rem;left:1.43rem;transform:none}}
@container (max-width: 38rem){.sf-guide .guide-track{grid-auto-flow:row;grid-auto-columns:auto}.sf-guide .guide-track li+li{border-inline-start:0;border-top:1px solid #dedede}.sf-guide .guide-track li:not(:last-child)::after{top:auto;right:auto;bottom:-.23rem;left:1.43rem;transform:none}}
#monitorList{scroll-margin-top:5rem}
@media (max-width:600px){
  #monitorList .product-table{display:block}
  #monitorList .product-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
  #monitorList .product-table tbody{display:block}
  #monitorList .product-table tr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.45rem .6rem;padding:.75rem;border-bottom:1px solid #dedede}
  #monitorList .product-table td{display:block;padding:0;border:0;max-width:none}
  #monitorList .product-table td:last-child{grid-column:1/-1}
  #monitorList .row-actions{display:flex;flex-wrap:wrap}
  #monitorList .row-actions button{min-height:2.75rem;width:auto}
}
.monitor-samples-label{display:none}
@media (max-width:600px){.monitor-samples-label{display:inline}}
.sf-guide .guide-sample{margin:.7rem 0;padding:.8rem 1rem;background:#fafafa;border:1px solid #dedede;border-radius:12px}
.sf-guide .guide-sample h3{margin:0;font-size:1rem}
.sf-guide .guide-sample p,.sf-guide .guide-sample ul{font-size:.8125rem;color:#525252;line-height:1.6}
.sf-guide .guide-sample details+details{margin-top:.45rem}
.sf-guide .guide-sample summary{min-height:2.75rem;padding-block:.45rem}
'''

JS = '''
(() => {
const guideInsightInput = document.getElementById("ti-inputType");
guideInsightInput.addEventListener("change", () => {
  document.getElementById("ti-createButton").textContent = guideInsightInput.value === "file" ? "导入并查看数据" : "读取已采集数据";
});
for (const guide of document.querySelectorAll(".sf-guide")) {
  let animations = [];
  guide.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-guide-import")) {
      const inputType = document.getElementById("ti-inputType");
      inputType.closest("details").open = true;
      inputType.value = "file";
      inputType.dispatchEvent(new Event("change"));
      document.getElementById("ti-analysisMode").value = "voc";
      document.getElementById("ti-importFile").focus();
      return;
    }
    if (button.hasAttribute("data-guide-report")) {
      const example = document.getElementById(button.getAttribute("aria-controls"));
      example.hidden = !example.hidden;
      button.setAttribute("aria-expanded", String(!example.hidden));
      return;
    }
    if (button.dataset.guideJump) {
      if (button.dataset.guideJump === "ti-query") {
        guideInsightInput.value = "saved";
        guideInsightInput.dispatchEvent(new Event("change"));
      }
      const target = document.getElementById(button.dataset.guideJump);
      if (target.getAttribute("role") === "tab") target.click();
      target.focus({preventScroll: true});
      target.scrollIntoView({block: "nearest"});
      return;
    }
    if (!button.hasAttribute("data-guide-play")) return;
    for (const animation of animations) animation.cancel();
    if (!Element.prototype.animate || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const example = guide.querySelector(".guide-example");
      example.tabIndex = -1;
      example.focus();
      return;
    }
    animations = [...guide.querySelectorAll(".guide-track li")].flatMap((stage, index, stages) => {
      const pulse = stage.animate(
        [{backgroundColor:"#fafafa"}, {backgroundColor:"#dbeafe", offset:.35}, {backgroundColor:"#fafafa"}],
        {delay:index*670, duration:790, easing:"ease-in-out"}
      );
      if (index === stages.length-1) return [pulse];
      const vertical = Math.abs(stages[index+1].offsetTop - stage.offsetTop) > 4;
      const dot = Object.assign(document.createElement("span"), {className:"guide-motion-dot"});
      dot.setAttribute("aria-hidden", "true");
      dot.style.cssText = vertical
        ? "position:absolute;left:1.43rem;bottom:-.23rem;"
        : "position:absolute;right:-.23rem;top:calc(50% - .23rem);";
      dot.style.cssText += "z-index:2;width:.46rem;height:.46rem;border-radius:50%;background:#2563eb;";
      stage.append(dot);
      const travel = dot.animate(
        [{opacity:0,transform:vertical?"translateY(-12px)":"translateX(-12px)"},
         {opacity:1,offset:.35},{opacity:0,transform:vertical?"translateY(12px)":"translateX(12px)"}],
        {delay:index*670+460,duration:440}
      );
      dot.style.opacity = "0";
      travel.finished.then(() => dot.remove(), () => dot.remove());
      return [pulse, travel];
    });
  });
}
})();
'''


MONITOR_FORM_HELP = (
    '<div><h3>搜索条件示例</h3><p class="help">关键词：新加坡牙医。目标网站：clinic-a.example。'
    '竞品：clinic-b.example。地区：新加坡。间隔：每天一次。</p>'
    '<p class="help">保存后，在下方列表点击「开始」。</p><a href="#api">连接工作空间</a></div>'
)


def extend(page):
    if 'id="sfWorkspaceGuidesStyles"' in page:
        return page
    for marker, guide in (
        ('<section id="analysisView" class="analysis-workspace" hidden>', ANALYSIS),
        ('<section id="monitorsView" hidden>', MONITOR),
        ('<section id="insightsView" hidden data-screen-label="数据分析">', INSIGHTS),
    ):
        if page.count(marker) != 1:
            raise ValueError(f'Workspace guide mount missing or ambiguous: {marker}')
        page = page.replace(marker, marker + '<div class="panel sf-guide">' + guide + '</div>', 1)
    page = page.replace(
        '<h2>数据覆盖与搜索表现</h2><p class="muted">汇总已存数据，追踪同条件排名与竞品；分析本身不会启动采集。</p>',
        '<h2>搜索记录与排名报告</h2>', 1,
    )
    page = page.replace(
        '<h2>关键词与竞品监测</h2><p class="muted">固定搜索条件，积累自己的排名时间线。先在 API 工具连接工作空间。</p>',
        '<h2>新建关键词监测</h2>', 1,
    )
    help_start = page.index('<div><h3>从一次结果到长期观察</h3>')
    help_end = page.index('</div>', help_start) + len('</div>')
    page = page[:help_start] + MONITOR_FORM_HELP + page[help_end:]
    page = page.replace('id="monitorList"', 'id="monitorList" tabindex="-1"', 1)
    page = page.replace(
        '<td>${fmt(x.samples)}<div class="help">${x.last_sample?',
        '<td><span class="help monitor-samples-label">已完成 </span>${fmt(x.samples)}<div class="help">${x.last_sample?', 1,
    )
    # The initial hash handler runs before React attaches the sidebar links.
    page = page.replace(
        """document.querySelector('aside [data-view="insights"]').setAttribute(""",
        """document.querySelector('aside [data-view="insights"]')?.setAttribute(""", 1,
    )
    page = extend_insights_results(page)
    page = page.replace('</head>', '<style id="sfWorkspaceGuidesStyles">' + CSS + '</style></head>', 1)
    return page.replace('</body>', '<script id="sfWorkspaceGuidesScript">' + JS + '</script></body>', 1)
