# SearchFlow 采集与验收流程

本流程以小样本验证、有限时长真实采集、停止后复核三个阶段检查已开放功能。它不会开启全站持续供给、正文、自动上传或平台领取，也不替代平台 Consumer 读回及长期 SLA 验收。

## 一 小样本准入

1. 执行隔离全量回归。测试目录固定为 `tests/`，不扫描 `state/` 历史冻结包。一次性 PostgreSQL 与 Redis 仅监听回环，结束后关闭本次创建的进程。

   ```sh
   bash scripts/run_acceptance_regression.sh state/本轮证据/reproducible-regression
   ```

2. 依照项目 ego-browser 规则在一个专用后台 Space 检查页面、访问门、权限、任务控制、筛选、分页、详情、导出、分析、监测和密钥生命周期。密钥从本机受限文件读入内存，不打印。
3. Web、News、Trends、Images 各提交一条私有客户 API 小任务，使用固定 Idempotency-Key。逐来源核对成功终态、JSONL 原记录、CSV 行列与字段、幂等重放、公开详情 404，以及成功结算后 reserved=0。并发满时遵守返回的冷却，不将 429 当成成功，也不自动重放未知写请求。
4. `readiness()` 检查全量 JUnit 无失败/跳过及四来源样本、导出和隔离证据。不将外部未配置服务算作通过。必须保留网页/API 与本地模拟测试的区别。

## 二 有限时长采集

```sh
.venv/bin/python scripts/searchflow_acceptance.py \
  --space 本轮Space编号 --minutes 30 \
  --evidence state/本轮证据 --output state/本轮证据/formal

# 在另一个终端启动独立批次看护，使用同一任务 Space 和 journal。
.venv/bin/python scripts/searchflow_acceptance.py \
  --space 本轮Space编号 --watchdog-journal state/本轮证据/formal/journal.json
```

采样窗口使用单页、有成功次数上限的独立 once 任务。Web 计划每分钟两词，News 和 Images 每五分钟各一词，Trends 每十五分钟一次。最多计划 44 个任务、74 个查询；工作线程内部失败尝试另计，不能把查询预算当成实际外部 HTTP 次数。

每个来源独立检查积压，慢来源不阻塞其他来源；仍有活动批次时不追加该来源。批次超过三分钟由看护停止，保留失败记录，后续取下一组关键词。达到至少二十次关联尝试且失败比例超过一半时停止整轮，报告实际时长，不能伪称完成三十分钟。

所有 POST 在发出前落 intent，成功响应立即存档；不自动重试 POST。发生未知写入结果时停止投新任务，使用原 intent、时间与任务列表只读对账。输出目录已有 journal 时拒绝覆盖。读取窗口中所有计数按实际记录计算，缓存单列，不计作新增请求或首次贡献。

## 三 停止与出报告

采样器停止本轮所属任务并确认没有活动任务；其他任务不受控制。全站持续供给、正文、上传和领取开关保持原关闭状态。已发出的请求仍可能完成，必须留出排空窗口，并对比停止后两次任务计数。

```sh
.venv/bin/python scripts/searchflow_acceptance_report.py \
  state/本轮证据/formal --capture-space 本轮Space编号
```

报告器按 campaign 捕获请求，拒绝截断结果；按 request ID 去重，记录原始响应与摘要清单。统计任务关联数、缓存复用、排除缓存后的尝试/成功、新增历史去重 URL、分类错误与停止状态，并生成 SHA256 清单。再次读取不触发搜索或上传。

验收报告分别列明：已通过、失败/性能短板、缺凭据或额度的阻塞、本地修复尚未上线的部分。News 包装 URL 不冒充出版者真实地址；Trends 话题与报道分列；图片记录不冒充已下载文件；queued 不冒充平台最终入库。

## 本轮生产边界

2026-11-09 的生产 SSH 在密钥认证前关闭连接，故没有通过这条链路部署代码、查数据库或验证容器资源。浏览器 API 是本轮线上测量入口。私有来源缓存隔离补丁已在本地实现并回归，未因此直接覆盖冻结 worker/backend；上线须核对实际冻结文件并保留回滚，不允许整包覆盖既有差异文件。
