# 纯 Google Web 长时链接采集选优

用户明确授权持续在生产服务器采集并优化到 **2026-10-11 上午 09:00（Asia/Singapore，UTC 01:00）**。UTC 2026-10-10 09:44:01 经过 worker 重启屏障后开始纯来源基线；不是正文实验，也不恢复此前正文/hash 迁移。

仅使用 OpenSERP 获取 google_web 的自然搜索链接和元数据。News、Trends、RSS 关闭，正文保持 false。网页关闭自动采集可以中止实验；监督器不会覆盖用户关闭操作。既有三来源数据和 URL 历史完整保留，网站 24 小时总览包含此前三来源数据，不能直接用总览数字评判本轮纯 Web。

主指标是成功持久化、规范化并对全部留存历史去重后的新增唯一 URL / 实际墙钟小时数。度量从 serp_urls 的首次记录关联 serp_requests 来源获取，同时记录请求成功/失败、验证码、429、供给 cursor、队列和资源。等待、冷却、失败、重试、切换时间计入；先排空在途请求再结束阶段，下一阶段从上一阶段截止时间起算，包含重启耗时。已完成阶段原计划 90 分钟，UTC 15:16 后当前及后续阶段改为 75 分钟，以覆盖更多候选并保留截止前长时复验；同配置重复阶段合计新增链接/合计小时，禁止挑最佳小时或只计活跃时间。

按顺序测量：

| 配置 | 任务槽 | 每词页数 | 代理最小间隔（秒） | 起始/最大 RPS | 代码 |
|---|---:|---:|---:|---:|---|
| 基线 | 6 | 1 | 90 | 1 / 2 | 网站 r3 |
| 队列/速率修复 | 6 | 1 | 90 | 1 / 2 | 独立冻结优化 worker |
| 增加并发 | 12 | 1 | 90 | 1 / 2 | 同上 |
| 增加并发 | 18 | 1 | 90 | 1 / 2 | 同上 |
| 增加搜索深度 | 12 | 2 | 90 | 1 / 2 | 同上 |
| 降低请求间隔 | 18 | 1 | 60 | 2 / 4 | 同上 |
| 更高并发 | 24 | 1 | 45 | 2 / 4 | 同上 |
| 复测基线 | 6 | 1 | 90 | 1 / 2 | 同上 |
| 补测中等并发 | 8 | 1 | 90 | 1 / 2 | 同上 |
| 补测低并发 | 4 | 1 | 90 | 1 / 2 | 同上 |

最终剩余时间复验当前最佳配置，到截止时间重新按全部合格样本加权选择并固定已测最佳配置，继续纯 Web 采集。若没有合格样本，明确报告未知，不宣称最优。

比较限制：不同阶段依次使用新关键词片段，时间、关键词构成、历史去重饱和度、继承的代理健康和上游波动会影响结果。结果只能说明本轮已测配置中的实测表现，不能证明绝对系统上限或把差异全部归因于某一代码改动。

代码修复：已停止/暂停任务从延迟队列出队后不再无限重排；仍处于 active 但全局自动开关关闭的任务保留可恢复调度。SERP 创建调度回调时传入配置的 maximum_rps，消除调度器默认 2 RPS 导致配置上限不完整生效的问题。保留全部验证码、限速、代理隔离和自适应状态。

监督器以 systemd 独立运行，正常阶段切换范围为当前 compose 项目的 local-worker；web、postgres、valkey、OpenSERP、proxy-relay 不随实验切换。已发现故障的单独维护另行记录，服务器其他项目不切换、不重启。重启用既有镜像和冻结 Python 代码，沿用 worker 的 SERP/工作台幂等初始化，不执行旧正文/hash 迁移。配置变更通过 updated_at 比对及行锁，用户在切换期间操作开关时不会被恢复逻辑覆盖。

记录目录：state/google-web-longrun-20261010。manifest.json 保存范围与配置；heartbeat.json / state.json 为当前状态；observations.jsonl 为连续观测；result-*.json、ranking.json、final.json 为阶段、加权排名和截止报告。源码 scripts/google_web_longrun.py；冻结优化代码在该目录 code/realtime；原 compose/systemd 文件保留。初期基线 worker 使用 r3，后续监督器才部署已验证的优化包。

验证：核心改动全量回归 386 passed、6 skipped、38 subtests；冻结代码 115 passed、22 subtests。后续新增真实 PostgreSQL 监督快照测试与操作员停止并发保护测试，相关测试通过。跳过项是未配置隔离 Redis 的集成测试；生产真实 Valkey 调度持续观测。启动阶段尚无完整一小时选优结果。

启动修正：UTC 09:30 的来源设置切换仍留有旧三来源批次，初始窗口出现 News/Trends，不用于纯 Web 比较，原数据保留在 initial-contaminated-window.json。暂停并更换 worker、确认启动配置与数据库均为 google_web 后，于 UTC 09:44:01 重新锚定基线。监督器新增实际请求来源检查，后续发现非 Web 请求会停止实验，而不仅依靠设置字段判断。逐小时记录包含零产出时段，并注明首尾不足一小时的实际秒数。

纯来源复核：UTC 09:48:37 的独立数据库快照确认 62 次请求均为 google_web、均成功，新链接 111；正文基线未变化，256 个活动供给任务均仅使用 google_web。证据 pure-baseline-confirmed.json。UTC 09:50:58 的监督快照累计 98 次成功请求、新链接 227，采集和监督服务均 active / enabled。这些是启动核验数据，不能替代完整小时及长时选优结果。

基线瓶颈诊断（UTC 09:54）：成功搜索平均约 24 秒，P95 约 41 秒；6 个运行中任务槽，准备队列包含 471 个 stopped 任务。后续优化代码将清理出队的无效任务，而不是反复延迟重排。当前查询尚未带 after 日期过滤，供给下一关键词索引约 99,304 / 120,000；跨目录周期后查询构成会变化，需单独注明，不能把日期过滤效果归因于并发。只读诊断脚本及证据存于本轮 state 目录。

## 首组完整长时结果（UTC 11:14:45）

| 配置 | 完整时长 | 新增唯一链接 | 按完整时长平均每小时 | 请求成功率 |
|---|---:|---:|---:|---:|
| r3 / 6 槽 / 1 页 / 90 秒 / 最大 2 RPS | 5,443.435695 秒 | 9,913 | 6,555.93 | 1,799 / 1,852 = 97.14% |

该段来源及配置核验通过，正文基线不变，无 worker 重启或 OOM。UTC 10:00–11:00 单个自然小时新增 7,340；选优采用包含首尾不足小时的整段平均 6,555.93，不采用单小时峰值。证据 result-02-baseline-r3-s6-p1.json。数字已按 google-tracking-v2 重算，修正前 10,024 / 6,629.34 及小时数 7,421 作废，原始结果留档。

此 r3 版本存在已确认的停止任务无限重排缺陷，数量和时长作为控制基线保留；最终长期候选均使用已修复版本，见下述 queue_safe 门槛。

第二组在 UTC 11:14:45 起算，包含排空、worker 重建与恢复等待。实际 worker 已挂载本轮冻结优化代码，网站继续 r3。UTC 11:41 的只读诊断确认第二组没有 after 日期过滤，平均成功请求约 18 秒；队列仍有继承的 stopped 条目等待自然出队，不手动清空来美化本组产能。

UTC 11:37 本机 SSH 观察连接被关闭；重新连接确认监督进程 MainPID 2572469 持续存活，阶段已自动切换且没有服务重启记录。这是观察连接中断，不作为生产停机证据，也未据此重启 worker。网站隧道重新建立，API 确认 pure Web / body false。

队列修复线上抽样（UTC 11:57–12:03）：只读跟踪接近 FIFO 出队位置的 5 条 stopped 任务。UTC 11:58:24 已有 4 条从 ready、delayed、queued marker 中消失；UTC 11:59:55 复核这 4 条没有重新排队；UTC 12:01:15 第 5 条也已消失；UTC 12:03:28 再次确认全部 5 条仍不存在于 ready、delayed 和 queued marker。没有手动删除队列条目或改任务状态。证据 queue-probe-ids.json、queue-probe-observations.jsonl；这是所跟踪样本的行为验证，不代表所有旧队列条目已清理，也不代替产能对照。

第二组整小时检查（UTC 12:15:23）：从 UTC 11:14:45 起算 3,638.046690 秒，按固定原截止时间重算后新增 6,007，平均 5,944.18 / 小时；1,044 / 1,067 次请求成功，成功率 97.84%。来源、实际配置、正文基线及 worker 稳定性检查均通过。修正前 6,063 新增 / 5,999.59 每小时作废并留档；证据 phase1-hour-checkpoint.json，明确标记 checkpoint_only / not_added_to_ranking，不将中间快照与整段结果重复累计。不能宣称代码修复提升了产量，也不能把不同关键词和时间的差异全部归因于代码。

## 跟踪参数修正与维护（UTC 12:29–13:01）

Google 自然结果中的 srsltid 仅为跟踪参数，旧规范化没有移除，导致部分变体重复计新链接。google-tracking-v2 移除此参数（含大小写和重复键），继续保留 page、article、ref 等内容参数；SERP schema 仍为 v1，原链接、标题、摘要等观测字段不变。

正式修正前先关闭自动采集并排空请求，进行 dry run 后 apply；所有旧 URL 主键保留，11,150 条旧变体标记为别名，新增规范代表行 7,532 条，修正 1,344 次请求的派生规范链接和新增标记。URL 原行与原请求 JSON 均有备份。请求总数前后均 18,800；正文始终 332,768 行、max_id 333185、latest UTC 02:30:23.164897。未访问出版社、未清空历史数据。

历史阶段按原起止时间重算，排除别名；原 state、ranking、result 与小时快照保存在 normalization-v2/original-metrics。规范化代码单独冻结为 baseline、optimized、web 三包，保持各组原 worker 行为，120 个 Python 文件哈希核验通过。回归 393 passed、6 skipped、38 subtests；最后针对性及优化冻结包均 37 passed、4 subtests。

优化 6 槽组结束于 UTC 13:01:09.813356，完整 6,384.679106 秒新增 7,321，平均 4,127.94 / 小时，1,277 / 1,301 请求成功（98.16%）。维护暂停完整计入该组墙钟时长；这个平均不能用于单独推断 worker 修复的效果。证据 result-03-queue-rate-fix-s6-p1.json。随后进入 12 槽、1 页、90 秒间隔、1 / 2 RPS 阶段，等待完整长时结果。

UTC 13:06 独立复核：最近 25 次请求全部纯 Web 且标记 google-tracking-v2，规范代表 URL 中带 srsltid 参数的数量为零；正文基线完全不变。网站结果 API 可返回最新 5 条真实 Web 数据。证据 normalized-runtime-verification.json。监督服务 MainPID 3117864 持续运行，采集服务和监督服务均设置开机启动。

## 代理中转端口冲突修复（UTC 13:31–13:42）

UTC 13:26 起出现代理连接失败高峰；13:26–13:32:34 的窗口记录 60 次 openserp_proxy_connect / HTTP 502。容器日志确认 proxy-relay 在 reconcile 的 asyncio.start_server 处发生 EADDRINUSE（errno 98），UTC 13:31:38 自动重启。OpenSERP 没有重启。中转监听范围 20000–49999 与容器临时端口范围 32768–60999 重叠，具体占用者未确认。

修复保持端口映射、认证和转发行为：遇到 EADDRINUSE 时保留其余可用监听，冲突端口在下一次刷新重试；权限等其他绑定错误仍明确抛出，不当作正常冲突吞掉。真实占用端口、释放后恢复、其他监听不中断、认证与 HTTP/SOCKS 转发测试，本地 20 passed、5 subtests；相同测试在生产中转容器内运行，4 项通过，无真实 Google 请求或数据写入。

UTC 13:41:42.270882 关闭自动采集并排空请求，备份原文件后只替换生产根目录 realtime/proxy_relay.py，重启该项目 proxy-relay；UTC 13:42:30.060280 通过 settings updated_at CAS 恢复自动采集，暂停 47.789398 秒。worker ID 未变化、无重启或 OOM；正文基线完全不变，纯 Web 来源和实际 12 槽配置保持不变。共享冻结包哈希校验通过。监督服务恢复为 MainPID 3238639。

中转文件新 SHA256 为 71ecabfb671def44daefdcda87b29042a2f5837cf8041ed66d3fe848732ec733；原文件保存在 relay-bind-fix/original-proxy_relay.py，维护与恢复证据为 relay-bind-maintenance-before.json、relay-bind-maintenance-paused.json、relay-bind-resume-confirmed.json。此次修复在 12 槽阶段中途发生，该组保留原起止边界，所有失败、冷却和维护暂停计入墙钟时长，不能仅将该组表现归因于并发差异。

恢复后独立快照（UTC 13:45:20）：35 次新搜索全部为 results，新增 186 个历史去重链接，无错误组；首条完成于 UTC 13:43:20。证据 relay-fix-post-resume-diagnostics.json。这是恢复确认，不作为独立选优窗口。

进一步检查生产标准库并用真实长连接复现：Python 3.12+ 的 Server.wait_closed 会等待旧监听已接受的所有连接结束。旧实现在刷新中逐个等待，会推迟新监听发布。回归测试在旧代码上超时失败；修正后关闭旧监听立即创建新监听，旧隧道仍能传输字节，测试通过。相关本地 21 passed、5 subtests；生产中转容器内 5 项测试通过。

此补充修正于 UTC 13:52:30.330664–13:53:23.814151 排空并部署，暂停 53.483487 秒完整计入同一 12 槽组。worker 未重建，来源、正文基线及冻结包不变；监督服务恢复为 MainPID 3270956。当前中转文件 SHA256 cc8f8c78987c1ff2e4804e3d81b7e179a126d14f73ff6763f2b87f25e3bd54a4，维护证据 relay-refresh-maintenance-before.json、relay-refresh-maintenance-paused.json、relay-refresh-resume-confirmed.json，前版备份 relay-refresh-fix/original-proxy_relay.py。

## 长期候选的队列稳定性门槛（UTC 14:00）

核验生产冻结 baseline/realtime/worker.py：r3 在检查任务状态前调用 console.runnable，停止任务返回 false 后仍被 schedule(30)，导致永久重排。优化版本先检查 active 状态，线上已验证 stopped 样本出队后没有重新排队。为保证最终长期运行不恢复已知缺陷，result 增加 queue_safe，best 仅从 verified optimized 版本选择；旧 r3 数量、时长、请求和逐小时数据全部保留为控制基线。

原资格标记和排名归档 queue-safety-gate/original-metrics，派生结果保留 eligibility_before_queue_safety_gate。排序防回归测试覆盖旧版本高产量、旧 eligible=true 且无 queue_safe 字段的记录，均不能恢复旧 worker；5 项选优测试通过。监督器更新未暂停采集、未重建 worker、未修改参数或计时边界；MainPID 3292255，证据 queue-safety-policy-applied.json。

12 槽组小时快照（UTC 14:02:19.829493）：从原起点 UTC 13:01:09.813356 起算 3,670.016137 秒，新增 5,306 个唯一链接，平均 5,204.77 / 小时；1,054 / 1,130 次请求成功（93.27%）。纯 Web、正文基线、实际配置、queue_safe 和 worker 稳定性检查通过。所有代理故障、冷却和两次中转维护已计入。phase2-hour-checkpoint.json 标记 checkpoint_only / not_added_to_ranking；阶段继续至完整 90 分钟，避免重复累计中间观察。

## 12 槽完整结果与扩大候选（UTC 14:31–15:16）

12 槽、1 页、90 秒间隔、1 / 2 RPS，完整 5,436.551812 秒新增 8,451，平均 5,596.12 / 小时；1,624 / 1,709 请求成功（95.03%）。纯来源、配置、queue_safe、worker 稳定性和正文基线检查通过；两次中转维护与全部故障已包含，成为当前已完成的长期候选最高平均。证据 result-04-s12-p1.json，不能用其前后部分时段替代整段平均。

UTC 14:31:46.365168 进入 18 槽组，排空、worker 重建和恢复时间计入该组；实际配置 18 槽、1 页、90 秒间隔、1 / 2 RPS，仅 OpenSERP，正文 false。worker ID 3ab4c517b65c5adbeb4d71d4c9c3708f10747f33011410100a039997b91b0adf。

12 槽阶段末诊断（UTC 14:25）：成功搜索平均 36.50 秒、P95 52.43 秒；首组 6 槽对照完整平均 17.50 秒。时间、代理故障、维护和查询构成共同影响比较，尚不能判定并发的独立因果效果。关键词 next index 103512 / 120000，无 after 日期过滤，目录尚未跨周期。证据 phase2-near-end-diagnostics.json。

为覆盖 4 / 6 / 8 / 12 / 18 / 24 槽，保留原全部高并发和两页测试，追加 8 槽、4 槽组；UTC 15:16:56 将当前及后续阶段计划改为 4,500 秒（75 分钟），每组仍覆盖完整一小时，再用截止前剩余时间复验最佳。所有已有数量和时长、18 槽原起点、截止 UTC 01:00 均不变，资格仍要求纯来源、queue_safe、至少一小时、成功率至少 85% 和稳定 worker。原计划、manifest、state 和监督器备份 adaptive-plan/original-*；两个新增配置通过 Compose 校验，5 项选优测试通过。

此计划更新未暂停采集、未重建 worker、未修改当前参数或正文；冻结包哈希校验通过，监督服务 MainPID 3517043。证据 adaptive-plan-applied.json。UTC 15:16:57 的 18 槽观察仅运行 2,711 秒，尚不足一小时，不加入正式排名。

18 槽组小时检查（UTC 15:32:02.593049）：完整 3,616.227881 秒新增 6,950 个唯一链接，平均 6,918.81 / 小时；1,347 / 1,363 次请求成功（98.83%）。纯 Web / OpenSERP、实际配置、queue_safe、worker 稳定性和正文基线检查均通过；没有 worker 重启或 OOM。证据 phase3-hour-checkpoint.json 标记 checkpoint_only / not_added_to_ranking，继续完整 75 分钟阶段，不能将这个快照与最终结果重复累计。

UTC 15:27:54 阶段诊断：成功返回结果的搜索平均耗时 47.25 秒、P95 54.34 秒；关键词 next index 105072 / 120000，cycle 0，无 after 日期过滤。当前查询耗时比 12 槽组长，但尚不能把差异全部归因于并发。UTC 15:28 的资源抽样中本项目 OpenSERP 使用约 306% CPU、5.61 GiB 内存，worker 约 106 MiB；这是瞬时抽样，不能当作整段资源平均。证据 phase3-diagnostics-1528.json、phase3-resources-1528.txt。没有为采样重启或调整服务，后续继续测试低并发以比较实际平均产能。
