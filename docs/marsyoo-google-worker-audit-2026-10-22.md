# Google 与 Marsyoo 对接只读核查（2026-10-22）

## 最新更正与实测进展

后续浏览器完整加载并切换全部状态后，历史 Web 样本文档已查到，document/version ID 与历史回执一致。先前“未查到”是当次页面观察结果，不应推断未入库。默认“有效”状态筛选会排除 Google 的 published 状态，应使用全部状态。

2026-10-22 用户授权后新增有界平台任务 `ctask_61e69eb08a4db672ed2681477d34ea53`：worker 实际领取，1 次非缓存 Google 请求成功、返回 10 条自然结果、3 个历史去重新 URL；按任务上限上传 3 条，平台 succeeded。新文档逐条浏览器验收继续记录在后续章节。领取与上传已恢复 false，自动采集与正文始终 false。

## 初次只读核查结论（历史记录）

已实现并部署 Google SERP 链接采集器对接，平台也有对应注册记录；当前领取与上传均关闭，没有验证本日新任务的端到端执行。保留用户暂停状态，未创建平台任务，未启用自动采集、正文、上传或领取。

## 本次核验方式

- 通过浏览器自动化操作用户已登录的 Chrome：打开采集器实例、按 Google 搜索筛选、刷新实例、打开一次性任务表单、切换平台后检查字段，关闭表单未提交。
- 通过 SSH 读取服务器 `20.106.103.139` 的生产 GET API，并检查实际 worker 容器配置和运行中的源码。
- 浏览器内容中心用真实 Web 上传回执 document ID 精确检索，时间范围 2026-09-22 至 2026-10-22，状态先有效、再全部，未取得匹配记录。数据集和平台未限制。
- 本次是只读验证和表单检查，不是新任务采集、上传、业务入库的全链路验收。

## 真实状态

| 项目 | 读取结果 |
| --- | --- |
| 平台采集器 | `collector-google_search-prod-c0fc2624` |
| 名称 | Google SERP Link Collector |
| 平台记录状态 | active；不能据此认定当前在线 |
| 最后心跳 | 2026-10-11 08:15:31 UTC，浏览器显示 16:15:31 |
| 负载 | 0/1 |
| 声明能力 | identity、title |
| 允许任务类型 | keyword_search |
| 数据集 | social_media_raw |
| 平台来源 | google_search |
| worker 对接代码 | whale_serp_enabled=true |
| Collector Key | 已配置；未读取或输出值 |
| 接口地址 | http://20.169.21.11 |
| uploads_enabled | false |
| claims_enabled | false |
| continuous_enabled | false |
| content_fetch_enabled | false |

实际 worker 环境中的 `whale_agent_id` 为 `collector-website-prod-01`；注册客户端会使用平台注册响应中的 agent_id，运行记录保存的是上表 Google ID，两者用途不同。

生产 `_tick()` 在两个开关均关闭、且无待处理平台任务或通知时直接返回，不注册、不发心跳、不 claim。与 10 月 11 日停止更新的运行记录一致。

## 如何连接

1. 管理员在 Marsyoo 创建任务或配置周期任务，指定 dataset、source_platform、task_type、required_capabilities 和 payload。
2. Azure 上 local-worker 内的 WhaleSerpRuntime 使用 Collector Key 向平台注册、发心跳并主动 claim。Marsyoo 页面也明确说明平台不主动连接采集器。
3. worker 校验任务授权和参数，持久化本地任务、平台任务 ID 与 campaign 关联。
4. SERP worker 根据来源执行 Google Web / Google News RSS / Google Trending Now RSS，保存请求与结构化结果，规范化 URL 并按历史去重。
5. 本地持久 outbox 保存待上传消息。消息携带原查询、来源、摘要、排名等元数据；平台任务消息携带 source.collection_task_id。
6. uploads_enabled 开启时向 `/v1/documents/bulk` 上传。保存逐条回执与失败原因，临时失败退避重试，稳定消息身份避免重试重复发送。
7. 平台任务通过 heartbeat 上报进度和接收暂停/恢复/取消信号；完成或失败调用对应接口。平台收件之后的业务存储、索引等处理需另行查验。

注册/心跳：`/v1/collectors/register`、`/v1/collectors/heartbeat`。
领取：`/v1/collection/tasks/claim`。
任务控制：`/v1/collection/tasks/{id}/heartbeat|complete|fail`。

本机 8093 工作台只是管理和查看入口。worker 主动向平台发送请求，所以它对接 Marsyoo 不依赖把工作台 8093 暴露到公网。

另有两条入口：网站创建手动采集任务时勾选同步到 Whale，或把本地已存结果筛选加入上传队列。这两种本地任务不等于 Marsyoo 下发的平台任务。

## 真实回执与边界

当前 4 个本地上传 job：3 个 succeeded、1 个 stopped；outbox 共 3 条 queued。三个成功 job 各引用 Web、News、Trends 一条消息，不将共享消息额外相加。

Web 样例：

- job：`f1a0b5ea-e091-4090-8cae-fbc19983b092`
- document：`doc_ecfebc6817d2a9bb3de07d049a76a949`
- version：`ver_59cb5c941fd025cb9cf99594b489206e`
- state：queued；attempts=1；last_error=null；outbox_event_created=false。
- 本日内容中心按该文档 ID 未查到匹配。不能凭此确定数据丢失原因，也不能证明所有 Google 数据均未入库；尚需核对平台底层存储、旧接口地址与域名路由，以及该 ID 的处理记录。

本地 succeeded 表示上传流程完成，queued 表示接口收件，不等于最终入库、标准化、关键词索引或向量化完成。

## 平台表单兼容问题

本日一次性任务表单默认数据集为 recruitment_raw，平台 LinkedIn，JSON 包含 location。只切换 Google 搜索后，原数据集与 JSON 仍保留。Google worker 要求 social_media_raw，不接受 location 参数；不能只切换平台名称就提交默认模板。

当前 Google 任务必须匹配：social_media_raw / google_search / keyword_search；required_capabilities 仅使用 identity、title 或其子集。不能要求正文、评论等未声明能力。

供后续明确授权测试时参考的有界 payload（本次未提交）：

```json
{
  "keyword": "solar energy research",
  "sources": ["google_web"],
  "region": "US",
  "language": "en",
  "max_pages": 1,
  "page_size": 10,
  "max_items": 10,
  "max_duration_minutes": 5
}
```

当前平台来源统一注册为 google_search；Google News/Trends 是 payload.sources 内的采集子来源，不能仅因平台下拉里有 google_news/google_trends 就认为现有 worker 已分别注册这两种平台身份。

历史文档中 backfill/content_detail 的代码能力和隔离测试不代表生产授权；当前实际配置只允许 keyword_search。

## 相关实现与历史文档

- `realtime/worker.py`：启动 WhaleSerpRuntime 后台线程。
- `realtime/whale_serp.py`：注册、领取、任务映射、消息、outbox、回执与控制。
- `realtime/serp_worker.py`：SERP 采集与同步入队。
- `realtime/whale_serp_http.py`：工作台平台状态与上传任务 API。
- `docs/whale-serp-adaptation-2026-10-11.md`：原对接验收及权限边界。

## 用户授权后的完整新任务验收（已通过）

用户明确授权实际浏览器端到端测试。通过已登录 Chrome 原生自动化在 `/admin/collection` 创建任务，未以 API 代替平台建单。

- 平台任务：`ctask_61e69eb08a4db672ed2681477d34ea53`。
- 本地 job：`269a16cd-8afd-4686-a6b1-3801a1c64efe`。
- 本地 campaign：`7a310535-5bac-46c2-8d1f-1eed92fbab58`。
- 参数：`solar energy research Singapore 2026`，US/en，仅 google_web，1 页，每页预算 10，上传上限 3，运行上限 3 分钟。平台最大重试 UI 最小值为 1，本次实际无重试。
- required_capabilities 为 identity/title，已从默认模板取消 body/author，并删除 location。
- UTC 03:17:17 临时开启领取与上传；03:17:22 实际领取；03:17:42 搜索成功；03:17:48 上报完成，平台浏览器显示 succeeded。
- 搜索请求 `467589fe-cb43-44f6-8f0a-884326c5a818`，HTTP 200、OpenSERP、cache_status=null，10 条自然结果、3 个历史去重新 URL。搜索返回条数和上传上限是两个口径；本次仅上传 3 条。
- 3 条均一次上传取得 queued，无拒收、无重复、无 pending；platform_task_id 在消息 source.collection_task_id 中一致，reported=true。
- 浏览器逐条查询下表 document ID，确认标题、原始 URL 和 version ID 与回执一致，均属于 social_media_raw / google_search / search_result，状态 published，无正文。

| 文档 | 版本 | 标题 |
| --- | --- | --- |
| doc_37cc24a2229b00860ca970c80d88179f | ver_b9eededb5b00003ac1d97cd1291bf466 | Solar Energy Research Institute of Singapore (SERIS): Home |
| doc_b015c34158af12f28cddb58a1edf44ff | ver_41c00010ff41e35150907048ca0c70b1 | 20. International Conference on Photovoltaic Solar Energy ... |
| doc_d6fbf1415a7a90785e8dfb24469acaed | ver_974fae6c1a79328923b3a5d927d5ba7f | Solar Energy Research Institute of Singapore |

内容中心默认“有效”筛选不显示这些 published 文档，改为“全部”后逐条查到。本次确认的是内容列表事实源，不扩大为全文索引、向量化或高级模块验收。

测试结束 UTC 03:18:02 已恢复 uploads_enabled=false / claims_enabled=false；continuous_enabled=false、content_fetch_enabled=false，collector settings.updated_at 与原值一致。没有部署代码、修改接口地址、重启容器或开放公网端口。旧 IP 接口在本次测试中实际成功领取 www.marsyoo.com 页面创建的任务，并能从域名内容中心看到新文档；不再把它作为阻断本次流程的未验证问题。

本地证据：`state/marsyoo-e2e-20261022/` 中 baseline.json、latest.json、receipts.json、search-requests.json、after.json、collector-after.json。脚本在 finally 中恢复两开关，进程已正常结束。

结论：本次真实 Google Web keyword_search 的“平台创建 → worker 领取 → 搜索 → 上传 → 平台完成 → 内容中心事实源可见”全部通过；不声称 News/Trends 新任务、backfill/content_detail、取消/重试和索引处理本次全部验收。
