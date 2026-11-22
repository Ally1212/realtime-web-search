# 中台统一迁移测试结果（未完成验收）

结论：本地实现与回归已有进展，尚未部署。不能确认所有来源通过中台保存并成功读取，不能标记迁移完成。

## 已有证据

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 第四轮全量回归 | 1476项，1475通过、1项因本机空闲磁盘不足报错、0跳过；不包含之后的最新来源修复 | output/middle-unification-20261113/full-fourth.log |
| 规模测试隔离补测 | 服务器独立PG17与独立测试容器，3项全部通过；保留16GiB门槛，仅303条合成记录，未采集或上传 | output/middle-unification-20261113/remote-scale-regression.log |
| 最新来源、运行器与Maps联合回归 | 隔离UTF8 PostgreSQL下65项通过，0跳过 | output/middle-unification-20261113/live-source-final.log |
| Maps Consumer Key只读接口 | browse返回200；不能证明Maps数据集、live或snapshots授权，也不能证明写入与读取成功 | output/middle-unification-20261113/maps-read-authorization.json |
| 临时规模测试资源清理 | 两个自有容器及临时目录清理核验完成 | output/middle-unification-20261113/remote-scale-cleanup.json |

## 尚未通过的最终验收

1. Maps数据集、专属Collector Key、live查询和快照读取授权配置。最新只读检查服务器state/private没有Maps命名凭据文件；不能单凭命名推断中台后台权限状态。已创建的Maps Consumer Key仅在本地私密配置中，未部署。
2. Maps真实非零和零结果：accepted/duplicate回执、document_id/version_id、固定版本完整platform_payload/hash逐字段一致及CSV/JSONL一致。
3. Web最新真实重复URL版本问题复验；News/Trends真实派单、入库、固定版本读取。历史Web成功不能替代本次代码验收。
4. Images中台来源契约确认并完成接入；目前明确拒绝新增请求，保留历史读取，不能称全部来源迁移完成。
5. 生产候选冻结、部署、旧运行器和入口收尾、回退验证及生产回归。工作树存在原有改动，不能直接整仓覆盖正式冻结代码。

## 当前阻塞

中台管理员登录尚未完成交接。浏览器任务空间294的管理员页面p2已交给用户，未收到登录完成或交回控制的明确消息；没有尝试绕过登录。需用户完成登录并回复“已登录”，再继续创建数据集、Collector凭据及授权并真实联调。无需在聊天中提供密码或Key。

完整过程与限制见 searchflow-middle-unification-2026-11-13.md。以上是阶段测试结果，不是全链路通过报告。
