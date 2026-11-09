# 数据交付可靠性准备：事件身份、规模与运行门槛

本阶段只修改本地代码并执行隔离数据库测试。不改生产开关、不发真实搜索/上传，不对正式来源或平台准入代签。

## 旧链接事件身份修复（GO-ID-001/003、GO-ING-003、GT-01/06）

已用 RED 测试复现两个缺陷：旧 `link_message()` 以 item/query/source/page 的内容 hash 作为事件身份，不同真实请求返回同值时会合并；同一保存请求的载荷变化却生成新事件，无法检测冲突。

本地修复使用版本 `serp-link-observation-2`：

- URL 对象仍是 `url:<规范化URL SHA256>`；新事件 key 独立绑定真实 request UUID、来源、页码、URL 身份与平台任务，不包含可变内容 hash。新 request 即使值相同也是新事件；重试同一保存请求复用事件。
- `payload_hash` 独立绑定实际保存的 metadata、content 与接收时间。键相同而 payload 不同时，stage 明确报错并回滚当前入队事务；不覆盖已有 outbox、回执、租约或旧任务。
- 旧九条 v1 outbox 不迁移、不重新生成键、不重新上传；运行期已有排队记录继续使用其保存的原消息。版本切换后的历史重新 stage 会生成新版本事件，**发布前必须冻结历史重放范围，不能自动重排全部历史**。

原单测曾要求“更换 request ID 但事件键不变”，与本轮明确要求及 GO-ID-003 冲突；先新增失败测试复现，再将该旧断言改为新请求键必须不同，保留同请求/同平台任务重试键不变的断言。真实隔离数据库测试进一步证明：同值新请求得到独立 outbox；修改旧保存响应后再次 stage 会报 payload 冲突，原消息保持不变，新增任务回滚。

此修复仍是旧链接专用路径：同一个旧上传 job 按 URL 选择最新一条，不能承载全部重复 occurrence、历史 run 或 Trends 话题关系。完整 run/occurrence 交付使用独立快照合同，待正式 schema/ReaderAdmission 批准，不能称 GO-ID-002 全部通过。此次未部署此变化，生产仍保留 v1 行为和旧证明。

## 百万行隔离工具（GO-API-108、GO-SLO-008、GT-11/12/14）

`scripts/google_data_scale_probe.py`：仅接受显式 loopback PostgreSQL host/port，创建 `google_scale_<随机UUID>` 独立 schema。只建两张最小测试表；不调用应用初始化、正文/hash 迁移、Redis、Google、Whale 或其他服务。结束时仅删除自己新建的 schema。

运行条件：已有独立本地 PostgreSQL；输出父目录已存在，空闲磁盘至少16 GiB；新输出目录不存在。DSN只通过指定环境变量读取，不输出异常细节或凭据。

```sh
# 低规模验证；默认10000个 occurrence。
.venv/bin/python -m scripts.google_data_scale_probe /新的私有目录/smoke \
  --occurrences 300

# 合成百万行隔离压力测试，不能充当真实平台交付验收。
.venv/bin/python -m scripts.google_data_scale_probe /新的私有目录/million \
  --occurrences 1000000
```

环境变量默认 `GOOGLE_SCALE_TEST_DATABASE_URL`。所有输入明确标记 `synthetic_benchmark_not_real_observation`，结果 `synthetic_performance_only=true` / `real_million_row_delivery=false`。每个请求最多100个 occurrence，话题/报道分开；最终不足100条的请求也保留，不能补齐伪造计数。

预设百万行场景：10,000个公开合成请求，Web/News/Trending Now轮替，共1,000,000 occurrence，100,000规范化URL对象；900,000重复出现仍须保留。另有三个私有 sentinel，通过同一数据库过滤但不进入导出。100万是总 occurrence 数，不是请求数、唯一URL数或平台消息数。

验收工具执行：

1. 保存独立输入 manifest（request/source/原响应 hash/结果数/话题数）；调用原有 `export_public_data` 写 JSONL、嵌套 CSV、occurrence CSV。
2. `verify_bundle` 逐条核对三文件、字段/顺序/数量/hash；再按输入 manifest 独立核对请求身份、源响应 hash、结果/话题数量，防止三文件同错；最多六个正常读取函数样本逐字段对照，三个私有详情均404。
3. 导出期间调用既有标准详情读取函数，记录真实并发读样本数和经验 P95；不足200样本明确标记。此负载不是公网HTTP或5rps/并发2的完整S1测试。
4. 记录机器/CPU/磁盘、输入生成/导出/核验时长、进程峰值RSS、输出字节数、截断与清理。保留输出与校验清单供复制/恢复；不声称获准永久保存。

百万行工具完成只支持隔离性能和一致性判断；正式真实百万行交付还需获准源与平台固定版本、真实权限/期限、实际HTTP/API/异步任务和交付路径逐项核对。不能将此结果提升为 GO-API-108 或 GO-SLO-008 整条通过。

## 七天真实运行前的执行方案（GO-AC-004、GO-SLO-001～004、GT-07）

尚未获得本轮真实持续采集/批量上传授权，不能启动。准备就绪后应审批具体且可复核的运行包，至少包含：

| 项目 | 运行前需冻结 | 验证方式 |
| --- | --- | --- |
| 来源与权限 | 每个来源adapter/解析版本、字段用途/期限、平台Dataset/schema/质量profile/ReaderAdmission | 平台与来源负责人批准记录；未知项保持未准入 |
| 授权范围 | 允许哪些新搜索、最多请求/付费单位/上传事件；预算与停止条件；开始/结束UTC | 用户明确授权，不将已有单条上传授权扩为批量 |
| 采样计划 | 关键词清单/版本、国家语言/设备/位置、深度与缓存策略、逐个计划点 | 批准的分母与输入manifest不可在见到结果后修改；未支持条件明示 |
| 七天窗口 | 固定连续168小时，保留维护、等待、失败、人工暂停，不换成七个成功日 | 开始/截止UTC及每天完整分账；中断不自动重新开始以剔除故障 |
| 请求到交付账 | planned/requested/parsed/sent/queued/accepted或其他终态/readable/expired分别记录事件与数量单位 | 逐事件可追踪，run/item/HTTP/费用分账，不把queued计为终态 |
| 时效 | source observed/received/ingested/pinned-readable时间分开；缓存源时间未知不替成请求时间 | 每个适用P95至少200个有效样本；负时延/未知/失败/缓存单列 |
| 来源专项 | News依据独立已知集与确证publication；Trends为热门话题或已获准数值源分别命名 | wrapper/RSS顺序不冒充publisher/organic；entries不冒充数值series |
| 失败与恢复 | 429/503、Retry-After、部分400、断网、spool重启/期限、取消、背压、低磁盘 | 先隔离演练；真实平台故障注入及范围需平台同意，不擅自制造生产事故 |
| 私有/期限 | 同URL公开/私有负例，旧游标/导出/报告撤权，expiry/tombstone恢复 | 公共结果不得夹私有事实；旧证据到期明确不可重放 |
| 结束与留证 | 关闭已批准运行、处理在途、终态与固定版本核验、快照导出、费用账、限制 | 最终开关和在途核查；不撤销无关共享Key，不改历史异常 |

PRD的7h/27h、5min、99%等均按D-07待批准目标处理；冻结前不得把建议变成声称通过的SLA。首个测试可缩小明确批准的样本范围，但不能据此标记总PRD的完整市场/设备/样本门槛通过。

证据目录：`state/google-data-closure-20261106/reliability/`。平台旧摘要异常继续依赖旧固定版本原始入站载荷、事件审计和 raw hash 算法；本轮不覆盖异常或篡改证明。

## 已完成的百万行实测

Mac ARM64、11逻辑CPU、Python3.12.11，开始时磁盘空闲约93.87GB。合成公开请求10,000（Web3,334、News3,333、Trending Now3,333），完整导出1,000,000 occurrence、100,000 URL；三种文件合计4,267,784,468字节，complete_scope=true仅指本次合成测试库。3个私有sentinel全部被排除，6个标准详情样本逐字段一致，全部10,000个独立输入响应hash、结果/话题数一致。

生成2.178秒、导出100.695秒、文件/输入核验47.277秒；进程峰值RSS58,621,952字节。导出期间859个实际并发标准详情读样本，0错误、经验P95 0.016880秒。完整结果 `state/google-data-closure-20261106/reliability/million/result.json`，文件校验清单在同目录 `bundle/manifest.json`。独立schema已在finally中删除，导出包保留作本地恢复验证。

## 最终验证与清理

百万行导出包完整复制后，独立校验仍为10,000请求/1,000,000 occurrence，文件manifest hash与原包一致；复制3.278秒，恢复校验40.658秒。证据 `million-restoration.json`。临时恢复副本已清理，原合成导出包和证明保留。这只证明本地导出文件复制恢复，不代表平台灾备、撤权/expiry/tombstone恢复。

22项事件/规模针对性测试通过；完整独立回归 **861通过、0失败、0跳过**，88.233秒，日志 `full-regression.log`；编译及diff空白检查通过。所有benchmark临时schema均已删除；本轮独立PostgreSQL/Redis已停止，并核对本轮端口无监听。未启动本机Docker、未部署生产、未启动采集或上传。下一项仍是旧上传终态/部分失败/重试链路的独立审核，以及正式平台合同和运行授权。
