# Feed 私有捕获的实时权利约束（2026-11-06）

关联 GO-SRC-001、GO-DAT-010、GO-NFR-007。仅本地新增 `RightsBoundFeedCapture` 显式包装器与隔离测试，未部署、未启用、未写入生产registry，未创建密钥或访问真实来源/平台。

## 实际边界

包装器复用现有RightsRegistry/SourceRegistration/Grant/Principal，不创建另一套批准系统。每次检查使用同一个repeatable-read、read-only事务读取当前subject、epoch、source、grant，语句上限5秒；registry不可用直接失败，不回退旧授权。阶段写入前后、恢复读取前后分别重查；旧principal在任何registry epoch/范围变更后失效，需要按现有流程重新取得有效主体。

必须满足：

- 显式tenant/subject，存储写入要求manager，私有恢复读取要求有效主体角色；grant绑定实际adapter/version、字段、用途和当前版本。构造默认production=True，未获生产批准不放行。
- 新闻入口固定News RSS search，Trending Now固定trending RSS；country必须在coverage中，News language也需明确覆盖。Trending Now仅允许trending-now上下文，不冒充关键词历史曲线。query/locale/source与本次执行清单一致。
- `raw_response_entity` 和 `capture_context` 两项都必须显式允许raw_store；恢复另需internal_display。它们是本地完整实体/私有上下文权利标识，**不是正式Whale schema或已取得许可**。标题/摘要权利不自动扩大为整个XML及未知字段。
- 私有expiry不得超过grant与任一字段deadline，且必须仍存活；原vault的最多24小时上限保留。journal授权引用必须等于source批准引用。许可较原期限缩短时拒绝，不自动延长期限或改写旧密文。

`begin`在来源请求前检查；请求期间撤权会在sink检查时阻止实体写入/数据库保存。写文件或读取期间变更也会在返回前再次拒绝。文件系统、registry和外部HTTP不是一个事务，不能宣称撤销与文件写入完全线性化：已写的密文可能存在，请求也可能已经发生；失败不授权自动重放。

这是可信内部执行器的包装层。直接vault/journal仍是内部原语，持有密钥的内部代码可以直接访问；没有新增面向客户的原始数据读取接口。生产路径尚未接入，不能声称已保护全部现有采集、旧数据、API、备份或平台副本。

## 验证

`state/google-data-closure-20261106/feed-capture-rights/targeted-current.log`：85项相关测试通过，0失败/跳过。包含两来源独立入口与范围、未授权字段/用途、未批准生产、缩短期限、失效subject/grant、请求中撤权、读取前后撤权、写密文后撤权、registry不可用、固定attempt恢复以及现有Web/Feed保存路径。全部批准记录都在本任务临时PostgreSQL schema中；源HTTP仅Mock/既有loopback测试。

检查通过的正例没有改变registry epoch；撤销写入仅为隔离测试故障注入。写后撤权负例明确验证密文仍存在但不返回引用，以免将访问拒绝冒充删除完成。

初次复用registry fixture时把fixture的DSN属性用于vault路径，导致6次tearDown错误；`targeted-initial.log`保留原记录。更名vault路径属性后，按本任务独立cluster中精确的6个测试schema清理；`initial-fixture-cleanup.json`留存清理范围。未修改原断言或清理生产数据。后续测试schema均正常清理，专用PostgreSQL已停止。

本次未重跑整个工作树；上一阶段971项全量、上一轮64项相关与本轮85项存在重叠且代码时点不同，不可相加或称为当前全量验收。

## 剩余条件

来源/用途/整体实体权利、保存期限、批准主体需实际签核，生产registry最近证据仍为0记录，不自动补齐。用户运行授权、平台可用、正式schema/ReaderAdmission及真实独立采样仍缺失。

下一步是撤权/到期后的定向清除与生命周期审查：当前包装器只阻止继续处理/返回，不负责销毁raw、阶段记录、备份或下游副本；不能据此标记GO-NFR-007完整。全链路撤销、密钥托管/轮换、冻结发布、七天和真实百万行均待完成。

后续增加加密asset/grant归属及显式定向清理，见 `google-feed-capture-cleanup-2026-11-06.md`。现在rights包装的本地attempt最多五个阶段/实体文件；旧85项为先前时点，不覆盖新增归属/清理代码。仍未部署或授予实际来源权利。
