# SERP 新链接优先：现状与优化方案

用户于 2026-10-10 明确要求把重点放在搜索返回的新链接，而非继续抓取正文。本文件保留切换前的只读研究；随后用户授权实施，现已部署无正文 SERP worker、持久化正文开关、结构化字段与页面/API。最新范围及实际限制见 [需求与本轮实现](google-serp-requirements-2026-10-10.md)。下方核验描述的是切换前状态。

## 目标与范围

核心指标：连续 24 小时内成功持久化的新增唯一自然搜索结果 URL 数。URL 经过保守规范化、排除不合格地址，并对所有留存历史去重；历史已丢失的地址无法排除，不宣称从未在互联网或已删除历史里出现。

目前按标准 Google Web 设计，与常见 Google organic SERP API 对齐。后续确认仅 Google Web，News / Trends / RSS 不运行、不纳入新指标；若将来扩展来源，应单独定义统计口径。

抓不到正文、正文短、正文语言或相关性不达标、不同 URL 的正文完全相同，都不再作为排除 SERP 新链接的条件。需要保留的是搜索结果信息，摘要不是文章全文。入库不承诺目标站一定可访问。

## 核验所得

核验时间：2026-10-10 UTC 02:01–02:07（新加坡 10:01–10:07）。

- 生产 local-worker 仍是 UTC 2026-10-09 11:02:08.720572 启动的原容器，重启 0；当前 OpenSERP、Web 最多 1 页、3 槽，Web + News + Trends + RSS 全来源。
- 当前 `realtime-google-hourly.service` active；自动升级等待程序 MainPID=0，没有启动新迁移。本轮未停采集、重启服务、修改代理或提交诊断 Google 查询。
- 冻结采集程序请求 `/google/search`，参数为 `format=json`、`extract=0`、`features=false`、`limit=10`、`start=(page-1)*10`、语言 EN、地区 US。
- OpenSERP 是 Google SERP 请求与解析服务。正文提取在 OpenSERP 请求里已关闭，但项目 worker 随后启动 Scrapy 下载候选文章。需要拆开的是项目下游采集流程。
- 上游 OpenSERP 0.8.12 文档的数据模型包含 URL、title、snippet、rank、position、domain、display_url 等。是否每次 Google 查询都有各可选字段，未发送新请求验证，不宣称字段全部非空或支持所有商业服务模块。
- 项目 `SearchResult` 只有 `url/title/engines`；OpenSERP 适配器仅保留这三个字段。生产现有 Web 缓存的两份非空样本字段也只有这三项。摘要和排名没有进入这条缓存/结果记录链路，不能从这些旧记录恢复。
- 审计表保留请求信息与 `result_urls`，已具备新链接计数基础，但不是完整 SERP 结果产品。
- `best_plan()` 仍以 `new_qualified_bodies` 为首排序键，不符合新目标。不能只改排序键就声称已完成纯 SERP 优化，因为 worker 仍被正文阶段占用。
- 现有审计 recorder 失败会被 `except Exception: pass` 吞掉。新流程必须让结果持久化与任务完成关联；写失败不能标记采集成功或消耗任务。

### 独立只读数据库复核

原连续 run 前 14 个精确小时：UTC 2026-10-09 11:02:08.720572 至 2026-10-10 01:02:08.720572。

| 指标 | 数量 |
| --- | ---: |
| 窗口内规范化唯一 Web 结果 URL | 22,486 |
| 排除启动前留存历史后的新增唯一 Web URL | 20,148 |
| 14 份完整小时报告的新链接之和 | 20,148 |

强制只读会话 `transaction_read_only=on`。全窗口集合与逐小时之和一致；实际冻结控制脚本会排除每个小时开始前 registry 已见 URL，不能错误地指称现有小时报告必然重复计数。此结果只覆盖 Web，新来源的全部辅助链接未完整审计，也不是连续 24 小时产量。

## 按新目标重读旧实验

以下为旧正文流水线下的独立完整小时，不是 discovery-only 性能实测。

| 配置 | Web 客户端审计调用 | 有结果调用 | 新 Web 链接 | 新正文（历史参考） |
| --- | ---: | ---: | ---: | ---: |
| OpenSERP，纯 Web，1 页 3 槽 | 838 | 746 | 6,225 | 3,161 |
| OpenSERP，纯 Web，1 页 6 槽 | 883 | 819 | 6,341 | 3,222 |
| OpenSERP，纯 Web，11 页 3 槽 | 829 | 707 | 4,505 | 1,936 |
| OpenSERP，全部来源，1 页 3 槽 | 248 | 205 | 1,579 | 12,475 |
| OpenSERP，正文浏览器回退修复，纯 Web 1 页 3 槽 | 917 | 857 | 5,305 | 2,626 |

纯 Web 1 页 6 槽是留存实验中 Web 新链接的暂优配置，较 3 槽高约 1.86%；单次顺序实验不足以证明稳定优势。全部来源以正文量选优的结论不能迁移到 Web 新链接目标。不同关键词、时间、代理健康与历史基线影响对比。

## 具体改造顺序

### 1. 建立独立 SERP worker

流程：查询任务 → 预约搜索容量和代理 → OpenSERP → 校验 JSON → 保存请求与结果 → 登记新 URL → 领取下一查询。

- 使用显式模式，例如 `collection_mode=serp_only` 或独立 CLI worker，默认不隐式改变旧采集；正文任务将来可订阅 URL，采用独立队列和资源预算。
- SERP worker 不启动正文 Scrapy 子进程，不下载文章，不等待浏览器正文回退、提取或 Outbox。
- OpenSERP 本身获取 Google 页面所需的 Chromium 仍保留；不能因为不抓文章就关闭搜索浏览器或代理。
- 尽量使用长驻 worker，复用 HTTP session/数据库连接和共享容量控制；任务细化到 query/page/locale，失败按预约时间进入延迟队列。
- 搜索结果保存成功后再确认任务完成。结果写失败保持可恢复任务；避免同一请求重试落库被再次计为新链接。

### 2. 保存完整基础 SERP 数据

建议分为请求表、每次请求的结果项表和全局 URL registry。

| 层级 | 关键内容 |
| --- | --- |
| 请求 | request_id、run_id、query、语言/地区、设备（已知才填）、page/start/limit、开始/结束时间、状态、耗时、缓存/重试证据 |
| 结果项 | request_id、类型、原 URL、规范化 URL、title、snippet、上游 rank/position、domain/display_url、可用扩展字段 |
| URL registry | 规范化 URL 唯一键、首次持久化时间、最后观察时间、首次来源 |

保存上游排名定义，不把数组下标冒充全页绝对排名；缺失字段为 null 或缺省，保留已知数据与适配版本。API 对外可统一成 `organic_results`，上游原 envelope 可按允许字段保存用于复核，不能保存代理凭据、请求认证头。

同一 URL 在多个关键词/时点出现时，各次 SERP 排名记录都保留。只有 registry 首次插入成功的 URL 才增加新增量。事务/唯一约束保证并发工作者只计一次；首次时间和统计窗口规则明确冻结。

自然搜索结果为首阶段范围。广告、PAA、知识图谱、AI Overview 等需要实际解析能力与独立类型；当前 `features=false` 且适配器只接收 organic，不能宣称已完整覆盖这些模块。先验证当前 Google 通道可返回的基础字段，不为追求字段数量开启文章提取。

### 3. 修正链接指标与去重边界

- 新主指标为 24 小时首次持久化的全局唯一有效 organic URL。保留原 URL 与规范化 URL，去掉已确认的追踪参数，但保留影响页面身份的 query 参数、路径、分页和站内搜索条件。
- 不因同一域名而合并全部页面，不用正文 hash 合并两个不同 URL，不因正文质量或正文相关性排除 SERP 结果。
- 标题/摘要相关性可作为附加标签或下游筛选，不默默删除基础搜索结果；无标题等不完整记录单独标记解析质量，不编造标题。
- 已知私网、localhost、Google 自身导航链接等不能冒充目标 organic 结果。保留现有地址安全边界；URL 去重所需的规范化不依赖下载目标页面，必要的 Google 跳转解析单独记录开销。
- 每请求返回结果数、窗口唯一 URL 数、历史新增 URL 数分别展示。有效返回搜索包含真实 empty，非空率单独报告；不能把 empty、解析失败、验证码混为同一分类。
- 缓存返回、重复查询、同一个 URL 的多次出现都不虚增新增量；缓存可用于 API 复用，但不能算新的上游调用。

### 4. 优化查询供给与搜索容量

- 先以纯 Web、1 页为基线；正文阶段拆开后，重新测试实际搜索并发，而非继续把旧 `CRAWLER_SLOTS` 当作搜索请求并发。
- 保存全局新 URL 产出/请求、请求耗时、非空率、429/明确 CAPTCHA、代理可用率及 OpenSERP CPU/内存。控制并发需与 OpenSERP 最大进程、共享限速和真实可预约代理容量匹配。
- 不清空旧冷却来制造高峰；验证码与 429 为出口健康信号，服务卡死和连接错误分开处理。仅 `/ready` 不能证明实际搜索健康。
- 查询缓存键包含 keyword、page、locale、provider、适配版本等影响结果的参数；singleflight 和查询去重防止多 worker 同时重复搜同一条。
- 用新 URL / 请求与新 URL / 秒衡量主题、角度、地区切片。为长期表现更好的组合分配更多搜索预算，保留探索比例，避免直接耗尽热门词或无休止拼接近义词。
- 翻页由实际 `has_more`、每页全局新 URL 收益和请求成本决定。先试最多 1 / 3 / 5 页，必要时再试 11 页；连续若干页没有新增即可停止。现有 11 页表现较差不证明所有关键词深页都无价值。
- 当前请求 EN/US。如果目标改为中文 SERP，需明确语言和地区并独立实验，不能把语言混入同一配置后宣称严格对照。

### 5. 对照与验收

第一轮固定为 pure Web、1 页、serp-only，测试共享有效请求并发 3 / 6 / 12（为候选参数，不是已证最优）；不同时变化代理冷却和关键词策略。先完整小时重复对照，以服务饱和与失败率决定是否继续提高并发。

后续在选定并发下测试分页策略和查询分配。使用多个相当、独立的关键词切片，交替运行降低时段偏差；全局历史去重仍保留，因此也报告每次请求的产出与基线，不能给第二组重复词而把去重劣势当性能下降。

最终候选必须连续实测 24 小时，并给出每小时趋势、全局唯一新增 URL、写入缺口、失败率和资源使用。唯一 URL 日量为选优首指标；不能把单小时峰值乘 24 当日产量。

必要验证：同一链接跨请求/worker/小时只计一次；跨关键词排名记录不丢；缓存不虚增调用；写失败不确认任务；缺字段和分页信息不编造；所有 serp-only 成功路径不发目标文章 HTTP 请求、不启动正文浏览器。测试应验证这些行为边界，不只是重复配置值。

## 参考与代码位置

- SerpApi organic 结构：https://serpapi.com/organic-results
- DataForSEO Google organic live advanced：https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
- OpenSERP 固定版本文档：https://github.com/karust/openserp/blob/v0.8.12/README.md
- `realtime/free_google.py`：OpenSERP 请求参数及 organic 字段映射。
- `realtime/discovery.py`：三字段 SearchResult、请求审计、缓存与搜索调度。
- `realtime/crawler.py`、`realtime/worker.py`：任务等待正文采集结束的现有路径。
- `realtime/campaign_store.py`：SERP URL 审计与历史 registry。
- `scripts/google_hourly_series.py`：仍按正文量选优。
- `scripts/google_hourly_control.py`、`scripts/archive_google_hours.py`：历史窗口去重与整小时归档。

公开文档与代码/数据库已核验；未完成商业服务逐项覆盖、价格或 SLA 比较。现有生产响应完整 JSON 没有在检查的证据目录留存可用样本，因此摘要、排名等 Google 上游字段的实际覆盖率仍须在后续授权的真实实验中验证。
