# 订阅联合备份恢复（本地第二十五阶段）

联合检查点升级为 `searchflow-data-erasure-3`，scope=`observations_jobs_subscriptions_and_source_responses`。在原事实、任务与响应墓碑之外，完整包含订阅墓碑：subscription_id、tenant_id、request_hash、idempotency_digest、首次erased_at、安全reason。明文名称、筛选和幂等键不写入文件。订阅最多100000条，外层正文仍最多64MiB，超限拒绝完整导出，不截断。

恢复先校验HMAC、完整结构及独立固定SHA256，再在独占恢复锁下原子重放响应、订阅、任务、事实。订阅墓碑先落库，原行尚不存在也能阻止原键重建；按受影响租户以服务端游标扫描id/幂等键，最多1000000行，不加载名称/filters/batch payload。原键恢复到另一ID、租户/请求摘要/键摘要冲突均整笔回滚，包括先前响应清除。锁定匹配订阅后清名称、筛选、明文键、所有batch和seen，暂停并清next_poll_at；独立事实和费用保留。首次墓碑不被后续原因/时间覆盖，重复恢复仍清后来重新出现的内容，不因存在回执跳过。

原恢复回执和任务附表不改列，新增独立 `sf_capability_subscription_erasure_replays` 计数附表。v1继续只恢复事实/响应，v2只恢复任务/事实/响应，subscription_entries=0；库内存在订阅墓碑时拒绝旧范围重放。旧版本本身不能证明恢复了订阅，不能给绑定了v3要求的启动门禁放行。

恢复门禁现在要求本进程完成固定ID/SHA256匹配的v3重放并提交。每次受保护访问同时检查订阅计数附表；附表丢失/冲突重新关闭，补回回执不自动开放，仍需重放。旧回执不放行新进程，第二实例独立关闭。升级后需重新从独立最新记录生成/固定v3检查点，在每个进程绑定后恢复，不能继续拿v2证明启动。

```python
from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_restore_gate import RestoreGate

# 停止所有HTTP/worker；data已初始化独立新域表。
recovery = DataErasureCheckpoint(data, configured_response_store)
recovery.initialize()
# 最新v3文件及摘要来自独立保管记录，不能从被恢复的旧库推断最新。
gate = RestoreGate(data, checkpoint_id=latest_v3_id, checkpoint_hash=latest_v3_sha256)
proof = recovery.replay_file(private_checkpoint_path, independent_signing_key,
                             expected_hash=latest_v3_sha256)
# 成功后才挂载新域路由/启动worker。
```

没有新增客户删除/恢复路由、付费调用或自动恢复。capabilities/fields和主OpenAPI1.9.0同步v3范围及503语义。本地合成旧库测试覆盖四域订阅/事实/响应复活、原行缺失、原键换ID、身份冲突回滚、扫描/条数上限、无效签名/重复身份、v1/v2兼容与门禁拒绝、附表丢失锁闭及本地真实HTTP鉴权/五格式导出410。

仍未完成：独立最新检查点自动保管、正式启动装配与真实恢复演练、许可整域撤权/未关联副本清理、最终检查到socket发送严格屏障、平台删除证明和外部文件责任；真实来源许可/Key/预算/费用/金标、正式挂载、Whale四域固定版本读回、规模与七天仍未验收。逻辑清除不是PG/WAL/磁盘擦除。运行中管理员任意SQL覆盖库不在保证范围，必须停服并以新进程核验最新外部固定检查点。

最终测试、源摘要和资源清理见 `state/data-capabilities-20261110/subscription-checkpoint-manifest.json`。

第二十五阶段最终隔离回归 **1419 passed / 749 subtests / 0 skipped / 0 failed**（208.01秒）；专项 **154 passed / 127 subtests**（28.32秒，与全量重叠）。618个OpenAPI引用与v3范围/恢复门禁/双层鉴权合同通过。三套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/subscription-checkpoint-manifest.json`；GOV-04及真实全需求仍未完整验收。
