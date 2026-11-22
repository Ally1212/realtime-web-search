# Maps 小范围真实中台存取测试：通过

本次范围：手动、隔离运行器执行真实Maps查询，经中台实时派单、上传、accepted回执、完成任务、固定版本原始载荷读回、逐字段/hash校验、正式导出函数生成CSV/JSONL并深比较。没有发布网站或开启自动采集。

| 样本 | 实际结果 | 中台保存 | 固定版本读回与hash | 导出 |
|---|---:|---|---|---|
| Singapore coffee / 1500m | 20家 | accepted | 完整一致 | CSV record_json逐条一致；JSONL全部商家一致 |
| 不存在的商家关键词 / Singapore | 0家 | accepted | 完整一致 | CSV仅表头；JSONL保留零结果及查询条件 |

两次幂等重放都复用原查询，未触发重复采集；首轮请求遇429后遵守等待再验，最终均succeeded。此前变更参数复用原key返回409的现场证据仍保留。

修复：提交时包含真实place_count指标及payload.platform_metrics，避免中台补字段改变摘要；逐文档接口缺载荷时用官方只读batch-get读取，绑定document_id/snapshot_id/snapshot_at及固定version.raw_payload_hash，继续严格比较完整payload和实际digest。有稳定id且逐字相同的重复快照行去重，冲突拒绝。

证据目录 output/middle-unification-20261113：maps-small-v2-real-result.json、maps-zero-v2-real-result.json、maps-v2-idempotency-proof.json、maps-verified-export-proof.json、maps-verified-20.csv/jsonl、maps-verified-zero.csv/jsonl、maps-hydration-tests.log（17项通过）。前期空快照、retrying、额外字段hash不一致记录保留，不覆盖为成功。

临时独立Maps容器及服务器自有临时目录已清理。密钥仅私密配置，不在报告与导出中。

限制：这是Maps小范围真实协议流程通过，不代表所有来源已迁移、旧代码全仓清理完毕、正式网站已部署验收，或长期稳定性/SLA已通过。
