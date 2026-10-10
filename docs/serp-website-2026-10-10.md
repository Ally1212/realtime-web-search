# 三来源采集网站

## 开发前检查

| 能力 | 现状 | 本轮补齐 |
|---|---|---|
| 三来源采集、全局 URL 去重 | 已实现，独立于正文 | 保留 |
| 关键词及来源选择 | 首页可提交并立即启动 | 多关键词、地区参数、保存待启动 |
| 任务操作 | 后端暂停/继续/停止；首页缺操作 | 任务列表、状态、启动/暂停/继续/停止，真实 worker 停止后续请求 |
| 结果查看 | 最新 50 次请求及 JSON | 跨请求结果筛选、分页、任务/来源/日期/关键词/新增筛选、JSON/CSV 导出 |
| 统计 | 全局、分来源请求与新增量 | 明确 24 小时窗口、成功率、失败分类、异常记录 |
| 配置 | 仅正文开关持久化 | 持续采集、来源、关键词、地区、分页、RSS 间隔保存并被 worker 使用 |
| 页面 | 单页简单列表，无视觉验收 | 总览/任务/结果/设置，桌面及移动验证 |

## 页面和流程

总览显示最近 24 小时全局新增唯一 URL、真实请求数、成功率、异常及三来源贡献。分来源链接可重叠，首次发现贡献之和才等于全局新增。

任务页填写一行一个关键词、来源与适用地区参数，选择“创建并启动”或“保存待启动”。纯 Trends 不要求关键词。任务列表默认手动任务，可切换持续供给任务；任务可启动、暂停、继续、停止，查看其结果。暂停/停止允许已发出的请求完成持久化，阻止后续请求；保留既有数据。

结果页查看网页、新闻、热门话题，按来源、任务、关键词、新链接、采集日期筛选与翻页；请求页可筛选失败、查看完整 JSON。导出当前筛选的全部记录为 JSONL 或 CSV；每项保留结构化 JSON，缺失值保持 null。

设置页管理正文（默认关闭）、持续采集总开关、持续来源、可选自定义关键词、适用地区、Web 页数、News/Trends 缓存与重采间隔。保存写入 PostgreSQL；下一轮 worker 读取，供给循环至多 30 秒读取。关闭持续采集停止自动任务后续请求，手动任务仍按各自状态执行。

## 明确边界

News 使用 Google News RSS，保留无法离线解析的跳转链接和 link_status；RSS 顺序不称 Google 排名。Trends 为 Trending Now RSS；关键词历史曲线、商业高级模块、第三方 RSS 未支持。Web 语言目前 en/zh，地区发送给 OpenSERP；News 地区采用可用 edition 列表。上游可能因地区不可用返回失败或空结果，不伪造字段。搜索和 RSS 获取 Google 数据所需的网络访问仍运行；正文开关只控制目标文章下载。

## 设计

采用采集工作台：白色 #ffffff、蓝灰底 #eef3f8、深蓝文字 #20334c、操作蓝 #245ab5、失败红 #b33343、成功绿 #20745a。字体使用系统中文无衬线，数据按表格对齐；左侧导航、顶部运行提示、主区任务/结果密集表格。突出三来源各自的数据语义，避免把热点话题数当链接。手机导航横排，表格可横向滚动，表单自动单列，键盘焦点明确。

## 完成记录与验收（UTC 2026-10-10 04:16）

网站已部署到原入口 `http://127.0.0.1:8093`（通过 SSH 本机转发访问）。最终冻结包为 `state/serp-website-20261010/revisions/r2`；Compose 与 systemd 均固定该路径，服务 active/enabled。六个服务运行、重启计数 0；其余四个服务容器 ID 未变化。上线前独立 SERP 备份 `state/serp-website-20261010/before-website-serp.dump` 保留在服务器，已核验目录清单，未宣称实际恢复演练。旧包保留，部署脚本发现 mutation marker 后不会自动重跑。

完整回归 **380 passed、6 skipped、34 subtests passed**。6 个跳过项为未配置隔离 Redis 的队列集成测试；生产任务使用真实 Valkey 队列完成创建/启动/暂停/继续/停止/重新启动，覆盖本轮优先入队路径。最终冻结包独立测试 **66 passed、10 subtests passed**，验证其独立使用原生产 CampaignStore，不依赖工作树中的未上线正文/hash 迁移。JS 语法和 `git diff --check` 通过。

Playwright 对真实生产网站操作：三来源任务保存待启动、启动、暂停、继续、停止、重新启动、查看结果；结果分页及来源筛选；Trends JSONL/CSV 和完整请求 JSON 下载；请求视图导出；来源/成功异常/UTC 日期筛选、重置筛选、异常 JSON；设置修改保存、重载持久化。桌面和 390px 手机布局已截图检查，控制台错误与警告为 0。下载的 Trends JSONL/CSV 均有 10 个话题，完整请求 JSON 保留 10 个话题及报道；请求视图 JSONL 包含完整响应。

真实三来源验收任务 `ae3fb5dd-a66e-4b81-b51f-893ca7d74cca` 查询 `solar energy research`，Web/News 英国，Trends 美国：Web 9 条首次发现贡献、News 71 条贡献；Trends 复用持久缓存，关联原响应，任务贡献不重复计算。Web JSON 的 `gl=gb`，News 的 `locale=GB:en`。纯 Trends 验收任务 `76934296-f54a-465c-a91f-ca31ca9d97e5` 关键词留空，得到 10 个话题的持久缓存关联，新增贡献 0。两个验收任务均已停止，结果保留。

Web 的共享缓存现在也关联到每个消费任务；不增加真实请求数，也不重复增加新链接贡献。没有原始请求关联的旧 Web 缓存不会冒充可导出的完整响应，首次重新采集后建立关联。旧正文/RSS 实验任务不进入新工作台，也不能从新操作 API 重新激活；历史正文入口保留。

设置验收曾临时关闭持续采集并将 Trends 间隔改为 960 秒，worker 有效配置实际读取到 false / 960，自动任务 `runnable=false`。随后经页面恢复 true / 900，并重载检查持久化；News 3600 秒、三来源、内置关键词库、Web 1 页及原 run ID/cursor/offset 继续保留。缓存按原到期时间失效，修改间隔不会删除已保存缓存。

正文全程 false、正文进程 0；正文表 **332,768 行、max ID 333,185、最新 fetched_at 2026-10-10 UTC 02:30:23.164897**，与上线前一致。没有执行历史正文/hash 迁移。

### 需求完成审计

| 用户要求 | 权威证据 |
|---|---|
| 先检查、列出缺口、梳理页面流程 | 本文开发前检查表和页面流程 |
| 创建任务、来源、关键词及地区 | 浏览器创建的两任务和三来源实际 JSON 参数；ConsoleStore 数据库/HTTP 集成测试 |
| 状态、启动/暂停/继续/停止 | 页面真实操作快照；后台状态流转集成测试；两个验收任务最终 stopped |
| 查看筛选网页/新闻/热门话题 | 生产结果视图、Trends 截图、日期/来源/异常筛选快照；结果跨来源分页及无报道话题测试 |
| 完整 JSON、导出 | 浏览器实际下载文件逐项解析；请求导出完整响应；服务端流式遍历全部筛选，非仅当前页 |
| 全局和分来源新增/请求/成功率/异常 | 生产 stats；首次发现贡献之和与全局新增一致；并发去重、缓存复用、事务快照测试 |
| 正文及持续配置保存生效 | 修改后 worker 实际配置、恢复后配置、重载持久化快照；本地正文开启/关闭持久化及取消进程测试 |
| 正文默认关闭、Trends 热门模式 | 生产正文 false、旧正文计数不变、无正文进程；Trending Now 实际话题和完整 JSON |
| 所有按钮真实、未支持明确标注 | 四区操作连接 ConsoleStore/Valkey/持久 SERP；设置页边界说明；无模拟生产数据 |
| 开发、测试、部署 | 全量及冻结包日志、线上 SHA、Compose/systemd 和服务身份检查 |

证据：

- [最终生产核验](../state/serp-website-20261010/revisions/r2/live-final-check.json)
- [配置修改后 worker 实际读取](../state/serp-website-20261010/verification/worker-disabled.json)
- [恢复后 worker 状态与正文计数](../state/serp-website-20261010/verification/worker-restored.json)
- [三来源任务完整响应](../state/serp-website-20261010/verification/browser-task-responses.json)
- [浏览器验收记录](../state/serp-website-20261010/verification/browser-verification.json)
- [桌面截图](../output/playwright/final-overview-desktop.png)、[手机设置](../output/playwright/settings-mobile.png)、[手机任务](../output/playwright/tasks-mobile.png)、[手机结果](../output/playwright/results-mobile.png)

### 新操作接口

- `GET /api/serp/tasks?kind=manual|automatic|all&status=active|paused|stopped|failed&q=...&offset=0&limit=20`
- `POST /api/serp/tasks`：`keywords` 数组（纯 Trends 可空）、`sources`、`options`（`web_region/web_language/news_locale/trends_geo`）、`start` 布尔值。
- `POST /api/serp/tasks/{id}/start|pause|resume|stop`：校验状态；start/resume 优先放入真实队列。
- `GET /api/serp/results`、`GET /api/serp/browse-requests`：`source/campaign_id/q/new_only/status/since/until/offset/limit`。日期按 UTC 解释，since 含、until 不含。
- `GET /api/serp/export?mode=results|requests&format=jsonl|csv`：同样的筛选，忽略分页，流式导出全部符合条件的留存记录。请求导出包含完整 response，CSV json 列保留结构化信息；对表格公式前缀做文本保护。
- `GET/POST /api/collector-settings`：正文、持续开关、来源、自定义关键词、默认地区、Web 页数、News/Trends 间隔。数据库持久化，worker 每批次读取；非法字段拒绝。
- 原 `/api/serp/requests` 和 `/api/serp/requests/{id}` 继续保留。

## r3：停止自动采集、数据优先（2026-10-10）

用户要求关闭自动采集并让数据直接可见。UTC 09:03:31 保存 `continuous_enabled=false`，最后在途请求 UTC 09:03:53 完成，总请求数 15,260，此后保持不变；部署默认自动开关改为 false，数据库状态持久保留。正文仍为 false，pages 332,768 行、max ID 333,185、最新正文 UTC 02:30:23 不变。

当前 Compose/systemd 冻结 r3。r3 从 r2 复制代码，只替换网站页面与 ConsoleStore，未执行 schema 初始化或任何正文/hash 迁移；保留旧 override 和 systemd 文件，部署失败可回退。其他四个服务容器 ID 未变。

首页展示最新 10 条真实采集数据：来源、查询关键词、采集时间、可点击标题、链接、摘要、请求 JSON。顶部按钮在四页可直接开启/关闭自动采集；按钮只更新该字段，不覆盖其他设置或未保存表单。关闭保留所有记录、阻止自动任务后续请求，手动任务单独控制。自动任务列表在总开关关闭时不再显示为运行中。统计/异常默认折叠；结果高级筛选、设置高级参数折叠，原功能保留。

网页采用不计精确总数的快速分页 (`count_total=false`)，返回 `total=null` 和真实 `has_more`。首页/未按结果字段筛选的列表仅按时间读取所需请求，跳过空响应，正确支持页边界落在单响应内部。页面不再等待遍历约 60 万条结果后才显示数据；原 API 默认精确计数和导出全部筛选记录未改。

完整回归 **381 passed、6 skipped、34 subtests**；冻结包 **109 passed、18 subtests**。新增集成测试比较快速分页与完整查询，覆盖空响应、异常请求、无报道话题、来源、任务、日期、关键词、新链接及页边界；HTTP 快速模式已验证。JS 语法与差异检查通过。生产首页、第二页、News/Trends、关键词筛选实测约 0.4–0.5 秒返回，设置及请求查看 HTTP 200。真实 Chrome 已显示关闭状态和实际数据，并打开完整请求 JSON；未为了验收开启自动采集。

证据：`state/serp-simple-20261010/full-tests-final.log`、`frozen-tests-utf8.log`、`live-api-check.json`；部署核验在 `state/serp-website-20261010/revisions/r3/live-final-check.json`。
