# Google 图片搜索数据适配研究（2026-11-06）

结论：可以新增 Google Images 数据来源，优先复用现有 OpenSERP。当前线上尚未具备该功能；本地存在部分未上线代码，但接口、字段及调度尚未闭环，不能直接启用。第一版采集搜索结果元数据与图片外链，不批量下载原图，暂不新增分析。

本次只读研究了源码、生产数据库、上游文档及供应方实现；没有发起新的采集任务、图片检索 API 调用、平台写入、生产部署或开关修改。下文真实上游成功率和当前部署镜像图片解析可用性仍需小样验证。

## 1. 如何理解这个功能

输入一个关键词，系统保存 Google 图片搜索返回的一组结果。例如每条包含图片地址、图片所在网页、标题、缩略图地址、上游提供的宽高，以及当次查询的地区、语言和时间。它首先是一份可追溯的“图片搜索结果目录”，不是把全网图片复制到服务器。

必须区别三个地址：`image_url` 是图片文件地址，`source_page_url` 是发布该图片的网页，`thumbnail_url` 是预览图地址。三者不能互相替代。同一图片可以出现在多个网页、多个查询和多次运行中；这些出现记录均有保留价值。

| 数据 | 第一版范围 | 不能承诺的内容 |
| --- | --- | --- |
| 标题、图片地址、来源网页、上游结果 ID | 上游返回则保存，缺失明确标记 | 不补造缺失字段 |
| 缩略图地址、宽高 | 上游提供则记录 | 不下载文件来推断尺寸或 MIME |
| 查询、地区、语言、请求与完成时间、缓存状态 | 保存请求条件与可证实的观测信息 | 请求参数不冒充已验证的实际展示条件 |
| 结果顺序 | 保留源顺序、上游 rank 及排名依据 | 图片网格顺序不冒充 Web 自然排名；不按每页 10 条推算全局排名 |
| 原始结构化返回及错误 | 完整保留、可回放、可导出 | HTTP 200 不自动代表有效图片结果 |
| 原图文件、OCR、以图搜图、Google Lens | 不在第一版 | 不等同于关键词图片搜索 |
| 作者、许可、拍摄时间、真实发布日期、图片二进制相似度 | 未提供则未知 | 来源页、搜索时间和文件后缀不能推导这些事实 |

总需求 GO-ING-012 明确“不下载原视频/全网图片；默认外链”。因此“先图片搜索元数据，后按独立需求考虑媒体处理”与当前要求一致，见 `docs/google-prd-v1-source-2026-11-04.txt`。

## 2. 本项目实查结果

生产读取的是采集机实际冻结代码，而不是本地工作树。实际镜像为 `realtime-openserp:0.8.12-ready-results-20261007`。线上 worker 的 `free_google.py`、`serp_fields.py`、`serp_worker.py`、`discovery.py` 均未出现图片来源/vertical 接入标记。数据库现有 Web 31,679、News 8,127、Trends 29 次请求，合计 39,835，没有 google_images 请求。

| 环节 | 本地代码现状 | 必须补齐 |
| --- | --- | --- |
| 来源枚举 | `serp_fields.GOOGLE_SOURCES` 已含 google_images | 与 worker、API、页面、平台契约统一，而非仅放行枚举 |
| 上游调用 | `free_google.py` 可传 vertical，但使用 `/google/search?vertical=images` | 按上游真实 `/google/image` 路由适配；不能假定未声明参数会切换搜索类型 |
| 字段转换 | `image_result()` 主要读取扁平字段 | OpenSERP 文档返回嵌套 image/source，必须独立解析 |
| 调度/缓存 | `serp_worker.py` 只调 Web 及 News/Trends；`discovery.py` 请求流程仍是 Web | 图片执行分支、缓存/锁/frontier/计数/暂停与错误归属 |
| 浏览/导出 | `serp_console.py` 结果 SQL 默认落到 organic_results | 增加 image_results 分支、字段齐全的 JSONL/CSV 和请求详情 |
| 旧库读取/客户 API | `google_legacy.SOURCES`、`product.py` 仍只允许三个来源 | 统一鉴权、私有隔离、额度及幂等后的图片路由 |
| 标准化语义 | `google_data.py` 虽有 image 类型，通用映射仍将 link 放 article_url | 区分图片与来源页身份；不要把图片文件称为文章 |
| Whale 交付 | 当前完整 run builder 明确仅支持 Web；平台注册/预期任务仍按三来源 | 独立图片快照 schema、显式映射和读回验收，默认不自动上传 |

用 OpenSERP v0.8.12 官方 ImageResult 结构构造了一条**合成诊断样本**，调用现有本地 `image_result()`，图片地址、缩略图、来源页、宽、高五项全部丢失；第二页传入第一个序号还得到 `rank_absolute=1`。证据 `state/google-images-research-20261106/local-mapping-diagnostic.json`。这证明字段适配需要修正，不代表生产真实图片请求测试。

另一个数据损失点：现有 transport 用图片 URL 作为字典键，会在入库前合并同图多次出现。应先保存每个 occurrence 和原始顺序，再计算不同对象的唯一数。

## 3. 上游可行性及路线

**优先 OpenSERP。** 官方 v0.8.12 的 OpenAPI 明确提供 `/{engine}/image`，Google 实现有 `SearchImage`，使用 Google 图片页面；图片结果定义为 `image.url/thumbnail/width/height` 与 `source.page_url/domain`，不是当前本地解析的扁平对象。接口同时有 query/meta/results/pagination，仍须核验请求 ID、实际引擎、错误状态、返回类型及分页。

依据：[v0.8.12 OpenAPI](https://github.com/karust/openserp/blob/v0.8.12/docs/openapi.yaml)、[Google SearchImage 实现](https://github.com/karust/openserp/blob/v0.8.12/google/search.go)、[图片搜索 URL 构造](https://github.com/karust/openserp/blob/v0.8.12/google/url.go)。主分支文档与生产定制镜像不是同一验收证据，不为此直接整体升级镜像。

该版本实现涉及滚动和解析图片网格。图片页是动态网格，不能套用 Web 已测的槽位、每页结果数或成功率。首次试验建议并发 1、有限结果和超时，复用现有代理调度与冷却；遇验证码按失败记录和暂停策略处理，不绕过验证。

**备用是明确支持 Google Images 的供应方。** 例如 [SerpApi Google Images 文档](https://serpapi.com/google-images-api) 明确 `engine=google_images`。这仅证明产品有此接口，不代表项目已配 Key、已采购或验证了它的成功率/成本；本轮未使用。供应方字段必须经独立 adapter，不能与 OpenSERP 混用名称或分页语义。

**不建议新接入 Google Custom Search JSON API 作为长期主线。** [Google 官方概览](https://developers.google.com/custom-search/v1/overview) 当前说明不再向新客户开放，已有客户需在 2027-01-01 前迁移。其 [cse.list](https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list) 虽提供 searchType、imgSize 等参数，但它是 Programmable Search Engine，不能假定与 google.com 图片界面结果和排序相同，也不能把它的参数直接发给 OpenSERP。

## 4. 推荐的数据结构与计数

保留三级：一次 run、一条结果 occurrence、一个图片 URL 对象。run 带 `source=google_images`、`vertical=images`、供应方及版本、查询条件、请求/返回深度、真实时间、状态、原始响应和 hash；occurrence 带 run_id、独立 ID、返回序号、上游排名及依据、图片和来源页两个 URL、缺失字段状态和原始字段。

图片对象第一版按保守的 URL 身份去重，不下载图片，因此没有真实文件 hash 或感知去重证明。CDN 变体、尺寸与签名参数可能影响内容，不能照搬网页跟踪参数清理或直接删查询参数；敏感链接要脱敏并明确不可重放，不用另一条地址冒充原始可用地址。

重复 URL 不删除 occurrence。同图不同来源页应各自保存；新增图片 URL、来源网页 URL、结果出现次数和全站新增链接分开显示，不能相加冒充新增图片数。原始响应、规范化结果和导出之间校验条数、顺序与摘要，未知/部分/截断均显式表示。

## 5. 实施顺序和验收

1. **先完成上游及数据验证，不先做图片墙。** 正确路由及嵌套字段 adapter → 单独 source 调度和缓存身份 → 保留原始请求及 occurrence → 手动小样验证。建议 3 个固定关键词、US/en、每次最多 20 条、并发 1；这是后续验证方案，本轮未发这些搜索。
2. **再打通网站与出口。** 任务来源选择、图片结果卡片（图片/来源页分开）、尺寸未知标记、失败与部分结果、按同一已应用筛选导出 JSONL/CSV；客户 API 沿用原访问门、租户隔离、幂等与成功计费。预览属于用户浏览器对外请求，宜按需加载；保存缩略图 URL 不等于已下载或永久可访问。
3. **最后做平台交付与可选扩展。** 保持本地 source=google_images；若当前平台没有 google_images 枚举，须按现有 google_search 加明确 vertical=images 的契约验证，不发送不存在的枚举、不冒充 Web、News 或 Trends。只读 Key 的字段权限、固定版本完整 payload、最终回执及逐条读回都要验证；不为验证自行恢复批量上传。

最小通过条件：请求确实到 Google 图片路由、返回项为 image、图片和来源页不混淆、重复出现不丢、缺失字段不补造、分页/缓存不跨 Web 命中、成功空结果与解析失败分开、数据库重读与 JSONL/CSV 原字段一致、私有结果不公开、暂停后不再新增请求。再按需验证更深分页、其他地区语言与长时稳定性；首批成功不宣称产能或 SLA。

代码发布应在当前生产冻结包上做独立小补丁及备份，避免把工作树历史正文/hash 迁移带上线。研究期间 continuous/body/uploads/claims 均为关闭；本报告没有恢复任何采集或上传。

**最值得做的下一步：修正 `/google/image` 与 ImageResult 映射，先实现一次手动图片搜索完整入库和导出。** 这一环节最先回答“当前部署能否真实采到、字段是否完整”，比先加按钮、下载原图或上图片分析更有验证价值。
