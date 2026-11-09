# Feed 私有捕获的耐久关联与恢复（2026-11-06）

关联 GO-ID-002、GO-ING-005、GO-DAT-010、GO-SRC-006/007。仅本地实现和隔离验证；没有冻结部署、生产密钥、新Feed调用、平台请求或开关变更。

## 实际改动

`PrivateFeedCaptureJournal` 是默认关闭的显式sink。调用方必须提供固定attempt UUID、tenant、同一保存期限与授权引用、显式密钥注入的vault；这些参数不授予来源许可。记录全部在已有私有vault中AES-GCM加密，受同一总配额、私有目录/文件权限和到期规则约束。source/query/locale/campaign/requested_at保存在密文内，未把查询上下文附到明文AAD或公共响应。

记录阶段是不可覆盖的意图、捕获、数据库保存标记；原响应另存原UUID，阶段记录用该UUID派生的UUID。意图在源请求前写入，使用vault锁下排他的发布；持有同一固定attempt的恢复实例不能再次begin，即使旧查询缓存不存在。原响应文件发布与PostgreSQL提交并非同一事务，因此不能宣称不存在中间状态。

`google_feeds.collect` 仅在显式注入该sink时调用begin/saved阶段；普通可选回调仍兼容。显式固定attempt不会被collect重启时自动换成新UUID。sink或标记写入失败抛错、不缓存、释放lease；数据库已提交后的标记失败保留为待对账，不自动重写数据库。

`reconcile` 必须读取已有固定attempt记录，并调用注入的只读SQL reader：

| 证据 | 结果与限制 |
| --- | --- |
| 只有意图、没有完整原响应 | source_outcome_unknown，不把缺失当成源请求未发生 |
| 原响应已发布但captured标记未写 | 按固定UUID发现并认证完整原实体，仍保留标记缺失 |
| 原响应在、数据库无该请求 | capture_present_database_missing；不自动补写或重新采集 |
| 数据库已提交、saved标记未写 | 逐项核对request/campaign/source/query/page/locale/HTTP状态，保留标记缺失，不补标记 |
| 标记齐全 | 同样核对当前数据库，不以本地saved标记代替SQL证据 |
| 密文篡改、错tenant/key/attempt、期限/授权引用不一致或到期 | 拒绝；不能把认证失败当成不存在 |

所有返回都明确 `replay_authorized=false`、`journal_changed=false`、`database_writes=0`、`formal_admission=false`。当前恢复只核对上下文和HTTP状态，`payload_roundtrip_verified=false`；它不是XML解析投影逐字段核验，更不是平台固定版本读回。

vault增加带认证binding的只读 `read_record`，原 `read` 仍只返回实体bytes；私有阶段JSON使用独立representation及null HTTP状态，不冒充来源响应。新增缺失记录异常仍为ValueError子类，供恢复区别“缺失”和“无效/认证失败”。旧HTTP实体绑定和读取行为保留。

## 验证

证据 `state/google-data-closure-20261106/feed-capture-recovery/`：

- `unit-initial.log`保留初次失败：旧vault把记录不存在也抛通用无效文件错误，恢复无法区分。修复类型区分后通过，没有把任意ValueError吞成缺失。
- `targeted-current.log`：最终相关64项全部通过、0跳过，包括既有Web原始实体/单次source、Feed映射/存储、原始实体隔离与新恢复逻辑。
- 独立PostgreSQL中，News/Trends各覆盖插入前失败、真实提交后失败、保存标记已写后失败，共6个合成响应窗口；恢复后请求分别为0或1条、查询lease为0、再次collect未调用源HTTP。
- 独立子进程从同一密文恢复并只读对账，文件bytes不变；四线程争抢同一意图仅一个成功；到期拒读，显式purge删除意图/原响应/两个标记共4个本地文件。错误源上下文、SQL上下文、key、tenant、attempt、期限/授权引用和篡改均拒绝。

相关源HTTP全部为Mock或既有Web测试的loopback服务，不是外网真实样本。没有重跑全部工作树测试；上一阶段971项完整回归不覆盖本次新模块，不能与本次64项相加。

## 未解决的边界

- 权利/用途/期限、registry持续查权和撤销传播、密钥托管/轮换、备份副本清除均未因本地journal完成。授权引用不代替批准。
- 固定attempt必须由调用方的耐久执行清单保存并在恢复时原样使用；不能每次启动生成新ID。记录按许可过期清除后也不授予重用旧ID或自动重放许可；跨保留窗口去重需要单独获准的执行清单/事件保留策略，不能暗中无限保留源数据。
- 底层持久化提供fsync/exclusive发布及测试的中断窗口，不宣称已完成真实主机掉电、磁盘损坏、异机恢复或生产长时运行。
- 没有自动扫描/恢复全部任务的守护进程；不创建数据库迁移或恢复自动采集。生产仍使用原Feed投影路径。下一步完成来源权限/期限强制约束及冻结候选审查；真实验收仍需明确授权和可用平台。

后续已补只读registry前后查权包装，见 `google-feed-capture-rights-2026-11-06.md`；仍是未部署局部实现，未授予实际来源权利，也未完成撤销清除。
