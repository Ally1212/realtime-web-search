# 网站与中台真实数据对账（2026-11-10）

## 结论

本轮完成现有授权可见记录与网站的只读对账；全量同步一致性未通过。网站已有的六次 News / Trends / Images 请求可以完整读取和导出，但在本轮扫描的中台目录中未找到对应请求或 URL 对象。不能据此断言中台其他数据集、权限或时间范围也不存在这些数据。

| 核对范围 | 实际结果 |
| --- | --- |
| 一条 Web 完整快照 | 固定版本完整 payload、摘要及网站完整响应一致，7 条结果 |
| 九条旧链接 | 当前 discovery 中的结果字段与网站对应原始结果一致；Web 5、News 2、Trends 2 |
| 九条旧链接的完整运行载荷 | 固定版本均未返回 platform_payload，不能证明整次请求、所有话题或所有报道完整交付 |
| 旧载荷摘要 | 与 11 月 6 日留存的预期摘要比较，8 条一致，1 条仍不一致 |
| 网站最新六次成功公开请求 | News 85 条、Trends 20 话题 / 60 报道、Images 39 条；目录内对应请求和 URL ID 均未找到 |

## 实际读取与匹配方法

使用既有 SearchFlow Google Readback Consumer Key，通过 ego-browser 读取 Marsyoo `/v1/documents/browse`、固定 `/v1/versions/batch-get`，以及网站 `/api/serp/requests/{id}` 和客户 `/v1/data/requests/{id}?scope=public`。密钥仅从原私有文件读取，未写入证据或输出。

目录限定 social_media_raw，平台过滤 google_search/google_news/google_trends/google_images。分两个有效窗口扫描 2026-10-01 至 2026-10-31、2026-10-31 至本轮当前时间，分别 HTTP 200 返回 9、1 条，无 next_cursor。目录不是冻结快照。9 月窗口返回 HTTP 503，不能算作空数据；最初超过 31 天的窗口返回 HTTP 400，随后按官方窗口限制拆分，未据失败响应得出数据结论。

旧记录全部标为平台 google_search，但 discovery.metadata.source / request_metadata.source 能区分 Web、News、Trends。本轮按真实 source 选择原响应列表、匹配原始链接，比较整个结果对象，仅排除本地统计字段 is_new_url。九条均唯一匹配、其他字段一致。当前 discovery 是文档当前元数据，不冒充固定版本原始载荷。

网站六个最新样本沿用 `docs/website-other-data-validation-2026-11-10.md` 的固定请求 ID。将每次请求 ID 和所有 occurrence 的 URL ID 与目录对象比较，均未匹配；另按每个请求第一条 URL 作精确查询，六条均最终 HTTP 200 返回空列表。两次图片精确查询初次 429，按已确认的每秒 1 次限制间隔重试后成功。已知 Web 对象的 external ID 精确查询曾返回 503，因此不将精确查询作为唯一依据，也不把失败查询算成不存在。

## 明确的未完成项

1. 完整 News / Trends 请求在本轮目录中没有对应固定快照；旧单链接对象不能补足整次列表和父子关系。这需要正式平台完整运行合同及实际同步后再验收，不能通过更改本地状态解决。
2. 应用访问限制页面明确只允许 Google 搜索、Google 新闻、Google 趋势，未列 Google Images；字段为身份、标题、元数据、平台原始字段、指标、历史快照，正文和图片附件未开启。因此图片查询只能说明当前可读范围内没有匹配，不能证明平台不存在图片数据。未扩权或更换凭据。
3. `doc_ecfebc6817d2a9bb3de07d049a76a949` / `ver_59cb5c941fd025cb9cf99594b489206e` 的 raw_payload_hash 与历史预期仍不一致。其当前结果字段虽与网站一致，但缺少固定入站载荷，无法据此解释或改写历史摘要。须取得该固定版本的入站载荷或平台事件审计再定位；保留原异常，不改旧 queued。

## 证据与操作边界

`state/website-middle-reconciliation-20261110/reconciliation.json` 保存逐文档和逐请求判断，目录内留存真实目录、固定版本、网站响应及只读权限页面。验证包含固定版本身份、完整 Web payload 摘要和全响应、旧结果逐字段、网站规范化源响应摘要、六请求全 URL ID 匹配。

没有新增采集、上传、注册、权限调整、充值、部署、迁移或开关修改。本轮没有发现需要修改网站数据或读取代码的差异，不能通过重写源数据制造一致。浏览器独立空间 127 在完成后清理，其他任务空间不操作。
