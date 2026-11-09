# 新版 Google 命名空间：真实历史数据预览

`GET /v1/google/legacy-observations?source=google_web&limit=20` 是只读过渡入口。
它从现有 `serp_requests` 读取实际公开历史请求，支持
`google_web`、`google_news`、`google_trends`，`limit` 默认和最大均为 20。
需要现有网站访问 Key 与客户 Bearer；生产还需显式设置
`SEARCHFLOW_GOOGLE_LEGACY_PREVIEW_ENABLED=true`。不创建搜索、上传、采集任务或新表。

结果只选择 query、请求状态/完成时间、查询条件和对应来源的结果字段。
`serp_task_options.options.customer_private=true` 的任务在 SQL 中排除。
Web 源绝对排名与自然位置分开；News 保留 feed 顺序和未解析跳转状态；
Trends 保留话题和 `approx_traffic` 原始标记，不生成数值历史曲线。
旧记录未知的设备、精确位置、登录态、缓存与源观测时间明确保持未知。

响应中 `provenance=legacy_serp_requests_not_whale_readback`，
`whale_readback=not_connected`，`frozen_manifest=null`。
这条预览不属于 PRD 已验收的 `/observations` 投影，不能用于
Whale 固定版本复算、正式导出、授权资产归并、来源许可证明或七天 SLO。
它只让新版受保护接口查看已经存在的真实公开历史数据。

2026-11-05 验证：隔离 PostgreSQL 私有任务/字段语义 3 项通过；
完整本地回归 790 项通过、0 失败；生产只读核对公开历史请求数为
Web 31,676、News 8,126、Trends 28。

## 网站展示验收

- 网站“Google 研究 → 真实历史数据”已接入上面的受保护只读入口。
  来源切换显示 Web、News、Trends 各 5 条最新公开请求，并可展开每次保存的结果。
- 卡片显示采集完成时间、请求状态、地区/语言、设备、精确位置、缓存和源观测时间。
  未知字段显示“未知”；Web 展示自然位置与源绝对排名；News 展示列表顺序与
  出版者网址解析状态；Trends 展示热门话题和原始搜索量标记。
- 线上 ego(lite) 检查三来源均返回真实卡片；390px 宽度页面无横向溢出。
  `/v1/google/legacy-observations` 无客户 Bearer 返回 401。
- 生产仅发布 `google_legacy.py`、`google_http.py`、`google_research_ui.py` 和
  `workbench_ui.py`，独立 Google 服务与 Web 各重启；未修改 `web.py`、
  `serp_page.py`、采集 worker 或旧数据。发布后 `serp_requests=39,835`、
  `pages=332,768`；持续采集、正文、Whale 上传与领取均为 false。
- 旧 `workbench_ui.py` 备份于 Web 服务器
  `state/google-real-data-ui-20261105/workbench_ui.py.before`；
  旧 `google_http.py` 备份于
  `state/google-legacy-preview-20261105.google_http.before`。

## Whale 固定版本读回准备（2026-11-05）

- 新增 `realtime/google_whale_readback.py`：使用 Consumer Key 的 HTTPS 只读请求，先核对
  文档 ID、Dataset 与来源，再按回执中的文档/版本 ID 读取固定版本；仅返回安全摘要。
  禁止重定向，读取失败不降级为已验证状态。
- `tests/test_google_whale_readback.py` 的 4 项测试通过，覆盖固定版本、错身份、
  401/403、缺版本、重定向和网络错误。旧数据预览的数据库测试需要独立
  `TEST_DATABASE_URL`，本次未在本机重跑；先前完整回归结果为 790 passed。
- 生产 PostgreSQL 只读核查：`whale_serp_outbox` 共 9 条，均为 `queued`，
  9 条均含 `document_id` 和 `version_id`；同一 Dataset、同一来源。
  回执不等于最终入库，也没有证实固定版本可读。
- 当前未取得可用的 Consumer 读取凭据；本次没有调用 Whale 业务读 API，
  没有开启上传或修改生产数据。读回客户端未部署为网站 API，网站继续显示
  `whale_readback=not_connected`。取得批准的 Consumer 权限后，用这 9 个回执中的
  具体版本做真实只读核验，再独立确认正式 run/item schema 与终态。

### 操作核查命令

`scripts/google_whale_readback_probe.py` 可从旧库 `whale_serp_outbox` 只读提取
最多 100 个排队/受理回执，或读取操作员提供的固定版本 JSON 清单。数据库模式使用
只读事务；脚本不调用采集/上传接口，也不输出密钥、文档 ID 或原始内容。

在已配置 `DATABASE_URL`、批准的 `WHALE_CONSUMER_READ_KEY` 和经核实的
`WHALE_READBACK_HTTPS_BASE_URL` 的受控环境中运行：

```bash
.venv/bin/python scripts/google_whale_readback_probe.py --from-outbox --base-url "$WHALE_READBACK_HTTPS_BASE_URL"
```

缺 Consumer Key 返回 `blocked_missing_consumer_key` 且 `attempted=0`；
401/403 首条停止。`readable` 仅证明该固定版本通过 Consumer API 可读，
不代表旧 per-URL 数据已满足新版 run/item schema 或平台终态验收。
本次新增客户端和核查命令合计 9 项测试通过；尚未在生产调用 Consumer API。
