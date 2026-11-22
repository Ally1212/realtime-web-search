# SearchFlow → Whale 写入与读回现场测试（2026-11-12）

## 结论

本次 Google Web 实时链路未通过完整闭环。采集器取得 4 条链接，Whale 对 4 条均返回 `accepted` 持久化回执，Consumer live query 返回 `succeeded` 且列出相同 4 个 document ID。固定版本读回时 3 条的 `platform_payload` 哈希与提交内容一致；另 1 条指向 2026-11-11 已存在、缺少本次 `platform_payload` 的版本，无法证明本次任务数据可读。网站拒绝开放下载，最终 `search_deadline_exceeded`、`charged=0`。

## 本次请求

- 网站任务：`ef2b794a-93b6-400c-adef-a059f4b1a8b1`；查询 `sodium ion battery cathode research 2026`，Google Web、US/en。
- Marsyoo live query：`lq_19f2e8c80904dc1ca93b98a9f06162db`；平台采集任务：`ctask_03264be3fb7daa02678dca5ead7474fd`。
- 网站任务创建于 10:43:31 UTC，15 分钟后于 10:58:35 UTC 失败；未重复提交。
- 旧 Whale 上传和平台领取开关均为 `false`，持续采集仍显示暂停。本次是显式网站请求，由独立 `searchflow-middle-live-1` 执行。

## 流转与验证

1. 网站快速请求创建私有 job → Consumer 创建 live query → 独立运行器领取指定平台任务并搜索。
2. 运行器按 `X-Ingest-Class: realtime` 写入 4 条链接；4 条 receipt 均为 `accepted`，含 document/version ID。`queued` 未被当作成功。
3. Consumer live query 为 `succeeded`，结果页有 4 条，document ID 与回执完全一致。
4. Consumer 按 document/version ID 读取固定版本：`doc_42951d89caba264588ab44d4ab0cf1b6`、`doc_4624b1464dcc2fdc21282e93da28183a`、`doc_96421e505fc2889df579f6df382027c7` 的 `platform_payload_hash` 校验通过。
5. `doc_0b06fdba30329770b2e354358e29fa3d` 的回执指向 `ver_970636109edd5decb07499a612f4ed79`；该版本创建于 2026-11-11 10:20:23 UTC，Consumer 固定版本响应没有 `platform_payload`。本次期望的 SHA256 为 `b74d15c74b63b075d6d018cfe66b9420c31a4bbdb978c954a3a12c9d802d05e8`，所以完整记录无法验证。回执为何关联到旧版本需检查 Whale 的版本复用与 live query 结果契约，不能仅凭现象断定原因。
6. 逐条读回时还观察到 HTTP 429、`Retry-After: 1`；运行器日志持续记 `DomainError source_error`。这是读取阶段另一个需要处理的问题；即使消除限流，上述缺失 payload 的固定版本仍不能通过校验。

## 下一步

先确认 Whale 在相同 URL/内容重复写入时，是否承诺返回带本次查询 `platform_payload` 的固定版本，或提供独立的查询观察记录。随后将运行器改为批量读回、尊重 429 退避，并对缺失 payload 给出明确终态，再用重复 URL 和全新 URL 各跑一次端到端测试。当前不能把本次任务或所有来源称为写读闭环通过；Maps 尚无对应中台契约。

## 再次只读复查

用户要求后再次查询同一 live query 与 4 个固定版本，没有重新采集或写入。中台任务仍为 `succeeded`、结果仍是同 4 个 document ID；网站任务仍为 `failed`、`charged=0`。用 Consumer 的文档批量接口和版本批量接口读取：第 1、2、4 条的 `platform_payload_hash` 再次通过；第 3 条的文档和版本都能返回，但固定版本响应仍无 `platform_payload`，报 `Whale pinned platform payload differs`。问题稳定复现，不是一次读取超时或临时 429 所能解释。
