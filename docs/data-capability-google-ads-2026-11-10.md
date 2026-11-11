# Google Ads 历史关键词量候选（2026-11-10）

新增 `realtime/data_capability_google_ads.py`，默认禁用，无环境凭据发现、OAuth授权/刷新、服务自动挂载或生产修改。四能力现在均有本地来源适配候选，仍没有真实来源准入与完整交付验收的证明。

## 官方协议依据

通过 ego lite 任务空间90读取并保存官方指南、v26请求/结果/指标/月枚举/鉴权与REST绑定，见 `state/data-capabilities-20261110/provider-contracts/google-ads-*-stage14.json`。空间已finish一次并核验不存在；没有点击API Explorer的Execute、申请OAuth或调用任何Ads接口。

- [历史指标指南](https://developers.google.com/google-ads/api/docs/keyword-planning/generate-historical-metrics)：关键词与近似变体可合并，返回数量可少于输入；月量是近似量，非Trends指数。源单次最多10,000词，本项目合同仍限制100个输入。
- [REST方法](https://developers.google.com/google-ads/api/reference/rpc/v26/KeywordPlanIdeaService/GenerateKeywordHistoricalMetrics?transport=rest)：固定POST `/v26/customers/{customerId}:generateKeywordHistoricalMetrics`，无分页或逐词partial_failure字段，不虚构源未提供的失败原因。
- [HistoricalMetricsOptions](https://developers.google.com/google-ads/api/reference/rpc/v26/HistoricalMetricsOptions)：最多过去四年，可能只返回请求范围中有数据的子集。
- [MonthlySearchVolume](https://developers.google.com/google-ads/api/reference/rpc/v26/MonthlySearchVolume)：null表示该月数据不可用。[指标对象](https://developers.google.com/google-ads/api/reference/rpc/v26/KeywordPlanHistoricalMetrics)区分广告竞争度、竞争指数和micros金额。
- [请求结构与头](https://developers.google.com/google-ads/api/docs/concepts/call-structure)、[JSON字段映射](https://developers.google.com/google-ads/api/rest/design/json-mappings)：Bearer、developer-token、可选login-customer-id；JSON用lowerCamelCase，int64保留精度。

## 准入和传输

`AdsScope`要求显式customer/manager、OAuth主体/client、project、生产access level、适用用途、地区/geo常量、语言常量、已核验客户币种、不可包含凭据的证明引用与核验有效期。每个任务通过 `admission_for()`重查同一绑定和有效期；OAuth access token另有到期时间。它是可信操作员配置的承载结构，不会自行完成Google账号、项目、权限或币种核验；尚无真实配置，不能把合成scope当正式准入。

启用还要求原响应保留许可、独立账单核对器 `bill_for(status, sanitized_response, request_id)`、明确计费单位/逐次上界以及响应仓、before_call/confirm_bill/defer_calls。即使最终来源费用为0，也须独立证据，不默认为免费。额度/用量与货币费用不能混为一个值。

固定googleads.googleapis.com、v26、已绑定customer；不接受任意URL，不跟随跳转、不使用隐式代理。凭据只入头，不进URL/正文/日志。libcurl覆盖DNS/连接/读取的总期限不超过任务、OAuth与scope到期时间；原文流式16MiB、头64KiB。单个任务只有一次上游POST，无自动刷新或收费重试。

原生REST不回显查询条件：响应绑定依据是固定TLS账号端点、精确发送的请求体及安全request-id；日志明确记录该依据和公开scope证明引用，不伪造源查询回显。缺失/不安全request-id、坏JSON/重复键/超限/未知传输均保留明确不可保存原因并待对账；完整脱敏响应先入独立日志，再核对费用与解析。未知费用保留预留和账号隔离；已知HTTP失败结算已知费用、无成功事实；Retry-After在费用核对前持久保存共享冷却。

## 输入映射和事实语义

保留原始输入顺序及重复，发送时只做字符串精确去重，不擅自大小写/中文/近似词归并。国家和语言必须匹配当前经过核验的单个scope，不从任意代码猜geo或language常量；两种网络显式映射。候选限定过去48个完整月，不请求当前或未来月；adult keywords明确关闭。此限制不等于真实覆盖保证，也不虚构设备维度。

每个原生text/closeVariants返回一个合并组，全部对应输入映射到同一组，不平分搜索量。只接受精确可证明的词关联；未知变体、重叠组或无法绑定的结果拒绝规范化，完整源响应仍可按许可交付。没有返回的输入标not_observed并说明源未给逐词原因，不把它说成真实0或永久无需求。

每个月保留year/month/native来源引用；显式null与缺失月份分别注明。近似数值0是available的近似0，不标true_zero；来源不声明舍入/阈值时保留未知，不编造rounded/range。数值严格有限非负int64，以十进制字符串保存，不经float。缺失metrics/月/输入保留其他已返回可验证数据并标partial；坏单位、月份越界或重复、歧义组及未知字段不静默丢弃。

均值与月值独立保存，不自己重算平均。指南说明请求窗口可影响均值，但响应不回显实际有效均值窗口，且可能返回月份子集，故 `statistic_window` 两边均null并附reason，原请求窗口另存source_fields。不能把返回月份的首尾或请求范围写成已确认统计窗口。只要此窗口仍未知，回答保留partial，而非宣称完整可比。

广告competition/competition_index不叫SEO难度；CPC及20/80百分位bid保持micros、明确来自当前核验customer的币种，不换算或混币。模型无关；源未提供独立观测时间则unknown，本机接收时间不替代它。

## 客户合同与验证

OpenAPI升级1.6.0，`CapabilityVolume.statistic_window`增加严格的未知分支：两个边界均null且reason非空；已知分支沿用原合同，禁止半未知和已知窗口夹带未知理由。fields同步语义；不改变旧事实或增加数据库迁移。现有客户任务、groups/months分页、五格式固定摘要导出、订阅、独立响应日志/ZIP和租户/撤权保护复用同一链路。

本轮测试全部是显式合成来源及准入：中英文参数、重复/近似词合并、无数据/null/近似0、缺失月份、超界/int64/枚举/歧义、单次传输/流式限制/安全头、未知账单隔离、已知限流费用与冷却、真实本地客户HTTP的分页/五格式/独立ZIP/订阅/撤权。最终计数与清理以 `state/data-capabilities-20261110/ads-manifest.json` 为准，不计入100真实关键词或来源金标。

仍缺真实Ads主体/项目/OAuth/customer/权限/用途/币种核验、真实Key/费用和许可；没有取得或消耗这些凭据。此原生接口没有逐词失败状态，当前不自动推断失败子集并收费重试；跨作业失败子集重试及计费去重仍须完成明确策略和真实验证。其他三来源真实准入、正式服务挂载、平台各域固定版本读回、真实规模与七天验收也未完成。生产采集/正文/上传/领取/监测保持关闭，未部署或提交Git。

最终全量隔离1282 passed / 641 subtests / 0 skipped，153.48秒；专项105 passed / 211 subtests，28.68秒，与全量重叠。两套本轮PostgreSQL/Redis及测试HTTP/worker已停止、端口关闭，自己的空socket目录已删除。web.py/serp_page.py摘要与前阶段一致。

## 第十七阶段来源重试跟进

默认仍一次调用且来源禁用。可信操作员可显式冻结有界策略；仅已确认费用、没有成功数据的429/503错误可按原预算继续，指数退避/Retry-After和总窗口生效，最终失败仍写共享冷却；未知费用隔离不重试。每次phase、原响应和账单独立保存，见[来源退避与验证](data-capability-backoff-2026-11-10.md)。本页早先“一律不重试”描述为第十四阶段历史行为；真实许可/身份/费用及关键词覆盖仍未验收。
