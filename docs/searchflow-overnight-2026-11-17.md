# 分来源持续采集实验：2026-11-17 至 11-18

用户授权：Google Web 持续压力采集与优化；News、Trends、Images、Maps 分开采集。截止为新加坡时间 2026-11-18 09:00（UTC 01:00）。到时停止本轮供给，保留数据，等待已发请求排空；截止后完成的数据不计入截止前产量。

服务器：采集机 20.106.103.139，项目 `/home/azureuser/projects/realtime-web-search-openserp`。
运行目录：`state/overnight-20261117/`，权限700。开始时间 UTC 10:17:22；首次修正和暂停排空时间计入总墙钟时间，不删除失败样本。

## 运行及统计

- Web 复用原冻结 worker 和 google-serp-20261010 供给游标。正文关闭，原库保留；仅供给 Google Web。先18槽，再24、12槽，每段至少75分钟；之后按全部已测时段加权的新历史唯一 URL / 墙钟秒选择配置，成功率至少85%、至少100请求及1小时测量才有资格。每75分钟重新比较并复验较优配置。只实验1页，不能据此宣称全参数空间的最大值。
- News、Trends、Images 使用现有采集容器的独立暂停任务直接调用，不进入原worker队列。结果及原始结构保存在既有 SERP 数据库，以独立 campaign ID 归属本轮。News 跳转解析预算10秒、最多10链接，剩余保留原始包装链接及未解析标记；不抓正文。Trends为13地区 Trending Now RSS，约16分钟一轮，不是关键词历史热度。Images保存图片元数据，不下载媒体。
- Maps 单并发，38城市、20类别及城市网格，使用已有gosom快速模式。原始响应、参数、商家字段在 `results.sqlite` 的tasks表；places表按data_id做本轮去重。这不是全历史新商家计数，不与Web URL相加。初始未自动上传中台；现已按下方最新授权启用独立中台同步。
- `report.json` 每分钟更新；`observations.jsonl` 保留过程；`state.json` 保留配置比较。结束时 `final.json` 保存截止前指标和排空状态。SERP统计由真实请求和 `serp_urls.first_request_id` 联查，排除historical和canonical别名；缓存复用不冒充新请求，跨来源的历史新URL只归属其首次请求。
- 低于5GiB磁盘或1GiB可用内存、正文表变化、连续3次统计失败、网站总开关/设置被操作员修改，实验停止。不会自动覆盖操作员关闭动作。服务故障会关闭本轮供给，error.json留证，不自动反复恢复采集。

## 服务和停止

`searchflow-overnight-20261117.service` 已启用；重启后可继续未停止的本轮任务。`searchflow-overnight-deadline-20261117.timer` 为独立持久化定时器，UTC 11月18日01:00执行关闭供给并写STOP文件。定时器不依赖聊天或监督进程正常工作。

查看：`systemctl status searchflow-overnight-20261117.service`，读取运行目录 `report.json`。
提前停止：`python3 state/overnight-20261117/supervisor.py stop`；不要直接重新启用已结束的实验服务。

代码：`scripts/overnight_source_collect.py` 和 `scripts/overnight_source_supervisor.py`。本轮部署分别命名collect.py、supervisor.py。未部署工作树中其他改动、未运行旧正文/hash迁移、未提交Git、未改其他采集项目。

## 启动验收与修正

已实际执行一次关闭及排空，确认continuous_enabled=false，再通过updated_at比较并交换恢复。定时器已核实下一次执行时间为UTC 2026-11-18 01:00。配置选择的加权、短窗口排除、低成功率排除3项检查通过，Python编译通过。

最初业务容器缺少Images模块且缺代理中转配置，已改用采集容器；旧采集版本不支持create_task幂等关键字和部分Trends地区，已按实际接口修正。Maps首轮网格偏移导致多数空结果，已修正并扩展地理覆盖。初始失败均保留。News原150秒跳转解析预算压低吞吐，后续收窄为10秒，实际AI新闻样本17.93秒返回61条、47个历史新链接。

短样本仅证明正在采集，不是整夜最终产量、SLA或理论产能上限。最终应在截止后读取final.json并独立复算。Maps统计必须标注“本轮唯一商家”。

UTC 10:25:19 独立查询启动证据：Web 166请求/157成功/496历史新URL，News 17/8/284，Trends 7/4/96，Images 5/3/59；Maps本轮唯一762商家。失败含初始配置问题，不抹除。正文332768条、max_id333185未变。24槽候选Compose真实config校验通过。证据output/overnight-20261117/startup-proof.json。

## 最新要求：本轮同步中台（2026-11-17，覆盖最初仅本地保存方案）

用户明确要求本轮压力测试同步到数据中台。新增 `scripts/overnight_middle_sync.py` 与 `scripts/overnight_middle_rpc.py`，独立 `searchflow-overnight-middle-20261117.service` 已启用，回补本轮已存成功/空结果请求及所有成功Maps查询，再持续跟随新增。旧Whale开关不启用，不重新搜索，不借用/伪造live_query任务。采集仍于11月18日09:00新加坡时间停止，同步在采集结束后继续到本轮队列排空并全部读回；生成 `middle-final.json` 后退出。

- Web/News/Trends/Images → social_media_raw；平台分别google_search/google_news/google_trends/google_search。Maps → google_maps_raw / google_maps。source_name为 `searchflow.pressure.<google_web/google_news/google_trends/google_images/google_maps>`，原始载荷run_id为overnight-20261117。
- **一条中台文档代表一次完整搜索/Maps查询快照**，platform_payload保存完整原请求/原响应、全部结果、参数及来源。result_occurrences表示快照中的出现次数，不能称历史唯一URL；Maps也不能用文档数冒充商家数。本地历史去重URL、本轮唯一商家统计继续单列。
- `middle-sync.sqlite`为事务性待同步账本：prepared、submitted、accepted、verified、rejected。事件编号和原始上传载荷先落盘，未知上传结果重试复用同编号；accepted/duplicate且具有document/version才确认入库，完整固定版本读回后才verified。`middle-report.json`每30秒更新分来源/分状态的文档和出现次数，并记录限流/失败；未扫描的积压不包含在prepared数中。
- 早期Maps未保存原始完成时间，明确observed_at=null、observed_at_known=false。该部分snapshot_at为首次准备同步时间，元数据注明时间依据；没有伪造采集时间。后续Maps记录新增真实completed_at。
- 非Web账本允许后到的任务完成日志回补较早的请求，按已准备request_id排除重读，避免游标越过尚未登记的任务。Web只扫描原供给run及本轮时间范围。Maps按原task index读取，失败和有效零结果区别保留。
- 批量固定版本读取使用原Consumer凭据；读取版本绑定原始payload_hash，并验证完整载荷。中台偶尔把1.0序列化为1；只允许整数数值等价变化，保留原摘要和读回摘要，标明numeric_encoding_normalized。字符串、布尔、数组、字段丢失和其他差异仍失败。读取限流退避，不计为验证成功。

五来源各一条真实快照已取得accepted及固定版本读回；另20条Maps批量验证通过，其中1条包含数值表示等价差异。初次上传因PostgreSQL时间使用空格分隔被API拒绝400，已对尚未发送成功的准备记录规范化为RFC3339；原业务数据未改。21项相关测试及20个子测试通过，覆盖未知上传结果复用事件、任务日志晚到不丢请求、时间和数据集语义。初始证明 `middle-canary-proof.json`、`maps-batch-proof.json` 在服务端运行目录；本地证据位于output/overnight-20261117。

启动时已实际证明同步工作，不代表全部积压已完成。最终完成以middle-final.json及分来源verified统计为准。

结束时对Web补做不依赖扫描游标的全量身份对账，避免迟提交请求遗漏。部署后再次确认采集服务、同步服务、独立截止定时器均active，截止仍为UTC 2026-11-18 01:00。21:34快照已verified：Web1061、News726、Trends153、Images342、Maps141篇完整请求/查询快照；仍有未扫描及待处理积压，不是全部完成。

## 2026-11-18 最终结果

本轮02:54因统计命令参数过长提前失败，未完成跑到09:00的要求。已修复参数传递、未重启采集，独立复算及中台完整对账完成。详情见[最终结果与事故](searchflow-overnight-results-2026-11-18.md)。
