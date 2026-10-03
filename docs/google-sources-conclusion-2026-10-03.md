# Google 来源采集结论（2026-10-03）

## 结论

- **纯 Google Web 搜索**：最新 154 个 `mass-*` campaign 平均约 **1,043 篇正文/小时**（按整段任务摊销）；有效采集窗口约 **1,361 篇/小时**。
- **Google Web + Google News + Google Trends**：最新整段任务平均约 **3,494 篇正文/小时**，约 **8.39 万篇/天**。
- **News/Trends 增益明显**：相比纯 Google Web 的有效窗口速率，三入口组合明显提高正文产量，并能填平 Web 搜索低谷。
- **RSS 已移除**：RSS 来源属于 Bing，不是 Google。生产配置已改为仅使用 `google_web`、`google_news`、`google_trends` 三个 Google 入口。

## 最近任务统计

| 配置 | 时长 | 发现 URL | 成功正文 | 平均速率 |
|---|---:|---:|---:|---:|
| Google Web + News + Trends | 36h21m | 1,017,448 | 126,967 | 3,494/小时 |
| 纯 Google Web（mass 任务） | 约24h35m | 46,006 | 25,657 | 1,043/小时 |

## 当前理解

- `google_web` 是主要搜索发现入口。
- `google_news` 提供新闻增量。
- `google_trends` 提供热点增量。
- 最终正文入库仍然经过统一去重、抓取和正文提取流程。
