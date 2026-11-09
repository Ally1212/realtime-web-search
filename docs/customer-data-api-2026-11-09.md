# SearchFlow 数据 API 接入

SearchFlow 提供网页、新闻、当前热点和图片元数据的异步搜索，以及已有数据的完整读取、导出和质量统计。网站已有的公开历史分析、监测、管理任务和平台上传也有对应接口。未接通的外部数据域不会用空结果冒充已完成。

**当前状态：本轮新增接口已开发与测试，待最后统一发布。** 曾提前发布的八个 API 文件已恢复原版，三个本轮索引已撤回；之前的 News 真链接等已上线功能保留。下文描述候选版本，不能将其中新增路径当成当前正式站已开放。

## 凭据与入口

正式入口为 `https://searchflow.20-150-145-162.sslip.io`。客户请求同时带：

```text
X-SearchFlow-Access-Key: <站点访问凭据>
Authorization: Bearer <该客户的密钥>
```

不要把管理密钥发给客户。浏览器已有访问会话可以通过站点门禁；服务器集成应使用上述请求头。完整接口目录在 `GET /v1/openapi.json`，来源及限制在 `GET /v1/capabilities`，字段目录在 `GET /v1/data/fields`。

## 发起新搜索

```http
POST /v1/search
Content-Type: application/json
Idempotency-Key: customer-order-001

{"query":"solar energy","source":"google_web","region":"US","language":"en"}
```

返回 202 与任务 ID 后，轮询 `GET /v1/jobs/{id}`。202 仅表示接单；成功、失败及取消以任务终态为准。

成功后，读取 `GET /v1/jobs/{id}/data` 得到该客户自己的新版标准数据；或者在原详情加 `?field_contract=prd-v1`，从 `data_record` 读取增强结果。完整下载使用：

```text
GET /v1/jobs/{id}/export?profile=full&format=jsonl
GET /v1/jobs/{id}/export?profile=full&format=csv
```

旧下载默认 `profile=compact`，保持原三来源 7 列、图片 14 列。JSONL 的完整模式按请求返回标准记录；CSV 完整模式包含 request、result、topic、feature 四种记录行。筛选 `record_type=result` 可以取结果明细；`record_json` 保留无损结构。

`GET /v1/jobs` 支持 `limit`、`cursor`、`query`、`source`、`status`。`GET /v1/jobs/{id}/progress` 返回等待、重试、缓存与成功进度。`POST /v1/jobs/{id}/pause`、`resume`、`cancel` 控制本人的任务。暂停不延长既有 15 分钟任务期限；已发出的请求仍可能完成。Serper 不支持暂停，且当前未配置。

批量创建使用 `POST /v1/search/batch`，必须带不超过 100 字符的 `Idempotency-Key`：

```json
{"searches":[{"query":"solar energy","source":"google_web"},{"query":"solar energy","source":"google_news"}]}
```

每批 1–20 项；仍受客户并发和额度约束。整批保存或整批回滚，不会为了批量功能绕过限额。同键同参数返回原任务；同键变更任何一项均冲突。网络结果不明时先查任务，不自动换键重发。

## 读取已有数据

```text
GET /v1/data/requests?scope=own&source=google_news&limit=20
GET /v1/data/requests?scope=public&source=google_web&query=solar%20energy&query_match=exact
GET /v1/data/requests/{request_id}
GET /v1/data/requests/{request_id}/items?limit=100&offset=0
GET /v1/data/requests/{request_id}/topics
GET /v1/data/requests/{request_id}/features
```

列表使用 `next_cursor` 翻页，`has_more=false` 表示当前筛选已读完。items/topics/features 是单请求内分页，使用 `next_offset`；这些记录属于固定请求，不是全库搜索分页。

数据范围：

| scope | 含义 |
| --- | --- |
| own | 本客户任务关联的数据，列表和快照默认值 |
| public | 已有公开历史；“公开”仍受站点访问门保护 |
| accessible | 公开历史加本客户数据，详情默认值 |

无论如何设置 scope，都不能读取其他客户私有数据。历史上误关联的其他客户私有缓存也不构成读取权限。

筛选支持 source、query、domain、from、to、classification、country、language、device、page。from 含边界，to 不含边界，时间须含时区。`query_match=exact` 使用精确关键词；默认 contains 为包含查询。`new_only=true` 仅选择记录了首次新增链接的请求，不会删除同请求中的重复出现记录。domain 是保存目标 URL 的精确主机名，图片按图片 URL 主机，不能当成注册域或图片来源网页主机。

新版默认 `field_contract=prd-v1`。`legacy` 保留旧结构。兼容入口 `/v1/google/legacy-data` 默认仍为 legacy，且只读公开历史，不能读取客户私有任务。

## 下载筛选后的整批数据

```text
GET /v1/data/export?scope=own&source=google_news&format=zip&max_requests=1000
GET /v1/data/export?scope=public&source=google_web&from=2026-11-09T00:00:00Z&to=2026-11-10T00:00:00Z&format=jsonl
```

导出在一个只读数据库快照内生成，完成并校验后才开始发送。默认 1,000、最多 10,000 个请求，原始 bundle 总量上限 128 MiB；超大小失败，不发布半成品。最多同时处理两个导出，每个后台进程独立限额。

ZIP 包含 `full.csv`、可独立校验的 `bundle/requests.jsonl`、`requests.csv`、`occurrences.csv`、`manifest.json`，以及完整 CSV 的 hash。`record_json`、`source_fields_json` 和增强字段 JSON 保留嵌套内容；CSV 普通显示列做公式转义。

实际 Web 样本的完整 JSON 单元格超过 Python CSV 默认 128 KiB 限制；机器完整交付优先使用 JSONL 或 ZIP。Python 读取完整 CSV 时可设置 `csv.field_size_limit(64 * 1024 * 1024)`，普通表格软件可能限制超长单元格。

达到 max_requests 会明确标记截断。所有格式检查 `X-SearchFlow-Truncated`；ZIP 清单同时记录 `truncated`。截断时缩小来源、时间或关键词范围，不能把它当作全量交付。JSONL/CSV 不携带 ZIP 内的独立校验清单，需审计交付时优先选 ZIP。

## 字段与质量

`GET /v1/data/fields` 列出请求、结果、增强字段、富结果及完整 CSV 列名。增强 run 字段区分 value、availability、basis、measurement、missing_reason。

`GET /v1/data/quality?scope=own&sample_limit=100` 返回筛选下的准确请求/成功总量，以及最新样本的字段提供数、未解析新闻链接数、结果出现数、样本去重 URL 数。最多抽样 1,000 个请求；抽样不足全量时标记 truncated。字段提供数的结果分母为 sample.occurrences，请求字段分母为 sample.requests，不把样本唯一 URL 当作全历史新增贡献。

未知作者、canonical、设备、真实源时间、缓存年龄等保持未知。News RSS 时间不代表已核验出版者页面；Trends 当前热点不代表历史数值曲线；图片元数据不代表下载了原图；普通模块链接不自动成为 AI 引用。

## 其余网站功能

| 功能 | 接口 |
| --- | --- |
| 首页公开历史汇总 | `GET /v1/analytics/summary` |
| 数据总览、每日、域名、关键词 | `GET /v1/analytics/overview` |
| 排名、竞品、页面变化 | `GET /v1/analytics/rankings` |
| 上述分析下载 | 对应接口加 `format=csv`，各表以 section 区分，JSON 保留全字段 |
| 监测创建和列表 | `POST/GET /v1/monitors` |
| 监测启动、暂停、报告 | `/v1/monitors/{id}/start`、`pause`、`report` |
| 用量与客户密钥 | `/v1/usage`、`/v1/keys`、`/v1/keys/{id}/revoke` |
| 公开历史平台交付证明 | `GET /v1/data/delivery`，只读缓存，不触发平台上传或读回 |
| 手动多关键词、组合来源任务 | 管理接口 `/api/serp/tasks` 及 start/pause/resume/stop/progress |
| 全站设置 | 管理接口 `/api/collector-settings` |
| Whale 队列、状态、回执与控制 | 管理接口 `/api/whale/status`、`settings`、`jobs` 及详情操作 |

分析与网站相同，仅包含公开历史，不把其他客户私有数据纳入总览。私有任务使用自己的完整数据、质量统计或监测报告。

管理接口保留原权限，写操作需要管理密钥；客户密钥不能开启全站采集或操纵平台上传。图片仍不进入旧 Whale 上传协议。公开数据读取也不证明来源允许任意再分发。

新版研究目录 `/v1/google/openapi.json` 中的数值 Trends、GSC、GA4、Ads、完整 AI 等须以 `/v1/google/capabilities` 的真实来源准入为准；目录中有路由不代表来源已接通。新版 `/v1/google/monitors` 仍是另一套未连接调度器的研究配置，不替代上表已运行的 `/v1/monitors`。

## 运维

新增 API 不运行历史正文/hash 迁移、不启动采集、不上传平台、不修改来源权限。只读 API 的网络请求会更新密钥使用时间及读取限流计数。

可选性能索引由 `scripts/customer_api_indexes.py --apply` 显式安装，使用当前项目 DATABASE_URL，采用 CONCURRENTLY，锁等待 3 秒、单语句 120 秒；不自动删除无效索引。它只新增三个可重建索引，不修改数据表结构和源记录。

## 验证与发布状态

- 完整本地隔离回归 1,087 passed、333 subtests、0 skipped；生产实际冻结代码组合 53 passed、7 subtests。代码与测试证据在 `state/customer-api-20261109/`。
- 临时发布期间用真实客户凭据只读验证四来源：新版字段、74 列 CSV 和 JSONL 逐字段一致，旧下载保留 7/14 列；进度、结果/富结果子项、查询分页、公开分析及 CSV 均成功。ZIP 的两请求截断样本通过 bundle 校验与完整 CSV hash 校验。成功搜索创建、暂停/继续、批量原子回滚在隔离环境验证，没有用生产新搜索冒充只读验收。
- 无客户凭据 401，客户写全站设置和 Whale 设置 401，私有原请求通过公开兼容接口读取 404。保留实际读取耗时，不宣称长期 SLA 或全库模糊查询已无慢读取。
- 仓库末尾要求暂缓部署。本轮提前发布后已撤回八个文件及三个索引，最后统一发布前不得自动重放已带 marker 的安装脚本。回退保护 `web.py`、`serp_page.py`、`serp_console.py`、`google_http.py`，未撤销之前 News 真链接功能。
