# 数据闭环可靠性最小补丁上线（2026-11-06）

已按冻结候选包精确发布4个文件副本、3个模块。仅重启Web的web/prd和采集机local-worker，其他四个采集服务ID、启动时间、挂载不变。每台独立备份先完成后替换；没有迁移、来源登记、新搜索、Whale写入、旧队列重开或自动开关变更。

## 实际改动与验证边界

- 两端whale_serp.py：请求级事件身份、同键异载荷拒绝、queued与终态区分、部分失败对账与共享冷却。未开启上传/领取；本次没有对真实平台注入故障，因此线上重试/终态行为仍待获授权验收。
- Web google_data_export.py：JSONL、嵌套CSV与occurrence CSV逐字段/顺序核对。生产只读导出最新公开Web1请求7结果、News1请求86结果、Trends1请求10话题30报道，均与保存源记录标准化结果完全一致，三格式验证通过。News86条仍为未解析包装链接；Trends不是数值历史曲线。临时文件已删除。
- Web google_exports.py：已存在导出service返回前重查当前权限、时间与来源期限。正式域导出表仍不存在，本次未启用该能力，不宣称生产到期下载端到端验收。

候选包SHA256：`882cf3d7a93dc02db9859669f60cc3ff2118f22372c9d9daa455c34a637f76f7`。发布前后目标hash符合before/after；web.py、serp_page.py及其他保护文件hash不变。发布前已有冻结测试Web38项、worker28项和发布工具8项通过；本次未重跑工作树887项，不以它替代生产验证。

## 线上证据

HTTPS6项API烟测通过：未认证401；有访问Key的healthz200；客户usage200；Web/News/Trends历史接口各200并只返回1条匹配来源的公开请求。只读GET不创建采集任务。三容器running、OOM false、restart_count0，新启动日志Traceback0。

前检、逐机重启后、所有API/导出后数据库只读快照一致：请求39840（Web31679/News8127/Trends29/Images5）、pages332768、旧outbox9queued；continuous/content_fetch/uploads/claims均false，手动任务、分页/查询/上传lease、客户queued/running任务和启用监测均0。正式registry0条，新spool/账本/正式导出表仍不存在。

首次限制1请求的导出按升序选最早记录，因此Trends10话题/0报道；与API最新记录不是同一样本。保留该截断验证证据，并另用最新完成时间过滤得到上述10话题30报道，与源逐字段相等。没有将两者错误拼成一致样本。

证据目录：`state/google-data-closure-20261106/release-review/data-closure-release-20261106-0820/`。汇总 `verification-summary.json`，各机`*-verification.json`、HTTPS `https-api-smoke.json`、真实导出 `deployed-latest-source-export.json`、最终数据 `final-after-export-no-change.json`。原只读发布评审文档记录的是发布前阶段，不覆盖本文的新状态。

## 精确回退

两机项目state下均有 `data-closure-release-20261106-0820`，含本次原文件、journal.json、manifest.json及release_guard.py。用新鲜只读数据库快照和同一固定候选包执行工具rollback，只有当前文件为本次old/new hash且保护文件未变才允许恢复。随后仅重启相同三个服务并重做业务/鉴权检查；不恢复旧Compose、不覆盖其他文件或回滚数据库。此次未触发生产回退，回退故障路径仅在本地测试验证。

## 对应需求与剩余缺口

GO-ID-003、GO-ING-001/003/005、GO-API-108、GO-NFR-007仅新增上述局部上线/导出证据，整体保持部分完成。正式来源/用途、schema和ReaderAdmission批准未齐；旧1条固定版本摘要异常待平台审计；News/Trends独立平台交付、真实新采集、七天及真实百万行仍未验收。本次不把旧链接上传路径升级为完整run自动交付。

下一步准备有单次边界、停止保护与完整证据输出的Web端到端执行器，先完成隔离验证；新搜索及平台写入仍需用户明确授权。
