# 订阅元数据清除（本地第二十四阶段）

新增可信操作 `SubscriptionRetention(data).erase(tenant_id, subscription_id, reason='operator_erased')`。未增加客户删除接口或自动任务，未部署生产。

清除订阅name、filters、明文幂等键、所有批次payload/校验记录和seen成员；暂停订阅并清next_poll_at。保留订阅ID、请求摘要、首次清除时间/安全原因码及原键摘要。独立观测、源响应、任务和账目不因删除一个订阅而删除，已交付外部文件无法主动撤回。

原键和替代标记键均不能重建旧订阅；拥有者详情、统计、批次、导出、ACK、discard、暂停/继续均410，外租户404，列表排除。新键可以创建新订阅，受原有限额与来源规则约束。删除的订阅不占100条有效订阅上限。创建共享恢复锁后锁租户，清除独占恢复锁后锁租户/订阅，与现有重放顺序一致。批次生成/确认已有订阅行锁，与清除串行；失败整体回滚，重复和并发清除幂等。

限制：本阶段墓碑尚未纳入v1/v2联合检查点。库内存在订阅墓碑时，联合检查点导出/重放明确409 erasure_checkpoint_scope，避免静默遗漏后放行。不能宣称已支持包含订阅清除的备份恢复；恢复旧备份导致墓碑丢失时仍需外部最新墓碑证明和下一版联合恢复实现。候选功能不得正式挂载。逻辑删除不等于WAL/磁盘/备份擦除，最终检查至socket发送严格屏障仍缺失。

本地隔离测试覆盖四域清除、批次/seen移除、独立事实可读、原键拒绝、跨租户隔离、事务回滚和并发幂等。真实本地HTTP验证五格式导出及读写410、外租户404、站点401、旧任务仍可读。主OpenAPI1.9.0、fields/capabilities同步说明清除范围与恢复限制。最终测试和清理证据见 `state/data-capabilities-20261110/subscription-erasure-manifest.json`。

第二十四阶段最终隔离回归 **1412 passed / 742 subtests / 0 skipped / 0 failed**（219.30秒）；专项 **147 passed / 120 subtests**（25.83秒，与全量重叠）。618个OpenAPI引用与订阅清除/恢复范围合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产删除/恢复/部署、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/subscription-erasure-manifest.json`；订阅联合恢复及全需求仍未完成。
