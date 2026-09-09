# Realtime Web Search

面向关键词的持续网页采集系统。只使用 Google Web 网页搜索发现 URL，Scrapy 并发抓取，并按正文指纹去重。本地模式保存正文并在网站展示；Whale 模式通过 Outbox 上传。

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
docker compose up -d --build postgres valkey web local-worker
```

打开 <http://localhost:8091>，输入关键词即可创建本地采集任务。每个搜索批次固定覆盖 Google Web 前 11 页，持续切换时间窗口、过滤重复链接、抓取正文并保存在 PostgreSQL；页面每 2 秒显示最新结果。`local-worker` 强制设置 `WHALE_ENABLED=false`，不会创建 Whale Outbox 消息，也不会上传 Whale。

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

启动后，`collector` 会按日期切片持续扩展 Google Web 搜索结果。固定查询覆盖模型、研究、开发、算力、治理、商业和行业应用。调度器每轮选择到期且产出较高的查询，抓取、过滤、去重后调用 `POST /v1/documents/bulk` 上传 Whale。`CONTINUOUS_DAILY_TARGET=0` 表示不限量持续采集；设置正整数才启用每日停止线。完整正文只在待上传 Outbox 中临时保存，Whale 确认后立即清除；本地长期保留 URL、哈希、标题和摘要。

自适应放量默认从 8 路关键词并发启动，每 5 分钟评估一次，连续两个健康窗口后逐级升至 12 路。Outbox 达到 2,000 或出现上传错误时降低整体并发。Google Web 初始 0.5 RPS、最高 2 RPS，同一查询缓存 6 小时。每个查询用 PostgreSQL 保存分页游标，按 `1–3、4–6、7–9、10–11` 分批固定覆盖前 11 页，不再按新链接率提前停止；失败或进程退出后从未完成页继续。CAPTCHA 超过 2% 时暂停 Web 30 分钟，相关代理会话冷却 6 小时，恢复后从 0.25 RPS 预热。

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

统计页会区分“采集器运行”和“Google Web 熔断”，显示 Web 当前 RPS、恢复时间、CAPTCHA、健康/冷却代理数，以及前 11 页的查询批次和逐页候选/唯一/新链接数量。`/metrics` 同时暴露 Google Web 请求、结果、唯一产出、熔断、代理会话和分页覆盖指标。持续采集默认使用单进程常驻 Scrapy Reactor；设置 `CONTINUOUS_EXECUTOR=subprocess` 可临时回退旧执行方式。

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

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```
