# Whale 直派实时查询未领取：排查与修复（2026-11-16）

**后续更新：Maps已支持不传坐标的文字查询，原来的强制位置/max_items=1限制说明仅为修复前历史。当前用法见 [Maps文字查询上线说明](searchflow-maps-text-query-2026-11-16.md)。**

## 结论与接口

Whale/Marsyoo 对接使用 HTTP 实时查询 API，执行是异步的，不是同步 HTTP 请求一直阻塞到搜索结束，也不是 WebSocket 推送。Consumer 发起 `POST /v1/live-queries`，平台生成 `live_query` 采集任务，Collector 使用 `POST /v1/collection/tasks/claim` 领取，执行后提交 `/v1/documents/bulk`（`X-Ingest-Class: realtime`），等待 `/v1/ingest/receipts` 的 accepted/duplicate 和固定版本，再调用任务 complete 提交 document_ids。Consumer 查询状态、读取结果及固定版本载荷。

用户确认同事从 Whale 中台直接发起。原线上四个运行器仅在 SearchFlow 网站数据库有未完成任务时才调用 claim；空闲时每15秒发送心跳，但不检查平台派单。即使碰巧领取其他应用的任务，原 Web/News/Trends 运行器也会以归属不匹配拒绝执行。因此 11月13日网站入口验收通过，不等于 Whale 中台直派也已接通。

## 现场证据

- 采集机四个实时容器11月13日启动，排查开始时重启计数0，最近半小时日志没有错误。
- sf_jobs 中最后的正式网站实时成功任务仍为11月13日；Maps同日后也无新网站任务。旧领取、旧上传均为false。
- Collector `/v1/collectors/me` 返回200；Collector 凭据读取管理员任务列表返回401，未扩大权限或绕过管理员接口。
- 本次直接创建 `lq_3d5aecd91a7ccd7cab556dd87bcde250`，平台查询为 collecting，对应 `ctask_3fb36770a62a376674ea6492dfa47136` 的 task_status 仍为 pending。修复后同一任务被领取并完成4个文档。
- 本地回归先红：`test_whale_dispatch_is_polled_without_website_jobs` 明确失败，期望调用poll、实际0次；接入后转绿。

## 修复范围

新增 `realtime/middle_direct.py` 和私有持久表 `sf_middle_direct_tasks`，在原来源PG领导锁内处理平台直接派发的 live_query。任务、查询、原始结果消息、回执和状态持久化；不会伪造网站客户任务或扣网站客户额度。声明和执行仍受 Collector 来源/数据集/能力约束；Maps要求完整位置/类型条件，不支持的条件拒绝。

运行器空闲时检查平台待领取任务；已有直派任务先恢复处理。领取到已绑定网站的任务时交回原网站任务状态，保持账号、预留额度和原固定版本读回逻辑。网站处理过程中领取到直派任务时转入直派队列，避免归属不符后一直等待lease过期。

结果先持久化再上传，queued/retrying不算成功；accepted/duplicate且有document/version后才complete。取消/暂停信号阻止继续采集和上传；限流及传输错误延后重试。Maps无可回放的提供方请求身份，进程死亡后若只有“已开始”标记、未保存结果，会明确失败，不悄悄重复采集。

只修改生产冻结包中 `middle_live_runtime.py`、`middle_maps_runtime.py`、`maps_worker.py`、`middle_maps_collector.py`，新增 `middle_direct.py`；仅重启四个实时运行器。未覆盖原Web服务、静态页、原采集worker、代理或数据库容器，未开启持续供给、正文或旧Whale队列开关。

## 正式真实直派验证

| 来源 | live query | 结果 | 验证 |
|---|---|---|---|
| Web | lq_57db0482c6ea0d921ef641c7cf791b7d | 3文档 | succeeded、回执固定版本完整载荷及SHA256一致 |
| News | lq_5fa5701eea202c9dd99659340a47b115 | 10文档 | succeeded、回执固定版本完整载荷及SHA256一致 |
| Trends | lq_5ac6fbfceb73cbf95f089f8977502d90 | 10文档 | succeeded、回执固定版本完整载荷及SHA256一致 |
| Maps | lq_c0fe3e474cbbf143b43261edfb7c6bb4 | 1查询快照、20家 | succeeded、固定版本完整载荷及SHA256一致 |

Consumer结果集的document IDs也与回执一致。以上直派Consumer查询未创建网站sf_jobs。首次探测命中Consumer并发/频率限制，复用原幂等键退避；Maps固定版本读取遇一次503，复用同查询读回通过，无重复采集。真实小样本不是长期SLA或负载验收。

## 部署、证据与操作建议

生产备份：`/home/azureuser/projects/google-workbench-backend/state/whale-direct-20261116/backup/`。生产运行目录仍为 `state/middle-release-20261113/candidate/realtime/`。回退前停止新派单，排空/处理 `sf_middle_direct_tasks` 中 running 任务，确认没有网站和Maps在途，再停四运行器、恢复备份模块、移除新模块调用并启动；保留新表和历史数据。

本地证据：`state/whale-direct-20261116/` 的基线、候选、回归日志、direct-search-proof.json、maps-probe.log、website-probe.log；服务端部署摘要及final-state另留同名state目录。Key未写入报告或证据。

同事应使用 live_query 实时接口分别请求 google_search、google_news、google_trends、google_maps。旧keyword_search/backfill/content_detail未重新启用。实时接口的pending/collecting表示处理中，必须跟踪run/task及最后回执，不能仅凭心跳或“8分钟无领取”判断链路最终成功。没有请求的空闲采集器应显示“在线/空闲”；健康检查若判失败，应以实际发起的小查询和截止时间验证为依据，不通过周期性制造假任务维持绿色。

最终验证：独立服务器PG中59项相关Python回归全部通过（86.675秒），本机Node队列测试1项通过，合计60项无跳过。服务器Python镜像不含Node，首次合跑60项的唯一错误为缺少node，已按运行环境拆开验证，未算作功能修复。SearchFlow网站另外真实Web任务 `ce2e3da4-ffbb-444a-8f00-e126a4f1d372` succeeded，3条结果、固定版本读回和JSONL深比较通过。最终reserved=0、网站和Maps在途均0，continuous/content/旧uploads/旧claims均false。最终直派模块SHA256为 `aa619899cacccc54b64fed8992e1988dff9808d1dfcb7dc6816a3c0a8ab340c8`。

本机Docker因本机磁盘空间耗尽转为只读，本任务临时PostgreSQL已停止，但删除该停止容器返回overlay2只读错误；未删除其他项目数据。后续59项验证使用服务器独立临时数据库、独立网络和不含生产凭据的代码容器。

## Maps 复测参数

现场另有直派任务 `ctask_4f4a9f60b816b732eb1de681be1aa144`（查询 `lq_e7ea342936943eb7aed0b69bbdaaca8b`）传 `coffee in Seattle`、max_items=2，但没有conditions，region/language为空。这条已被领取，随后在采集前被参数校验拒绝，状态failed；不是未领取，也没有实际搜索或伪造零结果。

Maps当前使用查询快照契约，一次查询对应1篇快照，`max_items=1`表示1篇快照，不是只返回1家商家。Whale中台直接复测可使用：

```json
{
  "dataset_id": "google_maps_raw",
  "resource_kind": "content",
  "source_platforms": ["google_maps"],
  "query": "coffee",
  "language": "en",
  "region": "US",
  "max_items": 1,
  "deadline_seconds": 300,
  "conditions": [{
    "fact_type": "location",
    "name": "search_area",
    "op": "within",
    "value": {"lat": 47.6062, "lng": -122.3321, "radius_m": 1500}
  }]
}
```

向 `POST /v1/live-queries` 提交，使用Consumer Bearer及新的Idempotency-Key，再轮询返回的live_query_id。不要复用已失败查询的幂等键期待触发新任务。Web/News/Trends数据集为social_media_raw，平台分别google_search/google_news/google_trends，不需要Maps位置条件。
