# Marsyoo 单次完整快照交付与读回（2026-11-06）

用户明确授权仅新增写入一条已有公开 Google Web 请求的完整快照，并验证固定版本；不发新搜索，不启用自动上传或持续采集。本次已完成这条链路，不能据此宣称总需求全部验收。

## 应用与凭据

通过 ego lite 在用户已登录的 Marsyoo 组织创建 `SearchFlow Google Readback`，应用 ID `consumer_3960ff4ef9607b8028514c1d73e3c002`。Consumer Key 仅保存在本机 `~/.config/searchflow/whale-consumer-read-key` 和 Web 服务器 `state/private/whale-consumer-read-key`，权限 600，不记录密钥值。

应用限制为 `social_media_raw`、Google Search/News/Trends 与必要读取字段；关闭导出、实时查询、订阅、分析及 SQL 功能，限制为 1 RPS、burst 2、并发 1、每日查询 100。禁用功能对应的额度最小值为 1，平台不接受 0；没有购买、充值或修改其他应用。

## 一条真实交付

- 已有请求：`05677d68-57e4-4cd0-9e7c-cbeb585a016b`，共 7 条结果。提交前再次核对公开属性及原响应摘要未变。
- 两次 validate 只校验；正式 bulk 仅调用一次，先写入独占创建的提交意图文件，未重试上传。回执从 queued 查询到真实终态 **accepted**，持久化时间 `2026-11-06T03:42:12.895978Z`。
- 文档 `doc_9fb5017c5add01bc17809c40dade7d60`；固定版本 `ver_f79f3f3454858029a9c13759a3e1536a`。
- Consumer 实际读取固定版本，完整平台字段、原始响应、运行标识、摘要及 7 条结果的顺序和字段全部一致；本地读取客户端也通过同一固定版本验证。
- 平台质量标记为 **low**，未修改、掩盖或伪造正文来提升评级。需要后续核查结构化搜索数据的质量准入规则；不能将本次完整性验证当作正式全需求 ReaderAdmission 验收。

`google_serp_delivery.py` 使用 `google-serp-run-envelope-1`，原始已存响应与规范化 observation 一起进入 `content.platform_payload`。外部身份为 `serp-run:<request UUID>`，每次运行独立，同次重试稳定。标题明确表示查询词，缺失的观察时间保留未知，不伪造出版时间、正文或出版者 URL。当前 builder 仅支持公开 Google Web 保存请求，没有接入自动批量生产。

## 历史样本与网站

旧上传 9 条的文档及固定版本均可读，当前元数据均匹配；源摘要与版本摘要 8 条匹配、1 条待对账。旧固定版本均没有完整 platform_payload，不能用当前元数据替代固定版本证明。旧回执查询均为 unknown，不代表失败，也没有改写旧队列为 accepted。

新 `google_delivery.py` 读取受限大小、严格校验的缓存证明，并实时排除客户私有请求；过期、损坏或未来时间会标记或拒绝。`/v1/google/legacy-delivery` 及历史接口展示这一状态，读取不会访问平台、上传或迁移数据库。页面显示“7/7 条已读回（平台质量标记：low）；旧样本 1 条待对账”。

浏览器验证：已认证接口及历史接口 200、匿名接口 401、正式 HTML 的状态函数与本地一致。这里只验证接口和已发布状态文案，没有宣称本轮进行了完整手机视觉验收。

## 发布与验证

Web 发布数据读取、交付构造、HTTP 和状态文案模块；未覆盖 `web.py`、`serp_page.py`。采集机仅更新 `whale_serp.py`，读取显式 HTTPS 端点配置并拒绝重定向；有效地址 `https://www.marsyoo.com`。没有运行旧正文/hash 迁移。Web/prd 与 local-worker 重启，采集机其他四服务未重建。

两机备份：各自项目下 `state/marsyoo-readback-20261106/backup-{web,worker}`；Web 第二次发布备份 `backup-web-r2`。平台端点配置位于 `state/private/whale-api-endpoint.json`，证明缓存位于 Web 的 `state/private/google-whale-readback-proof.json`；证明只代表其记录的验证时间，默认 24 小时后标记过期。

完整隔离回归 **837 通过、0 失败、0 跳过**；编译、JS 语法及 diff 空白检查通过。证据位于被 Git 忽略的 `state/marsyoo-readback-20261106/`：终态回执、固定版本逐字段证明、SDK 证明、浏览器证明、发布清单及最终生产核查。

没有新搜索。原始请求 39,835、正文记录 332,768、公开投影 39,830、旧本地上传队列 9 queued 保持原口径；新增平台快照独立留证，不冒充旧队列的终态。持续采集、正文、自动上传、平台领取及监测仍关闭。

## 尚未完成

以下为最初读回时的限制；本条 low 已按下节修正。一条历史摘要不一致仍需对账；News 出版者链接解析、Trends 数值历史曲线、全部来源的新运行交付、正式完整 schema/ReaderAdmission、七天稳定性和百万行验收不能宣称完成。后续如需批量写入或恢复自动采集，需要新的明确授权。前一阶段数据保存、去重与导出记录见 [数据层优化](google-data-optimization-2026-11-06.md)。

## 后续授权：修正同一条文档的低质标记

用户随后明确要求按已讨论方向解决。通过 ego lite 打开平台管理后台的该条文档，获得之前 Consumer 响应没有提供的明细：规则版本 3、40 分、`informativeness:low`、文本字符数 0。这是具体命中的信息量规则，不再只是缺少正文的猜测；具体评分阈值和完整实现仍未公开。

检查内容质量页、Dataset 编辑表单和 OpenAPI 后，没有发现受支持的搜索专用质量配置入口，因此没有向共享 Dataset 随意塞入 `quality_profile` 等未定义设置，也没有修改全平台规则。

修正方式是增加真实可检索的**搜索结果快照文本**。`with_search_snapshot_text()` 是显式调用的纯函数，将已有查询、运行 ID、完成时间以及每条结果真实的标题、链接、摘要和排名投影成文本；没有的字段省略，不推断排名、不扩写摘要、不抓网页正文，不填充凑字数。文本及 discovery 元数据明确标记 `saved_search_result_snapshot_not_publisher_body`，不是出版者文章。原始 platform_payload、响应、7 条 occurrence、源摘要和外部文档身份完全保留。修正事件使用独立、稳定的 `google-serp-snapshot-text-1` 后缀，避免把不同内容冒充原事件重试；自动上传入口仍未连接。

一次 dry-run 有效后，仅对原文档提交一次修正，真实终态 accepted（`2026-11-06T04:08:02.992256Z`），没有新建其他搜索对象。新版本 `ver_4a29e4654356820e56abd6407398d5b0`：

- 平台现为 **normal / 100**，原 `informativeness:low` 消失，剩余标记 `language:detected` 是平台自动识别语言的说明。
- 固定版本 Consumer 读取中，原始平台字段、源摘要、响应及 7/7 occurrence 顺序和字段全部一致；管理接口按同一版本读取的 2,358 字符快照文本逐字匹配。
- 再次批量读取证明原版本与新版本均保留可读，原版本字符数 0、新版本 2,358，raw_payload_hash 相同；没有覆盖历史证据。平台实际页面显示正常 100 分及快照文本。
- 工作台证明缓存更新为新版本，正式 `/v1/google/legacy-delivery` 返回 `platform_quality=normal`、`sample_roundtrip_verified`；旧 9 条历史证明的时间及其待对账状态未刷新或冒充修复。

本次 Web 仅安装更新后的 `google_serp_delivery.py` 与证明缓存，没有重启任何服务、没有更新采集机、没有改数据库或全局设置。备份为 Web `state/marsyoo-readback-20261106/backup-quality-text`。完整回归 **839 通过、0 失败、0 跳过**，编译和 diff 检查通过；生产核查请求 39,835、正文 332,768、投影 39,830、旧 queued 9，所有自动开关关闭、启用监测 0 均不变。临时 Collector 凭据已删除，浏览器清理后只保留该条正常质量的详情页供用户查看。

证据同目录下 `admin-quality-response.json`、`quality-corrected-dryrun.json`、`quality-correction-terminal.json`、`quality-corrected-pinned-proof.json`、`quality-version-preservation.json`、`quality-corrected-ui.txt`、`quality-fix-workbench-proof.json`、`quality-fix-production-state.json` 与 `quality-fix-regression.log`。这解决的是本条快照的文本表示及已确认的低信息量标记，不等于为全平台实施了新的质量 profile，也不等于所有历史数据和总需求均已验收。
