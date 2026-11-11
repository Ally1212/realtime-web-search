# 第二十二阶段：任务查询内容、重试计划与v2恢复（本地候选）

本阶段继续推进GOV-04。清除范围是选中任务、共享观测的缓存别名、向下派生的重试任务，以及它们的事实/响应内容。独立采样、其他修订、订阅元数据和外部平台仍按各自规则处理。没有生产清除、恢复、迁移或发布。

## 任务内容清除

可信 `JobRetention(service).erase(tenant,job_id,reason='operator_erased')` 在一个事务中计算别名/重试闭包，最多1000个任务与1000个观测；超限失败整批回滚。恢复独占锁、响应仓锁、租户锁、行锁的顺序与创建/重试/完成协调，闭包不会漏掉同批新建的子任务。查询别名和子任务也有SQL行数上限。

清除内容包括任务parameters、明文idempotency_key、自由文本对账说明、事件detail与相关重试plan；观测/子投影及关联响应采用既有事务清除。保留原任务ID、request_hash、plan_hash、来源/许可、费用/预算/账单审计和已结算终态。幂等键改成不含原文字的标记，原键的摘要成为独立墓碑；原键或标记键都不能使已清除任务重新收费创建。

- queued任务转canceled，actual_cost=0，释放未执行预留，不会再被claim。
- running/reconciliation_required只设置停止标记；实际费用未知继续持有预留。已经发出的来源请求可能返回并收费，返回体不保存。已知真实费用可在清除后正常结算；未知费用继续隔离、待人工对账。
- 已结算任务的费用和终态保持。后续对账使用固定安全说明，后续事件抑制自由文本，避免把查询内容重新写回审计行。

运行态任务详情、进度、事实、关联/重试预览、响应及导出遇任务墓碑返回410；跨租户404；已清除任务排除于列表。取消入口也返回410，因为清除事务已经请求停止。原单纯来源撤权仍允许拥有者停止尚未清除的任务，不要求来源读取授权。底层可信费用账本仍可读取安全状态及空参数，支持在途结算。

从子任务发起只向下处理其派生；相关重试计划清空以移除已删除内容。完整父任务若有独立保留依据仍可保留。订阅名称/筛选、合法供应商账单证明、其他独立任务和平台副本不在本次任务闭包清除范围内，不能把本阶段称为客户全部数据删除。

## 联合检查点v2

`searchflow-data-erasure-2` / `observations_jobs_and_source_responses` 增加任务墓碑：job_id、tenant、request_hash、原幂等键摘要、首次清除时间和安全原因码。明文查询、幂等键和重试内容不会写进检查点。单次任务墓碑最多100000条，外层正文继续受64MiB上限保护。

联合重放先恢复响应墓碑，再处理任务元数据，最后清事实/投影，任何身份、原请求摘要或原幂等键映射冲突整笔回滚。任务不存在也先保留墓碑，后续原键不能重新创建。任务元数据通过服务端游标有界扫描，每种扫描最多1000000行，不加载parameters或事实payload。完整恢复仍必须停服并使用独立保管的最新固定SHA256。

v1检查点继续被校验并按其原事实/响应范围重放，job_entries=0；不把旧范围冒充任务参数清除。原重放审计表不改列，任务计数存独立附表，旧数据无需重写。已应用回执不跳过后来被备份覆盖的内容；v2仍执行幂等重放。

```python
from realtime.data_capability_job_retention import JobRetention
from realtime.data_capability_checkpoint import DataErasureCheckpoint

JobRetention(data).erase(tenant_id, job_id, reason='operator_erased')
recovery = DataErasureCheckpoint(data, configured_response_store)
recovery.initialize()
proof = recovery.checkpoint(private_path, independent_signing_key)
# 停服恢复后，从独立最新备份记录取得固定摘要，再读取并重放完整v2文件。
result = recovery.replay_file(private_path, independent_signing_key,
                              expected_hash=independently_pinned_latest_hash)
```

没有新增客户删除/恢复接口，主OpenAPI仍1.9.0；任务相关路由补齐410/404与在途费用语义，capabilities/fields说明清除范围及保留账目，现有调用格式与双层鉴权保持。新功能不增加来源调用、付费预算或平台写入。

## 验证与未完成项

隔离数据库覆盖四域任务清除与v2旧库恢复、别名/明确失败重试子任务级联、幂等键/事件/对账自由文本不复活、未执行预留释放、在途已知费用结算、未知费用保留、事务失败/闭包超限回滚、原行缺失预先墓碑、v1兼容及错误任务身份/重复墓碑拒绝。真实本地客户HTTP验证元数据/进度/重试/响应和五格式导出410、列表排除、跨租户404、双层鉴权401与原键拒绝重新创建。

未完成：订阅名称/筛选等全部元数据的统一清除；按来源许可整域撤权和其他未关联任务扫描；最终授权检查至socket发送间严格屏障；独立最新检查点自动保管与恢复启动门禁；平台删除证明及外部文件责任；真实来源许可/Key/预算/费用/金标、正式挂载、Whale各域固定版本读回、规模和七天。逻辑清除不等于PG/WAL/备份/磁盘擦除。

最终源码、测试及清理记录：`state/data-capabilities-20261110/job-erasure-manifest.json`。

最终隔离回归 **1396 passed / 720 subtests / 0 skipped / 0 failed**（203.84秒）；专项 **131 passed / 98 subtests**（23.15秒，与全量重叠）。606个OpenAPI本地引用、错误语义/字段/双层鉴权合同及既有鉴权示例bash语法通过。三套本轮独立PG/Redis及测试HTTP/worker已停止、端口关闭，自己的空socket与合成PG目录已删除；日志及源摘要保留。web.py/serp_page.py保护摘要不变，未创建浏览器资源、生产删除/恢复/部署、外部调用/收费或Git提交。
