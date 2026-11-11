# 四域平台候选载荷与独立读回核对（本地第二十八阶段）

新增 `data_capability_delivery.py`。覆盖历史热度、关键词搜索量、AIO、AI Mode的完整固定观测；这是本地候选合同 `searchflow-capability-platform-candidate-1` / `searchflow-capability-observation-envelope-1`，不是已注册的Whale ingest schema。没有选择Dataset/content_type、上传、注册、队列、生产配置或客户新路由。

## 候选内容与身份

载荷包含tenant_id、调用方明确指定的source_platform及原完整observation。保留源字段、查询条件、源时间/未知原因、raw状态、series/point/scale、volume/month/input_mapping、answer/block/citation/link结构、顺序、revision/supersedes及partial/truncated。与现有完整JSONL/CSV导出共用投影/重建oracle，计算每类记录数量，不转成SERP链接或七列表格，不添加分析标签。

object_key按tenant/source_platform/observation_id摘要定位；event_key再绑定固定snapshot_hash。新真实观测同值仍有不同ID和事件，缓存别名复用同一个观测不制造新事件，跨租户不共享对象键。纯builder的tenant参数不构成授权，`prepare_candidate(runtime,tenant,observation_id,source_platform=...)`才通过当前租户、来源权限/期限、事实墓碑和恢复门禁读取，并在构建后重新检查版本与权限。载荷最多64MiB，超限拒绝，不截断。

候选完整性是“完整已保存观测”，不是完整任务审计包；独立任务/缓存别名关联、审批原件、费用账目和响应仓多阶段journal不在此载荷。原文仅按观测内raw字段已保存内容及状态交付，不把not_retained补成完整原响应。现有公共快照的platform_delivery=not_submitted保持原值与hash，外部证明另行返回，不改写历史快照。

## 固定版本Consumer核对

新增opt-in `CapabilityReadbackClient`，复用现有Whale GET /v1/documents/{id}与POST /v1/versions/batch-get协议。调用方需先具备真实Consumer、Dataset/source/字段范围授权，并明确目标document/version，不从当前文档元数据推断固定版本正文。

核对项：document/Dataset/source、external_content_id、固定document/version、平台raw_payload_hash、完整platform_payload与独立本地候选canonical JSON、tenant、观测/源payload/snapshot hash、四域记录数、结构关系与顺序。current_version可以已推进，旧固定版本仍可独立核对，明确返回current_document_version_matches=false；缺失固定platform_payload不得用当前metadata替代。JSON数值拼写不自动做语义修正，平台重写类型时会拒绝，真实平台兼容性尚待验收。

证明单独标注version_readable、payload_roundtrip_verified、candidate_contract_only=true、platform_schema_admitted=false、terminal_receipt_verified=false。可读回不代表receipt accepted，不代表平台正式schema/字段用途已准入，也不验证平台质量标记；质量和终态仍需独立证据。没有回执升格或本地任务状态变更。

新客户端采用流式JSON：文档最多1MiB、版本响应最多64MiB+64KiB；限制累计解压后字节，拒绝重复字段、NaN/Infinity、无效UTF-8/JSON及重定向，响应完成/错误均关闭。每请求30秒传输检查窗口加5/15秒连接/读取超时；单次阻塞读取可能超出窗口一个读取超时，不能称严格30秒总期限或SLA。既有旧客户端不变。

```python
from realtime.data_capability_delivery import CapabilityReadbackClient, read_authorized_observation
from realtime.google_whale_readback import ReadbackTarget

# 仅已明确授权的Consumer和目标，方法本身不会授予平台权限。
client = CapabilityReadbackClient(approved_https_base, private_consumer_key)
target = ReadbackTarget(approved_document_id, approved_version_id,
                        approved_dataset_id, approved_source_platform)
result = read_authorized_observation(runtime, authenticated_tenant, observation_id,
    client, target, source_platform=approved_source_platform)
```

高层方法在读回后再次检查本地当前权限、期限、墓碑和固定版本；撤权、到期或清除则不交付候选或证明。最后检查至socket发送严格屏障仍未实现；已经读出的外部副本无法主动撤回，纯低层client不替代租户鉴权。

## 验证与尚缺事项

供应商和平台均为合成响应，测试覆盖四域导出数量一致、同值新观测/租户/修订身份、字段与内容篡改、跨租户零网络调用、固定版本缺失/错位、原始摘要/对象/Dataset不符、资源超限/超时/重复JSON、读回期间撤权/清除/到期。真实隔离PostgreSQL验证授权入口。没有真实Consumer请求、平台写入或真实费用。

仍缺Whale四域正式schema/字段用途冻结、目标Dataset/source准入、完整任务/审批/账目交付合同、上传/终态与独立真实读回、平台删除证明，以及真实来源金标、生产装配、独立检查点保管、整域撤权/严格发送、规模与七天。主OpenAPI1.9.0/capabilities明确候选未接通；不据本轮证明GOV-03/04或全需求通过。

最终源码/测试/清理证据：`state/data-capabilities-20261110/delivery-manifest.json`。

第二十八阶段最终隔离回归 **1441 passed / 805 subtests / 0 skipped / 0 failed**（323.18秒）；专项 **200 passed / 246 subtests**（32.89秒，与全量重叠）。618个OpenAPI引用与候选未接通/不升格回执/既有鉴权合同通过。本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产写入/部署/恢复/删除、真实来源或Consumer请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/delivery-manifest.json`；Whale正式交付及真实全需求仍未完成。
