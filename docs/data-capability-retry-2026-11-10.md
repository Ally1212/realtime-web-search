# 关键词量明确失败子集重试（2026-11-10）

本地新增显式重试合同，不自动重发来源。`data_capability_retry.py`派生只读范围，使用既有队列/预算/鉴权与执行器；默认未挂载实例仍不接收新任务。本阶段未部署或调用真实供应商。

## 范围与费用

GET `/v1/jobs/{id}/retry`只读取当前获准且未过期的固定父事实，返回plan_hash、父job/observation/payload/snapshot摘要、每个child_input_index对应parent_input_index、排除项及现有子任务。无来源请求、无预算预留。

仅 `keyword_volume` 已结算、未取消的 partial 父任务可以派生。只有输入映射明确source_error且record_id为空的输入可进入；not_observed、unknown、低量、0、缺失月份、已有任何关联记录或存在成功重复输入/相同返回词均不自动作为失败子集。不会从供应商错误文字、空返回或未出现推测可重试状态。不将未知账单/在途/失败整批请求当作安全重试许可。

当前同一关键词部分月份成功时，不重发整个关键词以重新获取所有月份；这需要来源支持更窄范围、单独计费与真实失败依据。Google Ads原生接口未提供逐词partial_failure，正常“未返回”不是source_error，因此不会仅因Google Ads返回较少词而触发此重试。

POST `/v1/jobs/{id}/retry`要求新Idempotency-Key、刚读取的plan_hash和新的budget。服务器派生参数，客户不能改关键词、网络、月份、国家、语言或来源。新任务重新核对当前准入、执行器健康和日/月预算；继承父条件，只收录可重试输入。重复输入在映射保留，供应商适配器按自身批准规则去重请求。

新 `sf_capability_retries` 保存不可变父子关系及范围，独立于旧SERP/正文表。初始化仅由既有显式CapabilityData.initialize执行，不自动运行旧迁移。父行、租户advisory锁、子任务、预算预留、事件及唯一关系在同一数据库事务提交；失败全部回滚。同一父观测固定版本最多一个子任务（包括缓存复用到其他父任务的同一观测）：同key/plan/预算重放返回原子任务，不要求重新有可用执行器；换key返回409 retry_already_created，计划改变409 version_conflict，复用其他普通任务key返回409 idempotency_conflict，不附着无关任务。

缓存别名不能绕过唯一观测索引另建子任务。每个子任务独立冻结预算、确认实际费用、保存事实、形成订阅事件。父费用不退款或转给子任务，成功结果不抹掉；父ZIP字节不变。不将新子结果暗中合并成父观测修订，也不宣称提供商未收取新的合法重试费用。若子任务仍部分失败，可以按其独立固定版本重新检查下一次显式重试。

GET `/v1/jobs/{id}/lineage`返回is_retry及固定父版本/输入映射，非重试任务返回is_retry=false。读取重查子任务、父任务及父观测权限/保留期；跨租户404，撤权/过期拒绝，不从关系表泄漏旧关键词。

## 客户合同和示例

主OpenAPI1.7.0/capabilities/fields同步retry策略和三个操作；仍要求站点访问Key与客户Bearer，状态/轮询/数据/五格式导出/订阅沿用现有子任务接口。

```bash
# 使用已有环境变量，不在命令中写入真实凭据。
# BASE、PARENT_JOB_ID、SITE_ACCESS_KEY、CUSTOMER_KEY由调用者提供。
curl --fail-with-body --silent --show-error "$BASE/v1/jobs/$PARENT_JOB_ID/retry" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" \
  -H "Authorization: Bearer $CUSTOMER_KEY" > retry-plan.json
# 先检查eligible、excluded、creation_available，明确批准新的费用。
# 将返回的plan_hash写入本地请求文件；不由示例自动触发付费重试。
# retry-request.json: {"plan_hash":"<返回的64位摘要>","budget":{"maximum_cost":"<批准上限>","currency":"USD"}}
curl --fail-with-body --silent --show-error "$BASE/v1/jobs/$PARENT_JOB_ID/retry" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" \
  -H "Authorization: Bearer $CUSTOMER_KEY" \
  -H "Idempotency-Key: $RETRY_REQUEST_KEY" -H 'Content-Type: application/json' \
  --data-binary @retry-request.json
# 接受后的status_url/data_url/export和订阅按子任务继续读取。
```

## 验证和限制

测试为合成来源事实与隔离PostgreSQL/客户HTTP：显式失败/乱序/重复输入、非失败状态、并发不同key、同key重放与预算冲突、原子回滚、父导出不变、子费用与事实隔离、两层鉴权、租户/撤权、父状态/未知费用与无执行器。最终计数与清理见 `state/data-capabilities-20261110/retry-manifest.json`。

本阶段未新增网页重试按钮，未做供应商真实失败/重试或实际账单对照；来源可重试错误分类、指数退避与最大窗口、单词部分月份的最小计费范围、完整网页操作仍需继续。已创建子任务若取消或整批失败，本阶段不会另建替代子任务；需后续结合“未进入来源/已知费用/未知费用”的明确证据实现有界续试，不能删除唯一关系绕过计费保护。当前只提供有明确失败依据的显式子任务，不据此宣称全部可靠性/重试要求完成。真实许可/Key/预算、正式挂载、平台各域固定版本读回、真实金标/规模/七天验收仍未通过。

最终隔离全量1292 passed / 653 subtests / 0 skipped，157.69秒；专项115 passed / 223 subtests，33.01秒，与全量重叠。595个OpenAPI本地引用解析、路由/目录/fields及示例bash语法通过；两套本轮PostgreSQL/Redis和测试HTTP/worker已退出，端口关闭、自己的空socket目录已删除；本轮没有新浏览器资源。

## 第十六阶段网页跟进

显式失败子集重试现已接入本地候选工作台，包含范围/费用确认、已有子任务、父子映射、迟到响应隔离与未知提交后的只读找回；详见 [网页实现与验证](data-capability-retry-ui-2026-11-10.md)。上文“未新增网页按钮”为第十五阶段历史状态；来源真实重试、最小月份范围及全量可靠性边界不变。
