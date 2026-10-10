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
- 当前开机服务为 `realtime-google-serp.service`；生产 `compose.override.yaml` 固定三来源配置，最新网站冻结代码为 `state/serp-website-20261010/revisions/r3`（旧三来源包 `state/google-sources-20261010/revisions/r2` 保留）；供给 run ID `google-serp-20261010`、关键词 cursor/offset 沿用原版本。旧小时实验监督器与归档/资源 timer 已停用。旧自动迁移等待程序已取消，不恢复旧正文实验或执行待上线正文/hash 迁移。
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
- 当前 Compose 和 systemd 固定 `state/serp-website-20261010/revisions/r3/compose.website.json`。不要重跑已带 mutation marker 的部署脚本，也不恢复旧正文/hash 实验。
- 完整需求检查、操作流程、接口与验收证据见 `docs/serp-website-2026-10-10.md`。最终全量 380 passed、6 skipped、34 subtests；冻结包 66 passed、10 subtests；生产 Playwright 操作及桌面/手机验收通过。

## 最新调整：关闭自动采集并展示数据（2026-10-10 UTC 09:03 起）

- 用户明确要求关闭自动采集。数据库 `continuous_enabled=false`，部署默认也为 false；不得为了验收开启自动采集或正文。关闭不删除历史数据，手动任务按自己的状态执行。
- 关闭操作于 UTC 09:03:31 保存，最后在途请求 UTC 09:03:53 完成；请求总数停止在 15,260，供给 cursor 不再推进。
- 网站 r3 首页优先展示最新真实标题、链接、摘要及详情；顶部提供全站可见的开启/关闭自动采集按钮。统计与异常、结果高级筛选、设置高级参数默认收起。自动任务在总开关关闭时明确显示“自动采集已关闭”。
- UI 结果分页使用 `count_total=false`，按页读取并返回 `has_more`，不编造总数；原 API 默认精确总数、全部筛选导出保持可用。最新页、下一页、News、Trends 和关键词筛选线上约 0.4–0.5 秒返回。
- r3 只在原 r2 冻结代码上替换 `serp_console.py`、`serp_page.py`，未部署工作树历史正文/hash 迁移。完整回归 381 passed、6 skipped、34 subtests；冻结包 109 passed、18 subtests。已在真实 Chrome 核验关闭状态、首页数据和请求 JSON。

## 最新授权：纯 Google Web 长时选优（2026-10-10 UTC 09:30 起）

- 用户重新明确授权在服务器持续运行，优化截止 2026-10-11 09:00 新加坡时间（UTC 01:00）。本轮仅 google_web / OpenSERP，News、Trends、RSS、正文均关闭；此前“自动采集关闭”记录为历史状态，不再作为本轮禁止启动的依据。
- 核心指标改为长时实测新增唯一 URL / 全部实际墙钟小时，包含等待、失败、重试与配置切换，不用单小时峰值代替。共享原历史 URL registry，不清空数据库、不重复计缓存；同配置重复阶段按累计数据/累计时长加权。
- 初始 UTC 09:30 窗口含旧三来源批次，已归档为无效；经过 worker 重启屏障后的纯 Web 基线于 UTC 09:44:01 启动，6 槽、1 页；供给沿用 google-serp-20261010、offset 90248、原 cursor。实验监督服务 realtime-google-web-longrun.service，状态/逐小时证据/排名在 state/google-web-longrun-20261010。每组计划 90 分钟，随后长时复验当前最佳配置；到截止时间固定符合纯来源和稳定性条件的已测最佳配置，继续纯 Web 采集，不宣称理论上限。
- 开机采集服务 realtime-google-serp.service 改为读取 state/google-web-longrun-20261010/current-compose.json；初期 worker/web 仍为原网站 r3 冻结包，后续优化 worker 代码单独冻结在本轮 code/realtime，web 保持网站 r3。只改 worker 的任务队列清理和配置速率上限传递，不部署工作树正文/hash 迁移。
- 网站关闭自动采集优先于监督器：监督器退出且不重启自动采集；配置切换采用 updated_at 比对与数据库行锁，操作员期间修改设置时不得自动覆盖。异常资源、来源/正文变更、人工任务混入会停止实验并保留记录。
- 需求、配置、度量方法及进展见 docs/google-web-longrun-2026-10-10.md。URL 规范化已于 UTC 12:29–13:01 维护期间修正为 google-tracking-v2，Google srsltid 跟踪变体不再重复计新增；旧 URL 主键、原请求和正文保留。首个合格基线完整 5,443.44 秒新增 9,913 个唯一 URL，平均 6,555.93 / 小时，成功率 97.14%（原 10,024 / 6,629.34 是修正前口径，作废但留档）。优化 6 槽组包括维护暂停，完整 6,384.68 秒新增 7,321，平均 4,127.94 / 小时；UTC 13:01:09 已进入 12 槽、1 页组。后续对照及长时复验未完成，不能称为最优或理论上限。
- 当前 baseline / optimized / web 冻结包位于 state/google-web-longrun-20261010/normalization-v2/code；只替换 SERP 规范化与存储文件，baseline 保留旧 worker 行为，optimized 保留队列/速率修复。源码哈希校验通过；历史统计按原起止时间重算，旧结果保存在 normalization-v2/original-metrics。
- UTC 13:31 代理中转因刷新监听端口遇到 EADDRINUSE 退出并自动重启。UTC 13:41:42–13:42:30 排空后单独部署 proxy_relay.py 修复：端口占用时保留其他监听，下次刷新重试；未修改共享端口映射或重建 worker。相关本地测试 20 passed、5 subtests；生产中转容器 4 项测试通过。原文件及维护证据在本轮 relay-bind-fix 与 relay-bind-*.json；维护完整计入 12 槽组时长。监督服务恢复后 MainPID 3238639。
- 进一步复现 Python 3.12+ 的 Server.wait_closed 会等待已接入长连接，导致刷新阻塞。UTC 13:52:30–13:53:23 排空后补充修复：关闭旧监听后立即创建新监听，旧隧道继续传输。相关本地 21 passed、5 subtests；生产中转容器 5 项测试通过。当前生产中转文件 SHA256 cc8f8c78987c1ff2e4804e3d81b7e179a126d14f73ff6763f2b87f25e3bd54a4，证据 relay-refresh-*.json。53.483487 秒暂停计入同一 12 槽阶段，worker 未重建、正文不变；监督服务 MainPID 3270956。
