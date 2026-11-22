# 四来源真实存取验证（进行中）

范围：Web、News、Trends、Maps；用户明确排除Images。须完成来源真实采集、中台accepted/duplicate、固定版本完整载荷与hash校验、CSV/JSONL一致性，再交付同事操作短文档。当前不能宣称全部通过。

现有生产Collector Key经/v1/collectors/me只读核验：仅social_media_raw/google_search，允许keyword_search/live_query；无News/Trends平台权限。因此在独立ego任务空间315新建News与Trends专属Collector Key，各仅social_media_raw、各自真实平台、live_query。密钥在state/private/news-middle-collector-key及trends-middle-collector-key，600，未打印/提交。Maps此前20家/零结果严格存取及导出已通过，证据保留。

下一步：核验现有Consumer对Web/News/Trends授权及live限额；建立隔离PG/运行器，使用现有OpenSERP与代理基础设施小范围采集，不启用自动供给或正文；补共享固定版本载荷读取适配（参考Maps的批量snapshot读回），各来源实际验收；最终完成同事文档。正式旧生产worker尚未改动。

浏览器空间315，p1当前/admin/access；上个空间294已finish且保留用户参与页面，不再操作。

## 已完成的小范围真实验收

Web 4条/4固定版本；News10条/10固定版本；Trends10条/10固定版本。三个来源各succeeded/readback_verified=true/charged1，同空间used3/reserved0，跨客户job404与本地API幂等复用通过。来自中台固定版本的完整record分别通过正式customer_export导出函数CSV/JSONL深比较（含request记录和每条occurrence）。Maps复用同日已完成的20家与0家真实协议验证及导出证据。Images未测试、未适配。

补齐News/Trends专属live_query Collector；共享固定版本snapshot读取移至MiddlePlatformClient，Google三来源在batch版本响应缺platform_payload时使用绑定同document/version/hash的官方snapshot payload batch补齐；Web/Feed原始envelope预先保存真实result_count指标及metrics，避免异步补字段改变摘要。相关47项回归全部通过21.065秒，无跳过；compileall/diff检查通过。

使用独立PG17/运行器，连接既有OpenSERP/代理与真实中台，小范围手动任务；生产原worker/网页未重启，没有开持续供给或正文。临时自有容器/PG/服务器目录及ego315已清理，用户原页面未操作。正式网站后端尚未替换发布，不能称所有现网入口已上线。

证据output/four-source-validation-20261113/{google_web-job.json,google_news-job.json,google_trends-job.json,summary.json,export-proof.json,isolation-quota-proof.json,live-run.log,regression.log}；同事操作短文档：中台小范围存取验收指南.md。

## 正式发布更新（2026-11-13）

新实时查询适配已部署正式网站并完成Web 3条、News 10条、Trends 10条、Maps 20家正式存取、固定版本校验和下载深比较。此前未部署的描述为历史阶段。Images沿用原流程；原始历史和原采集管理任务不在本次迁移范围。正式验收发现并修复Trends跨话题重复报道的计数问题，保留首测失败。详见[正式发布说明](searchflow-middle-production-release-2026-11-13.md)。
