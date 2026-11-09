# Google PRD v1.0 实施与验收账

原始需求：`https://knowledge.xinzhiaigc.com/doc/google-v10-nCI82AHL62`，
文档标注 2026-11-02 / v1.0，2026-11-04 通过 ego(lite) 读取全文。
全文、结构化表格和读取证据保存在本地 `state/google-prd-v1-20261104/`。

## 范围与不可变约束

- 保留完整 G0～G3、WP-GO-01～08 范围；基础库通过不等于产品完成。
- 本地已存在的修改为任务前基线，不回退、不直接覆盖生产差异。
- 前置访问 Key、客户/管理员鉴权、私有数据隔离必须保留。
- 不自行恢复自动采集、正文、Whale 上传/领取、已有监测。
- 开发授权不等于采购、付费调用、创建账号/Key/OAuth、迁移生产存量授权。
- 没有许可、资产或平台准入的域必须阻断；不生成假数据、假正文或假完成回执。
- 不能用单元测试替代真实样本、人工金标、独立读回、性能与连续七天验收。

## 执行计划

| 步骤 | 工作包 | 交付 | 证明方式 | 当前状态 |
| --- | --- | --- | --- | --- |
| 1 | 01 | 全文需求账、来源/用途门禁、公共 schema、身份、指标/oracle、OpenAPI | 逐编号映射、RED/GREEN 契约测试；批准项单列 | 进行中 |
| 2 | 02 | SERP/News/Trends adapter、稳定事件、spool、背压、终态/独立读回 | GT-01～07；真实 1,000 事件、30 任务另验 | 进行中：隔离 spool/回执状态机已实现，真实 adapter/读回未接通 |
| 3 | 03 | 关系、manifest、22 指标与基础洞察 | GT-08～10；许可、固定 scope、金标、独立复算 | 未完成 |
| 4 | 04 | 46 组域 API、工作台、监测、确定性报告/导出 | GT-11/15；五条流程；百万行与三端一致 | 进行中：多组只读分析/报告/导出本地契约已实现；工作台、真实 E2E 与百万行未完成 |
| 5 | 05 | GSC/Bulk、GA4、Ads、Merchant 独立连接/对账 | 对应准入和 GT-03/04/14/16 | 未完成；缺资产/用途核准 |
| 6 | 06 | Local/评论、AI 重复、趋势预测 | 独立许可、POC、GT-10/13/16 | 未完成；条件域关闭 |
| 7 | 07 | 预算、质量、SLO、清除/撤权/恢复、全量回归 | GT-06/07/12/14；七天证据、恢复演练 | 未完成 |
| 8 | 08 | AI 报告、跨平台证据 | GT-17；有依据数字、引用、用途与注入负向测试 | 未完成 |
| 9 | 全部 | 发布、逐条完成审计 | 备份、最小部署、线上验收、浏览器清理；所有出口逐项证明 | 未开始 |

执行主体为本任务编码代理；产品/平台/数据验收人与依赖负责人未提供，不能代签。
没有依据估计人日、采购价或上线日期。所有数值门槛沿用 PRD 提案，不声称已获业务批准。

## 关键外部依赖

1. D-01～09 的明确决定，特别是市场/必达域、规模、预算、来源用途、资产及 oracle。
2. WHL-GO-01～10：正式 schema、数据集、质量 profile、私有/到期/历史与独立读取权限。
3. 合格 SERP/AIO、数值 Trends 来源；当前 Trending Now RSS 不等于数值趋势时序。
4. 至少三个真实授权资产及服务专用用途/准入；不以现有管理员凭据替代终端租户验收。
5. 真实金标与七天计划样本；原文引用的中台 GEN-01～18、来源调研和平台验收。

依赖存在不妨碍本地实现与隔离测试；但不能在依赖未满足时宣布对应域上线或目标完成。

## 证据登记

- 任务前 Git 状态：既有 5 个已跟踪文件修改、多个未跟踪产品模块；不回退。
- 原文读取：完整 63,045 字符、47 张表、82 个标题；不把原文中“已完成研究”当工程完成。
- 本轮变更、命令、退出码、测试数与真实剩余项在执行后继续填写。

## 第一实施检查点（不是完成验收）

完整冻结全文：`docs/google-prd-v1-source-2026-11-04.txt`。
逐编号账：`docs/google-prd-v1-requirements.json`，共 273 个编号条目，并列出
正文中的独立强制出口。当前只有 24 项拥有部分本地契约证据，没有任何条目被标为整体验收完成。
后续检查点已更新同一需求账；最新为 70 项具有部分本地证据、203 项仍未验证、
0 项整体验收完成。

| 代码 | 已实现的本地行为 | 证明边界 |
| --- | --- | --- |
| `realtime/google_domain.py` | NFC 保留原词语义；query/variant/report/event 分 namespace；有限数值、Decimal 精度、RFC3339、缺失/真零/测量性质 | 不等于所有实体/schema 已获 Whale 批准 |
| `realtime/google_rights.py` | 来源登记缺项默认拒绝；逐字段用途、保留期限、租户/资产/授权版本、最严派生权限；禁止 endpoint 带凭据 | 没有替任何真实来源签许可，也未接到现网所有读取/任务路径 |
| `realtime/google_metrics.py` | 排名/删失、topK、同尺度增长、close variants、GSC 加权/BQ 零基换算、AI 出现/提及/引用/SoV/首次提及位置 | 只验证确定性公式；不是品牌识别、数值 Trends 或真实 AIO 数据能力 |
| `realtime/google_evidence.py` | 固定 Whale 文档版本引用；来源/API/许可版本与查询权限冻结；使用时重新查权；签名游标绑定租户/筛选/排序/source/manifest | 没有暗建原始库；还没有生产 manifest 存储、真实历史读取和导出 API |
| `realtime/google_ingest.py` | 单表临时 spool；最大 2GB/24h；100 个同步释放的并发提交稳定去重；新 run 保留新观测；CAS lease/恢复；逐事件回执、终态和读回分账；撤权/到期清 payload | 只在隔离 PostgreSQL 调用；没有自动发送、真实 Whale 独立读回或生产迁移 |

测试文件：

- `tests/test_google_domain.py`：26 个合成契约/公式/权限用例。
- `tests/test_google_evidence.py`：9 个 manifest/版本/到期/游标用例。
- `tests/test_google_ingest.py`：11 个真实隔离 PostgreSQL 边界用例；远端回执和读回证明为测试夹具，
  不宣称是生产 Whale 验收。

RED 及多次 GREEN 结果保存在 `state/google-prd-v1-20261104/`。
最终全回归命令同时把默认配置与 TEST 配置指向本机隔离实例，避免后台连接误用默认 5432：

```sh
DATABASE_URL='postgresql://ziheng@127.0.0.1:54404/postgres' \
VALKEY_URL='redis://127.0.0.1:56404/0' \
TEST_DATABASE_URL='postgresql://ziheng@127.0.0.1:54404/postgres' \
TEST_REDIS_URL='redis://127.0.0.1:56404/0' \
.venv/bin/python -m unittest discover -s tests -v
```

执行日志：`state/google-prd-v1-20261104/full-regression-isolated.log`。
最终结果：509 项通过（新增 46 项）、0 失败、0 跳过，耗时 16.770 秒，退出码 0；
没有默认 5432 连接错误。`compileall` 与 `git diff --check` 通过。
本地测试实例只服务此任务，不启动 Docker 或采集 worker。
检查点结束后已停止本任务 PostgreSQL/Redis，`lsof` 确认 54404/56404 均无监听。

线上只读基线：原始请求 39,835、pages 332,768、公开投影 39,830；
持续采集/正文/Whale 上传/领取 false，启用监测 0。前后审计及受保护文件哈希相同；
未发布任何模块、未重启服务、未改存量数据。

### 下一实施顺序与仍缺证据

1. 补正式来源/用途 registry、实体与 OpenAPI；明确 ingestion class 的独立准入、
   Dataset/能力校验、计费/配额/预算及取消/清除链路。
2. 新契约接入版本化 adapter、API/manifest/投影/分析任务与工作台；
   不能仅把纯函数放在仓库里当产品实现。
3. 现网旧 `whale_serp.link_message` 仍使用基于内容的旧事件身份，
   `WhaleSerpRuntime` 也不是本 PRD 的完整终态/读回闭环。本轮没有偷偷替换旧语义；
   后续必须按正式 schema 版本与历史兼容方案接入，不能把本地 spool 测试当修好了现网。
4. 真实 source 许可、数值 Trends/AIO、授权资产、Whale 正式 schema/读取/TTL、
   双人金标、百万行出口、负载、恢复演练、五条 E2E 和连续 7 天证据仍未完成。
5. 各 WP 与 G0～G3 原始范围不变；全部未满足项保留，目标维持 active。

浏览器仍复用本目标的 ego(lite) Space 58 / p1；后续阶段不得新建第二个同目标空间。
最终目标结束时按 ego 官方指南完成 `finish` 和资源核验；本检查点不提前结束该空间。

## 第二实施检查点（不是完成验收）

- 新增持久化版本化 registry：来源、资产 grant、独立 ingest permit、客户 Key 的显式资产/角色。
  API Key 不自动获得租户所有资产；来源/字段/用途/期限、独立 task/dataset/class 均默认拒绝。
- 新 `/v1/google` 接入既有客户 Bearer 与 HTTPS 保护，默认关闭；实现的是能力、市场、
  位置目录、指标定义、连接登记和登记质量的控制面读取。登记不表示 adapter 已连接或 POC 通过。
  未实现的数据/写操作返回 501，不转发旧采集入口。PATCH/DELETE 仅进入新 namespace。
- 冻结 PRD 生成 46 组、68 操作、22 指标目录及 OpenAPI；文档明确
  `implementation_in_progress_not_approved_frozen_contract`，不是 46 组工程验收。
- PG 跨实例限制每租户 5 次/UTC 秒、2 并发；签名分页绑定租户、权限 epoch、筛选、
  来源、排序和页大小。读取组装后复查撤权，响应期限不超过最短字段/资产期限。
- 独立入库准入与每次 reserve 复查当前 permit 已接入 spool；queued、终态和独立可读仍分账。

验证：`state/google-prd-v1-20261104/phase2/full-regression-final.log`，
540 项通过、0 失败、0 跳过，25.202 秒，退出码 0。OpenAPI 经
`openapi-spec-validator==0.7.2` 验证合法；compileall、git diff --check 通过。
测试只使用本任务 54404/56404 隔离实例；无部署、生产修改或采集启动。
真实来源许可、Whale 正式 schema/独立 reader、数值 Trends/AIO、资产、
真实样本及七天出口仍未通过；下一阶段实现可从独立读取重建的 SERP 观测投影。

## 第三实施检查点（不是完成验收）

新增 `google_observations.py`、`google_projection.py` 和隔离测试，接入
`GoogleDomainService(projection=...)` 与既有 HTTP envelope。
实际实现 query/variant/run/item/feature 的选定事实存储、固定版本重建、
查询/筛选/签名分页、精确 URL 排名历史、期限清除、撤权清除及资产限定 tombstone。
源绝对 rank、自然位置、occurrence 顺序分别保留；未知位置不伪装深度外。

重建与查询均需独立 reader，事实不匹配、过期、撤权或并发清除会拒绝响应。
已在隔离 PostgreSQL 检查稳定 run 的并发重试、不同 run 的新观测、
同 URL 多 occurrence、跨租户/Key/资产隔离、权限用途、时间、清除及分页。
HTTP 集成使用明确标注的 fixture reader，不代表真实 Whale 读回成功。
生产未接入 reader，默认仍返回 501；没有将接口目录改标为整体验收完成。

完整设计、内部端口和未完成边界见
`docs/google-observation-projection-2026-11-04.md`。
第三阶段最终测试日志、精确数量和哈希见
`state/google-prd-v1-20261104/phase3/checkpoint.json`；第一、二阶段检查点保持独立历史。

下一步：正式读回契约与来源批准仍单列；可继续实现持久化 manifest、
只读异步分析/导出/确定性报告、固定 cohort 指标与工作台，再完成其余 WP。
普通观测分页不是冻结快照；尚未完成所有 API 参数、指标、生产接线及整体验收。

## 连续实施检查点（不是完成验收）

在第三阶段之上新增 `google_exports.py` 和 HTTP 集成：

- 同步小范围冻结导出，支持 keywords/query-variants/observations/items/features、
  明确字段 allowlist、JSONL/CSV、幂等 Key、内容 SHA256、分页列表和受控下载。
- 每个导出 manifest 固定投影 revision、请求、Key/grant 范围、选定事实和固定 Whale
  version/hash；下载时复查当前源/字段/用途/资产/到期，并独立读取复算 hash。
  过期 410、撤权 403/501、夹具 reader 失败 503，不返回空或旧数据冒充成功。
- CSV 公式注入防护、manifest 篡改检测、100 行上限和 `partial_result` 均单列。
  同步导出、最多 100 行不等于异步/大导出完成；未声称百万行出口或生产后台清理已完成。

详细边界见 `docs/google-observation-projection-2026-11-04.md`。
最新全回归、OpenAPI 验证与哈希将记录在第四阶段 checkpoint。

## 第五实施检查点（不是完成验收）

新增 `google_jobs.py`：冻结 item manifest 上的持久化分析任务、幂等 Key、
租约 worker 状态机、取消、过期和事件审计；实现 `item-coverage` 与
`rank-summary` 两种确定性分析。worker 未部署，queued 明确不当作成功；
未知自然位置不补成深度外，top-k 只按已知位置计算并标注限制。

该项只是 API-040 的本地基础，不是已上线的异步系统；没有进度分块、完整
崩溃恢复、后台定时器或真实数据集验收。导出 manifest 同时固定
`source_path`、筛选条件、方法版本和查询时权限快照；Key 范围绑定
subject/grant ID，而不是 grant 版本，下载仍按当前字段/用途重新核验。
只有内部展示而没有导出用途时不能创建或下载导出；创建和读取成功的分析结果
都必须有当前 `aggregate` 用途，撤权后已成功任务也不得继续返回结果。

最终本地回归为 584 项通过、0 失败、0 跳过，耗时 40.632 秒，退出码 0：
`state/google-prd-v1-20261104/phase5/full-regression-final.log`。
从当前代码重新生成的 OpenAPI 经 `openapi-spec-validator==0.7.2` 验证合法：
`state/google-prd-v1-20261104/phase5/openapi-validation.log`。
`compileall` 与 `git diff --check` 通过。精确文件哈希、需求状态计数和未完成边界
见 `state/google-prd-v1-20261104/phase5/checkpoint.json`。

## 第六实施检查点（不是完成验收）

在第五阶段之上补齐独立 worker 调度与异步分块导出基础：

- `AnalysisJobWorker` 可按租户扫描队列、恢复过期租约、CAS 心跳、记录进度和
  attempts；租约连续过期 3 次后以 `worker_lease_exhausted` 终止。Web 进程仍不启动
  该 worker，queued 不冒充成功。
- `/exports` 超过同步 100 行或显式 `async=true` 时返回 202 queued；独立
  `ExportWorker` 分页读取固定投影，逐行复查 `export` 用途，按块压缩存储并记录
  块 hash、总 hash、行数和进度。状态机覆盖
  `queued/running/partial_result/succeeded/failed/canceled/expired`。
- 新增本地取消扩展 `/analysis-jobs/{id}/cancel` 与 `/exports/{id}/cancel`。
  running 导出在下一块前观察取消；过期、撤权和源读回失败不返回完整文件。
  下载会逐块校验 hash、总 hash，并重新读取 manifest 固定的每个版本。
- 异步 manifest 只保留字段级 evidence、version/hash 和分析所需最小 item 事实，
  不复制完整导出内容或原始响应；item 导出完成后可作为分析任务 manifest。
- OpenAPI 增加明确的分析/导出请求 schema、进度对象及两个取消扩展；仍标注
  `implementation_in_progress_not_approved_frozen_contract`。

最终本地回归为 592 项通过、0 失败、0 跳过，耗时 48.251 秒，退出码 0：
`state/google-prd-v1-20261104/phase6/full-regression-final.log`。
OpenAPI 从当前代码重新生成并通过 `openapi-spec-validator==0.7.2`。

仍不是百万行完成：下载和 hash 校验当前在内存中拼接，未做生产对象存储、HTTP
流式发送、百万行负载/费用/中断续传、cron 部署、真实 Whale reader 或七天验收。
worker 代码可独立运行但没有部署到生产；需求账只把 `GO-API-104` 扩为部分本地契约，
整体仍不完成。

## 第七实施检查点（不是完成验收）

新增 `google_reports.py`、`google_watchlists.py`、`google_monitors.py`，并接入既有
`GoogleDomainService`、HTTP envelope 与 OpenAPI：

- `POST /reports` / `GET /reports/{id}` 从冻结 item manifest 生成确定性报告。
  输出结论、反例、限制、待验证行动、逐项证据、source/license/parser/API 版本、
  source hash、grant 版本、expiry、输入 manifest hash 与报告 hash。使用
  `aggregate` 和 `internal_display` 两种当前用途；撤权、过期、reader/source 失败、
  manifest/结果篡改均失败关闭。没有接入 LLM。
- `POST/GET /watchlists` 与
  `DELETE /watchlists/{id}/items/{entity_id}` 按客户 Key 隔离；创建支持
  `Idempotency-Key`，删除仅移除 membership，不删除源事实。
- `POST/GET /monitors`、`GET/PATCH /monitors/{id}` 保存词/prompt/域/池、source、
  频率、预算和规则。PATCH 使用版本化 `If-Match`；所有新配置固定暂停，
  `scheduler_state=not_connected_no_scheduler`，不写旧 `sf_monitors`、不创建 campaign、
  不启动采集，`enabled=true` 明确返回 `501`。

完整回归为 596 项通过、0 失败、0 跳过，耗时 49.847 秒，退出码 0：
`state/google-prd-v1-20261104/phase7/full-regression-final.log`。
当前代码重新生成 `docs/google-domain-openapi.json`，经
`openapi-spec-validator==0.7.2` 验证为 OK：
`state/google-prd-v1-20261104/phase7/openapi-validation.log`。

仍未完成：报告工作台和 CSV/JSONL 一致性、50 manifest 重放、AI 报告质量/安全、
监测调度与 100 规则回放、真实来源许可、真实 Whale reader/独立读回、生产部署、
五条用户 E2E、百万行负载与连续七天证据。目标保持 active。

## 第八实施检查点（不是完成验收）

为 `GO-API-037` 和 `GO-API-045` 补充本地可解析契约：

- 选中 SERP run 生成稳定 `evidence_id`，投影条目和允许导出的字段均包含该 ID；
  新增租户内唯一索引及旧投影回填。`GET /evidence/{id}` 每次读取都重新核验当前
  grant、用途、期限、固定 source/projection 版本和 hash，并通过独立 reader 重读；
  返回单条许可内证据、版本、grant、相关指标定义和可加性。多行冻结重放仍由导出/报告承担。
- `GET /usage` 仅允许 `manager`。只统计租户本地控制面的分析任务、导出、报告、
  收藏清单、监测配置和 API 读限流；provider usage、费用、币种结算、源额度明确
  `not_covered`，没有计费账本时不返回零费用或猜测金额。

完整回归为 598 项通过、0 失败、0 跳过，耗时 51.777 秒，退出码 0：
`state/google-prd-v1-20261104/phase8/full-regression-final.log`。
从当前代码重新生成 `docs/google-domain-openapi.json`，经
`openapi-spec-validator==0.7.2` 验证为 OK：
`state/google-prd-v1-20261104/phase8/openapi-validation.log`。
`compileall` 与 `git diff --check` 通过。

仍未完成：真实 Whale reader/独立读回、provider 计费账本与额度、报告工作台和
CSV/JSONL 一致性、AI 报告质量/安全、监测调度、真实来源许可、生产部署、五条用户
E2E、百万行负载与连续七天证据。目标保持 active。

## 第九实施检查点（不是完成验收）

新增 `google_domain_analytics.py`，从同一固定窗口、逐 run 重新读回并复算 hash 的
选定 SERP 事实生成只读分析，接入 `GoogleDomainService`、HTTP envelope 和 OpenAPI：
新增 `google_runtime.py`，只有来源、数据集、合同和双批准字段全部显式为真时，
才注入独立 reader 并组装 projection/export/job/report/watchlist/monitor/analytics；
不会因此启动 worker、调度器或采集。缺项、伪造 reader 合同或未获生产批准均失败关闭。

- `/domains`、`/domains/{id}`、`/domains/{id}/pages` 使用显式目标 URL/域名、
  固定 run 分母和可分页证据，不把排名解释为真实流量。
- `/rankings`、`/comparisons` 固定 universe、surface/窗口、分母和成对差异；
  top3/top10 使用真实深度，缓存 run 排除，未知位置不补作深度外。
- `/topics` 与 `/topics/{id}/relations` 只生成“精确归一化查询”主题候选和共享
  URL/域名 Jaccard 关系；明确不是批准 taxonomy，品牌关系为 `not_covered`。
- `/news/observations`、`/news/events` 保留显式 publisher/URL/发布时间。事件是
  exact publisher URL 或明确 publisher+title 的候选聚类，单独报告独立文章与转载，
  不声称人工金标事件真值。
- `/insights/opportunities` 返回 observed-rank-gap、样本/分母和不可判定项；
  CPC、真实需求与因果提升 `not_covered`。`/insights/anomalies` 只接受显式 split，
  返回 before/after、threshold、evidence 和 data/business 分类，不伪造业务原因。
- `/insights/brands` 分离 search/news/AI citation tracks，需求与组合分数不制造。
  `/articles/{id}` 仅返回许可内标题/摘要；正文未进入 typed projection 时明确空值。
- `/ai/answers`、`/ai/citations`、`/ai/visibility` 返回 AI feature presence 和显式
  citation URL/domain；answer text/mention 没进入投影时明确 `not_covered`。

完整回归为 604 项通过、0 失败、0 跳过，耗时 54.772 秒，退出码 0：
`state/google-prd-v1-20261104/phase9/full-regression-final.log`。
当前代码重新生成 `docs/google-domain-openapi.json` 并通过
`openapi-spec-validator==0.7.2`。详细公式、分母和限制见
`docs/google-domain-analytics-2026-11-04.md`。

仍不是生产分析完成：所有数据来自独立 reader 夹具，未接真实 Whale 数据；没有获批
topic/brand taxonomy、数值需求/CPC、AIO/Mode 正文与人工金标。工作台、三端一致、
真实用户 E2E、百万行、七天 SLO、生产部署和 D-01～09/WHL-GO 批准仍未完成。
目标保持 active。

## 第十实施检查点（不是完成验收）

新增 `google_research_ui.py` 并接入现有工作台“Google 研究”只读视图：

- `GET /reports` 只返回签名分页后的报告元数据，不夹带报告正文或证据；详情仍在
  每次读取时重新核验当前 grant、到期、固定 manifest、source reader 和结果 hash。
  同一报告连续 50 次重放保持相同 `report_sha256`，并重新读取固定证据。
- 域名比较和页面下钻返回最近许可内证据；用户可在三次操作内从聚合计数值进入
  单条 `evidence_id`，读取时再次核验权限、版本与 hash。
- 导出、分析任务、报告、实时查询、订阅、监测和收藏清单统一修正 `<` 游标边界，
  翻页不再重复上一页末项；游标继续绑定租户、过滤、排序和权限范围。
- CSV/JSONL 可基于同一冻结语义导出；新增 API、JSONL、CSV 小规模指标复算一致
  测试，避免三端在分母、未知位置或字段转换上分叉。
- 工作台展示数据边界、固定集比较、产出/报告、质量/用量；空态、未准入、过期、
  部分失败、Tab 键盘导航和 390/320px 布局均有本地只读 fixture 证据。

最终全量回归为 616 项通过、0 失败、0 跳过，耗时 64.199 秒，退出码 0：
`state/google-prd-v1-20261104/phase10/full-regression-final.log`。
`tests.test_google_api` 54 项通过。当前代码重新生成的
`docs/google-domain-openapi.json` 经 `openapi-spec-validator==0.7.2` 验证为 OK：
`state/google-prd-v1-20261104/phase10/openapi-validation.log`。
`compileall`、`git diff --check` 和抽取页面 JS 后的 `node --check` 通过。

浏览器证据只来自 ego(lite) Space 84 / Page p1 的本地只读 fixture：
`state/google-prd-v1-20261104/phase10/research-desktop.png`、
`research-390.png`、`research-320.png`、`research-error-390.png`。已验证固定集比较、
证据/页面下钻、产出列表、质量/用量、错误态与 Tab 键盘切换；1440/390/320px
无横向溢出，浏览器控制台无 JS 错误。该结果不是生产页面、真实租户数据或用户 E2E。

仍未完成：真实 Whale reader/独立读回、D-01～09/WHL-GO 批准、GSC/GA4/Ads/Merchant、
数值 Trends、AIO/人工金标、worker/scheduler 部署、百万行、对象存储/流式下载、
五条真实 E2E 和连续七天 SLO。需求账仍只把相关条目记为
`partial_local_contract`，没有任何条目记为完整验收；目标保持 active。

## 第十一实施检查点（不是完成验收）

新增 `google_search_console.py`，为 `GO-API-027` 和 `GO-API-028` 提供失败关闭的
typed fixture 契约，并接入 `GoogleDomainService`、HTTP envelope、OpenAPI 与独立
`SEARCHFLOW_GOOGLE_GSC_*` 双批准门禁：

- `GET /search-console/reports` 明确 asset/template/date/dataState/aggregation、
  `top_rows`/`bulk`、同 grain row、source timezone、privacy、统计完整性、冻结证据
  ID 和签名分页。`top_rows` 不得声明统计完整，未完整返回的 bulk 也不得声明
  `bulk_partition_verified`；匿名行不重建，未知 CTR/position 保持 null。
- `GET /search-console/diagnostics` 返回明确 URL Inspection 版本、抓取时间、问题、
  索引/覆盖状态和源配额；未知字段不回填。检查 URL 必须落在当前 grant 的
  `sc-domain:` 或 URL-prefix property 内，否则在调用 adapter 前以 403 失败关闭。
- GSC adapter 仅在 contract、dataset、source/API version、显式批准和生产批准全部
  匹配时注入；未批准、错合同/数据集、过期、撤权或字段期限不足均失败关闭。
  该门禁不启动 worker、scheduler、采集或任何真实 Google 请求。

最终全量回归为 626 项通过、0 失败、0 跳过，耗时 70.039 秒，退出码 0：
`state/google-prd-v1-20261104/phase11/full-regression-final.log`。
`tests.test_google_search_console` 与 `tests.test_google_runtime` 合计 10 项通过；
`tests.test_google_api` 58 项通过。当前代码重新生成的
`docs/google-domain-openapi.json` 经 `openapi-spec-validator==0.7.2` 验证为 OK，
SHA256 为 `a60434f8b663eea1ebfc3826fe32ef07c34ed0b9c22ba355ed3d6b3b8c62df60`：
`state/google-prd-v1-20261104/phase11/openapi-validation.log`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：74 项 `partial_local_contract`、199 项 `unverified`、0 项完整
验收。本轮只把 `GO-API-027`、`GO-API-028`、`GO-DAT-004`、`GO-DAT-007` 从
`unverified` 调整为部分本地契约；没有把 `GO-SRC-008`/`GO-SRC-009` 或其他条目
升级为完成。GSC 仍是 fixture，不是真实授权 property、真实 API 资产、用户流程或
生产 POC；DST 23/25 小时日、GA4/Ads 时区、GA4 抽样/threshold、服务/project/
property/CID 配额、真实来源批准和连续七天 SLO 均未完成。目标保持 active。

## 第十二实施检查点（不是完成验收）

新增 `google_analytics.py`，为 `GO-API-029` 和 `GO-API-030` 提供 GA4 报告与
API/BigQuery 对账 typed fixture 契约，并接入 `GoogleDomainService`、HTTP
envelope、OpenAPI 与独立 `SEARCHFLOW_GOOGLE_GA4_*` 双批准门禁：

- `GET /analytics/reports` 保留 property/template/period/report timezone、
  dimensions/metrics、row_count、`samplingMetadata`、`subjectToThresholding`、
  `dataLossFromOtherRow`、schema restrictions 和 `returnPropertyQuota`。
  分页只表示已返回行，不宣称统计完整；thresholded 值保持 null，不从
  `subjectToThresholding` 反推被删除行。
- `GET /analytics/reconciliations` 将 API 与 BigQuery 固定到显式 scope，逐指标
  返回 `exact_match`、`tolerance_match`、`difference_explained`、
  `difference_unexplained` 或 `not_comparable`。真实非零 delta 原样保留，不强制
  零差；不兼容 scope 不得输出可比差异，不做用户级跨源拼接。
- GA4 报告游标绑定本次 source/API/dataset/request/report 快照；源报告内容变化后，
  旧游标失败关闭。GSC 报告游标同步补强同一快照绑定。
- GA4 adapter 仅在 contract、dataset、source/API version、独立批准和生产批准全部
  匹配时注入；未批准、错合同/数据集、过期、撤权或字段期限不足均失败关闭。
  门禁不启动 worker、scheduler、采集或任何真实 Google 请求。

最终全量回归为 635 项通过、0 失败、0 跳过，耗时 70.944 秒，退出码 0：
`state/google-prd-v1-20261104/phase12/full-regression-final.log`。
`tests.test_google_analytics` 5 项、`tests.test_google_runtime` 6 项、
`tests.test_google_api` 61 项通过。当前 OpenAPI 经
`openapi-spec-validator==0.7.2` 验证为 OK，SHA256 为
`f31d12afaa673b9c05a50ab78fc289fb046769a5a9226e4d05358d5a881d829f`：
`state/google-prd-v1-20261104/phase12/openapi-validation.log`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：77 项 `partial_local_contract`、196 项 `unverified`、0 项完整
验收。本轮新增 `GO-API-029`、`GO-API-030`、`GO-MET-020` 部分本地契约，并扩展
`GO-DAT-004`、`GO-DAT-007` 的 GSC/GA4 本地覆盖。GA4 仍是 fixture，不是真实
property/Data API/BigQuery 资产；真实 token/并发耗额、30 模板、抽样/HLL++/
threshold、迟到/归因/consent 差异、独立分析员复算及生产用户流程均未完成。
目标保持 active。

## 第十三实施检查点（不是完成验收）

新增 `google_ads.py`，为 `GO-API-031` 和 `GO-API-032` 提供托管 Ads 输出
typed fixture 契约，并接入 `GoogleDomainService`、HTTP envelope、OpenAPI 与
独立 `SEARCHFLOW_GOOGLE_ADS_*` 双批准门禁：

- `GET /ads/keyword-plans` 只读取 approved managed plan。plan、customer、
  geo、network、language、currency、source timezone 和窗口显式绑定；
  4 年历史按月份对齐，close variant 保留 requested queries、returned text、
  group 和 non-additive 状态，同组/月份只允许贡献一次。
- `GET /ads/reports` 只读取 approved managed report。customer、template、
  window、timezone、geo、network、language、currency 和 attribution 全部进入
  输出绑定；`cost_micros`/CPC 保持 integer micros，`conversions` 保持小数，
  负值仅允许 `revision_delta`，partial 输出不宣称完整覆盖。
- Ads admission policy 独立存入 rights registry，绑定 tenant、caller、business
  subject、Cloud project、OAuth subject、customer 和 API version；仅 Basic/
  Standard 可通过，Explorer/Test/unknown、跨主体项目、未批准 SERP+Ads 组合、
  未批准模板/范围、任意 GAQL/query/method/fields 和纯代理均失败关闭。
- adapter 读取前、读取后均重验 current rights、policy、binding、grant、字段
  期限和 evidence 期限；撤权或策略 CAS 变化后丢弃已组装结果。游标绑定证据
  快照，不重复边界项；provider 异常返回 503，不泄露内部细节。

最终全量回归为 662 项通过、0 失败、0 跳过，耗时 76.958 秒，退出码 0：
`state/google-prd-v1-20261104/phase13/full-regression-final.log`。
Ads/runtime/OpenAPI 专项 35 项通过：
`state/google-prd-v1-20261104/phase13/ads-contracts.log`。当前 OpenAPI 经
`openapi-spec-validator==0.7.2` 验证为 OK，SHA256 为
`9b80ecf81d90ec4199f3841941982f95d4bea87db065464213eb7eac45a1e1a1`：
`state/google-prd-v1-20261104/phase13/openapi-validation-final.log`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：82 项 `partial_local_contract`、191 项 `unverified`、0 项
完整验收。本轮新增 `GO-API-031`、`GO-API-032`、`GO-SRC-003`、`GO-SRC-004`、
`GO-DAT-008` 部分本地契约，并扩展 `GO-DAT-003` 的 close variant 覆盖和
`GO-DAT-004` 的 Ads customer 时区覆盖。`GO-SRC-009` 与 `GO-MET-021` 不升级。

Ads 仍是 fixture/policy 本地契约，不是真实 Google Ads Basic/Standard POC、
真实 Cloud project/OAuth/customer、真实 Keyword Planning/GAQL 报表、G-O62
合规结论、真实 CID 配额或生产验收；分币种往返、汇率转换和完整 Ads 经营指标
仍未完成。目标保持 active。

## 第十四实施检查点（不是完成验收）

新增 `google_merchant.py`，为 `GO-API-033` 提供只读 Merchant 报告 typed
fixture 契约，接入 `GoogleDomainService`、HTTP envelope、OpenAPI 与独立
`SEARCHFLOW_GOOGLE_MERCHANT_*` 双批准门禁：

- `GET /merchant/reports` 只接受 source/API version、merchant asset、approved
  access、template/report version、显式 IANA timezone、date window、country
  currency 和分页；不转发任意方法、查询、字段或写操作。
- Merchant access policy 绑定 tenant、caller subject、merchant、source/version、
  approved templates/report versions 和完整 output scope。未批准、撤权、过期、
  跨主体或 scope 改变均在 adapter 读取前返回 `use_not_permitted`。
- `cost_micros`/`sales_micros` 保持 integer micros 和显式币种，负值只允许
  revision delta；product/date/country/device 维度与 metric scope、行窗口、
  duplicate grain 全部校验。未知标题保持 null，不补造商品或竞品销售数据。
- 读取前后重新核验 rights/policy/binding/期限；游标绑定证据快照，不重复边界
  行；provider 异常返回 503。输出明确 `content_api_compatibility=not_claimed`
  且不包含商品发布/修改能力。

最终全量回归为 674 项通过、0 失败、0 跳过，耗时 78.083 秒，退出码 0：
`state/google-prd-v1-20261104/phase14/full-regression-final.log`。当前 OpenAPI
经 `openapi-spec-validator==0.7.2` 验证为 OK，SHA256 为
`8e8dfdca7b7ec9e05a122dba7fe8ea4725498d066ee0b9227f49e8176e0e0816`：
`state/google-prd-v1-20261104/phase14/openapi-validation.log`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：83 项 `partial_local_contract`、190 项 `unverified`、0 项
完整验收。本轮只新增 `GO-API-033` 部分本地契约，并扩展 `GO-DAT-004` 的
Merchant 时区与 `GO-DAT-008` 的 Merchant money micros 本地覆盖；
`GO-POC-11` 仍为未验证，因为没有真实当前 Merchant API 报表端点/字段 POC。

Merchant 仍是 fixture，不是真实授权 merchant 资产、当前 API 报表、产品数据、
配额或生产验收；Content API 不作为后备。目标保持 active。

## 第十五实施检查点（不是完成验收）

新增 `google_quota.py`，为 `GO-SRC-009` 提供本地 PostgreSQL 原子配额桶，
并补强 `GO-ING-005` 的 429/503 重试契约：

- service、Cloud project、property、CID、vendor right 分别建桶；一次请求覆盖
  多桶时全有或全无，任一桶不足均返回 `quota_exceeded` 与最早恢复秒数。
- 共享 property/project 的其他消费者可通过 `consume_external` 计入同一桶，
  本租户随后立即降级；`account_ref` 只作审计标签，账号轮换不产生额外容量。
- request id 绑定完整资源/账号 payload，相同请求幂等，变化返回
  `idempotency_conflict`；事务级 advisory lock 与行锁防止并发超卖。
- 429/503 重试取 Retry-After、指数退避和显式抖动的较大组合；非 429/503
  拒绝重试。配额不足不会切换未批准来源。

最终全量回归为 679 项通过、0 失败、0 跳过，耗时 77.846 秒，退出码 0：
`state/google-prd-v1-20261104/phase15/full-regression-final.log`。OpenAPI
未改路由，仍通过 `openapi-spec-validator==0.7.2`，SHA256 为
`8e8dfdca7b7ec9e05a122dba7fe8ea4725498d066ee0b9227f49e8176e0e0816`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：84 项 `partial_local_contract`、189 项 `unverified`、0 项
完整验收。本轮只新增 `GO-SRC-009` 部分本地契约并扩展 `GO-ING-005`；未接入
真实 GSC/GA4/Ads/Merchant 配额头、真实 project/property/CID 多消费者耗额或
生产调度，`GO-POC-12` 仍未验证。目标保持 active。

## 第十六实施检查点（不是完成验收）

新增 `google_local.py`，为 `GO-API-024`、`GO-API-025`、`GO-API-026` 提供
条件式 Local/Places/review typed fixture，并接入 `GoogleDomainService`、
HTTP envelope、OpenAPI 与独立 `SEARCHFLOW_GOOGLE_LOCAL_*` 双批准门禁：

- `/local/grids` 固定 grid/version/query/variant/center/point/excluded point；
  excluded point 不可采样，点 ID 不得重复。`/local/grids/{id}/observations`
  分开 observed/missing/failed/excluded、depth、rank、cost 和原因；缺测或失败
  不伪装为空排名，未来观测时间失败关闭。
- `/places/{id}/reference` 只返回 place/provider/source URL、display/raw-store
  能力和限制。adapter 声称 raw-store 但 grant 未许可时返回
  `use_not_permitted`，不构造完整商家档案。
- `/local/review-insights` 固定 place、sample、eligible denominator、sorting
  和主题证据；默认无批准 adapter 即 `not_covered`，只返回聚合主题，不返回
  原始评论库。`GO-SRC-010` 的本地字段门禁同步覆盖四类 Local 字段。
- 所有路由要求 source/version/asset grant、field/use/retention 和 typed
  evidence；无任意方法/查询/字段代理，也不生成地图 tiles、地址或受限内容。

最终全量回归为 690 项通过、0 失败、0 跳过，耗时 80.590 秒，退出码 0：
`state/google-prd-v1-20261104/phase16/full-regression-final.log`。当前 OpenAPI
经 `openapi-spec-validator==0.7.2` 验证为 OK，SHA256 为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：87 项 `partial_local_contract`、186 项 `unverified`、0 项
完整验收。本轮新增 `GO-API-024/025/026` 部分本地契约并扩展 `GO-SRC-010`；
`GO-POC-10` 仍未验证，因为没有真实 Places/GBP/供应商 Local、真实网格样本、
地图许可、评论样本复核或生产 disabled 配置验收。目标保持 active。

## 第十七实施检查点（不是完成验收）

新增 `google_aio.py`，为 `GO-ING-011` 提供本地内存 AIO 短效展开队列契约：

- 二次展开任务固定为 `short_lived` 优先级，句柄只保存在受控内存对象中；
  公开任务、repr 和结果不包含真实 token，调用方必须显式 `reveal()` 才可取用。
- `expanded`、`not_available`、`expired`、`timeout`、`failed` 分开记录；
  非展开结果不得携带 answer 或 citations，只有 `expanded` 可进入 visibility 分母。
- claim/complete/expire 均按短效过期时间失败关闭；任务、observation 和 run scope
  绑定，完成幂等冲突不覆盖历史结果，失败原因不伪装成“无 AIO”。

最终全量回归为 695 项通过、0 失败、0 跳过，耗时 82.434 秒，退出码 0：
`state/google-prd-v1-20261104/phase17/full-regression-final.log`。
`tests.test_google_aio` 5 项通过。本轮未改路由，当前 OpenAPI 继续通过
`openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：88 项 `partial_local_contract`、185 项 `unverified`、0 项
完整验收。本轮只新增 `GO-ING-011` 部分本地契约。当前队列不是持久任务、
没有真实 AIO/AI Mode 通道或供应商短效 token，也未完成源特定超时、失败率和
真实品牌 visibility 分母验收；`GO-POC-06` 和人工金标仍未验证。目标保持 active。

## 第十八实施检查点（不是完成验收）

新增 `google_ingest_ledger.py`，为 `GO-ING-007`、`GO-ING-008` 提供本地
UTC 小时录入账契约：

- source attempts、billable units、parsed、sent、accepted、terminal、
  readable、rights-expired 按阶段和显式单位分开累计；result item、stable
  event、receipt 和 document 不合并，缺失阶段保持 unknown 而不是补零。
- run 固定 scheduled/requested/source observed/received 时间；缓存和重试成功
  仍入账，但不计入 fresh rank/AI sample。late 仅在宽限期后标记，计划点只有
  显式 close 后才标 missed，不补造历史。
- UTC 小时由 scheduled time 决定；早于宽限期拒绝 close，close 后拒绝追加或
  改写记录；run/plan 身份幂等，scope 或计数变化返回冲突。

专项 9 项通过。最终全量回归为 704 项通过、0 失败、0 跳过，耗时 82.261 秒，
退出码 0：`state/google-prd-v1-20261104/phase18/full-regression-final.log`。
本轮未改路由，OpenAPI 继续通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：90 项 `partial_local_contract`、183 项 `unverified`、0 项
完整验收。两项仍是内存契约，没有真实供应商 attempts/费用、真实计划调度、
持久化小时账、生产 close 作业或连续对账；`GO-POC-01/12` 未验证。目标保持 active。

## 第十九实施检查点（不是完成验收）

新增 `google_asset_budget.py`，为 `GO-ING-012` 提供本地资产预算与准入契约：

- 默认只允许显式 grant 的 viewport screenshot、full-page screenshot 和必要
  HTML；原视频、全网图片及其他任意资产类型直接拒绝。
- 每个 tenant/source/UTC 日按建议值 250,000,000 bytes 与 3,600 秒原子预留；
  80% 告警、100% 只阻断新资产，返回明确 `fact_text_ingest_blocked=false`。
- 全页截图/HTML 必须在 grant 内再有独立 approval reference 和未过期期限；
  资产保留期取 grant expiry 与 retention days 的较早值。请求幂等，撤权、
  expiry、scope 变化和预算耗尽均失败关闭。

专项 7 项通过。最终全量回归为 711 项通过、0 失败、0 跳过，耗时 79.331 秒，
退出码 0：`state/google-prd-v1-20261104/phase19/full-regression-final.log`。
本轮未改路由，OpenAPI 继续通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：91 项 `partial_local_contract`、182 项 `unverified`、0 项
完整验收。当前没有实际外链默认、抓取器、对象存储/TTL、生产预算持久化或
真实资产采集；不得把本地准入当作媒体许可或存储验收。目标保持 active。

## 第二十实施检查点（不是完成验收）

新增 `google_parser_quality.py`，为 `GO-ING-006` 提供本地源能力与解析质量契约：

- source/parser version、DOM signature、provided capabilities 和已知结果类型
  必须显式绑定；消息字段不能超出源声明，版本变化返回 scope 冲突。
- 空 HTML、DOM signature 变化和新增结果类型分别触发质量告警；新类型保留在
  `unknown_result_types`，不解释成 feature absent。
- 空 HTML 或 confidence < 0.8 的解析结果不得进入正式排名；输出排除原因，
  不静默修复也不生成替代结果。

专项 6 项通过。最终全量回归为 717 项通过、0 失败、0 跳过，耗时 82.645 秒，
退出码 0：`state/google-prd-v1-20261104/phase20/full-regression-final.log`。
本轮未改路由，OpenAPI 继续通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：92 项 `partial_local_contract`、181 项 `unverified`、0 项
完整验收。当前没有真实 HTML parser、DOM 快照历史、连续漂移告警、结果类型
重放或人工解析金标；不得把合成消息当作真实解析验收。目标保持 active。

## 第二十一实施检查点（不是完成验收）

将 `GO-ING-007/008` 从独立内存契约推进为隔离 PostgreSQL 持久小时账，并接入
`IngestSpool` 生命周期：

- 新表分别保存计划点、源 run 与已 close 的 tenant/source/UTC 小时；重启后可
  读回，close 后不可追加或改写，跨租户计划点、run 和 retry 均隔离。
- spool 投递、平台回执、独立读回、重试/取消和权利过期都会生成显式 delivery
  transition；它与 source run 分列，不把一次投递伪装成 source attempt。
- sent/accepted/terminal/readable/rights-expired 继续按 stable event、platform
  receipt、platform document 等单位汇总。旧 spool 行不回填，缺 source timing
  或真实供应商计数时保持 unknown。
- spool 状态转移仍由 PostgreSQL 事务保证；小时账记录失败关闭，不覆盖既有
  小时或伪造源请求计数。

专项 `tests.test_google_ingest + tests.test_google_ingest_ledger` 36 项通过。
最终全量回归为 730 项通过、0 失败、0 跳过，耗时 84.233 秒，退出码 0：
`state/google-prd-v1-20261104/phase21/full-regression-final.log`。本轮未改路由，
OpenAPI 继续通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账状态计数保持 92 项部分本地契约、181 项未验证、0 项完整验收，本轮是
`GO-ING-007/008` 证据升级。仍缺真实源 attempts/费用、真实计划调度、生产平台
回执和独立读回、部署后的连续小时对账；不能称 `GO-ING-001` 闭环。目标保持 active。

## 第二十二实施检查点（不是完成验收）

新增 `google_gsc_revisions.py`，为 `GO-ING-009` 建立隔离 PostgreSQL 的 GSC
分区修订审计，并把 GSC 写入接入现有 rights registry：

- API top rows 与 `searchdata_site_impression`、`searchdata_url_impression`
  保持独立分区；分区身份固定 tenant、source/API/contract/dataset、property、
  template、grain、filter、source timezone、日期/小时与来源表。
- 同一期间 fresh/all 先保存，final 到位后选 final 的最新 source revision；
  迟到 preliminary 不覆盖 final，晚到修订新增 revision，不原位覆盖原审计或
  把两版数字相加。选择先于 expiry，过期 final 不会复活尚存 preliminary。
- 小时分区使用显式 IANA 时区及 UTC `hour_start`。DST 重复小时可有不同身份，
  不存在的本地小时失败关闭；行必须落在对应日/小时，重复 grain 拒绝。
- bulk 每表分别要求成功 ExportLog 引用、独立表分区检查和明确 row count；
  成功日志不能替代表核验，也不能把另一张表判完成。缺证/不完整响应保留排除
  原因；过期未计划、pending、overdue、preliminary、abandoned 分别可见。
- 证据后补可增审计 proof，但不能改写同一导出对应的 source report/hash，也不
  伪造新 source revision。台账只在内存计算报表 hash，数据库仅保存审计元数据、
  行数与 hash，不保存第二份原始报表或行值；`derived_store` 由当前 shared
  registry epoch、grant 和期限在同一事务再次核验，并声明未完成 Whale 读回。

专项 `tests.test_google_gsc_revisions + tests.test_google_search_console` 27 项
通过。全量回归为 752 项通过、0 失败、0 跳过，耗时 87.341 秒，退出码 0：
`state/google-prd-v1-20261104/phase22/full-regression-final.log`。本轮未改路由，
OpenAPI 继续通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：93 项 `partial_local_contract`、180 项 `unverified`、0 项
完整验收。本轮只把 `GO-ING-009` 升级为部分本地契约，并扩展 `GO-ID-004` 的
GSC 修订和 `GO-DAT-004` 的 DST 小时边界。仍缺真实 GSC/BigQuery/ExportLog、
计划回补、真实 source revision 映射与费用、Whale 原始事实和独立读回、漏日
恢复演练、真实 23/25 小时金标及生产连续验收；不能称 GSC/Bulk 已接通。目标
保持 active。

## 第二十三实施检查点（不是完成验收）

新增 `google_metrics_revisions.py`，为 `GO-ING-010` 建立 GA4/Ads/Merchant
统一重读审计账；同时把 `GO-ING-006` 的 parser 质量门接入正式 SERP 投影：

- GA4 `intraday`/`final`、Ads `current`/`conversion_adjusted`、Merchant
  `current`/`revised` 使用独立 stage；final/revised 不能与 preliminary 混成
  一条最新事实，revision 只保留新版本身而不累加两版数值。报告 partition 固定
  tenant/source/property/customer/merchant/template/window/dimension/metric/scope。
- 重读必须引用已存在 observation；源批准的最短重读间隔、final/修正窗口和
  metric-limited lag scope 会失败关闭。exact retry 幂等，同 source revision
  不得伪装成新 observation；超出源批准 reread budget 返回 quota。
- `SourceAccounting` 只接受源或合同明确提供的 revision fee、query scans、
  tokens、rows/bytes scanned；聚合结果把缺失记 unknown，并返回缺失观察数，
  不生成零费用、零扫描或伪造的供应商金额。
- 账本仅保存版本、scope、payload identity/hash 和会计字段，不保存 GA4/Ads/
  Merchant 报表行，也不复制原始报表。Preliminary/final 状态与 expiry 由源证据
  显式提供，未知不回填。
- source accounting 的缺失值保持 unknown 并给出缺失观察数；不同 accounting
  basis/currency 不直接合计，聚合返回 `mixed_basis` 与各组分别可见的总额。
- `ObservationProjection` 可绑定 `ParserQualityGate`。有 gate 时必须携带 typed
  `ParsedMessage`；gate 结果进入固定投影 header/hash。空 HTML 或 confidence
  below 0.8 的有机排名在写入前失败关闭，不能进入正式排名；未配置 gate 却携带
  parser metadata 也失败关闭。运行时 parser 六项配置不齐全或缺少已批准 reader
  时 `not_covered`。

专项 `tests.test_google_metrics_revisions` 16 项通过，投影/runtime 相关
`tests.test_google_projection + tests.test_google_runtime` 43 项通过。最终全量
回归为 772 项通过、0 失败、0 跳过，耗时 83.535 秒，退出码 0：
`state/google-prd-v1-20261104/phase23/full-regression-final.log`。本轮未改路由，
OpenAPI 重新生成并通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：94 项 `partial_local_contract`、179 项 `unverified`、0 项
完整验收。本轮把 `GO-ING-010` 升级为部分本地契约，并扩展 `GO-ING-006` 的正式
投影排名门禁证据。真实 GA4/Ads/Merchant adapter、源重读窗口、费用/扫描量、
真实 parser DOM 历史、人工低置信样本和生产连续对账仍未完成。目标保持 active。

## 第二十四实施检查点（不是完成验收）

补齐两个入库 P0 的本地合同边界：

- `IngestSpool.admission_check` 为 `validation_raw` 提供显式准入检查，绑定
  permit 的 Dataset、source platform、agent、credential scope、task、能力、
  mode 与 ingest class。dry-run、错 Dataset/platform/agent/key、空能力占位、
  未授权任务或任务类型失败关闭；生产准入要求独立 `approved +
  production_approved`，该检查不创建生产 Key。
- `settle_bulk` 要求 bulk 响应逐项对应提交行。混好坏批次中好项可按
  queued/accepted/duplicate 单独结算，`failed_item` 只重试所属行，不把整批
  当作全坏项或原子回滚；失败行保留原 `received_at`、payload hash 与字段，
  下次按原稳定身份重试。响应长度/类型不匹配整体失败关闭。
- 该实现仍只使用隔离 PostgreSQL 与夹具对象，不发起 Whale 写入、不创建真实
  Agent/Key，也不改变任何生产或采集开关。

专项 `tests.test_google_ingest` 18 项通过。最终全量回归为 775 项通过、
0 失败、0 跳过，耗时 88.017 秒，退出码 0：
`state/google-prd-v1-20261104/phase24/full-regression-final.log`。OpenAPI
重新生成并通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`；
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：95 项 `partial_local_contract`、178 项 `unverified`、
0 项完整验收。本轮把 `GO-ING-002`、`GO-ING-003` 升级为部分本地契约。仍缺
真实 Whale validation_raw 平台 Key/Agent/Dataset/任务登记、真实 bulk 400 与
`failed_item` 响应样本、终态 receipt、独立读回和生产连续对账。目标保持 active。

## 第二十五实施检查点（不是完成验收）

推进三个范围 P0 的本地合同：

- `SERPItem.content_semantics` 显式区分 `serp_snippet`、`news_index` 与
  `publisher_article`。摘要/索引不能标出版者全文；`publisher_article` 必须携带
  实际来源，且不进入 organic rank 计算。
- `QueryVariant` 继续以 surface/channel 参与稳定身份。同 URL 可在 organic 与
  paid 等不同 surface 独立出现并分别计数；paid/local/news/shopping/AIO/AI Mode
  不生成跨 surface 的 `google_total_rank`。
- capabilities 与 `authorize()` 使用同一 source `FieldRight` 交集。未批准用途
  不会出现在能力输出，也不能在任务准入时通过；不是仅靠文档提醒。

专项 projection/domain 62 项、API 62 项均通过。最终全量回归为 779 项通过、
0 失败、0 跳过，耗时 82.183 秒，退出码 0：
`state/google-prd-v1-20261104/phase25/full-regression-final.log`。OpenAPI
重新生成并通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`；
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：98 项 `partial_local_contract`、175 项 `unverified`、
0 项完整验收。本轮将 `GO-SCP-003/004/005` 升级为部分本地契约。仍缺真实
surface/source 样本、出版者正文准入、完整逐字段 connector 用途矩阵和真实
平台任务阻断验收。目标保持 active。

## 第二十六实施检查点（不是完成验收）

推进三个范围 P0 的本地合同：

- 授权/公开/估算/研究数据继续按 `MetricValue.provenance_kind`、
  `measurement_nature`、`scale_id`、`additivity`、quality flags 与 metric scope
  无损输出。Keyword Planning 的 provider estimated + rounded 值保留非精确
  语义，不冒充私有实际值。
- 投影和导出继续按 tenant + 当前 grant 隔离。新增跨租户有效 Key/grant 的
  export/list/detail 负向检查；tenant one 的冻结 manifest 不能被 tenant two
  读取，隐藏 detail 与字段/用途负向保持失败关闭。
- GSC/GA4/Ads/Merchant runtime 接线新增显式 OAuth scope 白名单，仅允许声明的
  read-only scopes。Indexing、webmasters、Ads/content 写域、business.manage、
  cloud-platform 或未知 scope 在接线前拒绝；Ads/Merchant 继续拒绝任意源方法、
  GAQL/query/raw proxy，HTTP mutation 不代源写入。

专项 runtime/metrics/domain 43 项、API 63 项通过。最终全量回归为 782 项通过、
0 失败、0 跳过，耗时 81.261 秒，退出码 0：
`state/google-prd-v1-20261104/phase26/full-regression-final.log`。OpenAPI
重新生成并通过 `openapi-spec-validator==0.7.2`，SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`；
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：101 项 `partial_local_contract`、172 项 `unverified`、
0 项完整验收。本轮把 `GO-SCP-002/006/007` 升级为部分本地契约。仍缺全域真实
数据跨租户/字段负向、全图表与 CSV 口径、真实 OAuth consent/scope 返回、外部
SDK 最小权限审计和生产反例。目标保持 active。

## 第二十七实施检查点（不是完成验收）

推进公开 SERP 条件与跨源观测限制的 P0 本地合同：

- `QueryVariant.location` 显式精度与依据；城市/行政区/坐标只接受来源或用户
  明确给出的条件，不从 IP 国家推断精确位置。variant 身份保留地区、语言、
  设备、登录态、通道等条件；缓存年龄及原观察时间随投影读取。
- 新增 typed `ObservationLimitations`，分别记录匿名、低量、圆整、TopN、
  抽样、模型估计、缓存和截断的来源状态；缺失状态不假定无影响，缓存/截断
  状态与实际 run 标志冲突时拒绝。本地 GSC、GA4、Trends 合同分别保留原有
  隐私/TopN、抽样/阈值、低量 null 语义。
- SERP 投影、JSONL/CSV 和确定性报告都保留固定版本的限制状态；报告详情
  重新核验 pinned source 的限制账。CSV 空数值是空字符串，JSONL 仍为 null，
  都不填零或重建受保护 query。报告版本升级为 `deterministic-report-2`。

专项 80 项通过，报告定向 2 项通过。最终全量 786 项通过、0 失败、0 跳过，
耗时 83.376 秒，退出码 0：
`state/google-prd-v1-20261104/phase27/full-regression-final.log`。当前 OpenAPI
与已保存合同一致，并通过 `openapi-spec-validator==0.7.2`；SHA256 仍为
`6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`。
`compileall` 与 `git diff --check` 通过。

需求账现为 273 项：103 项 `partial_local_contract`、170 项 `unverified`、
0 项完整验收。`GO-SRC-005/008` 只升为部分本地合同；真实双来源 200 条人工
条件比对、真实隐私/抽样/截断元数据、全端导出/报告验收、生产运行与批准
证据仍缺。未触碰生产，自动采集/正文/Whale 上传领取/监测调度仍关闭；本地
PostgreSQL/Redis 保留。目标保持 active。

## 第二十八实施检查点（真实存量数据只读探查，不是完成验收）

按用户要求“记录真实数据来测试看效果”，本轮对生产历史公开请求做了一次
**只读 200 条抽样**，未写入、未采集、未部署，不落原始行：

- 抽样策略：`google_web results 150、google_web 非结果 20、google_news 20、
  google_trends 10`，最新优先；排除 `customer_private=true`。
  SQL：`state/google-prd-v1-20261104/phase28/read-only-sample.sql`
  （`BEGIN READ ONLY` + 30 秒超时 + `COPY ... TO STDOUT`）。
- 输出只含 request_id 的 md5 指纹、来源、分类、http 状态、error、完成时间、
  gl/hl/device/location/num/start 参数存在性与字段覆盖计数，不导出原始
  query/url/标题/正文；落盘仅保留聚合摘要
  `state/google-prd-v1-20261104/phase28/real-data-profile.json`。
- 探查结果：200 条中 Web 170（results 150 / empty 8 / error 6 / timeout 6）、
  News 20、Trends 10；全部 cache_status 未知、schema 版本均为旧
  `google-serp-v1`；organic 1,199 条 link/title/snippet/position/absolute_rank
  全覆盖，news 1,318 条 link/title/snippet/publisher/feed_position 全覆盖，
  trends 100 条 topic/approx_traffic/articles 全覆盖但无数值时间序列。
- 条件映射：150 条 Web results 均可构造 `QueryVariant`，但 location/device
  全部 unknown（历史参数缺 location、device），不能推断精确位置或登录态。
  新增 1 项本地断言锁定“旧参数映射不虚构精确位置/设备”
  （`tests/test_google_domain.py`，30 项 domain 测试通过），是合成夹具，
  不是把 200 条真实样本当作双源人工金标。
- 意义界定：现有存量数据可验证旧 schema 到新 `QueryVariant`/投影的**字段
  适配**，但无法验证真实 cache age、login state、location precision，也不
  能替代双来源人工条件比对；`google-serp-v1` 是旧版本，不代表新合同已上线。
- 本轮最终全量回归 **787 项通过 / 0 失败 / 0 跳过，耗时 79.294 秒，退出码 0**：
  `state/google-prd-v1-20261104/phase28/full-regression-final.log`。
  OpenAPI 未改路由，SHA256 仍为
  `6f831d9650dc633ca1aaa8c21deb0f3d8ac3c87243e756341174de9d027c7e38`；
  `compileall` 与 `git diff --check` 通过（结果在 phase28 日志）。
- 需求账保持 273 项：103 项 `partial_local_contract`、170 项 `unverified`、
  0 项完整验收，本轮未升级任何条目。真实 Google/Whale 回执、双人金标、
  D-01～09 决策、WHL-GO 正式 schema、生产连续七天验证、浏览器端 E2E 验收
  仍未完成。
- 生产状态不变：`production_mutated=false`、`production_deployed=false`，
  自动采集/正文/Whale 上传领取/监测调度均关闭；本地 PostgreSQL 54404 与
  Redis 56404 保留供隔离测试。目标保持 active。

## 第二十九实施检查点（线上最小发布，已上线但未验收）

按用户确认“2：部署到线上看效果”，完成一次**可回滚的最小发布**，仅新增
独立服务与网关路由，未覆盖任何线上既有文件。

- 发布内容：新增 29 个 `google_*.py`（含 `google_gateway.py`）到
  `/home/azureuser/projects/google-workbench/code/realtime/`（全部为不存在的
  新文件，`cp -n` 不覆盖）；启动独立容器 `google-workbench-prd-1`
  （`--network host`，监听 `127.0.0.1:18096`，`--entrypoint python -m
  realtime.google_gateway`，restart unless-stopped）。新增 `private/prd-web.env`
  = 原 `private/web.env`（去引号）+ `SEARCHFLOW_GOOGLE_DOMAIN_ENABLED=true`。
- 网关路由：`nginx.conf` 备份后在 `location /` 前新增 `= /v1/google` 与
  `^~ /v1/google/` 两个 location，保留 `auth_request /_access/check`、
  `error_page 401 = @access_denied`、`X-Forwarded-Proto https`；`nginx -t`
  通过并 reload。备份：
  `state/google-prd-v1-deploy-20261104/nginx.conf.bak`、`compose.json.bak`。
- 线上验证（2026-11-04 UTC 09:22 后）：
  - `google-workbench-prd-1` Up；`http://127.0.0.1:18096/healthz` = 200；
    `/v1/google/openapi.json` 无钥匙 = 401 JSON；`/v1/google/capabilities`
    无钥匙 = 401。
  - 经网关内网 `172.18.0.1:18094` 访问 `/v1/google/openapi.json` 无会话 =
    403 访问门（@access_denied），证明公网路由已挂载且受访问门保护；
    ego(lite) 浏览器打开公网 URL 跳转 `/_access/login`（未登录状态）。
  - 既有线上文件哈希不变：`web.py=769cf7bc…`、`serp_page.py=ad5fcc70…`、
    `product.py=2eff6fa5…`。
  - 生产库新增 6 个独立 `sf_google_*` 表（registry/tenant_rights/
    subscriptions/api_leases/api_settings/api_windows），全部 `CREATE TABLE IF
    NOT EXISTS`，可整体删除回滚；未做任何历史数据迁移。
- 边界与未验收项：自动采集/正文/Whale 上传领取/监测调度全部保持关闭；
  Google 域 API 无真实 adapter 配置，所有真实数据端点按合同返回
  `not_covered`/`not_authorized`，不会冒充成功。需求账计数不变（103/170/0），
  完整验收、真实双源金标、生产七天证据仍未达成。全量本地回归 787 项
  通过、0 失败、0 跳过（phase28 日志），本发布只是新增 gateway 文件，
  测试库未变化。
- 回滚：`docker rm -f google-workbench-prd-1` + 恢复 `nginx.conf.bak` 并
  reload + 按需删除新 `sf_google_*` 表与 staging；
  回滚后回到发布前状态。
