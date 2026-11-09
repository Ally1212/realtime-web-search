# Google 研究工作流本地契约

日期：2026-11-04

本阶段实现 `GO-API-037`、`GO-API-038`、`GO-API-039`、`GO-API-043`、
`GO-API-045` 的本地控制面、证据详情和确定性报告基础。它们只在现有租户、
客户 Key、资产 grant、字段用途和期限门禁下运行；未部署到生产，也没有连接真实
Whale reader、计费/额度源、LLM 或监测调度器。

## 证据详情

- 每个选中 SERP run 生成稳定 `evidence_id`；投影条目和导出字段同时返回该 ID。
  `sf_google_serp_runs.evidence_id` 有租户内唯一索引，旧行在初始化时回填。
- `GET /v1/google/evidence/{id}` 只解析当前租户的一条固定观测。读取时重新检查
  当前资产 grant、`internal_display` 用途、证据期限、固定 Whale 文档版本/hash，
  并由独立 reader 重新读取、复算投影 hash。
- 响应包含固定 source adapter/API、license、parser、projection 版本、source hash、
  grant 版本、适用的指标定义和可加性。reader 失败、撤权、到期或版本变化均失败关闭。
- 该接口返回的是一条许可内证据，不是冻结查询 manifest；多行可重放结果仍须导出或报告。

## 确定性报告

- `POST /v1/google/reports` 接收冻结 item manifest、`report_type=deterministic`
  和可选研究问题，必须带 `Idempotency-Key`。
- `GET /v1/google/reports/{id}` 每次读取都重新核验当前资产 grant、
  `aggregate` 与 `internal_display` 用途、证据期限、固定 Whale 版本/hash 和独立读回。
- 报告包含结构化结论、分子/分母/scope、反例、限制、待验证行动、逐项证据、
  source/license/parser/source API 版本、source hash、grant 版本和 expiry。
- `input_manifest_sha256` 和 `report_sha256` 固定输入与输出。结果篡改、manifest 篡改、
  reader 失败、撤权或到期均失败关闭。
- 不调用 LLM，不生成 AI 结论；AI 报告仍为 P2。

请求示例：

```json
{
  "manifest_id": "google:async-export-manifest:...",
  "report_type": "deterministic",
  "research_question": "Which fixes are worth testing first?"
}
```

## Watchlist

- `POST /v1/google/watchlists` 创建 Key 范围内收藏清单，可附带稳定实体 ID；
  支持幂等 Key 和同键异载荷 `409`。
- `GET /v1/google/watchlists` 仅返回当前客户 Key 的清单。
- `DELETE /v1/google/watchlists/{id}/items/{entity_id}` 只移除 membership，
  不删除 SERP run、item、feature、URL 或报告等源事实；重复删除幂等。
- 当前 GET 列表上限 100，游标分页明确返回 `501`，不做无界扫描。

## 监测配置

- 实现 `POST/GET /v1/google/monitors` 与 `GET/PATCH /v1/google/monitors/{id}`。
- 保存 query/prompt/domain/pool、source、频率、预算和规则；每次创建或更新都检查
  当前 source grant，PATCH 必须匹配版本化 `If-Match`，冲突返回 `412`。
- 新配置固定 `enabled=false`、`scheduler_state=not_connected_no_scheduler`，
  不写入旧 `sf_monitors`、不创建 campaign、不启动采集。尝试 `enabled=true` 返回 `501`。

## 管理用量

- `GET /v1/google/usage` 仅允许租户 `manager` 角色读取，仍受 Key、grant 和
  rights version 门禁。
- 返回本地控制面的 analysis job、export、report、monitor、watchlist 数量和状态，
  以及 5 次/秒、2 并发的读限流观测值。
- `provider_usage`、费用、币种结算和源额度固定为 `not_covered`；没有计费账本时
  不返回猜测值、零费用或伪造预算。

## 验证边界

本地全量回归见
`state/google-prd-v1-20261104/phase9/full-regression-final.log`；OpenAPI 验证见
`state/google-prd-v1-20261104/phase9/openapi-validation.log`。

## 生产运行时注入门禁

- `SEARCHFLOW_GOOGLE_READER_MODULE` 只接受 `module:callable`；合同引用、
  数据集 ID、批准引用、租户批准和生产批准任一缺失都不组装数据服务。
- reader 的实际 `contract_ref`/`dataset_id` 必须与显式环境配置一致，且必须实现
  `read_version`；仅写“approved=true”不能替代合同核验。
- 完整批准后只组装 projection、export、analysis job、report、watchlist、monitor
  和 analytics 本地服务；进程不启动 worker、scheduler 或采集。

这些测试证明隔离 PostgreSQL/HTTP 契约和权限负例，不证明生产部署、真实来源许可、
真实 Whale 读回、provider 用量/账单、LLM 质量、监测调度、报告页面/CSV/JSONL
三端一致、50 次重放、五条用户 E2E 或连续七天 SLO。整体 PRD 仍未完成。

## 固定 Cohort 分析扩展

`/domains`、`/rankings`、`/comparisons`、`/topics`、`/news/*`、`/insights/*`、
`/articles/{id}` 与 `/ai/*` 已增加本地只读契约。它们只使用显式窗口内、逐 run
复核当前授权与固定版本的 selected SERP facts。

top-k share 保留分子/分母并排除缓存 run；未知位置不补作深度外。新闻发布时间、
AI answer text、正文、CPC/需求和业务因果缺失时均明确 `not_covered`。主题只代表
精确归一化查询候选，事件只代表确定性候选聚类，不是已批准 taxonomy 或人工金标。

最新全量见 `state/google-prd-v1-20261104/phase9/full-regression-final.log`；
OpenAPI 见同目录 `openapi-validation.log`。方法与限制详见
`docs/google-domain-analytics-2026-11-04.md`。工作台、真实 reader/source、
三端一致和生产验收仍未完成。
