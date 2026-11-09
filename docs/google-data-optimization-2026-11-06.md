# Google 数据层优化实施记录（2026-11-06）

本轮按用户要求聚焦采集数据标准、完整性、去重、Whale 交付准备、News/Trends 数据语义和导出可靠性。未新增分析、洞察或 AI 报告。本记录不代表总 PRD 数据层全部验收完成。

## 已实现与上线

| 项目 | 实现与边界 |
| --- | --- |
| 统一读取合同 | `google_data.py` 的 `google-public-data-1`：保留请求、原查询、请求条件、条件 hash、来源版本、时间、缓存状态、结果 occurrence、Trends 话题与子报道；源排名与自然排名分列。选定来源字段经凭据过滤后保留，未知字段不填默认事实。 |
| 稳定身份 | 同一 request 的每个顺序位置有独立 occurrence ID；不同 request 保留独立观测，同 URL 对象可去重。条件身份包括保存的完整请求参数；这是旧库适配身份，不冒充已批准的 Whale schema。 |
| URL 安全与去重 | 保留内容参数/路径/子域名；跟踪参数仍采用既有 `google-tracking-v2`。同一次响应中重复 URL 只贡献一次 `is_new_url`，所有出现记录保留；敏感 URL 不生成可交付身份。 |
| 实际采集时间 | Web/RSS 新请求记录实际 requested/received 时间；旧记录未知时间保留 null，不把完成时间或缓存请求时间冒充源观测时间。尚未产生新生产搜索来验收此新增元数据。 |
| News 时间 | 统一核对保存日期与原始 RSS pubDate（带时区、误差不超过 1 秒）；区分缺失、无原始依据、无效、不匹配与 RSS 匹配。RSS 匹配不代表出版者网页独立核验。 |
| Trends | 热门话题、原始流量标记与相关报道分别保留，父子关联可追溯；`numeric_trend_series=null`，不生成历史曲线。 |
| Whale 读回准备 | 固定文档/版本/数据集/来源核对，可附带预期 content/raw/platform payload SHA256；不匹配拒绝。探查中有失败时返回 incomplete，不冒充全部完成。未配置 Consumer 时不访问平台。 |
| 快照导出 | 单个 PostgreSQL repeatable-read、read-only 快照，SQL 排除私有任务；JSONL、无损嵌套 CSV 与 occurrence CSV，附文件 hash、实际计数、筛选与截断状态。SQLite 临时索引用于有界内存的 URL 去重。 |
| 失败与恢复 | 导出写到权限 700 的临时目录，文件权限 600；失败清理，完成后原子发布，不覆盖已有结果；复制/恢复后可校验文件、行数和 JSONL/CSV 一致性。CSV 展示字段防公式，record_json 保留原值。 |
| 大字段 | 真实生产响应触发 CSV 默认 128 KiB 字段上限，已修复；每请求导出上限 64 MiB，超过则明确失败，不发布残缺结果。 |

## 新增接口与命令

网站访问门、现有客户 Bearer 和原 legacy preview 开关继续生效：

- `GET /v1/google/legacy-data?source=google_web&limit=20`：版本化标准数据，最多 50 个请求、keyset 分页，复用旧库来源/查询/域名/时间/状态/国家/语言/设备/页码筛选。
- `GET /v1/google/legacy-data/requests/{request_id}`：单次标准数据与完整 occurrence；私有记录 404。
- 原 `/legacy-observations`、详情和专项接口保留；News 卡片恢复可信 RSS 时间，并附 publication 状态。

上述数据明确标记 `legacy_serp_requests_not_whale_readback`、`whale_readback=not_connected`；不是正式 Whale 固定版本投影。未改变 `/observations` 的生产准入要求。

操作员导出（使用当前环境中的数据库配置，不在命令行放密钥）：

```sh
.venv/bin/python -m realtime.google_data_export /absolute/new-export-directory \
  --source google_web --max-requests 10000
.venv/bin/python -m realtime.google_data_export /absolute/new-export-directory --verify
.venv/bin/python -m scripts.google_data_quality
```

达到请求上限且还有记录时，manifest 显示 `truncated=true`，命令退出码 3。`--verify` 无需数据库。运行时间快照标识不是可长期重开的 PostgreSQL 快照或正式 Whale manifest；导出是操作员持有的数据副本，需要沿用现有保存期限，尚无自动撤回本地文件功能。

## News 时间差异的真实原因

11 月 5 日审计曾报告抽样 1,866 条 News 时间全部缺失。此次生产只读全量核对：**551,494/551,494 条均有保存日期和原始 RSS 日期，而且全部相互匹配**。线上旧 `_item` 额外要求 `collection_method=google_news_rss`，历史记录不满足这一附加字段判断，因此被映射成 null。

此次修复使用实际日期证据核对，不回写或补造源日期。历史审计结果保留，不能继续把原“全部缺失”结果解释为源库丢失。证据 `state/google-data-optimization-20261106/news-time-audit.json`。

## 验证

- 隔离 PostgreSQL/Redis 完整回归：**822 passed，0 failed，0 skipped**，128.547 秒（最终发布版本；此前 821 项回归日志保留）。
- 生产冻结采集源码应用最小补丁后，独立加载该源码的 94 项针对性测试通过。
- 后续导出大字段修复的 6 项数据库/文件测试通过；最新标准数据、导出、私有隔离、Whale hash 与 HTTP 合同的 30 项测试通过，随后敏感 URL 与数据链路 62 项测试通过（这些结果不与全回归简单相加）。
- compileall 与 git diff --check 通过。
- 生产三来源各取两次已存请求，标准接口和导出按相同 request_id 逐字段一致。三个样本包 JSONL/CSV/hash 校验通过；样本包为截断样本，未称为全库导出或百万行压测。
- 在生产独立 Google 容器内用实际客户 Key 执行真实路由处理器，三来源均 200 / 2 请求；无客户 Key 401。未向输出或日志写入密钥。此次不是公网浏览器 E2E 或前置 Nginx 访问门重新验收。
- 原有 IngestSpool 重启 lease、部分批次受理、退避、幂等和撤权测试纳入回归；仍属于隔离测试，不是 Whale 真实平台故障演练。

证据位于 `state/google-data-optimization-20261106/`，包括完整测试日志、生产前后基线、全量时间核对、真实样本回环和生产路由处理器验证。

## 发布与回退

Web 服务器 `/home/azureuser/projects/google-workbench`：仅更新 `google_data.py`、`google_data_export.py`、`google_http.py`、`google_legacy.py`、`google_whale_readback.py`，并对生产 `serp_fields.py` 只增加敏感键过滤规则。只重启 `google-workbench-web-1` 和 `google-workbench-prd-1`。未覆盖 `web.py`、`serp_page.py`。

采集服务器：在实际挂载的 `state/field-expansion-20261016/code-r2/local-worker/realtime` 对 `discovery.py`、`google_feeds.py`、`serp_store.py` 应用本轮最小补丁，新增标准库数据 helper，对 `serp_fields.py` 只增加敏感键过滤；只重启 local-worker，未重建其余四个服务。

发布脚本逐文件校验部署前 hash、备份后再原子替换；检查正文/自动采集/上传/领取关闭且无活动手动 SERP 任务，状态改变则中止。**没有执行数据库迁移，没有部署工作树中的正文/hash 历史迁移。**

两台服务器各自备份目录：`state/data-optimization-20261106/backup-web` / `backup-worker`。manifest 的 `before=null` 表示本轮新增文件。回退时先用完整 manifest 核对当前文件，恢复备份、移除本轮新增文件并仅重启对应容器；不要恢复旧整套 Compose。敏感 URL 写法及片段过滤补丁的前版另存 `backup-sensitive-url-web` / `backup-sensitive-url-worker`。导出大字段热修的前版另保存在 Web 备份 `google_data_export.py.r1`，用于回退到本轮首版。

## 尚未完成的外部闭环

1. **Consumer Key 创建和真实 Whale 读回**：用户已授权创建只读 Key；ego lite 空间 25 停在 Marsyoo 登录页并已 handOff。等待用户登录，尚未创建 Key，未调用真实 Consumer 读接口。
2. **正式 Whale run/item/feature schema、数据集与终态**：仍需平台真实确认；旧 9 条 queued 不代表终态或新合同验收。
3. **News 实际文章 URL**：大量 Google 包装链接仍未解析；保留原地址、出版者站点和未解析状态，未开启出版者正文请求或伪造目标地址。
4. **数值 Trends**：尚无真实历史时序来源；此次只完善现有热门话题数据。
5. **新采集端到端、连续 7 天、百万行与灾备**：未恢复自动采集，未开始持续运行验收；当前仅有真实历史样本与隔离恢复验证，不能宣称这些出口通过。

这五项不能因本轮代码或测试通过而标记完成。
