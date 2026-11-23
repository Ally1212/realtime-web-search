# Google Web 实时采集 100 条：测试与修复（2026-11-23）

## 中台接口直连测试

直接调用 Marsyoo `POST /v1/live-queries`（不经过网站），随机英文关键词：

| 请求 `max_items` | 关键词 | 平台返回 | 最终状态 | 读回文档数 | 耗时 |
|---|---|---|---|---|---|
| 100 | sourdough starter troubleshooting | 100（未缩减） | succeeded | **100** | 623 秒 |
| 100 | beginner pottery wheel techniques | 100 | succeeded | **83**（该词真实结果不足） | 724 秒 |
| 200 | cast iron skillet seasoning | HTTP 400 | 拒绝：`max_items must be between 1 and 100` | — | — |

结论：中台单次上限为 100，200 属平台规则拒绝；不足 100 时如实返回，不补造。

## 网站链路发现的问题与修复

1. **期限过短**：平台默认 300 秒，而 100 条实测需 620–730 秒，任务必然超时。
   修复：`web_deadline_seconds()` 按数量缩放（10→300，50→600，100→900）；本地任务期限 15→20 分钟。
2. **重试排到截止时间**：`google_query_inflight` 等重试把 `collection_retry_at` 算成“截止前 25 秒”，任务实际在睡觉直到超时。
   修复：重试时间必须在未来且早于截止；剩余不足即按真实错误码失败。
3. **入库 413**：100 条时每条文档携带整份查询记录（约 1.3 MB/条，合计 139 MB）。
   修复：每条文档只携带自己的一条 occurrence（约 12 KB），页面证据与字段适配只保留一份；读回时按顺序合并并校验 occurrence 身份。
4. **容器 OOM**：768 MB 内存不足以处理 100 条。修复：共享记录 + 内存提升到 2 GB。
5. **批量入库超限**：新增 `ingest_batches()`，按条数与字节（1 MB）分批提交 receipts 与 documents。
6. **网站导出 503**：compact 导出只认旧 `response.data`，中台任务没有该结构。
   修复：导出时用固定版本 `middle_record.occurrences` 生成行；CSV/JSONL 均恢复。

## 最终网站验证

任务 `9b7b5474-78ee-444a-880a-722889b90158`（`indoor herb garden lighting`，`max_items=100`）：

- 状态：`succeeded`，阶段 `readback_verified`
- 中台固定版本读回：100 个文档，100 条 occurrence，hash 校验通过
- 下载：CSV 200（100 行 + 表头），JSONL 200
- 采集期间无 413、无 OOM、无重启

## 测试

相关回归 95 项通过（含 13 项依赖隔离数据库的既有跳过）。全量 discover 受镜像缺少 pytest 影响，68 项导入错误属环境问题，不作为失败结论。

## 限制

- 单次最多 100 条；200 条不被平台接受。
- 100 条采集约需 10–12 分钟，期间任务保持 running 属正常。
- 结果数量取决于 Google 当次可见结果，可能少于请求值，不会补造。
- 每条链接文档只含自身 occurrence；页面级证据只在首条文档保留，读回时合并。

## 部署

- 采集器：`/home/azureuser/searchflow-releases/web-live-deadline-delivery-20261119/realtime`（middle_live_runtime.py、middle_ingest.py、middle_direct.py、live_collection.py、middle_platform_client.py），容器内存 2 GB，重启 `searchflow-middle-live-1`。
- 网站：`google-workbench-backend/code/realtime`（product.py 最小期限补丁、product_http.py 中台导出补丁），重启 web/prd。
- 备份：`/home/azureuser/searchflow-releases/web-live-{deadline,batching,slicing,export}-20261123/`。
