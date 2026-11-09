# SERP 观测投影实施边界

本地实现，不是 Whale 正式 schema、真实 reader 接通或 PRD 整体验收。
本轮没有部署、重启生产服务或开启采集。默认 `/v1/google` 仍关闭；
即使启用 namespace，没有显式接入合格投影时，数据路由仍返回 501。

## 内部读取端口

`realtime/google_observations.py` 定义从固定 Whale 版本选择出来的
`SERPObservation`、`SERPItem`、`SERPFeature`。这是内部适配器输出，不是臆造的
Whale HTTP 响应格式，也没有新增对外平台端点。

真正的 reader 还必须在 WHL-GO 的正式 schema、dataset、版本读取、私有授权、
历史/到期语义获准后实现，不能直接将旧 `queued` 回执或本地 SERP 记录包装成读回。
内部端口要求：

- `contract_ref`、`dataset_id` 与独立 `ReaderAdmission` 对齐。
- `read_version(tenant_id, reference)` 独立读取明确的 document/version/observation；
  没有该版本、权限或可复算内容时抛出领域错误，不回退最新版，不转成空成功。
- 返回的引用须与请求逐字段一致，包括 source/API/parser/license/hash/asset。
  数据必须能确定性重建原投影；不接受回执、可读布尔标志或另一份原始数据库替代。
- reader 自己负责真实平台的鉴权、精确映射、超时、限流及线程安全。
  此处没有偷偷实施平台 schema 审批、配置真实凭据或授权网络调用。

测试中的 `fixture:*` admission、reader、来源、hash 和数据全部为夹具，
`production=False` 仅用于隔离验证，不能作为生产批准证据。

## 存储与权限

`realtime/google_projection.py` 只显式初始化五张独立 `sf_google_*` 表：
租户投影 revision、按 grant 限定的 tombstone、run、item、feature。
不接触现有业务迁移或旧请求/正文表。HTTP 读取不会自动初始化数据库。

只保存有 `derived_store` 权利的选定字段及 Whale 版本引用；没有原始 response、
文章正文、token、任意源 payload、外部 AIO page token。请求配置中的敏感键被拒绝。
原词、NFC query identity、条件 variant、collection/source/version 都保留。
同 URL 多次出现的自然结果、广告与 citation 不按 URL 合并；本地 occurrence_order
只表示输入顺序，不冒充自然排名、源绝对排名或像素坐标。

每次重建先独立读取固定版本；稳定 run 重试不产生新事实，事实变化报 409。
每次查询重新检查当前租户、Key 资产、source/field/use/grant、许可版本和最短期限，
并再次独立核对读取版本。读取期间撤权、过期或清除会丢弃组装中的响应。
并发重建复用事务连接，避免两连接池中的嵌套查询死锁。

`purge_expired()` 级联删除所有选定事实副本；内部
`reconcile_rights()` 可在全部 grant 已撤销后清除投影并记录 tombstone。
限定资产的 manager 不能清除其他 grant 的同文档投影。
这些是内部调用，尚未连接生产定时器、Whale 删除事件、报告/出口和完整灾备恢复流程。
不能宣称全链路清除或灾备演练已通过。

## 本地查询与 HTTP 集成

通过 `GoogleDomainService(projection=...)` 显式注入合格投影后，可实际执行：

- `/keywords`、`/keywords/{id}`：原词与 NFC 身份；`observed_form_only` 明确不是全体别名表。
- `/query-variants`：完整条件与 hash 版本。
- `/observations`、`/observations/{id}`：观测、深度、缓存、真实/未知时间、引用。
- `/observations/{id}/items`：独立 occurrence、父 block、URL、源排名和自然位置。
- `/serp-features`：已观测、未出现或未知的明确记录。
- `/serp-history`：固定 variant 和精确 target URL；排名未知、深度外和源失败分开，
  `series_scope_id` 按来源/API/parser/license/collection/目标区分，不能静默拼接。

时间窗口为 UTC `[from,to)`。SQL 按租户/显式 grant/期限过滤并进行 keyset 分页；
默认 20、最多 100。签名游标绑定 Key 资产范围、权限 epoch、筛选、路径、
排序、页大小及投影 revision。新增或清除使旧普通分页游标失效；
这不是冻结 manifest，不保证跨新增数据的冻结快照。

真实 HTTP 测试通过现有产品 Bearer 认证读取同一查询实现，保留统一 envelope、
HTTPS 要求和现有读取限制。fixture 读回失败返回 503 而非空成功。
meta 明确为 observed_subset，不声称全 Google 覆盖，也不把缓存命中时间冒充新鲜源观测；
没有创建 manifest 时明确给出 `no_frozen_query_manifest_created`。

## 未完成

真实 reader、许可、dataset/profile 批准、生产接线和实时更新仍未完成。
尚未完成正式金标、真实 1,000 样本、百万行负载出口、生产异步调度与对象存储、
排名变化分析、全部参数/高阶模块、工作台用户流程、性能与连续七天验证。
本地独立读取目前需要重新获取所选版本，不是已经通过生产延迟与成本验收的实现。

## 小范围冻结导出

`realtime/google_exports.py` 已接入 `POST /exports`、`GET /exports`、
`GET /exports/{id}`、`/exports/{id}/content` 和本地扩展
`POST /exports/{id}/cancel`。100 行以内保持同步；超过同步分页或显式
`async=true` 时进入 queued，不在请求中伪造成功。

- 发起导出必须提供现有客户 Bearer、`Idempotency-Key`、明确的 `source_path`、
  筛选条件、字段 allowlist 和 `jsonl`/`csv`。没有合格投影时 501；超过同步分页
  自动转异步，不能把第一页冒充完整文件。
- 只允许从已获准的 keywords、query variants、observations、observation items、
  SERP features 导出；不允许借导出读取原始响应、正文、来源 token 或旧采集接口。
  用户选择的字段必须全部在导出 allowlist 内。
- manifest 冻结请求来源路径、筛选、字段、方法版本、查询时权限快照、格式请求 hash、
  投影 revision、Key/grant 范围、已选事实、每条 version/hash evidence 和最短 expiry。相同幂等 Key
  同请求返回原导出；不同请求 409。导出及 manifest 最长存在 24 小时，
  以源/字段/资产到期中更早者为准。
- CSV 对以 `= + - @` 开头的单元格前置 `'`，保留原文本并阻止电子表格公式执行；
  JSONL 不做该改写。内容 hash 在下载时验证。manifest 内容被篡改返回 503。
- 下载前重新核对导出期限、Key/grant 范围、每个 selected fact 的当前 source/use/期限，
  再独立读取每个固定 Whale 版本并复算 projection hash；失败返回 503，
  过期返回 410。冻结内容不因源页面后续变化而静默改写。
- 导出创建与下载分别要求当前 `export` 用途；只有 `derived_store` 或
  `internal_display` 不能导出。manifest 的 Key 范围绑定 subject/grant ID，
  不绑定可能正常递增的 grant 版本；grant 合法更新后仍按当前字段/用途重新核验。
  身份、撤权、资产或 grant 不存在与字段/用途不足分别返回权限错误，不能借版本号
  更新绕过撤权，也不能把正常版本升级误判为撤权。

### 异步分块导出基础

`ExportWorker` 已实现独立 worker 的本地运行入口：

- queued 任务使用租约和 heartbeat CAS 领取；崩溃后过期租约重新排队，连续 3 次
  失败以 `worker_lease_exhausted` 终止。running 任务在下一分页前观察取消/到期。
- 每页最多 100 行，按 `jsonl`/`csv` 渲染；CSV 只在首块写表头。块使用 zlib
  存储，记录块 hash、行数、总行数、总 hash 和进度。逻辑行上限设为 1,000,000，
  超限只保留上限内数据并标 `partial_result`/`row_limit_exceeded`。
- 完成事务同时写入冻结 manifest 和最终状态；manifest 只保存 evidence、
  每事实 projection hash 及分析所需最小 item 字段，不存第二份完整导出内容。
- 下载逐块校验 hash、大小/reflate 边界和总 hash；manifest 的每个固定版本仍需
  独立读回复算，撤权、过期、源失败或篡改均失败关闭。

这仍不是百万行验收：当前块存 PostgreSQL，下载会先在内存中拼接；没有对象存储、
HTTP 流式发送、断点续传、生产 worker 部署/调度、百万行负载与费用证据。也不能把
本地 1,000,000 行常量解释成实际容量或已测吞吐。也没有生产 cron/任务调度器执行
`purge_expired()`，不能宣称已自动清除。

## 持久化异步分析任务基础

`realtime/google_jobs.py` 已实现独立任务表、事件表和有界状态机，接入
`POST /analysis-jobs`、`GET /analysis-jobs/{id}`、`GET /analysis-jobs` 及
`POST /analysis-jobs/{id}/cancel`。这仍是本地实现，不是已部署异步作业。

- 创建任务必须引用一个当前 Key/资产范围内可见、未过期的冻结 item manifest，
  并使用 `Idempotency-Key`；同键异载荷 409。queued 不代表成功，响应明确
  `execution=queued_not_started`，列表标明 worker 代码可用但未部署。
- 仅实现 `item-coverage` 与 `rank-summary` 两种确定性分析。结果给出观测项数、
  类型计数、有机位置已知/未知、URL 数；`rank-summary` 额外给已有位置 histogram
  与 top-k **对已知自然位置的观测计数**，不得解释为全站 top-k 份额、完整
  分母或 Google 总体覆盖。未知位置绝不补成深度外。
- 状态机保留 `queued/running/succeeded/failed/canceled/expired` 审计事件；
  `AnalysisJobWorker` 可独立扫描租户队列并调用 `run_once()`，使用租约 token、
  heartbeat CAS、进度和 attempts 防重复完成。当前 Web 进程没有启动 worker/定时器，
  不能把接口存在当作生产异步处理已上线。
- 任务只在冻结 manifest 内计算，不发起新的源调用；读取/处理时重新检查当前
  Key、grant、source/use/期限，manifest 到期后不返回结果。queued 可立即取消；
  running 记录取消请求并在 worker 结算时阻断成功。过期租约可恢复，连续 3 次
  失败后显式终止。
- 创建和读取成功分析都要求当前 `aggregate` 用途；分析列表不返回结果正文，
  单项详情在重新验证 aggregate 后返回。只拥有内部显示或导出用途不能读取分析结果。

目前有本地进度和租约恢复，但没有生产 worker 部署/调度、分布式时钟/网络分区演练、
百万行流式处理或真实数据集验收，因此 `GO-API-040/104/108` 不能整体标记完成。
