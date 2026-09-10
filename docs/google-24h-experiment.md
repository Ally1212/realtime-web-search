# Google 限定来源的 24 小时实验

只统计 Google 发现、本地历史基线之外、规范化 URL 和正文 SHA-256 去重后的自动有效正文。
不保证这些文章当天发布，不保证与桌面 Google 排名相同，也不保证远端被清空前的历史完整去重。
正文原文和未知发布时间记录均保留本地，不会因 Whale 接收而删除。

## 实现与限制

- `benchmark-google start`：独立 SQLite、进程锁、Google 查询缓存、正文版本和 Outbox。开始后连续 24 小时，不跨午夜重置；重启、暂停、冷却不延长时间。
- 四组公平轮询：原 AI 主题；真实事件查询；从合格结果选取最多 20 个站点各 5 个主题；近一天/七天日期查询。所有查询与分页响应落盘。前 3 页每小时、深页每 6 小时到期，每个查询最多 11 页。
- 不启动旧 `collector`/`local-worker`，不认领 Whale 外部任务，不冲刷旧 Outbox。只复用 PostgreSQL 中的 Google 全局配额和代理健康记录。
- 正文 4 并发、单个请求域名最多 2、独立子进程 65 秒硬截止；robots/public-address/5 MB 保护复用 LiveFetcher。HTML 之外仅保留失败记录，不下载视频字幕。
- 自动质量规则：至少 500 字符、中英文、独立 AI 语境、排除导航/验证/登录页；10 万字符上限的可能截断文本不计主量。相似改写不保证被精确哈希识别。
- 同 URL 在后续 Google 结果中再次出现，距离上次抓取至少 6 小时才复查。相同正文计重复，新版本计更新，已知历史单列，不计新增。
- 视频/PDF 等新抓取后端不在此实验范围。未增加任何付费搜索接口。
- Whale 的 `published_at` 是必填项（预检实测 HTTP 400）。有明确带时区 `datePublished`/`article:published_time` 才投递，其他正文标记 `blocked_missing_publication`。不使用抓取时间伪装发布时间。
- 到期停止新请求，最多 5 分钟收尾；晚于截止的正文和确认量单独报告。10 GiB 本地数据预算、2 GiB 可用空间保护；容量不足停止，不删除旧数据。合并全文不足空间时保留逐篇文件。

## 本次目录

正式状态目录：`state/experiments/daily-rZbymFie`。
正式桌面输出：`/Users/ziheng/Desktop/Google-24h-experiment-rZbymFie`。
已启动：2026-09-09 16:10:03（Asia/Singapore）；截止：2026-09-10 16:10:03，最多另留 5 分钟收尾。
启动后确认四组均有真实搜索记录，原 collector/local-worker 仍停止；87 项自动化测试通过（数据库测试使用独立测试库）。
正式日量尚未完成，运行中读数不能当作 24 小时结果。
两次预检状态目录分别为 `preflight-PdKhSb0P`、`preflight-R6fpdbMq`，都加入正式实验历史基线。
第一轮确认 Whale 发布时间必填；第二轮 19 个 URL、12 条规则有效新正文，3 条 Whale 接收、9 条发布时间未知本地保留。它们不是正式日量。

## 管理命令

### 统一网站

打开 <http://localhost:8091/stats>，默认选择最新正式实验；下拉可查看预检，预检不计正式日量。
旧任务仍在 <http://localhost:8091/stats/legacy>，两类数据不混加；本地关键词采集入口仍为 `/`。

- 实验已持续：从原开始时间计算，含暂停、冷却和离线，到截止或提前结束时冻结。
- 累计在线运行：已记录的 active + google_cooling，加新鲜心跳至当前的估算；不重复计算限速等待，不包含已记录的暂停/离线。不是 CPU 工作时间，心跳补算可能被后续离线记录校正。
- 剩余时间：距原计划截止，不因暂停或重启延长；开始和截止均显示新加坡 UTC+8。

统计每 5 秒更新，计时每秒更新。无心跳或心跳超过 120 秒会显示“心跳失联”，不能据此确认采集器仍在运行。
接口失败保留上次快照并提示错误，不把旧数据伪装成实时零值。
新增有效正文、Whale 接收/幂等重复/待投递/拒收、缺少发布时间分别展示；速度是实际最近一分钟新增，不外推全天。
API：`GET /api/experiments` 获取实验列表，`GET /api/experiments/<目录名>` 获取单次实验统计。
使用 SQLite 只读快照和短缓存；打开统计页不会更改实验状态、心跳、队列或截止时间。
本次接入仅更新网站服务，不重启正式实验。
接入验收：99 项自动化测试通过；真实浏览器确认正式实验计时递增、预检结束计时冻结、
旧入口跳转正常、390 px 手机视口无横向溢出，实验页控制台无错误。

### 命令行

在项目目录执行。以下操作只修改独立实验，不恢复旧任务：

```bash
docker compose exec -T web python -m realtime.cli benchmark-google status --directory /app/state/experiments/daily-rZbymFie
docker compose exec -T web python -m realtime.cli benchmark-google pause --directory /app/state/experiments/daily-rZbymFie
docker compose exec -T web python -m realtime.cli benchmark-google resume --directory /app/state/experiments/daily-rZbymFie
```

`resume` 解除调度暂停，不复活已经被停止的容器；如容器被手动停止，另执行 `docker start realtime-google-daily-rZbymFie`。
恢复沿用原截止时间。完成后的实验不能通过 resume 延长。
实验容器仅在非零退出时最多自动重启 3 次；结束后不重新开始。

输出中的 `README.md` 每分钟更新；`search-pages/` 保留真实查询和有序结果数组，
`documents/` 保留逐次正文/失败/质量记录，`小时统计.md` 保存采样，结束时生成 `全部正文.md`。
SQLite 包含全部分钟采样及各组来源关系。手动完整重导出应在暂停或停止后执行，挂载同一桌面目录到 `/export`，使用 `benchmark-google export --directory ...`。

电脑和 Docker 必须持续运行。休眠/停机不补足采集时间；最终报告会反映可观察到的离线间隔。
不得把 `0.5 RPS × 24 小时` 当作正文数量，也不得把 Whale 接收与本地有效正文混为一项。

## 验收

核对原始 URL/正文哈希、小时累计和最终计数；跨查询重复与跨站同文只计一次。
观察新正文、更新、重复、质量不合格、Whale 接收/拒收/时间未知，分别按来源组归因。
来源组覆盖量可重合，独有量依据最终发现关系计算，不以先抓到者获全部贡献。
首 6 小时与后 18 小时分别报告实际观察速度；首日不能代表长期稳定日增量。
