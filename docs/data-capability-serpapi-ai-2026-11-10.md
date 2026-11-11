# Google AI Mode 独立来源候选与完整回答（2026-11-10）

对应 DATA-04 / DATA-06 的本地第十一阶段。代码 `realtime/data_capability_serpapi_ai.py`；默认禁用、未部署、未调用真实收费来源，全部验收数据为明确标记的合成响应。此项不是整份需求的完成声明。

## 来源与支持范围

按 [SerpApi Google AI Mode 官方协议](https://serpapi.com/google-ai-mode-api) 实现独立 `google_ai_mode` 同步单轮文本请求；2026-11-10 用 ego lite 读取官方参数与结果示例，保存在 `state/data-capabilities-20261110/provider-contracts/`。公开文档示例仅用于理解结构，不作为实际账号可用性或真实样本验收依据。

候选仅接受 US/GB、en、desktop/mobile；GB 映射 gl=uk。发送 no_cache=true、async=false、continuable=false、output=json，核验回显 engine/q/gl/hl/device 和请求创建时间窗口。gl 是国家本地化参数，不冒充经验证的实体网络出口。请求创建时间不作为 Google 独立观测时间；未声明的模型为未知。

[AIO 官方协议](https://serpapi.com/google-ai-overview-api) 的独立 page_token 扩展流程尚未实现，不能将 AI Mode 当 AIO。AI Mode subsequent_request_token 是后续对话入口，本候选不继续提问，不把它当本轮未完成分页；短效 token 字段、带凭据 URL 及其他字符串内重复出现的敏感值均脱敏。

启用必须由可信宿主显式配置获准来源、凭据、许可/保留政策、币种/计费单位、独立账单核对器、预算、响应仓和受监督执行器。导入与构造不读取凭据文件、不初始化数据表、不发起请求、不启动服务。现有公开能力目录继续 source_verified=false / not_configured，客户请求不能自行准入来源。未采购许可，也没有以合成账单函数替代真实费用证据。

## 完整回答与引用依据

官方示例的 reconstructed_markdown 比结构化正文更短，因此仅保留为辅助源字段，不拿摘要替代完整回答。answer_text 从源 text_blocks 按原顺序，保留 title/snippet/code、嵌套 list/text_blocks/code_block 和表格单元格，使用明确记录的换行/制表符连接。每个顶层块保存原结构、位置与精确 JSON 路径；文本是有依据的结构连接，不声称复原 Google 网页排版。

引用使用来源明确的 index，而不是数组序号；只按实际 reference_indexes 关联包含它的顶层块，保留嵌套映射路径，不推断字符区间。未关联引用明确 not_covered。snippet_links 与辅助模块链接为独立普通链接，不提升为 citation，不按 URL 合并角色。辅助 shopping/local/video 等模块仅保留普通链接和对应源记录，不宣称已提供这些模块的独立完整标准模型。

未知内容类型/字段、缺失引用集合、显式 partial/truncated/has_more、未展开的回答分页均保守标记 partial；未知结构仍保存，不把未经识别的内容宣称为全文。结构深度、节点、列表和传输体积有界。无法建立文本或引用映射、通道混杂、缓存/范围冲突、非法 URL/时间/索引时失败，不生成成功空回答。

只有来源明确返回空 text_blocks 且无冲突证据时记 not_observed；缺字段或错误不是“无回答”。自然语言拒绝措辞不能当作机器确认的 refused 状态。完整状态仅表示通过当前同步源合同与本地结构检查，不表示独立核验 Google 页面、模型、来源许可或长期完整率。

## 持久化、费用与交付

复用已有 HTTPS/TLS、拒绝跳转、16MiB/64KiB 流式界限和 libcurl 总期限。共享适配器增加参数、范围及解析钩子，Trends 原协议不变。脱敏先收集敏感值，再处理字段、编码变体及重复文本，扫描和敏感值容量均有上限。

响应在计费/事实解析之前经独立响应仓保存；错误响应若明确回显其他范围，即使带 error 也不得保留完整原文。无许可/空间不得先调用。已确认付费但解析失败仍保存实际账单和合规响应；费用未知保留预留并暂停同账号任务，不自动重试收费调用。

沿用 OpenAPI 1.4.0、现有观测/子记录/响应接口、五格式固定快照导出及独立校验器、新域订阅。没有新增公共来源入口或更改客户鉴权。客户 HTTP 集成覆盖完整回答、逐块/引用/链接分页、部分成功、付费解析失败、未知费用隔离、跨租户404、撤权409，以及暂停订阅→补领历史→固定ZIP→ack摘要不变。

## 网页与验证

沿用现有组件，完整回答改为可键盘聚焦的滚动阅读区，高度最多420px，避免长回答挤走引用和下载。全文不截断；220046字符合成回答的浏览器文本 SHA256 与后端一致，键盘 PageDown 实际滚动。桌面原页面约261976px高，修复后回答阅读阶段约3127px；展开引用会增加页面高度。

最终来源全量隔离回归 **1237 passed / 535 subtests passed / 0 skipped，142.91秒**；专项 **60 passed / 105 subtests passed / 0 skipped，15.06秒**，两者重叠，不相加。日志和 JUnit 在 `state/data-capabilities-20261110/ai-mode-regression-final/`。随后网页样式调整的页面组合/JS/曲线专项3项通过（0.13秒）；最终网页验证与清理见本阶段 manifest，不将源码回归时间错报成样式后的全量重跑。

ego lite 空间80通过实际组合页面、候选执行器和客户 HTTP 服务读取已保存回答；五种浏览器下载逐字节匹配 ZIP 各成员，ZIP通过独立身份/摘要校验。引用原生 index 9/2/50，最后一个未关联，普通链接未误作引用。浏览器数据全为合成，每次独立fixture启动仅一次本地来源回调，不是实际 Google 搜索。390px/320px均无横向溢出、阅读区420px，长状态文字可换行；全文最终摘要一致、页面脚本错误为0。样式修复重启后重新验证读者快照，之前下载的固定快照与其后端原记录另存核验，不混淆观测ID。

空间80已finish一次并核验从清单消失；三次本轮UI fixture和两套回归的独立PostgreSQL/Redis、HTTP/worker已退出，仅删除自己记录的空socket目录。详见 `ai-mode-cleanup.json`，未触及其他任务页面。

## 尚未满足的验收

真实来源许可/Key/预算/独立账单、真实地域与完整率、AIO扩展、关键词搜索量实际适配、正式服务挂载、各域 Whale 映射和固定版本读回、500金标/1000真实记录及七天/百万行验收仍未完成。响应物理磁盘保障、自动保留调度及恢复后的墓碑重放沿用上一阶段待办。

生产未部署，生产采集/正文/Whale/监测状态未变，web.py/serp_page.py 未修改；无真实外部调用或 Git 提交。本地回退为移除新增适配器、恢复共享钩子/脱敏扩展和阅读区样式，不涉及生产回滚。

## 后续：AIO展开所需的多阶段基础

第十二阶段已补分阶段持久响应、逐次收费准入与费用凭据、异常恢复封存及OpenAPI1.5.0进度字段，详见 [分阶段调用](data-capability-source-calls-2026-11-10.md)。AI Mode单轮合同不变，AIO的实际初始查询/短效token/展开/标准化尚待下一步，不把合成两阶段基础当成AIO实现或真实验收。


## 第十三阶段兼容扩展

AIO默认禁用候选现已实现，见 [AIO来源与两段交付](data-capability-serpapi-aio-2026-11-10.md)。SourceContext.defer_calls在收到限流后立即持久保存冷却；SourceResult.retry_after支持部分事实交付并保留剩余冷却。AI Mode与AIO共享结构解析核心，仍独立核对通道和原生范围，不把候选当真实来源已接通。

## 第十八阶段分阶段重试跟进

原默认调用与来源禁用保持；现支持操作员显式批准的有界来源重试，任务共用次数/窗口/预算，原生partial/混合数据及未知费用不重发。AIO只继续失败阶段并保留已成功搜索，token期限不延长。早先不支持自动重试的说明为当时阶段记录；具体实现、成本、验证与未完成边界见[data-capability-native-backoff-2026-11-10.md](data-capability-native-backoff-2026-11-10.md)，不代表真实来源或生产已验收。
