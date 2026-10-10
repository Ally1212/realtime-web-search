# Google 开源链路逐小时实验

> 历史正文实验记录。2026-10-10 用户将目标改为 Google Web SERP 新链接与结构化字段，旧正文 worker 已于 UTC 02:30:24 停止，新 SERP 服务于 UTC 02:36:47 启动。下方“当前”“待启用”等描述属于当时实验状态，不是现行配置。最新需求见 [Google SERP 需求与实现](google-serp-requirements-2026-10-10.md)。旧自动升级已取消，正文/hash 修复未随本轮部署上线；旧小时正文排名不能作为新链接目标的选优结论。

持续目标仍未完成：9组冻结实验全部完成独立3600秒及独立SQL核验；本轮最高为OpenSERP全Google来源3槽12,475篇首次正文，纯Web6槽3,222篇。已自动启动旧版暂优配置连续采集（UTC11:02:08.720572），首完整小时截止UTC12:02:08.720572；正文/hash修复及辅助审计新版仅已上传待启用，实际24小时产量尚未验证。

## 检查所得

2026-10-09 开始检查时，生产 local-worker 已退出，没有 active campaigns；OpenSERP、代理中转和数据库正常。上一轮已结束的 Web/News/Trends 混合来源实验，精确首小时正文 8,587 篇：News 7,918、Web 654、Trends 15。这个数字不能代表 Google 网页搜索正文产能。原始审计已复制到本地 `state/google-hourly-20261009-evidence/prior-mixed-first-hour.json`。

## 已实施的配置

服务器唯一生产目录：`/home/azureuser/projects/realtime-web-search-openserp`。

服务：`realtime-google-hourly.service`，已启用开机启动和失败重启。旧截止 timer 已禁用，避免机器重启后过期任务停止新采集。保留原 OpenSERP 服务，不改 `.env`；每组仅通过 Compose override 调整 local-worker。

顺序如下，每组实际 worker 启动后运行完整 3,600 秒，停止后冻结报告再进入下一组：

| 顺序 | 获取 Google 结果的链路 | 页数上限 | 采集槽 | 来源 |
|---|---|---:|---:|---|
|1|curl-cffi|1|3|Google Web|
|2|Playwright Chromium|1|3|Google Web|
|3|WML，经 curl-cffi|1|3|Google Web|
|4|保留的 OpenSERP|1|3|Google Web|
|5|保留的 OpenSERP|1|6|Google Web|
|6|保留的 OpenSERP|11|3|Google Web|
|7|保留的 OpenSERP|1|3|Google Web + News + Trends + RSS|
|8|Playwright，导航超时与证据修订版 v3|1|3|Google Web|
|9|OpenSERP，正文浏览器回退初始化与关闭修订版 v1|1|3|Google Web|

RSS 只指定 Google News RSS，没有其他搜索引擎或付费搜索 API。所有组固定使用私有代理池；旧的公共代理回退仅在本轮代码快照中关闭。Google 初始 RPS 1、最大 2、每代理至少 90 秒间隔，保留验证码及失败冷却。关键词来自相同的 120,000 条英文主题词库，每组使用不同的确定性起点，避免直接重复前组查询。

## 审计与选择

- 搜索：按固定窗口内完成并写入审计表的实际 SERP 调用计数，缓存复用不增加搜索次数；另报有结果搜索和 CAPTCHA/全部调用比例，JavaScript 验证单独归类。
- 链接：新增 `result_urls` 保存真实结果 URL，规范化后跨请求去重，排除启动前已知链接。初始历史基线涵盖现存正文、抓取事件、发现缓存和已记录结果，共 619,526 条规范化 URL。历史记录中已经丢失的 URL 无法证明是新链接。有效链接表示 Google 返回并通过公共 URL 检查的候选，不代表正文已抓取成功。
- 正文：生产抓取质量与相关性检查通过、至少 500 字符、全库 URL/正文 hash 唯一、启动前页面 ID 排除；正文抓取与首次关联均须在同一固定窗口内。混合来源报告保留互不重复的来源组合。
- worker 中途停止、重启、替换或链接审计缺失，该小时不能参与选优，会另外重新运行完整小时，最多重试 3 组；不会把短窗口折算成实测小时。
- 全部配置完成后，以去重合格新正文数选择生产配置，同时单独保存纯 Google Web 最优组。启动所选配置持续采集，并报告滚动小时指标和异常，保留 OpenSERP 锁响应检查。选择只是本轮样本的暂定结果，后续还要验证连续完整小时产能与稳定性。

顺序实验仍受时间、代理健康和不同关键词切片影响，不能把差异全部归因于链路。暂未证明系统上限。

## 当前实测与验证

首组 worker 于 **2026-10-09 09:54:19.191465 新加坡时间**启动，首小时截止 **10:54:19.191465**。截至 09:56:48.836622，运行 149.645 秒：36 次 Google 调用、有结果 0、新链接 0、CAPTCHA 0、合格正文 0；36 次均为 `google_javascript_required`。仍在运行，未将此短窗口当作最终结果。

启动检查修正了服务器 Python 3.10 对 Docker 纳秒时间戳的兼容问题，另验证了待运行 RSS 组的真实配置格式。监督器版本调整有 hash 修订记录；worker 启动时间保持不变、RestartCount=0，采集配置与代码快照未变。

验证：66 项本地相关测试通过；服务器 PostgreSQL 事务验证 URL 规范化、去重和首次发现记录，夹具已回滚；7 组真实 Config 加载检查通过，Chromium 可执行文件存在。服务 MainPID、worker PID、分钟心跳和数据库搜索记录均已核验。

权威状态文件：

- `state/google-hourly-20261009/series.json`：冻结计划、运行镜像与代码 SHA256。
- 同目录 `heartbeat.json`、`comparison.json`、`selection.json`：当前运行、逐组完成结果、暂定选优结果；后两者在相应阶段才生成。
- 每组 `state/google-hourly-20261009-<配置>/manifest.json`、`hourly-report.json`、`audited-final.json`：固定窗口、分钟统计、结束审计。
- 结束审计不存在时，该组尚未完成；状态文件不能替代 systemd MainPID、Docker 运行态和数据库的新记录。

下一次先核验上述实时状态及首组完整小时结果，再推进优化；目标保持 active。

## 10-09 后续核验：正文首次入库审计与连续小时归档

上一目标轮属于实际进展：已部署并启动独立实验。本轮再次确认监督器 MainPID、worker PID 和 Docker 原启动时间，worker 未停止、未重启。首组 10:07:03 的分钟报告：运行 764.179 秒，78 次 Google 调用，全部 JavaScript 验证；有结果搜索、新链接、CAPTCHA、合格正文均为 0，仍未完成首小时。

旧混合来源首小时冻结报告为 8,587 篇、470 次搜索。用旧统计逻辑再次查询 PostgreSQL，搜索仍为 470，正文变成 8,566。进一步检查发现，该小时首次关联的页面中，23 个当前合格页面的 `fetched_at` 已被后续刷新改到小时外；可变正文和来源也不能证明当初的状态。保留原冻结报告，不把本次重算值冒充当时实测，也不回填无法恢复的历史首次正文。

已部署 [首次入库审计迁移](../migrations/20261009_body_admission_audit.sql)：仅在新页面 INSERT 时记录原始正文长度、内容 hash、来源、抓取时间、语言与 HTTP 状态。Google 抓取代码、配置和 worker 均未调整。当前首组先前没有新页面，新增审计核验 `missing_body_admission_rows=0`，因此不存在本组正文记录漏采。

小时正文统计现在使用首次 INSERT 元数据，并先计算整个运行的首次页面关联，再筛选小时；同一页面后续刷新或重复关联不能改变首次小时的正文和来源，也不能计入第二小时。缺失原始正文证据的小时不参与选优。监督器及统计器的修订 hash 和旧源码留在状态目录中，采集代码快照未改动。

已部署 [完整小时归档程序](../scripts/archive_google_hours.py) 和 `realtime-google-hourly-archive.timer`，每 5 分钟用强制只读 PostgreSQL 会话运行。程序保存已完成实验，选优后以 worker 启动时间为基准冻结完整小时指标，留出 60 秒事务提交等待；保留实际小时数、有效小时数、连续非零正文小时数，以及 24 个连续有效完整小时的实际产量。未满 24 小时不会产生“实测日产量”，中断或缺失小时会阻断连续性；迟到的中断证据会撤销有效性而保留已归档计数。

验证：本轮 13 项相关单元测试通过；服务器回滚事务模拟正文后续缩短、来源变更和第二小时再次关联，原始小时仍计 1 篇 Google Web 正文，第二小时计 0，夹具已回滚。归档服务 `Result=success`、只读状态已验证，当前完整小时数为 0。原始复核留在 `state/google-hourly-20261009/hourly-archive/legacy-replay-check.json`，归档汇总在同目录 `summary.json`。

低产量期间仍保留失败代理退避。现场查询确认 Google Web/各 provider 没有全局熔断，78 个 Javascript 验证失败的代理会话正在分批冷却；没有清空健康记录或提前解除冷却来增加尝试数。待本组满小时后，再查看下一组实际产能及运行初始代理状态。

## 下一组浏览器运行准备验证

10-09 10:18 左右，服务器 local-worker 容器按真实 `DISPLAY` 环境启动有界面 Chromium，以离线 HTML 验证 `a:has(h3)` 节点读取，成功后关闭浏览器。耗时 2.394 秒，未导航至 Google、未提交 Google 搜索，也未重启采集 worker。这证明下一组浏览器依赖实际可运行；不能证明 Google 搜索成功率，仍须下一组独立的一小时实测。

本次启动检查时首组仍为 156 次调用、全部 JavaScript 验证、正文 0；保持原配置和失败冷却，首小时截止仍为 10:54:19.191465 新加坡时间。

## 首组完整结果与第二组交接（10-09 11:01 核验）

首组 curl 固定窗口为 UTC **01:54:19.191465–02:54:19.191465**（新加坡 09:54:19–10:54:19），精确 3,600 秒：313 次搜索、有结果搜索 0、有效新链接 0、CAPTCHA 0（0%）、全局去重合格新正文 0。313 次均为 `google_javascript_required`，因此 0% CAPTCHA 不代表没有验证挑战。

冻结报告 `interruptions=[]`、`valid_for_comparison=true`，链接和首次正文审计完整，PostgreSQL 同窗口独立计数一致。已归档至服务器 `hourly-archive`，本地证据为 [curl-final.json](../state/google-hourly-20261009-evidence/curl-final.json)。

监督器自动交接至 Playwright，实际开始 UTC **02:55:06.262793**，截止 **03:55:06.262793**（新加坡 **10:55:06–11:55:06**）。Docker ID `0f08c59ce2e07f273e1002beea023f47112c351e64d896d4bc0ae90fa49d3b7e`，worker PID 1692378，RestartCount=0，仍在运行。仅 Google Web、1 页、3 槽，冻结代码和配置未改。

UTC 03:01:35 的数据库核验已有 27 次 browser 调用，均为 `google_browser_deadline`，耗时 25.073–25.619 秒，HTTP 状态未记录，正文 0。这是子进程总时限触发；没有页面证据，不能据此判为 Google 验证码或确定代理故障。保留原组完整运行，不清空代理健康或提前重启。

随后用原始 `realtime.browser_probe` 和真实 Chromium 进行完全离线路由验证：所有请求在浏览器上下文内以固定 HTML 返回，不访问 Google、不写搜索审计。子进程正常退出，结果为空，耗时 12.603 秒。启动、导航与关闭链路可运行，但不能证明线上请求正常；启动开销是否挤占 25 秒总预算仍须进一步核验。

为区分各阶段开销，第二次离线验证记录阶段耗时：总计 4.880 秒，浏览器 launch 0.422 秒、context 创建及离线路由注册 0.154 秒、goto 0.681 秒、关闭浏览器 0.390 秒，正常退出。未复现线上超时。[离线诊断证据](../state/google-hourly-20261009-evidence/browser-offline-diagnostic.json) 保留完整阶段记录。诊断工具独立放在服务器状态目录，未安装进采集环境，也未修改冻结源码。

UTC 03:07:31 分钟报告为 53 次搜索、正文 0。用 `py-spy dump --nonblocking` 只读采样两个已有 browser_probe 子进程（存活 18.37 秒、12.09 秒），均停在 Playwright `greenlet_main` 的 asyncio/select 事件循环。采样没有读取局部变量或代理凭据；只能确认正在等待驱动，不能定位到导航、解析或关闭阶段。[现有子进程调用栈](../state/google-hourly-20261009-evidence/browser-live-stacks.json) 已保留。监督器 MainPID 1565913、worker PID 1692378、原始 StartedAt 和 RestartCount=0 均再次核验，冻结采集源码 SHA256 无差异。

## 浏览器审计修复与读取页面的超时证据（10-09 11:18）

对已有 Playwright 驱动做有限只读跟踪，捕获 `Page.content` / `Runtime.evaluate` 调用，驱动随后随子进程总时限终止。仅保留方法名及时间，未保存协议原始内容或代理凭据。[驱动方法证据](../state/google-hourly-20261009-evidence/browser-live-driver-methods.json) 说明读取页面可能占用时限，尚不能确定导航是否先前超时，也不能证明 Google 页面属于 CAPTCHA。

代码检查发现一个可复现的独立统计缺口：旧 `browser_probe` 只传结果或错误，不传 `transport.last_evidence`；discovery 的后备分类没有使用明确 CAPTCHA 标志，因此已识别验证码可能被记为普通 `error`。修订版传递页面分类、HTTP 状态、HTML hash/path 与保存选项，父进程只接收明确白名单字段；无页面证据时仍按明确 CAPTCHA 标志分类。

修订版独立冻结在服务器 `state/google-hourly-20261009/revisions/browser-evidence-v2`，状态 `staged_not_activated`，有源码 SHA256 清单。当前七组实验仍使用原采集快照，不混入修订版调用。后续启用该版前须另跑独立完整一小时，不能将离线结果当作 Google 产能证据。

验证：62 项 browser/discovery 相关单元测试通过；服务器在真实 local-worker 的 Chromium 中、从修订目录加载模块，分别用完全离线的空结果与验证码页面验证整个子进程→父进程证据传递。分别耗时 1.525、1.574 秒，HTTP 200 与 `empty`/`captcha` 分类保留，验证码错误及标志均正确，外部请求为 0。[修订版服务器验证](../state/google-hourly-20261009-evidence/browser-evidence-v2-offline-smoke.json) 已保存。

当前小时统计器另已原子升级：CAPTCHA 计数接受审计 `classification=captcha` 或明确 `error_code=google_captcha`，修复旧子进程缺失页面证据导致的漏计；不把 `google_browser_deadline`、JavaScript 验证或 HTTP 429 推测为验证码。14 项小时计量与归档测试通过。服务器保留旧统计器及 SHA256 修订记录 `observer-revisions/captcha-audit-20261009T031853Z.json`，采集快照 SHA256 无变化，无需重启 worker。

UTC 03:16:44 分钟报告已有 80 次调用、正文 0，worker 仍为原始 PID/StartedAt，RestartCount=0。其余组及连续日产量验证仍未完成，目标保持 active。

## 导航超时定位与第八组追加（10-09 11:31）

完整采样一个已存在的浏览器驱动生命周期：[调用序列](../state/google-hourly-20261009-evidence/browser-live-driver-lifecycle.json) 显示 `Page.goto` 于 UTC 03:23:15.254 开始，03:23:35.262 返回 `TimeoutError`（20.008 秒），随后异常处理又发出 `Page.content`，直到子进程约 25 秒被杀仍未收到该调用响应。现有 `google_browser_deadline` 因此可能掩盖导航超时，不能直接解释为 CAPTCHA。

修订版 v3 删除异常后的第二次 DOM 读取，只使用缓存 URL 和已读取 HTML 判断已知 CAPTCHA；保留原导航错误和未知 HTTP 状态，确保未知响应不会被冒认为 HTTP 200。只在最终路径关闭页面一次，保留上一版证据传递及验证码分类修复。服务器冻结目录为 `revisions/browser-navigation-v3`，有独立源码 SHA256 清单。

验证：63 项 browser/discovery 测试通过。服务器真实 Chromium 指向本地挂起 HTTP fixture，禁止超时后调用 `Page.content`，4.582 秒内返回 `google_timeout`，HTTP 状态未知，未触发外层总超时；离线验证码场景 3.235 秒内正常识别。[v3 真实容器验证](../state/google-hourly-20261009-evidence/browser-navigation-v3-offline-smoke.json) 未提交 Google 或其他外部请求。

另在服务器用同一私有 HTTP 出口验证 Chromium 访问 `https://example.com`：直接认证代理与 credential-hiding relay 均 HTTP 200，总计分别 2.442、2.619 秒。没有 Google 查询或代理健康写入。[非 Google 传输验证](../state/google-hourly-20261009-evidence/browser-proxy-non-google-smoke.json) 只能说明这个出口的普通浏览器传输可用，不能证明 Google 连通或整个池正常。

已将 v3 追加为第八组 `browser-v3-p1-s3`，仅 Google Web、1 页、3 槽、关键词起点 105000，在原七组之后独立运行完整 3,600 秒，并参与最终正文量选优。原七组计划、采集快照和正在运行的第二组配置均未变化。监督器新增按组选择修订代码的能力，启用前验证全部源码 SHA256、修订身份及离线验证；17 项小时计量/归档测试通过。

为载入追加计划，仅重启宿主机监督器。新 MainPID 1810756，服务 active/running；交接前后 worker Docker ID、PID 1692378、StartedAt、RestartCount=0 和采集挂载完全一致，原快照 SHA256 无差异，当前 `interruptions=[]`。[交接实证](../state/google-hourly-20261009-evidence/browser-v3-registration-handoff.json) 已保存。UTC 03:31:27 心跳为 143 次搜索、正文 0，第二组截止仍为 UTC 03:55:06.262793（新加坡 11:55:06）。新增实验与持续采集的完整小时验证仍待执行，目标保持 active。

## 第二组完整结果与 WML 实际启动（10-09 11:57 核验）

Playwright 原版固定窗口 UTC **02:55:06.262793–03:55:06.262793**（新加坡 **10:55:06–11:55:06**），精确 3,600 秒。最终 258 次搜索，全部 `google_browser_deadline`；有结果搜索、有效新链接、合格新正文均为 0，识别到的 CAPTCHA 为 0（0%）。没有响应页面证据，不能把 0% 解释为无 Google 验证挑战，也不能将全部 258 次都归因于仅采样到的那一次导航超时。

`interruptions=[]`、`valid_for_comparison=true`，链接与首次正文审计均完整。PostgreSQL 强制只读会话按同一窗口独立查得 258 次、结果链接 0、去重正文 0，耗时范围 25.073–26.376 秒。[完整冻结报告](../state/google-hourly-20261009-evidence/browser-final.json) 与 [数据库复核](../state/google-hourly-20261009-evidence/browser-final-db-check.json) 已保存。只读归档服务 `Result=success`，`hourly-archive/summary.json` 已包含两个完整有效实验，尚未开始选优后的连续阶段。

监督器在截止后停止旧 worker 并自动启动第三组 WML，没有提前换组。WML 实际开始 UTC **03:55:48.452281**，截止 **04:55:48.452281**（新加坡 **11:55:48–12:55:48**）；新 Docker ID `a485d503396bf5ecbe2c2ee26ad49839753ff4b6e6b23205fe92849239303e5d`、PID 1890055、RestartCount=0，运行态 true。

已从真实容器读取 Config：仅 `wml` provider、Google Web 来源、1 页、3 槽、正文门槛 500 字符、初始 RPS 1/最大 2、代理间隔 90 秒、RSS 为空；仍挂载原冻结采集快照，所有源码 SHA256 一致。[WML 启动核验](../state/google-hourly-20261009-evidence/wml-start-check.json) 已保存。

WML 最初 64.379 秒已有 15 次调用，均 `google_http_403`，正文 0。这只是分钟观察，不冒充完整小时，也不因低产出提前停止或清空代理冷却。当前完成 2 组、待完成 6 组，目标保持 active。

## WML 403 与历史证据（10-09 11:59）

强制只读查询当前已审计请求：52 次 WML 调用均 HTTP 403，涉及 52 个不同代理哈希、52 个不同响应 hash，耗时 0.304–2.700 秒；实际请求端点为 `https://www.google.com/wml/search`。不同代理哈希代表配置端点身份，未额外探测其真实出口 IP，不能据此证明 52 个独立出口。

同表保留的历史 WML 审计中，44,953 次为 HTTP 200/有结果，最近一条为 UTC 2026-10-06 21:32:57；另有空结果、超时、验证码和 403。它只证明历史上该链路曾返回结果，不证明这些结果属于当前小时、当前配置或当前私有池，也不回填为本轮产能。[当前与历史审计证据](../state/google-hourly-20261009-evidence/wml-403-history-check.json) 已保存。

已复用 [10-08 的 403 调查](wml-403-research-2026-10-08.md)：当时私有代理也返回 `Your client does not have permission to get URL /wml/search...`，没有严格的同出口/同时间普通 Google 搜索对照，不能据此断言全球关闭或永久封禁。没有重复提交额外诊断 Google 查询，保留当前组配置，等待完整小时，再与随后 OpenSERP 组比较。

## WML 暂无新增调用的健康限制核验（10-09 12:08）

已直接核验 worker PID 1890055、运行态 true、原始 StartedAt 和 RestartCount=0，以及持续更新的分钟心跳。已有 79 次调用、正文 0，不据无新增调用判断进程停止。

PostgreSQL 强制只读会话显示 `google_web` 和 `google_wml` 均为 degraded、`circuit_until=NULL`、来源连续失败计数 0，没有来源全局熔断。Google Web 当前 RPS 0.25、WML 1.0，保留历史自适应速率，不人为重置至初始 RPS。

当前已使用的 79 个代理身份组全部有成员处于持久化冷却；237 条组/别名会话中有 79 条正在冷却，最晚到 UTC **04:31:07.618789**（新加坡 **12:31:07**）。其中 2 个组还包含累计样本不少于 20、成功率低于 20% 的历史别名会话。后续核对实际入口发现当前 crawler 只绑定单端点预约，未绑定组预约，因此这两个旧别名的低成功率不能解释为本次运行的组隔离。累计隔离代码分支无时间窗口判定，仍是后续稳定性优化的待核验项；当前不清空计数或改写预约规则。

[来源与代理健康证据](../state/google-hourly-20261009-evidence/wml-reservation-health-check.json) 已保存。当前继续等待正常冷却和完整小时截止，不重启或强制追加请求。

## WML 正常冷却后恢复调用（10-09 12:27）

UTC 04:26:31 分钟心跳显示调用数从 79 增至 87，正文仍为 0。Docker 仍是原 ID/StartedAt、PID 1890055、RestartCount=0；未重启 worker、未清空代理健康、未解除冷却。用只读 PostgreSQL 保存首批调用末次时间与暂停后首条新调用，以及当前错误分类：[自然恢复审计证据](../state/google-hourly-20261009-evidence/wml-natural-cooldown-resume.json)。恢复调用不能等同于 Google 返回结果或正文产能恢复，完整小时截止仍为 UTC 04:55:48.452281（新加坡 12:55:48）。

### 预约路径解释纠正

已读取服务器实际冻结 `crawler.py`，确认绑定的是 `proxy_reserver=self.store.reserve_google_proxy`，没有 `proxy_group_reserver`；组预约在其他实验入口使用，不能直接套用到本轮生产 crawler。此前将两个旧别名组的累计低成功率视为当前另外隔离条件，解释不够准确，已纠正。实际使用端点的重新核验保存在 [预约路径与端点健康证据](../state/google-hourly-20261009-evidence/wml-actual-reserver-check.json)。当前 79→158 的正常重试与原 worker 的连续运行证据不变，没有据旧别名数据修改采集。

## WML 完整结果与第四组 OpenSERP 启动（10-09 12:57 核验）

WML 固定窗口 UTC **03:55:48.452281–04:55:48.452281**（新加坡 **11:55:48–12:55:48**），精确 3,600 秒。最终 **158 次调用，全部 HTTP 403**；有结果搜索、有效新链接、去重合格新正文均为 0，明确识别 CAPTCHA 为 0（0%）。HTTP 403 不等同于 CAPTCHA，也不能据此断言无 Google 验证挑战。

最终 `interruptions=[]`、`valid_for_comparison=true`，链接与首次正文审计完整。截止前直接核验原 Docker ID、PID 1890055、StartedAt 和 RestartCount=0；截止后监督器按计划停止该容器，Docker FinishedAt 为 UTC 04:55:58.756631，没有提前停机。强制只读 PostgreSQL 会话独立核对调用数、分类、链接去重和首次正文，共 9 项计数全部与冻结报告一致，耗时 0.304–2.701 秒。[冻结报告](../state/google-hourly-20261009-evidence/wml-final.json) SHA256 为 `7c0ba6edf25bde83648e79d384bdcce4cd382081ea07c634bf668a82cf1d3e23`；[独立数据库复核](../state/google-hourly-20261009-evidence/wml-final-db-check.json) 已保存。

第四组 `openserp-p1-s3` 实际 Docker 启动 UTC **04:56:25.238407**，截止 **05:56:25.238407**（新加坡 **12:56:25–13:56:25**）。新容器 ID `9dc6edb80e2301595afd2cfbb1cdcd651750983eb3b40ffe7fd9c10716ca2244`、PID 2051264、运行态 true、RestartCount=0；监督器 MainPID 1810756 仍 active。

已读取真实容器 Config：仅 OpenSERP provider、Google Web 来源、1 页、3 槽、正文至少 500 字符、初始 RPS 1/最大 2、每代理间隔 90 秒、RSS 为空。只读挂载仍为原冻结采集快照，全部源码 SHA256 无差异。[启动核验](../state/google-hourly-20261009-evidence/openserp-start-check.json) 已保存；尚未满小时，未把启动统计当成完整结果。当前已完成 3 组、剩余 5 组，选优及连续产能验证仍未完成。

第四组首份分钟报告截止 UTC **04:57:29.780444**，实际观察 **64.542 秒**：10 次 Google 调用全部有结果、78 条原始结果链接、74 条有效新链接、明确 CAPTCHA 0、合格新正文 0。随后强制只读查询已看到有结果调用与明确 CAPTCHA/普通 HTTP 429 分类并存，不能把首份报告的 100% 成功率外推到整小时。[初期报告与真实运行态](../state/google-hourly-20261009-evidence/openserp-initial-progress.json) 保留该分钟快照；UTC 04:58:22 直接确认原容器仍运行、RestartCount=0。

UTC 04:57:47 已执行既有只读归档服务，`Result=success`、`ExecMainStatus=0`；汇总包含 curl、browser、WML 三个完整有效实验。连续阶段仍为 0 小时，24 小时实测产量尚不存在。

## 第四组正文审计与浏览器回退修复（10-09 13:08）

第四组仍在原容器中运行。UTC **04:59:38.931093** 的分钟窗口（193.693 秒）独立只读数据库复核为：41 次调用、33 次有结果、5 次明确 CAPTCHA、259 条有效新链接、81 篇合格新正文；9 项计数与读取到的分钟报告全部一致。[数据库复核](../state/google-hourly-20261009-evidence/openserp-live-db-check.json) 属于运行中的短窗口，不是完整小时报告。

UTC 05:00:22 的[抓取与正文核验](../state/google-hourly-20261009-evidence/openserp-live-funnel-check.json)：本组已关联 150 个页面，其中 145 个超过启动基线；这些新页面有完整首次入库审计、原始正文最短 600 字符、全部 HTTP 200/英文/Google Web、原始 hash 互异。关联页面当前正文与存储 hash 无不一致。Google Web/OpenSERP 来源当前 RPS 均为 1、无全局熔断；实际使用的 39 个端点中 9 个冷却，0 个命中累计低成功率隔离。该检查只覆盖当时关联页面和现存审计，未补回已丢失的历史 hash 或 URL。

同窗口出现 4 条 `AttributeError` 抓取失败与 4 次正文浏览器回退。生产日志只保留错误类和汇总，没有每条失败的详细栈，因此不能认定所有生产错误都属于同一根因。独立离线 Scrapy 夹具证实原冻结代码存在确定故障：`HybridDownloadHandler.lazy=True` 导致嵌套 Playwright 处理器在 `engine_started` 信号发出后才构造；curl 首次抓取后再请求浏览器回退，触发 `'ScrapyPlaywrightDownloadHandler' object has no attribute 'browser_provider'`。[原版离线复现](../state/google-hourly-20261009-evidence/browser-fallback-original-offline-smoke.json) 保存了实际加载路径与源码 SHA256，没有 Google 或外部请求。

已修改 [hybrid_handler.py](../realtime/hybrid_handler.py)：提前构造处理器，使 Playwright 收到启动信号；关闭改为异步等待各传输的原生 coroutine/Deferred，避免 `maybeDeferred` 驱动 asyncio Future 时的关闭错误。两个资源关闭测试与既有小时监督/归档测试共 19 项通过。

服务器独立冻结修订为 `revisions/openserp-fallback-v1`，相对于原快照只改变 `code/realtime/hybrid_handler.py`，SHA256 为 `109bd61fed33ce9ef3c06ab5d3914b54119aff48b2d0bd7cd5f2d7a09bcfe632`。真实 Scrapy/Chromium 验证三种情况：直接浏览器请求、curl 抓取后回退、关闭浏览器功能后仅 curl；三项均得到预期 HTTP 200/标题，错误与 reactor 关闭错误均为 0，外部请求为 0。验证从 `/tmp` 工作目录显式加载该修订，避免 `/app` 中默认模块遮蔽 `PYTHONPATH`。[全部离线检查](../state/google-hourly-20261009-evidence/openserp-fallback-v1-offline-smoke.json) 与夹具源码均已保存。

已把 `openserp-fallback-v1-p1-s3` 追加为第九组，仅 Google Web、1 页、3 槽，按实际 Docker 启动独立完整运行 3,600 秒并参加正文量选优。原八组不变，当前第四组继续使用原冻结源码。提前初始化的实际产能与资源影响仍须通过新增完整小时实验判断，离线修复不能冒充已经增产。

为加载新计划只重启宿主监督器，新 MainPID **2090843**，active。重载前后当前 worker 的 ID、PID 2051264、StartedAt、RestartCount=0、只读挂载和原采集快照 SHA256 全部不变；截止仍为新加坡 **13:56:25.238407**、`interruptions=[]`。11 项[交接核验](../state/google-hourly-20261009-evidence/fallback-v1-registration-handoff.json) 全部通过。最新分钟窗口截止 UTC **05:08:03.092626**，运行 **697.854 秒**：142 次调用、121 次有结果、990 条有效新链接、10 次明确 CAPTCHA（7.042%）、473 篇去重合格新正文。仍未满小时，不折算日产量、不提前选优。

当前共 9 组，已完成 3 组，剩余 6 组；长期稳定运行与 24 小时产量仍待验证，持续目标保持 active。

## 第四组首 15 分钟固定窗口核验（10-09 13:14）

只读复核固定 UTC **04:56:25.238407–05:11:25.238407**，精确 **900 秒**，并等待至少 60 秒提交余量：[首 15 分钟检查点](../state/google-hourly-20261009-evidence/openserp-first-15m-checkpoint.json)。196 次 Google SERP 调用、161 次有结果（82.143%）、11 次明确 CAPTCHA（5.612%）、24 次普通 HTTP 429、1,320 条有效新链接、614 篇去重合格新正文，来源全部 Google Web；链接及首次正文审计无缺失。该窗口明确标记 `full_hour_experiment=false`，没有纳入完整小时选优、没有折算小时或日量。

上游元数据记录 196 次尝试，196 条调用全部提供 `upstream_attempts=1`，无未知值。这是 OpenSERP 返回的上游审计，不是独立抓包证明；上游缓存状态未提供，不能据此声称已额外证明无缓存。调用分段独立汇总与总计吻合，5 项检查全部通过。

三个连续 5 分钟窗口调用数为 68/52/76，有结果数 54/48/59，明确 CAPTCHA 9/0/2，平均调用耗时 13.564/26.136/16.268 秒。调用耗时有明显波动，目前不足以确定瓶颈，也不因此重置 RPS、代理冷却或提前结束当前组；后续 6 槽完整小时仍按原计划比较。

UTC 05:12 的只读 OpenSERP `/stats/proxy` 响应正常，`request_proxy_url_enabled=true`。该模式由请求指定代理，`configured_count=0`/`healthy_count=0` 不表示当前私有代理池为空或全坏，不能据此重启 OpenSERP。同期 Docker 单次采样：local-worker CPU 69.66%、内存 545.9 MiB；OpenSERP CPU 118.27%、内存 566.2 MiB；web CPU 5.45%、内存 348.4 MiB。CPU 百分比可超过一个核心的 100%，单次样本不能证明长期资源上限或瓶颈。

## 第四组正文内容与 hash 核验（10-09 13:20）

再次确认监督器 MainPID 2090843 active，worker 原 ID/PID 2051264/StartedAt/RestartCount=0 不变。UTC 05:17:40 的分钟报告为 263 次调用、1,867 条有效新链接、940 篇合格新正文、14 次明确 CAPTCHA（5.323%），尚未满小时。

UTC **05:20:00.147031** 强制只读核验本组基线后、首次关联在本窗口内的 **1,077 个新页面**，覆盖 **783 个域名**。首次正文审计缺失 0，原始长度最短 501、中位数 5,779、最长 100,000 字符；原始状态全部 HTTP 200、英文、Google Web。逐页计算当前正文 SHA256，与页面存储 hash 及首次入库 hash 一致，两个不一致计数均为 0。

[内容核验与 8 个均匀样本](../state/google-hourly-20261009-evidence/openserp-live-body-quality-check.json) 已保存并审阅：样本包含研究摘要、贸易服务介绍、技术报道、课程介绍、招聘说明、能源报告、教材及市场报告正文。精确匹配的常见挑战页标题（Access Denied/Just a moment/Robot Check 等）为 0。该标题筛查和少量内容抽样不能证明每个页面的语义质量，不改变生产资格规则，也不把此窗口当作完整小时；仍等待当前组实际截止后冻结最终产量。

## 连续小时中断证据修订（10-09 13:28）

已离线复现旧归档缺口：中断可能从第一小时中途开始，直到第三小时才被监督器发现；只按事件 `at` 加减 60 秒筛选，会把早先受影响的小时漏判。修订不改变采集配置、关键词或正文资格规则。

已部署 [监督器](../scripts/google_hourly_series.py) 与 [归档程序](../scripts/archive_google_hours.py) 的观察逻辑：每次真实 Docker ID/StartedAt/RestartCount 匹配且运行、未暂停、未重启中时，持久化 `last_verified_running_at`；发现中断时记录从上次真实核验至发现/恢复核验的可能受影响区间 `affected_from`/`affected_until`，归档按区间覆盖所有受影响小时，包括撤销此前归档的有效性。旧事件仍使用原边界余量规则。没有小时结束后的真实容器核验时，连续阶段暂缓冻结该小时，不因墙钟时间已经经过就认定连续运行。最终实验报告另保存 `worker_verified_through`。

24 项监督/归档/资源关闭测试通过；服务器 Python 3.10 编译通过，三个无外部请求的离线核验通过：跨三小时延迟中断全部覆盖、没有实际核验的小时被延后、边界后实际核验允许归档。旧源码、前后 SHA256 与验证结果保存在 `observer-revisions/uptime-interval-v1`，监督器 SHA256 为 `9a5fdcc2666a1acdd33f79fd9f1a185190d43f7591b5182b0ab5e628f60dbe21`，归档脚本为 `c7aab413a966ae4678bb02fe327550f6a908d9fbb9a09a23871f1a6a2f24d5ee`。

仅重载宿主监督器，新 MainPID **2146058** active；既有只读归档服务 `Result=success`/`ExecMainStatus=0`。交接前后采集 worker 原 ID/PID 2051264/StartedAt/RestartCount=0、只读挂载、9 组计划、原采集快照 SHA256 和截止全部不变，`interruptions=[]`。已从新监督器实际写出的 manifest 核验 `last_verified_running_at`，12 项[交接检查](../state/google-hourly-20261009-evidence/uptime-interval-v1-handoff-after.json) 全部通过；[交接前证据](../state/google-hourly-20261009-evidence/uptime-interval-v1-handoff-before.json) 已保存。

本次交接读取到的第四组分钟报告截止 UTC **05:26:53.230036**，实际 **1,827.992 秒**：429 次调用、375 次有结果、3,129 条有效新链接、15 次明确 CAPTCHA（3.497%）、1,560 篇合格新正文。仍未满小时，连续采集阶段尚未开始、实测 24 小时产量仍不存在；本次修订只增强将来的连续运行证据，不能代替实际长期验证。

## 第四组完整小时结果与第五组启动（10-09 14:00 核验）

OpenSERP `p1-s3` 固定窗口 UTC **04:56:25.238407–05:56:25.238407**（新加坡 **12:56:25–13:56:25**），精确 **3,600 秒**：**838 次 Google 搜索、746 次有结果（89.021%）、6,225 条有效新链接、26 次明确 CAPTCHA（3.103%）、3,161 篇去重合格新正文**。另外 66 次普通 HTTP 429 单列，不冒充 CAPTCHA；全部正文来源 Google Web。首次正文与结果 URL 审计完整，缺失首次正文元数据 0，`interruptions=[]`、`valid_for_comparison=true`。

最终实际容器核验记录 `worker_verified_through=2026-10-09T05:56:25.399705+00:00`，晚于固定窗口截止。只读观察器在本轮截止前记录 35 个样本，全部保持原容器 ID、PID 2051264、运行态 true、RestartCount=0；未提前停止、重启、换配置或清空健康。[观察记录](../state/google-hourly-20261009-evidence/openserp-p1-s3-readonly-observation.jsonl) 与[完整冻结报告](../state/google-hourly-20261009-evidence/openserp-p1-s3-final.json) 已复制本地，报告 SHA256 为 `63e2dbdce0b730f73b3aa57f87a1f6c8ed9039b4beb8da257f51ba174d7cfee2`。补充的 Docker 历史事件查询没有返回事件，不能把该空结果当作独立的停止时刻证据；停止顺序依据实际截止后的监督器核验、观察与冻结报告。

强制只读 PostgreSQL 会话按相同固定窗口独立复核搜索、成功、明确 CAPTCHA、原始结果数、保存链接数、全局规范化新链接、首次入库合格正文与审计缺失，共 9 项计数全部一致。746 次有结果调用耗时 3.190–54.923 秒，普通 429 为 2.379–9.202 秒，明确 CAPTCHA 为 1.769–13.226 秒。[独立数据库复核](../state/google-hourly-20261009-evidence/openserp-p1-s3-final-db-check.json) 已保存。归档服务 `Result=success`、`ExecMainStatus=0`；只读归档汇总已包含四个完整有效实验，尚无连续阶段实测小时或日量。

| 完整有效小时 | Google 调用 | 有效新链接 | 识别 CAPTCHA 率 | 合格新正文 |
|---|---:|---:|---:|---:|
|curl，1 页、3 槽|313|0|0%|0|
|Playwright 原版，1 页、3 槽|258|0|0%|0|
|WML，1 页、3 槽|158|0|0%|0|
|OpenSERP，1 页、3 槽|838|6,225|3.103%|3,161|

前三组分别全部 JavaScript 验证、外层浏览器超时与 HTTP 403；其 0% 仅表示没有明确识别到 CAPTCHA，不代表没有验证挑战。当前 OpenSERP 的正文量最高只是在已完成四组中的实测结果，未生成最终选优，也不折算成实测日产量。

监督器在旧组截止后自动启动第五组 `openserp-p1-s6`，实际 Docker 开始 UTC **05:57:37.684824**，截止 **06:57:37.684824**（新加坡 **13:57:37–14:57:37**）。新容器 ID `1df720b939edc6acebe5cb3c61bc1440c42159cccd799972168ede89da4fff2d`、PID **2241195**，Running=true、RestartCount=0。实际 Config：仅 OpenSERP/Google Web、1 页、**6 槽**、正文至少 500 字符、初始 RPS 1/最大 2、每代理间隔 90 秒、RSS 为空；只读挂载原冻结采集快照，全部源码 SHA256 一致。[启动核验](../state/google-hourly-20261009-evidence/openserp-p1-s6-start-check.json) 已保存。

第五组首份 67.296 秒报告为 19 次调用、10 次有结果、65 条有效新链接、2 次明确 CAPTCHA、正文 0；这只是启动窗口，不能和上一组完整小时直接比较。当前完成 **4/9**，剩余 **5** 组，当前 6 槽组继续完整一小时；持续目标保持 active。

补充全小时只读上游元数据复核：838 条调用全部报告 `upstream_attempts=1`，合计 838 次、未知 0；缓存状态全部缺失，不据此宣称已独立证明无缓存。结果已附入第四组数据库复核文件。UTC 06:03 再次确认第五组原容器仍运行、PID 2241195、RestartCount=0，分钟报告 85 次调用、224 篇正文；仍不将这一短窗口当作完整小时结果。

## 第五组运行核验与控制失败摘要（10-09 14:13）

本轮先核验真实状态：第五组仍为原 Docker ID/PID 2241195/StartedAt/RestartCount=0。监督器 MainPID 曾从 2146058 变为 2157256。systemd 与只读 journal 显示：UTC **05:30:06**，上一组的一次 `control tick` 返回非零状态 1，使宿主监督器退出；UTC **05:30:27** systemd 自动恢复。旧采集 worker 没有更换或重启，第四组最终 `interruptions=[]` 且完整小时审计有效。原监督器捕获了子进程 stderr 但没有记录它，因此内部失败原因无法从既有 journal 恢复；不能把离线示例的数据库异常当作该次真实根因。

已修改 [监督器](../scripts/google_hourly_series.py)：控制子进程非零退出或超时时，追加仅含时间、运行、动作、退出码、异常类及栈文件名/行号/函数的摘要到 `control-failures.jsonl`（新文件权限 0600），同时输出同样的安全摘要供 journal 留存。不会保存原始 stdout/stderr、异常消息、源码行或命令参数，不改变控制动作、超时、采集配置或失败后的恢复规则。历史丢失错误不会回填，须用以后实际出现的摘要定位同类问题。

26 项相关测试通过；服务器 Python 3.10 编译与三项无外部请求的离线检查通过，保留异常类型及栈位置、排除虚构的凭据/命令/输出内容。修订、旧代码及 SHA256 保存在 `observer-revisions/control-failure-summary-v1`，新监督器 SHA256 为 `fa41e1e92b8a3b5991ea1629dc59432b79bbdfc5c10f6db7212f0962f329e7ca`。

只重载宿主监督器，新 MainPID **2284486** active。12 项[交接核验](../state/google-hourly-20261009-evidence/control-failure-summary-v1-handoff-after.json) 全部通过：第五组容器 ID、PID、原启动时间、RestartCount=0、只读挂载、原采集源码 SHA256、9 组计划、固定截止及 `interruptions=[]` 全部不变。[交接前证据](../state/google-hourly-20261009-evidence/control-failure-summary-v1-handoff-before.json) 已保存。

交接读取到的第五组分钟窗口截止 UTC **06:12:19.623583**，实际 **881.939 秒**：200 次调用、179 次有结果、1,341 条有效新链接、3 次明确 CAPTCHA（1.5%）、652 篇合格新正文。尚未满小时，不能据此判定 6 槽优于或劣于 3 槽。

UTC **06:13:23** 的[只读来源与抓取检查](../state/google-hourly-20261009-evidence/openserp-p1-s6-live-funnel-check.json)：Google Web/OpenSERP 均为 healthy、当前 RPS 1、无来源熔断，近期窗口 73 次/71 次成功/明确 CAPTCHA 0；本组实际使用端点 45 个，其中 21 个在冷却、0 个命中累计低成功率隔离。这是已使用端点的统计，不等同于全池独立出口 IP 数。792 个已关联页面中，730 个超过本组基线；新页面原始正文最短 539 字符，首次审计完整、原始 hash 互异，当前关联页面正文与存储 hash 无不一致；新页面标签均为 HTTP 200/英文/Google Web。保留冷却，继续到新加坡 **14:57:37.684824** 的完整小时截止。


## 第五组六槽与全池预约核验（10-09 14:25）

UTC **06:25:39** 的[只读进程检查](../state/google-hourly-20261009-evidence/openserp-p1-s6-process-check.json)直接识别到 **6 个 `realtime.scrapy_runner` 子进程**，均由原 worker PID **2241195** 启动；容器 ID、StartedAt、Running=true、Paused=false、Restarting=false、RestartCount=0 不变。因此六槽实际参与采集，并非仅配置值为 6。宿主监督器 MainPID **2284486** active，当前实例 NRestarts=0，新增控制失败摘要行数 0。未重载监督器或采集服务。

[运行核验](../state/google-hourly-20261009-evidence/openserp-p1-s6-live-0625-check.json)的分钟报告截止 UTC **06:24:34.782849**，实际 **1,617.098 秒**：**383 次调用、351 次有结果、2,713 条有效新链接、4 次明确 CAPTCHA（1.044%）、1,365 篇合格新正文**；28 次普通 HTTP 429 单列。正文与链接审计完整、源码 SHA256 匹配。仍未满小时，当前完成 4/9，固定截止仍为新加坡 **14:57:37.684824**。

[全池缓存与数据库预约检查](../state/google-hourly-20261009-evidence/openserp-p1-s6-full-pool-check.json)采用强制只读数据库会话，直接读取缓存，不同步、不选择或预约代理，也不发 Google 请求。UTC **06:24:03** 缓存有 **80 个端点/80 个主机**，HTTP 28、SOCKS5 52；当前组实际使用其中 50 个，缓存之外使用 0。按互斥优先级统计：**21 个被累计低成功率规则排除、35 个处于失败冷却、24 个未命中旧观察公式的限制**。**10-09 15:47 更正：旧脚本使用 Web Config 的代理间隔，不能作为采集worker实际90秒间隔下的可预约证明；本回合核实Web默认为30秒、worker为90秒。旧即时会话细节未冻结，无法可靠重算该历史时刻的90秒分桶，因此撤回“24个在数据库层可预约”的判断。** 累计隔离与失败冷却计数不依赖此间隔字段；未观测进程本地冷却/sticky，主机数也不是独立出口IP证明。

被累计规则排除的 21 个端点为 HTTP 9、SOCKS5 12，各自累计样本 20–31 次，全部普通冷却已过期；合计历史成功 44、失败 395。HTTP 组最近成功 UTC 10-07 14:11:39，SOCKS5 组最近成功 UTC 10-08 05:36:20。对应留存的 SERP 审计有 628 条/43 条结果，不能将它和累计会话计数视为同一数据集。当前 `reserve_google_proxy` 在累计样本≥20且成功率<20%时始终返回拒绝和 3,600 秒等待，没有按时间恢复或试探逻辑；冷却到期本身不会解除此项排除。此前仅统计“本组实际用过的端点”得到隔离数 0，不能据此判断全池没有隔离。保留现有健康记录与冻结实验配置，后续优化须单独验证恢复策略的正文产量及挑战率，不提前清空计数或释放这些端点。

另冻结本组[精确首 15 分钟检查点](../state/google-hourly-20261009-evidence/openserp-p1-s6-first-15m-checkpoint.json)：UTC **05:57:37.684824–06:12:37.684824**，900 秒，已留足 60 秒提交余量；207 次调用、186 次有结果、1,403 条有效新链接、3 次明确 CAPTCHA（1.449%）、652 篇合格新正文。5 项只读与分段计数核验全部通过，上游元数据全部报告 attempts=1。此检查点不纳入完整小时选优、不折算日产量；仍等待原截止后的最终报告。


## 第五组首 30 分钟独立复核（10-09 14:29）

先直接核验 UTC **06:28:16** 的实际 Docker 状态：第五组仍为原容器 ID/PID **2241195**/StartedAt/RestartCount=0，Running=true、Paused=false、Restarting=false，6 个真实 Scrapy 子进程运行。监督器 MainPID **2284486** active，新增安全失败摘要 0；没有重启、提前停止或修改采集配置。上一目标回合取得了全池历史隔离证据，属于实际进展，本回合继续等待真实完整小时。

[首 30 分钟冻结检查点](../state/google-hourly-20261009-evidence/openserp-p1-s6-first-30m-checkpoint.json)固定窗口为 UTC **05:57:37.684824–06:27:37.684824**，精确 **1,800 秒**，读取时已超过窗口结束 60 秒提交余量：**431 次 Google 调用、392 次有结果（90.951%）、3,042 条有效新链接、5 次明确 CAPTCHA（1.160%）、1,503 篇去重合格新正文**，另有 34 次普通 HTTP 429。链接及首次正文审计完整，正文来源全部 Google Web；431 次上游元数据全部报告 attempts=1，不据此独立证明缓存状态。

另用独立 SQL 与规范化 URL 计算复核搜索、成功、CAPTCHA、原始结果、记录链接、独立链接、全局新链接、合格首次正文和审计缺失，[9 项计数全部一致](../state/google-hourly-20261009-evidence/openserp-p1-s6-first-30m-db-check.json)。数据库强制只读，冻结的[单独报告字节](../state/google-hourly-20261009-evidence/openserp-p1-s6-first-30m-report.json) SHA256 与独立复核中的 `bd7849ee0932397a87cb4b3dc2c04f377237f9b7d753d020a13586bd066a3af5` 一致。该窗口仍为 `full_hour_experiment=false`，不参与完整小时选优、不折算小时或日产量。

已启动[只读观察器](../state/google-hourly-20261009-evidence/openserp-p1-s6-readonly-observer.py)，每 45 秒直接检查实际 Docker 状态并记录原容器身份、运行态、截止、分钟报告与最终报告，采集到本组归档且第六组实际运行后结束；观察器自身截止不被解释为采集停止。首个 UTC **06:28:45** 样本确认原容器、`interruptions=[]`、当前组仍运行。[观察记录](../state/google-hourly-20261009-evidence/openserp-p1-s6-readonly-observation.jsonl)继续写入本地，当前仍完成 **4/9**，剩余 **5** 组；本组原固定截止 **14:57:37.684824 新加坡时间**不变。目标保持 active。


## 第五组资源实测与持续只读记录（10-09 14:44）

本回合先轮询已确认存活的观察会话并直接检查实际 Docker 状态；上一回合首 30 分钟冻结与独立复核属于实际进展。当前仍为原六槽 worker ID/PID **2241195**/StartedAt/RestartCount=0，无中断、未重启、未更改本组配置。继续等待原完整小时截止，而非因监督/观察超时或短窗口产量变化结束实验。

UTC **06:39:20–06:40:01** 的[5 个约 10 秒 CPU 模式采样](../state/google-hourly-20261009-evidence/openserp-p1-s6-cpu-modes-samples.json)确认主机为 **4 核**，用户态约 **81.1%–87.4%**、内核态约 **10.6%–17.3%**，idle **0%–0.024%**、iowait **0.024%–0.049%**、steal **0%**，整体几乎没有计算余量；5 分钟系统负载约 **25.7–26.6**。没有操作其他服务器项目，样本不独立归因于任何项目，也不证明本组的全部耗时来自 CPU。

[补充进程角色采样](../state/google-hourly-20261009-evidence/openserp-p1-s6-cpu-roles-samples.json)识别到 OpenSERP 的 `headless-shell`：一个浏览器主进程及多个 renderer。早期两个采样脚本只按 chrome/chromium 名称识别，该可执行程序被归为 `other`，因此不把它们的 `other` 或匹配进程 CPU 当作准确的 Chrome 总 CPU。补充样本仍只计算两次采样均存活且启动 tick 相同的进程，短命 renderer 会遗漏；这些局部进程计数不能用于宣称请求并发上限。源码与 Rod v0.116.2 检查还确认 Element 子元素查找使用 NotFoundSleeper，不据可选描述元素不存在就臆断它会等待整个搜索超时；没有基于未复现假设改 OpenSERP。

已部署[只读资源观察脚本](../scripts/observe_google_resources.py)与 `realtime-google-resources.timer`，每 **5 分钟**追加 `resource-observations.jsonl`（0600）。记录实际 worker 身份/运行态与 manifest 匹配、配置声明、主机 CPU 各模式/负载/可用内存，以及本项目 local-worker/OpenSERP 两个容器的 CPU 和内存采样。主机与 Docker 的短采样窗口不同，数据用于解释条件，不能直接当作完整小时平均。脚本不访问数据库、不发 Google 请求、不改变健康、任务或任何服务；身份不匹配只记录证据，不触发恢复/重启。异常仅打印类型，不输出原 Docker stderr 或异常消息。

本地与服务器 Python 编译通过，真实[首次探测](../state/google-hourly-20261009-evidence/resource-observer-first-probe.json)确认匹配原 worker，CPU 计算占比 **99.50%**、steal 0、可用内存约 25.2 GB。新增定时器 enabled/active，首个自动执行已在 UTC **06:43:01**完成，服务 `ActiveState=inactive`、`Result=success`、`ExecMainStatus=0`，timer 进入 waiting 并设定后续触发；尚未将首次成功当作长时间运行证明。[16 项交接核验](../state/google-hourly-20261009-evidence/resource-observer-handoff-after.json)全部通过：采集 ID/PID/启动/重启计数/挂载/截止/原采集 SHA/9 组计划不变，监督器仍 MainPID **2284486** active，**没有重载监督器或采集服务**。

交接时分钟报告截至 UTC **06:42:24.048647**，实际 **2,686.364 秒**：**664 次搜索、605 次有结果、4,711 条有效新链接、9 次明确 CAPTCHA（1.355%）、2,391 篇去重合格新正文**，另有 50 次普通 HTTP 429；链接与首次正文审计完整，仍未满小时。当前完成 **4/9**，第六组 11 页/3 槽尚未启动；本组截止仍为新加坡 **14:57:37.684824**。此次补充提供以后各组及持续采集的资源条件记录，不回填过去完整小时的资源均值，不提前选优或折算日产量。


## 第五组完整小时与第六组启动（10-09 15:00 核验）

第五组 OpenSERP `p1-s6` 固定窗口 UTC **05:57:37.684824–06:57:37.684824**（新加坡 **13:57:37–14:57:37**），精确 **3,600 秒**：**883 次 Google 搜索、819 次有结果（92.752%）、6,341 条有效新链接、10 次明确 CAPTCHA（1.133%）、3,222 篇去重合格新正文**。另外普通 HTTP 429 **52** 次、HTTP 403 **2** 次单列；6,755 条原始链接去重为 6,699 条，再排除历史基线后为 6,341 条新链接。全部正文来源 Google Web，首次正文及结果 URL 审计完整、缺失首次正文元数据 0；`interruptions=[]`、`valid_for_comparison=true`。

[完整冻结报告](../state/google-hourly-20261009-evidence/openserp-p1-s6-final.json) SHA256 **`752e5ca3bc2017e6db7daf7fe9c4db90a1589ed821b0bd390f3ce3c8bde88427`**，最终真实容器核验 `worker_verified_through=2026-10-09T06:57:37.784454+00:00`，晚于原截止。[只读观察器](../state/google-hourly-20261009-evidence/openserp-p1-s6-readonly-observation.jsonl)在截止前保存 **39 个实际 Docker 样本**，全部原 ID/PID **2241195**/StartedAt、Running=true、Paused=false、Restarting=false、RestartCount=0。UTC **06:58:03** 观察到旧容器已按计划停止，且完整有效报告已生成；UTC **06:58:48** 确认第六组实际运行后观察器正常退出。截止后的预期停止与换组样本不被冒充成截止前中断，也不把观察器终止当作采集停止。

[独立数据库复核](../state/google-hourly-20261009-evidence/openserp-p1-s6-final-db-check.json)使用强制只读 PostgreSQL 会话，9 项搜索、成功、CAPTCHA、原始/记录/独立/新增链接、首次合格正文及审计缺失全部一致。819 次有结果调用耗时 **5.395–56.703 秒**。[上游元数据复核](../state/google-hourly-20261009-evidence/openserp-p1-s6-final-upstream-check.json)883 条全部报告 attempts=1、合计 883、未知 0；缓存状态 883 条均缺失，不据此声称独立证明无缓存。[归档检查](../state/google-hourly-20261009-evidence/archive-after-s6.txt)服务 `Result=success`/`ExecMainStatus=0`，汇总包含 **5 个完整有效实验**，连续采集阶段完整小时仍为 0。

| 完整有效小时 | Google 调用 | 有效新链接 | 明确 CAPTCHA 率 | 合格新正文 |
|---|---:|---:|---:|---:|
|curl，1 页、3 槽|313|0|0%|0|
|Playwright 原版，1 页、3 槽|258|0|0%|0|
|WML，1 页、3 槽|158|0|0%|0|
|OpenSERP，1 页、3 槽|838|6,225|3.103%|3,161|
|OpenSERP，1 页、6 槽|883|6,341|1.133%|3,222|

六槽小时比三槽小时多 **61 篇（1.930%）**，目前只是已完成组的正文数最高；关键词切片、时段、持久化代理健康与资源竞争均不同，不能断言差值由六槽造成，也不能据此认定系统上限或换算实测日产量。原版 curl/browser/WML 的零 CAPTCHA 只表示无明确识别，仍分别存在 JavaScript 验证、外层超时与 403。

第六组 `openserp-p11-s3` 实际 Docker 开始 UTC **06:58:41.476405**，截止 **07:58:41.476405**（新加坡 **14:58:41–15:58:41**）。[实际启动核验](../state/google-hourly-20261009-evidence/openserp-p11-s3-start-check.json)：新 ID **`a54247f6121a0b39d66406b862086606b2afa41206ba2cef86f47eec008dfc74`**、PID **2428521**，Running=true、Paused=false、Restarting=false、RestartCount=0；Config 仅 OpenSERP/Google Web、页数及每批页数均 **11**、**3 槽**、正文≥500 字符、初始 RPS 1/最大 2、每代理间隔 90 秒、RSS 为空。只读挂载原冻结采集源码，全部 SHA256 一致；查询起点 75,000，健康记录未清空。本组初始 2.702 秒报告为 0 次请求，只是启动瞬间，不能当小时结果。

资源定时器已在 UTC **06:43:01、06:48:01、06:53:02**连续三次自动执行成功，所获记录均匹配原第五组容器；[资源记录检查点](../state/google-hourly-20261009-evidence/resource-observations-checkpoint.jsonl)另包含第一次 CLI 验证及后续计划交接。该记录是短窗口条件，不是整小时平均。宿主监督器仍 MainPID **2284486** active/NRestarts=0，未出现新的安全失败摘要。当前完成 **5/9**、剩余 **4** 组；未生成最终选优，目标继续 active。


## 第六组实际分页与 OpenSERP 独立 HTTP 日志复核（10-09 15:14）

本回合先直接核验原第六组 Docker ID/PID **2428521**/StartedAt/RestartCount=0，Running=true、Paused=false、Restarting=false，Config 仍为 11 页/每批 11 页/3 槽，仅 OpenSERP/Google Web，冻结源码 SHA 全部匹配。正在运行的只读观察会话确认 `interruptions=[]`，固定截止仍为新加坡 **15:58:41.476405**；上一目标回合完成了第五组完整小时及第六组启动，属于实际进展。

UTC **07:06:37** 的[实际分页审计](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-page-depth-audit.json)使用固定分钟报告窗口 UTC **06:58:41.476405–07:06:23.018304**，实际 **461.542 秒**：172 次调用已覆盖 **第 1–11 页**；审计 request_url 中 `start=(page-1)×10` 的缺失与不一致均为 **0**。第 2–10 页均有真实结果，第 11 页首次调用为普通 HTTP 429，不能声称该早期窗口已成功返回第 11 页。结果不能据配置声明直接算成 11 次有效搜索。

| 返回页 | 调用 | 有结果 | 归入该最浅返回页的全局新链接 |
|---|---:|---:|---:|
|1|52|42|339|
|2|34|25|126|
|3|24|22|88|
|4|19|15|73|
|5|11|10|46|
|6|9|8|26|
|7|8|7|31|
|8|6|5|17|
|9|5|4|10|
|10|3|2|14|
|11|1|0|0|

新链接共 **770** 条，其中 **431** 条归入第 2–10 页。归属规则是在整个窗口所有查询中，将同一规范化新 URL 只分给它出现过的最浅页，避免重复相加；这不是正文来源页的因果归属，重定向与爬取后代未追溯至页号。与同一完整查询的成功第一页对照，深页调用没有任何一组 URL 集合完全相同，出现少量交集；不足以证明所有深页均有同样的边际收益。搜索、原始结果、新链接分桶总数、分页偏移及数据库只读，共 **5 项核验全部通过**。实时 frontier 与 RPS 另按审计查询时刻记录，不能当作该固定窗口的历史状态。

补充独立调用证据：从 OpenSERP 自己的 HTTP 完成日志抽取 Google `/google/search` 调用，只保留 UUID、UTC 时间、HTTP 状态与耗时；不落盘原始消息、请求头、查询、代理地址、错误内容或 payload。读取已完成小时的前后 120 秒边界缓冲，随后按固定小时筛选，再与强制只读数据库中的 `upstream_request_id` 逐一匹配。

- OpenSERP 三槽完整小时：[HTTP 日志与 DB 对应检查](../state/google-hourly-20261009-evidence/openserp-p1-s3-http-log-db-check.json) **838/838**，ID 集合完全一致、状态全部一致、缺失 ID 0、日志解析/时间/UUID 错误 0，9 项检查全部通过。[脱敏日志](../state/google-hourly-20261009-evidence/openserp-p1-s3-http-log-sanitized.json) SHA256 `105dccbf62093bcd190553a3af41f32d0a653f28d2cbcf8fc12fe2623f2d0e36`。
- OpenSERP 六槽完整小时：[HTTP 日志与 DB 对应检查](../state/google-hourly-20261009-evidence/openserp-p1-s6-http-log-db-check.json) **883/883**，同样无集合或状态不一致、缺失/解析错误 0，9 项检查全部通过。[脱敏日志](../state/google-hourly-20261009-evidence/openserp-p1-s6-http-log-sanitized.json) SHA256 `c7e81d33e0f615fc2b428c962bd91ee6f881ebde70d80f118b90d8dbd138ce38`。

该核验为两个已完成 OpenSERP 小时提供来自服务端日志的独立调用对应证据，不改冻结产量，也不把本地客户端/服务端两份证据称为 Google 上游抓包或无缓存证明；更不据此宣称前三个非 OpenSERP 小时已取得同样的独立上游证据。原始日志仅在内存中筛选，服务器脱敏导出文件创建为 0600。

UTC **07:14:07** 的真实只读观察仍为原容器运行、重启 0、无中断；分钟报告截止 **07:14:04.337941**，实际 **922.862 秒**：**271 次调用、228 次有结果、1,332 条有效新链接、10 次明确 CAPTCHA（3.690%）、505 篇合格新正文**。这是分钟窗口而非冻结首 15 分钟，更不是完整小时。监督器 MainPID **2284486** active/NRestarts=0，资源服务最新执行 UTC **07:13:14** `Result=success`/`ExecMainStatus=0`，没有新增控制失败摘要。当前仍完成 **5/9**，不因深页短窗口正文较少改变当前采集或健康配置，继续等待完整小时。


## 第六组租约与发起搜索前失败核验（10-09 15:24）

本回合先直接核验实际 Docker：UTC **07:22:04** 的[运行检查](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-0722-check.json)仍为原第六组 ID/PID **2428521**/StartedAt/RestartCount=0，Running=true、Paused=false、Restarting=false，实际 Config 仍为 11 页/每批 11 页/3 槽、仅 OpenSERP/Google Web，原冻结源码 SHA 全部匹配。已确认存活的观察 session **20317** 继续记录，没有因观察超时、产量变化或监督器 PID 变化提前停止本组。

新增可复现的[只读 frontier 核验脚本](../state/google-hourly-20261009-evidence/google-frontier-snapshot.py)，数据库强制只读并使用 repeatable read 一致快照，不选择/预约代理，不发搜索请求，不改健康、配置、源码或采集状态。源码确认 `acquire_google_page_batch` 默认租约为 **900 秒（15 分钟）**；不能把此前未核实的 5 分钟假设用于瓶颈判断。

- UTC **07:23:09.117201** 的[一致快照](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-frontier-snapshot.json)：**56 个 cooling、357 个 pending、3 个 running、completed/failed 为 0**；三个 running 均未过期，最早租约到期 UTC **07:37:51.408544**。pending 中 337 个已到可运行时间，不能把待推进任务等同于已发 Google 请求。
- UTC **07:23:53.866297** 的[错误分桶快照](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-frontier-errors-snapshot.json)：**57 个 cooling、359 个 pending、running/completed/failed 为 0**。本组 416 条 discovery_failed 事件全部归入白名单错误：**355 次 google_proxy_unavailable、34 次普通429、14 次明确CAPTCHA、9 次普通403、4 次 openserp_proxy_connect**；当前 frontier.last_error 分桶相同。累计 Google 调用此刻为 **405 次、344 次有结果**，第11页仍为1次调用/0次有结果。

`google_proxy_unavailable` 在 `_select_proxy` 中抛出，位置早于运输层调用和 SERP 尝试记录；不能把这 355 条发起前失败额外算作 Google 搜索、HTTP失败或 CAPTCHA，也不能据此断言整个私有池已经失效。固定每端点间隔、持久化健康、进程本地冷却与选择机制均可能使预约未取得，本回合不改变这些条件。

两个快照没有过期 running，累计记录没有 frontier acquire/release 错误或 `google web RuntimeError`，不支持当前存在租约丢失瓶颈。`google_frontier_lease_lost` 原异常会被转为泛化 RuntimeError，因此字面事件为0本身不能排除历史丢失；当前 frontier 与 page_stats 不是不可变租约历史，也不能还原每次重试。

[监督与资源核验](../state/google-hourly-20261009-evidence/openserp-p11-s3-supervisor-0725-check.json)仍为 MainPID **2284486** active/NRestarts=0，安全失败摘要文件尚不存在、行数0；资源定时器 enabled/active，最近资源服务执行 UTC **07:23:17** Result=success/ExecMainStatus=0。未重载任何服务。

UTC **07:23:53** 直接 Docker 观察仍匹配原容器、`interruptions=[]`。分钟报告截止 **07:23:46.431334**、实际 **1,504.955 秒**：**405 次调用、344 次有结果、2,132 条有效新链接、14 次明确 CAPTCHA（3.457%）、875 篇合格新正文**。尚未满小时，固定截止仍为新加坡 **15:58:41.476405**；完成 **5/9**、剩余4组、未生成最终选优，目标继续 active。


## 第六组精确首 30 分钟冻结及独立复核（10-09 15:31）

上一目标回合取得租约与搜索发起前失败的真实只读证据，属于实际进展。本回合先直接核验 UTC **07:26:04** 的[Docker/Config/源码状态](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-next-check.json)，仍为原 ID/PID **2428521**/StartedAt/RestartCount=0、Running=true、Paused=false、Restarting=false，11 页/每批 11 页/3 槽、仅 OpenSERP/Google Web，冻结源码 SHA 无不一致。只读观察 session **20317** 确认存活，持续真实 Docker 检查，无中断；等待窗口截止及60秒提交余量，没有提前停止或重启实验。

新增[通用固定窗口检查点脚本](../state/google-hourly-20261009-evidence/google-fixed-checkpoint.py)，通过只读 repeatable read 事务读取冻结控制器的指标，不执行 prepare/tick/finish、不写数据库或采集状态；时长及run由参数指定。UTC **07:30:16.764084** 读取的[首30分钟检查点](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-30m-checkpoint.json)固定窗口 UTC **06:58:41.476405–07:28:41.476405**，精确 **1,800秒**：

| 指标 | 计数 |
|---|---:|
| Google Web调用 | 469 |
| 有结果调用 | 406（86.567%） |
| 有效全局新链接 | 2,491 |
| 明确 CAPTCHA | 14（2.985%） |
| 普通 HTTP429 / 普通 HTTP403 | 36 / 9 |
| OpenSERP代理连接502 | 4 |
| 全局去重合格新正文 | 1,041 |

原始记录链接3,548条，规范化去重3,196条，再排除历史基线后新链接2,491条；合格首次正文审计缺失0、来源全部Google Web。6项固定窗口、分段计数及只读核验通过。469条上游元数据均为attempts=1、缓存状态全部缺失，不当作独立无缓存证明。

[单独冻结报告](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-30m-report.json) SHA256 **`516f1531d511bef1c8467faa7b2498ce4e529da9b9b516fd0c4742beaf8ab1ca`**，以0600权限同步到服务器系列目录，仅为检查点证据。新增[可指定报告文件的独立核验脚本](../state/google-hourly-20261009-evidence/google-independent-checkpoint-audit.py)读取该准确报告字节，使用独立SQL及规范化URL计算；[9项复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-30m-db-check.json)全部一致、报告SHA匹配，数据库强制只读。

同一固定窗口的[分页复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-30m-page-depth-audit.json)5项检查通过，请求start偏移缺失/不一致均为0。按返回结果最浅页归入的全局新URL，第1页为 **1,749** 条，第2–10页合计 **742** 条；第11页仍只有1次普通429调用/0次有结果。深页新链接分桶不能用于归属正文、重定向或后代页面，也不能解释全部差值的因果。可复现的[分页检查点脚本](../state/google-hourly-20261009-evidence/google-page-depth-checkpoint-audit.py)已保存。

补充核验服务器实际[后续9组计划](../state/google-hourly-20261009-evidence/pending-google-plans-0727-check.json)，接下来仍为 Google全来源、browser-navigation-v3、openserp-fallback-v1，selection尚不存在。[两份修订的只读完整性检查](../state/google-hourly-20261009-evidence/pending-google-revisions-0729-check.json)：各31个Python文件与原SHA清单完全一致，留存的离线smoke分别2/3项通过且external_requests=0；本次未重跑浏览器测试、未替换当前源码或挂载，启用时监督器仍会重新校验。

首30分钟不参与完整小时选优、不折算小时或日产量，不能据此认定11页优劣。当前仍完成 **5/9**，第六组继续运行到新加坡 **15:58:41.476405**，目标保持active。


## 第六组深页重试的实际队列位置（10-09 15:36）

上一目标回合完成首30分钟冻结、独立SQL与分页复核，属于实际进展。本回合先直接检查 UTC **07:32:34** 的[原容器运行状态](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-0732-check.json)，ID/PID **2428521**/StartedAt/RestartCount=0、运行态不变；实际11页/每批11页/3槽、Google Web/OpenSERP、冻结源码SHA仍匹配。只读观察session **20317** 确认存活，没有提前停止或重启。

新增[只读队列/frontier/子进程核验](../state/google-hourly-20261009-evidence/google-queue-frontier-readonly.py)，仅执行Redis TIME/LRANGE/ZRANGE、强制只读repeatable read数据库查询、Docker inspect及容器内/proc白名单子进程读取；不执行enqueue/pop/promote/schedule等队列变更，不访问代理或Google，不清理旧任务。DB、Redis、子进程扫描是短时间内的分别观测，不声称跨系统原子快照。

UTC **07:35:57.287785** 的[实际worker配置及队列检查](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-queue-frontier-worker-config-check.json)：

- 原容器身份及运行态在探测前后完全一致、重启0；真实 **3个Scrapy子进程** 均属于本run。直接读取worker Config为3槽、查询线程配置4、最大11页、provider仅OpenSERP；线程配置不代表实测同时完成调用数。
- 本run **1,408个active采集任务**，其中 **1,402个在ready、3个实际运行、3个delayed**；互斥分桶合计准确覆盖全部任务。ready共1,434条且无重复、与delayed无交集；额外32条ready/21条delayed全部属于已stopped任务，未观察到其他active来源，也没有把这些旧条目解释为仍在采集。
- ready任务包含 **244个已到期且尚未完成的深页frontier**，最老到期已过去约 **2,085.9秒**。包含这些frontier的采集任务当前位于按BRPOP计算的 **第1,325–1,434位**；rank1才是下一个候选。它们已到期但仍在长队列尾部，不是租约过期：本run各队列分桶的expired_running均为0。

此前 UTC **07:35:02** 的[初次队列快照](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-queue-frontier-check.json)同样得到231个已到期深页、任务队列位置第1,329–1,434位。该初版输出把web容器Config标为actual_config，其中slots=2；这只是web默认值，不能当作采集worker的实际槽数。后续脚本已明确输出web_config_only，并直接读取worker Config=3及真实三个子进程；保留原证据、不覆盖第一次字节。

两次调用审计按完整query+page分组，均没有n>1的重复完成SERP调用；这不能排除发起前代理预约失败或缓存读取，但与大量已到期任务排在尾部的实时证据一致。[冻结调度源码核验](../state/google-hourly-20261009-evidence/openserp-p11-s3-frozen-scheduling-source-check.json)确认controller、world_keywords、worker及campaign_queue的SHA均匹配；controller的ACTIVE_LIMIT为 **1,408**；冻结worker会在采集结束后按frontier最短延迟重入队列，当前FIFO策略没有为这些重试提供前排位置。队列名中的ready不代表该查询已发出请求，rank也不能直接折算等待秒数。

该证据支持后续独立验证“小规模任务积压/更及时重试”的调度配置，不能据此证明它必然增加全局新正文或提前否定本组11页结果。本组、后续九组计划及代理健康保持冻结，当前不改ACTIVE_LIMIT或队列。

UTC **07:35:54** 真实只读Docker观察仍匹配原容器、`interruptions=[]`；分钟报告截止 **07:35:41.241491**，实际 **2,219.765秒**：**577次调用、491次有结果、3,053条有效新链接、19次明确CAPTCHA（3.293%）、1,288篇合格新正文**。尚未满小时，固定截止仍为新加坡 **15:58:41.476405**；完成 **5/9**、未最终选优，目标保持active。


## 第六组首45分钟、缓存协议变化及观察口径纠正（10-09 15:47）

上一目标回合取得真实队列位置及冻结调度源码证据，属于实际进展。本回合先直接核验 UTC **07:39:46** 的[Docker/Config/源码状态](../state/google-hourly-20261009-evidence/openserp-p11-s3-live-0738-check.json)，原ID/PID **2428521**/StartedAt/RestartCount=0、Running=true、Paused=false、Restarting=false，11页/每批11页/3槽/仅Google Web/OpenSERP与原冻结源码SHA均不变。存活观察session **20317** 持续真实Docker核验，无中断。

使用已验证的通用只读检查点脚本冻结[首45分钟](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-45m-checkpoint.json)，固定窗口 UTC **06:58:41.476405–07:43:41.476405**，精确 **2,700秒**，读取时留足60秒提交余量：**677次搜索、581次有结果、3,651条全局新链接、20次明确CAPTCHA（2.954%）、1,552篇合格新正文**；普通429为52次、普通403为19次、代理连接502为5次。原始链接5,070条、规范化独立链接4,560条，首次正文审计缺失0、来源全部Google Web。[9项独立SQL复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-45m-db-check.json)全部一致，[准确报告字节](../state/google-hourly-20261009-evidence/openserp-p11-s3-first-45m-report.json) SHA256 **`70687de085656b7c54b4682b537b6f6f20a74698c04768d94b3ace2bb85fb75e`**，本机和服务器报告一致。检查点不参与完整小时选优、不折算日产量。

UTC **07:41:24** 的[旧口径全池探测](../state/google-hourly-20261009-evidence/openserp-p11-s3-full-pool-0740-check.json)缓存仍为80端点/80主机，但协议已为HTTP 24/SOCKS5 56；本组用了62个当前键，另外3个用过的键不在当前缓存。**其中db_minimum_interval/db_reservable不能作为worker实际90秒下的预约计数**：随后[Web/worker缓存核对](../state/google-hourly-20261009-evidence/openserp-p11-s3-worker-web-cache-match.json)确认两容器缓存文件SHA、同步时间、80条记录全部相同，但Web默认间隔30秒、worker实际90秒；初次探测的interval_matches=false/assertion失败属于观察口径发现，不是采集故障或中断。旧证据保留，未用它判断代理池失效或改变生产。

已修正[全池只读脚本](../state/google-hourly-20261009-evidence/google-full-pool-worker-interval-readonly.py)，按本run冻结的local-worker compose override读取间隔，并与当前真实worker的90秒核验对应；输出明确标注间隔来源及Web默认值。`/tmp/google_hourly_full_pool_check.py`也已指向修正版本，原版保留为web_default_legacy，防止后续误用。没有修改冻结采集代码、override、容器或数据库。

UTC **07:45:33.167542** 的[正确90秒分桶](../state/google-hourly-20261009-evidence/openserp-p11-s3-full-pool-worker-90s-check.json)：80个端点中 **20个累计低成功率排除、45个失败冷却、15个最短间隔限制、可预约0**；计数互斥并准确覆盖80个。此时Google Web/OpenSERP均healthy、RPS1.5625、无熔断，近期38次/36次有结果/明确CAPTCHA0。可预约0仅为该瞬间DB限制，不代表永久停采或80个出口物理失效；本地冷却与实际外部出口IP仍未独立核验。此前旧观察脚本读Web Config的历史可预约判断已在对应段落明确更正，不凭当前状态回填历史90秒分桶。

[协议键关联复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-proxy-protocol-alias-check.json)数据库强制只读：三个旧键全部映射到当前缓存同一host:port的另一协议，HTTP→SOCKS5为2、SOCKS5→HTTP为1；本组65个协议键映射到62个当前host:port。该窗口672次调用中631次使用当前协议键、41次使用这些另一协议键，未映射键0。源码按host:port挑质量/延迟较好的协议后发布缓存，保留的元数据不能独立还原历史API候选或证明实际出口IP。20个当前键被累计规则排除不同于此前快照21个，不宣称手动释放了旧隔离；协议键对应独立健康记录，当前80端点中有4个当前键未命中低成功率阈值、但另一协议键命中，此项不证明其当前可用，也不据此直接合并/重置健康。可复现的[只读关联脚本](../state/google-hourly-20261009-evidence/google-proxy-protocol-alias-readonly.py)已保存。

[本组8次自动资源采样](../state/google-hourly-20261009-evidence/openserp-p11-s3-resource-samples-through-0740.json)覆盖UTC **07:03:09–07:38:19**，全部匹配原worker；[采样摘要](../state/google-hourly-20261009-evidence/openserp-p11-s3-resource-sample-summary-through-0740.json)显示短窗口主机计算占比 **54.88%–99.88%**、steal0，5分钟负载 **13.14–20.23**，可用内存 **22.85–24.07GiB**。这些不是整小时均值或等待时间因果证明，也不把第五组六槽几乎满CPU的几个短样本延伸为所有配置均无计算余量。监督器MainPID **2284486** active/NRestarts=0、安全失败摘要0，资源timer enabled/active、最近服务执行UTC07:38:19 success。

UTC **07:46:25** 的直接Docker观察仍匹配原容器、无中断；分钟报告截止 **07:45:30.655201**、实际 **2,809.179秒**：**699次搜索、601次有结果、3,797条新链接、20次明确CAPTCHA（2.861%）、1,607篇合格新正文**。尚未满小时，原截止仍为新加坡 **15:58:41.476405**，完成 **5/9**、未最终选优，目标继续active。


## 第六组完整小时及第七组全Google来源启动（10-09 16:03）

上一目标回合完成首45分钟独立复核、协议键关联和代理观察间隔纠正，属于实际进展。本回合先直接核验原容器仍运行，并持续轮询已经确认存活的session **20317** 到原固定截止；没有因低产出、代理瞬时不可预约、观察超时或监督器变化提前停止/重启。

第六组 `openserp-p11-s3` 固定窗口 UTC **06:58:41.476405–07:58:41.476405**，精确 **3,600秒**：**829次Google Web搜索、707次有结果（85.283%）、4,505条有效全局新链接、23次明确CAPTCHA（2.774%）、1,936篇合格新正文**。另外普通429 **64次**、普通403 **30次**、OpenSERP代理连接502 **5次**单列；原始链接6,151条、规范化独立链接5,540条。正文全部Google Web，首次正文与结果URL审计完整、缺失0；`interruptions=[]`、`valid_for_comparison=true`。

[冻结最终报告](../state/google-hourly-20261009-evidence/openserp-p11-s3-final.json) SHA256 **`131c1eb37dd93b59b08eeee6c338225af5dc8fb7f973ad18b3321a45c1254d50`**。最终真实Docker核验 `worker_verified_through=2026-10-09T07:58:41.541715+00:00`，晚于原截止。[观察摘要](../state/google-hourly-20261009-evidence/openserp-p11-s3-observation-summary.json)确认截止前 **75个**实际Docker样本均为原ID/PID **2428521**/StartedAt/RestartCount=0、正常运行；UTC07:59:12观察到截止后的预期停止及完整有效报告，07:59:57核验下一组实际运行后观察器正常退出。[完整观察记录](../state/google-hourly-20261009-evidence/openserp-p11-s3-readonly-observation.jsonl)保留77个样本，换组不记为提前中断；旧session20317已结束，不再轮询。

[独立数据库复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-final-db-check.json)在截止60秒提交余量之后使用强制只读会话，9项搜索、成功、CAPTCHA、原始/记录/独立/新增链接、首次正文及缺失全部一致，最终报告SHA匹配。707次有结果调用耗时3.157–51.769秒。

[完整分页复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-final-page-depth-audit.json)5项检查通过、请求start偏移缺失/不一致均0；第2–10页全部有结果，第11页只有1次普通429、0次有结果。全局新URL按最浅返回页分桶：

| 页号 | 调用 | 有结果 | 分桶新链接 |
|---|---:|---:|---:|
|1|498|451|3532|
|2|208|157|573|
|3|59|46|179|
|4|21|17|77|
|5|11|10|46|
|6|9|8|26|
|7|8|7|31|
|8|6|5|17|
|9|5|4|10|
|10|3|2|14|
|11|1|0|0|

第1页分桶3,532条、第2–10页合计973条，合计严格等于4,505条新URL；同一完整查询的成功深页与成功第一页无任何完全相同URL集合，但该分桶仍不能将正文、重定向或爬取后代归属到页号。冻结配置允许11页不等于11页都成功，也不能把配置页数直接乘作有效调用。

补充[OpenSERP服务端HTTP完成日志与DB逐调用复核](../state/google-hourly-20261009-evidence/openserp-p11-s3-http-log-db-check.json)：严格小时 **829/829**，ID集合与状态完全一致、缺失0、9项检查通过；[脱敏日志](../state/google-hourly-20261009-evidence/openserp-p11-s3-http-log-sanitized.json) SHA256 **`42e2c0b5775a6ceec5645188e5e2a99c556b2870e1579771bd7b3c005a504efa`**。原始Docker日志只在远端Python内存中过滤，仅保存UUID、时间、HTTP状态与耗时，导出0600；这是服务端HTTP完成证据，不是Google抓包或无缓存证明。

[归档复核](../state/google-hourly-20261009-evidence/archive-after-p11.json)已包含6个完整有效小时，archive服务UTC08:00:48 Result=success/ExecMainStatus=0；持续阶段完整小时0、selection尚不存在。

| 已完成配置 | Google Web调用 | 新链接 | 明确CAPTCHA率 | 合格新正文 |
|---|---:|---:|---:|---:|
|curl 1页/3槽|313|0|0%|0|
|browser原版 1页/3槽|258|0|0%|0|
|WML 1页/3槽|158|0|0%|0|
|OpenSERP 1页/3槽|838|6,225|3.103%|3,161|
|OpenSERP 1页/6槽|883|6,341|1.133%|3,222|
|OpenSERP 11页/3槽|829|4,505|2.774%|1,936|

当前测试的11页配置正文数少于两个已完成的OpenSERP第一页配置；这不证明所有深页调度都更差，也不能消除关键词切片、时段、持久化代理健康/协议缓存变化、资源与大队列等待的影响。六槽仍是已完成组中正文数最高，尚未完成全部实验、连续验证或证明系统上限，不折算实测日产量。前三组CAPTCHA0仍只代表明确识别计数0。

第七组 `openserp-google-all-s3` 实际开始 UTC **07:59:21.233696**、截止 **08:59:21.233696**（新加坡 **15:59:21–16:59:21**）。[真实启动检查](../state/google-hourly-20261009-evidence/openserp-google-all-s3-start-check.json)确认新ID **`5559a27c4c241cf4eb748a0a719d9bb09a2679dba709723de1d6feb92665e20a`**、PID **2605918**、Running=true/Paused=false/Restarting=false/RestartCount=0；实际Config为OpenSERP、1页/每批1页/3槽、正文≥500字符、初始RPS1/最大2、每端点90秒。RSS只有 **`https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en`**；原冻结采集源码只读挂载、SHA全部一致，关键词起点90,000、代理健康未清空。

[独立任务来源核验](../state/google-hourly-20261009-evidence/openserp-google-all-s3-campaign-sources-start-check.json)强制只读数据库，1,408个active任务全部实际设置 **Google Web+Google News+Google Trends+RSS**，3项检查通过。它证明启用来源，不证明每次辅助请求已发出或成功；本系列搜索/新链接计数仍明确是Google Web SERP口径，News/Trends/RSS辅助请求未取得新增不可变请求审计，不能把Web计数冒充为全部辅助调用/链接计数。正文数则覆盖本run所有来源的合格首次入库。

新[只读观察器](../state/google-hourly-20261009-evidence/openserp-google-all-s3-readonly-observer.py)已启动并确认session **52202** 存活，每45秒检查实际Docker，持续至本组完整报告及第八组真实启动；[观察记录](../state/google-hourly-20261009-evidence/openserp-google-all-s3-readonly-observation.jsonl)首样本UTC08:01:23匹配原新容器、无中断。该分钟报告窗口仅65.416秒、12次Web调用/8次有结果/39条Web新链接/1次明确CAPTCHA/正文0，不用于小时产量或选优。宿主监督器仍MainPID **2284486** active/NRestarts=0、安全失败摘要0，未重载任何服务。当前完成 **6/9**，剩余3组（含当前全来源），目标继续active。


## 第七组首15分钟与正文刷新一致性复现（10-09 16:19）

上一目标回合完成第六组完整小时、独立SQL/分页/HTTP服务日志核对及第七组真实启动，属于实际进展。本回合先直接读取实际Docker、Config、源码SHA与存活观察session **52202**；[UTC08:05运行检查](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-0805-check.json)仍为原第七组ID/PID **2605918**/StartedAt/RestartCount=0、正常运行、四来源及Google News RSS配置不变，没有提前停止或重启。

新增[只读正文/来源审计](../state/google-hourly-20261009-evidence/google-body-sources-audit-readonly.py)，读取一份准确分钟/固定窗口报告，在强制只读repeatable read快照内独立重算首次关联、原始正文字符数/抓取时间、原始hash及来源，不使用可变fetched_at或可变来源标签替代首次元数据。

[第一次固定分钟窗口审计](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-body-sources-audit.json)截止 UTC **08:07:02.327003**，独立核对 **1,362篇** 正文与报告一致，缺失首次审计0、原始hash全部唯一、与留存全局首次hash无重复，最短521字符、中位6,038字符，原始HTTP200/英文、覆盖741个当前域名。来源为Google News1,235、Google Web119、News+Web1、Google News RSS1、Google Trends6；这些是正文首次来源标签，不是辅助请求或全部新链接计数，也不做逐篇语义质量证明。

**该审计并非全部通过**：2篇当前正文重新计算SHA与存储content_hash不一致；原始hash及字符数审计仍在，因此没有静默取消此项检查或把fail改成pass。精确当前challenge标题筛查0也不能证明全部页面都无挑战内容。

[后续只读刷新核验](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-body-refresh-integrity-check.json)在UTC **08:10:10**检查本run2,331个新关联页面：两条不一致为page_id **157392**（gamingbible.com）及 **157936**（businessday.ng），均有晚于首次抓取的当前fetched_at，存储hash仍等于不可变原始hash。后一条正文从原始4,136字符变为当前4,292字符；原始文本和中间版本没有保留，不能准确回放所有文本变化。按当前正文SHA查询存储hash索引没有命中其他页面，不把这一有限查询当作对所有当前正文的完整重复检查。

已定位 `CampaignStore.record_page` 同一URL再次写入分支：更新content/title等字段，但未同步content_hash。没有对生产页面做猜测性修补，也没有修改当前冻结采集代码。

在本机创建独立临时 **PostgreSQL17**，执行真实CampaignStore与原首次入库触发器，完成[无mock的三步复现](../state/google-hourly-20261009-evidence/local-body-refresh-lab-repro.json)：原URL插入A正文→同URL刷新B正文→新URL提交B正文。8项异常行为断言全部成立：同URL保持page_id/不新增关联、正文变B但hash仍A、首次入库元数据保持A；再提交新URL的B被当作新页，最终两份当前正文相同而存储hash不同。这证明当前正文去重/一致性有缺陷，**不证明此窗口不可变首次hash计数已被重复**，也不把异常复现成功称为修复通过。临时数据库已停止、目录已清理、生产连接0、外部HTTP请求0；[可复现脚本](../state/google-hourly-20261009-evidence/local-body-refresh-lab-repro.py)保留。

[服务器冻结源码对应检查](../state/google-hourly-20261009-evidence/openserp-google-all-s3-record-page-source-check.json)确认该方法AST源码SHA为 **`ab96d169efc406784a9718203a845a7455ea0a27978ca920bec0c6b686a036c3`**，与本地真实复现方法完全一致，整份冻结文件SHA与系列manifest匹配。后续需以隔离回归验证修复刷新/去重一致性，再作为单独修订部署验证；本回合没有实现或部署修复，当前9组实验代码保持冻结。

另用通用检查点冻结[精确首15分钟](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-15m-checkpoint.json)，窗口 UTC **07:59:21.233696–08:14:21.233696**，精确 **900秒**，读取时已留足60秒提交余量：**72次Google Web搜索、53次有结果、374条Web全局新链接、4次明确Web CAPTCHA（5.556%）、3,313篇合格首次正文**；普通Web429为15次。原始/独立Web链接均410条。

[准确报告字节](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-15m-report.json) SHA256 **`d7a279dcc155063d25b23b74b128f839aa2481c7c50330dba8488c0f7ed26bd0`**，[9项独立SQL复核](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-15m-db-check.json)全部一致。更详细的[首15分钟正文来源审计](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-15m-body-sources-audit.json)确认3,313篇首次元数据完整、hash在本组及留存全局首次审计中均无重复，最短 **505** 字符、中位 **5,841** 字符、原始HTTP200，英文3,312/中文1，覆盖1,570个当前域名；原始来源如下：

| 首次正文来源 | 篇数 |
|---|---:|
|Google News|3,127|
|Google Web|175|
|Google News+Web|1|
|Google News RSS|2|
|Google Trends|8|

该正文审计仍保留2条当前正文/hash不一致及`all_checks_passed=false`，其余首次计数、来源分桶和原始hash检查通过。合格篇数按初始入库规则统计，不把当前变更后的正文重新算成新的首次正文；不存在已留存原始hash重复的证据，丢失的历史版本也不能被重新构造。Web新链接374不能与来自全部来源的正文3,313直接作抓取成功率分母；辅助发现链接/请求未新增完整不可变审计，CAPTCHA率也只为Web明确识别口径。

前15分钟正文数已高于此前已完成组的整小时篇数，但不因此提前结束、选优或折算日量。需等待原完整小时及后续持续验证，修复一致性问题仍是未完成工作。

[UTC08:18:56运行核验](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-0819-check.json)仍为原ID/PID **2605918**、重启0、正常运行、原冻结Config及源码SHA匹配；分钟报告截止 **08:17:51.728599**、实际 **1,110.495秒**：92次Web调用、511条Web新链接、5次明确Web CAPTCHA、3,994篇合格首次正文。当前仍完成 **6/9**，第七组原截止新加坡 **16:59:21.233696**不变，目标保持active。


## 第七组首30分钟与本地正文修复（10-09 16:40）

[首30分钟冻结检查](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-30m-checkpoint.json)窗口 UTC **07:59:21.233696–08:29:21.233696**，精确1800秒且留足60秒提交余量：**128次Google Web调用、101次有结果、763条Web新链接、5次明确Web CAPTCHA（3.906%）、6,610篇合格首次正文**。准确报告SHA256为 **`3ca7b43da6053795e1f3fde8426f6b4b0f9cb51eb7475f57060dedf4979b5896`**；[独立SQL](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-30m-db-check.json)9项计数全部匹配。

[正文来源审计](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-30m-body-sources-audit.json)首次元数据缺失0、原始hash本组及留存全局首次审计无重复；来源为News6,260、Web335、News+Web1、RSS4、Trends10。当前正文hash不一致仍为 **3** 条，`all_checks_passed=false`明确保留。Web调用/新链接/CAPTCHA不涵盖辅助请求，不能与全部来源正文直接相除，未满小时不选优或折算日量。

本地 `CampaignStore.record_page` 已实现正文刷新同步hash、正文变化清空indexed_at、历史hash表维持全局去重；同URL真实刷新包括回到旧正文仍可更新，其他URL命中历史版本不覆盖较新canonical正文。URL与incoming hash的事务锁同时覆盖插入/刷新并发，候选查询走URL/hash/历史hash索引。Whale仍只在outbox保留瞬态正文，既有本地正文/对应hash/元数据受保护。

一次性迁移marker避免每个采集子进程全表回填：记录现存存储hash、当前可读正文实际SHA、可用首次审计原hash；优先保留现有owner，不猜测重建、合并或删除旧页面/版本。已回填正文收到可验证相同正文时可校正旧hash；丢失的中间版本无法恢复。部署时必须先停止旧writer，不能让两种去重协议混写；本次没有部署或修改生产数据库，当前9组采集代码和配置全部冻结。

[真实临时PostgreSQL17回归](../state/google-hourly-20261009-evidence/local-body-refresh-lab-fixed.json) **11项通过、0失败/错误/跳过**，含刷新后新URL去重、历史版本去重/同URL回退、首次审计不变、同hash及同URL并发、刷新与新URL竞争、Whale空正文/outbox/本地正文保护、一次性回填、旧hash校正及拒绝错误incoming hash。方法SHA **`401816a7ce733355d270368e850f044c5f4337c069e5ccb5cbeffb188c865692`**，文件SHA **`ffc231b52fd9d08d97e44d79e2000761858da316f9711597b39404e31198d482`**。临时库已停止清理，生产连接0/外部HTTP请求0。[复现驱动](../state/google-hourly-20261009-evidence/local-body-refresh-lab-fixed.py)与[测试快照](../state/google-hourly-20261009-evidence/local-body-refresh-history-tests.py)留存，原异常复现证据未覆盖。

本地全量单元测试在已有SOCKS转发fixture等待中未结束，停止的是本地测试PID77386，退出137；不能称全量通过。其后剩余4模块共13项通过，`git diff --check`通过。[验证限制](../state/google-hourly-20261009-evidence/local-body-refresh-broad-tests-summary.json)明确记录；无需修改当前生产代理实现来掩盖此限制。

[UTC08:38真实运行检查](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-0839-check.json)仍是原ID/PID **2605918**、重启0、正常运行、冻结源码SHA匹配。分钟报告截止UTC **08:37:16.653875**：160次Web调用、973条Web新链接、7次明确CAPTCHA、7,931篇首次合格正文，不是完整小时。

[新监督器自动恢复证据](../state/google-hourly-20261009-evidence/openserp-google-all-s3-supervisor-auto-recovery-check.json)记录 UTC **08:20:06.766268** 本组`tick`失败：明确 **`psycopg.errors.DeadlockDetected`**，位置为冻结 `CampaignStore.initialize()` 执行SCHEMA的DDL；此处只导出异常类与栈位置，未输出凭据或原始日志。systemd于UTC **08:20:26**自动恢复，现MainPID **2669763** active/NRestarts=1；这是本次有直接证据的初始化死锁，不把它反推为早前05:30未知异常的根因。原采集容器未变化，观察器持续记录原PID/无重启，实验不提前结束或重启。控制器反复初始化的DDL竞争需后续单独修复和验证。

当前完成 **6/9**，第七组原截止UTC **08:59:21.233696**不变，continuous完整小时0、selection不存在，目标仍active。

补充更正：前30分钟窗口正文一致性失败实际为3条（早前口头提及2条沿用了15分钟结果）；[UTC08:42只读刷新复核](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-body-refresh-integrity-0842-check.json)检查8,994个本组新关联页面，当前不一致共5条：157392、157936、162703（tejwin.com）、163888和164533（frontiersin.org）。全部存储hash仍等于原始hash、当前抓取时间晚于首次；各不一致正文与其他当前页面精确文本比对命中0，这是针对这5条的查询，不是全局所有当前正文重算。没有生产修补或丢弃原始计数。观察器UTC08:41:58仍匹配原容器/PID/重启0；分钟报告截止08:41:45.373443，168次Web调用、1,014条Web新链接、7次明确CAPTCHA、8,952篇首次正文，尚非完整小时。


## 管理脚本消除重复初始化与第七组首45分钟（10-09 16:53）

本轮首先直接核验Docker与存活session52202：[UTC08:43](../state/google-hourly-20261009-evidence/openserp-google-all-s3-live-0844-check.json)仍为原容器/PID2605918/重启0、冻结代码匹配。上一轮本地正文修复/真实PG测试是实际进展，本轮继续处理有直接脱敏证据的管理初始化DDL死锁。

管理脚本新增 `controller_store`：prepare保留正常初始化；tick/anchor/finish以 `initialize=False` 创建连接，通过10组零行SELECT验证真实schema关键字段，成功后在本进程正常初始化锁下登记DSN缓存，使原冻结run controller中再次构造CampaignStore也复用已验证schema。缺表/字段会在写入前明确失败，不静默迁移。未改冻结采集源码、run controller、关键词调度、来源/页数/并发/代理健康，正文修复也未部署。

[本地隔离验证](../state/google-hourly-20261009-evidence/local-hourly-schema-control-lab.json) **20项通过、0失败/错误/跳过**：4项真实PostgreSQL17场景（持有pages写锁仍可监督/内层普通constructor不执行DDL、缺列失败不登记、新schema prepare正常建表、未知action无初始化）与16项现有控制/证据单测。测试使用200ms数据库锁超时，可检出原重复ALTER TABLE行为；临时库已停止清理，生产连接0/外部HTTP0。保留[驱动](../state/google-hourly-20261009-evidence/local-hourly-schema-control-lab.py)及[测试快照](../state/google-hourly-20261009-evidence/hourly-schema-control-tests.py)。

于UTC **2026-10-09 08:47:31.087904+00:00** [原子部署](../state/google-hourly-20261009-evidence/schema-control-v1-deployment-result.json)只替换 `state/google-hourly-20261009/control.py`。原文件SHA **`288f8315353de95e034a8200887390f8f3eaa738423d6c9cadf3658cbc324318`**备份为 `control.py.before-existing-schema-v1`，新SHA **`0232ef1c0b33cf601a44e0752bff9678878dac08773c6192dff793f24b045b83`**；没有重载systemd服务或重启采集容器，监督器下次调用自动读取新管理脚本。

[服务器强制只读验证](../state/google-hourly-20261009-evidence/schema-control-v1-server-readonly-probe.json)实际使用原冻结采集包与新管理脚本、DSN设置`default_transaction_read_only=on`，管理constructor及内层默认CampaignStore constructor全部成功、3项通过。这直接证明该现有schema路径没有重复执行会被只读事务拒绝的建表迁移，不只是静态检查或mock。

[交接后18项检查](../state/google-hourly-20261009-evidence/schema-control-v1-handoff-after.json)全部通过：原worker ID/PID2605918/启动时间/重启0/只读挂载和原截止不变，原31个采集文件、9组plan、run及系列scheduler、后续两个revision均保持原SHA；MainPID2669763/NRestarts1/active未重载，失败摘要仍1条，新监督tick已经实际完成（分钟报告截止UTC **2026-10-09 08:49:46.808918+00:00**）。这修复重复DDL路径，不宣称已排除所有未来数据库死锁。

[前45分钟冻结](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-45m-checkpoint.json)准确窗口 UTC **07:59:21.233696–08:44:21.233696**，2700秒并留足60秒提交余量：**176次Web调用、140次有结果、1,076条Web新链接、7次明确CAPTCHA（3.977%）、9,220篇首次合格正文**。准确报告SHA **`a7c86578a6b1e0295c7f0c26f92cfd8155e1642f82f829e4886f1592143a975c`**，[9项独立SQL](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-45m-db-check.json)全通过。

[正文审计](../state/google-hourly-20261009-evidence/openserp-google-all-s3-first-45m-body-sources-audit.json)保留5条current body/hash不一致及`all_checks_passed=false`。首次元数据缺失0、原始hash本组及留存全局首次审计无重复，最短505字符、中位5,803、最长100,000；英文9,219/中文1、当前域名3,180。来源News8,705、Web492、News+Web2、RSS6、Trends15。请求/新链接/CAPTCHA仍是Web口径，正文是所有启用来源；不混作成功率分母，也不把45分钟折算实测日量或提前选优。

[本组短窗口资源证据](../state/google-hourly-20261009-evidence/openserp-google-all-s3-resource-samples-through-0853.json)共10个样本、全部原容器，实际主机[4]核；计算占比74.62%–100.00%、steal为0。样本不是整小时均值或并发上限证明，未据此改变当前3槽。

完整小时原截止仍UTC **08:59:21.233696**；全小时审计/第八组真实启动尚待核验。目标仍active，完成6/9，持续阶段完整小时0。

本地全量验证限制已推进解决：[单独有界复现](../state/google-hourly-20261009-evidence/local-socks-fixture-before.json)确认现有SOCKS fixture卡住，而HTTP fixture正常。仅在`tests/test_proxy_relay.py`补齐模拟upstream和client两端writer的close/wait_closed，生产relay代码未改；[修复后两个原协议测试](../state/google-hourly-20261009-evidence/local-socks-fixture-after.json)均通过。重新执行[全量单元测试](../state/google-hourly-20261009-evidence/local-full-unit-suite-after-socks-cleanup.json)：283项、247通过/36条件跳过、0失败，1.669秒，原资源警告仍有但非失败。数据库集成测试在此无TEST_DATABASE_URL而跳过，另有正文11项真实PG回归及管理4项真实PG回归通过，不能把跳过说成实测通过。`git diff --check`通过。此前中止的全量证据保留，不覆盖或改称通过。


## 第七组完整小时归档与第八组真实启动（10-09 17:03）

第七组 `openserp-google-all-s3` [完整报告](../state/google-hourly-20261009-evidence/openserp-google-all-s3-final.json)窗口 UTC **07:59:21.233696–08:59:21.233696**（新加坡 **15:59:21–16:59:21**），精确 **3600秒**、`valid_for_comparison=true`、无中断，SHA256 **`7e9fcc58dba22e089dfedbd994dea3d36c3d33ddad705ddd080fe71da860d633`**。结果：**248次Google Web调用、205次有结果、1,579条Web新链接、8次明确Web CAPTCHA（3.226%）、12,475篇合格首次正文**。普通429为34次、代理连接错误1次；原始结果链接1,709、规范化独立1,708。

[独立SQL复核](../state/google-hourly-20261009-evidence/openserp-google-all-s3-final-db-check.json)9项计数与准确最终报告全部一致。[实际Docker观察摘要](../state/google-hourly-20261009-evidence/openserp-google-all-s3-observation-summary.json)共有80样本，截止前78个全部原ID/PID2605918/重启0/Running，无超过45.118秒的相邻观察间隔；监督器最后匹配原容器时刻 **08:59:21.291535**，晚于截止。截止后正常停止及第八组新容器不当作提前中断。旧session52202已在最终报告及第八组真实启动后正常退出，不再轮询。

[OpenSERP服务端逐调用核对](../state/google-hourly-20261009-evidence/openserp-google-all-s3-http-log-db-check.json) **248/248**：DB与严格小时日志request ID集合及HTTP状态完全对应，缺失0、9项通过。[脱敏日志](../state/google-hourly-20261009-evidence/openserp-google-all-s3-http-log-sanitized.json) SHA256 **`b28d513e984417a6ba60b2da791f151189dafeac85fd2ced55af166ef1ebdb4b`**，仅导出UUID、时间、HTTP状态和耗时。它是HTTP完成证据，不是Google抓包、无缓存证明，也不计未通过OpenSERP的辅助请求。

[最终正文审计](../state/google-hourly-20261009-evidence/openserp-google-all-s3-final-body-sources-audit.json)核验12,475个新关联页面全部符合首次规则，缺失首次元数据0、原始hash在本组及留存全局首次审计中无重复；最短505字符、中位5,638、最长100,000，全部原始HTTP200，英文12,474/中文1、当前域名3,967。来源如下：

| 首次正文来源 | 篇数 |
|---|---:|
|Google News|11,695|
|Google Web|748|
|Google News+Web|2|
|Google News RSS|9|
|Google Trends|21|

正文审计仍有 **5条current body/hash不一致**，`all_checks_passed=false`保留，不能因首次hash计数复核通过而把正文一致性也说成通过。本地hash/历史去重修复已有11项真实PG通过但未部署，丢失的中间历史正文不能恢复；需在冻结9组完成后单独部署及验证。资格仍为生产首次规则/正文≥500字符，不是每篇完整语义质量审核。调用/新链接/CAPTCHA是Web口径，正文覆盖所有启用来源；辅助News/Trends/RSS请求和全部辅助URL审计仍不完整，不能把1,579条Web链接与12,475篇正文直接相除。

[归档验证](../state/google-hourly-20261009-evidence/archive-after-google-all.json)已包含 **7个完整有效小时**，archive服务UTC **Fri 2026-10-09 09:01:05 UTC** Result=success/ExecMainStatus=0；监督器MainPID2669763 active/NRestarts1、失败摘要仍1条（原08:20初始化死锁），新管理脚本没有新增失败。selection不存在，continuous完整小时0。

| 已完成配置 | Google Web调用 | Web新链接 | 明确Web CAPTCHA率 | 合格首次正文 |
|---|---:|---:|---:|---:|
|curl 1页/3槽|313|0|0%|0|
|browser原版 1页/3槽|258|0|0%|0|
|WML 1页/3槽|158|0|0%|0|
|OpenSERP 1页/3槽纯Web|838|6,225|3.103%|3,161|
|OpenSERP 1页/6槽纯Web|883|6,341|1.133%|3,222|
|OpenSERP 11页/3槽纯Web|829|4,505|2.774%|1,936|
|OpenSERP 1页/3槽全部Google来源|248|1,579|3.226%|12,475|

全来源组是已完成组中首次正文数最多，纯Web最佳仍为6槽3,222篇；尚有两组冻结实验、正文一致性修复部署及连续验证未完成，不提前最终选优、宣称系统上限或折算实测日产量。单次顺序小时不能排除关键词切片、时段、累计代理健康、来源历史去重与资源差异的影响。

第八组 `browser-v3-p1-s3` 实际开始UTC **09:00:07.764302**、截止 **10:00:07.764302**（新加坡 **17:00:07–18:00:07**）。[真实启动12项检查](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-start-check.json)全通过：新ID **`51f76eac006c66eba0d5a3d95f58a077953f0536e524de8190f3c070d1effea3`**、PID **2782297**、重启0，实际仅browser Google Web、1页/每批1页/3槽、正文≥500、初始RPS1/最大2、每端点90秒、RSS空；`browser-navigation-v3`的31个源码文件SHA与独立revision manifest一致且只读挂载，关键词起点105,000、健康未清空，原计时锚定真实DockerStartedAt。

新[通用只读观察器](../state/google-hourly-20261009-evidence/google-hourly-successor-readonly-observer.py)session **53123**已启动并确认真实存活，[观察记录](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-readonly-observation.jsonl)UTC09:02:46仍原新ID/PID/重启0，分钟报告截止09:02:12.897300为21次调用、0有结果/新链接/正文、明确CAPTCHA0。零明确CAPTCHA不证明没有挑战；短窗口0产出不提前结束或重启本小时。目标保持active；下一组仍为`openserp-fallback-v1-p1-s3`，全部有效小时后再临时选优并验证持续阶段。


## 第八组检查点与本地辅助来源审计（10-09 17:50）

接手先复核观察session53123及监督器：UTC09:44:45 MainPID2669763 active/NRestarts1、失败摘要仍1条、完成7/9、selection不存在。随后[UTC09:49:31实际核验](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-live-0950-check.json)12项全部通过，仍原ID/PID2782297/重启0、原冻结revision31个源码SHA匹配、只读挂载，截止UTC10:00:07.764302不变。分钟报告截至09:49:27为204次调用、0有结果/新链接/明确CAPTCHA/正文；不提前结束或重启。

| 第八组固定窗口 | 调用 | 有结果 | 新链接 | 明确CAPTCHA | 合格首次正文 |
|---|---:|---:|---:|---:|---:|
|首15分钟，900秒|63|0|0|0|0|
|首30分钟，1800秒|126|0|0|0|0|
|首45分钟，2700秒|189|0|0|0|0|

[15分钟检查](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-first-15m-checkpoint.json)、[30分钟检查](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-first-30m-checkpoint.json)、[45分钟检查](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-first-45m-checkpoint.json)均准确锚定原启动、只读查询；各自独立SQL9项计数一致。45分钟错误为google_timeout187、google_browser_deadline2；明确CAPTCHA为0不能证明没有挑战。第一次45分钟查询在60秒提交余量之前返回ready=false，保留[未就绪证据](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-first-45m-not-ready.json)，后续就绪才冻结准确报告，未控制采集。

本地新增辅助来源插入型审计，尚未部署：请求先记录start、再实际GET/POST、返回后记录finish；proxy/preflight未发请求标attempted=false，缓存/offline只有batch与链接。News feed与文章resolver GET/RPC POST分开，Trends/RSS分别保留；redirect hops单独记录。正文仅记前缀SHA/字节数及明确Google CAPTCHA标记，不存正文、请求headers、RPC签名/payload、query原文或代理凭据。审计写失败不发出未审计请求，finish写失败保留可识别的未完成start；普通429/304正文未读保持body_not_examined。

新增事件表和辅助known URL registry，保留最早观察、精确重试允许、冲突证据拒绝覆盖。只读汇总按trace读取跨窗口关联记录，检查start/finish/content/batch关联；Web和辅助链接取union，同时排除两类历史registry与baseline，不能简单相加。Web SERP与News keyword feed合为keyword搜索，其余resolver/RSS/Trends独立计数，各自CAPTCHA分母保留。Web表没有campaign_id，汇总范围仍是全数据库窗口，需要独立排他运行证据。

只读CLI `scripts/google_source_audit.py --run-directory <目录> --report <准确冻结报告>` 要求manifest中有 `auxiliary_audit.revision` 和覆盖整个窗口的 `enabled_since`。这是部署者记录的启用断言，不能代替实际流量证据；缺启用信息/缺表/缺关联输出unavailable或unknown、metrics=null，不把旧冻结组补成0。实际read-only repeatable-read事务保留DSN原search_path，输出不含数据库异常原文。

[95项隔离回归](../state/google-hourly-20261009-evidence/local-auxiliary-audit-lab.json)全部通过/0跳过，实际含20项真实PostgreSQL17（该原报告fixture文字仍写19，已在最终来源证据注明更正）及75项现有/模拟请求验证；含原正文历史去重、存储、schema-control和discovery。随后CLI真实PG测试检出连接options覆盖search_path，修复为保留DSN options再追加强制只读；[最终25项针对性验证](../state/google-hourly-20261009-evidence/local-auxiliary-audit-lab-final.json)全部通过/0跳过，含6项真实PG及19项请求/CLI模拟验证。两个失败复现及日志均保留，没有把失败标成通过。

[本地全量单测](../state/google-hourly-20261009-evidence/local-full-unit-suite-auxiliary-final.json)于UTC09:48:23执行307项：266通过、41条件跳过、0失败/错误；其后新增CLI真实PG case及options修复由上述25项单独验证，全量快照不冒充后续版本。临时PG均停止清理、生产连接0/外部HTTP0，diff-check/py_compile通过。[最终源码SHA及限制](../state/google-hourly-20261009-evidence/local-auxiliary-audit-final-sources.json)与源码快照保留。新增DB/DNS开销尚未做生产整小时对照；所有事件均缺失的请求无法靠关联检查发现，未宣称审计全面完备。

现有9组采集源码/配置仍冻结；本地正文hash历史修复与辅助来源审计均未部署，不改变第七组5条current body/hash不一致的既有结论。9组结束后仍需单独revision整小时验证、持续24小时实际产量验证及继续优化，目标active。


## 本地采集子进程 schema 复用准备（10-09 17:56）

本轮开始UTC09:52:39直接检查监督器active/MainPID2669763/无新增失败，观察器原PID2782297/重启0继续正常；上一轮为实际代码、证据与文档进展，未暂停目标或重启实验。

仅本地进一步解决每个新采集子进程重复执行SCHEMA的路径：constructor按SCHEMA SHA生成durable revision marker，已提交版本只读查询后复用；缺版本时先按真实database/schema取得事务advisory锁，锁后重查，再原子执行DDL、历史hash回填和Whale旧payload清理并写marker。两个新协议初始化器不会同时CREATE/ALTER。显式initialize()仍保留强制迁移/清理行为，失败事务不会留下表或成功marker。marker仅证明对应迁移成功提交，不是schema外部损坏的全面检查；升级仍须停止旧writer，新旧协议不能混写。

[102项回归](../state/google-hourly-20261009-evidence/local-cold-schema-reuse-lab.json)全部通过/0跳过，含24项真实PostgreSQL17。新增3项实际场景：两个独立Python进程同时初始化空schema、已建版本在pages ROW EXCLUSIVE写锁存在时新的只读进程仍能constructor成功、schema变更产生新marker且故意失败的迁移整笔回滚。其余覆盖正文历史去重、辅助请求/只读CLI、管理schema-control及原discovery。临时库停止清理、生产连接0/外部HTTP0；[源码方法SHA及限制](../state/google-hourly-20261009-evidence/local-cold-schema-reuse-sources.json)与测试/驱动快照保留。正文record_page方法SHA仍401816a7ce733355d270368e850f044c5f4337c069e5ccb5cbeffb188c865692。

[新代码全量单测](../state/google-hourly-20261009-evidence/local-full-unit-suite-cold-schema.json)311项：266通过、45条件跳过、0失败/错误；真实PG已由上述单独回归实测，不把跳过当通过。diff-check和py_compile通过。正文hash修复、辅助审计、cold-schema复用源码已在本地冻结到state/google-next-revisions/body-aux-schema-v1（33个源码文件），用于9组完成后的单独整小时候选，未上传/启用生产、不改当前9组。生产整小时开销/连续24小时产量仍待验证。


## 第八组完整小时归档与第九组真实启动（10-09 18:03）

第八组browser-navigation-v3 [完整最终报告](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-final.json)窗口UTC09:00:07.764302–10:00:07.764302，精确3600秒、valid_for_comparison=true、无中断。**245次调用、0有结果、0新链接、0明确CAPTCHA、0合格首次正文**；错误google_timeout240、google_browser_deadline5。准确报告SHA1632a2ec84a00cc57d536a1ff5082794cccaed3eb02ffca124a5e0a4ce774f40，[独立SQL9项](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-final-db-check.json)全部一致。明确CAPTCHA为0仍不证明没有挑战；没有把浏览器调用套用OpenSERP服务日志验证。

[Docker观察摘要](../state/google-hourly-20261009-evidence/browser-v3-p1-s3-observation-summary.json)80个样本、截止前78个全部原ID/PID2782297/重启0，最大相邻间隔45.109419秒；监督器最终匹配原容器时刻10:00:07.818629晚于截止。截止后正常停止、下一组新容器均为正常交接；session53123在完整报告及真实下一组启动后正常结束，不再轮询。[归档](../state/google-hourly-20261009-evidence/archive-after-browser-v3.json)已包含8个完整有效小时，服务10:01:36 Result=success/ExecMainStatus0，continuous完整小时0。

第九组openserp-fallback-v1-p1-s3实际开始UTC**10:01:03.705293**，截止**11:01:03.705293**（新加坡18:01:03–19:01:03）。[12项真实启动检查](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-start-check.json)全部通过：ID293862d1d4a605c6141c65d41b1e04c8cbe12213e20ecce2e6f247375b3c94bb、PID2980143、重启0；实际只有OpenSERP Google Web、1页/每批1页、3槽、正文≥500、初始RPS1/最大2、每端点90秒、RSS空，原revision31个源码SHA匹配且只读挂载，未清空代理健康或改冻结plan。

新[只读观察器记录](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-readonly-observation.jsonl)session43482已真实启动并确认原PID运行；等待本组完整小时归档及continuous-best真实启动后正常结束。监督器10:01:26仍MainPID2669763 active/NRestarts1，失败摘要仍1条，selection不存在、完成8/9。最优组仍仅是已完成数据中全Google来源12475篇/小时，纯Web6槽3222篇/小时；第九组未完成，不最终选优或折算实测日量。

本地下一版body-aux-schema-v1的33个源码已冻结，直接从冻结目录导入并运行43项discovery测试全部通过，SHA和语法检查通过。最初系统Python缺requests的尝试未完成，之后使用项目virtualenv才完成，不把它列为产品代码故障。后续候选拟先全Google来源3槽、再6槽，各3600秒；当前只有本地计划，未上传或运行生产。仍需9组全部独立核验后，在不提前结束已启动连续小时的前提下另行升级、整小时验证正文/辅助审计/初始化修复，然后完成连续24小时实际产量与稳定性验证；目标active。


## 第九组首15分钟与本地新系列验证包（10-09 18:21）

本轮先于UTC10:04核验session43482原PID2980143/重启0、监督器MainPID2669763 active/NRestarts1、失败摘要仍1条，完成8/9。随后[UTC10:09:48真实检查](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-live-1010-check.json)12项全部通过，仍原容器、实际配置/31个源码SHA/只读挂载一致，截止11:01:03.705293未改变。

[首15分钟冻结](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-first-15m-checkpoint.json)准确窗口UTC10:01:03.705293–10:16:03.705293（900秒，已留足60秒提交余量）：**261次Web调用、233次有结果、1080条Web新链接、4次明确Web CAPTCHA（1.5326%）、526篇首次合格正文**。错误为普通429共24次、google_captcha4次。[独立SQL9项](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-first-15m-db-check.json)全部匹配准确报告。

[同窗口正文审计](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-first-15m-body-audit.json)全部检查通过：首次元数据缺失0，本组/留存全局原始hash无重复，本组当前正文/hash不一致0；526篇全为Google Web标签、原始HTTP200/英文，最短524字符、中位6507、最大100000，当前域名410。它是本组窗口审计，不是对历史第七组异常的修复，也不是逐篇语义审核。不把15分钟折算实测日量或提前结束本小时。

仅本地进一步把辅助统计/正文完整性接入下一版管理脚本：启用新审计revision且enabled_since覆盖整窗口时，固定小时报告补充completed_google_keyword_searches（Web+News keyword feed）、all_source_valid_new_links（Web/辅助URL union、排除两类历史registry），并保留各来源完整明细。旧top-level searches/links仍为Web SERP口径；不默默换分母。启用信息缺失输出unavailable/metrics=null，不把旧组补为0。

新增只读SQL正文窗口检查：首次关联+首次抓取时间与原始≥500字符规则不变；另检出当前正文/hash不一致、当前hash历史owner缺失、留存全局首次hash重复和首次元数据缺失。管理报告使用read-only repeatable-read。新版本正文完整性或启用覆盖失败不能进入有效对照；显式未完成start/batch仍触发extended_audit_complete=false及告警，但不伪称正文计数不可知。正文有效对照不等于所有辅助事件完整；completed client calls是已记录完成调用，缺失start/finish时总尝试数仍有未知部分。没有把未读body说成无CAPTCHA，不能把这些客户端事件当Google抓包。

连续24小时归档补充Google keyword总调用、全来源新链接总量及extended audit完整性；旧系列缺新字段返回None。不存在24个相邻有效完整小时不产生实测日量；body integrity/enablement检查沿用到新连续小时。归档失败输出脱敏错误类型/返回码。新系列准备启动时若local-worker实际Running，拒绝control/Compose调用，以防覆盖原运行小时；这不是自动部署旧系列收尾/迁移的全部门禁，实际升级仍须逐项验证。

[增强管理52项验证](../state/google-hourly-20261009-evidence/local-augmented-hour-control-lab-final.json)全部通过/0跳过，含16项真实PG；其后新增别名字段、归档总量和活容器启动拒绝由[最终36项针对性验证](../state/google-hourly-20261009-evidence/local-augmented-hour-control-final-followup.json)覆盖，含6项真实PG/0跳过。[全量单测](../state/google-hourly-20261009-evidence/local-full-unit-suite-augmented-control.json)320项：269通过、51条件跳过、0失败/错误；真实PG另行实测，不把跳过当通过。临时PG已停止清理、生产连接0/外部HTTP0，diff-check/py_compile通过。[最终源码SHA/限制](../state/google-hourly-20261009-evidence/local-augmented-hour-final-sources.json)与源码/驱动快照保留。

本地完整验证包位于state/google-next-series/google-hourly-20261009-audited：body-aux-schema-v2（34个源码文件）、冻结管理/关键词/归档/资源脚本及未安装systemd单位；3组候选为全Google来源3槽、6槽，以及纯Web6槽，各3600秒。包含纯Web控制组以便同版正文协议下比较；成功跑完后按首次正文量选优并自动进入continuous-best。镜像SHA4947b1c14809a89a079273ae07badfbbee4de02d8f9cf884591e9245b34ce482与当前Docker实际镜像独立核对相同，没有重建镜像或打印env。[包校验](../state/google-hourly-20261009-evidence/next-series-local-package-validation.json)复用真实collection_code检查34个源码SHA/离线证据、3组来源计划与启用revision，全部通过。旧body-aux-schema-v1快照保留，不覆盖；v2未上传/运行。

部署前仍须9组全部独立核验，若旧continuous-best已自动启动先完整收完当前小时，然后停止旧writer/旧监督器、初始化新schema及历史hash、记录实际Docker计时与审计启用，再运行单独小时候选。包中的deployment_gate文字是待实施的门禁要求，不能代替实际验证；当前只在本地准备包，未声称升级已完成。生产开销、最佳新配置及连续24小时实际产量尚待验证，目标active。


## 第九组30/45分钟复核及只读升级门禁（10-09 18:53）

第九组原Docker ID/PID2980143/StartedAt/重启0持续匹配，原截止UTC11:01:03.705293未变。首30分钟固定1800秒：484次Web调用、442次有结果、2078条新链接、6次明确CAPTCHA（1.2397%）、1061篇合格首次正文。首45分钟固定2700秒：727次Web调用、669次有结果、3795条新链接、11次明确CAPTCHA（1.5131%）、1826篇合格首次正文；普通429共44次、HTTP403共3次。两个窗口均从原Docker实际启动计时、留足60秒提交余量，准确报告与独立SQL9项全部一致。

[首45分钟正文审计](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-first-45m-body-audit.json)全部通过：首次元数据缺失0、留存全局原始hash重复0、当前正文/hash不一致0；1826篇全为Google Web/原始HTTP200/英文，最短506字符、中位5928、最大100000，当前域名1314。准确报告SHA aaa898c2dd6b00b3f95e6815d27b1c04305cdfe8265bbc35e09bbbb986d425a1。未把短窗口算作完整小时或折算日产量，第七组既有5条当前正文/hash异常仍未修复。

新增[scripts/google_revision_gate.py](../scripts/google_revision_gate.py)，仅只读检查：9组固定完整小时与SHA对应的9项独立SQL、非重叠真实窗口、旧worker及监督器已停止；若旧continuous已启动，必须有边界后真实核验的准确完整归档小时，且在该边界后120秒内正常停止，不能中途结束。6项本地单测通过；[UTC10:37实际只读检查](../state/google-hourly-20261009-evidence/revision-transition-gate-live-final-check.json)8个已完成小时及其完整SQL全部通过，ready=false，明确第九组未完成、worker及监督器仍运行；生产修改0。门禁报告是快照，不是锁，也不认证历史当前正文完整性；不能当作新服务每次重启的ExecStartPre。

补齐curl与browser原版完整9项SQL证据，原browser简要证据保留。curl/browser原版/WML缺少保存的worker_verified_through时间戳；门禁只对这3个固定legacy run接受SHA核验的原监督代码截止后先检查真实Docker再停止的流程，明确保留证据差异，不回填时间戳。保留旧源码SHA62a2626c7d2e40e61a680a2d5af198d0d214486e151e1578e8f0dcd8402579a2；[实际旧代码mock clock/Docker验证](../state/google-hourly-20261009-evidence/legacy-final-state-protocol-actual-code-probe.json)两份旧代码各2场景全部通过：截止运行可有效、已停止无效、inspect先于stop，生产修改/外部请求0。这验证旧流程语义，不重建过去缺失的现场时间戳；continuous小时不能套用该例外。

强制只读数据库资源快照UTC10:42:43及10:47:48：max_connections100、预留3、连接21/22，累计deadlocks均676、conflicts0、stats_reset=null。约5分钟累计值未增加，不能把历史676归因于本组或说历史只有一次死锁；不是整小时资源或6槽容量验证。

升级前代码复核发现新管理stdin进程在/app工作目录可能优先导入web旧包而忽略PYTHONPATH冻结包，正在本地修正为Compose exec工作目录/tmp，并保留先前冻结包。此修正未部署当前系列；下一系列真实容器导入路径仍须单独验证。第九组仍等待完整小时自然收尾，旧continuous若先启动须先收完完整小时；目标active。


### 管理冻结包导入路径已验证（10-09 18:58）

原/app stdin进程实际导入/app/realtime/campaign_store.py，SHA00795de560f3fc87a541e29d8ae0b54da33af2081b1d1280b9b1af05bf6e86d9。管理Compose exec现加入-w /tmp，让PYTHONPATH指定的本系列冻结包优先；supervisor/control调用及独立archive均修正，当前旧系列文件不变。34项相关测试全部通过，包含实际Python stdin子进程的同名旧包遮蔽复现/修复检查，diff-check通过。

原本地冻结包保留，另存state/google-next-series/google-hourly-20261009-audited-wd-v1，runtime仍body-aux-schema-v2，仅管理工作目录和系列身份有显式修订。新包已上传到生产state同名独立目录，75个声明源码SHA全部核验；尚未安装systemd单位、启用worker或初始化schema。[实际服务器加载路径核验](../state/google-hourly-20261009-evidence/new-frozen-import-server-probe.json)确认/tmp cwd及3个新版模块确实来自/app/state/google-hourly-20261009-audited-wd-v1/code/realtime，并全部匹配SHA；数据库连接/外部请求0。新包不能沿用旧导入路径的绿色静态验证作生产证明。

已准备只读观察器，跟踪旧continuous-best实际启动及首完整归档小时；观察失败或分钟产量不触发停止。实际迁移步骤记录于state/google-transition-20261009/transition-plan.json，状态prepared_not_applied，当前原采集仍持续运行。新版性能、辅助审计完整性及实际24小时产量尚待验证，目标active。


## 九组完整小时结束及暂优配置连续启动（10-09 19:06）

第九组[最终报告](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-final.json)为UTC10:01:03.705293–11:01:03.705293，准确3600秒、无中断、valid_for_comparison=true：917次客户端审计调用、857次有结果、5305条Web新链接、12次明确CAPTCHA（1.3086%）、2626篇合格首次正文。普通429共45次，HTTP403共3次。SHA78e6d038fd10f6181c63b341132112507bb8a5d241085ee3c94f13c636c2ae53，[9项独立SQL](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-final-db-check.json)全部吻合。

[最终正文审计](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-final-body-audit.json)全部通过：2626篇全为Web/原始HTTP200/英文，首次元数据缺失0、原始hash本组及留存全局重复0、当前正文/hash不一致0；最短503字符、中位5983、最长100000，当前域名1822。它不能认证其他旧组的当前正文完整性，第七组既有5条异常尚未修复。[Docker观察摘要](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-observation-summary.json)82条、截止前79条均原ID/PID2980143/重启0，最大相邻间隔45.114128秒；监督器最终实际核验11:01:03.845239晚于截止。session43482已在正常收尾及连续容器真实启动后退出0，不再轮询。

[OpenSERP严格窗口逐ID检查](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-http-log-db-check.json)明确保留all_checks_passed=false：917个DB窗口ID全部有HTTP记录且状态吻合，严格HTTP完成日志有920个ID。[边界复核](../state/google-hourly-20261009-evidence/openserp-fallback-v1-p1-s3-http-boundary-reconciliation.json)核实多出的3个均为窗口末尾HTTP200完成，分别在11:01:04.500424、11:01:07.386016、11:01:08.853367才写入客户端审计，均有留存DB对应；不是丢失请求，但两个严格时间口径集合不相等。保留917/920双口径，不把这3行移入冻结小时，不改原报告或把严格匹配失败改成通过；这也不证明Google网络请求/缓存状态。脱敏HTTP导出SHA74172bf1c35c82a8ac4ad2e6f9b2e32498e5cf9122cd2ec4acb73dc32ef23dcf。

九个完整小时汇总（调用/链接/CAPTCHA仍是Web客户端审计口径，全来源组正文包含News/Trends/RSS，辅助历史调用不能回填）：

|配置|Web客户端审计调用|Web新链接|明确Web CAPTCHA|合格首次正文|
|---|---:|---:|---:|---:|
|curl-p1-s3|313|0|0|0|
|browser-p1-s3|258|0|0|0|
|wml-p1-s3|158|0|0|0|
|openserp-p1-s3|838|6225|26|3161|
|openserp-p1-s6|883|6341|10|3222|
|openserp-p11-s3|829|4505|23|1936|
|openserp-google-all-s3|248|1579|8|12475|
|browser-v3-p1-s3|245|0|0|0|
|openserp-fallback-v1-p1-s3|917|5305|12|2626|

服务器已真实生成selection.json：本轮暂优为全Google来源1页3槽12475篇，纯Web暂优为1页6槽3222篇。尚未证明系统上限或实测日产量，顺序样本仍受时间/关键词/代理历史/去重基线影响；旧协议正文一致性问题与辅助证据缺口未因选优消失。只读归档11:04:43 Result=success/ExecMainStatus0，包含9个完整有效实验，continuous完整小时0。

continuous-best真实Docker启动UTC11:02:08.720572721Z（新加坡19:02:08），新ID1cf4a9bb7e474cb4bb2a9ad4746b2d4bde63887d42cc4291e0a9791a9f55405b、PID3159536、重启0。[12项启动核验](../state/google-hourly-20261009-evidence/continuous-best-start-check.json)全部通过：全Google来源、仅OpenSERP provider、1页3槽、正文≥500、每代理90秒、Google News RSS限定、原31个冻结源码SHA及实际导入/只读挂载匹配。首次检查的RSS断言用了dict，实际Config规范化为二元列表；该观察器错误已纠正并保留首次失败文件，没有修改生产Config或重启worker。首完整小时截止UTC12:02:08.720572（新加坡20:02:08），至少60秒提交等待且实际边界后Docker核验通过后才能归档。

[9小时升级门禁](../state/google-hourly-20261009-evidence/revision-transition-gate-nine-hours-live-check.json)9个准确非重叠小时及SHA对应独立SQL均通过；ready=false，旧连续worker和监督器仍运行、未收完连续小时。首次远端命令因待部署gate目录尚未上传而失败，之后已创建专用目录、上传真实gate源码/9份输入再实际核验；不能把初次失败当作门禁通过。门禁目录不提供自动停止或迁移。监督器11:05:38仍MainPID2669763 active/NRestarts1、失败摘要仍1条，旧控制器无新增失败。

首连续小时只读观察器session32464仍运行，证据continuous-best-first-hour-readonly-observation.jsonl；其观察到完整归档后才结束，不因低产出或观察超时停止采集。若错过完整边界后120秒的正常停止窗口，应继续收完下一完整小时，不能中途切换。新google-hourly-20261009-audited-wd-v1包已上传但没有启用；后续真正停止旧writer/监督器、退役旧run campaigns、schema/history迁移及新3组独立小时仍待实施，然后继续24小时实际产量验证。目标active。


## 完整小时后的升级执行程序与关键词切片修订（10-09 19:42）

本轮起始UTC11:08再次核验真实连续容器，原ID/PID3159536/StartedAt/重启0及31个冻结源码匹配，未中断或重新开始该小时。上一轮第九组完整审计及管理加载路径修复属于实际进展，本轮继续实施尚未完成的生产修复升级流程。

新增[scripts/transition_google_revision.py](../scripts/transition_google_revision.py)和[scripts/google_revision_migrate.py](../scripts/google_revision_migrate.py)。默认CLI只读核验；--wait --apply是单次执行：核验9个准确小时/独立SQL和冻结输入，等连续小时边界后60秒归档及独立9项SQL通过，再正常停止旧监督器/worker并重查gate，备份数据库（server-only0600/custom archive目录校验），停止旧web、确认无旧数据库客户端或无关active campaigns，退役仅原run任务，再初始化新schema/history。新协议初始化前后对全pages的id/存储hash/实际正文hash有序摘要比对，不修改或猜测合并旧正文；校验首次审计数量、留存原始/存储hash覆盖和冷只读constructor。随后用冻结代码override启动web，实际冷只读加载路径通过后安装新系列及归档/资源timer。失败记录类型/阶段，不打印命令原始输出、凭据或正文，不自动重试；已有mutation-started证据时拒绝自动重复部署。门禁绿色快照仍不是锁，实际阶段立即重查。

边界最少60秒提交等待，预留20秒关闭余量；错过可正常停止的完整边界就等下一完整小时，不在之后的半小时停机。最初14项边界/门禁测试通过，后加入备份失败不得迁移/启动新系列。[最终19项验证](../state/google-hourly-20261009-evidence/local-transition-postgresql-lab.json)全部通过/0跳过，其中4项是真实隔离PostgreSQL17/独立Python迁移进程：旧正文/hash摘要/首次审计保持不变，保留旧已知异常并回填可留存hash，新URL命中当前历史正文被去重；旧数据库客户端、无关active任务或错误源码身份均在任何变更前拒绝。首次测试夹具未建立原campaign/page关联，重复正文正确去重但新增关联为true，导致一个断言失败；已补齐实际旧关联重跑通过，原失败证据保留，不修改生产代码掩盖。临时数据库停止清理，生产连接/外部HTTP0。该小型隔离验证不证明完整生产迁移或小时资源容量已经通过。

只读unit核验发现本项目两项旧采集服务inactive但enabled（optimization/world-11pages），三项旧finish timer已过期但仍active，NextElapse=infinity，没有证据它们即将中断当前小时。确认WorkingDirectory和源文件SHA均归属唯一生产项目后，已备份原unit并disable --now这5项；[4项交接核验](../state/google-hourly-20261009-evidence/legacy-project-controls-retirement.json)全部通过，当前worker和监督器原PID/StartedAt/重启计数及enabled状态均不变。作用是清理过期控制与防止重启恢复旧采集，不把它声称为修复正在发生的截止中断。

[只读数据库备份预检](../state/google-hourly-20261009-evidence/transition-db-size-preflight.json)：总数据库2027026099字节、pages1127546880字节；磁盘此前约28.59GiB空闲。1408个active campaign全部属于当前continuous，其他active0。真正备份和schema迁移尚未开始。

### 待启用系列避免关键词重复

早先待启用计划用0/15000/30000起点，而当前continuous本身是15000，实际初始1408个campaign×8词=11264词，无法继续称其避免直接重复。读取实际run cursor/既有审计及Web/News缓存、保留SHA69aa3e0883bce69d7a2e8027042fd0dd873ca1f6cea42a9ce4632aa245cea7f9的同一120000词库，未提交任何Google查询。[只读切片检查](../state/google-hourly-20261009-evidence/keyword-catalog-unused-spans-preflight.json)34136个留存请求/缓存query hash中，4195个命中当前词库；另保留当前continuous15000..29999整片。选用如下未命中留存Web请求/Web或News缓存的连续区间起点：

|配置|实际起点|对应未命中区间|
|---|---:|---|
|全Google来源3槽|90248|90248..104999|
|全Google来源6槽|30532|30532..44999|
|纯Google Web6槽|45869|45869..59999|
|随后持续采集|105894|105894..119999|

各区间最少14106词，覆盖初始11264及已完成小时最高1480×8=11840的生成量；不是对未来全部生成量或已丢失辅助历史的完整保证。辅助旧请求若没有缓存/不可变事件仍未知，不把此检查伪称历史请求齐全，也不能消除顺序时段/代理健康/不同关键词对产量的影响。controller允许实验显式keyword_offset；continuous使用另一个声明起点，不重复获胜小时起点，旧默认逻辑兼容。35项针对性测试全部通过，包括实际stdin冻结导入和显式起点/continuous/非法起点检查；未改变当前旧连续配置。

新包state/google-next-series/google-hourly-20261009-audited-fresh-v2已上传生产同名独立目录；runtime仍body-aux-schema-v2，34源码和全部75个声明Python文件SHA通过。此前audited/audited-wd-v1包均保留，未静默替换当前采集源码。冻结部署输入包括执行/迁移/门禁/独立SQL/切片证据及series/unit文件，共12项。待启用主服务另显式修订boot-persistence-v1，补[Install]WantedBy=multi-user.target；原unit留在unit-revisions/boot-persistence-v1，SHA从86bcec50389fb3ed6c3dfccf59c300cd8ac2387dc41cc07a8494ba36538b5aae变为53a9ca43bfff08961dc631dbdb8dec906820db44d815a43cd02ba7c80ecb4627。[服务器systemd-analyze verify](../state/google-hourly-20261009-evidence/new-supervisor-boot-persistence-server-revision.json)返回0，75个采集/管理Python文件及来源/槽/起点未因unit修订改变；新采集服务尚未安装启用。

### 真实升级等待进程

已启动realtime-google-revision-transition-20261009.service单次后台执行，Restart=no。第一次systemd-run设置TimeoutStartSec=0，实际TimeoutStartUSec=0导致真实Result=timeout/ExecMainStatus15/MainPID0且无阶段事件，mutation-started不存在；这是已确认终止的管理进程，不是观察超时，不是实验停止。修正为TimeoutStartSec=infinity后重新启动该管理任务，[实际启动](../state/google-hourly-20261009-evidence/transition-executor-relaunch-result.json)MainPID3245580/activating/进程存在，原采集PID3159536/重启0保持不变。

[UTC11:39:20实际等待核验](../state/google-hourly-20261009-evidence/transition-executor-live-wait-final-check.json)：管理MainPID3245580真实存活/NRestarts0，最新事件waiting_for_full_boundary，首小时仍截止UTC12:02:08.720572（新加坡20:02:08），最早提交余量后UTC12:03:08.720572才可能进入归档/停机步骤。mutation-started不存在、当前worker仍Running/未暂停/重启0。并发运行CLI被flock拒绝，不能重复执行；其后直接使用只读check函数验证12项冻结输入/75源码及9个真实小时/SQL通过，生产修改0，完整ready仍false，未把此当已迁移。

只读continuous观察器session32464仍存活，升级过程不依赖SSH观察会话维持。后续先核验管理真实PID/阶段与Docker，不因空输出或超时重启；如果程序失败，在确认真实终止及mutation阶段后处理，不能盲目重复部署。等待程序只在完整小时/归档/独立SQL及立即重查全部通过后执行升级，当前没有生产schema/history修复，旧正文/hash异常仍在；新3个独立小时、辅助审计覆盖、连续24小时实际产量均待验证，目标active。
