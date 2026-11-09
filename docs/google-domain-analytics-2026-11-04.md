# Google 固定 Cohort 只读分析契约

日期：2026-11-04

## 共同边界

- 所有分析要求显式 `[observed_from, observed_to)`；最多读取 5,000 个已投影 run，
  超出返回 `413 partial_result`，不静默截断。
- 每个 run 都重新检查当前 asset grant、字段用途、期限和固定 Whale 文档版本/hash，
  并由独立 reader 重读。撤权、到期、版本变化或 reader 失败均失败关闭。
- 分页游标绑定租户、rights version、筛选、projection revision、来源和页大小。
- 缓存 run 不进入 top-k 分母；重复查询不冒充独立样本；比例保留分子/分母。
  没有批准聚类方法时不返回伪造的 95% 区间。
- 目标域名来自显式 item domain 或 URL hostname；页面按精确 target URL 分组，
  不把不同 URL 自行归一为同一页面。

## 域名、页面与排名

- `GET /domains`：固定窗口中观测到的域名、URL、runs、median best position、
  top1/top3/top10 share；不是 Google 用户流量。
- `GET /domains/{id}`：单个域名及页面数量。`id` 可传域名或稳定 opaque ID。
- `GET /domains/{id}/pages`：精确 URL 的 occurrence、runs、rank、feature URL 数。
- `GET /rankings`：显式域名集的固定 universe 排名。指标限定
  `top3_share`、`top10_share`、`median_position`、`unique_urls`。
- `GET /comparisons`：2～10 个域名的固定集合比较和成对差值；不声称因果。

## 查询主题与新闻

- `GET /topics`：把精确归一化查询作为 topic candidate，不冒充批准 taxonomy。
- `GET /topics/{id}/relations`：仅按共享结果 URL/域名的 Jaccard 生成关系，
  保留两边 evidence IDs；没有统计显著性检验。
- `GET /news/observations`：只保留显式 news 类型/来源或显式新闻字段的 occurrence。
- `GET /news/events`：按 exact publisher URL，或明确 publisher + normalized title
  生成事件**候选**；独立文章 URL、转载和未解析 Google News 链接分账。
- `topic` 输入表示精确观测查询标签，不是 topic taxonomy ID。发布时间仅使用来源
  显式值，不推断。

## 机会、变化与品牌

- `GET /insights/opportunities`：`observed-rank-gap-v1` = 1 - 目标域名 top10 share。
  返回样本/分母；CPC、搜索需求和因果提升为 `not_covered`。
- `GET /insights/anomalies`：显式 `split_at` 比较前后 top10 share，规则为
  `top10_share_change_v1`。变化类别是 data observation change；业务原因未接入。
- `GET /insights/brands`：search、news、AI citation 三轨分别返回，不生成组合
  brand index；需求源未覆盖。品牌由显式域名集合代表，不是批准品牌实体。

## 文章与 AI

- `GET /articles/{id}`：只返回 typed projection 中许可内 title/snippet/URL/
  publisher/time。正文不存在时 `body=null` 且 `body_status=not_covered`。
- `GET /ai/answers`、`/ai/answers/{id}`：AIO/AI Mode feature presence 与显式
  citation/target URL。answer text 未进入投影时明确 `not_covered`。
- `GET /ai/citations`：展开显式 citation URL occurrence。
- `GET /ai/visibility`：prompt 是精确 query 标签；presence 和请求 brand set 内
  citation SoV 保留分母。mention 需要 answer text，当前 `not_covered`。

## 验证

- `tests/test_google_api.py` 使用隔离 PostgreSQL 和明确标注的 fixture reader。
- `tests/test_google_runtime.py` 验证未批准注入失败关闭，全批准时只组装本地服务，
  不启动 worker 或 scheduler。
- 全量：604 passed，0 failed，0 skipped，54.772s。
- 日志：`state/google-prd-v1-20261104/phase9/full-regression-final.log`。
- OpenAPI：`state/google-prd-v1-20261104/phase9/openapi-validation.log`。

这些结果只证明本地函数、隔离数据库和 HTTP 契约；不证明真实 Whale 读回、真实新闻/
AI 来源、批准 taxonomy、人工金标、工作台三端一致、生产部署、百万行或七天 SLO。
