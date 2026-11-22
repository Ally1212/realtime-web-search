# SearchFlow 与 Marsyoo 数据集对应（2026-11-13）

本记录依据仓库代码和已留存验收证据，不是本次线上状态探测；未读取私密凭据。

| SearchFlow 来源 | Marsyoo dataset_id | source_platform | 最新证据范围 |
|---|---|---|---|
| google_web | social_media_raw | google_search | 11月11日正式 Web 闭环已发布；11月13日隔离运行器4条固定版本验收通过 |
| google_news | social_media_raw | google_news | 11月13日10条真实存取、固定版本及导出通过；新后端未生产发布 |
| google_trends | social_media_raw | google_trends | 11月13日10条真实存取、固定版本及导出通过；新后端未生产发布 |
| google_maps | google_maps_raw | google_maps | 11月13日20家及0家样本严格载荷/hash、幂等及CSV/JSONL通过；新后端未生产发布 |
| google_images | social_media_raw | google_search | 用户最新确认复用现有平台，以conditions区分图片模式；assets映射及1张真实历史图片存取/导出通过，正式新查询入口未切换 |

映射依据：[中台小范围存取验收指南](中台小范围存取验收指南.md)、[四来源验收](searchflow-four-source-validation-2026-11-13.md)、[Maps最终验收](searchflow-maps-roundtrip-passed-2026-11-13.md)。代码分别见 [middle_ingest.py](../realtime/middle_ingest.py)、[middle_maps_contract.py](../realtime/middle_maps_contract.py)。`config.py` 的 `WHALE_DATASET_ID` 默认值仍是 `web_raw`，这是可覆盖的代码默认值，不应据此覆盖真实验收使用的 `social_media_raw`。

## 分析数据集是另一层概念

Insights 的 dataset 是本地 SQLite 中按租户保存的固定分析样本，ID 由 tenant、dataset、intent 的摘要生成，并非 Marsyoo dataset_id；目前未找到该分析样本同步到 Marsyoo 的实现。它可读取已存 Web/News/Trends/Images 或导入文本，不能把这四种读取入口等同于四种中台同步授权。依据：[insights.py](../realtime/insights.py) 的 datasets 表、create_dataset；[insights_source.py](../realtime/insights_source.py)；[同站整合记录](searchflow-insights-integration-2026-11-10.md)。

## 状态判读

11月13日统一迁移日志记录了多个历史阻塞，但末尾已记录 Maps 授权修复及真实验收成功；不能继续引用中途“Maps授权未完成”作为当前结论。最新四来源验收明确使用独立PG/运行器，生产worker和网页未重启，不能将“真实中台测试通过”写成“全部现网入口已上线”。对比：[统一迁移时间线](searchflow-middle-unification-2026-11-13.md)、[四来源验收](searchflow-four-source-validation-2026-11-13.md)、[11月11日Web发布](searchflow-middle-first-integration-2026-11-11.md)。
