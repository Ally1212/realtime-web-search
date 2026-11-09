# News / Trending Now 完整交付准备（2026-11-06）

本阶段补齐本地候选合同、公共历史请求读取、固定版本核验工具和逐来源真实历史样本导出。没有新搜索、上传、注册或平台权限变更；没有部署生产。两个来源的正式平台完整快照交付和读回仍未验收。

## 合同与边界

`realtime/google_feed_delivery.py` 只生成候选数据，不生成可发送的 `whale.ingest.v1` 消息，不选择 Dataset、content_type 或 provided_capabilities，不接自动 outbox。完整列表不能被当成某一篇出版者 article。

| 来源 | 候选 schema | 保存的语义 | 身份提案 |
| --- | --- | --- | --- |
| Google News RSS | `google-news-run-envelope-1` | 原请求条件、状态、源响应、标题摘要、publisher、RSS 日期依据、未解析 wrapper、列表与 occurrence 顺序；自然排名保持 null | `google-news-run:<request UUID>`，事件包含 source 和同一 UUID |
| Google Trending Now RSS | `google-trending-now-run-envelope-1` | 话题与原始 approx_traffic 字符串、独立关联报道及 parent_topic_id、顺序、未知与非法记录；numeric_trend_series 保持 null | `google-trending-now-run:<request UUID>`，事件包含 source 和同一 UUID |

候选始终标记 `admission_status=candidate_not_approved`。schema 和身份都是本地版本化提案，不能视作 WHL-GO-02 的平台签核。平台若要求不同承载位置或身份规则，须另定版本和兼容方式，不修改既有固定版本证据。

同一请求重试得到相同候选、object/event key 和 hash；同条件、同URL、同数值的新请求使用新 run/event，全部 occurrence 保留。URL 对象身份不替代出现身份。未知字段与非法列表项经凭据过滤后留存，错误/零结果不会丢弃或补造源时间。

`prepare_public_feed_run()` 读取当前公开历史请求，使用 read-only 事务、参数化 source/request 条件和私有 campaign 排除。纯构造函数要求调用方保证传入公开行，不自行声称执行过数据库权限核查。读取候选不访问 Google 或 Whale。

`validate_feed_payload()` 从保留的 source_response 重新生成标准 observation，完整核对字段、顺序、时间、hash、父子关系与状态；不能只核对数量。`validate_feed_candidate()` 另核对 proposed object/event key、source、payload hash 和候选状态。

## 固定版本核验准备

`WhaleReadbackClient.read_full_feed_run(target, expected_candidate=...)` 要求明确的来源绑定候选与 document/version/Dataset。核对文档 source/object ID、固定版本身份、完整 payload、raw_payload_hash 与全部 topic/occurrence 字段。当前 discovery 元数据不能代替固定版本缺失的 payload。错误来源、URL归并对象、缺失/修改的 payload、乱序或 hash 不匹配均拒绝。

返回 `candidate_contract_only=true`；`run_item_schema_verified=false`、`terminal_receipt_verified=false` 继续保留。该读方法不提交数据，也不使本地候选获得正式 ReaderAdmission。本阶段只用隔离响应验证此方法，未对 News/Trends 调用完整快照真实读回，不能称平台已保存这两种候选。

## 操作员工具

数据库与 Consumer Key 只通过既有环境变量读取；异常输出不含 DSN、Key 或原响应。候选文件600，先私有写入并 fsync，再以原子且不覆盖的方式发布。失败不发布部分目标。运行前仍需确认来源用途、保留期限与操作者权限。

```sh
.venv/bin/python -m scripts.google_feed_delivery_probe prepare \
  --source google_news --request-id <已有公开请求UUID> \
  --output /已有私有目录/news-candidate.json

.venv/bin/python -m scripts.google_feed_delivery_probe prepare \
  --source google_trends --request-id <已有公开请求UUID> \
  --output /已有私有目录/trending-now-candidate.json

# 仅当已有获授权写入的完整候选固定版本时运行；不是上传命令。
.venv/bin/python -m scripts.google_feed_delivery_probe verify \
  --candidate /已有私有目录/news-candidate.json \
  --target /已有私有目录/固定版本目标.json \
  --base-url https://www.marsyoo.com
```

target JSON 是 `ReadbackTarget` 对象，必填 document_id/version_id/dataset_id/source_platform；可另填预期 hash。Consumer Key 缺失、source 不匹配、候选损坏或平台返回缺失时退出2，输出 incomplete。不重试上传，不触碰旧9条 queued。

## 独立真实历史样本

生产 Web 容器只读获取两个 News、两个 Trending Now 已存公开请求，并调用部署中的标准读取函数 `read_public_request(..., normalized=True)`。这是实际旧库读取函数核验，不是公网 HTTP/API 重验。然后把四条原始行加载到本轮独立 UTF8 PostgreSQL schema，在本地运行新候选准备、验证和三出口导出；结束删除该测试 schema。

| 来源 | 请求数 | 完整 occurrence | 话题 | 核对结果 |
| --- | ---: | ---: | ---: | --- |
| News | 2 | 186 | 0 | 标准读取与候选 observation 逐字段相同；源响应 hash 相同；JSONL/嵌套CSV/occurrenceCSV 完整校验通过 |
| Trending Now | 2 | 60 | 20 | 标准读取与候选 observation 逐字段相同；全部话题/报道关联保留；三出口完整校验通过 |

样本为现有历史请求，不是新采集，不覆盖全部国家、语言或 RSS 形态。两个实际样本包均 complete_scope=true，含义仅为这次隔离库装载的两条来源请求全部导出，不代表全生产库导出。没有把合成数据冒充真实历史，也没有把历史导出冒充 Whale 交付。

证据前缀 `state/google-data-closure-20261106/feeds/`：

- `public-samples.json`：经凭据过滤的生产公开原始行及部署中标准读取结果，600，仅本地证据。
- `real-sample-roundtrip.json`：逐请求来源、结果数、话题数、读函数一致性、hash、去重统计和候选文件索引。
- `google_news-export/manifest.json`、`google_trends-export/manifest.json`：各来源两请求固定本地快照、计数和文件校验。
- `red.log` / `green.log`：缺实现时 RED；9项新增测试 GREEN。包括源混淆、乱序、错误父子/时间/rank/hash、缺失固定 payload、私有排除、Key缺失与秘密不回显、文件不覆盖。
- `targeted-final.log`：50项关联测试通过，0跳过。

## 对应总需求与后续门槛

| 需求编号 | 本轮实际完成 | 未完成的完整验收 |
| --- | --- | --- |
| GO-SRC-007 / GO-POC-05 | News 保存 publisher、RSS 时间依据、wrapper 状态和列表顺序；两个真实旧请求本地往返 | 出版者URL解析/独立金标、备用源、许可、七天召回/时延 |
| GO-DAT-002/003/004、GO-ID-001～003 | 按来源保留 run、独立 occurrence、URL、topic/child 与版本化候选事件；隔离负例和真实样本保存/导出一致 | 正式所有源/形态的真实结构样本、条件/时区金标、多次新运行端到端 |
| GO-ING-001、WHL-GO-02/03/05/06 | 完整候选与严格固定版本核验方法准备完毕 | 平台批准 schema/content_type/Dataset/质量/用途/期限；真实上传终态和 Consumer 独立完整读回 |
| GO-API-108、GT-11 | 两来源独立样本三出口逐字段/顺序一致；断开上传的操作员工具 | 公网 API 全条件/私有负例重验、百万行、性能和长期清除策略 |
| GO-AC-004、GT-07 | 不启动未经授权运行 | 连续七天和至少200有效P95样本需明确运行范围与授权 |

下一步优先完成百万行隔离导出工具与运行计划、旧 per-link 同值新观测事件缺口的隔离修复；平台批准/真实批量上传和连续采集授权继续保留为外部依赖。

## 最终回归与清理

本轮独立 UTF8 PostgreSQL/Redis 全回归 **856通过、0失败、0跳过**，86.172秒（`full-regression.log`）；编译与 `git diff --check` 通过。仅本轮使用的数据库与 Redis 已停止；未启动 Docker、未操作其他任务服务。生产代码、部署与开关均未修改。此回归不替代真实平台完整快照验收、七天或百万行。
