# 新数据能力客户 API 合同与本地验证

版本：主 OpenAPI 1.2.0；API `searchflow-capability-api-1`；请求 `searchflow-domain-jobs-1`；事实 `searchflow-facts-1`。负责人 Codex，对应 DATA-01 / DATA-06。本文冻结本地第四阶段的路径、参数、权限和错误，不表示生产已部署或真实来源已准入。

当前实现已挂入主客户 HTTP 分发器，默认不配置新 runtime。没有来源时创建返回409，没有受监督的可用执行器时返回503，不返回空转202。四来源旧 `/v1/search`、默认任务列表、原导出、订阅和已有分析继续兼容。新域订阅、网页、实际源执行器与平台读回仍待完成。

## 1. 启用与鉴权

代码：`data_capability_http.py`（路由）、`data_capability_queries.py`（可信 runtime / 查询 / 游标）、`data_capability_schema.py`（主 OpenAPI / 能力 / 字段目录）。主入口只增加小范围接入：`product_http.py`、`product.py`、`customer_data.py`、`customer_schema.py`；不修改 web.py 或 serp_page.py。

只有可信宿主显式把 `CapabilityRuntime` 实例挂到 HTTP server 的 `data_capability_runtime` 属性才可使用新数据存储；必须显式初始化独立 sf_capability_* 表，注入当前 grant 查询、私有持久游标签名密钥和受监督执行器健康检查。构造 runtime 不初始化数据库、不启动线程、不访问来源。默认 health check 拒绝全部创建。客户请求不能配置 grant、提供方凭据、预算限额或执行器。

所有数据请求继续经过既有站点访问门，再验证客户 Bearer；不新增 CORS，也不开放客户管理写操作。新域当前仅支持 own，所有读取按真实客户 account_id 隔离，不把 API Key 的记录 ID 当租户。旧 own/public/accessible 仍适用于旧数据。

`GET /v1/capabilities` 新增 data_capabilities；未挂载时 availability=not_configured，挂载后为 tenant_dependent，仍须逐租户准入。source_verified=false、production_release=not_deployed，不能把本地 fixture 的202或目录存在当作真实来源上线。`GET /v1/data/fields` 在 data_capabilities 下列出字段、独立模型、数值状态、引用语义、完整导出格式和限制。主 `/v1/openapi.json` 已声明新增路由与输入/输出模型。

## 2. 任务与费用合同

| 方法/路径 | 输入与行为 |
|---|---|
| POST /v1/data/jobs/preview | 四类能力参数，不带budget；只读预览来源、许可/用途/保留期限、批准的整任务费用上界、币种/单位、执行器可用性和当前预算快照 |
| POST /v1/data/jobs | 能力参数 + budget={maximum_cost,currency}；Idempotency-Key必填，1–120字符；通过准入、健康检查与原子预算预留后202 |
| GET /v1/jobs?domain=data | 新域任务列表；不改变domain=search或不传domain时的旧列表 |
| GET /v1/jobs/{id} | 新域和旧搜索均按本人任务UUID读取；新域保留独立金额预留/实际费用与结果引用 |
| GET /v1/jobs/{id}/progress | 真实任务状态、终态标记、错误及费用；没有虚构源进度百分比 |
| POST /v1/jobs/{id}/cancel | 空对象；已鉴权所有者在来源撤权后仍可停止自己的支出，不返回来源事实；在途保留实际费用 |
| GET /v1/jobs/{id}/data | 完整固定观测、当前源权限/到期检查、本地缓存关联及年龄依据 |
| GET /v1/jobs/{id}/export | format=json/jsonl/csv/zip/manifest，默认jsonl；可用snapshot_hash锁定版本 |

四类输入精确字段见 [内部请求合同](data-capability-contract-2026-11-10.md)。国家格式规范为大写，原词/prompt保留；未知字段和重复 JSON 键拒绝，创建/取消请求上限64 KiB，预算字符串上限100字符。金额必须为有限非负十进制字符串或整数，最多9位小数；不能传浮点JSON金额。country/language/device/网络等仍须由实际来源验证支持范围。

费用预览是批准配置的整任务上界，包含允许的重试、分页/展开，不是供应商实际账单或已采购报价；至多60秒有效，创建时仍重查。预览不创建任务、不预留、不扣旧搜索额度。实际费用在来源返回后独立结算，失败/取消可以发生真实费用。UTC日/月预算的窗口口径沿用第二阶段：任务创建时间，不冒充供应商按调用日出账。

相同键相同参数/预算返回原任务；参数或预算不同409。原任务的重放不会因执行器已停或新报价上升而重发调用，但仍必须通过当前权利检查。默认等待执行期限300秒（可信宿主可设1–3600秒），受理或任务读取时按租户回收超时queued任务并释放未执行预留；已经调用、lease过期的任务进入reconciliation_required，保留金额等待对账，不自动重试或假定免费。部署时仍需实际执行器监督与周期维护，本地实现不代表生产已常驻运行。

## 3. 观测与独立模型

| 方法/路径 | 返回 |
|---|---|
| GET /v1/data/observations | 观测元数据分页，不含大payload；详情取完整信封 |
| GET /v1/data/observations/{id} | 观测、实体/修订、原始/标准结构、payload_hash、snapshot_hash和保留期限 |
| GET /v1/data/trends/series | series摘要，不内嵌全部点 |
| GET /v1/data/trends/series/{id} | 完整series，包含全部已保存点 |
| GET /v1/data/trends/series/{id}/points | 点分页，保留真实顺序及0/低量/partial/修订 |
| GET /v1/data/keyword-volumes | 搜索量组摘要，不内嵌月份 |
| GET /v1/data/keyword-volumes/{id} | 完整组、均值窗口、输入合并组、月份和实际商业字段 |
| GET /v1/data/keyword-volumes/{id}/months | 月份分页；缺失与0分别表达 |
| GET /v1/data/ai/answers | AIO/AI Mode回答摘要，使用capability区分通道 |
| GET /v1/data/ai/answers/{id} | 全文、结构块、引用及普通链接 |
| GET /v1/data/ai/answers/{id}/blocks | 块分页 |
| GET /v1/data/ai/answers/{id}/citations | 明确引用分页及源给出的文本/块映射 |
| GET /v1/data/ai/answers/{id}/links | 普通链接分页，与引用分开 |

模型路径中的id必须使用列表返回的object_id；它绑定observation_id、模型类型及原record.id，不以重复的原生series/answer ID冒充全局唯一ID。原record.id仍完整保留（标准记录ID上限1024字符），object_id不是权限凭据。

列表统一支持capability、provider、country、language、from、to、limit、cursor；from/to指存储/创建时间，包含from、不包含to，必须带时区。模型列表限制capability为适用域；任务列表另支持status。设备/URL等无意义筛选明确400。列表limit默认20、范围1–50；旧任务列表保持默认30、上限100。子记录limit默认100、范围1–500，仅接受limit/cursor。

排序固定为存储时间降序、观测UUID降序，同一观测的多个根记录再按源位置升序；任务按创建时间/UUID降序，子记录按位置升序。HMAC游标1小时有效，绑定租户、路由/类型、规范化筛选及排序；子记录再绑定固定观测、父记录及payload_hash。不能把观测游标用于系列，不能修改国家、租户或父记录继续翻页。改变页大小可继续同一排序。列表是当前授权数据的keyset分页，不宣称跨事务长期数据库快照；完整导出绑定单个不可变观测。

当前权利在SQL中先限制来源/许可/用途等，并逐结果再检查，撤权或过期不会通过has_more/cursor泄露不可读观测数量。根模型列表从不可变事实取根记录，缺失投影不会静默变成空结果；子记录读取与事实信封对照，损坏返回503，支持可信投影修复。尚未验证全库复杂筛选的长期性能。

## 4. 导出与固定版本

`GET /v1/data/export?capability=<capability>&observation_id=<uuid>&format=zip&snapshot_hash=<hash>` 选择一个固定观测。新形式只允许这四个参数；旧URL域名筛选domain及旧批量快照继续走原实现。多观测新域快照尚未接通。

五种格式及精确父子重建、公式转义、长文本、资源上限、摘要和校验语义见 [事实与导出合同](data-capability-facts-2026-11-10.md)。新域不使用旧7/14列，也不需要profile=full。json/manifest是新增观测形式专用，旧快照仍只支持原zip/csv/jsonl。

先通过独立已鉴权的job/data取得观测身份与hash，再校验文件：

```python
from pathlib import Path
from realtime.data_capability_export import verify_bundle
# observation来自本次已鉴权的 /v1/jobs/{id}/data 响应，不从ZIP自身取信任锚。
proof = verify_bundle(
    Path('bundle.zip').read_bytes(),
    expected_observation_id=observation['observation_id'],
    expected_payload_hash=observation['payload_hash'],
    expected_snapshot_hash=observation['snapshot_hash'],
)
```

每次生成都重查当前权限，不保留可绕过撤权的持久下载文件。已下载外部副本不能主动撤回，客户仍须按许可处理。manifest给出每文件字节数和SHA256；payload_hash是规范化JSON的摘要，不冒充HTTP源字节摘要。

## 5. 完整鉴权示例

当前默认会返回source_not_admitted，不应为执行示例而自行购买、配置账户或开启采集。BASE指正式工作台，环境变量由客户本地设置，密钥不写进前端或文档。示例budget只展示格式，不代表真实价格或费用授权。

```sh
curl --fail-with-body --silent --show-error "$BASE/v1/data/jobs/preview" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" \
  -H "Authorization: Bearer $CUSTOMER_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"capability":"trends_history","country":"US","language":"en","terms":["electric vehicles"],"start_date":"2026-01-01","end_date":"2026-01-31","category":0,"search_type":"web"}'

curl --fail-with-body --silent --show-error "$BASE/v1/data/jobs" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" \
  -H "Authorization: Bearer $CUSTOMER_API_KEY" \
  -H "Idempotency-Key: $JOB_REQUEST_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"capability":"trends_history","country":"US","language":"en","terms":["electric vehicles"],"start_date":"2026-01-01","end_date":"2026-01-31","category":0,"search_type":"web","budget":{"maximum_cost":"1.00","currency":"USD"}}'

curl --fail-with-body --silent --show-error "$BASE/v1/jobs/$JOB_ID" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" -H "Authorization: Bearer $CUSTOMER_API_KEY"
curl --fail-with-body --silent --show-error "$BASE/v1/jobs/$JOB_ID/data" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" -H "Authorization: Bearer $CUSTOMER_API_KEY"
curl --fail-with-body --silent --show-error "$BASE/v1/jobs/$JOB_ID/export?format=zip" \
  -H "X-SearchFlow-Access-Key: $SITE_ACCESS_KEY" -H "Authorization: Bearer $CUSTOMER_API_KEY" \
  --output bundle.zip
```

其他三类创建body（沿用相同两层鉴权与独立Idempotency-Key；预览时去掉budget）：

```json
{"capability":"keyword_volume","country":"US","language":"en","keywords":["电动汽车","electric vehicles","electric vehicles"],"start_month":"2026-01","end_month":"2026-03","network":"google","budget":{"maximum_cost":"1.00","currency":"USD"}}
```

```json
{"capability":"aio_answer","country":"US","language":"en","prompt":"What is an electric vehicle?","device":"desktop","budget":{"maximum_cost":"1.00","currency":"USD"}}
```

```json
{"capability":"ai_mode_answer","country":"US","language":"en","prompt":"Explain electric vehicle charging options","device":"mobile","budget":{"maximum_cost":"1.00","currency":"USD"}}
```

## 6. 错误与未完成边界

400：invalid_parameter / invalid_body / invalid_budget / invalid_cursor；401：既有站点访问门或客户凭据无效；404：not_found（包括其他租户）。409：source_not_admitted / admission_invalid / admission_changed / currency_mismatch / budget_not_configured / idempotency_conflict / job_not_ready / version_conflict。410：expired。413：请求或完整响应/导出超资源上限。429：budget_exceeded 或既有限流/导出并发已满。503：executor_unavailable / data_capabilities_disabled / corrupt_snapshot / corrupt_projection / service_unavailable。错误沿用稳定code与request_id，不返回凭据或短效token。

旧 `/v1/data/subscriptions` 仍只覆盖旧occurrences/new_urls；不能以new_urls交付曲线、数值或回答，目录明确标记新域订阅not_connected。本轮也未新增Webhook、CORS、分析、付费流程、真实来源/Whale写入或持续运行。各域真实来源、用途/费用、真实样本及源oracle、实际执行器、订阅、网页和平台正式读回仍为完成门槛。

## 7. 本地验证、发布与回退

新增 `tests/test_data_capability_queries.py` 和 `tests/test_data_capability_http.py`。真实HTTP与PostgreSQL验证使用独立站点门和客户Bearer、显式合成fixture执行器线程（不是Google来源）；覆盖四能力202→执行→持久化→查询/全部下载、两层鉴权负例、跨租户、撤权、取消、参数/重复JSON/游标/预算错误、原生重复ID分页及只读费用预览。合成测试不计入真实1000记录/100series/100关键词/500回答门槛。

回归命令：`bash scripts/run_acceptance_regression.sh state/data-capabilities-20261110/api-regression`。使用脚本创建和清理的独立PostgreSQL/Redis，不用生产或Docker；HTTP线程由各测试关闭。最终 **1164 passed、426 subtests passed、0 skipped**（120.93秒）。结果、XML和模块源码哈希保存在本阶段证据目录；脚本自建数据库已停止。

没有部署；web.py/serp_page.py以及生产所有开关、数据和容器均未修改。后续按实际冻结版本做逐文件备份和最小差异发布，不覆盖保护文件或运行旧迁移。回退先停止新runtime/执行器挂载，再恢复本轮四个主接入文件；保留独立事实/账本数据，不重放采集或丢弃未知费用。发布与真实验证仍未完成。


## 后续进展：新域订阅本地候选

本报告中“新域订阅not_connected”的状态已由第五阶段本地实现覆盖。主接口现支持domain=data的独立观测/修订拉取、确认账本、完整批次文件与独立校验；OpenAPI更新至1.3.0。真实来源、执行器、网页、生产发布和真实验收仍未完成。详见 [订阅报告](data-capability-subscriptions-2026-11-10.md)。


后续本地网页接入见 [工作台验证记录](data-capability-ui-2026-11-10.md)；仅本地候选，不表示真实来源或生产已接通。


本地第七阶段已补来源无关执行器与共享账号调度/费用不确定恢复，见 [执行器合同和证据](data-capability-executor-2026-11-10.md)。仍无真实适配器或生产挂载，不据此宣称真实来源可用。


后续第八阶段新增明确 SerpApi 历史曲线适配候选及兼容的持久计费证明，见 [来源合同与验证](data-capability-serpapi-2026-11-10.md)。仍默认禁用，未验证真实账号/许可/费用；未部署、不增加已上线能力声明。

后续本地合同已扩展至 OpenAPI 1.4.0，新增 `/v1/jobs/{id}/response` 和 `/response/export`。它们返回独立响应信封，不改变观测和订阅语义；配置、权限、错误及五格式见 [响应保存与交付](data-capability-responses-2026-11-10.md)。

## 后续：AI Mode 来源候选

第十一阶段通过 [独立 AI Mode 适配器](data-capability-serpapi-ai-2026-11-10.md) 验证同一客户HTTP、原生引用/普通链接子记录、固定五格式导出、独立响应及观测订阅，无新增公共路由或字段版本。仍为默认禁用的本地候选，不将 AIO 或关键词量宣告已配置。全文采用明确记录的源结构连接，provider_reconstructed_markdown 仅为辅助字段；source_verified 仍为 false。

## 后续：OpenAPI 1.5.0 分阶段来源响应与费用

本地第十二阶段保留既有路由，progress增加 source_call_attempts、confirmed_call_cost、confirmed_call_charges、call_charge_basis；未取得凭据时为null，部分合计不替代actual_cost。原response接口增加可选 capture_kind=source_call_journal/call_count，固定导出完整保留逐次范围/完整性/脱敏状态。capabilities/fields/调用示例及鉴权、跨租户、撤权、未知费用保护同步验证。详见 [分阶段来源调用合同](data-capability-source-calls-2026-11-10.md)。AIO适配仍待实现，不以多段合成回调宣称来源已准入。


## 第十四阶段：搜索量均值窗口明确未知

主OpenAPI1.6.0，CapabilityVolume.statistic_window允许原有两个已知月份，或两个null加非空reason；不允许半未知或把请求窗口当实际窗口。fields同步，五格式/订阅保留null和reason。默认禁用Google Ads来源候选见[data-capability-google-ads-2026-11-10.md](data-capability-google-ads-2026-11-10.md)，未宣告来源已配置。


## 第十五阶段：明确失败子集重试

OpenAPI1.7.0新增GET/POST `/v1/jobs/{id}/retry`、GET `/v1/jobs/{id}/lineage`。仅已结算partial关键词量的明确source_error且无关联记录输入；服务端派生新任务、独立预算/费用，父版本唯一子关系在同一事务提交。未知费用、无数据/低量/0/成功结果不重发。错误码retry_not_supported/retry_not_eligible/retry_already_created、version_conflict、idempotency_conflict均409。详见[合同和双层鉴权示例](data-capability-retry-2026-11-10.md)。

### 显式失败子集重试网页

本地“数据任务”现可对已结算的关键词量部分成功任务只读核验失败范围，重新取得来源费用预览，经新的费用上限与显式确认后创建子任务。提交响应不明时不自动再发，先查询已有子任务；重试关联展示原输入位置，原结果不合并改写。见 [第十六阶段](data-capability-retry-ui-2026-11-10.md)。API合同仍为1.7.0，未因此挂载生产运行时。

### 来源有界退避策略（第十七阶段，OpenAPI1.8.0）

新增只读 `source_retry_policy`：preview显示当前拟用策略，job/progress显示创建时冻结策略，null表示无额外调用许可。只由可信操作员配置，客户提交同名参数仍拒绝。当前仅Google Ads关键词量可获准对已确定账单且只有error对象的HTTP429/503进行有界调用；部分/混合数据及费用未知不重发，逐次phase/费用保留。次数、指数退避、Retry-After、总窗口、共享冷却与整次预算共同生效。API版本现为1.8.0，替代上节的当前版本说明；详见[实现、成本与边界](data-capability-backoff-2026-11-10.md)。

### SerpApi分阶段重试（第十八阶段，OpenAPI1.9.0）

source_retry_policy现可由操作员分别批准Trends/AIO/AI Mode；仍不能从客户JSON启用。总次数/窗口/预算覆盖整个任务和全部原生阶段，AIO只重试失败的search或expand，不重发成功搜索，不延长短效token。Processing、部分/混合结果、矛盾echo及未知费用不自动重发。null表示无额外重试许可，AIO既有正常两阶段调用保留。逐次phase/账单/来源ZIP可查；主OpenAPI现为1.9.0，详见[分阶段实现与边界](data-capability-native-backoff-2026-11-10.md)。

### 响应清除与备份恢复（第十九阶段，可信运维端）

新增独立响应墓碑与 `ResponseRetention.export/replay/tick`，不新增客户清除/导入接口，不改变 OpenAPI1.9.0。响应原文及含查询参数的元数据会一并清除；客户读取和下载继续返回 410，跨租户保持404。签名检查点仅声明 `source_response_only`，不能用于宣称任务、事实库、订阅、平台或外部文件已经清除。备份恢复必须在提供服务前使用独立保存的最新完整检查点重放；自动调度和恢复启动门禁未部署。详见[响应清除与恢复边界](data-capability-retention-2026-11-10.md)。

### 事实清除与发送前复查（第二十阶段）

新四域详情、子记录、缓存复用和订阅重领/ACK拒绝已清除事实（410；跨租户404）；列表、类型根记录和订阅待交付/历史排除墓碑。可信清除删除观测内容/参数及子记录，并清除已安装响应仓中的关联副本；费用和任务账本不改写。任务参数/重试计划及平台尚未统一物理清除，事实墓碑尚无备份重放，不可称全链路删除。三条导出路径在生成后再检查当前权利、墓碑及固定版本，失败不发送已生成文件。OpenAPI1.9.0的错误/交付语义与capabilities已补充，事实字段及固定摘要规则不变；已交付外部文件不能主动撤回。详见[实现与验证](data-capability-fact-retention-2026-11-10.md)。

### 联合墓碑恢复（第二十一阶段，可信运维）

可信 `DataErasureCheckpoint` 将事实与已安装响应仓墓碑绑定为独立签名完整检查点，在同一事务重放；响应组件不得静默省略。`replay_file` 有界读取、先检查独立固定的文件SHA256，拒绝重复字段或损坏JSON；对象重放同样要求独立固定摘要与完整签名。恢复互斥先于完成/缓存复用的任务行锁，解锁后重查墓碑；同事件换ID、身份或范围冲突整批回滚。无新客户恢复/删除端点，OpenAPI仍1.9.0，capabilities的恢复状态已更新；事实字段和现有鉴权不变。必须停服并由操作员取得独立保管的最新摘要，自动外部保管和启动门禁未配置；任务参数/重试/平台/外部文件清除及严格传输屏障仍未完成。详见[恢复实现与边界](data-capability-checkpoint-2026-11-10.md)。

### 任务查询内容清除（第二十二阶段）

可信任务清除覆盖共享观测别名、向下派生重试、任务查询参数/明文幂等键/对账自由文本/事件detail/重试plan，并清事实/响应；单次闭包最多1000个任务/观测，失败回滚。已清除任务详情/进度/关联/重试/响应/取消/导出返回410，列表排除，跨租户404；原键无法重新创建收费任务。queued取消释放未执行预留，在途未知费用仍持有、已知费用正常结算，已结算账目不改。OpenAPI1.9.0任务路由、capabilities与fields同步，无新增客户删除接口。联合签名检查点升级v2，增加任务墓碑；v1继续只覆盖事实/响应。独立最新摘要、停服恢复和未完成的其他清除范围见[实现与验证](data-capability-job-erasure-2026-11-10.md)。


### 本地第二十三阶段：恢复启动门禁（2026-11-10）

- 增加逐服务实例显式绑定的RestoreGate：进程默认关闭，只有本进程完成独立固定ID/SHA256匹配的v2联合重放并提交才放行。旧回执不自行放行，错误签名/文件/事务保持关闭；并发旧重放不能覆盖较新关闭，回执或任务附表丢失锁闭且需重新重放。
- 新数据域HTTP/订阅/创建/读取/导出及worker启动/领取/付费阶段接入503 restore_required；旧搜索路由与双层鉴权保持。在途请求不可撤回，已知费用进入对账而不发布事实，未知费用继续保留；内部账目/恢复端口可用。未绑定实例保持原行为，必须每个进程启动前绑定，尚未正式装配部署。
- 独立最新检查点自动保管、订阅元数据统一清除、严格发送屏障、整域撤权、平台副本及真实许可/费用/金标/正式读回/规模/七天仍未完成；不得把本轮称GOV-04全验收。详见docs/data-capability-restore-gate-2026-11-10.md，测试/清理见本阶段最终记录。无生产恢复/删除/部署、外部调用/收费或Git提交。

第二十三阶段最终隔离回归 **1406 passed / 730 subtests / 0 skipped / 0 failed**（210.33秒）；专项 **141 passed / 108 subtests**（24.26秒，与全量重叠）。609个OpenAPI引用、503恢复状态/字段/双层鉴权合同通过。两套本轮PG/Redis及测试HTTP/worker均停止、端口关闭，自己的空socket与合成PG目录清理，日志保留；保护文件摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终源码与清理证据 `state/data-capabilities-20261110/restore-gate-manifest.json`；GOV-04和真实全需求仍未完整验收。


### 本地第二十四阶段：订阅元数据清除（2026-11-10）

新增可信SubscriptionRetention，单事务清订阅名称/筛选/明文幂等键/批次/seen，暂停并保留独立事实与账目；首次墓碑不可变、并发幂等、失败回滚。原键/标记键不能重建，所有拥有者读取/控制/ACK/导出410，外租户404，列表排除。主OpenAPI/fields/capabilities说明范围；无新客户删除端点。

订阅墓碑尚未纳入联合检查点，存在墓碑时v1/v2导出/重放明确409 scope拒绝，不冒充完整恢复；旧备份丢失墓碑仍待外部最新证明和下一版联合实现。正式挂载、独立保管、严格发送屏障、整域撤权、平台/真实来源/费用/金标/规模/七天继续未完成。详见docs/data-capability-subscription-erasure-2026-11-10.md，最终测试/清理见本阶段后续记录；未生产删除/恢复/部署、外部调用/收费或Git提交。

第二十四阶段最终隔离回归 **1412 passed / 742 subtests / 0 skipped / 0 failed**（219.30秒）；专项 **147 passed / 120 subtests**（25.83秒，与全量重叠）。618个OpenAPI引用与订阅清除/恢复范围合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产删除/恢复/部署、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/subscription-erasure-manifest.json`；订阅联合恢复及全需求仍未完成。


### 本地第二十五阶段：订阅墓碑联合恢复v3（2026-11-10）

联合检查点v3完整含订阅墓碑，与响应/任务/事实单事务重放；先落标记、再有界元数据扫描，原键换ID或租户/请求/键身份冲突整批回滚。清订阅内容/批次/seen，缺失原行预标记，重复恢复不跳过复活内容。新增独立订阅计数回执，不改旧表列；v1/v2保留原范围兼容，存在订阅墓碑时拒绝旧范围，不能冒充订阅恢复。

逐进程门禁改为要求独立固定ID/hash的v3新重放，校验订阅计数附表，旧v2不能放行，丢失附表重新锁闭。主OpenAPI1.9.0、fields/capabilities更新。详见docs/data-capability-subscription-checkpoint-2026-11-10.md。独立最新证明自动保管/正式装配、严格发送屏障、整域撤权、平台和真实来源/费用/金标/读回/规模/七天继续未完成；无生产恢复/删除/部署、外部调用/收费或Git提交。测试和清理见本阶段最终记录。

第二十五阶段最终隔离回归 **1419 passed / 749 subtests / 0 skipped / 0 failed**（208.01秒）；专项 **154 passed / 127 subtests**（28.32秒，与全量重叠）。618个OpenAPI引用与v3范围/恢复门禁/双层鉴权合同通过。三套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产恢复/删除/部署、外部调用/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/subscription-checkpoint-manifest.json`；GOV-04及真实全需求仍未完整验收。


### 本地第二十六阶段：新域显式装配与启停（2026-11-10）

新增CapabilityDeployment，统一service/grant/响应仓/执行器/runtime/v3门禁，拒绝错配/重复/占用。无导入或构造副作用，显式初始化→固定文件恢复→全部worker启动→既有鉴权server挂载；统一admission阻止部分启动期间领取，失败关闭并停止已启动worker。API也需running，重启恢复后尚未start不提前开放。

停止先关准入再有界等待，保留挂载返回503，实际线程未退出不冒充停止、不允许重放/初始化/解绑；费用继续独立对账。仅解绑自己的挂载，不覆盖他人，重启须重新恢复。主OpenAPI1.9.0/capabilities更新，无新客户运维接口。详见docs/data-capability-deployment-2026-11-10.md。

同进程候选尚未接正式serve/systemd，分进程监督/真实来源许可/Key/费用/金标/Whale读回/独立保管/严格发送/整域撤权/规模/七天仍待；无生产部署/恢复/删除、外部调用/收费或Git提交。测试和清理见本阶段最终记录。

第二十六阶段最终隔离回归 **1427 passed / 753 subtests / 0 skipped / 0 failed**（206.57秒）；专项 **162 passed / 131 subtests**（31.61秒，与全量重叠）。618个OpenAPI引用、显式装配目录/无客户运维端点/双层鉴权合同通过。两套本轮独立PG/Redis及测试HTTP/worker已停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产部署/恢复/删除、真实调用/收费、浏览器资源或Git提交。证据 `state/data-capabilities-20261110/deployment-manifest.json`；真实接通与全需求仍未完成。


### 本地第二十七阶段：原生适配器与当前审批绑定（2026-11-10）

新增NativeSourceRoute/NativeSources，精确绑定SerpApi Trends/AIO/AI Mode及Google Ads搜索量到同一部署/响应仓/API。每域一明确来源，无自动fallback/凭据发现/预算创建。每次重读租户审批JSON，校验许可/币种/计费单位/用途/原文保留/重试策略/时间和原生validator，拒绝错配/撤权及可执行配置；账单回调签名预检，不执行账单查询。

任务及观测内部admission保留规范审批UUID引用和核验时间，公共合同校验成对/UUID/非未来；无凭据或完整审批文件持久化。主OpenAPI1.9.0/capabilities/fields同步，无新客户审批或密钥端点。详见docs/data-capability-native-bindings-2026-11-10.md。当前仍仅合成原生链路，组件不代替外部真实许可核验；真实来源/费用/金标、正式挂载/Whale读回/独立保管/撤权清理/严格发送/规模/七天未完成。未生产部署/删除/恢复、外部调用/收费或Git提交，最终测试和清理见本阶段后续记录。

第二十七阶段最终隔离回归 **1432 passed / 774 subtests / 0 skipped / 0 failed**（210.59秒）；专项 **172 passed / 175 subtests**（32.67秒，与全量重叠）。618个OpenAPI引用、原生绑定/审批引用/双层鉴权合同通过。四套本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；web.py/serp_page.py保护摘要不变。没有生产部署/恢复/删除、真实请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/native-bindings-manifest.json`；真实接通和全需求仍未完成。


### 本地第二十八阶段：四域平台候选与固定读回oracle（2026-11-10）

新增data_capability_delivery.py，完整观测候选保留四域父子/引用/顺序/修订/partial/raw状态，复用完整导出投影oracle核数量；tenant/source/observation固定身份，缓存别名不制造新事件。授权prepare与读回后重新检查当前权限/TTL/墓碑/门禁。非完整任务/账目/journal审计包，不补未保留原文。

opt-in Consumer客户端复用既有document/version协议，流式限字节/超时/严格JSON，固定platform_payload与本地独立候选全量核对；当前metadata不能替代固定正文。readable、roundtrip与正式schema/终态分账，后两者明确false，不上传、不注册、不升格回执。主OpenAPI1.9.0/capabilities同步，旧读回客户端不改。详见docs/data-capability-platform-candidate-2026-11-10.md。

仍为合成平台及本地隔离验证，Whale正式schema/权限/完整审计交付/真实上传终态读回、源许可/费用/金标、生产装配、独立保管、撤权/严格发送、规模与七天继续缺失；无生产写入/部署/恢复/删除、外部请求/收费或Git提交，最终测试/清理见本阶段后续记录。

第二十八阶段最终隔离回归 **1441 passed / 805 subtests / 0 skipped / 0 failed**（323.18秒）；专项 **200 passed / 246 subtests**（32.89秒，与全量重叠）。618个OpenAPI引用与候选未接通/不升格回执/既有鉴权合同通过。本轮独立PG/Redis及测试HTTP/worker均停止，端口关闭，自己的空socket和合成PG目录清理，日志保留；保护文件摘要不变。没有生产写入/部署/恢复/删除、真实来源或Consumer请求/收费、浏览器资源或Git提交。最终证据 `state/data-capabilities-20261110/delivery-manifest.json`；Whale正式交付及真实全需求仍未完成。
