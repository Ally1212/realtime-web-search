# AGENTS.md

## 生产服务器

- 地址：`azureuser@20.106.103.139`（Azure 美国 VPS，主机名 `vm-ubuntu-4c8g-us`）
- 登录：`ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes azureuser@20.106.103.139`
- 唯一保留的部署：`/home/azureuser/projects/realtime-web-search-openserp`（compose 项目名 `realtime-web-search-openserp`；服务 openserp / proxy-relay / local-worker / web / postgres / valkey）
- 网页入口：当前服务器 `.env` 指定 `WEB_PORT=8093`，Compose 绑定在服务器 `127.0.0.1:8093`；本机使用 `ssh -L 8093:127.0.0.1:8093 azureuser@20.106.103.139` 后访问 `http://127.0.0.1:8093`。
- 私有代理白名单登记的是这台服务器的出口 IP（`20.106.103.139`）；不要从本机直连私有代理，必被防火墙拒绝。
- 已清理：`/opt/projects/realtime-web-search`（旧版）、`openserp-pipeline`（重复栈）、`/opt/projects/realtime-web-search-main`（代码副本）已于 2026-09-30 删除，删除前备份在服务器 `~/realtime-web-search-OLD-backup-20260930.sql.gz` 和 `~/openserp-pipeline-backup-20260930.sql.gz`。

## 当前目标修订（2026-10-10）

- 用户明确把优化重点从正文采集改为 **SERP 返回的新链接**；核心指标为每天成功持久化、规范化并对留存历史去重后的新增唯一结果 URL 数。正文下载、长度/语言/相关性检查、正文 hash 不作为链接入库条件或搜索产能的评判依据。
- 用户随后明确要求增加 Google News 和 Google Trends。当前链接模式支持 `google_web`、`google_news`、`google_trends` 三来源，任务可单选/组合，持续供给启用三者；第三方 RSS 不在本轮范围。三来源共用 URL registry、保留来源记录、分来源统计，不把重复贡献相加冒充全局新链接。
- 同时保存查询参数、时间、请求状态及自然结果的 URL、标题、摘要、排名等结构化信息；缺失字段标记未知，不能编造。重复 URL 可以保留多次查询/排名记录，但不重复计为新链接。
- 用户已授权关闭正文并实施 SERP 功能。旧正文 worker 于 2026-10-10 UTC 02:30:24 停止；第一阶段 Web-only 于 UTC 02:36:47 启动，三来源版本于 UTC 03:00:58 启动。Web 使用 OpenSERP、1 页，三来源共享 6 个任务槽，正文开关持久化 false。尚未完成三来源新模式整小时/24 小时选优，不能宣称最优或达到上限。
- 当前开机服务为 `realtime-google-serp.service`；生产 `compose.override.yaml` 固定三来源配置，最新网站冻结代码为 `state/serp-website-20261010/revisions/r2`（旧三来源包 `state/google-sources-20261010/revisions/r2` 保留）；供给 run ID `google-serp-20261010`、关键词 cursor/offset 沿用原版本。旧小时实验监督器与归档/资源 timer 已停用。旧自动迁移等待程序已取消，不恢复旧正文实验或执行待上线正文/hash 迁移。
- News 使用 Google News 搜索 RSS，未解析的 Google 跳转链接明确标记；RSS 顺序不冒充 Google 排名。Trends 使用 Trending Now RSS，当前地区 US，不是按关键词的历史热度曲线。两者共享持久缓存和查询锁，不访问出版社；高级商业 Trends 模块尚未实现。
- 基础字段已映射保存；高级模块只有上游 `serp_features` 透传，独立商业模块尚未实现，缺失字段 null。正文开关可经页面/API 修改；默认关闭，生产验证期间不得为了测试打开正文。
- 最新需求和项目检查见 `docs/google-sources-requirements-2026-10-10.md`；第一阶段 Web 需求见 `docs/google-serp-requirements-2026-10-10.md`，前期研究见 `docs/serp-link-priority-2026-10-10.md`。以下正文产量目标和实验方法保留为历史背景，选优时以本节最新目标为准。

## 历史总目标（正文采集）

**在服务器上持续运行采集系统，通过实验找到并固定"单日入库正文量最大"的采集配置，使系统长期以最大产能稳定运行。**

### 子目标与方法

1. **最大化日产量**：以"每天入库的合格新正文篇数"为唯一核心指标，持续优化直至达到系统上限。
2. **对照实验**：尝试不同来源组合并量化对比，至少覆盖两个极端配置：
   - "全部打开"：google_web + google_news + google_trends + rss 全部来源同时启用；
   - "纯正谷歌搜索"：仅启用 google_web（OpenSERP 标准 Google 网页搜索）。
   - 每组实验独立运行、记录尝试数/成功率/CAPTCHA 率/入库量，用数据决定最终配置。
3. **持续运行**：确定最优配置后，让系统 7×24 不间断采集，监控成功率与代理健康，异常时告警排查。

### 当前状态（2026-09-30）

- 4 个采集任务已停止（google_web 通道当时闲置）；历史产能参考：google_web 活跃期约 700 次搜索/小时，正文入库约 3.6 篇/小时（仅 News/Trends/RSS 来源时）。
- 私有代理池：固定 80 个云厂商 IP（Pekpik），经 proxy-relay 中转供 OpenSERP 使用。

## 三来源网站（2026-10-10 UTC 04:16 已验收）

- 网站四区：总览、任务、结果、设置。任务可保存待启动/启动/暂停/继续/停止，地区参数实际传给上游；新任务优先入队。
- 设置存于 `serp_collector_settings.runtime_config`，worker 每批次读取；持续供给循环始终运行但服从数据库开关。正文默认 false。原 run ID、cursor、offset 沿用。
- 新工作台只管理链接任务；旧正文/RSS 任务不在新列表和控制范围。Web/News/Trends 共享缓存响应保留任务关联，不重复计请求或首次贡献。
- 当前 Compose 和 systemd 固定 `state/serp-website-20261010/revisions/r2/compose.website.json`。不要重跑已带 mutation marker 的部署脚本，也不恢复旧正文/hash 实验。
- 完整需求检查、操作流程、接口与验收证据见 `docs/serp-website-2026-10-10.md`。最终全量 380 passed、6 skipped、34 subtests；冻结包 66 passed、10 subtests；生产 Playwright 操作及桌面/手机验收通过。
