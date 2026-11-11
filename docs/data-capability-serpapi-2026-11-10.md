# SerpApi 历史曲线适配器：本地候选（2026-11-10）

本阶段新增 `realtime/data_capability_serpapi.py`，负责人 Codex。实现明确来源的请求映射、传输、响应规范化与既有执行器/保存/导出连接；不是来源准入、采购、真实调用或发布完成。没有读取或配置真实 SerpApi Key，没有使用免费或付费搜索额度。

## 来源依据与范围

本轮通过 ego-browser 读取官方资料（2026-11-10）：

- [SerpApi Trends API](https://serpapi.com/google-trends-api)：`google_trends`、`TIMESERIES`、逗号分隔查询、地区/语言/日期/category/property/tz、同步及 no_cache 参数。
- [Interest Over Time](https://serpapi.com/google-trends-interest-over-time)：`timeline_data`、`query_index`、`timestamp`、显示值/提取值及 partial 结构。文档示例不是本项目真实采集记录；其中日历/时间戳不一致时，适配器仍拒绝，不能为了匹配样例篡改真实时间。
- [Google Trends 数据说明](https://support.google.com/trends/answer/4365533?hl=en)：相对归一化指数与绝对搜索量不同；低量也可显示0。故源指数0使用 available/value=0/censored，保留具体原因，不冒充 true_zero；明确显示 `<1` 的值保存为 below_threshold/null。
- [curl_cffi API](https://curl-cffi.readthedocs.io/en/latest/api.html)、[libcurl 总传输超时](https://curl.se/libcurl/c/CURLOPT_TIMEOUT_MS.html)：使用既有 curl_cffi 底层接口及总传输期限，不使用只限制空闲读的超时替代总时限。

本地参考快照在 `state/data-capabilities-20261110/provider-contracts/`。官方存在接口不等于用途许可、再交付权、原响应保留权或费用已获准；这些仍需冻结真实账号合同。

## 参数与事实合同

仅 `trends_history`；不将 Trending Now、AIO、AI Mode 或关键词搜索量转入此适配器。当前解析范围为英文显示的显式日/周日历区间、2004年起、最多5个不重复且不含逗号的100字符 term/topic。`language=en` 是明确限制，不静默改客户语言。超出范围在来源参数准入时拒绝；小时/月度显示格式及其他语言尚未验证，不宣称支持所有历史窗口。

原参数严格映射到 geo/hl/date/cat/gprop；shopping 映射 froogle。明确 tz=0、data_type=TIMESERIES、同步、no_cache=true。后者是请求策略，不冒充已证实的源缓存命中状态。实际费用不由这个开关或预览上界推算。

收到结果后复查实际参数回显、原生请求身份、请求时间、每项 query_index 与原查询。日历结束来自源标签，起点与 UTC epoch 双重核验；不把下一点、用户请求截止日或平均粒度拿来猜最后一周期。请求以外的周期、乱序/重叠、含糊索引及值冲突拒绝规范化，不剪裁源值伪造有效周期。

每次响应以原生请求ID及参数形成独立 scale/comparison group；同窗口再次获取不自动跨响应拼接。源 created_at 标记提供方请求创建时间，保留其依据；它不证明 Google 数值的独立观测时间，因此 source_observed_at 保持未知。API 本身未提供发布版本，api_version 明确记为 unversioned，parser_version 是本地版本。

保留实际指数、partial、原字段、原顺序、精确源引用与输入映射。单个周期遗漏某查询保留其他查询结果并生成明确的 not_observed gap；整个查询未出现不生成虚构 series。完整空 timeline 是 no-data，不补0。区域/related扩展明确 not_covered，不偷偷追加收费请求。原响应仅在显式许可 retain_raw=true 时保留；否则 not_retained，并保留正常交付所必需的标准事实及来源字段。

API、子记录、五格式导出及新域订阅沿用既有独立观测合同，无新来源枚举覆盖旧搜索路由。正常新源事实可直接通过既有观测接口读取；现有目录仍 not_configured/source_verified=false，不因这个文件存在而宣告已上线。

## 传输与凭据

固定 `https://serpapi.com/search.json`；校验 TLS、拒绝跳转、限制 HTTPS，不继承环境代理或读取其他浏览器凭据。该传输只供明确批准的后端来源调用，不替代 ego 网页操作。若真实部署要求其他网络出口，应另行冻结配置，不能直接套用现有私有 Google 搜索代理。

libcurl 总期限取任务 context.deadline 的剩余时间，连接最多5秒；流式响应上限16MiB、累计头部64KiB，只接收 JSON，拒绝重复JSON键及非有限值。不能把超限、断响应、HTML错误页或网络异常作为免费失败；未知结果进入既有待对账路径。测试以本机回环滴流验证总期限，不涉及外网。

不在 repr/事件/异常正文中输出 Key。递归删除敏感字段/短效token，删除携凭据URL，对已知Key及编码形式作值脱敏；保留 redacted_paths，不声称字节级无损。原响应摘要仍基于许可内解析后的结构，不冒充源原字节摘要。

## 实际费用与恢复

默认 enabled=false。启用必须显式提供凭据、账单核对器、计费单位与原文保留政策；构造和导入无读取文件、线程、初始化或出站动作。不能把本地 synthetic 账单函数复制到生产。

`bill_for(http_status, sanitized_response)` 必须返回 `SourceBill(actual_cost, currency, billing_unit, evidence_ref)`；需要真实账号/合同或独立账单依据，不能把套餐宣传价、预览 maximum_cost 或 HTTP200 自动当作已确认费用。币种、单位、金额和安全的账单引用均校验；未知/冲突保持预留并待对账，禁止用0结算。适配器不会自动采购、查询账号费用或默认免费。

执行器新增可选 billing_evidence，先持久保存已确认费用和证明引用，再保存事实/结算；失败/取消也保留实际账单。仅对本轮独立 sf_capability_dispatches 增加 JSON 字段，显式 initialize 才执行，不运行旧库迁移。旧内部适配器接口默认仍兼容，但真实供应商适配器应提供证明。

Retry-After 支持秒与带时区HTTP日期；超过本地可表达上限或格式无法确认时暂停待对账，不截短后擅自调用。该值持久延迟共享账号后续任务，本任务不自动重试。网络未知结果不会套用预览额度宣称实际扣费。明确付费错误按真实费用失败结算；事实格式/范围无法确认时返回 source_contract_unverified，不将模糊值保存成成功曲线。

当前限制：合同无法解析的失败响应尚无独立、获准的持久原文 spool，只有费用/错误账本；正常许可内响应才能通过严格事实存储。该缺口和真实传输/账单校验需在真实来源验收前继续，不宣称已满足所有失败原文保留、批量重试或规模要求。

## 验证与运行状态

完整隔离回归 **1209 passed / 480 subtests passed / 0 skipped**，129.73秒，`state/data-capabilities-20261110/serpapi-regression-final/pytest.xml`。随后仅调整未保留原文的原因文本并补一项真实客户HTTP链路测试；最终相关专项 **33 passed / 50 subtests passed / 0 skipped**，4.70秒，`serpapi-http-final/focused.xml`，与全量有重叠，不相加冒充一次完整回归。

```sh
.venv/bin/python -m pytest -q --junitxml=<证据目录>/pytest.xml
.venv/bin/python -m pytest -q tests/test_data_capability_executor.py tests/test_data_capability_serpapi.py --junitxml=<证据目录>/focused.xml
```

独立临时回环 PostgreSQL/Redis 启动脚本和命令日志保留在上述目录。覆盖默认禁用/无出站、参数/日期/索引/尺度、0与低量、partial与缺失查询、脱敏/不保留原文、已知/未知/币种错误费用、HTTP错误/无效JSON、秒/日期Retry-After、传输体积及真实本机滴流总期限、账单持久化后保存失败，以及主客户API读取/点分页/固定快照ZIP/跨租户404。三个本轮PostgreSQL/Redis实例及所有HTTP/worker测试线程已核验退出，只清理自己的空socket目录。摘要见 `serpapi-manifest.json`，清理证据 `serpapi-cleanup.json`。

所有 provider-shaped 响应与账单均为 SYNTHETIC fixture，不计入100真实series或真实记录数。集成测试经过来源形状 → 候选执行器 → 独立事实/子记录/费用 → 完整导出及外部身份/hash校验，未调用真实 SerpApi。

浏览器任务空间76已按指南 finish；SDK将其交还为 user-owned，保留受保护页面，没有再次操作/关闭，也未触及其他任务。生产、web.py/serp_page.py、全站采集/正文/Whale/监测开关均未变。

真实来源许可/凭据/账单和预算、正式启动挂载、100series及其他三能力真实适配、Whale各域固定版本读回、真实规模/七天验收仍待完成。本地候选可回退新增模块及执行器的兼容扩展，生产没有需要回滚的改动。

## 后续：独立响应保存候选

本地第九阶段已补充独立有界响应仓：完整且范围可核验的已脱敏JSON在解析/计费核对前保存，失败与未知账单均可独立查阅、导出，不生成成功事实。启用原文保留须同时配置明确许可和响应仓；未满足时拒绝新任务。原始HTTP字节、不可解析/不完整/范围不符内容不作为完整原文保存。详见 [响应保存与交付](data-capability-responses-2026-11-10.md)；前文“失败原文spool待补”为第八阶段历史状态。

## 后续：独立 AI Mode 候选

第十一阶段新增 [AI Mode 完整回答候选](data-capability-serpapi-ai-2026-11-10.md)，复用传输/响应/费用控制，按能力注入参数、范围核验和解析钩子；Trends 参数合同不变。共享脱敏进一步移除短效 token URL 及其他字段中重复出现的敏感值，错误响应显式范围冲突时只保存未保留原因。最终来源隔离回归1237 passed / 535 subtests / 0 skipped；合成验证，不代表两个来源均已真实接通。

## 第十八阶段分阶段重试跟进

原默认调用与来源禁用保持；现支持操作员显式批准的有界来源重试，任务共用次数/窗口/预算，原生partial/混合数据及未知费用不重发。AIO只继续失败阶段并保留已成功搜索，token期限不延长。早先不支持自动重试的说明为当时阶段记录；具体实现、成本、验证与未完成边界见[data-capability-native-backoff-2026-11-10.md](data-capability-native-backoff-2026-11-10.md)，不代表真实来源或生产已验收。
