# 新数据域任务合同：本地第一阶段

版本 `searchflow-domain-jobs-1`。对应 DATA-00 与 DATA-01 的参数/准入部分，负责人 Codex；代码 `realtime/data_capability_contract.py`。这是内部合同；第一阶段只冻结参数，后续本地任务、事实和导出进展见下文。尚未新增 HTTP 路由、实际适配器或上线能力，不改变旧 `/v1/search`。

## 冻结的输入

四个 capability 独立：`trends_history`、`keyword_volume`、`aio_answer`、`ai_mode_answer`。
所有任务显式提交 `country`、`language`，不静默填充搜索条件。country 格式检查后转大写，实际地区支持范围由提供方再次验证。未知字段拒绝。

- 历史曲线：`terms`（1–5，词或 topic 引用）、`start_date`、`end_date`（YYYY-MM-DD，闭区间请求边界）、非负整数 `category`、`search_type`（web/images/news/shopping/youtube）。实际周期结束语义由响应 period_start/end 表达；源不支持 topic 或搜索类型须拒绝。
- 搜索量：`keywords`（1–100）、`start_month`、`end_month`（YYYY-MM，含首尾月份）、`network`（google/google_and_partners）。输入顺序和重复保留，提供方调用去重及返回映射待实现；100 为本地合同上限，提供方可更低。
- AI：`prompt`（最多10000字符）、`device`（desktop/mobile）。会话、展开控制、location 等在适配器验证前拒绝，不承诺已支持。AIO 和 AI Mode 哈希不同。

请求摘要覆盖合同版本与规范化参数，仅用于检测幂等参数冲突，不能作为 observation_id、entity_id 或新观测去重键。幂等键仍须以租户为作用域在数据库事务内处理。

## 准入与费用

准入配置由可信操作员提供，客户不得自行声明 approved。独立检查 capability、approved 状态、provider、license_id、用途、保留期限、到期时间、费用单位、币种和提供方参数校验器。配置缺失、到期、预算不足、非有限费用或币种不符拒绝。禁止回退至旧搜索额度。

`maximum_cost` 是适配器对一次完整调用（含批准重试/分页/展开）的费用上界，不是实际账单；还须实现日/月预算、原子资金预留、独立实际费用账本、取消/退款以及释放规则。准入函数返回不等于任务受理或额度预留，HTTP 202 必须等上述事务完成。

错误：invalid_parameter、source_not_admitted、admission_invalid、currency_mismatch；提供方可返回 unsupported_parameter。当前没有准入来源，不能创建真实新域任务。

## 验证与后续

执行 `.venv/bin/python -m pytest tests/test_data_capability_contract.py -q`。合成反例覆盖独立通道、输入映射、未知参数、非法日期、无准入、到期、预算、NaN、币种与提供方拒绝；不代表真实数据验证。

尚待：实际来源与用途/保留条款、凭据及费用上限；独立任务/观测/版本表、原子预留和执行器；原响应与三域标准模型；全部 API/分页/导出/订阅和网页；Whale schema、真实样本、长时与规模验收。提供方和预算未定时继续可独立实现的存储与 API，不自行采购或开启采集。

部署范围：无。回退：移除本轮新增模块及测试即可，生产和数据库未改变。

## DATA-01 持久任务与预算（本地第二阶段）

实现 `realtime/data_capability_jobs.py`，显式 `initialize()` 只创建独立 `sf_capability_*` 表，不调用旧迁移。没有自动初始化、后台启动或生产配置变更。新增模块只供内部调用，尚未挂接 HTTP 或源适配器。

### 状态与恢复合同

`queued → running → succeeded / partial / failed / canceled`；lease 到期进入 `reconciliation_required`，费用预留保持不变。过期任务禁止自动重领，源是否已调用、是否计费须由可信操作员核对；`reconcile()` 为内部管理口，不向客户开放。worker 应在外部调用前取得 lease；源请求发生在数据库锁外。

创建时按租户串行幂等检查，在同一事务内做来源准入、精确十进制预算检查、插入任务和事件。相同键、相同参数/币种/最大费用返回原任务；不同参数或预算冲突。失败事务不保留任务或预留。输入词和 prompt 原文保留，包括空白；仅 country 按合同转大写。

claim 用 `FOR UPDATE SKIP LOCKED`，同任务仅一个 claimant。执行前通过可信 `grant_for` 重查准入、到期、实际参数支持及提供方/许可/用途/保留期限/计费单位一致性；撤权失败终止且释放尚未执行的预留。内部 lease_token 不在客户任务响应中返回。

queued 取消立即释放全部预留；running 或待对账任务取消只标记 cancel_requested，不假定外部调用已停止或免费。源返回结算后仍记实际费用，结果不发布。相同结算幂等，冲突拒绝。迟到 worker 不能用过期 lease 修改终态；恢复后只能可信对账。当前 result_ref 是未来事实存储的内部接口，不代表已实现原响应或完整事实保存。

### 费用合同

预算为每租户、每币种独立配置的 UTC 日/月额度；本地配额与实际货币账分开，不扣旧搜索额度。批准费用上界在任务创建时预留，actual_cost 已知后释放差额；失败也可有实际费用，不能统一免费。不同币种不合计。金额拒绝 float、非有限数、负数和超过9位小数，避免浮点精度及特殊 numeric 值污染。

预算归属固定为任务创建的 UTC 日/月，跨日未完成预留仍归原窗口；这不是供应商按实际调用日出账的财务报表。提供方实际超额账单禁止普通 worker 直接结算，须先进入对账并由可信操作员记录真实金额及原因；该金额会参与原窗口后续预算判断，不抹掉超额。冻结价格、提供方日/月实际调用限制、平台费用和税费仍须在接通来源时分别配置。

### 测试与限制

`TEST_DATABASE_URL=<独立测试库> .venv/bin/python -m pytest tests/test_data_capability_contract.py tests/test_data_capability_jobs.py -q`

真实 PostgreSQL 事务测试覆盖并发同键、不同币种冲突、预算竞争与释放、租户隔离、撤权、唯一领取、取消仍计费、过期 lease、服务实例重建、可信超额账单对账、UTC 日/月边界及插入后故障事务回滚。来源和金额均为明确的测试 fixture，不发起真实源调用。

尚待实现：独立事实模型/保存与版本、严格结果校验、限流和 Retry-After、原始响应保留/敏感字段策略、数据读写授权和撤权清除、游标/完整导出/订阅、新路由 OpenAPI/目录、网页、真实适配器与平台验收。当前成功终态只能由内部调用写入引用，尚不满足需求所述完整链路成功条件，禁止直接对外开放此队列。


## DATA-01 事实与交付（本地第三阶段）

当前已补独立事实信封、域模型与子记录投影、不可变观测/显式修订、当前权限/到期检查、本地缓存关联、完整 JSON/JSONL/CSV/ZIP/manifest，以及事实/文件损坏拒绝和投影重建。公共队列已禁止任意 result_ref 成功结算；`CapabilityData.complete()` 将保存和结算放在同一事务。细节、固定模型及仍待事项见 [第三阶段报告](data-capability-facts-2026-11-10.md)。

本地已有模型和完整单观测文件，不等于主客户 API、订阅或网页已经接通。真实来源、来源缓存声明、许可与预算、执行器、API/订阅/网页和平台链路仍须继续，四项均未完成真实验收或上线。


## 后续进展（本地第四阶段）

主客户 API、OpenAPI/fields/capabilities、查询及下载已完成本地接入，覆盖本报告此前的“尚无客户路由”状态；详情与完整回归见 [客户 API 报告](data-capability-api-2026-11-10.md)。真实来源、实际执行器、订阅、网页、发布及真实验收仍未完成。


本地第七阶段已补来源无关执行器与共享账号调度/费用不确定恢复，见 [执行器合同和证据](data-capability-executor-2026-11-10.md)。仍无真实适配器或生产挂载，不据此宣称真实来源可用。


后续第八阶段新增明确 SerpApi 历史曲线适配候选及兼容的持久计费证明，见 [来源合同与验证](data-capability-serpapi-2026-11-10.md)。仍默认禁用，未验证真实账号/许可/费用；未部署、不增加已上线能力声明。
