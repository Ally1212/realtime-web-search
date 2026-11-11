# 新数据域事实、版本与导出：本地第三阶段

对应 DATA-01 公共事实层及 DATA-02/03/04/05 的源中立标准模型；负责人 Codex。本轮是本地开发与隔离验证，没有真实来源接入、上线或 Whale 准入。主客户 OpenAPI、路由、订阅和网页仍未扩展至新域，四项能力不能标记完整完成。

## 代码与独立数据模型

- `realtime/data_capability_facts.py`：`searchflow-facts-1`，严格源结果校验。参数必须与已批准任务完全一致，不允许布尔/数字类型隐式替换；原词和 prompt 保留。源 API/解析器版本、原生请求 ID、请求/接收/源观测时间及未知原因分开。
- `realtime/data_capability_store.py`：`CapabilityData` 继承公共任务队列；独立 `sf_capability_observations`、`sf_capability_job_observations`、`sf_capability_fact_rows`。不读写旧 SERP、正文、URL registry 或分析表。不可变结果信封为事实依据；series/point、volume/month、answer/block/citation/link 为可重建子记录投影。
- `realtime/data_capability_export.py`：`searchflow-facts-export-1`，完整固定观测的 JSON、JSONL、CSV、ZIP、manifest 与独立校验器。

schema 只能由显式 `initialize()` 创建，无导入时建表或后台执行。worker/来源未配置，不启动真实调用。

## 三类事实合同

曲线保存独立 series、实际窗口、源时区、粒度、解析对象、scale_id、测量性质、归一化方式、比较组、修订与有序点。点为左闭右开时间区间，保留 true_zero、低量/未知/null、partial、源引用和原字段。相对指数超出 0–100、窗口外/重叠/乱序点拒绝。区域兴趣、related top/rising 是独立扩展结果集；未覆盖须标记原因。尚未接通源，不能将此模型称为已取得真实曲线。

搜索量保留逐输入索引映射，重复输入和 close variant 合并都不丢失；返回组保留 returned_text、测量性质、均值及源统计窗口、逐月状态/量值、原字段。月份缺失与真实0分开；数字必须为有限非负十进制字符串。月度范围越界或重复拒绝。搜索量单位不接受相对指数，商业竞争与 SEO 难度不混用；CPC/bid 明确 currency 与 currency/micros，不做隐式金额换算或跨币种合计。

AI 记录明确 aio/ai_mode 通道、全文、块顺序及原结构、明确引用、引用与块/全文范围的源映射、普通链接、源给出的 model、展开状态和结束依据。未出现、拒答、过期、展开失败、部分回答分别表达。引用偏移使用 Unicode 码点、左闭右开；未知映射不得猜测。展开失败、截断不能宣称完整。拒答可以保留源实际返回的拒答文本，不生成替代回答。

原响应保留状态区分 stored、redacted、not_retained，脱敏路径/理由明确。拒绝凭据或短效 token 字段、不合法 JSON、NUL、非有限数与超过64层嵌套；整个标准信封及许可内原响应上限64 MiB。SHA256 对规范化 UTF-8 JSON 计算，不声称源 HTTP 字节逐字节相同。是否可保存哪些原字段仍须真实来源许可冻结；当前测试不构成许可证据。

## 身份、缓存与原子性

- event_key 在租户内唯一；同事件重复提交必须保持任务、payload、revision 和 supersedes 一致。并发冲突结构化拒绝，不静默覆盖。
- 新任务取得同值结果仍生成独立 observation_id；即使 payload_hash 相同也不去掉观测。entity_id 按租户、来源/许可、条件定位，曲线再纳入源对象、实际窗口、粒度/时区、尺度及比较组。跨条件或跨尺度不得作为同一实体修订；旧观测完整保留。
- 本地缓存复用只新增任务关联，不新增观测或子记录；复用前重查租户、当前准入、条件和保留期限。保留原始时间与 cache_age 的依据，源时钟超前时年龄为 null，而非假0。`local_cache_reuse` 仅说明本地关联，未复用不代表上游未缓存：此时 cache_hit=null、basis=source_cache_status_unknown。上游缓存的完整声明及无法关联原本地样本的语义仍待具体适配器冻结。
- `complete()` 在单个事务内保存信封、全部子记录、任务关联、终态及费用。中途失败全部回滚。公共队列不再允许凭任意 result_ref 成功结算；必须有本任务实际关联的有效事实，且 succeeded/partial 与信封一致。
- 在途取消可以结算实际费用，但不保存或交付其响应内容。过期 lease 不得提交新事实；外部计费不明仍按第二阶段对账合同处理，不自动重发。

读取详情、子记录、任务数据、缓存复用、每次导出都校验当前来源准入和保留期限，其他租户读不到数据。许可/用途变化或过期即拒绝。权利检查依赖可信调用方传入当前 grant，不可把客户 JSON 当作 grant；尚无 HTTP 端口开放这项权限。

## 完整交付与损坏处理

JSON 保留整个固定观测。JSONL/CSV 含观测头与全部子记录，保留每条父子 ID、位置和源字段，可以精确重建 JSON；输入映射保留在观测头。CSV 身份列做公式转义，record_json 是未改写的机器读取依据。超长文本、表格/列表结构及明确引用不截短，不套旧7/14列合同。

ZIP 包含 observation.json、records.jsonl、records.csv、manifest.json；重复导出同版本得到相同字节。manifest 包含逐文件字节数和 SHA256、数量、范围/参数、schema、raw 状态、partial/truncated、存储和到期时间。payload_hash 校验结果信封，snapshot_hash 同时保护观测元数据；校验器要求从独立已授权接口取得 observation_id、payload_hash、snapshot_hash，不能只信压缩包自己的 manifest。ZIP 摘要在下载证明中另行返回。

每进程最多2个同时构建的导出，总未压缩文件与压缩包各限制256 MiB；失败只返回错误，没有发布半包。支持超过 CSV 默认128 KiB 的长记录。尚无持久下载缓存、spool 或批量多观测快照；已下载到外部的文件无法主动撤回，调用方负责按许可清除。

读取时核验事实/原响应摘要及所读投影页是否与不可变信封一致。投影损坏返回 corrupt_projection，可经可信 `rebuild_projections()` 重建；事实自身损坏返回 corrupt_snapshot，禁止用损坏事实重建投影，仍需独立备份恢复。本地修复投影不改原响应、事实版本或历史贡献。

## 隔离验证与边界

测试代码：`tests/test_data_capability_{contract,jobs,facts,store,export}.py`；所有来源、费用、原响应和回答均为明确的合成 fixture，不计入100真实series、100真实关键词或500真实回答验收。

命令（独立 PostgreSQL socket；禁止指向生产）：

```sh
TEST_DATABASE_URL='host=<本任务独立socket目录> port=55439 dbname=postgres user=postgres' \
.venv/bin/python -m pytest \
  tests/test_data_capability_contract.py tests/test_data_capability_jobs.py \
  tests/test_data_capability_facts.py tests/test_data_capability_store.py \
  tests/test_data_capability_export.py tests/test_customer_data_api.py \
  tests/test_customer_subscriptions.py tests/test_product.py -q
```

覆盖并发幂等/事件冲突、同值新观测、修订/尺度隔离、缓存、撤权/到期、部分/空数据、低量与0、引用映射/展开失败/拒答、超长回答、原响应脱敏、公式转义、篡改/损坏、事务回滚及投影恢复；并回归旧客户 API、订阅与产品。

部署范围与回退：没有部署、生产调用或开关变更。后续只在全部候选复核后按生产实际冻结包最小差异发布；保护 web.py/serp_page.py。此阶段没有生产表或数据需要回退。现有主 API 未改变，不宣称新域可通过客户接口创建或下载。

仍待：真实来源/用途/成本及凭据、实际适配器/限流重试与执行器、原始响应与源 oracle 逐字段对照、新客户路由及 OpenAPI/fields/capabilities、可绑定租户/快照的游标、增量订阅、网页、Whale schema/固定版本读回、备份/tombstone 全链路、真实规模/费用/七天与SLO。完整目标保持不变。

最终相关回归：**77 passed、85 subtests passed、0 skipped**，JUnit 合计162检查项、0失败/错误/跳过。证据：`state/data-capabilities-20261110/facts-regression.xml` 及 `facts-manifest.json`（测试报告与五模块SHA256、未完成范围）。另有编译检查和 `git diff --check` 通过。首次测试端口被已有本地进程占用，启动失败后改用本任务独立实例及专属socket；最终验收只使用该隔离实例，未停止原有监听。


## 后续进展（本地第四阶段）

主客户 API、OpenAPI/fields/capabilities、查询及下载已完成本地接入，覆盖本报告此前的“尚无客户路由”状态；详情与完整回归见 [客户 API 报告](data-capability-api-2026-11-10.md)。真实来源、实际执行器、订阅、网页、发布及真实验收仍未完成。


## 搜索量均值窗口扩展（第十四阶段）

statistic_window未知时start_month/end_month必须均为null并提供非空reason；已知窗口不带未知reason，半未知拒绝。原请求窗口和返回月份不能证明平均量的实际统计窗口。该兼容扩展没有重写旧事实或数据库迁移，详见Google Ads候选文档。
