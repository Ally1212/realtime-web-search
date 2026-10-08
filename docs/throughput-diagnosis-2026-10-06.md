# 2026-10-06 产量下降排查

检查对象：服务器 `/home/azureuser/projects/realtime-web-search-openserp`。本次只查询数据库、日志、配置和服务状态，采样运行中 OpenSERP 的 CPU；未修改生产配置、重启服务或恢复任务。时间除特别说明外均为 UTC。

## 结论

本次产量低同时涉及统计口径、搜索通道变化、Google 拦截、自适应降速、OpenSERP 会话淘汰循环卡死及健康检查失效。不能据此判断正文抓取系统的产能上限只有每小时 300 篇。

## 统计口径复核

- 旧 `结论.md` 的纯 Web 1043 篇/小时取自 154 个 mass campaign 的 `fetched=25657`，约 24 小时 35 分钟。`FocusedSpider.parse_page` 在正文提取、长度和相关性检查之前增加 fetched，因而它不是合格新正文数。
- 这 154 个 campaign 的实际关联页面：19468 条关联、18975 个唯一页面、13930 个正文长度至少 500 字符的唯一页面。沿用旧报告约 24h35m 的分母，约 567 篇/小时；这是重新按长度筛选的近似比较，并未重新构建每个历史小时的新增归属。
- 本次报告每小时计数为 307/301/234/0/0/0，求和 842。数据库在整个实验窗口中实际新增、属于实验 campaign、长度至少 500 字符的唯一页面是 **836**。逐小时按 campaign_pages.first_seen 计 distinct page id，随后加总，会把跨小时关联到另一个 campaign 的同一页面重复计入。
- 本次前三小时唯一正文平均约 279 篇/小时；全六小时约 139 篇/小时。
- campaign 的 fetched、页面抓取数、发现 URL 数、搜索返回数量、合格唯一新正文必须分别报告，避免再次混用。

## 历史高产所用通道

- 10 月 1 日 03:00–08:00：WML 3454 次尝试，3409 次成功（包含 empty），32205 个返回 URL，平均请求耗时 2.42 秒。同期全来源唯一新页面 5230，长度至少 500 字符 4218；不能把全来源正文全部归为 WML。
- 10 月 1 日、2 日的 google_web 尝试记录 provider 是 WML，并非本次标准 OpenSERP。
- 10 月 4 日 20:00–10 月 5 日 00:00：20369 个新页面，18325 个至少 500 字符；其中 Google News 20150/18114，Trends 219/211，Web 为零。因此这一段每小时数千篇的来源主要是 News。
- 旧 mass 任务共 154 个，其中 152 个各有 500 个 aliases。新实验是 876 个城市×旅行主题任务、无 aliases；关键词组合、批处理方式、来源均有变化，不是控制变量对照实验。

## 本次搜索瓶颈

实验 2026-10-05 08:45:49.962142–14:45:49.962142：

| 实验小时 | 报告正文数 | 搜索尝试 | 有结果 | 空结果 | CAPTCHA | HTTP 429 | Google timeout | 服务错误 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 307 | 287 | 72 | 0 | 81 | 58 | 36 | 40 |
| 2 | 301 | 238 | 73 | 4 | 68 | 68 | 25 | 0 |
| 3 | 234 | 158 | 46 | 2 | 36 | 33 | 4 | 37 |
| 4 | 0 | 101 | 0 | 0 | 0 | 0 | 0 | 101 |
| 5 | 0 | 63 | 0 | 0 | 0 | 0 | 0 | 63 |
| 6 | 0 | 56 | 0 | 0 | 0 | 0 | 0 | 56 |

- 前三小时 683 次尝试，197 次成功（191 有结果、6 空结果），成功率 28.8%；验证码及 HTTP 429 合计 344 次，占 50.4%。全程只返回 1637 个 URL。
- 前三小时约每小时 64 次有结果搜索；历史 10 月 1 日上述窗口约每小时 682 次成功请求（含 empty）。搜索供给显著下降。
- 前三小时的服务错误还有 77 次，成功请求平均耗时 10.04 秒，不能把这一阶段完全当成健康性能测试。
- 配置 initial_rps=2、max_rps=4，但快照显示实际 source current_rps 在 09:00 为 2、10:00 为 1、11:00 降至 0.25。这是运行时限流状态，调高环境变量并不等于持续达到该速度。
- 4 个 crawler slots、每 spider concurrency=144 保持配置；正文淘汰包括 short_content 123、irrelevant 127、non_html 19、HttpError 198 个事件。这些是事件数，可能含重试，不能当唯一 URL 数相加计算转化率。
- 同一批搜索结果中 novel URL 占比仍高，未见重复搜索结果耗尽成为主因。

## OpenSERP 卡死证据

- 最后一个成功响应日志：11:17:08.353601 UTC；最后一个 Google 限流响应：11:17:11.701307 UTC。新加坡时间约 19:17。
- 之后 257 次搜索均 service_error，涉及 73 个不同代理键，不是仅一个出口失效。
- 全程 service_error：287 次 ReadTimeout（等待 OpenSERP 60 秒）、10 次 RemoteDisconnected（08:50 服务重建时）。
- 检查时 OpenSERP 无 Chromium 子进程，CPU 约占一个核心，OOM=false、RestartCount=0。宿主机约 22GB 可用内存、根盘约 45GB 可用；这些是检查时状态，不能代替历史资源监控。
- 5 秒 perf 采样 121 个样本：LaneStore.evictLRULocked 53、map iterator 54、time.Time.Before 7、mapIterNext 5、hash/delete 各 1。热点全部位于会话 LRU 淘汰及其调用，说明这是持续忙循环。
- `/health`、`/ready`、`/stats/cache`、`/stats/cb` 立即返回；`/stats/proxy`、`/stats` 超时。后两者需要读取 LaneStore 锁，与卡住的淘汰循环一致。
- 0.8.12 源码 `LaneStore.evictLRULocked` 在超出上限时持锁反复找最旧键并 delete；该位置与从生产二进制 addr2line 解出的热点一致。
- `/ready` 只检查 draining，pooledBrowserEngine.IsInitialized 固定返回 true，不能证明浏览器或搜索可用。restart: unless-stopped 也不会重启仍然存活的卡死进程。
- **待验证触发原因**：X-Proxy-Session-ID 直接取 Fiber 请求头并存入长期 LaneStore map；Fiber 未开启 Immutable，会话键可能引用随后复用的请求缓冲区。键内容变化可能导致删除失败、持锁无限淘汰。但尚未以生产相同 Go 1.24.6 环境或内存快照验证；本机简单模型未复现，下载对应 Go toolchain 失败。因此只能确认循环卡死位置，不能把此触发假说列为已证实。

## 其他检查及修复顺序

- 旧文档提过 Valkey 时间落后三小时，本次实时读取宿主机 epoch 和 Valkey TIME 秒数一致。实验期间临时 promote 脚本持续移动 delayed campaign，存在绕过正常 next_run_at 的调度问题，但不是已确认的 OpenSERP 忙循环原因。
- 首先修复或绕过会话淘汰循环，并加入不取得进展时退出的保护；若采用克隆请求头或 Immutable，须在相同版本做回归验证。重启只能临时解除当前状态。
- 用低频真实搜索探测、连续超时率和正文增量监测服务；确认故障后自动恢复并报警。不能只查 /ready。
- 再比较标准 Google Web 的可持续速率：固定关键词、相同唯一正文口径，测试不同并发、代理间隔和冷却配置，同时记录 429 与 CAPTCHA。
- 全部来源与纯标准 Google Web 独立运行，用全窗口唯一合格新正文决定产量；不要把 WML 或 News 的产量用于标准 OpenSERP 的能力结论。

原始 CPU 采样保留在服务器 `/tmp/openserp-diagnosis-20261006.perf`，本地分析材料在 `/tmp/realtime-diagnosis-20261006/`。
