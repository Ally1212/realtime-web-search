# 上传终态、部分批次与租约结算修复（2026-11-06）

本轮依据实际代码、隔离数据库和项目留存的平台 OpenAPI 审核，不调用生产上传、领取或采集接口，不修改生产。终态与独立固定版本读回仍是两个不同验收阶段。

## 已复现的问题与修复

| 需求 | RED 复现的实际问题 | 本地修复与验证范围 |
| --- | --- | --- |
| GO-ING-001/007、WHL-GO-07 | 旧运行器在 pending=0 时将含 queued 的任务标成功，并向平台 complete；ingested_count 也包含 queued | queued 必须等待按保存事件范围查询的 writer accepted/duplicate；writer failed 进入失败。心跳/完成只计 writer accepted，duplicate 单列；旧 received 字段保留网关接收语义，新增 persisted/awaiting_persistence/terminal |
| GO-ING-003、GT-05/06 | HTTP400把整批事件全部设为 rejected，即使平台可能已受理部分 | 用保存的 Dataset/platform/source_name/record_key 调 receipts，分别保留 queued/accepted/duplicate/failed；unknown/pending继续保留原事件等待重试，不把 failed_item 当全批结论 |
| GO-ING-005、GT-06 | 未识别的单条状态被误当拒收；Retry-After只支持数字，未加抖动 | 未知条目保持 pending；秒数/HTTP-date、指数退避与抖动；本批收到429不立刻对账或重发，遵守原Retry-After，稳定事件重试 |
| GO-ING-001/003、GO-ID-003 | 新版spool UPDATE租约条件未命中时，后续SELECT仍可能返回“已结算”并记假小时账 | SQL `UPDATE ... RETURNING` 只对真实成功CAS返回true并记账；旧/过期token、重复回执均不误报成功 |
| GO-ING-007、GT-07 | rejected计入accepted；queued→terminal轮询再次计admission | 拒收记录terminal失败但不计accepted；重复queued轮询不重记，终态只增加terminal；readable始终需独立读回 |

原旧测试曾把 queued 立即算作 succeeded。本轮先新增失败测试，再保留 queued 阶段必须active/不得complete的断言，补上正确来源绑定的独立 writer receipt 后才允许原成功流程完成。没有删除旧 keyword/backfill/本地真实HTTP边界测试，也没有改成直接返回假终态来跳过等待。

## 事件级回执核对

平台合同依据为 `state/marsyoo-readback-20261106/platform-openapi.yaml` 的 `/v1/ingest/receipts`、`CollectorReceiptQuery`、`CollectorIngestReceipt` 及非原子 bulk 描述；已保存的 `one-run-terminal-receipt.json` 是一条真实 accepted 返回形态参考。本轮未重新抓取平台文档或进行平台写入。

每次查询从 outbox 保存的不可变消息构造4字段tuple，另核对数据库record_key/Dataset与消息一致。整个返回必须与请求数量、顺序、4字段tuple相符；已知document/version若冲突则拒绝。响应中未知自由文本/字段不持久化，错误不记录Key、DSN或原HTTP异常文本。

| writer状态 | 已经收到bulk queued的事件 | HTTP400不确定受理事件 |
| --- | --- | --- |
| unknown | 保留queued；标processing_status_unknown；不当失败、不重传 | pending；未知不是未受理证明，重试仍用原键/时间/payload |
| pending | 保留原queued事实，等待；本条pending不是新的queue证明 | pending；不当queue admission |
| queued/retrying | 等待终态，不重传 | queued，后续独立查终态 |
| accepted | accepted；可计writer accepted，但不计readable | accepted；仍须另外取得固定版本读回证明 |
| duplicate | duplicate；重复单列 | duplicate；不能冒充新贡献 |
| failed | rejected + persistence_failed；保留writer失败证明 | rejected；不是schema校验失败的猜测 |

queued查询使用独立120秒租约、`FOR UPDATE SKIP LOCKED`、最少5秒轮询与抖动，不增加源请求或bulk attempts；过期poll lease回执不能改状态。网络/权限/格式失败保留queued和错误阶段，429按Retry-After退避。暂停/停止任务或关闭uploads不领取新的poll lease。

`received` 为 queued+accepted 的兼容网关接收数；`persisted` 为 writer accepted 数；duplicate另列，`terminal`含 accepted+duplicate+rejected。不能据此推算唯一URL数量或完整可读事件数量。旧job每URL只选最新链接的局限仍在，完整run/occurrence候选与正式准入另验。

HTTP400对账本身失败时，不猜测任何一条已拒收；保留pending按原身份重试。400/429后都不生成新事件键或伪造新received_at。未知项可能需要人工修正后重试；旧路径尚未实现跨任务全局退避，也尚未给旧outbox增加正式许可TTL/总容量配置，不宣称旧路径已满足全部spool/背压要求。

## 隔离验证

证据前缀 `state/google-data-closure-20261106/receipts/`：

- `spool-red.log`：错误token误报成功/写账与拒收计admission的复现。`spool-green.log`：首轮20项spool测试通过。
- `legacy-red.log`：queued误完成、400整批拒收、缺少固定回执poll的复现。
- `targeted-final.log`：67项Whale旧路径、spool与小时账关联测试通过，0跳过。包括未受理/已受理混合批次、unknown不假成功、scoped receipt错配拒绝、过期lease、writer duplicate/failed、HTTP-date/抖动、重复轮询不重计和原本地HTTP任务流程。

所有HTTP测试只访问测试进程自己的本机server，不是Marsyoo生产故障注入。没有真实平台400/429/超时/崩溃演练，也没有把夹具回执或本轮隔离测试当作真实平台终态证明。

## 发布边界与剩余缺口

本轮未部署，旧9条queued、既有proof缓存、生产开关与代码均未改；不会重新打开原已完成job去查询或回写历史状态。独立Consumer已存在的Web单样本7/7证明保持此前时间，不因代码测试刷新。

发布前须在当前生产冻结版本上逐文件核对/备份、验证全部自动开关关闭且无活动手动任务、禁止旧事件重排；只应用本轮目标patch，不能整文件覆盖生产已有差异的web.py/serp_page.py，也不运行历史正文/hash迁移。此文不是恢复上传或采集的授权。

仍待完成：正式schema/ReaderAdmission/期限/用途与平台准入、实际三来源新运行与完整固定版本读回、真实部分失败/背压演练、七天及真实百万行交付。旧1条摘要异常仍需平台旧入站载荷及raw hash算法；queued unknown不自动变失败或accepted。

下一项优先检查spool/账本跨连接事务失败时的原子性及重启恢复，再准备可审阅的受限发布差异和运行准入材料；不启动未经批准的运行。

## 最终回归与证据封存

完整独立UTF8 PostgreSQL/Redis回归 **870通过、0失败、0跳过**，90.245秒，日志 `full-regression.log`；67项针对性测试通过，编译与diff空白检查通过。相关测试schema剩余0，本轮独立数据库/Redis已停止并验证本轮端口无监听；未操作其他任务服务。生产未部署、搜索/上传新增0。各测试结论仅覆盖本文本地实现和隔离/本机HTTP范围，不支持正式平台、连续七天或真实百万行交付通过声明。
