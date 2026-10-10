# Google SERP 采集需求与本轮实现

> 本文件记录第一阶段 Web-only 版本。用户随后要求增加 News/Trends，最新范围及项目检查见 [三来源需求与实现](google-sources-requirements-2026-10-10.md)。下方“仅 Web”“不运行 News/Trends”属于第一阶段边界。

## 需求结论

把项目的主产品从「搜索后继续下载文章」调整为「持续采集 Google 网页搜索结果，并保存、展示和返回结构化 JSON」。用户说的关闭爬取功能，按本次上下文指正文抓取。正文功能保留为独立开关，默认关闭。

首要指标是每天首次持久化的全局唯一自然搜索结果网址。搜索结果记录本身还包括查询、标题、摘要和排名；它不是目标网页的正文。此次明确聚焦 Google Web，不运行 News / Trends / RSS，不混用辅助来源产量。

## 1. 两种工作模式

| 模式 | 行为 | 默认状态 |
| --- | --- | --- |
| SERP 模式 | 搜 Google → 保存结构化结果 → 历史 URL 去重 → 下一查询 | 开启 |
| 正文模式 | 完成 SERP 保存后，使用原有正文抓取与质量检查路径 | 关闭，明确打开才运行 |

开关名：`content_fetch_enabled`。部署默认 `CONTENT_FETCH_ENABLED=false`；初始化后实际开关存于 PostgreSQL，页面/API 与 worker 读取同一值。重启不能覆盖用户已经保存的开关状态。打开后对后续任务生效；关闭后不再启动正文子进程，并取消正在运行的正文进程组。正文关闭不会关闭 OpenSERP 获取 Google 页面所需的浏览器。

正文开关不改变 SERP 的新链接资格，也不删除旧正文。打开后仍保存 SERP；下载失败、正文太短或重复，不能倒扣已经获得的 SERP 链接。

本轮开关开启时使用原有正文 worker 路径，占用相同任务槽；尚未建立有独立资源预算的正文消费队列。未来如确实需要长期同时采集正文，再将其拆为低优先级消费者。此前未上线的历史正文 hash 修复不包含在本次生产迁移中，旧正文一致性问题没有被声称修复。

## 2. 核心采集流程

1. 有界关键词供给生成英语主题查询；已有任务完成或达到轮换条件后补充。
2. 使用共享搜索并发、持久化来源限速和私有代理预约，调用唯一 OpenSERP Google 入口。
3. 校验请求 ID、Google 引擎、状态、JSON 和分页元数据；明确验证码、限流、超时、解析错误分类。
4. 保存每次搜索的结构化响应，并在同一事务登记全局新 URL。
5. 提交成功才允许更新搜索缓存和完成分页任务；写失败回滚并重试。
6. SERP 模式立即继续查询；正文模式另外进入原有正文处理路径。

SERP 模式不访问文章网址，也不查询文章域名 DNS 来证明可抓取。它仅验证 URL 格式、协议、明显不合格地址；必要的 Google 包装跳转解析仍可能请求 Google 自身。下游真正下载文章时仍执行 DNS 与地址安全检查。

## 3. 结构化 JSON 合同

对外采用常见 SERP 风格字段，优先兼容用途与含义，不声称是 SerpApi 或 DataForSEO 的完整协议替代品。

### 基础自然搜索结果

| 字段 | 含义 / 本轮状态 |
| --- | --- |
| `type` | `organic`，明确区分自然结果 |
| `title`、`link` | 上游标题、目标 URL；无标题保持未知，不伪造 |
| `snippet` | Google 结果摘要，不是目标页正文 |
| `position` | 保留上游 `rank`；未提供则 null |
| `rank_absolute`、`upstream_position` | 保留上游绝对排名及原位置对象；不按数组序号伪造 |
| `page` | 本次请求的页码 |
| `displayed_link`、`domain` | 显示地址与域名；域名允许直接从 URL 提取 |
| `source`、`favicon`、`thumbnail` | 上游提供则保存；未提供为 null |
| `date`、`author` | 上游提供则保存；不能把采集时间冒充发布时间 |
| `sitelinks` | 保留上游站点子链接结构 |
| `snippet_highlighted_words`、`extensions` | 上游提供的高亮词及扩展信息 |
| `rich_snippet`、`rating`、`reviews` | 上游提供的富摘要与评价信息 |
| `cited_by`、`extracted_cited_by` | 上游引用信息，可选 |
| `cached_page_link`、`about_page_link`、`related_pages_link` | 上游可选辅助地址 |
| `redirect_link` | 包装地址与解析后的目标不同时保留包装地址 |
| `normalized_link`、`is_new_url` | 本项目规范化地址与首次发现标记 |

这些字段已经进入适配器、缓存及持久化响应，不保证所有字段每次都非空；上游缺少的数据不能通过添加 JSON 键自动获得。真实覆盖率必须按实际成功响应评估。

### 请求与搜索元数据

- `schema_version`：当前合同版本 `google-serp-v1`。
- `search_parameters`：engine、q、hl、gl、start、num；未知设备为 null。
- `search_metadata`：id、状态、来源提供器、HTTP 状态、错误码、分类、耗时、缓存状态、上游版本及持久化时间。
- `search_information.total_results`：当前未知为 null；不能用当前页结果数代替 Google 估计总量。
- `pagination`：保存真实 page / has_more / next_start。
- `new_unique_links`：本次响应首次登记的全局唯一 URL 数。

目前采用异步采集：提交查询取得任务 ID，再读取搜索请求和已完成 JSON。没有实现商业 API 常见的同步「一条 HTTP 请求等待 Google 后直接返回」接口，也没有实现按查询覆盖所有地区、设备参数。当前默认 EN / US、1 页；中文查询可推断为 ZH，地区仍为 US。参数扩展是后续需求，不能把占位字段说成已支持设备模拟。

### 高级模块与能力状态

| 模块 | 本轮实际支持 | 目标 |
| --- | --- | --- |
| `organic_results` | 已实现字段映射、缓存、保存、返回 | 持续提高实际字段完整度 |
| `serp_features` | 开启上游 features，保留实际返回的 feature 数组 | 作为模块适配与核验依据 |
| `related_questions`、`related_searches` | 独立标准化适配尚未实现 | 首批补充 PAA 和相关搜索 |
| `knowledge_graph`、`top_stories`、`ads` | 独立标准化适配尚未实现 | 按类型解析并保留来源 |
| `ai_overview` | 尚未实现 | 概述文本、引用网址、显示/缺失状态 |
| `local_results`、`shopping_results`、`images`、`videos` | 尚未实现 | 对真实出现的模块定义字段及夹具 |

当前未实现的模块为 null，且 `module_status` 明确为 `not_implemented`。这与「已支持解析，但当前查询没有该模块」不同；不能用空数组宣称已经达到商业服务覆盖率。模块实现后应分别返回 supported-present、supported-absent、disabled、parse-error 等状态。

广告、地图商家卡片等不混入自然结果新 URL 指标；高级模块的引用网址可另设指标。不同服务的数组布局、排名含义和价格模式不同，需求是数据意义可对齐，不是每家所有字段同时返回。

## 4. 去重与计数

- 主指标：连续 24 小时内首次持久化的非历史唯一自然结果 URL；没有运行满 24 小时前只展示实际已观察窗口，不能按小时峰值外推日产量。
- 同一 URL 在多个关键词/页码/时点出现时，保留每次排名记录，但全局新增量只计一次。
- 两个不同 URL 的正文相同，仍然可以是两个新链接；此目标不使用正文 hash 合并地址。
- 去除已确认追踪参数与片段；保留可能有语义的 `ref` / `ref_src`、分页、站内搜索参数和路径。保留原地址以便审查规范化结果。
- 一次性导入留存正文 URL、抓取事件、历史 Web 结果 registry 和发现缓存作为历史基线。无法补回被删除的历史地址；新规范化版本不能假装恢复老版本已经删除的参数。
- 缓存复用不算新的上游请求，也不重复增加新 URL；新缓存 namespace 防止旧三字段缓存冒充带摘要和排名的记录。
- 并发、重复落库由事务和唯一约束保护；同一 request ID 冲突的不同内容拒绝覆盖。
- 真实空结果属于成功返回，单独统计；请求失败不伪装成成功的空结果。

## 5. 对用户可见的功能

- 首页改为 Google SERP：提交关键词、查看请求状态、标题/链接/摘要/排名、首次发现标记及完整 JSON。
- 首页提供正文复选开关，保存到共享数据库。
- 显示最近 24 小时的新 URL、请求数、有效返回和明确 CAPTCHA；历史正文与旧实验页面保留为历史入口。
- API：
  - `POST /api/local-campaigns`：创建查询任务，当前 Google Web。
  - `GET /api/serp/requests?campaign_id=...&limit=50`：搜索请求列表。
  - `GET /api/serp/requests/{id}`：结构化 JSON。
  - `GET /api/serp/stats`：新指标统计。
  - `GET /api/collector-settings`：读取正文开关。
  - `POST /api/collector-settings`：只接受 `{"content_fetch_enabled": false}` 或 true。

接口保持原有服务器本地绑定和 SSH 转发方式，没有开放公网访问。

## 6. 优化与验收

本轮先以 pure Google Web、1 页、6 个任务槽、共享最多 6 个搜索请求、每任务查询并发 1 为生产基线，保留每代理 90 秒间隔与持久冷却。新模式还没完成独立整小时或 24 小时对照，因此不宣称此配置已达到上限。

后续按新 URL / 请求、新 URL / 秒、成功率、重复率、验证码/429、代理可用率和服务器资源评估并发与查询组合。分页先以第一页为基线，再在固定并发下测试更多页；辅助来源需要单独授权范围和审计，不能自动混入。

已验证的行为边界：

1. 正文默认关闭；SERP campaign 不启动 Scrapy 或文章子进程，也不调用 News/Trends。
2. 摘要/排名在序列化、缓存及跨查询合并后不丢失。
3. 多查询和真实并发只登记一次新网址，同时保留全部搜索记录。
4. 写失败整笔回滚，任务不因持久化失败而被成功缓存。
5. 请求落库重试幂等，不接受同 request ID 的冲突内容。
6. 已有历史网址不重新计算；意义明确的 query 参数不随意删除。
7. 开关可通过真实 HTTP API 开启/关闭，重启读取持久值；错误参数和不存在请求正确报错。
8. 未支持模块和缺失字段明确标记，不返回虚构数据。

本地 PostgreSQL 17 隔离测试、代码回归和冻结包兼容测试已执行；浏览器控制环境没有可用浏览器，本轮页面只做了 HTTP、JS 语法与接口联调，未声称完成浏览器视觉验收。生产实际字段覆盖和服务状态另记在部署证据中。

### 本轮实际验收记录

- 旧正文 worker 实际停止于 2026-10-10 UTC 02:30:24，新 SERP 服务启动于 UTC 02:36:47。迁移仅新增 SERP 表、导入历史基线和退役旧任务，没有执行待上线正文/hash 迁移。
- 切换前取得 PostgreSQL custom 备份 1,558,472,478 字节，服务器权限 0600；`pg_restore --list` 成功，未做完整还原演练。历史 URL 基线导入 1,151,654 条。
- UTC 02:38:36 抽查最近成功响应中的 5 次请求，合计 42 条自然结果，42 条都有非空摘要、`position` 与 `rank_absolute`。这只是样本覆盖，不代表全部可选字段都齐全。
- UTC 02:42:27 最终核验：六个服务 running、重启计数 0；worker/web 挂载及加载模块 SHA 匹配冻结包；OpenSERP、proxy-relay、postgres、valkey 容器身份和重启计数未变；正文开关 false，文章抓取进程 0，256 个 active campaign 全为 Google Web。
- 正文表保留 332,768 行、max ID 333,185，与迁移前一致；旧 worker 停止后 `fetched_at` 新增 0。新模式实际已记录 135 次搜索尝试、128 次成功（含真实空结果）、1 次明确 CAPTCHA、951 条历史去重后的新 URL；尚未运行满 24 小时，不外推日产量。
- `realtime-google-serp.service` active/enabled；旧实验监督器与小时归档/资源 timer inactive/disabled。默认 Compose override 与新版本一致，普通 Compose 启动继续使用 SERP 版本；旧自动迁移进程 MainPID 0。
- 最终本地回归：349 passed、6 skipped、34 subtests passed。正文开关开启/关闭在隔离环境验证，生产保持关闭；本地 HTTP/JS 检查通过，临时 HTTP 和 PostgreSQL 已停止。

证据目录：[部署事件](../state/google-serp-20261010/deployment-events.jsonl)、[真实字段样本](../state/google-serp-20261010/live-api-first-check.json)、[生产最终核验](../state/google-serp-20261010/live-final-check.json)、[迁移前后正文与历史基线](../state/google-serp-20261010/schema-result.json)。数据库备份只保留在服务器，没有下载或展示原始内容。

## 7. 本轮交付与后续范围

本轮交付：需求重整、正文开关、无正文 SERP worker、基础字段扩展与保存、URL registry、页面/API、只增 SERP 表的生产部署，以及真实响应检查。

后续商业字段对齐顺序：基础字段缺口 → PAA/相关搜索 → 知识图谱/Top Stories → AI Overview → 地图/购物/图片/视频。每一项都须由实际上游响应或解析器证明，并附 present/absent/error 的测试。没有把这些尚未实现的模块说成已经完成。

参考：
- https://serpapi.com/organic-results
- https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
- https://github.com/karust/openserp/blob/v0.8.12/README.md
