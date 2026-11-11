# 数据能力工作台本地接入（2026-11-10）

本阶段为本地候选，未部署、未调用真实来源或平台；所有验收数据来自明确标记的隔离合成 fixture。真实来源、许可、凭据、预算、实际执行器与启动挂载、平台各域正式映射/读回、真实规模及七天端到端验收仍未完成。

## 已实现

- 沿用现有工作台的导航、组件和客户连接，新增“数据任务”。四能力分别提供严格参数表单、来源/许可/保留政策与费用上限预览、显式预算输入、提交、任务进度及取消。预览显示批准上限，不冒充供应商报价；来源未准入、执行器不可用或预览过期均阻止提交。
- 提交使用幂等键，响应丢失后只允许人工重试；成功后需重新预览。切换客户连接会清空结果并取消读取，旧响应不能填入新空间。读取有等待上限、版本检查及明确失败状态，不自动重放写请求。
- 独立观测列表、精确来源元数据、保留/终止/截断与未知状态、完整 AI 回答及原事实。父子记录按实际 API 分页，保留月份、块、引用、链接与输入映射。基础趋势画布按独立尺度显示，仅连接相邻且有数值的时段；缺失值和时间断档不连线，不插值，不将 unknown 当作 0。
- 单观测 JSON/JSONL/CSV/ZIP/manifest 下载绑定快照；调用示例仅含凭据占位符。独立 domain=data/mode=observations 订阅默认暂停，支持 now/history、领取、进度、历史、五格式下载、丢弃。完整文件成功下载后才开放人工校验勾选；确认须显式操作且绑定当前批次 SHA256，下载 manifest 不会开放确认。

## 验证

完整隔离回归：`state/data-capabilities-20261110/ui-regression-final/pytest.xml`，1176 passed、430 subtests passed、0 skipped（120.24 秒）。原始命令与日志保留在对应目录。此后只调整画布数值计算顺序以避免极大有限值溢出，并增加专项验证：

```sh
.venv/bin/python -m pytest -q tests/test_data_capability_chart.py tests/test_data_capability_ui.py --junitxml=state/data-capabilities-20261110/ui-render-regression.xml
```

最终专项 3 passed、0 skipped；包括实际组合页面的 ID/导航与 JavaScript 语法、未知/时间断档不连线、独立尺度及 1e308 坐标有限性。专项与全量有重叠，不相加冒充一次完整回归。

通过 ego lite 独立空间 72 实测实际工作台与隔离 HTTP/数据库：

- 四能力提交到合成执行线程、持久化、回读和子记录；恶意 HTML/脚本文本只作为文本显示，无脚本错误。
- 来源撤权、执行器未就绪、预览过期均阻止提交；切换空间时迟到响应被丢弃。接收成功后模拟连接丢失，人工重试前后任务 4→5，只有一个新增任务。
- 单观测五格式实际浏览器下载，ZIP 独立校验通过，其余文件与 ZIP 对应文件逐字节一致。订阅批次以外部 batch_id/SHA 校验后人工确认；确认前后再次下载 ZIP 字节相同。
- 320px/390px 无横向溢出，表单控件高度至少 44px；手机抽屉可切到旧 API 工具再返回。真实源曲线显示 true_zero 点并保留未知断档。截图在 `designs/data-capabilities-20261110/`。

结构化浏览器证据在 `state/data-capabilities-20261110/ui-preview/`：`four-domain-browser.json`、`source-gates.json`、`session-race.json`、`lost-response-retry.json`、`mobile-nav.json`、`curve.json`、`observation-verified.json`、`browser-batch-verified.json`。没有将 fixture 当作真实来源样本或规模/SLA 验收。

## 变更与边界

新增 `realtime/data_capability_ui.py`；`product_ui.py` 仅在现有最终页面组合处挂接扩展。保护文件 `web.py`、`serp_page.py` 未修改。HTML 快照是实际组合页面，不内置 fixture 数据或客户密钥。沿用访问门和客户 Bearer，不替代管理权限。

元数据每页 10、子记录每页 20；不以网页大响应表现代替真实百万行交付验收。单观测/订阅导出服务沿用既有大小与并发限制。没有自动创建来源、打开采集、挂载生产 runtime 或设置自动执行器。

本轮独立 HTTP/worker/PostgreSQL、两套完整回归 PostgreSQL/Redis 均已停止；ego 空间 72 已 finish 并从列表消失，未操作其他任务页面。旧自动采集/正文/Whale/监测开关及生产数据未改变。

后续第十阶段在任务详情增加独立来源响应读取、固定摘要五格式下载、权限失败清空与任务/连接切换防护；详见 [来源响应工作台入口](data-capability-response-ui-2026-11-10.md)。此前第六阶段未包含失败响应入口。

## 后续：完整 AI Mode 回答阅读区

第十一阶段沿用现有页面与鉴权，完整回答增加420px滚动上限、可聚焦region与可访问名称，不截断内容。220046字符合成回答浏览器文本摘要与后端一致、键盘可滚动；五格式浏览器下载与独立ZIP校验通过。来源完整性和引用语义见 [AI Mode候选说明](data-capability-serpapi-ai-2026-11-10.md)，不将 provider Markdown摘要当全文。
