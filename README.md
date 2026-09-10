# Realtime Web Search

面向关键词的持续网页采集系统。只使用 Google 搜索发现 URL，免费 Google 轻量页面、SearXNG、curl_cffi 和浏览器自动切换；Scrapy 并发抓取，并按正文指纹去重。本地模式保存正文并在网站展示；Whale 模式通过 Outbox 上传。

## 组件

- Scrapy：异步抓取、重试、限速和持久队列。
- Google Web：唯一搜索发现源；跨进程全局限速、查询缓存、代理会话隔离和 CAPTCHA 熔断。
- Trafilatura：提取主要正文；提取失败时自动回退 BeautifulSoup。
- PostgreSQL：Campaign、正文指纹、关键词关联和 30 天事件记录。
- Valkey：Campaign 调度队列。
- Whale：正文的最终存储；本地 PostgreSQL 只保存元数据与待投递 Outbox。
- Pekpik Proxy API：私有/公共代理池同步、健康筛选和域名级冷却。

## 启动

```bash
cp .env.example .env
# 如需私有代理，在 .env 中填写代理 API key、共享代理用户名和密码
docker compose up -d --build postgres valkey searxng web local-worker
```

打开 <http://localhost:8091>，输入关键词即可创建本地采集任务。每个搜索批次固定覆盖 Google Web 前 11 页，过滤重复链接、抓取正文并保存在 PostgreSQL；页面每 2 秒显示最新结果。`local-worker` 强制设置 `WHALE_ENABLED=false`，不会创建 Whale Outbox 消息，也不会上传 Whale。

默认 Campaign 使用私有代理；生产 Worker 的公网出口 IP 必须提前加入代理节点防火墙白名单。如未配置代理，可在 `.env` 中设置 `DEFAULT_PROXY_PROFILE=direct`。

凭据只允许存放在未提交的 `.env` 或 Docker Secret 中，不得写入 Git、URL、浏览器或日志。

### 一键启动 Whale 任务模式

Whale 平台发任务前，只需确保 Docker Desktop 已运行并已配置 `.env`，然后执行：

```bash
./scripts/start-whale.sh
```

该命令会启动数据库、统计网站和唯一的 `collector`，等待它们健康后才返回。完成后 Whale 平台即可向已注册的 `google_search` 采集器派发匹配任务。`TRAFILATURA_ENABLED=false` 可回退到原正文提取方式。

## Whale 采集器接入

Whale 运行在 pull 模式：先在 Whale 的 `/admin/datasets` 创建并启用 `social_media_raw`，再在 `/admin/collection` 创建仅允许 `social_media_raw`、`google_search` 和 `keyword_search` 的 Collector API Key。将明文 Key 仅写入未提交的运行环境，然后设置 `WHALE_ENABLED=true` 并启动：

```bash
./scripts/start-whale.sh
```

服务会注册 `realtime-web-search-01`、认领匹配任务，并使用 `POST /v1/documents/bulk` 上报完整正文。Whale 任务 Payload：`keyword_search`/`backfill` 必须使用 `keyword`（也兼容 `keywords` 与旧的 `query`）；页面默认的 `max_items` 会作为本次目标数量；`content_detail` 需要 `urls` 数组。未设置 `proxy_profile` 时默认直连，生产使用代理时显式传入 `private`。本地 PostgreSQL 使用 Outbox 保留待投递消息，网络重试不会改变 Whale 幂等键。

### 本地 AI 种子持续采集

如果不希望依赖 Whale 持续派发任务，可以让本项目维护约 260 个中英双语固定分类查询，并持续上传 Whale。在 `.env` 中设置：

```bash
WHALE_ENABLED=true
COLLECTOR_COMMAND=continuous-whale
CONTINUOUS_WHALE_ENABLED=true
CONTINUOUS_AI_KEYWORDS=artificial intelligence,AI news,generative AI,OpenAI,AI regulation
CONTINUOUS_INTERVAL_SECONDS=60
CONTINUOUS_DAILY_TARGET=0
CONTINUOUS_KEYWORD_CONCURRENCY=8
CONTINUOUS_KEYWORD_CONCURRENCY_MAX=12
CONTINUOUS_KEYWORDS_PER_ROUND=24
ADAPTIVE_CONCURRENCY_ENABLED=true
ADAPTIVE_EVALUATION_SECONDS=300
CONTINUOUS_PROXY_PROFILE=private
CRAWLER_OBEY_ROBOTS=false
```

启动后，`collector` 会持续轮询每个 AI 查询当前 Google Web 前 11 页，不追加 `after`/`before` 日期条件。固定查询覆盖模型、研究、开发、算力、治理、商业和行业应用。调度器每轮选择到期且产出较高的查询，抓取、过滤、去重后调用 `POST /v1/documents/bulk` 上传 Whale。`CONTINUOUS_DAILY_TARGET=0` 表示不限量持续采集；设置正整数才启用每日停止线。完整正文只在待上传 Outbox 中临时保存，Whale 确认后立即清除；本地长期保留 URL、哈希、标题和摘要。

自适应放量默认从 8 路关键词并发启动，每 5 分钟评估一次，连续两个健康窗口后逐级升至 12 路。Outbox 达到 2,000 或出现上传错误时降低整体并发。Google 搜索统一从 0.5 RPS 启动、最高 2 RPS。每个查询用 PostgreSQL 保存分页游标，按 `1–3、4–6、7–9、10–11` 分批覆盖前 11 页，不按新链接率提前停止；未完成的分页优先续跑，搜索故障不会使关键词被误判为低价值并冷却 12 小时。缓存和熔断策略详见下方“调度与质量保护”。

## 清理非 Google Web 历史数据

先预览数量，再执行清理：

```bash
docker compose run --rm collector purge-non-google-web
docker compose run --rm collector purge-non-google-web --apply --manifest-directory /app/state
```

执行模式会删除本地非 Google Web 页面、缓存、旧运行统计和动态趋势词，并在 `state/` 生成仅含 Whale 标识符的 CSV。Whale 暂无删除 API，需保留该清单，由 Whale 管理端按 `source_record_key` 删除已上传文档。

## API

创建每日 5 万目标的 Campaign：

```bash
curl -X POST http://127.0.0.1:8091/api/campaigns \
  -H 'Content-Type: application/json' \
  -d '{"query":"Singapore AI policy","aliases":[],"daily_target":50000,"proxy_profile":"private"}'
```

```text
GET  /api/stats
GET  /api/search?q=关键词  # 本地搜索已关闭，固定返回 HTTP 410
GET  /metrics
POST /api/campaigns/{id}/pause
POST /api/campaigns/{id}/resume
POST /api/campaigns/{id}/stop
```

持续采集统计页位于 <http://localhost:8091/stats>，会区分“采集器运行”和“Google Web 熔断”，显示 Web 当前 RPS、恢复时间、CAPTCHA、健康/冷却代理数，以及前 11 页的查询批次和逐页候选/唯一/新链接数量。`/metrics` 同时暴露 Google Web 请求、结果、唯一产出、熔断、代理会话和分页覆盖指标。持续采集默认使用单进程常驻 Scrapy Reactor；设置 `CONTINUOUS_EXECUTOR=subprocess` 可临时回退旧执行方式。

## 代理同步

私有池每 30 分钟完整读取 `/v1/private/proxies` 的游标链，只有全链成功才原子替换缓存。同步失败保留旧缓存，不回退公共池；记录超过 120 分钟或缓存连续 120 分钟未更新后停止使用。

手动检查同步：

```bash
docker compose run --rm collector sync-proxies --profile private
```

## 24 小时验收

```bash
docker compose run --rm collector benchmark \
  --query 'Singapore AI policy' \
  --profile private \
  --hours 24 \
  --target 50000
```

验收口径是同一 Campaign 当天新增、正文有效、相关且正文 SHA-256 不重复的页面。达到 50,000 需要平均至少 `0.579 篇/秒`；Dashboard 和 `/metrics` 会显示实时速率及预计日量。

## 测试

### 免费 AI 搜索与测速

无需搜索 API Key。默认 `GOOGLE_FREE_PROVIDERS=wml,wml_direct,searxng`：
已有代理的 Google 轻量页面优先，其次直连、SearXNG 的 Google 引擎。
直连任务会自动合并两个相同出口的轻量通道。
实测失败的标准页面与 Chromium 不参与默认重试；仍可显式配置 `curl`、`browser`
使用任务代理，或 `curl_direct`、`browser_direct` 指定直连进行诊断。
浏览器在独立子进程中执行，超过请求超时加 5 秒便终止自身进程树，避免卡住持续任务。
SearXNG 固定版本且只绑定本机 8092 端口；JSON 接口不使用公共实例。
这些通道均来自 Google，但轻量页面的排名与桌面 Google 可能不同。

```bash
docker compose up -d searxng
docker compose --profile whale run --rm --no-deps --entrypoint sh collector -c \
  'Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 & exec python -m realtime.cli benchmark-free'
```

默认测试 20 个中英文 AI 查询的第 1、3、11 页，0.5 RPS，正文抽样 20 条。
`--providers wml_direct` 可单测主通道；`--profile private` 可对比已有代理；
`--query 'AI agents' --pages 1,2,3,4,5,6,7,8,9,10,11` 可测试连续分页。
不调用收费服务，不创建 Whale 测试任务或上传测试正文。
结果写入 `state/benchmarks/free-google-时间.json`，最新完整报告在
`state/benchmarks/latest.json`、`GET /api/benchmark/free` 和 `/stats/legacy` 展示。
报告含有结果率、P50/P95（包含限速排队）、跨页重复率、唯一 URL 数和相关有效正文数。
连续失败会跳过后续探测，跳过数与实际尝试数单独统计；短期成功不代表长期稳定性。

### 调度与质量保护

每一次实际搜索通道调用都记录耗时、成功、错误、验证码；HTTP 200 空壳不算成功。
浏览器先识别验证页并在导航超时后再次检查。单通道连续 3 次失败会冷却；
验证码会冷却对应代理，共用单一出口的直连通道则立即冷却整个通道；
全局连续 10 次失败，或五分钟窗口至少 20 次请求且成功率低于 20%，暂停批量搜索。
恢复后低速探测，升速还要求至少 20 次请求、90% 成功率和实际结果产出。
各通道统一使用全局配额，代理统一哈希记账，只有最近一小时成功返回搜索响应的代理才算健康。

前 3 页缓存 6 小时，第 4–11 页缓存 24 小时（`GOOGLE_WEB_DEEP_CACHE_SECONDS`）。
跨进程租约合并相同查询、语言、页码的并发请求，过期租约自动恢复。
中英文任务仅搜索对应语言的别名；不再使用的旧分页游标保留审计但退出调度。
明确无结果可以推进分页；未知页面或解析失败不缓存为空结果。

### 单关键词 Markdown 对照导出

`export-markdown --query AI --languages zh,en --pages 11 --profile private --output /export`
执行一次性采集，不启动 266 关键词调度、不上传 Whale、不读取旧搜索缓存。
运行于已有 Docker 服务环境，并将电脑上的**独立空目录**挂载到 `/export`。
输出 `README.md`、逐页链接顺序 `search-pages/*.md`、每个唯一 URL 的正文/失败记录
`documents/*.md`，以及 `全部正文.md`。保留跨页重复和未通过相关性判定的结果，
方便人工对比；语言组分开，页内顺序仅代表轻量页面解析顺序，不保证桌面 Google 排名。
正文复用 LiveFetcher（robots 校验、5 MB 下载和 10 万字符限制），不包含未取得的正文或原始网页截图。

### Google 24 小时独立实验

`benchmark-google` 的 `start/status/pause/resume/export` 子命令运行独立日量实验。
采用主题、完整事件、站点、近一天/七天查询，所有 URL 必须有 Google 发现证据。
实验 SQLite/Outbox 与原任务隔离，原任务不会自动恢复。正文 4 并发、单域最多 2，
搜索上限 0.5 RPS，保持现有全局限速和冷却。全文保留本地，截止自动停止并导出 Markdown。
500 字符、AI 语境、非导航/验证页面、非截断是本实验自动有效正文门槛；
同文重复、同 URL 更新、已知历史、截止后完成量分别统计。

Whale 强制要求 `content.published_at`，实验仅使用原站明确带时区的发布时间。
未知时间的新正文仍保存并计入本地有效量，但标记 `blocked_missing_publication`，不填造时间上传。
完整操作和验收说明见 [Google 日量实验](docs/google-24h-experiment.md)。

统一统计入口为 <http://localhost:8091/stats>，默认显示最新正式实验，也可切换预检。
展示新增有效正文、Whale 回执、缺少发布时间、各组贡献和小时产量，独立于旧任务统计。
实验已持续时间包含暂停、冷却、离线；累计在线运行按心跳估算（含冷却，不重复累加限速等待）。
开始/截止时间使用新加坡 UTC+8，统计每 5 秒刷新、计时每秒更新；超过 120 秒无心跳显示失联。
页面和 `/api/experiments`、`/api/experiments/<目录名>` 为只读，不会启动、暂停或重置任务。
旧任务统计保留在 `/stats/legacy`，原 `/api/stats` 口径不变。

### 运行自动化测试

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```
