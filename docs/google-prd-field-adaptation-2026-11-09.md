# Google 总需求字段缺口与适配报告

日期：2026-11-09。主依据为《Google 数据产品需求文档 v1.0》冻结全文，重点核对 5.1～5.6、GO-DAT、GO-ID、GO-ING。2026-11-05 网站需求仅作当前展示范围补充，不替代总需求。

## 结论

当前已采到基础 SERP、RSS 新闻、当前热点和图片元数据，但尚未采齐总需求。缺口必须分为三类：

1. **源数据已有、缺标准适配**：请求和结果身份、来源引用、时间依据、语言、深度、feature 与结果关联。本轮已实现显式版本化适配。
2. **字段预留、上游没给或本轮没有记录**：作者、设备、精确位置、源观测时间、缓存年龄、展开状态、精确引用角色、canonical 等。保持未知，不能靠增加列补出数据。
3. **尚未接入的数据域**：数值 Trends、关键词量/CPC、完整 AI Answer/Mention/Citation、GSC、GA4、Ads/Merchant、Local/Review 和出版者正文。需要真实来源/资产/用途及接口验收，不能由 Web/RSS 推算。

本轮为字段适配、离线样本复算与隔离测试，没有发起新 Google 请求、平台上传、生产迁移或正文采集。该阶段当时因 SSH 认证前断连而未发布。随后已通过专用 SSH 复用与内网跳转恢复发布，最小补丁、线上接口及真实样本导出已验证，详见 [后续优化验收](searchflow-reliability-optimization-2026-11-09.md)。

## 实际样本与已经有的字段

审计固定的三十分钟真实记录，去掉跨任务缓存重复并按 UTC 半开窗口筛选：51 个请求、40 个成功，399 次链接出现、10 个热点话题。399 是出现记录，不是唯一 URL；原三十分钟首次贡献为 318。

| 来源 | 成功请求 | 实际数据 | 已有较完整字段 | 主要缺失 |
|---|---:|---|---|---|
| Web | 30 | 111 自然结果；26 个富结果块 | 标题、URL、摘要、自然位置、源绝对位置、原生 ID、显示/跳转地址 | 作者、独立发布时间、结果语言、真实设备/位置等 |
| News | 5 | 179 条新闻 | 标题、媒体、媒体主页、原始/规范 RSS 时间、RSS 顺序和链接状态 | 179/179 仍为包装 URL；文章 canonical、作者、正文、修改时间未取得 |
| Trends | 1 | 10 话题、30 相关报道 | 话题、原始量级、地区、报道关系、图片与量级下限标记 | 历史 series、scale/window/granularity、区域兴趣、top/rising/breakout |
| Images | 4 | 79 图片记录 | 标题、图片/来源页双 URL、宽高、顺序、源记录 | 79/79 缺缩略图；未下载原图，尺寸没有独立验证 |

富结果为 23 个 `ai_summary`、3 个 `people_also_ask`。保存了 59 个普通模块链接，但没有明确的 citation 角色证据，不能把它们升级成 59 个 AI 引用。3 个问答块含 12 次子项，其中原文存在上游错误提示，独立答案字段为 0；本轮没有清洗成假答案。

## 总需求字段差距矩阵

以下是按数据域梳理的字段清单，不代表把原 273 项需求重新标为完成。

| 数据域 | 对应字段/字段组 | 当前判定 |
| 观测公共字段 | entity_id、provider_namespace、source_ref、requested_at、received_at、observed_at、requested_item_limit、returned_item_count | 本地适配完成，待发布 |
| 来源和解析版本 | source_api_version、source_parser_version、provider_version、field_schema_version | 部分有值，其余未记录 |
| 搜索环境 | device、location、location_precision、login_state、safe_search、query_language、result_language、detected_language | 本轮样本未知；部分需上游支持 |
| 缓存与数据完整性 | cache_hit、cache_age_seconds、cache_original_observed_at、is_partial、is_truncated | 补三态/缺失原因；源事实仍缺 |
| SERP item/URL | native_id、provider_rank_group、provider_rank_absolute、organic_position、parent_block_id、display_url、redirect_url、canonical_url、registrable_domain、sponsored_label | 部分已有值，本轮补适配 |
| SERP feature | feature_id、feature_type、block_order、presence、expanded_state、channel、target_urls、citation_urls、source_result_ids、matched_occurrence_ids、children | 结构适配完成；展开/渠道/引用证据未覆盖 |
| Web 可选内容 | author、sitelinks、source_published_at、result_language、detected_language、rating、reviews、thumbnail | 映射可承载，但样本缺值 |
| News 文章实体 | publisher_article_url、canonical_url、author、body、source_published_at、modified_at、article_language、publication_source | 真链接/文章字段仍缺，不能伪补 |
| 数值 Trends 与 RelatedQuery | series_id、term_or_topic、requested_window、effective_window、granularity、timezone、scale_id、period_start、period_end、value、is_partial、revision、top_rising、breakout | 需合格数值来源及对应适配 |
| 关键词量与商业估计 | requested_query、returned_text、close_variant_group、monthly_volume、competition、CPC、bid_percentiles、currency、micros | 需获准来源/账号；未采集 |
| AI Answer/Mention/Citation | answer_id、prompt_version、consumer_ui_generation_api、AIO_AI_Mode、observed_model、turn、repeat_group、expanded_state、mention_span、brand_id、alias_version、citation_order、matched_content_id | 仅原始模块部分有值；完整域未接通 |
| Search Console | property、day_hour、query_page_country_device、searchAppearance、clicks、impressions、CTR、position、anonymous_flag、top_rows_bulk、revision | 需真实property授权与API/BQ对账 |
| GA4 | property、dimensions、metrics、rowCount、samplingMetadatas、subjectToThresholding、dataLossFromOtherRow、quota、report_timezone、event_revision | 需真实资产及报表/事件独立授权 |
| Ads/Merchant | customer_merchant_id、campaign_adgroup_criterion、segments、cost_micros、conversions、conversion_value、currency、attribution_window、product_report、revision | 需项目/资产/用途与实际报表来源 |
| Local/Review | place_id、grid_coordinates、point_id、location_precision、local_rank、review_id、rating_text、reply、review_updated_at | 独立来源、资产与字段用途未准入 |
| 权限/留存/固定复算 | tenant_id、asset_id、grant_id、license_id、license_version、allowed_uses、retention_deadline、redistribution_scope、input_manifest、cohort_version、rights_at_query、evidence_expires_at | 框架部分存在；实际登记/生命周期未完成 |
| 平台终态和成本 | ingested_at、terminal_receipt、Whale_document_version、billable_units、cost、credit、currency_unit | 需每事件真实终态/独立读回/费用账 |

## 本轮实际完成的适配

### 1 请求/观测字段

新增 `field_adaptation.run`，每个字段带 `value / availability / basis / measurement / missing_reason`。

- 分开 provider 版本、源 API 版本和解析版本；OpenSERP 的 2.1 不冒充 Google API 版本。
- 提供命名空间身份与过滤后响应 hash 引用。
- 分开本地接收时间、请求时间、源观测时间；`observed_at` 若使用接收/完成代理时点，明确写依据。
- 分开请求语言、源语言、查询语言；结果/检测语言在 item 上独立保留。
- 分开请求条数、实际解析条数、保存条数、话题数；失败时保存条数可以为 0，但实际返回数保持 source_error/unknown，不冒充有效零结果。
- `cache_hit` 保留 true/false/unknown，年龄与原缓存观测时间只认明确字段。
- partial/truncated 不因数组长度、没有错误、has_more 自动推成完整/截断；错误项明确保留。

40 个成功请求的接收/请求时间和国家有真实值；设备、精确位置、登录态、源观测时间、调度时间、源时区、缓存年龄、源 API/解析版本等没有直接证据，仍未知。上游缓存状态缺失不否定另外的 4 次任务关联复用事实，两者不能混写。

审计输入来自网页 request 响应投影，未包含 SQL 的 page/provider 列。页数仅从明确 response.page 等证据读取；RSS 样本的这项未知不能解释成生产 SQL 没有保存页码。正式 API 直接读 SQL 时保留原列。

### 2 Item/URL 字段

新增 `field_adaptation.items`，用原 occurrence_id 关联，保留原生 ID、源排名组/绝对排名/自然位置、页码与 offset、显示 URL、规范化版本、跳转证据、父块、付费标签及语言字段。

- Google 中转 URL 可以作为跳转证据保留，但不会冒充最终目标对象，也不会主动访问。
- 不通过最后两段域名猜注册域；PSL/IDN 及版本化域名归属仍待独立适配。
- 不把普通 Web 页面叫做出版者文章，不把图片文件叫文章；已解析的新闻/报道地址与目标链接分列。
- 不通过标题或 URL 猜作者、日期、语言、canonical 或是否为广告。

### 3 Feature 和问答字段

新增 `field_adaptation.features`：feature_id、native_id、type、顺序、源 block_order、presence、expanded_state、channel、parent_block_id、source_result_ids、明确匹配的 occurrence_id、目标链接/引用链接、子项及源字段/hash。

- 同一个模块同时出现在 `serp_features` 与透传命名模块时不重复计数；同一响应中的多个实际 occurrence 仍分别保留。
- 数组顺序不冒充 SERP 像素位置，也不代表“首屏占比”。
- 普通 links 与明确 citations 分开；citations 未提供为 unknown，只有明确空列表才可能为 true_zero。
- PAA 原始 text 原样留存；只有上游单独给出的 answer 才进入 answer_text，报错文本不变成答案。
- 模块未返回不推断该功能不存在；AI summary 不自动判成 AI Mode、生成 API、某模型或某品牌提及。

### 4 不改旧合同与既有平台事件

采用显式 `field_contract=prd-v1`；默认仍返回旧 `google-public-data-1`。新块带独立 `google-prd-fields-1` 版本，不改变旧 query/occurrence ID，也不把新字段静默塞入旧 Whale 事件。

已离线核对既有固定 Web 样本，旧 observation hash 一致。此次也修复了 query_conditions 中嵌套 location/device 可能绕过脱敏的问题；合法既有固定样本未因此变化。

## 本地已实现的调用方式

以下为显式字段版本使用方法；对应最小后台补丁已在后续优化中上线。

```text
GET /v1/google/legacy-data?source=google_web&field_contract=prd-v1
GET /v1/google/legacy-data/requests/{request_id}?field_contract=prd-v1
```

仍使用现有站点访问门与客户 Bearer，私有 SQL 隔离不变。错误版本或把该参数放到未支持的旧 preview 路由会被拒绝。

操作员只读快照导出：

```sh
.venv/bin/python scripts/google_data_export.py /绝对路径/新导出目录   --source google_web --field-contract prd-v1
```

增强字段完整进入 requests JSONL 和请求 CSV 的 record_json，并可离线验证。现有 occurrence CSV 保留原始明细及其 record_json，可用 occurrence_id 与增强 items 关联。旧网页 50 列和客户 7/14 列没有被擅自改成新的不兼容列集；尚未增加数据库实体列、过滤索引或前端新表单。

## 下一步优先级

| 优先级 | 工作 | 为什么不能直接补值 |
|---|---|---|
| P0 | 最小发布本次字段投影，验证真实鉴权/导出与固定样本兼容 | 后续已恢复 SSH 并最小发布；四来源接口与快照导出验证通过，冻结 worker/web.py 不作整包覆盖 |
| P0 | 明确设备/位置、真实缓存状态/原时间、源 API/解析版本和计划点 | 需要采集入口真实传参、来源支持和持久化证据；不是仅修改显示字段 |
| P0 | News 真链接解析与 canonical 证据 | 需要可验证的解析链路；媒体主页不能当文章 URL |
| P0 | AIO/feature 来源、展开状态和引用角色 | 需要对应真实响应或二次展开流程；不能凭普通链接和回答文本推断 |
| P1 | 数值 Trends、关键词量和商业估计 | 必须接入实际可用且获准的来源，明确 scale/window/unit/币种 |
| P1 | GSC/GA4/Ads/Merchant/Local | 分域接入真实授权资产与方法；无授权不能声称已采集 |
| P1 | 权利/期限、正式 Whale 全字段与终态读回 | 现有访问门不等于新版 source/field admission，单条快照读回也不代表全库闭环 |

## 验证与证据

- 字段样本：[固定样本审计](../state/prd-field-adaptation-20261109/verified-sample-audit/field-audit.json)、[适配后的请求 JSONL](../state/prd-field-adaptation-20261109/verified-sample-audit/adapted-requests.jsonl)、[hash 清单](../state/prd-field-adaptation-20261109/verified-sample-audit/manifest.json)。
- [机器可读差距矩阵](google-prd-field-matrix-2026-11-09.json)；[旧固定样本兼容校验](../state/prd-field-adaptation-20261109/compatibility.json)。
- 测试覆盖：默认合同不变、私有隔离、HTTP list/detail/meta、JSONL/CSV 快照、缓存三态/0/false、失败不变真0、问答错误、普通链接不变引用、未知模块、重复 occurrence、时间边界和嵌套脱敏。
- 最终隔离全量回归 **1,046 项通过、333 个子测试通过、0 失败、0 跳过**，107.64 秒。临时 PostgreSQL/Redis 已自动清理；详见 `state/prd-field-adaptation-20261109/closure-regression/pytest.log`。没有用本地测试冒充上线。
