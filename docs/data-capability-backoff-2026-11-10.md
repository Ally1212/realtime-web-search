# Google Ads 有界来源重试（本地第十七阶段，2026-11-10）

本阶段实现需求第10节的部分来源限流/重试链路：Google Ads关键词搜索量可由操作员显式批准有界重试。来源默认仍禁用；默认最多一次调用，不读取或获取任何凭据，不挂载生产服务。本阶段没有真实Ads调用或账单对照。

## 冻结策略与来源门禁

可信操作员在适配器配置 `RetryPolicy`，并在该租户对应准入中设置相同 `source_retry_policy`。四个字段全部显式保存到任务的现有admission JSON；不新增表或运行旧迁移。字段为：

| 字段 | 含义与限制 |
|---|---|
| maximum_attempts | 总调用次数，含首次；1–8 |
| initial_delay_seconds | 首次失败的指数退避基数；1–86400秒 |
| maximum_delay_seconds | 指数部分上限，不小于初始值；1–86400秒 |
| maximum_window_seconds | 从首次执行开始的总重试窗口；1–86400秒，仍受任务lease、OAuth和资产授权期限限制 |

客户JSON不能提交或修改策略。未批准多次调用而适配器配置多次时，执行器不报告就绪，客户创建返回503且不预留。历史已入队任务也在执行前阻断；策略在排队或两次调用间变化时不扩权继续调用。旧任务无此字段时保持一次调用。

示例为内部配置形状，不代表来源许可、购买或价格：

```python
policy = RetryPolicy(maximum_attempts=3, initial_delay_seconds=2,
                     maximum_delay_seconds=30, maximum_window_seconds=120)
# 适配器：retry_policy=policy
# 已获准的租户grant：source_retry_policy=policy.public()
# grant.maximum_cost 是整次任务总上限；call_cost_ceiling是每次调用上限。
```

## 哪些响应可以再次调用

同时满足以下条件才进入下一次调用：

- HTTP429或503，完整且已绑定本次请求的JSON只有 `error` 对象；没有返回的成功数据。HTTP200缺字段/partial、混合结果和错误、永久错误、解析失败、输运中断均不重发。
- 本次响应已保存到独立来源响应仓，且独立账单回调给出币种/单位一致的确定费用。未证明免费时不按0；未知费用立即进入reconciliation_required，暂停该来源scope，保留预留和已有账单。
- 本次费用没有超过单次上限，累计确定费用加下一次调用上限不超过原预留。整次任务预算、日/月预算不因重试扩张；已有成功事实不被清除或重取。
- 重试次数、墙钟与单调时钟窗口、lease/授权/取消/原文许可和共享来源门禁仍有效。每次真实调用均重新进入持久门禁，产生独立phase和实际费用检查点。

等待取 `max(Retry-After, min(指数延迟, 指数上限))`；不会为了重试把原生Retry-After缩短。即便本任务已达次数、预算或窗口上限，临时错误的冷却仍写入共享scope，防止其他排队任务立即冲击来源。过长冷却终止本任务的继续尝试，保留后续任务应遵守的来源冷却。

等待可被任务取消、执行器停止或授权变化中止。执行器不自动重放整个适配器：同一任务只有一次dispatch，内部每次允许调用使用 `metrics`、`metrics_retry_2` 等不同phase。进程中断或费用未知后仍采用现有隔离/独立对账，不自动重新进入来源。

## 费用、响应和API

- 每次响应和逐次账单分别保存，费用总计来自全部确定检查点；最终聚合reference仅是本地账单求和的依据，不冒充新的供应商发票。
- 最后成功响应生成标准化事实，之前的错误响应保留在同任务来源journal。失败期间没有成功关键词数据，因而没有隐式覆盖或重试成功子集。
- `/v1/data/jobs/preview` 返回当前拟用策略；job及progress返回创建时冻结策略或null。progress继续列出phase、进入时间、上限和confirmed/unconfirmed账单，不能把部分累计确定费用当最终账单。
- 既有JSON/JSONL/CSV/ZIP/manifest、子记录和订阅不改合同；来源journal导出仍保留全部调用，且大小/总容量/8次上限继续生效。主OpenAPI更新为1.8.0，fields/capabilities同步。

客户只读查看示例：

```bash
curl --fail-with-body "$BASE/v1/jobs/$JOB_ID/progress" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"
```

## 验证与尚未完成

隔离测试使用Google形状的合成响应、真实PostgreSQL队列/预留/来源journal/客户HTTP。覆盖付费503→429→成功的独立账单与总计、指数退避/Retry-After、次数/预算/窗口边界、最终失败仍持久冷却、迟到成功、部分/混合数据不重发、后次未知费用/断响应隔离、等待期间取消/改策略/停止执行器、重启不重放、排队策略冻结、未批准时拒绝创建，以及租户/完整导出。窗口另有墙钟回退与单调时钟负例。最终测试计数、源码摘要与服务清理见 `state/data-capabilities-20261110/backoff-manifest.json`。

此策略目前只接入Google Ads搜索量。Trends/AIO/AI Mode的实际可重试源错误和短效token语义仍需单独实现；不对那些通道自动套用Ads错误分类。已持久化部分成功结果的显式失败子集仍按第十五/十六阶段创建独立子任务；整批失败或取消子任务的安全续试、部分月份最小范围、进程死亡时等待状态恢复仍未完成。真实主体/项目/OAuth/customer/用途/许可、独立费用、来源金标、生产挂载、Whale各域固定版本读回、规模和七天验收仍未通过。

最终隔离回归：1337 passed / 679 subtests / 0 skipped（175.88秒）；专项127 passed / 243 subtests（41.68秒，与全量重叠）。全量另覆盖新增累计剩余预算负例。598个OpenAPI本地引用、只读/不可由客户启用合同与示例bash语法通过。本轮六套PostgreSQL/Redis及测试HTTP/worker均已停止、端口关闭；自己的空socket与合成PG数据目录已清理，测试代码与日志保留。未创建浏览器资源、未部署或外部调用/收费，受保护的web.py/serp_page.py摘要未变。最终证据state/data-capabilities-20261110/backoff-manifest.json。
