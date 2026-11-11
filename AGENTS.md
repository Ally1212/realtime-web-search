# AGENTS.md

## 真实数据主题分析（2026-11-10，最新）

- 本次主题分析已完成55/55，129处证据逐字校验，独立复算和ZIP校验通过。助手校准6条并补4处漏标，备注明确非用户人工复核，其余49条仍待逐条复核。模型合计草案1次+标注6次。中间未去重草案已留本地审计后删除，最终109原记录仍保留。

- 已按用户要求直接使用现有Google News查询 `sodium ion battery research` 的2个真实已存请求；109条原始occurrence逐条与客户数据接口深比较一致。
- 修正仅分析适配器的News身份：相同完整已保存文本只计一次，防止同文媒体直链/Google跳转链接重复统计。原109条及URL全留档，54重复排除，统计分母55个不同文本，不能称55篇唯一文章。其他来源身份及VOC原生评论不改变，旧分析快照不自动改写。
- 新数据集 `02f5a0e1d7d6494a641aaf3228db58b8727dff31b81da32642dffd3d8808881b`；标签依据真实可见文本校准，不把署名当作出版者独立核验。仅部署insights_source.py、重启独立insights；备份 `state/insights-real-analysis-20261110/backup/`。94 passed、5 subtests、0 skipped。详情 `docs/searchflow-insights-real-analysis-2026-11-10.md`。

## 数据分析同站整合（2026-11-10，最新）

- 入口现为同一工作台 `/#insights`、左侧“数据分析”，复用原导航/组件/会话；旧 `/insights` 仅302到正式HTTPS新入口。没有iframe，默认数据概览，AI/标签/导入/历史选择折叠保留。`insights_ui.extend()` 委托新 `insights_embed.py`。
- 只读真实已存Web/News/Trends/Images，保存标题/摘要/查询参数/原始occurrence、request/occurrence ID与响应hash；采集完成时间依据request.completed_at，不以出版日期替代。Google包装链接不当作已知出版者域名。原文CSV保留source_fields。
- 最新20请求样本242条与18个有内容原请求逐条深比较一致，另2请求无文本；128已知网站、153含摘要。不是全库统计。相关回归93 passed、5 subtests，四来源及桌面/320/390px验证。本轮0新搜索、0模型调用，原库计数及采集/正文/上传/领取关闭状态不变。
- 仅更新独立insights模块、static/index.html和gateway重定向，只重启insights/reload gateway；两机备份 `state/insights-integration-20261110/backup/`。详见 `docs/searchflow-insights-integration-2026-11-10.md`。

## 文本洞察 / VOC（2026-11-10，首次版本历史）

- 已上线 `/insights`，首页左侧“文本洞察”；沿用前置访问门及客户 Bearer。独立 `insights*.py` 服务只监听采集机回环 18098，Compose 项目 `google-workbench-backend` 新增 insights；SQLite 在该项目 `state/text-insights-data/`，不运行旧 PostgreSQL 迁移或采集协调器。
- 只读已有公开/本客户搜索标题与摘要，支持 JSON/JSONL/多行 CSV 导入评论；搜索内容与真实 VOC 分模式。AI 标签草案必须人工确认，标注逐字证据校验，Python 统计、人工复核、独立调用台账、可校验完整报告包。未接通新的 Amazon/Reddit/X 抓取器，不把搜索摘要冒充用户评论。
- 已按用户指定配置 Responses 兼容服务和 `elastic-code`；私密配置在采集机项目 `private/insights-model.json`，600，禁止输出/提交。每数据集需显式允许外部处理；每批10条、每次8000输出token、每任务最多500调用、每空间UTC日预留1000；不自动重试、不承诺金额。人工结果不覆盖，同版本完成标注复用，含空标注。
- 正式真实历史样本8条/11标签/12处证据/1条人工复核，草案和标注各一次模型调用，重复执行0调用。相关隔离回归90 passed、5 subtests、0 skipped；双层401、正式320/390px、下载校验及原服务未重建均核验。发布前后原始请求39962、pages332768、投影39934、Whale outbox9、启用监测0及采集/正文/上传/领取false一致。
- 两机备份在各项目 `state/text-insights-20261110/backup/`；网页只发布新静态页/一个导航和gateway路由，原后端只新增模块与ingress路由，没有覆盖线上web.py/serp_page.py。文档 `docs/searchflow-text-insights-2026-11-10.md`。本轮本地PostgreSQL、预览HTTP及ego任务空间101已清理；未提交Git。

## 工作台迁移完成（2026-10-22）

- 新网页已部署至 `azureuser@20.150.145.162`（dev-server），目录 `/home/azureuser/projects/google-workbench`；目标公网入口 `http://20.150.145.162:8093`，用户明确不加登录保护。
- 网页通过专用受限 SSH 隧道连接原 `20.106.103.139` 的 PostgreSQL/Valkey；worker、OpenSERP、代理和历史数据不迁移。原服务器仅新增回环数据 relay，未改五个原服务容器。
- 公网 8093 已实测 HTTP 200，Playwright 从公网创建一次性 Google Web 任务 `86784589-c680-48ce-ac38-6fa857d332fd`，原 worker 完成 1 次真实非缓存搜索、9 条结果、8 个历史去重新 URL；浏览器显示结果并下载 CSV（44 列、9 行）。
- 原 web 容器已移除，旧 8093 已无监听；原 base Compose、override、current-compose 和 systemd 已移除 web 启动项。原五个服务容器 ID 未变。回退文件在原项目 `state/workbench-old-web-backup-20261022`，不要使用旧冻结 Compose 恢复网页。
- 自动采集、正文、Whale 领取和上传保持关闭。新工作台和隧道 systemd 均 enabled；迁移记录 `docs/workbench-migration-2026-10-22.md`。

## 生产服务器

- 地址：`azureuser@20.106.103.139`（Azure 美国 VPS，主机名 `vm-ubuntu-4c8g-us`）
- 登录：`ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes azureuser@20.106.103.139`
- 采集与数据部署：`/home/azureuser/projects/realtime-web-search-openserp`（compose 项目名 `realtime-web-search-openserp`；服务 openserp / proxy-relay / local-worker / postgres / valkey）；另有仅回环监听的数据 relay。
- 网页已迁移：直接访问 `http://20.150.145.162:8093`，旧服务器不再运行 web。
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

## Whale 上传平台适配（2026-10-11）

- 已部署链接专用 Whale/Marsyoo 对接：采集器注册/心跳/领取/进度/完成/失败、平台控制、本地上传队列和暂停/继续/停止/重试；网站新增“平台上传”和创建任务“同时加入 Whale 上传队列”，实际消息、回执、失败原因可查看。
- 当前 worker 冻结 `state/whale-serp-20261011/code/worker/realtime`；网页冻结 `state/whale-serp-20261011/revisions/r2/code/web/realtime`。仅叠加 SERP 功能与已提交分页 lease 释放修复，未上线历史正文/hash 迁移。开机仍读取原 `state/google-web-longrun-20261010/current-compose.json`，根 override 一致，旧包保留。
- 生产验收结束：自动采集、正文、Whale 上传和平台任务领取均 false，未恢复采集。请求/正文/供给游标等与部署前完全一致；其他四个服务容器未改，r2 只重建 web。
- 完整回归 423 passed、44 subtests，无跳过；生产冻结相关测试 65 passed、10 subtests。Web/News/Trends 各一条真实已存链接上传均取得 queued 回执，桌面/手机及网站任务保存/控制自测通过。
- 平台 Key 当前仅允许 keyword_search；backfill/content_detail 注册真实 HTTP 403。两类实现和隔离联调已通过，但不能称线上验收完成。Collector Key 查询 document 返回 401，最终业务入库/索引未知；queued 不冒充最终入库。需要平台管理员补齐任务权限与读取凭据，不能为验收开启正文或自动采集。
- 功能矩阵、字段语义、接口、部署与限制见 `docs/whale-serp-adaptation-2026-10-11.md`。

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

- 最新用户指令（2026-10-11 新加坡时间 15:01）：暂停采集。已于 UTC 07:01:42.539261 保存 `continuous_enabled=false`；此前持续运行授权已被此次暂停覆盖。UTC 07:02:24 与 07:02:37 两次快照均在途 0，正式纯 Web 请求固定 24,153、全历史去重新增 URL 固定 114,478、cursor 固定 4083，已验证停止新增。保留全部数据和当前最佳参数，未获用户恢复指令不得重新开启。证据 `state/google-web-longrun-20261010/operator-paused-20261011.json`、`operator-pause-verification-20261011.json`。
- 最终状态（2026-10-11）：新加坡时间 09:01:10 自动结束参数试验并继续采集，固定 `s18-p1-i60-r4`：18 槽 / 1 页 / 每代理最小间隔 60 秒 / 初始 2、最大 4 RPS，仅 OpenSERP。两个实测段累计 4,054.787327 秒新增 8,412 个历史去重 URL，加权平均 **7,468.505142 / 小时**，1,669 / 1,684 请求成功（99.1093%）；不能称为理论上限或已完成 24 小时验证。
- UTC 01:45:31 独立只读数据库复算全部 12 个正式纯 Web 窗口通过，连续 55,027.644762 秒共新增 74,670，完整包含失败、等待、维护和两页排空故障停机；这是全实验平均，不能与选中配置平均混淆。原混合来源窗口和旧 r3 不参与选优，同配置短复测全部加权。
- UTC 01:46:02 已确认选优后实际新增 5,418 个 URL，worker 无重启/OOM、正文基线不变、News/Trends/RSS 关闭。监督服务正常结束；`realtime-google-serp.service` active/enabled，开机读取 current-compose.json，compose.override.yaml 一致。证据 `final.json`、`final-runtime-verification.json`、`measurement-audit-phase10-20261011T014531.json`。网站关闭仍优先，不自动覆盖用户设置。
- 两页批次在页间暂停未释放 lease 曾导致监督器 UTC 17:17 排空失败；通过精确 settings token CAS 于 UTC 20:03 恢复，停机完整计入该组。仓库已补 owned batch 释放及真实 PostgreSQL 测试，87 passed / 12 subtests；此修复尚未部署，最终一页配置不会进入两页页间暂停路径，不为此重启已选生产 worker。后续重新启用多页前需在冻结包单独上线该修复，禁止部署工作树历史正文/hash 迁移。

- 用户重新明确授权在服务器持续运行，优化截止 2026-10-11 09:00 新加坡时间（UTC 01:00）。本轮仅 google_web / OpenSERP，News、Trends、RSS、正文均关闭；此前“自动采集关闭”记录为历史状态，不再作为本轮禁止启动的依据。
- 核心指标改为长时实测新增唯一 URL / 全部实际墙钟小时，包含等待、失败、重试与配置切换，不用单小时峰值代替。共享原历史 URL registry，不清空数据库、不重复计缓存；同配置重复阶段按累计数据/累计时长加权。
- 初始 UTC 09:30 窗口含旧三来源批次，已归档为无效；经过 worker 重启屏障后的纯 Web 基线于 UTC 09:44:01 启动，6 槽、1 页；供给沿用 google-serp-20261010、offset 90248、原 cursor。实验监督服务 realtime-google-web-longrun.service，状态/逐小时证据/排名在 state/google-web-longrun-20261010。每组计划 90 分钟，随后长时复验当前最佳配置；到截止时间固定符合纯来源和稳定性条件的已测最佳配置，继续纯 Web 采集，不宣称理论上限。
- 开机采集服务 realtime-google-serp.service 改为读取 state/google-web-longrun-20261010/current-compose.json；初期 worker/web 仍为原网站 r3 冻结包，后续优化 worker 代码单独冻结在本轮 code/realtime，web 保持网站 r3。只改 worker 的任务队列清理和配置速率上限传递，不部署工作树正文/hash 迁移。
- 网站关闭自动采集优先于监督器：监督器退出且不重启自动采集；配置切换采用 updated_at 比对与数据库行锁，操作员期间修改设置时不得自动覆盖。异常资源、来源/正文变更、人工任务混入会停止实验并保留记录。
- 需求、配置、度量方法及进展见 docs/google-web-longrun-2026-10-10.md。URL 规范化已于 UTC 12:29–13:01 维护期间修正为 google-tracking-v2，Google srsltid 跟踪变体不再重复计新增；旧 URL 主键、原请求和正文保留。首个纯来源对照完整 5,443.44 秒新增 9,913 个唯一 URL，平均 6,555.93 / 小时，成功率 97.14%（原 10,024 / 6,629.34 是修正前口径，作废但留档）。优化 6 槽组包括维护暂停，完整 6,384.68 秒新增 7,321，平均 4,127.94 / 小时；UTC 13:01:09 已进入 12 槽、1 页组。后续对照及长时复验未完成，不能称为最优或理论上限。
- 当前 baseline / optimized / web 冻结包位于 state/google-web-longrun-20261010/normalization-v2/code；只替换 SERP 规范化与存储文件，baseline 保留旧 worker 行为，optimized 保留队列/速率修复。源码哈希校验通过；历史统计按原起止时间重算，旧结果保存在 normalization-v2/original-metrics。
- UTC 13:31 代理中转因刷新监听端口遇到 EADDRINUSE 退出并自动重启。UTC 13:41:42–13:42:30 排空后单独部署 proxy_relay.py 修复：端口占用时保留其他监听，下次刷新重试；未修改共享端口映射或重建 worker。相关本地测试 20 passed、5 subtests；生产中转容器 4 项测试通过。原文件及维护证据在本轮 relay-bind-fix 与 relay-bind-*.json；维护完整计入 12 槽组时长。监督服务恢复后 MainPID 3238639。
- 进一步复现 Python 3.12+ 的 Server.wait_closed 会等待已接入长连接，导致刷新阻塞。UTC 13:52:30–13:53:23 排空后补充修复：关闭旧监听后立即创建新监听，旧隧道继续传输。相关本地 21 passed、5 subtests；生产中转容器 5 项测试通过。当前生产中转文件 SHA256 cc8f8c78987c1ff2e4804e3d81b7e179a126d14f73ff6763f2b87f25e3bd54a4，证据 relay-refresh-*.json。53.483487 秒暂停计入同一 12 槽阶段，worker 未重建、正文不变；监督服务 MainPID 3270956。
- UTC 14:00 核验旧 r3 冻结 worker 的 stopped 任务无限重排缺陷，最终长期选择新增 queue_safe 门槛：只选已验证 optimized 版本，旧 r3 全部数量/时长保留为控制基线，不恢复该已知缺陷。旧资格与原排名归档 queue-safety-gate/original-metrics；5 项选优测试通过，更新监督器时未暂停采集或重建 worker，MainPID 3292255。
- 12 槽组小时检查 UTC 14:02:19：完整 3,670.016137 秒新增 5,306，平均 5,204.77 / 小时，1,054 / 1,130 请求成功（93.27%），纯来源/正文基线/配置/队列修复检查通过。包含两次中转维护及失败冷却，phase2-hour-checkpoint.json 标记 checkpoint_only / not_added_to_ranking；仍继续完整 90 分钟阶段。
- 12 槽完整阶段于 UTC 14:31:46 完成，5,436.551812 秒新增 8,451，平均 5,596.12 / 小时，1,624 / 1,709 请求成功（95.03%），当前已完成长期候选最高。18 槽组从该截止时间起算，含重建和等待；实际 18 槽、1 页、90 秒、1 / 2 RPS，正文基线不变。
- UTC 15:16:56 追加 8 槽、4 槽，保留 18/24 槽高并发及两页测试；当前及后续阶段改为 75 分钟，仍覆盖完整小时，截止前剩余时间复验最佳。所有历史数量、边界、截止 UTC 01:00 不变；未暂停采集或重建 worker。原计划等在 adaptive-plan/original-*，证据 adaptive-plan-applied.json；冻结包及新增 Compose 校验通过，5 项选优测试通过。监督服务 MainPID 3517043。
- 18 槽小时检查 UTC 15:32:02：完整 3,616.227881 秒新增 6,950，平均 6,918.81 / 小时，1,347 / 1,363 请求成功（98.83%）；纯来源、配置、queue_safe、worker 稳定性和正文基线检查通过。phase3-hour-checkpoint.json 仅作 checkpoint，不加入排名或重复累计；仍继续完整 75 分钟阶段。阶段诊断成功搜索平均 47.25 秒 / P95 54.34 秒，目录 cycle 0、无 after 日期过滤，不能据此判定并发的独立因果效果。
- UTC 15:37:23 补充监督器降低页数时的 frontier 保护：只完成本 run 活动自动任务中已覆盖新页数且无有效在途 lease 的游标，保留 page_stats / next_page / 请求 / URL；避免从两页切回一页后请求页与旧 next_page 不匹配。24 项相关测试通过；部署未暂停采集、重建 worker 或改动设置/游标/阶段边界。证据 page-limit-guard-applied.json，原文件 page-limit-guard/original-supervisor.py；新监督器 SHA256 b95748eed2e2b7fedae185d2bdd0d7b2e66c4db7e2795ce12df02ce0bc28522d，MainPID 3578451。实际降低页数后的非零调整另记 page-limit-reconciliation-*.json，仍待后续现场核验。
- 18 槽完整阶段于 UTC 15:47:42.253803 完成：4,555.888635 秒新增 8,666，平均 6,847.75 / 小时，1,698 / 1,729 请求成功（98.21%），CAPTCHA 3 / HTTP429 21；纯来源、配置、queue_safe、worker 稳定性和正文基线均通过，成为当前已完成候选最高。独立只读数据库审计验证四段请求、新链接、逐小时数和完整时间连续一致，证据 measurement-audit-phase4-20261010T154840.json。两页组从该截止起算，实际 12 槽 / 2 页 / 90 秒 / 1–2 RPS，worker 00524e7f2cb51100217545414110ec76048353ed7f69725489746cc018c3c282；第二页实际请求仍待核验，不因配置正确即宣称已采到第二页。
- UTC 15:55 队列确认每批一页时，待采第二页任务前有 1,665 个未访问查询。旧 s12-p2 段于 UTC 15:59:55.657586 关闭并完整保留：733.403783 秒、1,103 新链接、216/221 成功，只有第一页、不到一小时，不参与长时选优。新 s12-p2-b2 从该截止起算，每批连续两页，仍 12 槽 / 2 页 / 90 秒 / 1–2 RPS、计划 75 分钟；后续全部候选和截止不变。实际页数成功覆盖和批次参数纳入资格核验，历史仅补页数元数据、不改计数/边界；25 项相关测试及 6 项选优断言通过，五段独立数据库复算通过。证据 depth-batch-guard-applied.json、result-06-s12-p2.json、measurement-audit-phase4-20261010T160040.json；原档 depth-batch-guard/。新监督器 SHA256 b239268c8583bd3c805407ba119bbdbd216fe421f0d4569ac03e73f02ba01f01，MainPID 3648303；worker 852f9bf985f66e7e2fd4ea3cfb6f552ac640cf246758b00caccc3f13f25adf4b。
- UTC 16:01:22 已确认新批次实际第二页：19 次成功、172 个自然结果项、83 个全历史去重新 URL；第一页 7 次成功、37 新 URL，另 6 次 CAPTCHA/429。只是启动快照，不加入排名或代替完整阶段；证据 phase4-batch2-startup-diagnostics-1601.json。正文及其他来源保持关闭。

## 商业化验证版工作台（2026-10-27）

- 正式地址 `https://searchflow.20-150-145-162.sslip.io/`，旧 HTTP 8093 自动跳转。新增搜索分析、默认暂停的关键词/竞品监测、客户 API 中心。查看公开历史数据无需登录；现有管理写操作改用管理密钥，避免绕过客户额度。
- 新模块 `realtime/product.py`、`product_http.py`、`product_ui.py`；`SEARCHFLOW_PRODUCT_ENABLED=true` 时启动协调器。只初始化独立 `sf_*` 表；禁止借此运行历史正文/hash 迁移。
- API 异步返回 202，要求 Bearer 客户密钥、Idempotency-Key；租户隔离、UTC 月额度、原子预留/成功结算、失败取消退款、60 次/分钟限流、平台每月 100,000 个 API 新任务上限。管理员手动采集与旧 worker 内部重试不在这个新任务上限内。
- 客户私有 campaign 用 `serp_task_options.options.customer_private=true` 标记，公开任务、数据、导出、详情不可泄露；旧 `/api/local-campaigns`、`/api/campaigns`、`/api/stats`、`/metrics`、`/api/search`、免费 benchmark 已停用。不要直接恢复没有这些保护的旧公开路由。
- 原站 `web.py` 与本地有既有差异，本次线上只加 serve 的 `start_service` 钩子，禁止以后直接用本地整文件覆盖。
- 管理密钥在服务器 `state/private/searchflow-admin-key`；操作人本机 `~/.config/searchflow/` 有凭据与接入说明，禁止输出/提交完整密钥。验证空间月额度 10,000、并发 3；生产验收留下一个暂停监测。全站持续供给、正文、Whale领取与上传仍关闭，不得自行恢复。
- 正式 API 一次非缓存搜索返回 5 条、成功扣 1 次；另一次原代理连接失败未扣次，预留均已释放。代理存在偶发故障，未验证长期成功率或 SLA。未配置 Serper，不得宣称完整商业后端已可用。
- 需求、边界、部署备份与验收见 `docs/searchflow-product-upgrade-2026-10-27.md`。旧 worker 与采集机五服务未重建；共享 Nginx 仅新增本站虚拟主机，本网站 gateway 通过 Docker bridge 私有监听接入 HTTPS。

## 深度搜索分析（2026-10-27）

- 搜索分析改为“数据总览 / 排名与竞品”：全筛选窗口汇总、每日趋势、来源/字段质量、域名/高频 URL、可搜索分页关键词；同条件最多 1,000 快照的多域名、排名波动、Top 3/10、页面留存/重返、标题摘要与相邻变化，以及 JSON/CSV。
- 新 `analytics.py` / `analytics_ui.py`。仅维护可重建 `sf_analytics_requests`、`sf_analytics_items`、`sf_analytics_reports`；原始大响应不适合每次页面读取时全量解压。协调器每轮增量 100 条；只读已有数据，不发起采集。汇总按分析数据版本和 UTC 边界复用，关键词翻页不重新扫描所有链接。冷筛选约 5–18 秒，已预计算的正式总览约 1–2 秒。
- 已投影 39,830 个公开请求、810,068 条分析明细；2 个客户私有请求未投影。窗口链接 809,788 条、去重 522,676；News 包装链接未解析，不当成媒体域名。29,601 个网页查询词仅 1,247 个有重复成功请求，不能将全部词宣称为排名趋势。
- 452 测试，446 通过、6 跳过；正式 HTTPS、分页、报告/每日 CSV、深度竞品与手机布局验收。未覆盖 `web.py`，只重启 Web；原始请求 39,832、正文 332,768 以及采集/正文/Whale/监测关闭状态均未改变。详见 `docs/searchflow-analytics-expansion-2026-10-27.md`。

## 非商业化工具 UI 优化（2026-10-27）

- 最新用户要求：不增加商业化；Google 来源用官方标识，品牌、UI 与交互更专业。已上线非商业化研究工具界面；隐藏账户分配入口，保留原 API 接入和权限能力，不新增付费/套餐/销售流程。
- 新 `workbench_ui.py`、`ui_icons.py`，在 `product_ui.extend()` 中组合。Google 产品标识仅用于来源；通用操作/导航使用 Google Material 官方 SVG，本地内嵌且附许可与素材清单。新增全局搜索、来源入口、结果密度、复制、详情侧栏、手机抽屉导航和键盘支持；分析增加请求取消、已应用/待应用条件、错误重试和报告目录。
- 发布备份 `state/ui-refinement-20261027-064850`；仅部署四个 UI 模块与许可，只重启 Web。生产 `web.py` / `serp_page.py` 哈希不变。452 项测试（446 通过、6 跳过），正式桌面/320px/390px、图标、分析/排名、无脚本错误验收通过。
- 原始请求 39,832、正文 332,768 及全部采集/正文/Whale/监测关闭状态不变；没有新搜索与旧库迁移，未启动本机 Docker。详见 `docs/searchflow-ui-refinement-2026-10-27.md`。

## 全功能测试（2026-10-27）

- 本轮独立 PostgreSQL / Redis 下完整 452 项通过、0 跳过；本地浏览器 44 组、正式浏览器 9 组通过，正式 API/数据审核 62 项。详细证据 `docs/searchflow-full-test-2026-10-27.md`。
- 正式新增 Web/News/Trends 各 1 次非缓存私有 API 搜索：9 条 / 47 条 / 10 话题（28 报道），用量最终 4、reserved=0；公开历史及分析明细未改变。原始请求现 39,835，pages 332,768。
- 三来源各 1 条已有公开链接真实上传，均一次 queued；Whale queued 总计 9。Collector Key 读取这三条文档仍 401，不能把回执称作最终入库。持续采集/正文/上传/领取/监测最终均关闭。
- 仅更新 `workbench_ui.py` 并重启 Web，修复图表切指标后键盘焦点丢失和 API 结果数量说明；`web.py` / `serp_page.py` 哈希不变，备份 `state/fullqa-ui-fix-20261027/workbench_ui.py.before`。
- 已知数据出口限制：API 七列 Trends CSV 只含话题，不展开相关报道，真实样本 10 行 / 0 链接；JSONL 保留 28 报道，网页 44 列 CSV 可展开。此项未修复，后续勿宣称所有导出等价完整。
- 本轮本机独立 HTTP / PostgreSQL / Redis 均已停止，未启动本机 Docker；未运行旧迁移、未重启 worker、未提交 Git。

## 前置访问 Key（2026-10-27，最新授权）

- 用户明确要求网站先输入随机 Key，最新要求覆盖历史“公开无登录”。整个 HTTPS 工作台及 API 已通过专属 Nginx auth_request 保护；未认证浏览器跳登录页、数据/API 返回 401。不得自行恢复公开访问。
- 新 `realtime/access_gate.py`，标准库独立服务 `google-workbench-access-1`，loopback 18095；模板 `deploy/access-gateway.nginx.conf`。仅新增此服务、reload 专属 gateway、更新退出入口并重启 Web；共享 HTTPS Nginx与采集 worker 未改。
- 本机 `~/.config/searchflow/site-access-key` / `网站访问说明.md` 为访问凭据，权限 600，禁止打印/提交。生产 `private/site-access.json` 仅摘要与 Cookie 签名秘密；Key 与 operator/customer Key 分开。
- Cookie 12 小时、Secure / HttpOnly / SameSite=Strict；左侧退出清 Cookie。API 需额外 `X-SearchFlow-Access-Key` 请求头，原 Authorization Bearer 保留；只有旧客户 Key 仍被前置层拒绝。
- 本轮鉴权 4 单测通过、32 项线上验证、5 项浏览器验证；公网 18093/18094/18095 不可直连。持续采集/正文/上传/领取仍关闭。备份 `state/site-access-20261027`，详细 `docs/searchflow-site-access-2026-10-27.md`。

## 网站可靠性优化（2026-11-02）

- 已上线首页轻量汇总 `/api/product/analytics/summary`：只计算四个首页指标，保留私有隔离、UTC 边界、投影状态、生成时间和最多 60 秒缓存；不再用完整 overview 冷计算承担首页数字。
- 任务/结果/首页汇总增加读取状态、旧数据标识、手动重试和 20/30 秒等待上限；写请求超时不自动重放。结果导出锁定已成功应用的筛选，Trends API 七列 CSV 补齐话题下全部报道行。
- 生产仅发布 `analytics.py`、`product_http.py`、`workbench_ui.py`、`reliability_ui.py` 并重启 Web；`web.py`、`serp_page.py` 未覆盖且哈希不变。回滚备份在 Web 服务器 `state/reliability-20261102/backup/`，证据和测试记录在本地 `state/reliability-20261102/`。
- 隔离回归 463 通过 / 6 跳过 / 0 失败；生产轻量汇总冷测量 all 5.175s、Web 1.509s、News 2.290s、Trends 0.042s；线上首页、任务、结果、分页、未登录 401、390px、既有 Trends API 导出 10 话题 + 28 报道均验证。
- 发布前后原始请求 39,835、pages 332,768、持续采集/正文/Whale 上传/领取 false、启用监测 0、投影 39,830 均不变；未创建新搜索、未运行迁移、未重启采集机容器。详见 `docs/searchflow-reliability-improvement-2026-11-02.md`。

## 数据层优化（2026-11-06）

- 最新用户方向：先做数据获取、完整保存、去重和交付，暂不新增分析功能。已上线标准旧库接口 `/v1/google/legacy-data` 及 request 详情，沿用访问门、客户 Bearer 和公开/私有 SQL 隔离；明确标记旧库来源，不冒充 Whale 读回。
- 新 `google_data.py` / `google_data_export.py`：保留独立 occurrence、Trends 话题/报道和源字段；操作员可在单个只读快照导出 JSONL/CSV/occurrence CSV 与校验清单，截断明确标记、失败不发布、复制后可校验。大于 CSV 默认 128 KiB 的真实响应已修复，单请求限制 64 MiB。
- News 全量 551,494 条 RSS 原日期与保存日期均匹配；旧映射要求历史缺失的 collection_method 导致错误显示未知，现已按实际日期依据修复。RSS 匹配不等于出版者网页独立核验；文章真实链接仍大量未解析，Trends 数值历史曲线仍未覆盖。
- 采集机实际冻结路径 `state/field-expansion-20261016/code-r2/local-worker/realtime` 仅应用本轮时间记录/重复 URL 首次贡献/字段过滤补丁及 helper；只重启 local-worker，其他四服务未重建。Web 仅更新数据模块及字段过滤，只重启 web/prd，未覆盖 web.py/serp_page.py，未运行任何数据库迁移。两机回退备份 `state/data-optimization-20261106/backup-{web,worker}`。
- 完整隔离回归最终 822 通过、0 跳过；冻结采集代码 94 项通过；真实三来源样本 API/导出逐 request 一致、JSONL/CSV/hash 校验通过，实际客户 Key 路由三来源 200、无 Key 401。生产原始请求 39,835、pages 332,768、公开投影 39,830、Whale queued 9 均不变，持续采集/正文/上传/领取 false，启用监测 0。
- 用户已授权在 Marsyoo 网站创建 Consumer 只读 Key；ego lite 空间 25 已 handOff 等待用户登录，尚未创建 Key或真实读回。正式 Whale schema/终态、新采集端到端、七天与百万行验收仍未完成。详见 `docs/google-data-optimization-2026-11-06.md`，证据 `state/google-data-optimization-20261106/`。

## Marsyoo 单次完整快照读回（2026-11-06，覆盖上节等待状态）

- 已创建专用 `SearchFlow Google Readback` Consumer 应用并收窄数据集、平台、字段和速率；关闭额外功能。Key 位于本机 `~/.config/searchflow/whale-consumer-read-key` 与 Web 服务器 `state/private/whale-consumer-read-key`，600，禁止输出或提交。
- 用户明确授权仅上传一条已有公开 Web 请求（7 条结果）。仅一次真实 bulk，终态 accepted；固定版本完整 platform_payload、原响应、摘要与 7/7 结果均一致。平台质量标记 low，必须保留此限制。未开启自动上传或新搜索。
- 旧 9 条版本均可读，但摘要仅 8 条匹配、1 条待对账，旧版本均无完整 platform_payload，旧回执均 unknown，不自动改写旧本地 queued。新样本不冒充正式全需求 ReaderAdmission 或全部来源交付验收。
- 新只读 `/v1/google/legacy-delivery` 与历史页面显示缓存证明，沿用鉴权及私有隔离；默认 24 小时标记过期。采集机仅补 HTTPS 端点与拒绝重定向，配置 `state/private/whale-api-endpoint.json`；Web/prd 与 local-worker 重启，其他四服务未重建，未运行旧迁移。
- 完整回归 837 通过、0 跳过；浏览器接口 200/匿名 401，完整样本 7/7。原始请求 39,835、正文 332,768、投影 39,830、旧队列 9 queued 口径保留，所有自动开关关闭。两机备份 `state/marsyoo-readback-20261106/backup-{web,worker}`，Web 再次发布 `backup-web-r2`；详见 `docs/marsyoo-google-readback-2026-11-06.md`。

## 同条快照质量修正（2026-11-06，覆盖上节本条 low 状态）

- 用户授权解决质量标记后，平台管理后台明确显示 v3 / 40 分 / `informativeness:low` / 文本 0 字符；未发现受支持的质量 profile 配置入口，未修改共享 Dataset 或全平台规则。
- 新增显式 `with_search_snapshot_text()`，仅将已有真实标题、链接、摘要、排名等生成标明“搜索结果快照、非出版者正文”的可检索文本；缺失字段不造值，原始 platform_payload 和源摘要不变，事件键使用独立稳定后缀。同条文档仅一次修正提交，accepted，新固定版本 `ver_4a29e4654356820e56abd6407398d5b0` 已为 normal / 100，2,358 字符，7/7 原结果与文本均逐字段/逐字读回一致；原版本仍可读。此结果不等于全平台专用评分规则已实施。
- Web 仅更新纯 helper 与证明缓存，不重启任何服务；备份 `state/marsyoo-readback-20261106/backup-quality-text`。正式状态已显示 normal；839 项测试全通过，原始请求/正文/投影/旧上传队列与全部暂停状态不变。历史 1 条摘要不一致仍待对账，勿宣称全部需求完成。详见同一读回文档的后续授权章节。

## 工作台访问 Key（用户明确要求本地记录）

- 网站：`https://searchflow.20-150-145-162.sslip.io/`
- 访问 Key：保存在本机 `~/.config/searchflow/site-access-key`，不得提交凭据。
- 用户明确授权将此凭据记录于本项目 AGENTS.md；仅用于该工作台访问验证，不在回复、日志、截图中展示，不提交到 Git 或对外分享。


## Google Images 数据来源上线（2026-11-06）

- 最新用户授权已实现图片手动采集、保存、任务/数据集入口、分页、CSV/JSONL、客户异步 API 与规范化历史读取。实际入口 `/google/image`，每词首批最多请求 20 条，图片并发 1；不下载原图，不做 Lens/OCR 或新增图片分析。缩略图只在上游提供时按需预览，真实本轮样本均未提供。
- 生产 worker 图片分支使用新 `google_images.py` helper 与 discovery 调度，保留基础 `free_google.py` 原版本。保存每次出现、图片/来源页双 URL、原始响应与未知字段；图片 URL 精确保留参数/编码，不用网页规则合并 CDN 变体；重复出现只计一次首次贡献。旧分析排除 images，防止当成零条 Web 结果。
- 网站向导真实首轮三词请求有 2 次成功、1 次 Google 429；仅对失败词做一次手动重试成功，公开保存 60 条（55 条有宽高，缩略图 0）。客户 API 另 1 次私有成功、20 条，用量增量 1、reserved=0；幂等重放不重复扣次，公开列表与历史不泄露私有数据。总新增 5 次请求 / 4 成功 / 1 次 429 / 80 条结果记录，不能冒充 80 个唯一图片或文件，未验证长期成功率。
- 网页 CSV 50 列含图片原始记录 JSON，60 行与 JSONL 逐条一致；图片客户 CSV 14 列、20 行与 JSONL 一致，旧客户三来源七列不变。实际 25+25+10 分页、桌面/390px 与规范化图片历史已核验；完整回归 846 项全通过，最终冻结 worker 43 项 / Web 42 项通过。
- 图片不进入旧 Whale 协议：图片任务自动入队明确拒绝，显式图片上传不支持，“上传全部”排除图片。未新增任何平台写入，不得把旧 Web 快照的读回验收算作图片交付。
- 最终 requests 39,840（旧三来源 39,835 不变、图片 5），pages 332,768、原公开分析投影 39,830、旧 outbox 9 queued；continuous/body/uploads/claims false，活动图片任务与启用监测均 0。未运行历史迁移或重建其他四采集服务，web.py/serp_page.py 哈希未变。
- 两机备份在各自项目 `state/google-images-implementation-20261106/backup-{worker,web}` 及 `-r2`、`-r3`；不要重跑已执行发布脚本。完整功能、限制和证据见 `docs/google-images-implementation-2026-11-06.md`。浏览器任务完成只保留结果页；本机已有数据库/Redis 属其他任务，未关闭。

## 内网部署架构修正（2026-11-09，覆盖原网页整体位于应用机的部署记录）

- 页面、访问门和反代仍在应用机 20.150.145.162 / 10.0.0.7；原 web/prd 已停并保留回滚。应用机仅发布线上导出的静态页面，后续 UI 更新需同步 `static/`，不能只改后台 Python。旧公网数据隧道 disabled。
- 网站业务后台已在采集机 20.106.103.139 / 10.0.0.11 的独立 `/home/azureuser/projects/google-workbench-backend`；Compose/systemd 名 google-workbench-backend，内网入口 18097 仅 allow 10.0.0.7，后台 loopback 18093/18096。不要把业务后台迁回应用机。
- 后台和 worker 将 Whale HTTPS 域名固定解析到 10.0.0.10:443，保留证书校验，无公网备用；worker 仅为固化 HTTPS/hosts 单独重建，未改变冻结代码或重建其他四采集服务。原本机数据卷/队列保留，全部自动开关仍关闭。
- Writer 身份与 dry-run 通过；本轮查询历史固定版本终态 accepted。独立 Consumer 当前 402 credit_insufficient，未完成本轮读回/新提交，不充值或扩大旧单次上传授权。数据基线仍 39,840 请求、332,768 pages、39,830 公开投影、9 旧 outbox。
- 两机原项目 `state/architecture-20261109/backup/` 与 `rollback.sh` 可回滚；详见 `docs/deployment-architecture-audit-2026-11-09.md`。禁止直接用历史无鉴权配置或全量本地 web.py 覆盖生产。


## 全功能测试与三十分钟采样（2026-11-09）

- 用户本轮授权先小样本、再采集 30 分钟，已完成并停止；此授权不延续为无限持续采集。正式 1800.008 秒，32 个任务、51 次非缓存尝试/40 成功、318 个首次贡献 URL，缓存复用 4 次另计；停止与排空计数一致。
- 最终隔离 1027 项通过、333 子测试通过、无跳过；新增可复用回归/采样/看护/报告脚本。News 包装链接仍未解析，Web 本轮成功率 75%，Images 80%，不能称长期稳定或最大产能。
- 私有来源缓存可见性与同条件读取合并已本地修复并回归，尚未部署；两机 SSH 认证前断开。没有覆盖冻结包、重启生产服务或运行旧迁移。
- Consumer 既有 Web 固定版本本轮经 ego 公网 HTTPS 再读回 200，7/7 原结果及内容/原始哈希一致、normal；覆盖此前额度不足的当前读取状态，但不证明采集机私网复验、新上传或正式 ReaderAdmission。未新增平台写入。
- 最终持续供给/正文/上传/领取 false，启用监测 0，reserved=0。本轮 QA 监测保留暂停，临时客户 Key 已撤销。证据 state/fullqa-20261109，报告 docs/searchflow-full-test-2026-11-09.md。


## 总需求字段适配（2026-11-09，尚未发布）

- 已按总 PRD 5.1～5.6/GO-DAT/GO-ID/GO-ING 整理17域字段矩阵；docs/google-prd-field-adaptation-2026-11-09.md 与对应 field-matrix JSON 为本轮记录，不把273项总需求改判完成。
- 新 google_field_adaptation.py 只从已存数据生成版本化字段；legacy-data list/detail 与操作员导出显式 field_contract=prd-v1，默认旧合同/旧 Whale 事件不变。新增时间依据、请求/返回/保存量、缓存三态、语言、item/feature/子项和缺失原因；普通链接不自动当引用，PAA错误文本不当答案。
- 51请求/40成功/399链接出现/10话题的既有样本离线复算：26富结果（23 AI摘要/3 PAA），59普通模块链接、12子项，无明确引用或独立答案字段。设备、源观测时间、缓存年龄等仍未知；News真链接、数值Trends及授权资产域仍未覆盖。
- 最终隔离1046项、333子测试通过，0跳过；临时PG/Redis清理。旧固定Web observation hash一致；嵌套查询条件补同样脱敏。未发新搜索、未平台写入、未迁移或重启生产；SSH短暂hostname成功后后续仍认证前断开，补丁尚未发布。禁止将本地全文件整包覆盖冻结生产；发布需核对实际差异并保留回退。
- 本轮证据 state/prd-field-adaptation-20261109/；增强字段当前只在显式JSON/请求CSV的record_json，不改网页50列或客户7/14列，也未新增可索引数据库列。

## 采集可靠性与字段版本上线（2026-11-09）

- 已发布私有来源缓存隔离、结果读取单飞、显式 `field_contract=prd-v1` 及嵌套参数过滤；单次任务不再把未到重试时间的空批次误判完成。新手动单次任务默认从首次执行起最多 3600 秒，超时 `retry_window_exhausted`；图片临时失败可等待重试。旧客户 API 独立 15 分钟期限保留。任务详情和 `/api/serp/tasks/{id}/progress` 显示等待/恢复，私有任务 404。
- 新手动创建支持可选 `Idempotency-Key`，事务锁避免并发重复，参数冲突拒绝；网页响应未确认时保留请求体摘要与随机标识。真实浏览器在服务端提交后模拟丢失响应，重试复用同一 paused 任务、冲突 400，验证任务已停止且没有新搜索。后台最后三文件在长测结束后发布，仅重启后台 web/prd，worker 未再次重启。
- 完整本地隔离回归 1058 passed、333 subtests、0 skipped；冻结采集包 24 passed/4 subtests、后台字段包 23 passed。四来源小样本 4/4 成功，Web4/News54/Images20/Trends10话题30报道，PRD 导出 4 请求108 occurrence，API/JSONL一致，跨机复制后校验通过；新增87 URL。
- 30 分钟正式采样1800.004317秒、34任务：55次真实保存请求52成功（94.55%），另1成功缓存关联；53个查询/来源/页码组合52曾成功，两次超时约73.300/70.198秒恢复，一次403冷却中按测试截止停止，新增141个历史去重URL。不是最大产能或与前测的同条件对照。两次SQL快照无晚到新增，最终请求39960、正文332768、公开分析投影39933、Whale旧outbox9；自动采集/正文/上传/领取false、启用监测0、活动手动任务0。不得据本轮自动恢复持续采集。
- 仅覆盖实际冻结包的本轮补丁；`web.py/serp_page.py/free_google.py/config.py/campaign_store.py` 哈希保留；原OpenSERP/proxy-relay/PostgreSQL/Valkey未重启。采集机备份 `/home/azureuser/projects/google-workbench-backend/state/reliability-20261109/`，安装/回退脚本 `/home/azureuser/reliability-rollout-20261109/install_patch.py`；应用机静态备份 `state/reliability-20261109/backup/index.html`。回退先恢复静态页再恢复相关后台/worker文件，禁止旧正文/hash迁移。
- News本轮226/226仍是Google包装链接；数值Trends、其他外部资产和全来源正式ReaderAdmission未完成，源未给的字段继续未知。详见 `docs/searchflow-reliability-optimization-2026-11-09.md`、`state/reliability-rollout-20261109/`。

## 最新执行顺序（2026-11-09，采集与字段适配本轮）

- 用户最新要求：先完成全部功能开发与测试，最后统一更新部署。本轮后续保持本地开发/隔离测试；不再发布、重启服务或追加生产采集测试，不能因为已有 SSH 通道或测试通过就自行继续部署。
- News 真链接核心适配在此次顺序调整前已发布，真实一条 RSS 请求解析 55/55；暖缓存任务完成且未新增解析 HTTP 请求。后续本地兼容性保护未发布，不能把本地增强和生产版本混为一谈。不自行回滚之前已发布内容。
- 本地候选及证据在 `state/news-link-adaptation-20261109/`；`predeployment-manifest.json` 明确 deployment_hold=true。完整整理开发、测试、真实验证与外部依赖后，再进入最后统一部署阶段。

## 完整数据 API 候选（2026-11-09，等待最后统一部署）

- 已补现有数据功能的客户接口：本人/公开/可访问历史、请求及结果/话题/富结果详情、本人任务增强字段、74 列完整 CSV/JSONL、带校验清单的只读快照 ZIP、字段目录、抽样质量、公开分析 JSON/CSV、任务分页、进度和暂停/继续、原子批量搜索。原 7/14 列导出保留；客户权限不扩展为管理权限。主 OpenAPI 包含 54 条路径并记录已有管理/Whale 接口；未连接的数据域仍不可用。
- 完整隔离回归 1087 passed、333 subtests、0 skipped；实际生产冻结组合 53 passed、7 subtests。临时发布期间四来源已有客户任务的完整 JSON/JSONL/CSV 逐字段一致，ZIP 样本校验通过，权限负例和分析/分页/进度接口验证通过。没有新搜索或平台写入；成功批量提交及暂停/继续的写入验收为隔离测试，不冒充生产采集验收。
- 本轮未先遵守末尾发布 hold，曾提前发布八个 API 文件及三个性能索引；发现后已全部撤回，生产源码哈希恢复本轮前状态，之前 News 真链接发布保留。当前新增 API 不在线，`state/customer-api-20261109/predeployment-manifest.json` 明确 deployment_hold=true；不得自动重放有 marker 的旧安装脚本。
- 生产最终请求39961、pages332768、公开分析39934、旧 outbox9，与本轮开始一致；continuous/body/uploads/claims false，启用监测、活动客户任务、在途 lease 均0。只曾重启专属后台 web/prd，采集机原五服务容器ID未变；未改应用机静态页面、web.py、serp_page.py、serp_console.py、google_http.py，未运行旧迁移。
- 接入说明 `docs/customer-data-api-2026-11-09.md`；证据/候选/补丁清单在 `state/customer-api-20261109/`。远端回退备份在采集机独立后台 `state/customer-api-20261109/backup/`，`rolled_back.json` 记录已恢复；完整 CSV 大 JSON 单元格需适当解析上限，机器交付优先 JSONL/ZIP。全库模糊查询长期性能、七天/百万行和未配置来源不因本轮补接口而视为完成。

- 范围随后已由用户明确：本轮先完成 **News 真链接全链路**；不扩展成所有 PRD 数据域同时开发。最新本地全量 1087 passed / 333 subtests / 0 skipped，相关功能 95 passed / 11 subtests。部署冻结继续有效；本地候选与生产较早核心版本分开登记，报告 `docs/google-news-link-adaptation-2026-11-09.md`。

## 最终统一发布授权（2026-11-09）

- 用户现已明确授权提交、推送并统一更新网站，完成后小范围数据自测；覆盖此前 deployment hold。仍保持持续采集、正文、自动上传和领取关闭；仅部署已复核的冻结补丁，不运行旧迁移。

## 最终统一发布完成（2026-11-09）

- 功能提交 c4215ea 已推送 main，用户明确授权覆盖此前 hold。后台八文件与 worker 单文件保护补丁已上线；静态首页与最新冻结UI字节一致，无需重复替换。三个读取索引有效，未运行旧迁移，受保护 web.py/serp_page.py 等未覆盖。
- 全量1087测试/333子测试、冻结后台33/7、冻结worker65/4通过；四来源既有样本与新News样本JSON/JSONL/74列CSV一致，ZIP校验、权限和幂等通过。
- 唯一新News任务非缓存成功10条、10/10解析、新增8 URL；最终requests39962/pages332768/公开分析39934/旧outbox9。两次快照一致，所有自动开关false、活动客户任务/lease/监测0。未新上传，ego Space52已清理。
- 回退备份在采集机独立后台 state/final-release-20261109/backup/；发布报告 docs/searchflow-final-release-2026-11-09.md。历史News未批量补链，单次样本不代表长期稳定性。

## 对外API与全量数据排查（2026-11-10）

- 只读核验全库39962请求/811206次出现，标准化无丢行/ID碰撞/关联孤儿；177条线上矩阵请求、89项隔离测试/10子测试。公开39951、本客户11，分析39934恰好排除公开图片17，私有投影0。没有新搜索、上传、部署或生产修复。
- 确认未修复：排名分析带format=csv或json会400；网页curl缺站点访问头；字段目录漏9项图片字段；历史service_error筛选400；country=US误空、us命中。浏览器跨域预检501，无CORS；后端两层Key鉴权实测成功。不能宣称所有API已全部验收。
- News公开552055次中552000仍包装链接，仅55解析；私有91中81包装、10解析。六次Web URL被明确脱敏，图片缩略图全缺、作者/canonical均未知；Trends仍仅热点RSS，研究域not_covered。默认7/14列CSV不等价完整74列；快照最早优先并受限额，列表最新优先。
- 最终所有自动开关false、活动任务/lease/监测0，数据基线不变。详细报告docs/customer-api-data-audit-2026-11-10.md，证据state/api-data-audit-20261110/。

## 客户增量订阅与交付修复（2026-11-10）

- 五项审计缺陷已上线：排名显式JSON/CSV、两层Key示例、图片字段目录、service_error筛选及国家大小写。客户拉取订阅位于/v1/data/subscriptions，区别于研究域订阅；默认暂停，支持occurrences/new_urls、幂等、确认账本、迟到提交、完整下载与文件校验、历史批次重读与阻塞批次丢弃。events是增量项，requests保留完整上下文；new_urls不是全历史首次贡献。
- 网站API工具新增数据订阅；保留一个“示例：已有网页数据（暂停）”，已领取并确认4个本人旧Web请求/18个事件，最终active=false。不启动采集；客户端需定时拉取，Webhook未连接。新闻补链为独立证据表与限量可暂停工具，原始响应不改；生产只做离线预览，历史55万条未在线回填。
- 后台10文件与静态页哈希验收，web.py/serp_page.py/serp_console.py/google_http.py不变；仅专属后台web/prd重启，采集机五容器未重启。首次缺依赖预检查失败后自动回退；后补同版本解析器依赖与网页列表修复，已上线。后台回退state/subscriptions-20261110-v2/backup，应用机state/subscriptions-20261110。
- 前后原始requests39962/pages332768/公开分析39934/旧outbox9一致；自动采集、正文、上传、领取false，监测/活动任务/lease0。报告docs/data-subscriptions-release-2026-11-10.md，合同docs/data-subscriptions-2026-11-10.md，证据state/subscriptions-20261110/。未提交Git，未扩大CORS。

- 最终回归1098 passed / 333 subtests / 0 skipped，固定候选包40 passed / 3 subtests；CSV/JSONL/manifest逐项及SHA256一致。ego Space58与本地预览服务已清理。

## 订阅进度、新闻补链与七天巡检（2026-11-10 第二轮）

- 已上线 start_mode=now/history；API默认history兼容旧调用，网页默认now，创建仍暂停。now用已存匹配request快照排除历史，后提交的旧时间戳请求仍交付。新增status精确待处理请求、最近确认、分页batches及网页重新下载；大批量去重/确认改为批量SQL。新增“示例：从现在开始（暂停）”，与旧示例均active=false。
- 新闻补链增加attempts/next_retry_at、deferred跨请求继续及显式unresolved重试/最长24小时退避。生产限2条真实Google RPC解析，2/2 resolved、4次HTTP、0出版社请求；仅sf独立证据表，不改原SERP或URL贡献，不代表55万条完成。
- 后台仅5模块与静态页更新，回退分别在两机state/subscription-next-20261110；保护文件和原五采集容器不变。全量1108 passed/333 subtests；真实冻结包28 passed；隔离1万events分3批无重复或丢失（非生产吞吐）。线上起点/进度/历史下载SHA256/401/404/390px通过。
- 只读searchflow-delivery-integrity.timer每15分钟检查旧已确认4请求/18URL归档、确认账本、JSONL/CSV摘要与存储增长；固定截止2026-11-17T03:05:18.436923Z，进程重启不重置。到期记录completed或completed_with_issues后停用自己的timer。目前运行中，不能称七天通过，也不代表端到端新采集或网站可用性SLA。状态在采集机/home/azureuser/projects/google-workbench-backend/state/subscription-next-20261110/soak.json。
- 原始requests39962/pages332768/分析39934/outbox9及所有关闭开关不变，活动任务/监测/lease0；未提交Git。详情docs/data-subscriptions-next-release-2026-11-10.md。

## 最新数据能力建设方向（2026-11-10，用户确认）

- 本轮主需求为 `docs/google-data-capabilities-requirements-2026-11-10.md`，结合原总PRD及最新实际能力持续维护。先完善新数据获取、完整保存、去重/版本和完整交付；优先实现历史热度曲线、关键词搜索量、Google AIO及AI Mode完整回答。历史曲线指新任务获取源历史序列，不是旧库回填。
- 暂不处理现有历史数据，不批量补旧新闻链接、不改旧响应/历史贡献；暂不新增数据分析、预测、评分或AI研究报告，已有业务功能保留。三项之外按总PRD维护完整数据域缺口及后续条件域，不把有接口框架等同于真实接通。
- 每完成一项数据能力，必须同步主客户API、OpenAPI、capabilities、字段目录、任务/进度/错误、完整查询/JSONL/CSV/ZIP、适用订阅、示例和测试。网页可用而客户API缺失不算完成；旧四来源、鉴权及客户隔离保持兼容。新域路线和端点以需求文档为提案，实施前核对现有合同并冻结。
- 每域分别贯通真实来源→任务→原始响应/标准事实→去重与修订→API/导出/订阅→适用平台终态及独立读回。相对热度不冒充搜索量，生成模型API不冒充Google消费者AI回答，普通链接不冒充引用，片段不冒充完整；未知/低量/失败/缓存/部分数据明确标记。
- 状态分开记录本地实现、隔离测试、真实来源验证、上线、API验收与平台读回。来源/凭据/用途/费用依赖准确登记；不凭样本或代码存在宣称全域完成。每功能完成后更新需求状态与证据。
- 当前请求是整理需求，不自动新增真实搜索、采购或账号配置，不恢复持续采集、正文、自动上传/领取或监测。后续按已有明确授权和具体范围推进，不重复询问已授权事项；保护生产冻结差异文件，不运行旧迁移，不重写历史事实。

## 新数据能力合同第一阶段（2026-11-10）

- 新增内部 `data_capability_contract.py`：四个新能力严格参数、独立哈希、来源到期与费用门禁；不挂客户路由、不自动准入来源、不使用旧搜索额度。请求哈希不作为观测去重键，重复输入映射保留。
- 测试4 passed/23 subtests；真实适配器、持久化/原子额度、全部数据交付链路仍待实现，不能宣称已接通。合同/依赖/回退见 docs/data-capability-contract-2026-11-10.md。生产无改动，自动开关保持原授权边界。

## 新数据能力任务与预算第二阶段（2026-11-10）

- 本地新增 `data_capability_jobs.py`，显式初始化独立 sf_capability_* 表；并发幂等、独立货币日/月预算与预留、单次lease、终态结算、取消和过期对账。执行前重新检查准入；费用不明时不自动重试或退款，实际超额账单须可信操作员对账。预算按创建UTC窗口归属，不声称供应商按调用日账单。
- 请求原词/prompt现在保留空白，避免静默改写真实条件；country仍按合同规范大写。新增与旧客户API/订阅/产品隔离回归52 passed/33 subtests/0 skipped，独立PostgreSQL已用于真实事务/并发验证。
- 尚无真实适配器、事实持久化与完整客户接口/导出/订阅；result_ref仅内部存储接口，不宣称全链路成功，不能直接开放。没有部署、来源调用、生产数据库或开关变更。合同 docs/data-capability-contract-2026-11-10.md 更新状态/费用/恢复语义。

## 新数据能力事实与导出第三阶段（2026-11-10）

- 本地新增 data_capability_facts/store/export：四能力的标准事实合同、sf_capability_* 独立观测与可重建子记录、同事件重试/同值新观测/显式修订与尺度隔离、当前权限/到期、缓存原观测关联。保存与结算同一事务；基础队列现拒绝凭任意 result_ref 成功。尚无源适配器/实际执行器和主客户路由，不可宣称新域已接通。
- 完整单观测JSON/JSONL/CSV/ZIP/manifest，payload_hash与snapshot_hash、逐文件摘要/数量和父子重建校验；长回答、CSV公式/大单元格、损坏/篡改、投影重建与原事实损坏拒绝。source cache未知不显示miss；本地cache年龄源时钟超前为null；源舍入0不冒充true_zero。
- 最终相关隔离回归77 passed/85 subtests/0 skipped；JUnit与模块哈希在state/data-capabilities-20261110，合同及缺口见docs/data-capability-facts-2026-11-10.md。所有样本为合成fixture，不冒充真实源验收。没有部署、付费调用、平台写入或生产开关变化；主API/订阅/网页/真实源/平台/七天规模仍待完成。


## 新数据能力客户 API 第四阶段（2026-11-10）

- 本地接入四能力客户 API、只读费用上限预览、任务/真实状态/取消、观测与父子分页查询、完整导出；同步 OpenAPI 1.2.0、capabilities、fields 和调用示例。沿用访问门与客户 Bearer，独立预算、租户与当前来源权限；签名游标绑定条件/租户/版本。未配置来源返回409，无活执行器返回503，不接受无人执行的202。
- 新runtime只能显式挂载，默认无初始化/线程/来源调用；新域订阅仍not_connected。API的四能力端到端测试使用明确标记的合成执行线程，不计真实来源样本。完整独立 PostgreSQL/Redis 回归1164 passed/426 subtests/0 skipped，证据state/data-capabilities-20261110/api-regression；两套本轮测试数据库均已停止。
- 详见docs/data-capability-api-2026-11-10.md；本阶段未部署，保护文件web.py/serp_page.py未改。真实来源/许可/预算、实际执行器、订阅/网页、平台读回与真实规模/七天验收仍待完成；不能宣称整体接通或上线。


## 新数据能力订阅第五阶段（2026-11-10，本地候选）

- 主/v1/data/subscriptions新增显式domain=data/mode=observations分流，保留旧默认；默认暂停、now/history基线ID、每批完整单观测、新观测/修订事件、领取重试、校验确认后推进、丢弃不推进、进度与签名历史分页。只读已有新域事实，不启动采集/供给，不因缓存关联生成新观测事件。
- 新域批次五格式及内外独立摘要校验，确认前后下载相同；读取/确认/进度/历史均检查当前来源许可和参数政策，过期410，跨租户404；撤权仍允许拥有者暂停/丢弃。OpenAPI1.3.0/capabilities/fields及示例同步，见docs/data-capability-subscriptions-2026-11-10.md。
- 未部署/真实源调用或平台写入；不改web.py/serp_page.py或生产开关。实际来源/许可/预算、执行器、网页、平台读回及真实规模/七天目标仍未完成。

- 第五阶段最终隔离回归 **1174 passed / 430 subtests passed / 0 skipped**（127.89秒）；命令及XML在state/data-capabilities-20261110/subscription-regression-final，源码/文档/OpenAPI摘要在subscription-manifest.json。本轮四套独立PostgreSQL/Redis均已核验退出，HTTP/fixture线程已关闭。未发布或变更生产。


### 本地第六阶段：数据能力工作台（2026-11-10）

- 新增“数据任务”：四能力参数、来源/预算预览与显式提交、任务进度/取消、独立观测与子记录、完整五格式下载、默认暂停的新域订阅及人工校验确认；复用现有客户鉴权。基础源曲线不跨尺度、不连接未知/时间断档。
- 完整隔离回归1176 passed/430 subtests/0 skipped；最终画布及组合页面专项3 passed。ego lite验证四域、下载独立校验、批次确认前后相同、连接切换隔离、提交响应丢失后的幂等重试、来源/执行器/过期门禁及320/390px。仅合成fixture，详见docs/data-capability-ui-2026-11-10.md。
- 本地候选未部署，web.py/serp_page.py未改，无真实来源/平台调用或生产变化。本轮HTTP/worker/数据库/Redis及ego空间已清理。真实来源/许可/预算、实际执行器和启动挂载、平台各域读回、真实规模/七天验收仍待完成。


### 本地第七阶段：来源执行器与费用不确定恢复（2026-11-10）

- 新增data_capability_executor.py：显式初始化/启停、四能力可信适配器接口、持久来源账号/资产共享串行配额与间隔、调用前准入/取消复查、事务外调用、实际费用及完整事实原子保存。来源无关的单次执行基础，不附真实供应商适配器，不自动挂载生产。
- 未知结果/费用、无效事实、撤权返回、超账单/过期/存储失败保留预留并转待对账；暂停同scope派发和新任务准入，不自动重试扣费。崩溃后不重发旧来源请求；事实已提交时可修复调度账本。在途取消仍记实际费用，异常文本不进入事件/调度账本。
- 完整隔离回归1191 passed/444 subtests/0 skipped；最终账本/冷却恢复专项15 passed/14 subtests/0 skipped（与全量重叠）。真实客户HTTP下四能力通过合成适配器执行、保存和隔离查询；停机/账号待对账拒绝新任务503。四套本轮PostgreSQL/Redis和测试线程已核验退出。详见docs/data-capability-executor-2026-11-10.md与state/data-capabilities-20261110/executor-manifest.json。
- 无部署/外部调用/生产变化，web.py/serp_page.py未改。真实来源/许可/凭据/预算、实际适配器（含传输/分页展开/安全重试和批量失败范围）、正式服务挂载、平台各域映射读回、真实规模和七天验收仍待完成。


### 本地第八阶段：明确来源的历史曲线适配候选（2026-11-10）

- 按官方文档新增data_capability_serpapi.py：SerpApi google_trends/TIMESERIES参数映射、固定HTTPS/TLS/无跳转、流式16MiB/头部64KiB与libcurl总期限；默认禁用，无凭据/来源批准/账单核对器不得作为实际来源启用。不是Trending Now或关键词量，不宣称已采购或接通。
- 显式日/周标签与UTC epoch交叉核验、原生query_index映射、独立响应尺度、partial及缺失查询保留成功值；源指数0不标true_zero，<1为阈值缺失。请求创建时间不冒充独立源观测时间；原文按显式许可保留/脱敏，其他格式或含糊时间不猜测。
- 实际费用要求独立核对，校验币种/单位/安全证明引用；执行器兼容扩展先持久保存billing_evidence，再保存事实。未知收费不套用预览上界/免费值；秒/日期Retry-After及付费失败保留账本。当前未验证真实计费，无法解析的失败原文独立spool仍待补。
- 完整隔离1209 passed/480 subtests/0 skipped；最终执行器/适配器/客户HTTP专项33 passed/50 subtests/0 skipped（与全量重叠）。均为合成供应商响应，无真实外部搜索/费用。三套本轮PostgreSQL/Redis和HTTP/worker线程已退出；ego空间76已finish并由SDK交还user-owned，保护用户页面未再关闭。详见docs/data-capability-serpapi-2026-11-10.md与state/data-capabilities-20261110/serpapi-manifest.json。
- 未部署、未改生产数据/开关或web.py/serp_page.py。真实来源许可/Key/费用与预算、真实series/其他三能力适配、正式服务挂载、平台各域读回、真实规模和七天验收仍未完成。现有capabilities仍不宣告已配置。


### 本地第九阶段：失败响应独立保存与交付（2026-11-10）

- 新增显式启用的独立响应仓：来源调用前共享事务配额预留，完整且范围可核验的脱敏JSON在计费/解析之前保存；不可解析或范围不符只记明确未保留原因，不产生规范化观测或订阅。默认原文16MiB/共享128MiB/1000活跃记录/最长24小时；当前来源必须明确准许原响应保留，缺少许可或空间不得先调用。
- 新增受现有双层鉴权保护的 `/v1/jobs/{id}/response` 与 `/response/export`，OpenAPI1.4.0/fields/capabilities同步。支持完整JSON/JSONL/CSV/ZIP/manifest、固定摘要及独立校验；跨租户拒绝，撤权/取消/过期/损坏拒绝交付，导出生成后再复查。已知付费解析失败保留实际账单及响应；未知账单仍待对账、暂停派发，不自动收费重试。
- 完整隔离回归1223 passed / 488 subtests passed / 0 skipped（128.22秒）；执行器/供应商/响应仓专项46 passed / 58 subtests passed / 0 skipped（与全量重叠）。均为合成来源响应，本轮两套独立PostgreSQL/Redis及测试HTTP/worker已停止；未新建浏览器资源。
- 未部署、未调用真实来源、未改生产开关或web.py/serp_page.py。配额仅应用逻辑原文字节，不是物理磁盘保障；自动清理调度、备份恢复后的墓碑重放及网页响应入口未完成。真实许可/Key/预算/账单、其他三能力适配、正式挂载、平台各域读回与规模/七天验收仍待完成。详见docs/data-capability-responses-2026-11-10.md及state/data-capabilities-20261110/response-manifest.json。


### 本地第十阶段：来源响应网页入口（2026-11-10）

- 既有数据任务详情新增主动读取来源响应及固定摘要五格式下载；显示保存/脱敏/未保留、范围/完整性/保留期与原始响应信封，不将响应存在当作解析成功或平台读回。API示例补充响应及固定版本ZIP，沿用双层鉴权占位符；复用既有品牌、组件和移动布局。
- 任务/响应版本和连接绑定防止迟到读取覆盖新任务或迟到下载弹出文件；切换任务/断开连接清空旧数据，撤权/清除下载失败移除旧原文和下载按钮。超长JSON转义、换行并限制滚动区域；不增加来源重试或新分析功能。
- ego lite空间79真实组合页面+候选执行器/客户HTTP/独立响应仓合成验证：付费解析失败、部分成功、未知账单；五种浏览器下载逐字节一致且独立ZIP校验通过；212931字符与恶意HTML仅作文本；任务/连接切换、跨租户空记录、撤权、禁用仓、清除；390/320px无溢出、按钮超过44px。浏览器无脚本错误/待请求，finish确认整个任务空间已关闭；本轮仅合成来源，无生产和外部调用。
- 完整隔离回归1223 passed / 488 subtests passed / 0 skipped（132.58秒）；页面组合/JS与曲线专项3 passed；鉴权示例bash语法检查通过。本轮独立PostgreSQL/Redis及UI HTTP/worker/PostgreSQL已核验停止；证据在response-ui和response-ui-regression。
- 候选未部署，web.py/serp_page.py未改，未恢复生产采集/正文/上传/监测。真实来源许可/Key/预算/账单、其他三能力适配、正式挂载、平台各域读回、真实规模/七天验收，以及备份恢复墓碑重放、物理磁盘和自动保留调度仍待完成；不以网页合成验证替代整体验收。详见docs/data-capability-response-ui-2026-11-10.md与state/data-capabilities-20261110/response-ui-manifest.json。


### 本地第十一阶段：独立 AI Mode 来源候选与完整回答（2026-11-10）

- 新增默认禁用的SerpApi google_ai_mode单轮适配器，限定US/GB、en、桌面/手机；不把AI Mode当AIO。完整回答按源结构原序连接并保留原块，provider Markdown仅作辅助；原生引用index精确关联，普通链接独立，模型/独立源时间未知，不猜测拒答。未知结构或未展开内容标partial；缺失/错误不生成空成功。
- 复用共享HTTPS/期限/响应仓/实际费用与持久执行器；响应在解析前保存，未知费用隔离且不自动重试。共享脱敏移除短效token及其他字段内重复敏感值，显式错误范围冲突不保存完整原文。客户HTTP、原生子记录分页、五格式固定导出、独立校验、订阅和租户/撤权保护均经合成链路验证。
- 来源最终隔离回归1237 passed / 535 subtests / 0 skipped（142.91秒）；专项60 passed / 105 subtests（15.06秒，与全量重叠）。随后网页组合/JS/曲线3项通过（0.13秒）。长回答阅读区限制420px并支持键盘；220046字符浏览器文本摘要与后端一致，五格式下载逐字节/ZIP独立校验通过。手机长状态文字换行，最终浏览器及清理证据见本阶段manifest。
- 未部署、未真实来源调用/收费或Git提交，web.py/serp_page.py和生产开关未改。真实许可/Key/预算/账单、AIO与关键词量实际适配、正式挂载、各域平台固定版本读回、真实金标/规模/七天验收仍待完成；不以合成样本替代完整需求验收。详见docs/data-capability-serpapi-ai-2026-11-10.md和state/data-capabilities-20261110/ai-mode-manifest.json。


### 本地第十二阶段：分阶段来源调用与响应/费用检查点（2026-11-10）

- 为AIO二次展开补齐基础：最多8段共用原响应16MiB预留，阶段响应先落盘再继续调用，原生顺序/范围/完整性/脱敏状态独立；后段失败、超限或不可保留不覆盖首段。封存后沿用五格式与固定摘要，重启/过期执行可恢复封存，runtime只维护本人租户，不重发来源或产生成功事实。
- 逐次before_call持久记录身份与费用上界，重查lease、账号许可所有权、当前准入/原文许可、取消/停止、剩余预算和共享间隔；重复阶段、前次费用未确认、许可/响应预留被清除均禁止下一次收费调用。最后一段计入后续任务最小间隔，旧执行不清除新账号所有者。
- 逐次confirm_bill持久保存不可覆盖的独立凭据与已确认合计；总额须与全部已进入阶段一致。未知第二段保留首段费用、完整预留与账号隔离；过期待对账后的迟到已确认费用仍可保存，不解锁调用或成功事实。客户progress新增逐次进入、confirmed_call_cost/charges/basis，未知不是0、部分合计不是最终actual_cost；OpenAPI1.5.0/capabilities/fields/示例同步。
- 最终全量隔离回归1257 passed / 558 subtests / 0 skipped（146.48秒）；专项80 passed / 128 subtests（18.38秒，与全量重叠）。本地实际客户HTTP验证两段日志、进度、五格式独立ZIP、未知账单隔离、清除后停止与租户/撤权保护；六套本轮PostgreSQL/Redis及测试HTTP/worker均核验退出，端口关闭，自己的空socket目录已删除；本轮无新浏览器资源。
- 未部署、未真实来源调用/收费或Git提交，web.py/serp_page.py和生产开关未改。AIO供应商初始查询/token绑定/展开/标准化与关键词量实际适配仍待继续，不能把合成多段基础当AIO已接通。真实许可/Key/预算/账单、正式挂载、各域平台读回、真实金标/规模/七天验收仍未完成；物理磁盘、自动TTL调度、备份恢复墓碑重放等限制保留。详见docs/data-capability-source-calls-2026-11-10.md与state/data-capabilities-20261110/source-calls-manifest.json。


### 本地第十三阶段：独立AIO两段来源候选（2026-11-10）

- 新增默认禁用SerpApi AIO候选：原生google查询、严格范围校验、最多一次google_ai_overview短效token展开；不跟随响应URL、不借普通SERP或AI Mode冒充回答。US/GB、en、桌面/手机只是当前候选矩阵，不代表真实地区已验证。
- 每段先保存脱敏响应再确认独立费用；7MiB单次/16MiB共享日志，未知费用保留预留和账号隔离。Retry-After立即持久冷却，部分成功保留剩余等待；预计无法进入窗口不谎称实际过期。展开失败/结构不符保留可验证首段与两段实际费用。
- 共享结构核心保留全文顺序、原生块、显式引用与普通链接，扩展subtitle/expandable/comparison/top_stories及单对象snippet_links；通道/API/parser/范围保持独立。未出现、源错误、展开失败、保守本地窗口过期、未知结构明确区分，不从文字猜拒答。
- 最终隔离回归1271 passed / 598 subtests / 0 skipped（167.66秒）；专项94 passed / 168 subtests（24.76秒，与全量重叠）。两套本轮PostgreSQL/Redis和测试HTTP/worker已停止、端口关闭、自己的空socket目录已删除；源码摘要及清理见state/data-capabilities-20261110/aio-manifest.json。均为合成供应商响应，本地客户HTTP覆盖五格式/独立ZIP、分页、订阅、费用未知隔离、租户与撤权。官方协议浏览器空间88已finish并核验移除。
- 未部署、未真实来源调用/收费或Git提交，web.py/serp_page.py及生产开关未改。关键词量实际适配、真实许可/Key/预算/账单、正式挂载、平台各域固定版本读回、真实金标/规模/七天验收仍未完成；整体目标继续。详见docs/data-capability-serpapi-aio-2026-11-10.md。


### 本地第十四阶段：Google Ads历史关键词量候选（2026-11-10）

- 新增默认禁用Google Ads v26历史量适配器，固定TLS账号端点/单次POST；显式主体、OAuth client、project、customer/manager、权限/用途、地区/语言常量、客户币种及有期限核验记录。无凭据发现/OAuth刷新/自动挂载；scope为操作员已核验配置的承载结构，不能替代真实Google准入证明。
- 发送精确去重词、保留每个原始输入映射；原生text/closeVariants合并为一个组，不平分或造逐词量。近似0不标真实0，null/缺失月份明确区分，原生int64保留十进制精度；广告竞争度、micros与币种独立。不能证明的近似映射或源结构拒绝规范化，脱敏源响应独立保存。
- 均值实际窗口源不回显，statistic_window两边null加原因；不拿请求范围或返回月份子集推定。OpenAPI1.6.0/fields同步严格已知/未知分支，旧已知窗口合同保留；未迁移或重写旧事实。未知窗口/缺失仍partial，缺失源观测时间仍未知。
- 来源每次调用前检查准入/预算/保留/取消、响应先保存再确认独立账单；不默认免费，未知费用隔离且不重试。已知HTTP错误保留实际费用与冷却；响应绑定明确依靠固定TLS端点、精确请求和request-id，不伪造查询回显。源没有逐词partial_failure，本轮不虚构可重试失败子集。
- 最终全量隔离1282 passed / 641 subtests / 0 skipped（153.48秒）；专项105 passed / 211 subtests（28.68秒，与全量重叠）。本地客户HTTP分页、五格式/固定摘要/独立ZIP、订阅、费用未知/限流与租户/撤权通过；均为合成来源和准入，不算100真实关键词。两套独立PostgreSQL/Redis及测试HTTP/worker已停止、端口关闭、自己的空socket目录已删除；ego协议任务空间90已finish并核验不存在。
- 未部署、未外部Ads调用/收费、未取得新凭据或Git提交；web.py/serp_page.py与生产开关未变。四能力现均有本地候选，但真实许可/Key/预算/账单/Ads身份权限、失败子集重试与计费去重、正式挂载、平台各域固定版本读回、真实金标/规模/七天验收仍未完成。详见docs/data-capability-google-ads-2026-11-10.md与state/data-capabilities-20261110/ads-manifest.json。


### 本地第十五阶段：明确失败子集重试与独立计费（2026-11-10）

- 新增GET/POST /v1/jobs/{id}/retry及GET /lineage；只读plan_hash固定父观测/摘要/输入映射，仅已结算、未取消的partial关键词量，明确source_error且无关联记录输入可显式派生。缺失、低量、近似0、已有成功记录/重复词不重发；已返回相同词但映射矛盾时保守拒绝。Google Ads未返回词不自动当失败。
- 子任务重新准入、健康检查、独立预算/费用，父事实/ZIP/账单不改；独立sf_capability_retries保存关系。父观测唯一约束覆盖缓存复用别名，租户锁、子任务、事件、预留和关系同事务提交，失败全回滚；不同key不能对同一父观测另建收费重试，同key/计划/预算重放保持原任务。读取lineage复查父子权限与父事实保留期。
- OpenAPI1.7.0/capabilities/fields/双层鉴权示例同步；最终隔离全量1292 passed / 653 subtests / 0 skipped（157.69秒），专项115 passed / 223 subtests（33.01秒，与全量重叠）。本地客户HTTP验证父导出不变、子费用独立、竞态去重、事务回滚、缓存别名、鉴权/租户/撤权、未知费用/状态/无执行器拒绝；595个OpenAPI本地引用解析及示例bash语法通过。
- 本轮两套PostgreSQL/Redis及测试HTTP/worker已退出，端口关闭、自己的空socket目录已删除；无新浏览器资源、真实来源调用/收费、部署、旧迁移或Git提交，web.py/serp_page.py摘要与前阶段一致。
- 未新增网页重试按钮；整批失败/取消子任务的安全续试、部分月份最小范围、源错误分类/指数退避/最大窗口及真实计费仍待继续。真实来源许可/Key/预算/Ads权限、正式挂载、各域平台固定版本读回、真实金标/规模/七天验收仍未完成，不将本地显式重试当全量可靠性验收。详见docs/data-capability-retry-2026-11-10.md与state/data-capabilities-20261110/retry-manifest.json。

### 本地第十六阶段：失败子集重试网页（2026-11-10）

- 新增data_capability_retry_ui.py，接入现有“数据任务”；仅已结算、未取消的关键词量partial任务可核验明确失败输入，重新预览来源、填新费用上限并确认后显式创建子任务。已有子任务只读找回；lineage显示原输入位置，可返回父任务。不重发成功/缺失/低量输入，不改父事实或账单，API仍1.7.0。
- 状态绑定连接/选中任务/版本，迟到读写不覆盖其他任务；提交中禁用，保留幂等意图，未知写结果移除表单、只读核验。转义关键词/完整计划，API示例只含占位符，手机表格换行，按钮和确认区域至少44px，支持键盘。
- 全量隔离1319 passed / 658 subtests / 0 skipped（162.65秒）；专项115 passed / 223 subtests（32.62秒，与全量重叠）；最终列宽微调后UI/JS/曲线3项通过。ego真实组合页合成验证19项功能、9项服务端持有确认的在途竞态、8项1440/390/320px布局与键盘检查；无未捕获脚本错误，示例bash语法通过。浏览器空间99已finish并核验移除，独立PostgreSQL/Redis及HTTP/worker均停止，端口关闭；他人任务空间未动。
- 未部署、未外部源调用/收费、未改生产开关、未运行旧迁移/提交Git；web.py/serp_page.py摘要不变。不是自动重试或真实计费验收：整批失败/取消子任务续试、部分月份最小范围、真实错误分类/退避/最大窗口、真实许可/Key/预算/Ads权限、正式挂载、平台固定版本读回和真实金标/规模/七天仍待完成。详见docs/data-capability-retry-ui-2026-11-10.md与state/data-capabilities-20261110/retry-ui-manifest.json。

### 本地第十七阶段：Google Ads有界来源重试（2026-11-10）

- 新增data_capability_backoff.py，Ads适配器支持操作员批准的RetryPolicy，默认仍一次且来源禁用；策略存任务admission JSON，客户不可写，不新增表。未批准多次调用时worker不就绪、创建503；旧入队任务也阻断，执行/逐次调用检查冻结策略。
- 仅已绑定完整JSON、只有error对象的429/503且独立账单确定时允许继续；部分/混合/缺失数据、解析/输运未知及费用未知不自动重发。最多8次，指数退避取max(Retry-After,有上限的指数延迟)，总窗口受墙钟+单调时钟/lease/OAuth/资产授权限制，每次phase/原响应/账单独立。累计确定费用加下一次上限不超预留；即便次数/预算/窗口终止，仍保存共享冷却。取消/停止/策略变化中止等待后续调用，未知账单隔离且重启不重放。
- API1.8.0：preview当前策略，job/progress冻结source_retry_policy或null，原逐次费用/响应仓和完整导出保持。598个OpenAPI引用解析与示例bash语法通过；无浏览器资源、外部调用/收费、生产部署/开关变更、旧迁移或Git提交，web.py/serp_page.py保持保护摘要。
- 本阶段只覆盖Ads整批临时错误；其他来源重试、部分月份最小范围、失败/取消子任务续试、等待状态进程恢复、真实费用/许可/Ads权限、正式挂载、平台读回和真实金标/规模/七天仍未完成。详见docs/data-capability-backoff-2026-11-10.md与state/data-capabilities-20261110/backoff-manifest.json；测试/清理最终记录由本阶段后续条目给出。

最终隔离回归：1337 passed / 679 subtests / 0 skipped（175.88秒）；专项127 passed / 243 subtests（41.68秒，与全量重叠）。全量另覆盖新增累计剩余预算负例。598个OpenAPI本地引用、只读/不可由客户启用合同与示例bash语法通过。本轮六套PostgreSQL/Redis及测试HTTP/worker均已停止、端口关闭；自己的空socket与合成PG数据目录已清理，测试代码与日志保留。未创建浏览器资源、未部署或外部调用/收费，受保护的web.py/serp_page.py摘要未变。最终证据state/data-capabilities-20261110/backoff-manifest.json。

### 本地第十八阶段：SerpApi分阶段来源重试（2026-11-10）

- Trends/AI Mode显式策略使用query/query_retry_N逐次门禁与journal；AIO只重试失败search/expand，展开重试不重发成功搜索。SourceRetrySession跨阶段共享总调用数(≤8)、墙钟/单调时钟窗口、累计确定费用与原预留，成功阶段只重置连续失败退避。59秒token截止不延长；剩余次数/预算/窗口不足时保留已有inline部分回答。
- 仅完整HTTP429/503、非空字符串error且无成功数据可重试；metadata若有必须Error，参数echo需一致。Processing/未知/Success矛盾、混合/partial/缺失数据、解析/输运未知或账单未知不自动再发。逐次实际费用和原响应独立；未知后次费用保留预留并隔离；已知前次费用在后续门禁拒绝时不归0、不误报未知。达到限制后仍保存共享冷却，token/凭据不进原文/phase/账单ref。
- OpenAPI1.9.0：冻结策略、原进度/完整导出/订阅保持；null是无额外重试许可，AIO既有正常两阶段仍可用。598个本地引用与示例bash语法通过。无新表/旧迁移、浏览器资源、外部调用/收费、生产发布/开关变更或Git提交，保护文件不覆盖。
- 真源准入/Key/预算/Ads权限/实际账单、真实series/关键词/回答金标、正式挂载、平台各域读回/规模/七天仍未完成；死进程等待恢复、失败/取消子任务续试、部分月份最小范围、完整清除/备份恢复继续登记。见docs/data-capability-native-backoff-2026-11-10.md与state/data-capabilities-20261110/native-backoff-manifest.json，最终测试/清理见后续条目。

最终隔离回归1358 passed / 698 subtests / 0 skipped（186.99秒）；专项145 passed / 263 subtests（58.59秒，与全量重叠）。598个OpenAPI本地引用和两层鉴权示例bash语法通过。均为合成来源与账单，不算真实金标；两套本轮PostgreSQL/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket与合成PG数据目录已清理。没有新浏览器资源、生产部署、真实调用/收费、旧迁移或Git提交；web.py/serp_page.py保持原保护摘要。源码冻结、测试及清理记录见state/data-capabilities-20261110/native-backoff-manifest.json。

### 本地第十九阶段：响应仓清除与备份墓碑（2026-11-10）

- 新 `data_capability_retention.py`：独立 `sf_capability_response_erasures` 保存首个不可变响应墓碑，无外键允许先于恢复行重放。清除/到期同事务删除 payload/metadata/snapshot_hash、归零容量；初始化补录已 erased/expired 行。begin/get 拒绝有墓碑的响应，现有迟到 save/checkpoint/后次来源门禁保持拒绝。任务、事实、预算和来源账本未改写。
- 可信 `export/replay/tick`，不新增客户端删除或导入接口，不改 OpenAPI1.9.0。HMAC-SHA256、独立≥32字节密钥、严格完整结构/唯一身份，身份冲突整批回滚，幂等恢复；完整上限100000条/32MiB，超限失败不截断。tick写0600临时文件、fsync/原子替换/目录fsync后才提交清理，文件失败回滚。清理/重放只取身份/状态，不批量载入大原文。
- 仅 `source_response_only` 本地候选：事实、任务参数、订阅/重试、平台及外部文件未清除；未启用自动timer/恢复启动门禁，未外部保管检查点。恢复必须先取独立保管的最新完整检查点并停服重放；旧有效签名不证明新鲜性，逻辑清除不等于PG/WAL/磁盘擦除。真源许可/Key/费用/金标、正式挂载、Whale各域读回/规模/七天仍未完成。无生产删除/部署、旧迁移、外部调用/收费、浏览器资源或Git提交，保护文件不覆盖。详见docs/data-capability-retention-2026-11-10.md，最终测试/清理记录见后续条目。

第十九阶段最终隔离回归1364 passed / 698 subtests / 0 skipped / 0 failed（184.44秒）；专项24 passed / 12 subtests（4.00秒，与全量重叠）。三套本轮PG/Redis及测试HTTP/worker已停止、端口关闭，自己的空socket及合成PG目录已清理，日志保留。无浏览器资源、生产删除/部署、外部调用/收费或Git提交；web.py/serp_page.py保护摘要未变。最终源码、测试和清理记录state/data-capabilities-20261110/retention-manifest.json，完整GOV-04及真实验收仍未通过。

### 本地第二十阶段：事实清除与导出发送前检查（2026-11-10）

- 新 `data_capability_fact_retention.py`，独立 `sf_capability_fact_erasures`，可信逐观测清除与1–1000条TTL批次；清空观测payload/parameters/revision、事件原文和全部子投影，保留ID/摘要/关系/时间。已安装响应仓的关联副本及缓存别名响应同事务清除并记录响应墓碑。预算、实际费用、状态和来源账本不改写。
- 详情/子记录/投影重建/缓存复用/既有事件再次完成/订阅重领与ACK拒绝墓碑（410，跨租户404）；列表、类型根记录、订阅待交付计数和历史排除。事件锁与行锁串行化完成/清除，读取共享锁，投影修复先取得更新锁；并发重复只记一次，失败回滚。无payload批量加载。
- 任务、观测、订阅三条导出路径生成后复查当前权利、墓碑和固定版本，生成中清除返回结构化错误而不发送文件；真实本地双层鉴权HTTP覆盖五格式，未放宽60次/分钟限流。OpenAPI1.9.0错误/交付语义、capabilities和API文档同步，事实字段/摘要规则不变。
- 仍非完整GOV-04：任务参数/重试计划/订阅筛选等未物理清除，事实墓碑无独立检查点/备份重放；发送前检查与socket发送非原子，严格传输屏障未完成；在途未关联任务、许可整域撤权清除、平台/外部文件及恢复门禁未完成。真源许可/费用/金标、正式挂载/平台各域读回/规模/七天继续缺失。无生产删除/部署、旧迁移、外部调用/收费、浏览器资源或Git提交，保护文件不覆盖。详见docs/data-capability-fact-retention-2026-11-10.md，最终测试和清理见后续记录。

第二十阶段最终隔离回归1374 passed / 720 subtests / 0 skipped / 0 failed（192.76秒）；专项68 passed / 60 subtests（13.46秒，与全量重叠）。五套本轮PG/Redis及测试HTTP/worker已停止、端口关闭，自己的空socket和合成PG目录已删除；无浏览器资源，保护文件摘要未变。598个OpenAPI引用、清除合同和既有鉴权示例语法通过；历史第十八阶段OpenAPI原摘要保持，当前合同独立保存。未生产删除/部署、外部收费或Git提交；最终证据state/data-capabilities-20261110/fact-retention-manifest.json，真实全需求仍未完成。

### 本地第二十一阶段：联合墓碑签名检查点与恢复互斥（2026-11-10）

- 新 `data_capability_checkpoint.py`，可信DataErasureCheckpoint显式初始化独立重放审计表。事实/响应墓碑单一完整检查点，外/内层HMAC-SHA256、≥32字节独立密钥、唯一checkpoint_id及生成时间；响应仓已安装不得静默省略，无仓明确null。原响应单仓合同兼容，共用响应重放及原子0600/fsync发布器。
- replay/replay_file强制独立固定SHA256，完整身份/范围/签名校验；文件有界读取，核对原字节，拒绝重复JSON键/非有限值/损坏UTF-8。联合事务先恢复更早响应墓碑，再清事实/投影/关联响应；缺失原行仍留墓碑，同事件换ID拒绝。重放回执绑定ID/hash但不跳过后来复活内容，冲突整批回滚，费用/状态不变。
- 恢复独占锁30，complete/reuse取得任务行锁前先共享锁30，解锁后再检查墓碑；pg_locks实测等待与锁顺序。事实≤100000条、响应沿用≤100000/32MiB、外层正文≤64MiB，受影响租户事件ID/原文服务端流式扫描≤1000000行，不读payload或装pgcrypto，超限整笔失败。恢复须停候选网页/worker，不是在线无停机方案。
- OpenAPI1.9.0无新客户恢复/删除接口，capabilities更新恢复前提，事实字段与现有鉴权/来源调用不变。独立最新hash外部保管、自动备份/启动门禁、任务/重试/筛选物理清除、严格发送屏障、整域撤权/在途清理、平台及外部文件仍未完成；真源许可/费用/金标、正式挂载/读回/规模/七天继续缺失。无生产恢复/删除/部署、旧迁移、外部调用/收费、浏览器资源或Git提交。详见docs/data-capability-checkpoint-2026-11-10.md，最终测试/清理见后续记录。

第二十一阶段最终隔离回归1385 passed / 720 subtests / 0 skipped / 0 failed（195.37秒）；专项84 passed / 60 subtests（14.38秒，与全量重叠）。598个OpenAPI引用、独立固定摘要要求、无客户恢复端点及既有鉴权/字段/示例语法通过。三套本轮PG/Redis及测试HTTP/worker已停止、端口关闭，自己的空socket和合成PG目录已删除，日志保留；保护文件摘要未变。无浏览器资源、生产恢复/删除/部署、真实调用/收费或Git提交；最终源码、测试和清理记录state/data-capabilities-20261110/checkpoint-manifest.json。最新独立检查点保管/启动门禁及其他GOV-04、真源/读回/规模/七天仍未验收。

### 本地第二十二阶段：任务查询内容与重试级联清除（2026-11-10）

- 新 `data_capability_job_retention.py` 与独立job_erasure墓碑。可信闭包含共享观测别名及向下重试，≤1000任务/观测、查询有界、单事务回滚；清任务parameters、明文幂等键、对账自由文本、事件detail、retry plan及事实/响应，保留ID/摘要/账目/已结算终态。queued取消actual0释放预留；running/对账仅stop，未知费用仍hold，已知费用可返回后正常取消结算。后续事件/对账抑制自由文本。
- 原幂等键和标记键不能重建旧任务。详情/进度/事实/关联/重试/响应/取消/导出410、跨租户404，列表排除；底层费用账本可安全读取空参数。创建/重试先共享恢复锁再租户/行锁，与清除/重放互斥一致。来源撤权但未清除的拥有者仍可取消支出。
- 联合检查点v2增加任务墓碑（jobID/tenant/requestHash/keyDigest/time/reason），联合响应→任务→事实原子恢复，任务不存在先留标记；原键换ID/请求摘要/身份冲突回滚。有界任务元数据扫描、不读parameters；v1仍只事实/响应，任务计数存独立附表不改旧回执列。主OpenAPI1.9.0/fields/capabilities和606引用同步，无新客户删除/恢复接口。
- 仍缺订阅名称/筛选等全部元数据清除、许可整域撤权/未关联任务扫描、严格发送屏障、独立最新检查点自动保管/启动门禁、平台/外部文件以及真实许可/费用/金标/正式挂载/读回/规模/七天。无生产清除/恢复/部署、旧迁移、外部调用/收费、浏览器资源或Git提交；保护文件不覆盖。详见docs/data-capability-job-erasure-2026-11-10.md，最终测试/清理见后续记录。

第二十二阶段最终隔离回归1396 passed / 720 subtests / 0 skipped / 0 failed（203.84秒）；专项131 passed / 98 subtests（23.15秒，与全量重叠）。606个OpenAPI引用、字段/错误/鉴权合同与既有示例语法通过。三套本轮PG/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket和合成PG目录已删除，日志保留；保护文件摘要不变。未创建浏览器资源，未生产删除/恢复/部署、外部调用/收费或Git提交。最终源码、测试和清理记录state/data-capabilities-20261110/job-erasure-manifest.json；GOV-04及真源/读回/规模/七天仍未整体验收。


### 本地第二十三阶段：恢复启动门禁（2026-11-10）

- 增加逐服务实例显式绑定的RestoreGate：进程默认关闭，只有本进程完成独立固定ID/SHA256匹配的v2联合重放并提交才放行。旧回执不自行放行，错误签名/文件/事务保持关闭；并发旧重放不能覆盖较新关闭，回执或任务附表丢失锁闭且需重新重放。
- 新数据域HTTP/订阅/创建/读取/导出及worker启动/领取/付费阶段接入503 restore_required；旧搜索路由与双层鉴权保持。在途请求不可撤回，已知费用进入对账而不发布事实，未知费用继续保留；内部账目/恢复端口可用。未绑定实例保持原行为，必须每个进程启动前绑定，尚未正式装配部署。
- 独立最新检查点自动保管、订阅元数据统一清除、严格发送屏障、整域撤权、平台副本及真实许可/费用/金标/正式读回/规模/七天仍未完成；不得把本轮称GOV-04全验收。详见docs/data-capability-restore-gate-2026-11-10.md，测试/清理见本阶段最终记录。无生产恢复/删除/部署、外部调用/收费或Git提交。

第二十三阶段最终隔离回归 **1406 passed / 730 subtests / 0 skipped / 0 failed**（210.33秒）；专项 **141 passed / 108 subtests**（24.26秒，与全量重叠）。609个OpenAPI引用、503恢复状态/字段/双层鉴权合同通过。两套本轮PG/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket与合成PG目录清理，日志保留；保护文件摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终源码与清理证据 `state/data-capabilities-20261110/restore-gate-manifest.json`；GOV-04和真实全需求仍未完整验收。


### 本地第二十四阶段：订阅元数据清除（2026-11-10）

新增可信SubscriptionRetention，单事务清订阅名称/筛选/明文幂等键/批次/seen，暂停并保留独立事实与账目；首次墓碑不可变、并发幂等、失败回滚。原键/标记键不能重建，所有拥有者读取/控制/ACK/导出410，外租户404，列表排除。主OpenAPI/fields/capabilities说明范围；无新客户删除端点。

订阅墓碑尚未纳入联合检查点，存在墓碑时v1/v2导出/重放明确409 scope拒绝，不冒充完整恢复；旧备份丢失墓碑仍待外部最新证明和下一版联合实现。正式挂载、独立保管、严格发送屏障、整域撤权、平台/真实来源/费用/金标/规模/七天继续未完成。详见docs/data-capability-subscription-erasure-2026-11-10.md，最终测试/清理见本阶段后续记录；未生产删除/恢复/部署、外部调用/收费或Git提交。

第二十四阶段最终隔离回归 **1412 passed / 742 subtests / 0 skipped / 0 failed**（219.30秒）；专项 **147 passed / 120 subtests**（25.83秒，与全量重叠）。618个OpenAPI引用与订阅清除/恢复范围合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产删除/恢复/部署、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/subscription-erasure-manifest.json`；订阅联合恢复及全需求仍未完成。


### 本地第二十五阶段：订阅墓碑联合恢复v3（2026-11-10）

联合检查点v3完整含订阅墓碑，与响应/任务/事实单事务重放；先落标记、再有界元数据扫描，原键换ID或租户/请求/键身份冲突整批回滚。清订阅内容/批次/seen，缺失原行预标记，重复恢复不跳过复活内容。新增独立订阅计数回执，不改旧表列；v1/v2保留原范围兼容，存在订阅墓碑时拒绝旧范围，不能冒充订阅恢复。

逐进程门禁改为要求独立固定ID/hash的v3新重放，校验订阅计数附表，旧v2不能放行，丢失附表重新锁闭。主OpenAPI1.9.0、fields/capabilities更新。详见docs/data-capability-subscription-checkpoint-2026-11-10.md。独立最新证明自动保管/正式装配、严格发送屏障、整域撤权、平台和真实来源/费用/金标/读回/规模/七天继续未完成；无生产恢复/删除/部署、外部调用/收费或Git提交。测试和清理见本阶段最终记录。

第二十五阶段最终隔离回归 **1419 passed / 749 subtests / 0 skipped / 0 failed**（208.01秒）；专项 **154 passed / 127 subtests**（28.32秒，与全量重叠）。618个OpenAPI引用与v3范围/恢复门禁/双层鉴权合同通过。三套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/subscription-checkpoint-manifest.json`；GOV-04及真实全需求仍未完整验收。


### 本地第二十六阶段：新域显式装配与启停（2026-11-10）

新增CapabilityDeployment，统一service/grant/响应仓/执行器/runtime/v3门禁，拒绝错配/重复/占用。无导入或构造副作用，显式初始化→固定文件恢复→全部worker启动→既有鉴权server挂载；统一admission阻止部分启动期间领取，失败关闭并停止已启动worker。API也需running，重启恢复后尚未start不提前开放。

停止先关准入再有界等待，保留挂载返回503，实际线程未退出不冒充停止、不允许重放/初始化/解绑；费用继续独立对账。仅解绑自己的挂载，不覆盖他人，重启须重新恢复。主OpenAPI1.9.0/capabilities更新，无新客户运维接口。详见docs/data-capability-deployment-2026-11-10.md。

同进程候选尚未接正式serve/systemd，分进程监督/真实来源许可/Key/费用/金标/Whale读回/独立保管/严格发送/整域撤权/规模/七天仍待；无生产部署/恢复/删除、外部调用/收费或Git提交。测试和清理见本阶段最终记录。

第二十六阶段最终隔离回归 **1427 passed / 753 subtests / 0 skipped / 0 failed**（206.57秒）；专项 **162 passed / 131 subtests**（31.61秒，与全量重叠）。618个OpenAPI引用、显式装配目录/无客户运维端点/双层鉴权合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产部署/恢复/删除、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/deployment-manifest.json`；真实接通与全需求仍未完成。


### 本地第二十七阶段：原生适配器与当前审批绑定（2026-11-10）

新增NativeSourceRoute/NativeSources，精确绑定SerpApi Trends/AIO/AI Mode及Google Ads搜索量到同一部署/响应仓/API。每域一明确来源，无自动fallback/凭据发现/预算创建。每次重读租户审批JSON，校验许可/币种/计费单位/用途/原文保留/重试策略/时间和原生validator，拒绝错配/撤权及可执行配置；账单回调签名预检，不执行账单查询。

任务及观测内部admission保留规范审批UUID引用和核验时间，公共合同校验成对/UUID/非未来；无凭据或完整审批文件持久化。主OpenAPI1.9.0/capabilities/fields同步，无新客户审批或密钥端点。详见docs/data-capability-native-bindings-2026-11-10.md。当前仍仅合成原生链路，组件不代替外部真实许可核验；真实来源/费用/金标、正式挂载/Whale读回/独立保管/撤权清理/严格发送/规模/七天未完成。未生产部署/删除/恢复、外部调用/收费或Git提交，最终测试和清理见本阶段后续记录。

第二十七阶段最终隔离回归 **1432 passed / 774 subtests / 0 skipped / 0 failed**（210.59秒）；专项 **172 passed / 175 subtests**（32.67秒，与全量重叠）。618个OpenAPI引用、原生绑定/审批引用/双层鉴权合同通过。四套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产部署/恢复/删除、真实请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/native-bindings-manifest.json`；真实接通和全需求仍未完成。


### 本地第二十八阶段：四域平台候选与固定读回oracle（2026-11-10）

新增data_capability_delivery.py，完整观测候选保留四域父子/引用/顺序/修订/partial/raw状态，复用完整导出投影oracle核数量；tenant/source/observation固定身份，缓存别名不制造新事件。授权prepare与读回后重新检查当前权限/TTL/墓碑/门禁。非完整任务/账目/journal审计包，不补未保留原文。

opt-in Consumer客户端复用既有document/version协议，流式限字节/超时/严格JSON，固定platform_payload与本地独立候选全量核对；当前metadata不能替代固定正文。readable、roundtrip与正式schema/终态分账，后两者明确false，不上传、不注册、不升格回执。主OpenAPI1.9.0/capabilities同步，旧读回客户端不改。详见docs/data-capability-platform-candidate-2026-11-10.md。

仍为合成平台及本地隔离验证，Whale正式schema/权限/完整审计交付/真实上传终态读回、源许可/费用/金标、生产装配、独立保管、撤权/严格发送、规模与七天继续缺失；无生产写入/部署/恢复/删除、外部请求/收费或Git提交，最终测试/清理见本阶段后续记录。

第二十八阶段最终隔离回归 **1441 passed / 805 subtests / 0 skipped / 0 failed**（323.18秒）；专项 **200 passed / 246 subtests**（32.89秒，与全量重叠）。618个OpenAPI引用与候选未接通/不升格回执/既有鉴权合同通过。本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产写入/部署/恢复/删除、真实来源或Consumer请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/delivery-manifest.json`；Whale正式交付及真实全需求仍未完成。

## 订阅页面交互优化（2026-11-10）

- 已上线订阅历史/详情/领取/下载的选择版本保护，迟到响应不覆盖新选择，旧批次下载不误启用当前确认；真实SHA256元数据换行修复，320/390px真实详情无横向溢出。仅部署subscription_ui.py和static/index.html，网站后台web重启，采集五容器和四个保护源码不变。
- 隔离相关回归30 passed，末次UI/客户端5 passed（重叠）；7种乱序JS场景，旧版本复现失败。正式暂停订阅历史重下载JSONL摘要不变，未领取/确认新批次，无新搜索。原始39962/pages332768/投影39934/outbox9及所有暂停开关前后完全一致。
- 两机回退state/website-optimization-20261110，换行补丁前state/website-optimization-20261110-mobile；正式静态SHA256为5ae440e505d0fa5a5046ae43ec6ec9eb7689f7dde8162ba6d344d4fddbd3c724。浏览器130/本地8898/隔离PG与Redis已清理，未提交Git。详见docs/searchflow-subscription-ui-optimization-2026-11-10.md。

## 最新授权：AI 内容夜间持续采集（2026-11-10）

- 用户授权持续采集AI内容至2026-11-11新加坡09:00（UTC01:00）。已新建并启动独立scheduled任务a0d0ec51-9624-4b47-b73d-e57c02a05d9f，主查询“AI 人工智能最新进展”，20中英文关键词，Web+News/1页/US；不启用全站供给、正文或Whale。网站可查看运行中任务及结果。
- 采集机searchflow-ai-deadline-20261111.timer已active/enabled、Persistent，截止只停止这个CID；脚本后台state/ai-overnight-20261110/stop-task.py，失败30秒重试，不得提前运行真正停止命令。启动快照4请求/3成功/8新URL，非最终统计。到期停止尚待实际发生，勿称完成。详情docs/ai-overnight-collection-2026-11-10.md，ego135保留任务页交用户。

## AI数据Whale小样测试（2026-11-11）

- 用户授权测试，实际Web/News各1条上传，各attempts1、queued，独立处理回执仍queued无persisted_at；Consumer降速复核两文档404、固定版本单项not_found（HTTP200信封不算成功）。不能称上传全流程正常，原因尚未知；未全量上传1385条。
- 两测试job 3d051465-cc04-4c8e-86fb-7f301bc52192 / 5af9cb4b-8a07-4c0c-a723-a1e07f4d5f3f 已paused，uploads/claims最终false，总旧链接outbox现11 queued（原9+本次2），无新搜索。平台已接收不可撤回，后续可能继续异步处理。见docs/ai-whale-upload-test-2026-11-11.md；ego143已清理，无源码发布。
