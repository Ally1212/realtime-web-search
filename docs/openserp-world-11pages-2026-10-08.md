# OpenSERP 深分页与多来源实验

- 任务：`openserp-world-11pages-20261008`。
- 开始：2026-10-08 10:57:49.803468 UTC+8；按用户指令，今天 14:00 自动停止。
- 来源：OpenSERP Google 网页搜索 + Google News RSS + Google Trends 热点相关报道；未开启 WML 或通用 RSS 来源。
- 关键词：主题不限，英语查询；实际保存 120,000 条唯一关键词，首批入队 11,264 条。日期限定后续词库仅生成到当天，不使用未来日期。
- 配置：最大 11 页、每批最多 11 页、3 个 crawler slots；验证码、429 或请求失败会退避并保留分页游标，不能保证每个词都取到 11 页。
- 正文口径：全局 URL / 内容哈希去重，至少 500 字符，排除启动前已有页面。首个小时包含启动和部署调整时间，不代表纯稳态产量。

本轮修复 Google News 内部跳转误判和代理缺失异常类型；来源并行发现，原站链接先抓取，News 跳转按 64 条分批解析，以减少正文等待。42 个 discovery 测试通过，新词库全部唯一；服务器已验证分页和 News 原站正文入库。

## 运行和统计

部署：`/home/azureuser/projects/realtime-web-search-openserp`。

- 监督服务：`realtime-openserp-world-11pages.service`，失败自动重启。
- 停止定时器：`realtime-openserp-world-11pages-finish.timer`，2026-10-08 06:00 UTC。
- 状态目录：`state/openserp-world-11pages-20261008/`。
- `report.json`：分钟更新；未满一小时的折算速度仅为短窗口估计，不能作为实测小时产量。
- `first-hour-audit.json`：10:57:49 至 11:57:49 的精确首小时统计，附各页尝试、结果分类和互不重复的来源组合。满一小时后保存并冻结。
- `run-hours-audit.json`：以启动时间为起点划分完整小时与尾段，记录最高完整小时产量。
- `final-report.json`：停止时生成的最终报告。

准备完成时尚未满一小时，不预报最终速度。News RSS 返回链接和 Trends 相关报道数均不等于新增合格正文数。

旧纯网页第一页英文基线：9 个完整小时平均约 1,667 篇/小时，最高小时 2,010 篇。本次同时改变关键词、分页和来源，只能比较组合效果，不能将差异单独归因于分页。
