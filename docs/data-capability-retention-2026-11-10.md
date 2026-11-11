# 第十九阶段：响应仓清除与备份墓碑（本地候选）

本阶段仅覆盖独立 `sf_capability_responses` 响应仓；不等同于 GOV-04 全链路通过。没有生产删除、迁移、真实来源调用、平台清除或发布。

## 已实现

- 清除和到期回收在同一数据库事务中记录不可变首个墓碑，移除 payload、metadata、snapshot_hash，归零容量占用。metadata 包含查询参数，不能仅删除原文而保留它。独立任务、事实、费用和来源调用账本没有被改写。
- 初始化仅触及独立响应表，补录既有 erased/expired 记录并清除其残留元数据。墓碑没有外键，允许在恢复的响应行或任务尚不存在时先重放；之后同一 job 不能重新预留响应。
- `ResponseRetention.export(key)` 导出完整响应墓碑，HMAC-SHA256 签名。签名密钥至少 32 字节，由操作员独立保管，禁止与数据库备份一起保存。检查点只含 response/job/tenant 身份、时间、状态和安全原因码，不含原文、查询参数或密钥。
- `replay(checkpoint,key)` 在事务中验证完整结构、签名、唯一身份和当前库身份；身份冲突整批回滚。重放幂等，不存在的行保留墓碑，已恢复的行删除响应内容；跨租户客户读取仍为 404。墓碑首次时间与原因不因重复清除改变。
- `tick(path,key)` 是供可信调度器显式调用的清理入口：到期回收、完整墓碑快照、同目录 0600 临时文件、fsync、原子替换及目录 fsync，最后提交数据库。写入失败回滚清理；提交前崩溃可能留下更保守的外部删除记录，不会让已经记录的删除失效。未创建或启用任何定时器。
- 完整检查点最多 100,000 条、32 MiB 正文，超过上限明确失败，不发布截断清单。当前为完整快照，不是可直接扩展至百万墓碑的分页协议。

## 可信运维调用示例

```python
from realtime.data_capability_retention import ResponseRetention

retention = ResponseRetention(configured_response_store)
# key 从独立受限文件读取；不得输出。目标目录由操作员预先建立。
retention.tick(checkpoint_path, signing_key_bytes)
# 恢复时服务保持停止，验证并重放独立保存的最新完整检查点。
retention.replay(checkpoint_loaded_from_independent_storage, signing_key_bytes)
```

备份/恢复顺序：停止对外服务与采集；取得最新完整独立检查点；恢复数据库；初始化候选响应仓并重放检查点；核验墓碑覆盖、清除状态和预算/调用账本；通过后才允许恢复服务。旧的合法签名检查点不能证明它就是最新完整检查点；新鲜性、外部保管、备份绑定和恢复准入仍由运维流程负责。本模块没有提供自动启动门禁，也没有把检查点自动上传到外部保管系统。数据库备份本身仍可能含旧原文，必须由备份保留策略及密钥管理保护。

## 验证与边界

隔离 PostgreSQL 测试覆盖旧库响应复活后重放、预先墓碑、跨租户、签名损坏、身份冲突整批回滚、重复重放、到期检查点、文件写失败事务回滚、0600 文件、首个删除不可变及完整导出上限。源码和测试结果见 `state/data-capabilities-20261110/retention-manifest.json`。

尚未覆盖事实库、任务参数、订阅/重试计划、平台副本、已生成或已经交付的外部文件；它们不在 `source_response_only` 声明内。未实现撤权自动扫描清除、全站恢复启动门禁、独立检查点自动保管及正式调度部署。逻辑字段清除不等于 PostgreSQL/WAL/磁盘安全擦除。真实来源许可、账单、金标、Whale 固定版本读回、规模和七天验收仍未完成。

最终候选隔离回归：**1364 passed / 698 subtests / 0 skipped / 0 failed**（184.44秒）；专项 **24 passed / 12 subtests**（4.00秒，与全量重叠，不相加）。三套本轮独立 PostgreSQL/Redis 与测试HTTP/worker已停止，端口均关闭，自己的空socket及合成PG目录已删除；日志和测试证据保留。web.py/serp_page.py保护摘要未变。未创建浏览器资源，未部署、生产删除、外部调用/收费或提交Git。
