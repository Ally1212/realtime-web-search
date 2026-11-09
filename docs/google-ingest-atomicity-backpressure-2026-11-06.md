# Spool账本原子性与共享退避（2026-11-06）

本轮继续 GO-ING-003/005/007、GO-ID-003、GT-06/07/14 的本地工程验证。不部署、不发真实搜索/上传、不恢复任何自动开关；不以本轮隔离故障测试替代正式平台或七天验收。

## 复现结果与修复

| 问题 | 当前代码原行为的证据 | 本地修复 |
| --- | --- | --- |
| 状态回滚、小时账仍提交 | 注入ledger写完后的异常：spool仍inflight，但小时账出现accepted/terminal | `HourlyIngestLedger.record(..., connection=...)`支持调用方事务；spool全部状态转换把同一连接传入，不另开连接提交 |
| 同run多个item记账冲突 | 两个不同item共享run/attempt/stage身份，出现idempotency冲突或少计 | `delivery-event-ledger-2:<event_id>:<attempt>:<stages>`，按稳定事件分开；run仍是来源请求单位，不把item当多次源搜索 |
| 单个job退避、其他job仍调用 | job A收到429后重建runtime，job B上传及job C回执查询继续发送 | 在独立设置表现有runtime JSON保存全局not-before时间；上传和回执lease均检查同一数据库门禁 |

原子性只使用当前spool和同scope的持久账本。内存账本或不同DSN不能被当成可一起提交的账本；失败使当前状态事务回滚。账本单独调用的原有事务行为保留。没有创建分布式事务，也不声称两个不同数据库可以原子提交。

提交边界覆盖领取、拒权/到期转换、收到回执、重试、轮询终态和独立readback的状态/账本写入。若一次领取两个item而第二个记账失败，两个item的租约、attempt与本次账本全部回滚。`purge_expired`是单独的状态事务；不把整个“purge后领取”组合声称为一个大事务。

本轮并未解决或宣称通过封账后迟到转换、TTL全链路清除及平台灾备；这些仍应独立审核。历史封账数据不改写，不用补默认时间制造新账。

## 实际数据库断连与恢复验证

测试只在本轮独立本机 PostgreSQL 中执行：

1. 领取一条事件，保存当前lease和小时账。
2. 在同一事务内更新状态并插入账本后，通过另一条本轮测试连接终止**该测试事务自己的backend PID**；没有终止整个数据库或其他任务进程。
3. 返回连接报错，重开连接确认状态仍为原inflight，小时账没有新增accepted/terminal。
4. 新建spool对象，使用保存的未过期原lease结算；状态和账本一起提交。

另有“写完账本后抛异常”和“批次第二个item失败”测试，覆盖业务异常及数据库断连两种不同故障。重复回执仍受上轮CAS保护；同一个run的两个item最终sent/accepted/terminal各2，不是两次source attempts。

这不是生产数据库重启、平台故障注入、跨区域灾备或RPO/RTO验证；没有虚构生产恢复指标。

## 共享冷却与恢复

`whale_serp_settings.runtime` 保存：

- `ingest_retry_not_before`：数据库时钟计算的带时区绝对截止时间。
- `ingest_backpressure_reason`：受限机器码，如http_429/http_503，不含Key或任意响应文本。

收到429或5xx时，保存指数退避、Retry-After秒数/HTTP-date及抖动后的最迟截止时间；后来的较短退避不能提前解除已有较长冷却。更新使用设置行锁及JSON合并，不修改uploads/claims开关，也不覆盖其他runtime信息。没有新增schema或执行迁移。

上传与queued回执的SQL领取同时检查该截止时间，故新runtime/进程重建也不会绕过冷却；冷却期不新增attempt。新平台任务claim和新的source collection future也等待；已有返回数据仍可保存/stage，已有任务heartbeat继续，避免伪装任务失联。已经在途的网络调用不可能被此次门禁追溯撤销，不宣称“429后没有任何已在途请求”。

到期且原开关仍允许时才恢复领取，不自动开启开关、不改原事件键/接收时间/payload。持续运行、正文、Whale自动上传/领取及监测在生产保持原状；本轮仅在测试fixture中开关。

## 版本与发布前置

`delivery-event-ledger-2`是未来转换的新本地记账身份，不自动重写旧run级账目。生产上线前必须核对是否已有待恢复的旧spool事件、未封账窗口和旧ledger记录；存在时先冻结对应范围并制定版本切换/对账方案，不能直接重放全部旧事件造成重复或混算。当前真实Web单样本交付走显式helper，不由本轮测试推断生产spool为空。

共享冷却按当前旧Whale集成的同一设置/Key/后端共享；不是Google各来源、property/CID或租户的完整分层预算系统。旧outbox的正式TTL、总容量与全平台许可/清除仍未完成，不能把此修复称为全部背压/治理出口已通过。

## 证据与需求对应

证据前缀 `state/google-data-closure-20261106/atomicity/`：

- `red.log`：三处旧行为的失败复现。
- `ledger-green.log`：首轮44项账本/spool测试通过。
- `targeted-final.log`：75项账本、spool与旧Whale关联测试通过，包括真实测试backend终止、批次全回滚、跨runtime冷却、503/429不缩短、冷却到期恢复、新claim/source dispatch阻断且heartbeat保留。

| 需求 | 本轮证据覆盖 | 保留限制 |
| --- | --- | --- |
| GO-ID-003、GO-ING-003/007 | 稳定event/item账目分离，失败回滚不留假账，多item不丢计数 | 不改旧账；跨版本上线需当前数据前检；非真实平台流水账 |
| GO-ING-005、GT-06 | 429/5xx共享持久冷却、原身份重试、进程对象重建、真实测试连接断连 | 非平台故障注入；未验证所有分层额度/旧outbox容量与TTL |
| GT-07/14、GO-NFR-009 | 隔离事务与重连状态/计数一致 | 不是完整七天、备份/tombstone恢复或RPO/RTO |

下一项：核对封账后迟到回执与expiry清理，以及导出/缓存保留与撤权边界；继续准备冻结版本的最小发布差异。正式schema/ReaderAdmission、来源用途与实际运行授权仍为独立外部条件。

## 最终验证与清理

完整独立UTF8 PostgreSQL/Redis回归 **878通过、0失败、0跳过**，89.316秒（`full-regression.log`）。75项针对性测试通过；编译与diff空白检查通过。相关测试schema剩余0；独立测试PostgreSQL/Redis已停止并验证本轮端口无监听。所有故障注入仅针对本轮自建环境，未操作生产或其他任务服务。此阶段不构成正式平台、七天及真实百万行验收。
