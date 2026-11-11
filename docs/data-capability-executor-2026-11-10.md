# 新数据能力执行器：本地候选（2026-11-10）

负责人 Codex；代码 `realtime/data_capability_executor.py`。本阶段实现来源无关的单次执行调度，不附带真实适配器、不自动挂载生产、不调用真实来源。原始总目标和真实来源验收范围不变。

## 调用合同

`CapabilityExecutor` 必须由可信操作员显式构造、`initialize()`、`start()`。构造和导入不创建表、不启线程、不调用来源；initialize 只创建独立 `sf_capability_executor_scopes` / `sf_capability_dispatches`，基础 CapabilityData 须另行显式初始化。旧迁移、worker、搜索额度和生产开关不参与。

一个实例绑定 capability/provider 和可信适配器函数；同一供应商账号/资产共享额度的所有能力、线程和进程必须使用同一个 scope。scope 不含密钥，不提供客户自定义入口。当前采用共享并发 1 和显式最小间隔，保守覆盖账号/资产总配额；相同 scope 的配置不同会报 executor_policy_conflict，不在构造时静默覆盖。空队列不消耗共享间隔。

调度顺序：持久 scope permit → 原有 FOR UPDATE SKIP LOCKED 领取 → 当前准入、取消和 lease 复查 → 提交 durable dispatch entry → 数据库事务外调用 adapter → 当前权限再查 → 原有完整事实/子记录/任务结算同事务 → dispatch 账本完成。记录使用 job UUID 生成稳定 event_key；只有真实上游支持时，适配器才将其用于上游幂等，不能假定上游具有幂等保证。

适配器接收 `SourceContext`（原参数、任务费用上限/币种、绝对 deadline、稳定 event_key、取消查询函数）。凭据由可信适配器闭包/专用配置管理，不进入 context、任务事件或调度账本。适配器必须在底层网络设置连接/读取/总时限并遵守 deadline，限制响应体积、遵守实际参数/许可、过滤短效 token，并提供现有严格事实合同。Python 线程无法强制中断任意阻塞回调；本执行器不能替代底层传输超时。

成功返回 `SourceResult(facts, actual_cost, revision=None, supersedes=None)`；actual_cost 必须是以 context.currency 计价的已确认账单，不是预览费用；真实适配器必须核对供应商币种，不能把另一币种的数值直接套入。当前尚无真实账单币种验收。缺失/错误事实不写半份观测；partial 由保存事实合同确定。新源原始响应和事实仍受 64MiB 保存限制；这不等于验证了底层传输/内存的全部资源边界。

明确失败抛出 `SourceFailure(safe_code, actual_cost=..., retry_after=...)`。已知费用照实失败结算，包括付费失败；未知费用使用 None。retry_after 是适配器按真实响应转换的秒数（0–86400），持久推迟该 scope 的后续任务，不重复本任务。HTTP Retry-After 日期解析、上游具体配额、二次展开、批量失败子集及允许的安全重试仍属于后续具体适配器工作，尚未完成；不能据本模块宣称需求中的全部重试策略已实现。

## 不确定结果与恢复

异常、未知账单、无效事实、撤权后返回、超额实际账单、过期 lease 或保存失败均进入 reconciliation_required，保留原预留；已知 actual_cost 单独保存在调度账本供核对，不在失败时假定免费。账本只保存安全错误代码，不保存任意异常文本、响应、prompt 或 token。恢复不重发来源请求。

同 scope 存在待对账任务时停止后续派发，并且运行中执行器的 ready 返回 false，主客户 API 拒绝新任务（503）。只有可信操作员独立核对上游终态/账单、通过既有内部 reconcile 后，才可能继续；客户没有此管理路由。不得为恢复直接删除 pending 账本或改为免费。

进程在 dispatch entry 后崩溃：lease 到期转待对账，不重领旧任务。旧 permit token 无法释放新 owner 的 permit；即使 permit 到期，尚在执行/待对账的 dispatch 也阻止另一来源调用。在途取消不发布事实，但真实费用仍结算。stop 立即关闭 readiness，不强杀在途调用；返回 false 表示线程尚未退出，不得据此重启第二份执行器。

## 客户 API 挂载

显式初始化并启动后，才能把 `executor.ready` 或多实例的可信组合函数作为 `CapabilityRuntime(..., worker_ready=...)`。ready 检查匹配能力/provider、线程存活、最近调度活动、共享政策与待对账状态；同步 `run_once()` 可供外部监督器调用，但不会自行假装有常驻活执行器。默认 Runtime 仍拒绝任务，不因导入新模块自动开放。

本地 HTTP 测试使用真正的候选执行器替换旧手写测试消费者，验证两层鉴权下四能力提交→合成来源→事实保存→客户查询、跨租户404及启停/账号待对账503。这仍是合成来源，不是外部付费调用或生产验收。

## 验证与发布边界

完整独立回归：**1191 passed / 444 subtests passed / 0 skipped**，129.08 秒，证据 `state/data-capabilities-20261110/executor-regression-final/pytest.xml`。最终再补“事实结算提交后、调度账本更新前崩溃”的本地账本修复，以及迟到 Retry-After 不释放新 owner permit、冷却先于终态持久化的保护，专项 **15 passed / 14 subtests passed / 0 skipped**，3.35 秒，证据 `executor-recovery-release-final/focused.xml`；两组有重叠，不相加冒充一次完整执行。

```sh
.venv/bin/python -m pytest -q --junitxml=<证据目录>/pytest.xml
.venv/bin/python -m pytest -q tests/test_data_capability_executor.py --junitxml=<证据目录>/focused.xml
```

独立数据库/Redis 启动与环境配置脚本保留在对应目录 `run.py`。前序验证发现两类测试问题并修正：同时间戳任务的 UUID 排序不能假定创建先后，fixture 现使用独立递增微秒；旧测试启动路径需要 DATABASE_URL/REDIS_URL 也指向本轮实例，规模探针要求显式回环 TCP，最终回归使用独立临时端口并通过。没有修改业务代码来绕过既有测试。

专项覆盖四能力逐字段保存、单次调用身份、双执行器共享账号并发/间隔/政策冲突、空队列不消耗其他能力配额、Retry-After、未知账单保留/账号暂停、无效事实/超账单/撤权/迟到、部分数据、在途与调用前取消、进程崩溃、提交后账本修复、旧 permit 不释放新 owner、保存事务回滚、显式启停与客户 HTTP 隔离/503。全部为合成适配器，不是供应商网络实测。

四套本轮 PostgreSQL/Redis 已按进程和日志核验停止，HTTP/worker 测试线程已退出，仅删除本轮已识别的空 socket 目录；证据 `executor-cleanup.json`。源码与证据摘要见 `executor-manifest.json`。

未部署，`web.py` / `serp_page.py` 不修改。无生产表初始化/设置/数据/容器变化，无外部来源或平台调用。回退候选：移除新增执行器模块；生产从未挂载，无线上回退动作。本地测试表是独立隔离数据库内的数据。

仍待：真实提供方/用途许可/凭据/预算、真实适配器与传输限制、安全重试和批量失败范围、实际服务启动/监督挂载、Whale各域正式schema/终态/独立读回、真实规模与七天端到端。任务本地保存成功不改变 platform_delivery=not_submitted。


后续第八阶段新增明确 SerpApi 历史曲线适配候选及兼容的持久计费证明，见 [来源合同与验证](data-capability-serpapi-2026-11-10.md)。仍默认禁用，未验证真实账号/许可/费用；未部署、不增加已上线能力声明。

## 后续：逐次来源调用检查点

本地第十二阶段增加 before_call(phase, maximum_cost) 与 confirm_bill(phase, evidence)。每次进入持久保存，重复阶段或前段费用未确认不得继续；重新核验当前权限、取消/停止、lease、原文预留和剩余预算。分段已确认费用不替代最终结算，全部已进入阶段均有凭据且合计一致才允许最终金额。未知第二段保留首段费用、完整预留和账号隔离。详见 [分阶段调用与响应](data-capability-source-calls-2026-11-10.md)；AIO实际供应商流程仍待实现。


## 第十三阶段兼容扩展

AIO默认禁用候选现已实现，见 [AIO来源与两段交付](data-capability-serpapi-aio-2026-11-10.md)。SourceContext.defer_calls在收到限流后立即持久保存冷却；SourceResult.retry_after支持部分事实交付并保留剩余冷却。AI Mode与AIO共享结构解析核心，仍独立核对通道和原生范围，不把候选当真实来源已接通。
