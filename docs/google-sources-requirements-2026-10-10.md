# Google Web、News、Trends：需求与项目检查

## 本轮需求

用户明确要求在已有无正文模式中增加 Google News 与 Google Trends。三条来源都只采集结果、链接和结构化信息，正文仍默认关闭；新闻和热点数据不因文章下载失败而被丢弃。普通第三方 RSS 不在本轮范围。

## 检查所得与修改

旧项目已有 Google News RSS 搜索、Trending Now RSS、新闻跳转解析及正文抓取代码。但上一阶段 `run_serp_campaign` 固定只调用 Web，首页固定提交 `google_web`，独立 SERP 数据库与统计也只识别自然搜索结果；仅在请求中增加来源名并不能获得新模式的 News/Trends 数据。

本轮新增 `realtime/google_feeds.py`，复用已有代理选择、代理健康、下载大小限制及数据库查询锁；单独解析新闻和趋势字段，不启动旧新闻后台解析线程，不访问出版社。worker 尊重任务来源，正文模式开启后也先持久化 News/Trends 结构化响应。持续供给支持 `SERP_CONTINUOUS_SOURCES`，生产沿用原供给 run ID、cursor 和关键词起点。

## 面向用户的功能

| 来源 | 用户操作 | 实际返回 |
| --- | --- | --- |
| Google Web | 输入关键词并选中 Web | 自然结果的标题、链接、摘要、Google 排名及可选字段 |
| Google News | 输入关键词并选中 News | 新闻标题、链接、摘要、媒体、发布时间原文/解析值、列表顺序 |
| Google Trends | 选中 Trends；单独选中时关键词可留空 | 指定地区当前热门话题、搜索量原始标记、发布时间、图片及相关报道链接 |

来源可以单选或组合。页面支持来源筛选、请求选择、完整 JSON；展示趋势话题卡片与报道列表，仍保留正文开关和历史正文入口。无效来源或没有选中来源时接口返回 400，不悄悄改成 Web。

News 是 Google News 自身 RSS 搜索，**不是 Google Web 新闻标签页 `tbm=nws` 的商业 SERP JSON 完整替代品**。RSS 列表顺序保存为 `feed_position`；`position`/`rank_absolute` 保持未知，不编造 Google 排名。当前生产新闻地区/语言为 US:en 与 SG:zh-Hans，按查询语言选择。

Trends 是 Google **Trending Now 热门趋势**，当前生产地区 US；`GOOGLE_TRENDS_GEOS` 可配置地区。它不受输入关键词过滤，也未实现关键词历史热度曲线、区域兴趣分布、相关查询等商业 Trends API 模块。搜索量保留 `20,000+` 等上游原始标记，不转成精确数值，不把 RSS 时间冒充报道发布时间。

## 字段与接口

共用响应继续保留 `schema_version`、`source`、`search_parameters`、`search_metadata`、`new_unique_links`。Web 使用 `organic_results`，News 使用 `news_results`，Trends 使用 `trending_searches`（每个话题内有 `articles`）。Google RSS 采集明确返回 `provider=google_rss` 与 `collection_method=google_rss`。

News：`title`、`link`、`snippet`、`source`/`publisher`、`publisher_url`、`domain`、`date`、`date_raw`、`feed_position`、`google_news_link`、`publisher_link`、`link_status`。仅有上游字段才返回值，解析不了的日期为 null，原日期仍保留。

Trends：`keyword`、`feed_position`、`approx_traffic`、`published_at`、`date_raw`、`picture`、`picture_source`、`articles`；报道包含标题、URL、媒体、摘要/图片（上游可能不提供）。没有报道的热门话题仍保存，不伪造链接。

接口：

- `POST /api/local-campaigns`，例如 `{"query":"solar research","sources":["google_web","google_news","google_trends"]}`；只取趋势可提交 `{"sources":["google_trends"]}`。
- `GET /api/serp/requests?source=google_news&campaign_id=...&limit=50`：按来源/任务筛选，列表显示链接条数及趋势话题数。
- `GET /api/serp/requests/{id}`：对应来源的完整 JSON。
- `GET /api/serp/stats`：全局统计及 `sources` 三来源分项。
- `GET /api/collector-settings`：正文开关、支持来源、持续来源与 Trends 模式。
- `POST /api/collector-settings`：仍只修改正文开关，不混淆正文与来源选择。

## 链接、缓存与统计口径

所有三来源的结果网址进入同一个 `serp_urls`，数据库唯一约束保证并发时全局只算一次。不同来源的搜索/新闻/趋势记录分别保留。Trends 只统计相关报道网址，不把热点关键词当成网址。

每来源展示最近 24 小时「本来源首次收录的非历史网址」与「首次发现贡献」。同一网址先在 Web 后在 News 出现：Web/News 都可有来源记录，首次发现贡献只属于 Web，全局只增加一次。三来源的首次发现贡献可相加得到全局新增；分来源网址数可能重叠，不能直接相加。

News 的 Google 包装跳转链接不等于出版社网址。可离线解码时保留原包装地址与目标网址；否则保留 Google 链接，标记 `link_status=google_news_redirect`，`publisher_link=null`。未解析包装地址和其后另见的出版社地址可能仍算两个网址，当前不声称已完成语义层面的同篇新闻去重。正文 hash 不参与链接去重。

News 每查询/地区默认缓存 3600 秒；Trends 每地区默认缓存 900 秒。缓存和查询锁在数据库共享，不会因多个任务同时选 Trends 而逐任务重复请求相同地区。缓存关联到当前任务供查看，但不新增采集请求、不重复增加 URL。请求与 URL 保存必须先提交成功，才能缓存；写入失败回滚、不标记成功。HTTP 错误和无效 XML 不伪装成成功空结果，失败短暂冷却后重试。

## 验证与部署边界

本地最终回归 368 passed、6 skipped、34 subtests passed，含真实 PostgreSQL 的跨来源去重、事务回滚、持久缓存、重试幂等、来源贡献与兼容升级验证。另测试了统计读取中途另一 worker 提交数据，总数与来源贡献仍来自同一快照。冻结生产代码基于原部署，不含待上线正文/hash 迁移；最终冻结包独立测试 33 passed。页面 JS 语法检查及 8 项真实 HTTP 联调通过，未做浏览器视觉验收。

生产包 `state/google-sources-20261010`，沿用 `realtime-google-serp.service`，保持正文 false、原 Web 1 页/6 槽/限速与代理间隔，扩展三来源。迁移仅新增来源列、关联及缓存表，并把原持续供给任务扩为三来源；不删除历史结果、不修改正文。

初版三来源于 2026-10-10 UTC 03:00:58 启动。统计一致性修订保留在 `state/google-sources-20261010/revisions/r2`，使用 PostgreSQL repeatable-read 只读事务统一读取各项计数；修订不改数据库 schema，不覆盖原冻结包，供给 run ID/cursor 继续沿用。

UTC 03:03:06 抽查 5 次 News 成功响应，分别有 26/63/31/78/79 条，共 277 条，全部有摘要、媒体和可解析发布时间；这批 277 条仍为 Google 新闻包装跳转地址，未取得出版社原始网址。不能把 277 条说成已解析的出版社网址。首个 Trends 响应有 10 个话题、10 个搜索量标记、报道链接为 0；另外核验一次新鲜 RSS 的标签结构，并通过正式采集流程刷新一次，保留原响应，取得 10 个话题和 30 条报道（30 条均有媒体）。这次额外核验 RSS 读取单独留有诊断记录，未混入正常采集请求统计；正式刷新保存到请求表。

上线脚本 `scripts/deploy_google_sources.py` 默认只检查，`--apply` 才改变服务。保留旧 SERP 表 custom 备份和旧 Compose；存在 mutation marker 后须检查阶段，不能盲目重跑。真实返回覆盖、服务状态及正文无新增验证以同目录生产证据为准，不按短时产量外推日产量。

最终生产核验 UTC 03:08:00：六个服务 running、重启 0，worker/web 实际挂载与导入 SHA 匹配 r2；其余四个服务身份和重启计数未变。256 个 active 持续任务全部为三来源，run ID 保持 `google-serp-20261010`、cursor 284、offset 90248。正文开关 false、文章进程 0、正文表 332,768 行/max ID 333,185，与切换前一致；自旧正文 worker 停止后新 `fetched_at` 为 0。服务 active/enabled，默认 Compose 与 r2 匹配，首页和三来源成功返回检查通过。

该时点最近 24 小时窗口只包含实际运行以来的留存数据：全局新网址 12,007；首次发现贡献为 Web 4,394、News 7,585、Trends 28，合计一致。Web 数据包含第一阶段积累，News/Trends 开始时间更晚；不能把这些数用于同窗口来源产能对照或外推日产量。News 7,585 包含未解析的 Google 包装跳转地址。

证据：[最终生产核验](../state/google-sources-20261010/revisions/r2/live-final-check.json)、[新闻实际字段覆盖](../state/google-sources-20261010/live-feed-check.json)、[Trends 新鲜响应验证](../state/google-sources-20261010/trends-refresh-verification.json)、[迁移前后计数](../state/google-sources-20261010/schema-result.json)。
