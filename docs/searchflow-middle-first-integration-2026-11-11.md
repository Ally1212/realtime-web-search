# SearchFlow 网站 → Marsyoo → Google → 入库 → 网站下载

## 当前结论（2026-11-11）

Google Web 的网站完整链路已部署并真实验收。快速请求选择 Google 网页后，由服务端使用 Consumer 创建 live query，独立采集运行器领取对应任务，执行 Google 搜索，走 realtime 入库通道，等待 accepted/duplicate，再提交实际 document_ids 完成任务。网站只在 Consumer 固定版本 payload/身份/hash 校验通过后结算成功并开放 CSV/JSONL。

News、Images、Trends 快速请求继续原本地路径，未宣称这些来源已完成实时中台验收。Maps 本轮没有接入。页面无需填写密钥；前置访问门及客户隔离保留。

## 正式网站验收

从 https://searchflow.20-150-145-162.sslip.io/#api 通过 ego-browser 点击「获取数据」，输入 `sodium ion battery electrolyte research 2026`，US/en、Google 网页。

| 身份 | 实际值 |
| --- | --- |
| 网站 job | c23158b6-16ea-467b-96d5-6cf263e6717a |
| live query | lq_1bfa7047567d7c5762036fc218576c6d |
| 平台 collection task | ctask_df071be45591f218b7e6ac2fd22ff54a |
| 私有 campaign | 385df96e-7138-441c-a5a6-601b205d9634 |
| 已存搜索 request | eeaa0a24-76e9-4844-acf4-11f1567194c5 |

一次搜索返回3条链接，上限10不表示必定返回10条。3条均取得 accepted 回执，平台 query succeeded，网站 job succeeded、charged=1、reserved=0。查询完成后未继续采集。

| document | 固定 version |
| --- | --- |
| doc_67970e2bd566be4550790a1f93d611b5 | ver_e4b6d56521d285f8741e7527409fa3e6 |
| doc_2ef49f04a113de93670274ab8e4af168 | ver_5e4a68204b6a57e4727894e7961024bf |
| doc_4a0382b58f953e4a2f6af4b642b61b35 | ver_98ed33aee8a13bbfacca8f50745011f9 |

浏览器实际下载 CSV/JSONL。JSONL 整对象与网站数据完全一致；CSV request.record_json 及3条 result.record_json 深比较完全一致，另保留1条上游富结果。刷新后点击「继续查看进度」恢复完成结果；复用原幂等编号返回相同 job、reused=true；错误 receipt 返回404；从公开记录路由读取本次私有 request 返回404。

证据在 `output/middle-e2e-20261111/`：website-evidence.json（含3个实际 accepted 回执）、website-data.json、website-download.csv/jsonl、download-verification.json、website-safety-checks.json、website-complete.png。凭据没有写入这些文件。

## 运行与恢复

- 独立容器 `searchflow-middle-live-1`，与现采集器同网络和运行配置；没有公网端口，restart=unless-stopped。不重建原采集 worker。
- 程序 `realtime/middle_live_runtime.py`，使用 PostgreSQL 专属会话锁保证单运行器。sf_jobs 中保存上游 query/task 绑定、领取任务必要字段、原事件 payload、持久化回执；提交结果未知沿用原租户幂等键。
- 复用 sf_jobs 月额度、原子预留、成功结算/失败退款与租户归属。不运行历史正文/hash 迁移。
- 私有 campaign、whale_task_runs、options 和 sf_jobs.campaign_id 在同一事务建立；customer_private=true。没有建立 legacy whale_serp_jobs/outbox 项，旧运行器不能处理新中台任务。
- 仅领取 live_query，领取后检查 task ID/live_query ID 与本网站 job 一致；其他应用任务不执行，等待其租约失效。共享 Google Agent 的其他应用请求可能竞争队列，未验证高并发或 SLA。
- 收集期间独立心跳；取消/到期下传中台并停止私有 campaign；本地取消立即退款，远端取消通过持久状态继续同步，在途搜索可完成。单元/隔离数据库验证了绑定、退款、超时与取消逻辑；没有额外生产取消任务验收。
- bulk 使用 `X-Ingest-Class: realtime` 和 acquisition_type=live_query。先查原事件回执再决定提交，重启沿用同事件键。queued 不等于持久化；complete 使用 `result.document_ids`。完成校验400停止盲重试；租约冲突重新核实领取，仍保留原事件。
- 下载数据使用已验证的 Consumer 固定版本快照，不在每次下载时重新采集。每个链接的 platform_payload 为 `searchflow-live-links-1`，绑定任务、query 和完整所选链接记录；不冒充 google-serp-run-envelope-1 或全部分页 SERP。原始完整响应保留本地；富结果仍保持原 SERP 范围。

## 部署范围与回退

采集机 `/home/azureuser/projects/google-workbench-backend/state/middle-live-20261111/` 包含冻结运行代码和 backup。Web 只修改 product.py、customer_data.py、quick_request_http.py；基于线上版本打最小补丁，保留线上与本地既有差异，未覆盖 web.py/serp_page.py。重启 Web 与独立 middle 服务，未重启原五个采集服务。静态前端本轮未改。

Consumer Key 与 runner 环境文件在该项目 state/private 下，600，仅服务端读取，不打印、不提交 Git。环境文件继承原采集器配置；重建中台服务时需保持原采集网络与只读冻结代码挂载。

回退时先把快速请求 provider 路由还原到 openserp，再等待/取消所有中台在途 job，确认 reserved=0，然后停止中台容器并还原 backup 的3个 Web 模块；不得让中台在途 job 被旧协调器当成本地任务处理。

最终持续供给、正文、旧 Whale 上传与领取仍关闭；新独立服务只处理显式网站请求，不启用自动任务或监测。pages=332768，正式网站验收后的请求总数40311；本轮不使用早期历史数冒充部署前快照。

## 验证与历史

隔离 PostgreSQL 回归覆盖租户、配额、幂等、取消绑定、固定版本哈希、身份篡改、queued屏障、实时header、非Web保留路径及原数据API。最终89 passed、68 subtests passed、0 skipped；独立服务重启后running、RestartCount=0，完成任务与下载恢复成功，没有重复建单。

权限修复历史：SearchFlow Google Readback（consumer_3960ff4ef9607b8028514c1d73e3c002）原平台允许 live query，但客户 restriction 关闭；经组织所有者 Portal 仅移除此限制，version1→2，其他权限/额度不变。Google Collector Key 和注册追加 live_query。

此前有界任务 lq_59fe505a27590d085d19cd94945750a8 已用同一 Google Agent 成功采集并持久化3条，Consumer results200。更早首个请求因无 live_query Collector 失败并 canceled，未作成功证据；旧 complete400及 queued 日志是调试历史，不覆盖后续 accepted/succeeded 事实。
