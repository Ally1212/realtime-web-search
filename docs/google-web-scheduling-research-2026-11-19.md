# Google Web 调度与搜索数据来源对比

2026-11-19。项目要求每个查询采集第 1 至 11 页。选择数据源时，先核对它能否提供指定页的自然结果、URL 和排名；评估调度时，再比较相同页深下每个实际运行小时成功入库的历史去重新增 URL 数。

| 机制或来源 | 官方文档说明 | 对本项目的意义 |
| --- | --- | --- |
| Scrapy AutoThrottle | 默认按域名划分下载槽。收到响应头后，以响应延迟除以目标并发数计算目标间隔，再与原间隔取平均。非 200 响应只能维持或延长间隔，每域并发上限仍然生效。[Scrapy 算法](https://docs.scrapy.org/en/latest/topics/autothrottle.html#throttling-algorithm) | 可借鉴反馈算法控制发请求的节奏。若测量点位于 OpenSERP 客户端，耗时可能包含浏览器处理，应单独记录。 |
| PostgreSQL `FOR UPDATE SKIP LOCKED` | 多个消费者领取队列任务时，可跳过其他事务已锁的行。查询只看到当下可锁的行，表级锁仍可能等待。[PostgreSQL 锁定子句](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE) | 在短事务内领取到期页并写入租约，提交后再访问网络。崩溃恢复依赖租约到期和幂等写入；一次领取为空也可能意味着候选行正被锁定。 |
| Google Custom Search JSON API | 返回已配置搜索引擎的结构化结果。已停止接收新客户，现有客户须在 2027-01-01 前迁移。单次最多 10 条，每个查询最多 100 条。[产品公告](https://developers.google.com/custom-search/v1/overview) · [分页参数](https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list) | 按每页 10 条，第 11 页从第 101 条开始，超出接口上限。 |
| Google 官方替代渠道 | Google 建议搜索最多 50 个指定域名时考虑 Vertex AI Search；全网搜索需求可联系 Google 获取方案详情。[Google 迁移说明](https://developers.google.com/custom-search/v1/overview) | 限域搜索适合预先确定的网站集合。全网方案的页深、排名字段和准入条件仍需向 Google 确认。 |
| Search Console 与 Gemini 搜索接地 | Search Console 提供已授权网站的搜索流量数据；Gemini 可结合实时搜索生成带来源引用的回答。[Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index) · [Gemini 搜索接地](https://ai.google.dev/gemini-api/docs/google-search) | 前者适合分析本站曝光，后者适合追溯回答依据。两者均未承诺按查询交付连续 11 页自然排名。 |
| OpenSERP | 第三方自托管服务从搜索页面提取结构化结果。上游 v0.8.12 提供 `/google/search`，其 `limit` 表示自然结果条数，上限 100。[固定版本 README](https://github.com/karust/openserp/blob/v0.8.12/README.md) | 本项目的逐页调度应按实际返回的页码、结果和错误验收。单次请求的结果条数不足以证明第 11 页已采到。 |

**实验口径建议**：固定 11 页，分别记录每页领取等待、请求耗时、成功状态和新增 URL。汇总吞吐时把排队、失败和重试计入总墙钟时间，同时列出第 1 至 11 页各自的成功覆盖率，避免漏采后页造成表面提速。

## 本项目的实测与本轮试验

11 月 18 日六个整小时都采 Google Web 前 11 页，按成功请求平均新增 3.192–3.457 个历史唯一 URL，按完整小时从 1,546 到 3,269 个不等。跨时段比较含关键词和上游状态差异。每次成功的产出相近，成功页的数量解释了主要产量差异。[六个整小时逐页数据](../output/web-queue-recovery-20261118/yield-audit.json)

相邻的每批 1 页和每批 3 页窗口都成功 449 次，新增分别为 1,546 和 1,552。分钟采样中无可用代理的次数分别为 48/60 和 52/60。采样点不能换算成连续阻塞分钟数。窗口内实际完成全部页的查询只有 7 个和 9 个，从首次保存到完成的中位数分别为 23.1 和 18.5 分钟，有窗口截断偏差。三页批次 449 个成功请求的请求计时 P50 为 13.33 秒、P95 为 22.41 秒。页间等待、失败重试和浏览器调用需要分别测量。[容量](../output/web-three-page-batches-20261118/capacity-full-hour.json) · [覆盖](../output/web-three-page-batches-20261118/coverage-full-hour.json) · [实际请求计时](../output/web-scheduling-20261119/request-latency.csv)

历史回放中，每页 SQL 往返从平均 38.01 条降到 6 条。每批处理三页将 30 个完整关键词的调度次数从 330 次降到 120 次。这两项改善都没有带来相当幅度的线上增量。[入库回放](../output/web-batched-store-20261118/verification.md) · [批次回放](../output/web-three-page-batches-20261118/batch-replay.json)

11 月 19 日新加坡时间 10:21:34 起，采集机启动 18、12、12、18 槽的四个一小时窗口，固定现网 OpenSERP 版本、每批三页及现有速率预算。按原供给 run、历史规范化 URL 首次归属计数，按每组累计新增除以累计时间选两者中观察值较高的配置继续供给。选优计入本组切换和排空耗时；启动阶段与历史窗口独立。结果包含时段影响，只能判断本次两个候选的表现。服务器运行目录为采集项目的 state/web-scheduling-20261119，progress.json 是进度，selection.json 是四段结束后的结果。网站操作员修改设置会中止试验。[运行脚本](../output/web-scheduling-20261119/compare.py)
