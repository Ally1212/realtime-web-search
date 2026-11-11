# 新数据域拉取订阅（本地候选，2026-11-10）

本阶段在现有主客户 `/v1/data/subscriptions` 接入历史热度、搜索量、AIO、AI Mode 的独立观测交付。只读取当前租户已有的新域事实；不创建采集任务、不调用供应商、不预留费用。没有部署，也没有真实来源验收。

## 合同

- 旧调用默认行为、旧订阅与 occurrences/new_urls 保持原合同。新域用 `GET /v1/data/subscriptions?domain=data`；创建正文包含 `domain=data`、`name`、`filters.capability`，`mode` 仅为 observations。创建始终暂停；Idempotency-Key 必填、相同参数重放返回同订阅、不同参数409。新域独立每租户最多100个。
- 四能力为 trends_history / keyword_volume / aio_answer / ai_mode_answer。筛选允许 capability、provider、country、language、from、to；日期指观测存储时间，from包含/to不包含，必须带时区。不支持 URL 去重、设备、页码、limit/cursor 等订阅筛选。
- start_mode 默认 history，可选 now。now 保存创建时所有匹配自有观测ID作为基线，包括当时过期或被撤权的匹配记录；权限恢复不把旧观测冒充新数据。交付不使用时间高水位，晚提交的旧时间戳观测仍可领取。
- `POST /{id}/resume|pause|pull` 用空对象。interval_seconds 默认3600，范围60–604800；空轮询遵守间隔，确认后可立即排空积压。没有调度器自动拉取，resume 不启动采集。pull 不支持 limit：每批一个完整观测，不截断点、月份、块、全文、引用或原响应。
- 批次包含 payload（schema_version、observation_id、event_id、event_type、supersedes、capability、payload_hash、snapshot_hash）和完整 observation。event_id 是稳定观测UUID；event_type 为 new_observation 或显式 supersedes 的 revision。同值新观测保留；同事件重试或本地缓存关联不新建观测，也不产生新订阅事件。
- 一个订阅最多一个待确认批次；订阅行锁串行领取与确认，实例重建或并行重试返回同批次。暂停不会丢弃待确认批次，仍可重领。只在 `POST /{id}/batches/{batch_id}/ack` 的 `{ "sha256": "批次摘要" }` 校验成功后写入观测ID确认账本；幂等确认不重复推进。
- `POST /{id}/batches/{batch_id}/discard` 用空对象，丢弃不推进；再次领取可能生成新批次ID，但 event_id 不变。已确认批次不可丢弃，已丢弃批次不可确认。
- `GET /{id}/status` 返回当前许可内精确待处理观测数（包含未确认批次）。`GET /{id}/batches?limit=20&cursor=…` 只返回轻量历史元数据；limit为1–50，签名游标绑定租户/订阅/排序且1小时失效。列表和进度含当前可执行参数许可检查，无法在SQL表达的政策变化会使读取失败，不返回未经授权的数量或翻页信号。
- `GET /{id}/batches/{batch_id}` 读取历史或待确认完整批次。读取、重领、导出及重复确认均重新检查当前供应商许可、用途、币种、保留期、参数能力和不可变事实摘要。过期410、撤权409、跨租户404；所有路由沿用站点访问门和客户Bearer。拥有者即使被撤来源权也能暂停或丢弃，以解除阻塞；此操作不返回事实、不确认交付。

## 完整文件与独立验证

`GET /{id}/batches/{batch_id}/export?format=json|jsonl|csv|zip|manifest` 支持五格式，默认jsonl。新域CSV是一行完整 envelope_json，包含事件与完整观测，不是可直接合计的数值表；JSONL同样一条完整批次行。观测内原响应是否保留或脱敏仍由已有事实合同明确标记。

ZIP包含 batch.json、batch.jsonl、batch.csv、observation.zip 和 manifest.json。外层manifest提供各文件字节数/SHA256、事件及观测数量、固定payload_hash和snapshot_hash；内层observation.zip保留现有父子记录JSONL/CSV与独立manifest。下载不包含确认/丢弃等可变状态，确认前后字节相同。所有文件在内存按需生成，服务器不持久保存可绕过撤权的下载副本；已由客户下载的副本无法撤回。

批次sha256仅为payload的规范JSON SHA256，独立于文件摘要。客户应从已鉴权领取响应保存 batch_id 和 sha256，再验证下载包：

```python
from realtime.data_capability_subscriptions import verify_batch_bundle

verify_batch_bundle(
    open('batch.zip', 'rb').read(),
    expected_batch_id=batch_id,
    expected_batch_sha256=batch_sha256,
)
```

验证器核验外部批次身份/摘要、事件关系、五个文件的成员与大小、CSV/JSONL与JSON内容、内层独立payload/snapshot摘要及父子关系，以及内外观测完全相同。单纯重算文件摘要不能覆盖独立批次证明。导出并发最多2，累计未压缩文件上限256MiB，超限413/繁忙429，不截断、不自动确认。校验/损坏失败不推进进度。

## 调用示例

以下环境变量均为用户本地提供，不在仓库保存真实Key。既有主机须显式挂载新runtime并初始化新域独立表；目前线上未部署此功能，不能把示例当成现成线上支持。

```bash
curl "$SEARCHFLOW_BASE/v1/data/subscriptions" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_CUSTOMER_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: history-curve-delivery-1' \
  --data '{"domain":"data","name":"历史曲线完整观测","mode":"observations","start_mode":"now","filters":{"capability":"trends_history","country":"US"}}'

curl "$SEARCHFLOW_BASE/v1/data/subscriptions/$SUBSCRIPTION_ID/resume" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_CUSTOMER_KEY" \
  -H 'Content-Type: application/json' --data '{}'

curl "$SEARCHFLOW_BASE/v1/data/subscriptions/$SUBSCRIPTION_ID/pull" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_CUSTOMER_KEY" \
  -H 'Content-Type: application/json' --data '{}' -o batch-response.json

curl "$SEARCHFLOW_BASE/v1/data/subscriptions/$SUBSCRIPTION_ID/batches/$BATCH_ID/export?format=zip" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_CUSTOMER_KEY" -o batch.zip
```

从batch-response.json读取batch_id和sha256，独立校验并保存文件后再ack；没有批次时响应有batch=null及paused/not_due/no_eligible_observations原因，不应确认。搜索量、AIO、AI Mode只替换filters.capability，不使用new_urls。

```bash
curl "$SEARCHFLOW_BASE/v1/data/subscriptions/$SUBSCRIPTION_ID/batches/$BATCH_ID/ack" \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_CUSTOMER_KEY" \
  -H 'Content-Type: application/json' --data "{\"sha256\":\"$BATCH_SHA256\"}"
```

## 实现、验证与发布边界

实现：realtime/data_capability_subscriptions.py；初始化仅通过CapabilityData.initialize显式执行独立sf_capability_*订阅/批次/ID账本建表，不扫描或改写旧事实。现有customer_subscriptions.handle最小接入分流；OpenAPI 1.3.0、capabilities及fields同步新域状态和独立事件。

针对性验证和全量回归结果见本阶段 evidence manifest 与 state/data-capabilities-20261110/subscription-*。所有样本均为合成fixture；真实HTTP验证使用独立访问门、真实客户鉴权和独立PostgreSQL，源执行线程是显式fixture。此结果不证明真实供应商、规模、吞吐、SLO或七天端到端通过。

本阶段没有部署、真实源调用、供应商/平台配置、Whale写入或生产开关变更。真实来源/许可与预算、生产执行器、网页、平台映射与读回、真实规模/七天仍待完成。单观测批次明确为当前合同上限，不能宣称高吞吐已验收。

后续发布以实际冻结版本最小差异备份；不覆盖web.py/serp_page.py，不运行历史迁移。回退先停新runtime，再恢复本阶段customer_subscriptions.py及上一候选的新域模块；保留新域事实、确认账本与未知账单，不删表或重放任务。

最终冻结版本回归：`bash scripts/run_acceptance_regression.sh state/data-capabilities-20261110/subscription-regression-final`，**1174 passed、430 subtests passed、0 skipped**，127.89秒。之前定向回归30 passed/8 subtests；最终版本另含参数政策收紧反例。四个本轮独立PostgreSQL均由pg_ctl确认已停止，Redis日志确认退出，HTTP和fixture线程由测试关闭。源码、文档、OpenAPI及测试报告SHA256见`state/data-capabilities-20261110/subscription-manifest.json`。


后续本地网页接入见 [工作台验证记录](data-capability-ui-2026-11-10.md)；仅本地候选，不表示真实来源或生产已接通。
