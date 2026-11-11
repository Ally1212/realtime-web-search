# 新数据域显式装配与启停（本地第二十六阶段）

新增 `CapabilityDeployment`，将一个新域service、当前授权解析器、响应仓、执行器集合、API运行时及固定v3恢复门禁装配为同一生命周期。默认不加载，构造不连接数据库、不创建表、不启动线程、不读取环境或凭据，不采购/调用来源、不改生产挂载。

## 装配约束与顺序

调用方显式提供1–16个已配置且停止的CapabilityExecutor。每个执行器必须与API、响应仓共享同一service对象、同一当前grant解析器及同一response_store；拒绝重复capability/provider组合、另一装配已占用的执行器、未满足响应仓要求的适配器。每个来源账号/资产仍须正确选择共享scope，组件无法从字符串推断两个scope是否实际属于同一供应商账号。限流策略冲突仍由数据库策略登记拒绝，不能借装配提高并发。

1. `initialize()` 只初始化独立新域表、响应策略、恢复回执和执行器策略；不配置预算，不执行旧正文/hash迁移，不启动采集。中途失败保持关闭，已创建的独立表可能保留，可修复后重试。
2. `recover_file(path, signing_key)` 使用构造时固定的检查点ID和SHA256，完整验证并重放v3；任一失败关闭门禁。不接受运行中更换摘要或用旧库回执自行放行。
3. `start()` 启动全部执行器。统一admission回调在全部启动完成前阻止领取来源任务；部分启动失败，先关闭准入和恢复门禁，再停止已经启动的worker，不发布API。API读取同样要求装配处于running，避免重启期间旧挂载提前开放。
4. `mount(existing_authenticated_server)` 显式设置既有server的data_capability_runtime，不创建监听器，不替代X-SearchFlow-Access-Key与Bearer鉴权，不覆盖其他运行时。单实例最多8个明确挂载。
5. `stop(timeout=5)` 先关闭访问和新任务，再在总超时预算内等待worker。返回stopped和alive_workers；在途线程仍活着时不得假称退出，不允许初始化、恢复或解绑。已发出请求仍须真实记账，已知费用进入对账、未知费用继续保留，不能当免费取消。

停止后保留本实例的既有server挂载，由关闭的门禁返回503；原搜索路由继续可用。全部worker退出后，`unmount(server)` 只移除本实例仍拥有的挂载，不删除他人后来替换的值。再次启动必须重新重放固定检查点；最新pin轮换需要新service/新装配实例。worker意外退出会失去就绪状态，状态方法同时显示实际alive，不自动重试来源或重启线程。

```python
from realtime.data_capability_deployment import CapabilityDeployment

# approved_executors、current_grant_for、response_store均由可信启动代码明确构造。
# 每个executor的service/grant_for/response_store须与下面完全相同。
app = CapabilityDeployment(data, current_grant_for,
    executors=approved_executors, response_store=response_store,
    cursor_secret=private_cursor_secret,
    checkpoint_id=independently_retained_latest_v3_id,
    checkpoint_hash=independently_retained_latest_v3_sha256)
app.initialize()
proof = app.recover_file(private_checkpoint_path, independent_signing_key)
app.start()
app.mount(existing_authenticated_server)
# 关闭时先保持HTTP服务存活，让已关闭的新域明确返回503。
result = app.stop(timeout=5)
# 仅result['stopped']为true后，才可解绑或重放恢复。
```

## 验证与边界

隔离验证真实PostgreSQL与合成来源四域：零副作用构造、初始化/恢复/启动顺序、固定文件摘要失败、配置错配、重复提供方、部分启动失败零源调用、重新恢复启动、共享scope、每任务一次账单、停止后503、在途超时仍alive且不能恢复、已知费用隔离及挂载所有权。真实本地HTTP验证四域提交/终态/数据/ZIP、外租户404、访问门401和旧搜索可用。

这是同进程候选装配，尚未接入正式serve钩子或systemd；独立HTTP/worker多进程的监督与外部健康交接没有在本阶段实现。未将合成adapter替换为真实来源，未新增授权/预算、未真实收费、未开启生产自动采集/正文/Whale/监测。真实来源许可/Key/费用/金标、Whale各域固定版本读回、独立检查点自动保管、严格发送屏障、整域撤权、规模与七天仍未通过。主OpenAPI保持1.9.0，无新客户运维端点，capabilities声明显式装配范围。

最终证据见 `state/data-capabilities-20261110/deployment-manifest.json`。

第二十六阶段最终隔离回归 **1427 passed / 753 subtests / 0 skipped / 0 failed**（206.57秒）；专项 **162 passed / 131 subtests**（31.61秒，与全量重叠）。618个OpenAPI引用、显式装配目录/无客户运维端点/双层鉴权合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产部署/恢复/删除、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/deployment-manifest.json`；真实接通与全需求仍未完成。
