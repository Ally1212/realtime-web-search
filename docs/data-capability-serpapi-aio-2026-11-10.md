# AIO 来源候选与两段交付（2026-11-10）

新增 `realtime/data_capability_serpapi_aio.py`，默认禁用、不寻找凭据、不自动挂载服务。只接受独立 `aio_answer`，当前参数候选限定 US/GB、en、desktop/mobile；不是完整市场矩阵或真实来源验收。关键词量适配仍未完成。

## 协议与来源

本轮通过 ego lite 阅读 [Google AI Overview API](https://serpapi.com/google-ai-overview-api) 与 [AI Overview Results](https://serpapi.com/ai-overview)。公开文档抓取在 `state/data-capabilities-20261110/provider-contracts/*stage13.json`；其中示例含省略号和已脱敏短效值，不能作为真实响应、金标或生产账单。浏览器空间88已 finish 并核验移除。

先请求固定 SerpApi HTTPS 主机的 `engine=google`，严格核对原生查询、地区、语言、设备和域名；仅使用 `ai_overview`，不把 organic_results 摘要当回答。若返回 page_token，最多再请求一次 `engine=google_ai_overview`。展开响应须精确绑定本次 token，出现相互矛盾的可选查询回显则失败；不跟随源响应中的 URL，不进入 AI Mode 或第三次续答。

官方说明 token 一分钟失效。候选使用源创建时间加59秒的保守本地窗口，并受任务总期限约束；实际越过本地窗口标 expired，不宣称供应商已确认过期。已知账号等待/Retry-After无法在窗口内进入时标 expansion_failed，不把预测等待说成实际过期。token 只在调用内存中使用，原响应、费用证据及其其他字段内重复值均脱敏后保存。

## 响应、费用和恢复

启用必须显式提供凭据、原响应保留许可、逐次费用上界及独立 `bill_for(phase, http_status, sanitized_response)` 核对器；还要求执行器的 before_call、confirm_bill、defer_calls 和独立响应仓。预先验证两次调用上界不超过任务预算。每次实际进入前重查共享账号许可、lease、取消、预算与调用间隔，最多两次上游调用；本地等待不重放上游。

每次最多7MiB，共享既有16MiB响应日志预留。完整、范围可核验的脱敏响应先保存，再确认独立账单、再解析。范围不符/不可解析只保存不可保留原因，不保存越界原文。未知第二段费用保留首段已确认费用、整个预留及账号待对账隔离，绝不按0结算或自动再收费。总费用严格合计所有进入阶段。

收到 Retry-After 后立即通过 defer_calls 持久保存账号冷却，再核对账单；带部分事实的 SourceResult.retry_after 让已知展开失败仍保留剩余冷却。采用剩余秒数，避免处理后重新延长完整等待。成功事实、原响应日志分别通过既有客户接口交付，日志存在不代表回答解析成功。

## 回答语义

与 AI Mode 共用结构解析核心，但独立验证原生通道、查询范围、API版本、parser、终止原因和JSON路径。不伪造 AI Mode 响应。按源顺序保存 heading/paragraph/list/table/code、expandable/title/subtitle、comparison 和 top_stories；引用按显式 index 关联，普通链接、视频和图片来源独立保留。snippet_links 原生对象/数组均支持。原生结构保留，源 Markdown 仅辅助；模型与独立源观测时间未知。

原生缺失 AIO 与明确空文本仅在严格验证成功响应后标 not_observed，附依据；源 error 标 source_error，不从一句文字猜测拒答。未知结构、未展开子内容或第二次仍给 token 标 partial，不冒充完整回答。产品等尚未标准化的辅助模块保留在 source_fields.native_overview，不能宣称已完成全部商业模块。

展开失败、过本地时限、元数据不符或结构解析失败时，优先保留第一段可验证的回答和明确部分状态；没有第一段文本时保留失败/过期状态，不发明正文或引用。实际费用含已进入的两段，不因回退第一段而漏计第二段。

## 验证与剩余工作

测试使用显式合成供应商响应和本地真实客户HTTP/独立PostgreSQL/Redis：两次原生请求、绑定/脱敏、未知费用、取消/限流、结构保留、长回答及子记录分页、五格式固定摘要与独立ZIP校验、订阅、租户与撤权。最终计数和资源清理以 `state/data-capabilities-20261110/aio-manifest.json` 为准；未调用外部付费来源，未部署或改生产开关。

仍需真实来源许可、Key、预算及独立账单；真实AIO/AI Mode金标、完整参数矩阵、关键词量实际适配、正式服务挂载、各域平台固定版本读回、规模和七天验收。物理磁盘保障、自动TTL调度、恢复墓碑重放及无收到响应的空预留恢复等原有限制仍在。能力目录不能因本地候选而宣告来源已配置。

最终隔离全量1271 passed / 598 subtests / 0 skipped，167.66秒；专项94 passed / 168 subtests，24.76秒，与全量重叠。本轮两套独立PostgreSQL/Redis及测试HTTP/worker已退出，端口关闭、自己的空socket目录已删除；受保护web.py/serp_page.py摘要与前阶段一致。

## 第十八阶段分阶段重试跟进

原默认调用与来源禁用保持；现支持操作员显式批准的有界来源重试，任务共用次数/窗口/预算，原生partial/混合数据及未知费用不重发。AIO只继续失败阶段并保留已成功搜索，token期限不延长。早先不支持自动重试的说明为当时阶段记录；具体实现、成本、验证与未完成边界见[data-capability-native-backoff-2026-11-10.md](data-capability-native-backoff-2026-11-10.md)，不代表真实来源或生产已验收。
