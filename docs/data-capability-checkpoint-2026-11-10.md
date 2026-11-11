# 第二十一阶段：事实与响应墓碑的联合恢复（本地候选）

本阶段为 GOV-04 补上独立事实墓碑的备份重放，并将它与响应墓碑绑定成完整检查点。未恢复生产数据库、部署新服务或删除生产数据。

## 行为

- `DataErasureCheckpoint(service,response_store=None)` 是可信运维接口，不是客户HTTP接口。显式 `initialize()` 仅创建独立重放审计表，不运行旧库迁移。响应仓已安装时必须提供匹配数据库范围和策略的仓实例；不允许静默省略响应墓碑。未安装时检查点明确 `responses=null`。
- 单一事务快照导出事实和响应墓碑，外层 HMAC-SHA256 签名绑定 schema/scope、独立 checkpoint_id、生成时间、完整事实列表及带原签名的响应检查点。密钥至少32字节，由操作员在数据库备份之外独立保管；不写入数据库或检查点。
- 事实墓碑只有观测ID、租户、原事件摘要、首次清除时间及安全原因码。检查点不包含来源原文、查询条件、任务参数或供应商凭据；租户ID仍属于受限运维元数据，应按私有文件保管。
- `checkpoint(path,key)` 使用已有共享原子发布器：0600临时文件、fsync、原子替换和目录fsync，再返回检查点ID、规范化SHA256及数量。文件正文为规范化JSON，无额外换行，其字节SHA256等于返回的checkpoint_hash。写失败不发布半文件，超限拒绝截断或覆盖旧完整检查点。
- `replay_file(path,key,expected_hash=...)` 有界读取文件，先核对原文件字节SHA256，再拒绝重复JSON键、非有限值、无效UTF-8或损坏JSON，验证通过前不执行数据库重放。文件正文和签名整体受上限保护；不要求调用方自行无界加载。
- `replay(value,key,expected_hash=...)` 要求操作员提供从独立可信记录取得的固定摘要，先完整验证外/内层签名、schema、scope、UUID、唯一身份、时间和原因码。数据身份或同一事件映射到不同ID时拒绝，事实和响应的整笔重放回滚。
- 在单事务中先重放响应，再重放事实，保留更早的响应清除时间/原因，不被后续事实清理覆盖。事实和响应行不存在时仍保留墓碑；之后同一事件不能创建新观测。既有观测、投影、关联响应及缓存别名交付继续受墓碑阻断；预算、费用、任务终态、原观测ID和原摘要不重写。
- 重放记录 checkpoint_id/hash/count/applied_at，重复ID不能换内容。重放不会因为已有审计回执跳过清除：若同一行后来被旧备份再次覆盖，再次重放仍会清除内容。已存在的首次墓碑保持不可变。

## 并发和资源边界

重放持有恢复独占事务锁，普通事实完成和缓存复用在取得任务行锁前先持有对应共享锁。恢复中的完成调用先等待，解锁后重新检查墓碑，不能从“验证通过、尚未插入”的窗口发布旧事件。没有把来源HTTP调用放进数据库锁内。

检查点最多100000条事实墓碑，响应组件沿用100000条/32MiB上限；外层正文最多64MiB。恢复按受影响租户使用服务端游标扫描ID与事件原文、计算已有事件摘要，既不加载事实payload，也不要求pgcrypto扩展；全批最多扫描1000000条观测。超过任一上限整批失败并回滚，不把部分恢复称为完成。完整恢复可能长时间持锁，只能在停止候选网页与worker后执行；此互斥并非在线无停机恢复方案，未配置分片或百万墓碑协议。

## 运维调用

```python
from realtime.data_capability_checkpoint import DataErasureCheckpoint

recovery = DataErasureCheckpoint(capability_data, configured_response_store)
recovery.initialize()
proof = recovery.checkpoint(private_checkpoint_path, signing_key_bytes)
# proof['checkpoint_hash'] 与 checkpoint_id 应保存到独立可信的最新备份记录。

# 停止候选网页/worker，完成数据库恢复，初始化候选独立表及响应策略后：
result = recovery.replay_file(private_checkpoint_path,
                              signing_key_bytes,
                              expected_hash=independently_pinned_latest_hash)
# 确认检查点范围、清除状态与费用账本，再允许服务启动。
```

签名或文件自身的hash不能证明它是最新文件。调用者必须从数据库备份之外取得最新固定摘要；旧签名+旧hash仍然可以相互匹配。本阶段没有配置外部保管、自动检查点计划、密钥轮换、备份目录绑定或强制服务启动门禁。不能仅凭一次重放回执宣称所有未来恢复都受保护。

OpenAPI仍1.9.0，无新客户路由或事实字段；capabilities更新可信恢复能力与独立最新hash前提，fields仍为原事实模型。原响应检查点版本、单仓重放与TTL入口继续兼容，使用同一套响应重放/原子文件实现。

## 验证与未完成项

隔离PostgreSQL覆盖四域原数据恢复后再次可读、联合重放后清除、重复重放、事实缺失预先墓碑、首个响应清除时间、错误身份整批回滚、同事件换ID拒绝、错误签名/固定摘要/内层篡改/重复条目/scope拒绝、原文不进入检查点、无响应仓、导出/扫描上限、0600文件及失败不覆盖、字节固定摘要/重复JSON键/非有限值/损坏UTF-8与超大文件拒绝、回执不跳过后来复活内容。利用实际pg_locks验证完成操作等待恢复互斥且尚未持有任务行锁，解锁后重新拒绝墓碑。

仍未完成：任务参数、重试计划、订阅筛选/名称及其他元数据的统一物理清除；来源许可整域撤权及未关联在途任务清理；最终读取与socket发送间的严格传输屏障；平台删除证明、外部文件责任和恢复启动门禁；真实来源许可/Key/预算/费用、金标、正式挂载、Whale各域固定版本读回、规模及七天验收。逻辑清除不等于PG/WAL/备份/磁盘安全擦除。

源码、测试和资源清理记录：`state/data-capabilities-20261110/checkpoint-manifest.json`。

最终候选隔离回归 **1385 passed / 720 subtests / 0 skipped / 0 failed**（195.37秒）；专项 **84 passed / 60 subtests**（14.38秒，与全量重叠）。598个OpenAPI本地引用、可信恢复入口的独立固定摘要要求、无新增客户恢复端点、既有双层鉴权/字段目录及示例bash语法通过。本轮三套独立PG/Redis和测试HTTP/worker均已停止、端口关闭，自己的空socket和合成PG目录已删除；日志与源码摘要保留。web.py/serp_page.py保护摘要不变，没有新浏览器资源、生产恢复/删除/部署、真实来源调用/收费或Git提交。
