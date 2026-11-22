# Maps 对外 API 与 API 工具验收（2026-11-12）

## 范围与当前结论

本轮完成网站 API 工具中的 Maps 查询与外部 API 交付优化；**尚未完成默认中台入库，不应宣布全链路或商用 SLA 已达成**。

Marsyoo 已经用户登录核实：当前 `SourcePlatform` 没有 `google_maps`，`CreateLiveQueryRequest` 无经纬度/半径/地点查询参数。证据：`output/maps-commercial-20261112/middle-openapi.yaml`。本机 Projects 和当前 GitHub 账户未找到中台仓库，既有采集机可访问，中台主机现有 SSH 凭据未获授权；需要用户提供中台代码/发布入口。没有把 Maps 冒充 google_search，也未扩大旧 Collector 权限。

## 已发布行为

- API 工具增加 Google Maps 请求方式，与独立 Maps 页面复用同一表单、任务状态和结果。支持关键词/附近商家、城市或自定义坐标、半径、英语/中文、CSV/JSONL；刷新恢复 Maps 模式与原任务。
- 根据当前条件生成对外 curl 创建、轮询、结果、导出示例；不向浏览器输出服务端凭据。标准库 Python 客户端见 `examples/maps_api.py`，真实调用验证已通过。
- 外部仍使用网站访问凭据及客户 Bearer；页面操作不要求用户填密钥。OpenAPI 补齐 Maps 请求条件、响应字段、路径参数、格式、错误及限制；数据目录隐藏未实现的独立地点详情路由，兼容旧路由仍返回409。
- 幂等重试返回任务真实状态，而非始终 queued；返回 status/results/export URL 与轮询间隔。拒绝重复 JSON 属性、未知参数和错误类型；附近搜索允许城市预设。
- 修复 gosom v1.18.2 成功零结果的 `null\n` 输出被误判失败；只在成功任务日志、正常退出和可解析输出同时满足时接受，超时/解析失败/缺输出仍失败。
- CSV保留原列顺序，追加 place_id、categories_json、business_status、google_maps_uri、完整 record_json；普通单元格保留公式转义。JSONL保留完整任务信封。
- 单运行器增加全局最多4个在途 Maps 任务的原子准入锁；超出返回 maps_queue_full/429，不预留额度。每空间最多2个及共享搜索并发限制保持。此限制用于避免无界排队，不能代替健康监测或 SLA。

## 验证

证据均在 `output/maps-commercial-20261112/`，不包含客户密钥。

- 正式浏览器 API 工具：关键词查询20家、实际下载CSV/JSONL、刷新恢复20家与原job、仅API项选中、390px页面无横向溢出。`api-website-result.json`、`api-website-download.*`、`api-production-*.png`。
- 独立 Python 客户端：东京附近药店12家、海洋中心1米半径0家，均成功；同幂等键复用同任务且正确返回 succeeded。两种导出逐字段与结果深比较相等，含零结果元数据。`external-jobs.json`、`external-*.csv/jsonl/json`、`download-verification.json`。
- 缺网站访问凭据401、无效Bearer401、有效双凭据200；外部客户读取网站私有job返回404。10种无效参数请求均400，包括错误类型、控制字符、非法坐标、未知字段等。见 `external-auth-checks.json`、`external-parameter-matrix.json`。
- 初始真实查询：新加坡coffee20家、伦敦附近牙医17家、台北中文咖啡20家。初始海洋查询被错误判失败，修复后独立新查询成功返回0家。该失败保留，不计为成功。
- 两次新的相同新加坡coffee查询均成功20家，与首次20个 data_id 完全一致。不能由这几次样本推断长期稳定性或完整城市覆盖。
- `stability-summary.json` 的首批8次成功查询共129条商家出现记录（不代表129家唯一商家），完成延迟1.495–5.630秒（含队列）。电话74/129、网站97/129、评分123/129、营业时间124/129；独立place_id和business_status均未提供。缺失值保留null，未逐家人工核验真实性。
- 本地65项/57子测试通过，正式冻结后端46项/57子测试通过、0跳过。独立PostgreSQL测试覆盖幂等、租户、共享额度/并发、全局背压、失败/重启退款、过期、严格请求、稀疏/零结果、导出与既有Web中台链路。最终测试记录见 `tests-local.txt` 与 `tests-frozen.txt`。

- 城市切换专项验收发现并修复隐藏旧坐标覆盖新城市：恢复预设城市时清空坐标，切换城市清除自定义位置；自定义坐标恢复时自动展开。失败现场保留 `api-website-nearby-beforefix.json` / `api-nearby-debug.json`，不作为伦敦成功证据。修复后正式API页邻近牙医查询使用51.5074,-0.1278，返回17家；刷新后仍为伦敦、无隐藏坐标。320/390px及跨导航复用通过，见 `api-website-nearby.json` / `final-ui-checks.json`。
- 最终运行检查：SERP请求40311、pages332768，旧采集/正文/领取/上传仍关闭；Maps无在途、相关空间reserved=0、失败任务charged=0。见 `final-runtime.json`。

## 发布与回滚

应用机 `/home/azureuser/projects/google-workbench/state/maps-commercial-20261112/backup/index.html` 保存原静态页；新静态页SHA256为 `49fc4a84c571ec789ad94afcce17293ae3f43a70dd89d019726747381237add2`。沿用线上最新HTML最小修改，没有覆盖web.py/serp_page.py。

采集机 `/home/azureuser/projects/google-workbench-backend/state/maps-commercial-20261112/backup/` 保存初始后端，`queue-backup/`保存背压发布前版本。仅更新独立Maps模块、Maps OpenAPI、product能力发现和product_http最小补丁；后者同时补齐线上旧JSON解析器的strict参数。重启Web/PRD/Maps，原采集worker和Google Web中台运行器不重启。

首次外部验收曾暴露PRD仍加载旧解析器的问题，已修复并重启对应服务，后续真实独立客户端创建/读回/导出通过。保留失败证据，不用本地测试替代正式验收。

回滚先停止接收新Maps任务并排空，在reserved归零后恢复两机backup文件、移除新增maps_schema导入及恢复静态页，再重启Web/PRD/Maps。不得恢复旧自动采集、正文或Whale任务领取/上传，不删除Maps历史表。

## 完成全链路尚需的中台能力

需要中台维护者确认并实现以下契约后继续联调：

1. 独立 `google_maps` 来源及商家观测数据集，明确存储期限、租户/Consumer授权；不借用google_search。Collector注册/权限/界面来源列表同步支持。
2. Live query保留并传递 mode、query/included_type、中心坐标、半径、语言及请求元数据；不将位置拼进不可校验的关键词字符串。
3. 地点观测固定版本载荷保留完整请求条件、原始source_fields、归一化商家、观察时间、provider版本、响应摘要和稳定事件键。商家身份data_id与Place ID区分。
4. 采集任务入库取得accepted/duplicate与document/version ID，完成任务后Consumer按固定版本读回，校验身份、请求条件、原始字段及hash一致。上传queued不算完成。
5. 网站默认走该链路，保存/重启/重试可恢复，失败释放预留；其他租户不可读。对外API交付的是核验后的中台快照，API工具中再次真实跑通查询及CSV/JSONL。
