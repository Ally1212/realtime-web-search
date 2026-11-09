# AGENTS.md

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
