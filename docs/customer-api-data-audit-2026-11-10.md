# 对外 API 与网站数据核验（2026-11-10）

## 结论

**四来源已保存数据的主要读取与完整交付可用，但不能称为所有接口全部打通。** 对外接入仍有五项明确问题和若干能力边界；本轮为排查核验，没有改生产代码、部署、开启采集或新增搜索。

API 与网站共用 `serp_requests` 中的搜索记录。相同记录、权限范围、筛选和字段合同下，已核验样本的真实标题、摘要、链接、话题/报道与保留的源字段一致；默认列表、搜索条件、排序和简版 CSV 并不等价。网站是管理工作台，客户 API 仅允许本人数据与公共历史，不能获得管理员权限或其他客户私有数据。

## 对外接入是否可用

- 不带网页登录 Cookie、携带 `X-SearchFlow-Access-Key` 和 `Authorization: Bearer <客户Key>`，实际数据字段接口返回200。缺少任何一层或使用无效客户Key均401。站点Key是网站前置访问凭据，不是客户之间的数据隔离标识。
- 正式主目录54条路径：30条客户功能路径、3条旧库兼容路径、21条管理路径，不能把54条都宣称为客户功能。生产读链路已测；搜索创建、批量、暂停/继续、取消、额度等写流程本轮在隔离测试核验，没有用实际新搜索重放所有写操作。
- `/v1/google/openapi.json` 是另一套研究目录；实际 capabilities 明确十个研究域 not_covered、live_enabled=false。GA4、GSC、Ads、数值历史Trends、完整AI等不因存在路径而可用。第三方优先使用主 `/v1/openapi.json` 和 `/v1/capabilities`。
- API为异步搜索：202只表示接单，最终需看任务succeeded/failed。此轮没有新采集，不能用已存数据读取证明今日各上游搜索成功率或SLA。

## 已确认的问题（仍未修改）

| 优先级 | 问题 | 真实验证与影响 |
| --- | --- | --- |
| P1 | 排名分析显式format参数失败 | `/v1/analytics/rankings?...&format=csv` 返回400；显式format=json也400。不带format的JSON正常。源码将format错误传给history_report，隔离调用复现TypeError。总览与首页汇总CSV正常。当前可先取默认JSON。 |
| P1 | 网页复制接入示例漏站点访问头 | 正式页面的apiExample/apiPollExample均没有X-SearchFlow-Access-Key；脱离网页登录环境仅Bearer实际401。别人直接复制网页示例无法接入，须补齐第二个头。 |
| P2 | 字段目录漏列图片专用字段 | `/v1/data/fields` 的occurrence_fields由Web样本生成，漏image_url、source_page_url、thumbnail_url、width、height、upstream_rank、observed_order、rank_basis、media_downloaded九项。真实图片响应和完整CSV仍有相应数据，不是保存丢失。 |
| P2 | 已存状态无法作为筛选值 | 全库存在1条Web service_error；使用classification=service_error返回400。该记录通过普通列表/详情仍可读取，但状态枚举没有覆盖真实旧数据。 |
| P2 | 国家筛选大小写导致假空结果 | 同一News关键词，country=US返回200但0条，country=us返回真实记录。提交搜索region使用US，而数据过滤直接匹配保存的gl=us，大小写未统一。接入时不能直接复用提交参数值。 |

另一个接入边界：浏览器OPTIONS预检返回501，没有CORS允许头；在空白独立页面跨域fetch实际失败。因此**第三方服务器/后端可以按两层鉴权接入，其他网站前端不能直接跨域调用**。本轮未擅自扩大允许来源或变更鉴权。

## 全量数据核验

在一个只读数据库快照内扫描全部39962条请求，使用生产冻结包的标准化函数检查每条结果出现及源字段保留、标题摘要、occurrence ID、Trends父子数量及News日期依据。扫描耗时647.2秒。

| 来源与范围 | 请求数 | 结果出现数 | 话题数 |
| --- | ---: | ---: | ---: |
| Web公开 | 31763 | 257778 | 0 |
| Web私有 | 4 | 18 | 0 |
| News公开 | 8140 | 552055 | 0 |
| News私有 | 3 | 91 | 0 |
| Trends公开 | 31 | 868 | 310 |
| Trends私有 | 2 | 58 | 20 |
| Images公开 | 17 | 298 | 0 |
| Images私有 | 2 | 40 | 0 |
| 合计 | **39962** | **811206** | **330** |

结果：39962条均能标准化；原始有效对象数量与标准化出现数一致；没有发现标准化丢行、源字段过滤之外的篡改、occurrence ID碰撞、状态与非空结果冲突或无效结构。URL registry的first_request引用、canonical引用及campaign/request关联均无孤儿。出现次数保留重复观察，811206不是历史去重新URL数，也不等于文章正文数。

这项全量检查是数据库与标准化逻辑的机器核对；线上HTTP、CSV、ZIP使用下面的分层样本，没有声称对39962条记录逐条发起了HTTP下载。

### 数据本身的缺失与限制

- News公开552055次出现中，**552000仍为Google包装地址，55为解析后的目标地址**；私有91次中81仍为包装地址，10已解析。没有批量回填旧历史，不能把最新10/10成功外推成全库已补齐。
- News全部552146次出现的保存时间与RSS原pubDate匹配；这不等于独立核验出版者页面的发布时间。
- 所有出现记录都保留标题。Web有917次缺摘要；News摘要全部存在；图片和Trends报道不强行填造摘要。作者与canonical在这些基础结果中均缺失。
- 图片338次出现均有图片链接和来源页，缩略图均缺失；333次有宽度、336次有高度。没有下载原图，也没有核验尺寸真实性。
- Trends仅330个热点话题及926次相关报道出现，没有关键词历史数值曲线。网页话题行数与API报道行数不同是结构差异。
- 6次历史Web出现的URL包含敏感参数，标准化合同明确标为redacted_sensitive_url，不能称这6条原始URL按字节完整对外交付。其记录、标题及经脱敏的源字段仍保留。
- 平台交付接口读取的是已有证明缓存；该缓存已过期，旧9条中有1条原摘要不匹配、终态unknown，本轮没有重新上传或向Whale读回，不代表全部本地数据都已上平台。

## 网站与API为什么看起来不一样

| 项目 | 网站 | 客户API |
| --- | --- | --- |
| 数据范围 | 公共历史；客户私有任务不进入公开工作台 | own默认本人；public读公共历史；accessible公共加本人 |
| 当前范围数量 | 公共39951条 | 验证客户own=11、accessible=39962；生产目前仅一个客户账号，不以此冒充线上多租户交叉验收 |
| 数据列表 | 通常按结果卡片；Trends按话题展示 | requests按采集请求，每个请求再含occurrences、topics、features |
| 搜索 | 结果视图可匹配关键词、标题、摘要、链接 | query仅匹配原搜索关键词；同窗Australia在网页命中1条、API关键词过滤0条 |
| new_only | 结果视图保留新链接/含新报道的话题 | 挑选有新贡献的请求，仍保留请求内旧链接出现 |
| 列表排序 | 最新优先，offset分页 | 最新优先，cursor分页 |
| 整批快照 | 网站导出按当前筛选读取 | 快照按最早优先，最多10000请求/128MiB基础包；到限额明确truncated。max_requests=2不是“最新两条” |
| 简版CSV | 网站用户结果50列 | 旧客户Web/News/Trends7列，Images14列，不是完整字段出口 |
| 完整交付 | 网页详情保留原始已存结构 | 使用完整JSON、完整JSONL、74列CSV或ZIP；CSV的request/result/topic/feature是不同记录类型 |
| 日期与链接 | 部分显示归一化URL、未知值显示未知 | 同时有原始/规范URL、时间依据、未知/null、源字段及增强字段；不能按显示字符串简单比较 |
| 分析 | 仅公开Web/News/Trends投影 | 分析API同范围，排除图片和私有数据；图片必须走数据API，不能把分析拒绝图片当作图片数据不存在 |

公共分析投影39934 = 公共39951 - 图片17，pending=0，私有投影0；投影明细811011 = 公共非图片出现810701 + Trends话题310。网站和API首页/总览汇总一致，排名数据一致，仅生成时点/窗口截止时刻随调用不同。

需要完整读取与网站相同的公共数据，使用：

```text
GET /v1/data/requests?scope=public&field_contract=prd-v1&limit=50
```

依next_cursor读取到has_more=false；本人任务改scope=own。完整单次下载：

```text
GET /v1/jobs/{id}/export?profile=full&format=jsonl
GET /v1/data/requests/{request_id}/export?format=csv
```

当前有效的只读接入示例（均为占位符）：

```bash
curl 'https://searchflow.20-150-145-162.sslip.io/v1/data/requests?scope=public&limit=1' \
  -H 'X-SearchFlow-Access-Key: <站点访问凭据>' \
  -H 'Authorization: Bearer <客户API凭据>'
```

## 线上与隔离验证证据

177条线上矩阵请求：148次200、5次401、12次404、1次409、10次400、1次501。401为鉴权负例，404为11条私有请求公开读取及私有任务公开进度，409为失败任务不能导出成功数据。400中包含正常参数负例和上述真实故障，501为不支持OPTIONS；不能将全部177次称为通过。

已核验：

- 四来源最新记录ID与网站一致，逐字段对照网站保存响应与完整API；失败/空结果明确区分。成功Web样本含富结果，富结果分项与完整导出一致。
- 请求及任务cursor第二页无重复；items/topics/features分项数量、offset及详情一致。11个已有客户任务全部详情/进度可读，10成功任务标准数据一致，1失败任务正确409。
- 11条私有原请求逐条public读取404；公共分析与投影没有私有数据。跨客户隔离及错误缓存关联用隔离数据库测试覆盖，生产仅一个账号不伪装成两个真实客户。
- 四来源API JSON、单请求JSONL和74列CSV的request/result/topic记录一致；网页结果、JSONL与50列CSV一致。Trends已展开报道，不将10话题当30报道。
- 四来源各2请求的ZIP全部通过独立bundle校验与full.csv SHA256校验；这些公开样本都truncated=true。本人11请求/207次出现的ZIP完整、truncated=false，与JSONL及240行74列CSV一致。8条公开ZIP记录再与API详情逐项相同，4条旧合同兼容入口显式prd-v1也一致。
- 89项相关隔离测试、10个子测试通过，无跳过；未覆盖到的排名CSV故障另用线上400和本地TypeError实证复现，测试通过不代表没有漏测项。

证据目录：`state/api-data-audit-20261110/`。重点文件：census.json、matrix-*.json、matrix-verification.json、extended-verification.json、browser-integration.json、website-examples.json、url-edges.json、production-final.json。

## 最终状态

原始请求39962、pages332768、公开投影39934、旧Whale outbox9，和核验前一致；持续采集、正文、Whale上传与领取均false，启用监测/活动客户任务/在途lease均0。只读认证会更新Key最近使用时间和读取限流计数，没有新增采集用量。临时隔离PostgreSQL/Redis由脚本清理；浏览器任务完成后清理。未对生产进行修复或扩大CORS权限。
