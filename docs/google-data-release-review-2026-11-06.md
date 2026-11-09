# 数据闭环修复：生产只读前检与最小发布候选（2026-11-06）

本轮没有发布。读取两台生产机的容器/挂载/源码hash与一份数据库只读快照，下载代码副本在本地叠加目标补丁并测试；再次读取证明生产未变。没有运行生产初始化、旧正文/hash迁移、新搜索或平台写入。

## 当前生产事实

检查时间见 `state/google-data-closure-20261106/release-review/db-{before,after}.json`。两次业务状态一致：

| 检查项 | 实际结果 |
| --- | --- |
| continuous / content_fetch / uploads / claims | false / false / false / false |
| 活动手动SERP任务 / 有效分页lease / 启用监测 | 0 / 0 / 0 |
| 请求 | Web31,679，News8,127，Trending Now29，Images5；总39,840 |
| pages | 332,768；未重新抓正文 |
| 旧outbox | 9 queued，未改写为accepted；关联jobs已succeeded/stopped，没有active/paused |
| 正式registry | sf_google_registry存在但0条，不构成来源/用途/ReaderAdmission批准 |
| 新spool/账本/迟到表 | sf_google_ingest_events/runs/closed_hours/late_transitions均不存在 |
| 正式域导出表 | sf_google_exports/export_chunks不存在；模块有现成异步代码，不等于已启用 |

两台服务挂载已独立验证：Web与prd使用 `/home/azureuser/projects/google-workbench/code/realtime`；worker使用既有 `state/field-expansion-20261016/code-r2/local-worker/realtime`。Web两容器、采集机五容器均running。审计前后container ID、启动时间、重启次数及目标/保护文件hash均未变。

这证明前检期间无新增业务事实，不代表代理、Google或Whale当前成功率/SLA；本轮没有调用这些上游。

## 实际候选包

`state/google-data-closure-20261106/release-review/candidate-bundle.tar`：4个文件副本、3个不同模块，附逐文件before/after hash及固定服务清单。SHA256为 `882cf3d7a93dc02db9859669f60cc3ff2118f22372c9d9daa455c34a637f76f7`。

| 目标 | 文件 | 仅包含的行为变化 |
| --- | --- | --- |
| Web | whale_serp.py | 独立run事件、同键异payload冲突、queued等待writer终态、部分400对账、共享冷却 |
| Web | google_data_export.py | occurrence跨JSONL/嵌套CSV/明细CSV逐字段/顺序核对，不只计行数 |
| Web | google_exports.py | 已有导出service返回前再核对当前时间、权限与证据期限；不启用域导出 |
| worker | whale_serp.py | 与Web相同的可靠性修复；不改搜索worker、代理或Google Images分支 |

AST核对证明旧Whale SCHEMA、task_spec、initialize、stage_subscription、collect、stage_platform完全不变。worker与Web的旧whale模块before hash也完全相同。没有将整个工作树覆盖到线上。

**明确排除**：web.py、serp_page.py、google_data.py、google_images.py、google_ingest.py、google_ingest_ledger.py、google_feed_delivery.py、任何新source/endpoint/UI/商业化功能、sf初始化与自动开关。Web/worker保护文件hash见manifest。新spool、类型字段、迟到账本和Feed合同继续为本地待准入实现，不混入这一小包。

## 用生产冻结依赖验证

从实际挂载目录读取Python源码/本地assets，不复制.env、Key、Cookie、数据库或private目录。测试进程先加载候选目录的realtime包，再检查每个已导入realtime模块路径/hash；不允许借用工作树新增模块。

- Web候选：38项通过、0失败、0跳过；45个实际导入模块全部来自Web冻结包。
- worker候选：28项通过、0失败、0跳过；15个实际导入模块全部来自worker冻结包。
- 两组测试有重叠，不相加冒充66个独立验收项；均为本地隔离数据库/本机HTTP或service测试，不是生产故障注入。

初次复用工作树DomainHTTP测试fixture时，5个setup引用了生产web.py没有的PATCH钩子而失败。没有修改生产web.py，也没有把未上线钩子补到候选包；改用只验证导出service边界的专用隔离fixture。生产GET/POST网站包装及PATCH功能不因此被宣称完成。初次失败日志/JSON保留为 `web-frozen-tests-initial.*`。

上一阶段工作树完整回归887项的证据仍保留；本轮主要新增冻结依赖验证，不用旧full结果代替实际候选包绑定检查。

## 允许发布前必须再次满足

1. 重新核对所有目标before hash、保护文件、容器挂载，以及全部开关false、活动手动任务/lease/Whale job/监测为0；任一变化中止，不覆盖操作者设置。
2. 每台机器新建本次独立备份目录，保存目标文件和manifest；不复用旧带mutation marker脚本，不恢复旧Compose。原子替换仅包内目标。
3. Web仅重启现有web/prd，采集机仅重启local-worker；保留其他四服务。不得顺带初始化sf表、注册来源、创建Key或启用队列。
4. 发布后核对目标after hash、保护文件及其他服务ID；再次只读比较请求/pages/outbox/开关和活动任务。保持9条旧queued及单样本proof原样，不reopen旧job、不重新stage历史。
5. 回退仅恢复本次目标文件的精确备份，并只重启相同服务；若当前hash已被其他操作修改则停止，不盲覆盖。没有数据库回滚、删历史或恢复旧冻结栈步骤。

当前包只让已有工具/旧链接路径更可靠，不接通完整run自动上传；仍不能称新版G1准入完成。

## 仍需决定或外部验证的事项

| 依赖 | 缺少的具体条件 | 本轮可作与不可作 |
| --- | --- | --- |
| D-01～04 / GO-SRC-001/002 | 首个发布范围及逐源字段用途/保存/导出/转供范围；建议不代签 | 旧数据只读/现有技术验证；不宣布新版来源许可或全球覆盖 |
| D-06 / WHL-GO-02/03/05/06 | 正式run/item/feature承载、质量profile、Dataset、身份、期限、ReaderAdmission | 已有Web7条真实样本不等于正式schema通过；News/Trends候选不上传 |
| 旧摘要异常 | 平台旧固定版本入站载荷、事件审计及raw hash算法 | 保留异常及旧queued，不凭当前metadata覆盖固定版本事实 |
| 新运行与交付 | 明确新增搜索/写入的范围和数量 | 就本轮Web完整快照而言，已有明确授权限于历史单样本及其同条修正；不能自动扩大为新请求、批量或七天运行 |
| 七天/真实百万行 | 获准源、计划/阈值、平台/预算、实际运行窗口与逐事件账 | 本地合成百万行和恢复证据保留，不能当真实数据/平台交付 |

## 下一次受限真实验收提案（未授权、未执行）

本包上线并再次确认全部自动开关关闭后，可单独授权：现有OpenSERP链路的一次公开Google Web手动技术验收，关键词 `solar energy research 2026`，US/en、1页；只保留一次新的搜索请求，缓存情况与上游代理尝试按真实返回记录，不冒充强制非缓存或精确位置。执行前须有单次边界/停止保护；不使用持续任务绕过次数限制。

仅在该请求成功且完整保存后，将这**一条**已存公开请求经现有 `saved_web_run_message` 与真实结果快照文本投影，validate后正式bulk一次；使用 `serp-run:<request UUID>` 独立对象和稳定事件后缀，固定版本Consumer读回原响应/摘要/全部结果并对账。不会为了评分扩写正文；无结果/失败/拒收不追加新搜索或第二次写入，未知终态只读核查，不自动重发。

这不新增供应商、Key或外部权限，不启动News/Trends、正文、自动上传/领取、监测、七天或批量运行，也不等于正式ReaderAdmission。原授权附件明确把新搜索及上传留给当前明确授权，所以此提案需用户明确同意后才可执行；没有回答不视作同意。

前检V2另核对共享discovery查询lease、Whale上传lease和queued/running客户API任务，均为0，成对快照不变（`db-v2-before.json` / `db-v2-after.json`）。这些门槛已写入最终候选manifest；未修改任何生产状态。

冻结测试另启用Python socket只允许loopback的守卫，数据库/Redis固定到本轮自建loopback端口；Web与worker两组均0次外部连接尝试。此守卫及模块绑定结果写入最终测试JSON和候选manifest。

本轮收尾：隔离测试遗留的 5 个 schema 已删除，任务测试 schema 剩余 0；本机专用 PostgreSQL 55436、Redis 56436 均已停止。汇总证据：`state/google-data-closure-20261106/release-review/verification-summary.json`。未部署、未创建搜索或上传。

## 发布文件保护工具（后续增补，仍未部署）

新增 `scripts/google_data_release_guard.py`，固定候选包SHA与四文件允许清单；CLI只允许两台既定目录，check只读，install先完成全部备份再逐文件原子替换，rollback核对全部备份及当前旧/新hash后恢复。支持中断后旧/新混合状态；第三方修改、保护文件变化、损坏备份、symlink、重复执行或超过90秒的数据库前检均停止。工具不重启服务、不改变数据库、不开启采集或平台任务。前检不是跨操作者事务锁，正式操作前仍需串行维护窗口、挂载核对及发布后数据/服务检查。

8项本地测试通过；其中故障注入验证第二文件替换失败后可精确回退，损坏备份或第三方修改时在恢复任何文件前拒绝。初次fixture遇到macOS临时目录/var别名而失败，改为resolve本任务临时目录后通过，未放宽生产symlink检查。

随后在两台实际生产机通过SSH stdin执行同一工具的check，使用新鲜只读数据库快照，Web3文件与worker1文件、全部保护文件均通过。没有上传工具、安装文件、创建远端备份或重启容器。证据：`release-review/release-guard-verification.json`、`guard-db-preflight.json`及两份`*-guard-readonly-check.json`。本项仅补齐受控发布准备，不提升正式准入、真实交付或七天验收状态。
