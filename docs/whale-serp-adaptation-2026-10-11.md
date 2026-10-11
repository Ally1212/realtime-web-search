# Whale 数据上传平台适配与验收（2026-10-11）

本轮对接的是当前 Whale/Marsyoo 数据上传平台，沿用服务器已配置的 Collector Key、数据集 `social_media_raw` 和来源 `google_search`。实现 Google SERP 链接模式的采集器协议与持久上传队列；没有把链接摘要当正文，也没有开启自动采集。

## 功能范围

| 功能 | 实现与验证 |
| --- | --- |
| 采集器注册、心跳 | 仅声明 `identity/title`，实际平台注册与心跳通过 |
| 领取任务、进度、完成、失败 | 独立后台线程；真实 PostgreSQL + HTTP 测试平台联调通过；生产领取开关关闭 |
| `keyword_search` | 支持关键词/别名、三来源、地区语言、分页、链接数量和运行时限；现有 Key 允许注册 |
| `backfill` | 参数解析、分页、上传及完成上报的隔离联调通过；真实平台注册 HTTP 403，尚未线上验收 |
| `content_detail` | 只返回本地已有 URL 搜索详情；缺失明确失败，不下载正文；隔离联调通过，真实平台注册 HTTP 403 |
| 平台暂停、恢复、取消 | 心跳控制信号驱动；本地暂停不会被平台重新领取覆盖；测试通过 |
| 网站暂停、继续、停止、重试 | 数据保留；429/网络/5xx 自动退避，永久拒收显式重试；测试通过 |
| 已有数据上传 | 按来源、关键词、任务、采集日期、URL、数量筛选；可保存待上传或加入队列 |
| 采集任务同步上传 | 创建任务时勾选“同时加入 Whale 上传队列”；每批次加入持久队列，上传服从独立开关 |
| 回执与消息可见 | 网站“平台上传 → 数据与回执”查看真实 payload、状态、尝试次数、失败原因和 document/version ID；分页读取 |
| 幂等与并发 | 稳定消息身份、独立 outbox、租约和 `SKIP LOCKED`；过期旧 owner 无法覆盖新回执；单后台 leader |

`since/until` 筛选本地搜索记录的**采集时间**，不是文章发布时间，也不提供缺失的历史正文。`page_size` 映射为 Google 原生每页十条的分页预算；不保证每页必有该数量结果。未支持的任务参数和能力明确拒绝，不默默忽略。Trends 是 Trending Now RSS；News 仍保留未解析的 Google 跳转标记。

## 数据与状态含义

上传保存规范化 URL、真实标题，以及 discovery metadata 中的摘要、排名/Feed 顺序、媒体、来源、查询参数、请求状态、SERP features 和 Trends 话题上下文。正文不填；只有已知且带时区的真实日期才填发布时间。平台任务消息带 `source.collection_task_id`。

`queued` 表示平台收到消息，`accepted` 表示接口接受，`duplicate` 单独计数。网站“平台已接收”和任务“上传流程完成”均不等于业务入库或索引完成。为兼容现有任务接口，`stats.ingested_count` 上报已接收数；最终入库仍需平台查询端验证。多个上传任务可以引用同一 outbox，回执共享，但不会重复发送同一消息。

## 网站操作

1. “平台上传”中，先筛选已有链接，点击“保存待上传”查看实际消息；开启“允许上传已排队的数据”才会发送活动队列。
2. “接收平台任务并采集”会领取并执行新采集任务；它独立于顶部自动采集开关。关闭领取会暂停已领取的平台任务。
3. 任务行可暂停、继续、停止或在失败后重试；“数据与回执”查看实际内容。关闭开关/停止任务不删除历史数据，在途请求允许完成。

## HTTP API

| 路径 | 方法 / 用途 |
| --- | --- |
| `/api/whale/status` | GET：配置可用性、授权任务类型、开关、统计及运行信息；不返回 Key |
| `/api/whale/settings` | POST：`uploads_enabled`、`claims_enabled` 两个布尔开关 |
| `/api/whale/jobs` | GET 分页列表；POST 创建已有链接上传任务 |
| `/api/whale/jobs/{uuid}` | GET：任务及接收/重复/待上传/拒收计数 |
| `/api/whale/jobs/{uuid}/receipts` | GET：`limit/offset` 分页查看消息与回执 |
| `/api/whale/jobs/{uuid}/{pause,resume,stop,retry}` | POST：任务控制 |

新任务 `/api/serp/tasks` 增加 `upload_to_whale: true/false`。启用配置为 `WHALE_SERP_ENABLED=true`，开发默认 false；已有正文 Whale runner 仍关闭。

## 自测证据

- 完整回归：**423 passed、44 subtests passed，无跳过**。使用本机隔离 PostgreSQL（55440）和 Redis（55441），不操作生产历史正文表。
- 生产冻结 worker 对应测试：**65 passed、10 subtests passed**。覆盖 Whale、SERP、真实 frontier 和请求存储。
- 真实平台：Web、News、Trends 各一条已有链接，经新网站 API → outbox → runtime 上传，均获得 HTTP 200 的 `queued` 回执，无正文和伪造发布时间。

| 来源 | document_id | version_id |
| --- | --- | --- |
| Web | `doc_ecfebc6817d2a9bb3de07d049a76a949` | `ver_59cb5c941fd025cb9cf99594b489206e` |
| News | `doc_a82bf959c28baeb10f579f19eeb2db1a` | `ver_677e49f650157da9eee956a65f3f5731` |
| Trends | `doc_aba9d1e87b73c60d9cacbd2d97d8d6cc` | `ver_4b702531d7d737e607d6e09d38e949cb` |

三条 `outbox_event_created=false`，不据此宣称下游业务处理成功。用 Collector Key 查询 `/v1/documents/{id}` 返回 HTTP 401 `api key is invalid`；上传可用，但查询端需要平台提供合适的读取凭据。

隔离联调覆盖真实 HTTP 注册/领取/心跳/429/上传/完成，包含本地和远端控制、失败/重试、幂等、批量上限、分页恢复。没有对生产开启任务领取来测试新采集。

浏览器使用 Playwright 技能，实际操作草稿上传、继续、暂停、停止、实际消息和回执、平台开关保存；桌面 1440px 与手机 390px 检查。自测发现创建表单缺少上传 checkbox，已通过独立 r2 网页冻结修复，再验证保存待启动任务。

本地证据在忽略目录 `state/whale-serp-20261011/` 与 `output/playwright/`；服务器保留对应部署 marker、原包、新包、manifest、暂停基线和 `document-read-verification.json`。

## 生产部署与暂停约束

- 原 worker/web 冻结包复制至 `state/whale-serp-20261011/code/{worker,web}/realtime`；仅叠加 Whale、新网页和已提交的分页 lease 释放修复，不部署工作树历史正文/hash 迁移。
- 网页 r2 独立冻结在 `state/whale-serp-20261011/revisions/r2/code/web/realtime`，worker 保持原新包；所有旧冻结包保留。`current-compose.json` 和根 `compose.override.yaml` 保持一致，开机服务沿用原 current-compose 路径。
- 只新增独立 Whale 表，未执行 `CampaignStore.initialize()`。部署后重复核验：collector settings（含更新时间）、正文、SERP 请求、供给 cursor 与在途数均等于部署前；OpenSERP、代理、PostgreSQL、Valkey 容器未重建。
- 验收结束后：自动采集 false、正文 false、Whale 上传 false、平台领取 false；在途 0，正式纯 Web 请求仍 24,153、新增 URL 114,478、cursor 4083。正文 332,768 行、max_id 333185，最新时间 `2026-10-10 02:30:23.164897+00:00`。

## 尚需平台权限

现有 Key 的授权仅 `keyword_search`；注册另两类返回 HTTP 403 `collector task_type is not allowed`。保持现有授权配置，不能称三类任务全部线上验收。完成剩余线上联调需要平台管理员补齐两类任务权限，并提供可读取 document 的 API Key。正文能力不在当前链接任务范围内，未声明、未开启。
