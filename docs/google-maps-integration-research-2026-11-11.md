# SearchFlow 接入 Google Maps / Places 研究

> 后续实施：自建Maps查询、页面与导出已上线；中台尚未接通。见 [上线记录](searchflow-maps-integration-2026-11-11.md)。

> 最新决策：用户已否决官方 Places/结算方案，改为研究第三方及开源自建。以下官方建议保留为历史研究，不再是实施计划。开源实测见 [开源自建研究](searchflow-maps-open-source-2026-11-11.md)。

日期：2026-11-11。本文是接入研究与实施建议，不表示已开通 Google Cloud、购买额度、配置 Key、调用真实 Places API 或部署生产功能。

## 结论

SearchFlow 可以接入 Google Maps 相关数据，但必须把两类需求拆开：

1. **按需查商家并在 SearchFlow 中展示**：优先使用官方 Places API (New)。适合文本搜索、附近搜索、地点详情和有限评价展示。
2. **长期商家库、批量导出、Whale 转存、本地排名网格**：需要另选合同明确允许保存、派生、导出和再分发的商业 Local/Maps 数据源。官方 Places API 不适合把商家名称、地址、评价批量抓取后长期建库。

建议先完成官方 Places 的只读 POC，但第一版不上传 Whale、不建立原始评论库、不开放批量导出。与此同时保留现有 `/local/*` 合同，为后续获准的网格排名来源使用。

## 当前项目基础

项目已具备真实接入所需的一部分骨架：

- `realtime/google_local.py` 已定义 Local/Places 条件域，包括网格、点位观测、`place_id` 引用和评论主题洞察。
- `/local/grids`、`/local/grids/{id}/observations`、`/places/{id}/reference`、`/local/review-insights` 已进入 OpenAPI。
- `google_runtime.py` 已有独立的 `SEARCHFLOW_GOOGLE_LOCAL_*` 模块、合同、Dataset 和生产双批准门禁。
- 权限层已要求 source/version/asset grant、字段用途和保留期限，未准入时 `/local/capabilities` 返回 `collection_enabled=false`。
- 现有测试覆盖缺测、失败、排名深度、成本、来源证据、`place_id` 和评论聚合的失败关闭。

当前缺口：没有真实适配器、Google Cloud 项目与结算、API Key、字段级使用决定、真实 POC、独立存储、任务入口和正式网页。现有 Web 搜索偶尔出现的 `local_results` 不能当作 Google Maps 完整模块。

## 官方 Places API (New) 能力

| 能力 | 可用于第一版 | 关键边界 |
| --- | --- | --- |
| Text Search | 是 | POST；用文本、类型、区域限制或位置偏置搜索；每页 1–20 条，所有页面最多 60 条，结果不保证每次完全一致。 |
| Nearby Search | 是 | POST；圆心加半径，半径最大 50,000 米；可按类型过滤并按距离或受欢迎程度排序。 |
| Place Details | 是 | GET；按 `place_id` 获取名称、地址、电话、网站、营业状态、营业时间、评分等。 |
| FieldMask | 必须 | 没有默认字段，缺少 FieldMask 会报错；按请求中最高级别 SKU 计费，生产不能使用 `*`。 |
| 评价 | 有限 | `rating` 和 `userRatingCount` 可单独请求；`reviews` 最多返回 5 条，并进入更高 SKU。不是完整评论抓取接口。 |
| 照片 | 延后 | 需遵守照片作者归属与展示规则；第一版不保存原图。 |
| AI 摘要 | 不进第一版 | 必须原样展示 Google 返回的披露文本和举报链接，成本与用途也需单独批准。 |

推荐第一版 FieldMask：

```text
places.id,
places.displayName,
places.formattedAddress,
places.location,
places.primaryType,
places.types,
places.businessStatus,
places.websiteUri,
places.nationalPhoneNumber,
places.regularOpeningHours,
places.rating,
places.userRatingCount,
places.googleMapsUri
```

实际 FieldMask 必须按页面需要拆成“列表字段”和“详情字段”，避免每次搜索都触发不必要的高等级 SKU。原始评论、照片和 AI 摘要不在首版掩码中。

## 数据使用限制

Google 官方政策明确限制在服务之外预提取、编入索引、存储或重新分发 Google Maps 内容，也明确举例不能复制保存商家名称、地址或用户评价。因此不能把 Places API 直接做成 SearchFlow 的永久商家仓库或 Whale 原始数据集。

可采用的保存策略：

| 数据 | 建议保存方式 |
| --- | --- |
| `place_id` | 可长期保存，用作稳定引用。 |
| 请求条件 | 保存查询文本、类型、中心点、半径、语言、地区、FieldMask 版本和请求时间。 |
| 经纬度 | 仅按官方允许的临时缓存期限处理；当前政策页说明相关服务例外通常最长连续 30 天，必须设置到期清除。 |
| 名称、地址、电话、网站、营业时间、评分 | 官方来源模式下按需实时读取并展示，不进入永久事实表，不上传 Whale。 |
| 评论 | 首版不保存原文；若展示，必须保留作者头像/名称/资料链接、Google Maps 来源链接、排序说明等归属。 |
| 审计 | 保存 provider request ID、时间、字段掩码、状态、费用层级、响应 hash 和错误分类，不保存受限响应正文。 |
| 派生分析 | 未取得明确许可前不做 embedding、长期聚合、线索评分或跨来源合并。 |

如果业务目标是“批量商家线索 → CSV/CRM/Whale”，应采购并审查允许这些用途的独立商业来源，逐字段记录 raw store、aggregate、export、redistribute、model input 和 retention 权利。

## 推荐架构

```text
用户创建 Maps 查询
  → SearchFlow 异步任务与额度预留
  → Local source adapter
      ├─ 官方 Places：实时展示，短效缓存，永久保存 place_id
      └─ 获准商业来源：排名网格/长期事实，按合同决定保存与导出
  → 字段级用途门禁
  → 页面结果与审计
  → Whale（仅商业来源且 Dataset/字段/期限获准时）
```

建议新增模块：

- `realtime/google_places_adapter.py`：Places API (New) 传输、FieldMask、错误与费用层级映射。
- `realtime/google_local_store.py`：仅保存允许的数据、到期时间和清除任务；不保存官方受限正文。
- `realtime/google_local_jobs.py`：异步搜索、详情补拉、幂等、预算和失败退款。
- `realtime/google_local_ui.py`：Maps 查询表单、列表、详情侧栏和来源归属。
- `sf_google_local_jobs`、`sf_google_local_refs`、`sf_google_local_audit`：独立表；不修改旧 SERP 原始表。

现有 `LocalService` 保持读取合同，真实适配器返回带 `LocalEvidence` 的结果。生产仍须同时满足来源 registry、asset grant、字段用途、保留期限和 `SEARCHFLOW_GOOGLE_LOCAL_APPROVED` / `SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED`。

## 页面与 API 范围

第一版页面新增独立的 `Google Maps` 来源入口：

- 输入关键词、国家/地区、中心点或城市、半径、商家类型和语言。
- 列表显示名称、类型、评分、评价数、营业状态和“在 Google Maps 查看”。
- 点开详情时再请求电话、网站、营业时间等字段，降低费用。
- 清楚标记“Google Maps 数据，实时读取”，不与现有 Web/News/Images 记录混在同一永久数据集。
- 不提供“全部导出”“加入 Whale”“永久保存商家资料”和“抓取全部评论”。

建议 API：

- `POST /v1/google/local/jobs`：创建 Text Search / Nearby Search 异步任务。
- `GET /v1/google/local/jobs/{id}`：查看任务、费用层级和到期时间。
- `GET /v1/google/local/jobs/{id}/results`：返回短效结果及来源归属。
- `GET /v1/google/places/{place_id}`：按需获取详情。
- 现有 `/local/grids*`：留给获准的本地排名来源，不用官方 Places 结果冒充 Google Maps 消费者排名。

## POC 与验收

POC 默认保持生产关闭，不接 Whale：

1. 建立独立 Google Cloud 项目，启用结算和 Places API (New)，创建仅允许服务器出口和 Places API 的 Key；Key 只存服务器私密文件。
2. 选 3 类查询、2 个城市、Text Search 与 Nearby Search 各一组；冻结查询参数与 FieldMask。
3. 人工核验至少 50 个结果的 `place_id`、名称、类型、位置、营业状态、评分和 Maps 链接；缺失保持未知。
4. 记录每种 FieldMask 的实际 SKU、请求数、延迟、429/5xx、字段完整率和结果稳定性。
5. 验证无 Key 泄漏、无受限原始正文落库、短效数据按期清除、无授权导出/Whale/模型输入均失败关闭。
6. 通过后只开放小额度、低并发、手动查询；定时批量采集、排名网格和评论分析另行验收。

上线门槛：真实 API 读回、字段与费用表冻结、来源用途批准、清除任务、归属展示、权限负例、额度原子结算、桌面/手机页面和生产关闭状态均通过。不能只凭接口 200 即宣布接入完成。

## 决策建议

首选方案是：

- **现在实现**：官方 Places API (New) 的按需 Text Search、Nearby Search 和 Place Details；只长期保存 `place_id` 与审计。
- **暂不实现**：完整评论抓取、照片归档、AI 摘要、批量导出、Whale 同步和定时全城扫描。
- **另行选源**：Google Maps 本地排名网格、长期商家事实和 B2B 线索库。这类需求使用允许留存/导出的商业 Local 数据源，并复用现有 `/local/grids` 与权限合同。

这样能最快形成真实可用入口，同时不把官方 Places 的实时展示许可误当成长期数据仓库许可。

## 官方资料

- Places API (New) 概览：<https://developers.google.com/maps/documentation/places/web-service/overview>
- Text Search (New)：<https://developers.google.com/maps/documentation/places/web-service/text-search>
- Nearby Search (New)：<https://developers.google.com/maps/documentation/places/web-service/nearby-search>
- Place Details (New)：<https://developers.google.com/maps/documentation/places/web-service/place-details>
- 使用量与结算：<https://developers.google.com/maps/documentation/places/web-service/usage-and-billing>
- Places API 政策与归属：<https://developers.google.com/maps/documentation/places/web-service/policies>
- Google Maps Platform 服务条款：<https://cloud.google.com/maps-platform/terms>

官方文档页面显示最后更新时间为 2026-11-10；价格和条款仍应在采购与上线前按实际账号、结算地区和合同版本再次冻结。
