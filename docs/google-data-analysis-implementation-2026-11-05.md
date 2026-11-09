# Google 数据分析网站 v1.0 实施与验收记录

日期：2026-11-05（新加坡时间）

## 已实施

- `GET /v1/google/legacy-observations`：支持 `source`、查询词、域名、UTC 左闭右开时间、状态、国家、语言、设备、页码、`limit` 和 keyset `cursor`；上限 50；SQL 只读；数据库层排除 `customer_private=true`。
- `GET /v1/google/legacy-analysis/requests/{request_id}`：按 request_id 下钻到原始保存条件、字段状态和结果列表。
- `GET /v1/google/legacy-analysis/news`：News 请求、记录、发布者、解析状态、重复 URL 候选和发布时间字段质量。
- `GET /v1/google/legacy-analysis/trends`：Trends 话题、出现次数、相关报道和原始流量标记；不生成历史热度曲线。
- `GET /v1/google/legacy-analysis/quality`：旧库质量与正式观测质量分栏所需的请求、投影、字段完整度和限制说明。
- Google 研究页面：来源筛选、查询词/域名/时间/状态筛选、分页、请求详情、News/Trends 专项分析、旧库质量、当前页 JSON/CSV 导出。
- 左上角品牌改为纯文字品牌块；全站 SearchFlow 品牌区、搜索入口和搜索弹层均去掉放大镜图标，搜索行为保留。

## 真实数据计算（生产只读）

- Web：31,676 请求，29,601 查询词，257,516 条结果，Web 自然排名 257,516/257,516。
- News：8,126 请求，8,108 成功、17 空结果、1 失败；551,494 条新闻记录；0 条已解析出版者链接、551,494 条未解析包装链接；发布时间字段 551,494/551,494。
- Trends：28 请求，280 个话题，778 条相关报道，原始流量标记 280/280；历史数值趋势曲线未覆盖。
- 公开历史请求合计 39,830；分析投影 39,830。生产源表总请求 39,835，其中 5 条为客户私有请求，公开投影不包含它们；当前公开请求与投影均为 39,830。
- 生产采集开关 `continuous_enabled=false`，正文 `content_fetch_enabled=false`；本轮没有发起新采集。

## 验证

- `TEST_DATABASE_URL` + `DATABASE_URL` 指向临时 PostgreSQL：`796 passed, 6 skipped, 211 subtests passed`。
- 新增旧库边界测试：6 项通过，覆盖私有隔离、游标、详情、News/Trends 专项统计和质量口径。
- 独立静态检查：Python compileall、`git diff --check`、生成页面 JavaScript `node --check` 通过。
- 正式浏览器（ego lite）验证：访问门禁、品牌区、无搜索放大镜、真实历史 20 条分页、request_id 详情、News 专项、Trends 专项、质量页和当前页导出按钮。
- 发布后网站容器 `google-workbench-web-1`、`google-workbench-prd-1` 均 `running`，重启计数为 0；每次发布前保存了远端代码/环境备份。

## 已知语义限制

- News RSS 列表顺序不代表自然排名；包装链接未解析时不能归属出版者域名。
- Trends 是 Trending Now RSS 观测，不是关键词历史热度曲线、绝对搜索量或市场份额。
- 旧库缺失设备、精确位置、登录态、缓存状态或源观测时间时显示未知。
- 当前导出按钮导出当前历史分页，完整筛选数据仍使用现有数据集导出入口；分析页报告导出保留当前报告口径。
