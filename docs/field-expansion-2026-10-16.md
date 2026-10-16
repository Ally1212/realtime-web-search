# 采集字段扩展（2026-10-16）

已上线到当前生产栈，保留原冻结包的其他代码与数据库迁移边界。当前 Web 包为 `state/field-expansion-20261016/revisions/r3/code/web/realtime`，worker 为同目录 `code-r2/local-worker/realtime`；开机 current-compose.json 和 compose.override.yaml 已同步。原部署与配置备份留在服务器本轮 state 目录。

## 已增加的实际能力

- Web：最终目标域名、路径、协议与上游域名分开；原始结果 ID、引擎、类型、域名分类、URL 分类提示和原始图标保留。Google 图标不再冒充目标网站图标。每条提供 field_status，区分 observed、derived、not_provided、upstream_host_mismatch。
- 搜索页面：上游返回的 AI 摘要、相关问答、相关搜索、知识卡片、广告、本地/购物/新闻/图片/视频等模块按类型保存，保留原 serp_features。未返回的模块保持 null；module_status 只对实际返回内容标记 upstream_observed。没有访问文章页面来补作者、正文或日期。
- News：原始链接、目标域名、媒体主页域名、离线解码状态、guid、分类、请求语言和 RSS 元数据。保留 Google 包装链接；不支持离线解码的现代链接仍标为未解析，没有把媒体主页冒充文章原址。本轮未实现新增的在线跳转解析器。
- Trends：地区、图片来源、报道元数据、搜索量标记的数值与“是否下限”分开。20,000+ 不是精确次数；未实现指定关键词的历史曲线与区域兴趣模块。
- 导出：用户 CSV 从 17 列扩展为 44 列，新增作者、图片、站内链接、富摘要、评分、解析状态、字段状态等；嵌套结构按 JSON 导出，保留 false/0 与 CSV 公式防护。原 JSONL 和请求级 JSON 保留完整结构。详情页增加本次搜索的补充信息，单条 CSV 也保留附加字段。

## 当前真实验证

一次性、有上限的手动任务：`c5b06e08-5ca5-4072-af38-e9f6ae2ab5d7`，关键词 solar energy research，最多 3 次成功请求，已自行完成。

- Web：9 条结果，实际取得 AI 摘要。
- News：100 条；当前样本仍包含未解析的 Google 包装地址。
- Trends：10 个话题、30 条相关报道。
- 用户 CSV 实际下载为 44 列、139 行；任务增加 81 个历史去重 URL。
- 自动采集、正文、Whale 上传及任务领取始终为 false；没有恢复持续采集。

本轮完整回归运行 435 项，成功结束、6 项跳过，包含真实临时 PostgreSQL。追加的字段、控制台和导出验证 28 项通过；网页脚本语法检查通过。一次全量重跑碰到已有正文实验测试的 0.1 秒时间敏感断言，原代码未修改，再次全量运行通过。

生产验证数据、导出和状态在本地 `state/field-expansion-20261016/`。新增字段对后续采集生效，历史响应没有批量改写；上游未提供的字段仍为空。INS 的对应扩展见同级项目 instagram-graph-collector/FIELDS.md。
