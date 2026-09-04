# Realtime Web Search

面向关键词的持续网页采集系统。使用 SearXNG 的 Google 引擎和 Google News RSS 发现 URL，Scrapy 并发抓取，按正文指纹去重后写入 OpenSearch。目标容量为每个关键词每天 50,000 个相关唯一页面。

## 组件

- Scrapy：异步抓取、重试、限速和持久队列。
- SearXNG：仅启用 Google 引擎发现。
- Google News RSS：按关键词持续补充 Google News URL；单个来源故障不会中断采集。
- Trafilatura：提取主要正文；提取失败时自动回退 BeautifulSoup。
- PostgreSQL：Campaign、正文指纹、关键词关联和 30 天事件记录。
- Valkey：Campaign 调度队列。
- OpenSearch：全文索引；完整正文不保存在 `_source` 中。
- Pekpik Proxy API：私有/公共代理池同步、健康筛选和域名级冷却。

## 启动

```bash
cp .env.example .env
# 在 .env 中填写代理 API key、共享代理用户名和密码
docker compose up -d --build
```

打开 <http://localhost:8091>。默认 Campaign 使用私有代理；生产 Worker 的公网出口 IP 必须提前加入代理节点防火墙白名单。

凭据只允许存放在未提交的 `.env` 或 Docker Secret 中，不得写入 Git、URL、浏览器或日志。

### 一键启动 Whale 任务模式

Whale 平台发任务前，只需确保 Docker Desktop 已运行并已配置 `.env`，然后执行：

```bash
./scripts/start-whale.sh
```

该命令会启动数据库、索引、搜索源、统计网站和唯一的 `collector`，等待它们健康后才返回。完成后 Whale 平台即可向已注册的 `google_search` 采集器派发匹配任务。

`DISCOVERY_FEEDS_JSON` 是全局 Feed 模板数组，默认只包含 Google News RSS，`{query}` 会替换为 URL 编码后的 Campaign 关键词。设置为 `[]` 可关闭额外 Feed；`TRAFILATURA_ENABLED=false` 可回退到原正文提取方式。

## Whale 采集器接入

Whale 运行在 pull 模式：先在 Whale 的 `/admin/datasets` 创建并启用 `social_media_raw`，再在 `/admin/collection` 创建仅允许 `social_media_raw`、`google_search` 和 `keyword_search` 的 Collector API Key。将明文 Key 仅写入未提交的运行环境，然后设置 `WHALE_ENABLED=true` 并启动：

```bash
./scripts/start-whale.sh
```

服务会注册 `realtime-web-search-01`、认领匹配任务，并使用 `POST /v1/documents/bulk` 上报完整正文。Whale 任务 Payload：`keyword_search`/`backfill` 必须使用 `keyword`（也兼容 `keywords` 与旧的 `query`）；页面默认的 `max_items` 会作为本次目标数量；`content_detail` 需要 `urls` 数组。未设置 `proxy_profile` 时默认直连，生产使用代理时显式传入 `private`。本地 PostgreSQL 使用 Outbox 保留待投递消息，网络重试不会改变 Whale 幂等键。

### 本地 AI 种子持续采集

如果不希望依赖 Whale 持续派发任务，可以让本项目自己维护一组 AI 种子关键词并持续上传 Whale。在 `.env` 中设置：

```bash
WHALE_ENABLED=true
COLLECTOR_COMMAND=continuous-whale
CONTINUOUS_WHALE_ENABLED=true
CONTINUOUS_AI_KEYWORDS=artificial intelligence,AI news,generative AI,OpenAI,AI regulation
CONTINUOUS_INTERVAL_SECONDS=600
CONTINUOUS_MAX_ITEMS_PER_KEYWORD=100
CONTINUOUS_PROXY_PROFILE=direct
```

启动后，`collector` 会每 10 分钟按关键词创建本地采集轮次，只使用 Google 搜索和 Google News RSS 发现 URL，抓取、过滤、去重后直接调用 `POST /v1/documents/bulk` 上传 Whale。上传失败的数据会留在本地 Outbox，下一轮优先补投。

## API

创建每日 5 万目标的 Campaign：

```bash
curl -X POST http://127.0.0.1:8091/api/campaigns \
  -H 'Content-Type: application/json' \
  -d '{"query":"Singapore AI policy","aliases":[],"daily_target":50000,"proxy_profile":"private"}'
```

```text
GET  /api/stats
GET  /api/search?q=关键词
GET  /metrics
POST /api/campaigns/{id}/pause
POST /api/campaigns/{id}/resume
POST /api/campaigns/{id}/stop
```

统计页会显示各搜索引擎和 Feed 当天贡献的有效唯一页面数。`/metrics` 同时暴露发现、抓取、失败、重复、无关和预计日量指标。

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
