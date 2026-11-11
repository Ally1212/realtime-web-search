# 分阶段来源调用、响应保存与费用检查点（2026-11-10）

本地第十二阶段，服务于 PRI-03 的 AIO 二次展开和 DATA-06 的可靠交付。现有单次响应不能在第二次收费调用前保存首段，因此先补多阶段执行基础；此文不宣称 AIO 供应商适配、真实 token 展开或整个需求已完成。生产未部署，所有来源和账单均为合成测试。

## 调用前准入和已确认费用

可信适配器可使用 `SourceContext.before_call(phase, maximum_cost)`、`confirm_bill(phase, evidence)`。phase 是最多80字符安全代码，不能含 token、凭据或问题正文。最多8次调用；每个任务已预留的是完整任务费用，不按阶段重复预留整任务额度。

每次 before_call 在事务内重新验证有效 lease、共享账号许可所有权、停止/取消、当前来源准入与原文许可、冻结币种/单位/用途和剩余授权，将阶段身份、该次批准费用上界和进入时间持久写入独立调度表后返回。相同阶段不能再次进入；前次调用未取得费用凭据或其账单超过上界时，后续阶段被拒绝。阶段进入代表“可能发生收费调用”，不代表已拿到结果。逐段遵守同一账号最小间隔，最后一段的时间也用于推迟后续任务；未到间隔时内部 SourceCallDeferred 提供剩余秒数，不写新调用记录。可信适配器只能在任务期限和源token期限内本地等待，不将等待当成已调用或默认重试。账号许可失效/被新执行接管时拒绝继续，旧执行释放不清除新所有者。

confirm_bill 要求已进入的阶段、明确金额、冻结币种/单位和安全的不透明凭据引用。重复同一凭据幂等，不同凭据/金额不得覆盖。每张凭据与累计已确认金额在下一次调用前提交数据库；意外超价也保留真实账单，不丢失证据。最终结算必须覆盖所有已进入阶段，金额等于已确认凭据合计，不能将半段合计当总额或拿0掩盖未确认调用。

第二次网络/费用未知时，任务保持 reconciliation_required、完整预留仍 held，已确认的第一段账单另存并可查询；共享账号停止派发/新任务准入，不自动重放任何阶段。崩溃后的持久调用身份不是恢复短效 token 或自动重试的授权。任务过期并被其他监督器置为待对账后，同一原执行的迟到、已确认费用仍可落盘，不能借此继续调用、保存成功事实或解除预留；已人工终结的结算不被迟到回调改写。

旧单次适配器接口兼容，不要求旧可信内部适配器伪造逐次凭据；本接口只供显式启用的多阶段适配器使用。不得把 synthetic 账单函数、测试许可或批准费用上界当实际供应商账单。

## 分阶段响应仓

`ResponseSession.checkpoint(phase, ...)` 将完整且范围可验证的已脱敏 JSON 先保存，再允许适配器继续解析/计费/下次请求。每个阶段包含 HTTP 状态、请求/接收时间、范围/完整性、脱敏路径、未保留原因、payload及其摘要。所有阶段共用原先的单任务16MiB预留，不是每阶段新增16MiB；最大8段。单次响应的 save 和分阶段 checkpoint 不得混用或互相覆盖。

首段会立即提交，后段超限、写入失败或格式无法保留不会覆盖首段。范围不符/断响应的阶段只保存明确原因及空 payload；保留其他合法阶段，而不是因为后一段失败就删除已获许可的首段。输入仍须先由供应商适配器脱敏，敏感字段/非有限JSON被拒绝。时间必须属于本次执行且按来源调用顺序递增。

执行结束封存为 `capture_kind=source_call_journal`，payload 为 `{schema_version: "searchflow-source-call-journal-1", calls: [...]}`，响应ID与整体摘要随后固定。每段有独立范围/完整性；封存不等于回答完整、解析成功、费用已结清或平台读回。根 scope_verified/response_complete 为各段合取，某段不可保留时其他已核验 payload 仍可读，不能把根 false 理解为其它段也未保存。

封存前接口409，避免下载随后会变化的“固定快照”。显式 `seal_abandoned(limit=100, tenant=None)` 可在重启后封存已结束/lease过期的日志，不重试来源、不生成事实、不结算费用；挂载响应仓的客户 runtime 在既有租户维护过程中仅封存该租户的此类日志。活跃 lease 不会封存。取消、撤权、TTL、清除和摘要损坏沿用原响应保护。

## API、字段与示例

主 OpenAPI **1.5.0**，既有站点访问门及客户 Bearer 不变，无新公共路由。

- `GET /v1/jobs/{id}/progress` 增加 source_call_attempts、confirmed_call_charges、confirmed_call_cost、call_charge_basis。attempts 的 bill_status 是 confirmed/unconfirmed；金额仅为已有凭据合计，可能不完整，不替代 actual_cost。没有凭据时为 null，不是假0。
- `GET /v1/jobs/{id}/response` 同时支持原单次响应与封存日志，新增可选 capture_kind/call_count；capabilities/fields 同步声明，未配置仍不宣告真实来源可用。
- `/response/export` 沿用 JSON/JSONL/CSV/ZIP/manifest 与固定 snapshot_hash；日志整体是一个独立响应记录，各阶段完整保留在 payload，不塞入旧SERP列。ZIP仍用外部固定 response_id/snapshot_hash 独立校验。

```sh
curl "$SEARCHFLOW_BASE_URL/v1/jobs/$JOB_ID/progress" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"
curl "$SEARCHFLOW_BASE_URL/v1/jobs/$JOB_ID/response/export?format=zip&snapshot_hash=$RESPONSE_SNAPSHOT_HASH" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY" -o source-response.zip
```

跨租户404；撤权409；取消/过期/清除410；封存前409；来源下一阶段因撤权、费用未确认、预算不足或重复进入而被本地拒绝，不产生额外来源请求。新错误码仅供可信执行器/适配器判断，客户不能直接调用内部 before_call 或提交供应商账单。

## 验证及限制

测试覆盖持久首段、不可覆盖、统一总配额、时序/8段上限、敏感字段拒绝、后段不可保留仍交付首段、取消/过期/撤权/损坏、重启封存、全部已进入阶段账单一致性、逐次预算和准入、逐段共享间隔/最后一段后的任务间隔、账号许可丢失/响应清除后禁止继续、未知第二段账单隔离、相同阶段禁止重发，以及真实本地客户HTTP的进度、双段日志、五格式下载、独立ZIP校验与私有隔离。没有真实来源调用或AIO金标。

最终完整隔离回归 **1257 passed / 558 subtests / 0 skipped，146.48秒**；专项 **80 passed / 128 subtests / 0 skipped，18.38秒**，与全量重叠，不累计为同一次测试。API示例bash语法与主OpenAPI/fields/capabilities检查通过；六套本轮独立PostgreSQL/Redis及测试HTTP/worker已停止，端口关闭，仅清理自己的空socket目录，本轮未创建浏览器资源。

恢复封存目前只处理已经有阶段检查点的预留；完全未收到响应的空预留仍按原完成/TTL策略管理。

回归证据与清理结果见 `state/data-capabilities-20261110/source-calls-manifest.json`。实现只在显式初始化独立 sf_capability_dispatches 时增加 billing_parts/call_attempts，不运行旧库迁移；响应仓复用原表与逻辑字节预留。没有新增后台线程、生产服务挂载或浏览器资源。

接下来仍须实现并验证实际 AIO 初始搜索→短效 token→独立展开的绑定、过期/错误/部分回答与源块/引用标准化；不能把这里的合成两段回调当作该供应商流程已完成。真实许可/Key/预算/账单、关键词量适配、正式服务挂载、各域平台读回、真实金标/规模/七天验收仍待完成；响应物理磁盘保障、自动TTL调度及备份恢复墓碑重放继续保留为待办。


## 第十三阶段兼容扩展

AIO默认禁用候选现已实现，见 [AIO来源与两段交付](data-capability-serpapi-aio-2026-11-10.md)。SourceContext.defer_calls在收到限流后立即持久保存冷却；SourceResult.retry_after支持部分事实交付并保留剩余冷却。AI Mode与AIO共享结构解析核心，仍独立核对通道和原生范围，不把候选当真实来源已接通。
