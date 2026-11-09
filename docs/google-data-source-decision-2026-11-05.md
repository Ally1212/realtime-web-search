# Google PRD v1.0：数据来源与用途决定清单

日期：2026-11-05。范围仅为 PRD 的第一件事：D-01～D-06、4.1～4.2 的来源与用途准入。本文是依据现有文档和项目记录形成的**待批准决定稿**，不代表产品、平台、来源或资产负责人已经批准，不开启采集、采购、上传或新域 API 数据能力。

## 结论

- 首试范围按 PRD 的建议提出：品牌/SEO 内部研究、英语、US/UK、有限词集；这是 D-01～D-03 的**提案**，不是已批准范围或已测能力。
- 现有 OpenSERP Web、Google News 搜索 RSS、Google Trending Now RSS 已有历史采集和字段样本；只证明旧系统能取得这些数据。没有据此确认新版产品的来源许可、保存、聚合、导出和再分发权。
- 新 `/v1/google` 已最小上线，但没有合格真实 reader/adapter；所有真实数据域仍维持 `not_covered` 或未授权状态。旧 Whale `queued` 回执不证明终态、版本读回或正式 schema 准入。
- 因而当前**没有一个来源可标为“新版 G1 生产准入已通过”**。以下矩阵逐域写清实际来源、可验证事实和待完成的批准，不以候选接口或本地夹具冒充完成。

## D-01～D-06 决定账

| 决定 | 按 PRD 可提交评审的具体提案 | 当前结论与所缺证据 |
| --- | --- | --- |
| D-01 首试用户、市场、语言 | 品牌/SEO 内部研究；英语；US/UK；有限词集。 | **待批准。** 旧 Web 样本含 `gl`/`hl`，但设备、精确位置和缓存状态未知；不能声称已具备两市场×两设备的可比观测。 |
| D-02 G1 必达域 | 以 SERP/feature、获准的 News 与数值 Trends、AIO 基础组成目标；GSC 仅在真实资产授权及明确决定后成为必达。 | **待批准。** Web 可作为第一个来源 POC；News RSS 只能按有条件元数据源评估；现有 Trends 话题不提供数值 series；AIO 和 GSC 没有真实验收证据。缺域须缩小范围并命名。 |
| D-03 S1 规模、预算 | PRD 建议 1,000 词、核心 100、2 市场×2 设备；商业 $50/day、滚动 30 天 $1,500 为硬上限建议。 | **待批准，未采购。** 费用、调用合批、深度、配额与容量均是 H；需要真实 POC 及计费单位后才能冻结计划。 |
| D-04 来源、用途、保存、分发 | 对每个 adapter/字段登记合同、API/解析版本、source endpoint、可覆盖范围、raw/派生/聚合/embedding/model input/展示/导出/再分发用途和期限。Ads 组合用途另作结论。 | **未批准。** 项目记录没有可用于新版产品的逐源合同/用途批准证据；不能把来源可访问或旧系统在用视为获准。 |
| D-05 账号、资产、准入与 oracle | 授权域采用对应主体的最小只读 scope、真实 property/customer/merchant 及角色；金标指南和阈值先冻结。 | **未批准。** 无三个真实 GSC/GA4 资产或 Ads/Merchant/Local 对应主体准入证据；现有管理 Key 不替代终端租户授权。不创建账号、Key 或 OAuth。 |
| D-06 Whale schema、Dataset、身份、期限与清除 | 将 run/item/feature、News、Trends、AI 和授权报表分别映射到批准的正式 schema；版本化独立读回，期限取最短许可。 | **未批准。** 旧上传路径与 `google-serp-v1` 不构成新版完整 run/item/历史事实合同；WHL-GO-01～08、Dataset、读取和到期清除尚待平台验证。不迁移存量。 |

## 来源与用途逐域核对

“现有证据”仅说明项目或 PRD 实际观察；“用途结论”指**新版产品**，不追认旧系统的历史行为。凡合同、字段权利或真实资产缺失，`raw_store`、`derived_store`、`aggregate`、`embedding`、`model_input`、`internal_display`、`export`、`redistribute` 均不得自动批准。每项需在来源 registry 单独登记字段、用途和最短期限。

| 数据域 | PRD 候选来源；项目现有证据 | 用途/准入结论 | 下一项验证 |
| --- | --- | --- | --- |
| 公开 Web SERP | PRD 候选合格商业 SERP；旧系统实际使用 OpenSERP。只读 170 条 Web 样本中结果项可取 URL/标题/摘要/位置；位置、设备、缓存状态仍未知。 | **旧数据可做字段适配；新版来源用途待批准。** 不声明精确位置、登录态、全网覆盖或生产再分发权。 | GO-POC-02：实际提供方、合同、查询条件、缓存、深度、feature、200 套可匹配条件对照；逐字段用途。 |
| Google News | 旧系统实际使用 Google News 搜索 RSS，保存列表顺序；未解析 wrapper 不能当出版者 URL，摘要不是正文。 | **有条件的元数据候选；新版用途待批准。** RSS 无正式 SLA；正文需独立出版者许可。 | GO-POC-05：入口/版本、URL 解码与来源时间、去重、元数据保存/展示/导出权及备用源。 |
| Google Trends | 旧系统实际使用 Trending Now RSS。样本提供话题、`approx_traffic` 标记与报道数，不提供 term/topic 的数值历史序列。PRD 的官方 Alpha/合格供应商为候选。 | **话题观察与数值时序分开。** 现有数据不得接成 `trends/series` 或声称可比增长；新数值源未获准。 | GO-POC-04：官方或供应商真实准入、scale/window、低量/partial、跨窗可比、100 series；逐字段用途。 |
| AIO / AI Mode | PRD 候选消费者 SERP/AIO/AI Mode 观察服务；本项目仅有本地契约和夹具，无真实合格回答样本。 | **未覆盖。** 生成 API 不替代消费者渠道；answer text/citation 的保存及模型输入权分别审查。 | GO-POC-06：消费者 channel、展开与短效 token、回答/引用、缓存、版权/用途和 500 回答金标。 |
| Search Console / Bulk | PRD 指定授权 property 的 API/BQ 候选；代码有本地只读合同，未有真实资产读回。 | **条件域，未授权。** 仅自有/获准 property；top rows、匿名、修订不得补造。 | D-05、GO-POC-07：≥3 property、scope、API/BQ grain、匿名与权限负例。 |
| GA4 | PRD 指定获准 property 的 Data API/BQ；代码有夹具合同。 | **P1 条件域，未授权。** 报告与事件导出分别批准；不能声称竞品流量。 | GO-POC-08：真实 property、只读 scope、模板、metadata、配额、日期/归因与用途。 |
| Ads 关键词与经营 | PRD 指定主体 Cloud project、OAuth、customer 及相应访问等级；代码仅本地合同。 | **P1 条件域，未批准。** 关键词规划与经营报表分开；SERP 组合用途及纯代理限制未有结论。 | GO-SRC-003/004、GO-POC-09：项目主体、Basic/Standard、permissible use、组合用途、CID 配额和逐字段转供权。 |
| Merchant | PRD 指向当前 Merchant API 获准报表；本地是 typed fixture，未有真实端点/字段 POC。 | **P1 条件域，未授权。** 不以已 sunset 的 Content API 代替。 | GO-POC-11：实际只读报表方法、merchant 资产、scope、字段与保留/导出用途。 |
| Local / Places / GBP / 评论 | PRD 要求 Places 按需字段和归属展示，GBP/评论存储与转供需独立许可；本地仅夹具。 | **P1 条件域，默认关闭。** 未批准时最多评估 place ID/必要链接，不建评论仓库或通用代理。 | GO-POC-10：各来源逐字段存储、展示、聚合、embedding、转供、期限及实际网格数据。 |
| 出版者文章正文 | PRD 将 News 索引摘要与出版者正文严格分离；生产正文采集目前关闭。 | **不作为 News/Web 数据自动附带。** 未有独立出版者来源许可时 `body` 不覆盖。 | 独立来源/用途、正文质量 profile 和期限批准；本阶段不启动正文采集。 |

## 完成第一件事所需的证据和判定

1. 产品负责人决定 D-01～D-03 的范围、条件域和预算；来源/资产/平台负责人分别决定 D-04～D-06。记录批准人、日期、范围和适用来源版本。没有签核时保留“待批准”。
2. 每个拟上线 source 在 registry 填全 `adapter/version`、实际 endpoint、覆盖条件、API/解析版本、缓存/刷新、计费单位、许可编号与版本、逐字段用途及期限；任一必需项缺失则能力保持 `planned/not_covered`。登记本身还需真实 POC，不单独构成上线批准。
3. 先选**一个**获准 Web SERP 来源做 GO-POC-02；通过后才进入 Whale schema/独立读回和 G1 数据验收。News、Trends、AIO、授权资产域各自另验，不借 Web 结果代签。首个来源若无法取得目标用途，按 PRD 缩小发布范围或选择另一获准来源。

## 证据与限制

- PRD 冻结全文：`docs/google-prd-v1-source-2026-11-04.txt`；来源要求见 4.1～4.2，待决策见 12.3，POC 见 11.1。
- 现有来源字段：`docs/google-sources-requirements-2026-10-10.md`；生产历史只读 200 条样本摘要：`state/google-prd-v1-20261104/phase28/real-data-profile.json`。该样本不是来源许可、双源金标或真实 Whale 读回。
- Whale 旧链路：`docs/marsyoo-google-worker-audit-2026-10-22.md`；新版边界与最小发布：`docs/google-prd-implementation-2026-11-04.md` 第 28、29 检查点。
- 本次仅整理已有本地证据，未查看或修改真实合同、账号、Key、Dataset、生产配置或线上数据；外部来源条款未重新核验。所有待批准项保持待批准。
