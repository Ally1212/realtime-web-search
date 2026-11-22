# Maps 数量契约与 Trends 限流恢复修复（2026-11-20）

## 问题与根因

- Google Maps 中台任务允许 `max_items=1..100`，但采集端在执行前静默执行 `min(max_items, 21)`。因此请求100条会被缩减为21条，无法完成百条验证，也没有明确拒绝或部分结果契约。
- Google Trends 的 HTTP 429 会映射为 `upstream_rate_limited`，但该错误不在可恢复集合中；任务会立即失败。Trends 同时缓存失败120秒，简单15秒重试也只会反复读取同一失败快照。

## 修复

- Maps：中台采集合同改为明确只接受1–21；21原样传递，22/100明确 `invalid_parameter`，不再静默缩减。当前单次快速采集能力仍是最多21家；百条需求需由调用方拆分不同地点/查询或后续实现分页合同，不能冒充单次支持100家。
- Trends：新增统一的可恢复采集错误集合，将 `upstream_rate_limited` 纳入；429等待120秒（与失败缓存TTL一致）后在原任务期限内重试。普通代理/传输故障仍为15秒。网站任务与平台直派两条路径均使用同一分类和等待策略。

## 测试

- 针对性回归28项通过。
- Maps、Trends、中台运行器及直派相关回归129项通过，21项依赖外部数据库的既有跳过，无失败。
- 新增验证覆盖Maps 21边界、22/100拒绝、带conditions一致性，以及Trends 429保持等待、等待期不重采、到期恢复。

## 部署与自测

- 生产备份：`/home/azureuser/searchflow-releases/maps-trends-fix-20261120/backup/`。
- 更新Maps冻结运行目录的 `middle_direct.py`；更新Trends冻结运行目录的 `live_collection.py`、`middle_direct.py`、`middle_live_runtime.py`。
- 重启 `google-workbench-backend-maps-1` 与 `searchflow-middle-trends-1`；两者运行正常。
- 生产运行时断言：Maps 21通过、100明确拒绝；Trends 429退避合同为120秒。
- 真实Trends经生产代理池请求成功，返回93个0–100相对热度点。
- 最终Maps/Trends直派活动任务0、网站Maps排队/运行0、客户额度预留0；未开启持续采集、正文或自动上传。

## 限制

- Google Trends 公网直连仍可能返回429；生产必须走已配置代理池。该修复解决的是“429立即终止且无法恢复”，不保证上游永不限流。
- Maps 单次上限仍是21家。本次修复保证合同诚实一致，不等同于新增100家分页采集能力。
