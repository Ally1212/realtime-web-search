# 数据域恢复启动门禁（本地第二十三阶段）

已实现可选、逐服务实例绑定的恢复门禁。绑定后每次新进程默认关闭：新数据域任务创建、读取、导出、订阅调度及来源 worker 均要求本进程先完成联合检查点v2重放。返回503 `restore_required`，没有新增客户恢复接口；既有搜索路由和双层鉴权保持。

```python
from realtime.data_capability_restore_gate import RestoreGate
from realtime.data_capability_checkpoint import DataErasureCheckpoint

# 每个服务实例在挂载路由或启动worker前绑定；摘要来自独立保管的最新记录。
gate = RestoreGate(data, checkpoint_id=latest_id, checkpoint_hash=latest_sha256)
recovery = DataErasureCheckpoint(data, configured_response_store)
recovery.initialize()
recovery.replay_file(checkpoint_path, independent_signing_key,
                     expected_hash=latest_sha256)
# 成功提交且核验回执后才允许启动worker和开放新数据域。
```

仅签名、文件固定摘要、检查点ID及v2范围均匹配且重放事务提交成功才能放行。数据库已有回执不能自行放行；旧v1仍可用于不绑定门禁的历史恢复，不满足此门禁。任一次重放失败重新关闭门禁。并发旧重放完成也不能覆盖较新的关闭操作。每次受保护操作重新检查联合回执和任务计数附表，缺失或冲突立即关闭，恢复回执后仍需本进程重新重放。

worker启动、领取和付费阶段前检查门禁；已停止worker继续保持停止，不产生来源调用。调用已发出后关闭门禁无法撤回上游请求：账目仍记录，已知费用进入对账，不发布事实，未知费用继续保留预留。可信schema初始化、检查点重放、内部费用审计和在途账目收尾保持可用。

门禁需显式绑定每个候选服务/进程，未绑定保持原行为；本轮没有正式服务装配或生产部署。恢复必须先停止所有HTTP/worker，再以新进程绑定独立最新固定摘要重放。不能据此宣称支持运行中任意SQL覆盖数据库，或实现最终检查至socket发送的严格屏障。独立最新检查点自动保管、订阅元数据统一清除、整域撤权、平台副本删除证明及真实来源/费用/金标、正式挂载读回、规模和七天仍待完成。

验证覆盖默认关闭、已有回执不放行、第二实例独立关闭、错误签名/固定ID/文件失败、事务回滚、回执丢失锁闭、并发旧重放、来源零调用与调用中费用隔离，以及真实本地HTTP旧搜索兼容、双层401和新数据域503。最终结果见 `state/data-capabilities-20261110/restore-gate-manifest.json`。

第二十三阶段最终隔离回归 **1406 passed / 730 subtests / 0 skipped / 0 failed**（210.33秒）；专项 **141 passed / 108 subtests**（24.26秒，与全量重叠）。609个OpenAPI引用、503恢复状态/字段/双层鉴权合同通过。两套本轮PG/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket与合成PG目录清理，日志保留；保护文件摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终源码与清理证据 `state/data-capabilities-20261110/restore-gate-manifest.json`；GOV-04和真实全需求仍未完整验收。
