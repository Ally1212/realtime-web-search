# 私有Feed捕获定向清理与asset隔离（2026-11-06）

关联 GO-ID-002、GO-DAT-010、GO-NFR-007、WHL-GO-04。仅本地代码和隔离验证；没有部署、删除生产数据、创建生产授权/密钥或调用真实来源/平台。

## 清理与归属范围

新增显式 `RightsBoundFeedCapture.cleanup(reason=...)`，仅接受revoked/expired。当前主体必须仍为manager且其subject记录明确分配该grant；grant已撤销/到期后不要求继续持有raw_store读写权，以免无法删除，但不能使用无关manager权限代替scope归属。需registry明确显示source/grant撤销，或本次期限/grant/相关字段已到期；active或原因不符不删除。

该控制面操作持有tenant-rights行的共享锁，现有registry写入使用同一行的更新锁，避免清理过程中恢复许可而删除仍被重新批准使用的数据。它不修改registry记录或epoch；事务含行锁，不能称为read-only事务。文件系统不是数据库事务，部分unlink失败仍可能留下部分清理状态。

本轮发现原journal没有asset/grant绑定，新增加密rights阶段，绑定tenant/asset/grant、adapter/version、source/query/locale。写入、继续处理、恢复读取及清理均核对归属；同租户另一asset/grant不能借其权限操作该attempt。此阶段不含原始响应，不在公共输出中。

显式接入rights包装的attempt现在有最多5个发布文件：rights、intent、原响应、captured、saved。底层remove_records只接受最多5个明确UUID和tenant，先认证全部目标及必需的rights内容，再开始unlink。已知目标的加密崩溃临时文件也可清理；无法归属的临时文件保留并返回 `partial_unclassified_orphans`，不冒充清理完成。

每次unlink序列结束或中断都fsync目录；rights归属证据最后删除，允许中断后按原scope重试。缺失文件幂等计数；任何选定密文/归属认证失败时，本轮不删除其他目标。没有glob删除所有租户文件、没有删除数据库请求/URL历史。

## 测试证据与边界

`state/google-data-closure-20261106/feed-capture-cleanup/targeted-current.log`：95项相关测试全部通过、0跳过，覆盖：

- 撤销/到期删除五个发布记录及一个已知孤儿，同时保留其他tenant和其他attempt；active、无关manager assignment、错误原因均拒绝。
- 同租户另一asset/grant不能读或删；没有rights归属阶段的旧内部journal不能被新grant静默认领，必须另有明确的旧记录处置依据。
- 密文损坏/错key预检、第二个unlink中断后按原范围重试、未知孤儿保留并显式partial。
- 将合成备份恢复为正确600权限后，当前registry仍撤销时读取失败，可再次按同一scope清理。这不代表真实备份系统已删除。
- 实际隔离PostgreSQL通过pg_blocking_pids观察到权限恢复写入等待清理持有的tenant-rights锁，清理完成后才取得下一revision；不是靠sleep假定互斥。
- 既有Web/Feed保存、私有实体、来源权限与恢复测试保留通过。

`targeted-initial.log`保留初测失败：合成恢复用write_bytes重建文件导致默认权限644，被vault正确拒绝；改为保持600的正确恢复fixture，未放宽存储权限检查。

完整隔离回归1002项通过，0失败/错误/跳过；结果见本目录 `full-regression.json`。父进程socket审计记录非loopback连接尝试0，不由此推断任意子进程的网络行为。测试schema已全部清理，专用PostgreSQL/Redis已停止并确认端口关闭。仅本任务PostgreSQL/Redis和Mock/既有loopback HTTP；不能把合成源、局部清理或通过数量替代真实采集、正式平台终态或ReaderAdmission。

## 尚未完成

- 当前结果明确 `backup_erasure_verified=false`、`platform_erasure_verified=false`，只证明本vault中已识别目标的unlink；不声称物理介质擦除、异机/离线备份、平台索引/向量/缓存/导出副本清除。
- 未知临时文件、旧未绑定journal、已经变化且无法匹配的批准引用需要有归属证据的操作员处置，不扩大删除范围。
- 固定attempt控制清单和拒绝重放策略仍需获准的保留规则；清理不授权重用旧ID。若连registry与旧密文一同从旧备份回滚，仍需外部耐久撤销/删除证据防复活，不能由本地文件删除自证。
- 实际原实体/上下文来源许可、期限、密钥托管/轮换、备份生命周期、冻结审查与真实验收均未齐备；本地默认仍不启用，不新增自动清理守护进程或后台调度。
