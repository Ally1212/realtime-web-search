# 订阅交付第二轮优化与执行记录

2026-11-10，已上线。证据目录 `state/subscription-next-20261110/`。此次未创建搜索、开启采集、正文、Whale 上传/领取或关键词监测。

## 完成内容

1. 领取起点：网页默认“从现在开始”，可改“补领历史”并设置本地时间；API新增start_mode，默认history兼容旧调用。now在创建事务内保存已存在匹配请求基线，避免补领历史，也不会遗漏后来提交但时间戳较旧的请求。订阅仍默认暂停。
2. 进度与交付记录：精确待处理请求（含待确认批次）、已确认请求/事件、最近确认、批次大小；分页历史记录与重新下载。元数据查询不读取全部批次payload，下载继续按现有权限核验。历史批次不可重复点击确认。
3. 大批量交付：URL去重查询、确认账本改为批量SQL；导出预分组事件；保留8MiB批次上限、事务回滚、幂等确认及文件摘要。单请求超过上限仍返回413，需用完整单请求导出，不静默截断。
4. 新闻补链：保存尝试次数与下次重试时间，暂缓请求不持续阻塞后续请求；显式启用失败重试，指数退避最长24小时。已解析证据不覆盖；保留原响应和原始链接。

订阅为已有数据的拉取交付，间隔控制客户端轮询；不是主动推送、搜索调度或付费订阅。补链证据通过`/v1/data/requests/{id}/news-links`读取，原标准快照不会因此被改写。

## 验证

- 完整隔离回归：**1108 passed，333 subtests passed，0 skipped**，191.53秒；独立PostgreSQL/Redis已清理。
- 从实际生产包复制五个候选模块并预先锚定realtime导入路径：**28 passed**，17.40秒。
- 一万条模拟occurrence事件，20个请求，分成3批；10000个event_id全部唯一且完整，最大序列化批次8202495字节；重领同批、确认后无重复。纯交付段3.464秒，仅代表隔离模拟数据，不是生产吞吐或百万行验收。
- 客户端模拟确认响应丢失：文件已经保存校验后，重试复用原文件并幂等确认；损坏文件拒绝确认。暂停/权限撤销/跨客户/迟到提交/游标及失败重试覆盖通过。
- 正式网页：旧订阅待处理0、确认4请求/18事件，分页记录200；原JSONL重新下载SHA256保持`f965a8a64ffcefc2f58bdabf6b5ac71aba593731eeeb09baceeab2f728acbbdb`。确认按钮保持禁用。
- 新建一个“示例：从现在开始（暂停）”（`ac45c2e0-faa4-4d42-8516-10b6a8ce0593`），本人Web数据范围，now且active=false，待处理0；旧历史示例仍暂停。历史时间选项启停、390px页面无横向溢出验证通过。
- 正式无客户Key401、不存在订阅404；新闻独立证据接口200且返回2条resolved、attempts=1。

## 真实新闻小批验证

生产显式执行`--online --apply --max-requests 1 --max-links 2 --budget-seconds 45 --retry-unresolved`：

- 实际尝试2条，2条resolved，0 unresolved/deferred，4次Google HTTP，0缓存命中，2.593秒。
- 两条均通过google_news_rpc取得出版者URL；没有访问出版者页面，不能据此确认出版日期或页面内容。
- 只写独立sf证据/进度及解析器共享缓存/限速记录，原始请求不修改；未安排全量补链。`stopped=true`表示达到本轮链接数上限，剩余记录保留待续。
- 证据：`news-online.json` / `news-online-summary.json`。

## 七天只读巡检

采集机安装`searchflow-delivery-integrity.service`（oneshot）及`.timer`，每15分钟检查固定已确认批次`4a333b87-f6e6-40c4-9a03-3df7978ac912`：完整payload摘要、4请求/18唯一URL确认账本、原数据可见性、JSONL/CSV摘要、原始请求总数及交付表存储增长。它不领取/确认新批次、不初始化数据库、不发起搜索，也不持有客户Key。

- 开始：2026-11-10 03:05:18.436923 UTC；截止：**2026-11-17 03:05:18.436923 UTC**（新加坡11:05）。截至上线核验为running，初始检查无失败。
- 重新运行服务后原deadline、baseline保持不变。间隔超过两倍计为gap；摘要变化、账本缺失或读取失败计为failure。到期还需满足检查次数、无失败和无gap，才能记录completed，否则completed_with_issues；随后辅助脚本仅停用自己的timer。
- 状态：`/home/azureuser/projects/google-workbench-backend/state/subscription-next-20261110/soak.json`；检查日志同目录`soak.jsonl`；`systemctl list-timers searchflow-delivery-integrity.timer`查看下次运行。
- 如需主动终止：`sudo systemctl disable --now searchflow-delivery-integrity.timer`，保留证据。

**尚未运行满七天，不能宣称七天验收通过。此巡检是归档交付完整性检查，不覆盖新数据端到端持续交付、外部网站可用性、真实断网恢复或长期Google解析成功率。** 模拟断响应和服务重启验证不能替代上述长时验收。

## 发布与保护

仅更新后台customer_subscriptions.py、customer_http.py、customer_schema.py、news_backfill.py、subscription_ui.py及应用机static/index.html；附加sf表字段/索引和基线表，无旧正文/hash迁移。原web.py、serp_page.py、serp_console.py、google_http.py哈希守卫通过。

仅重启google-workbench-backend-web-1/prd-1；原local-worker/OpenSERP/proxy-relay/PostgreSQL/Valkey五容器启动时间不变。后台回退在`/home/azureuser/projects/google-workbench-backend/state/subscription-next-20261110/backup`，静态回退在应用机`/home/azureuser/projects/google-workbench/state/subscription-next-20261110/index.html.before`。附加数据库结构不需要回滚删表；回滚前先停本次巡检timer。

前后快照完全一致：serp_requests39962、pages332768、sf_analytics_requests39934、旧Whale outbox9；continuous/content/uploads/claims均false，活动jobs/monitors/leases均0。原数据保留，未提交Git，未改变CORS或访问认证。

收尾核验：巡检4次成功、0失败、0gap，重跑后截止不变；新建now订阅使交付相关表较首次巡检增长16384字节，正常记录而非删除数据。最终原始数据及开关快照仍与发布前完全一致。ego Space62已finish并从任务列表消失；本地8897预览已停止，隔离回归服务已清理。仅保留明确授权的七天只读巡检。
