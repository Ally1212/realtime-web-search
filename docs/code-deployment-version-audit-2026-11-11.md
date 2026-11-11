# 代码与部署版本核对（2026-11-11）

本轮只读核对实际部署并提交本地开发成果，没有部署、重启、迁移或改变采集/上传开关。Git fetch 后本地 main 与 origin/main 均为 9ec166f，当前新增成果尚未提交，因此此前远端并非最新开发代码。

## 核对结论

- 采集机独立后台运行容器挂载的 `/app/realtime` 共108个Python文件：90个与本地逐字节一致，18个不同。9个 `insights*.py` 全部一致，`subscription_ui.py` 一致，确认真实数据分析及后续订阅交互修复已经发布。
- 应用机实际 `static/index.html` SHA256 为 `5ae440e505d0fa5a5046ae43ec6ec9eb7689f7dde8162ba6d344d4fddbd3c724`，与本地 `state/website-optimization-20261110-mobile/index.html` 一致。这是数据分析整合之后的订阅优化最终静态页。11月10日较早核对文档中的63e431…是历史版本。
- 本地四域能力为开发候选，未在生产目录中部署。当前仓库包含这些新模块及合同/路由/UI组合改动，不能把当前main整包视为可直接覆盖生产的部署镜像。生产还保留已有的独立冻结源码差异，禁止用本地web.py等全文件覆盖。

此次核对的是容器挂载文件及应用机静态文件，不等同于重新进行浏览器功能验收，也没有重新运行模型或采集。

## 不同文件

- `realtime/config.py`
- `realtime/customer_data.py`
- `realtime/customer_schema.py`
- `realtime/customer_subscriptions.py`
- `realtime/discovery.py`
- `realtime/fetcher.py`
- `realtime/free_google.py`
- `realtime/google_feeds.py`
- `realtime/google_whale_readback.py`
- `realtime/product.py`
- `realtime/product_http.py`
- `realtime/product_ui.py`
- `realtime/proxy_relay.py`
- `realtime/serp_fields.py`
- `realtime/serp_store.py`
- `realtime/serp_worker.py`
- `realtime/web.py`
- `realtime/worker.py`

customer_data.py、customer_schema.py、customer_subscriptions.py、product.py、product_ui.py的生产摘要与本轮提交前HEAD一致，差异来自本地候选改动；其余文件保留生产组合差异，不在本轮盲目同步。完整SHA256清单和本地比较证据保存在 `state/version-audit-20261111/`（忽略目录，不含凭据）。

## 本轮提交范围

保存数据分析实现、订阅交互修复、四域能力本地候选及其测试、设计与历次验证文档。提交与推送不构成上线，也不将合成测试能力声明为真实来源已接通。私密配置、原始业务数据、报告包和临时数据库不进入Git。

## 本轮验证

完整隔离回归：1455 passed、846 subtests passed、2 failed、0 skipped（898.78秒）。一项比较full/light统计时实时elapsed相差0.1秒；另一项本地HTTP的5秒读取超时。原两失败项立即在新隔离环境单独复跑均通过（2 passed / 1.38秒），超时原因不能仅凭复跑确定。机器当时负载很高。

将统计比较测试固定同一时钟，避免两次调用的墙钟推进影响等价断言，仅修改测试，不修改生产统计。另执行相关测试文件回归，结果见下方最终记录。Python编译、Git空白检查、待提交文件凭据模式扫描通过；无凭据模式命中。独立临时服务按脚本退出清理，不操作其他任务服务。

最终相关文件回归：149 passed、19 subtests passed、0 skipped、0 failed（89.95秒）。包含统计时钟修复、Google API、数据分析及订阅交互/客户端。三轮临时PostgreSQL/Redis均已停止，清理日志保留。没有再次重跑整套全量，不把专项通过改写为全量首次全通过。
