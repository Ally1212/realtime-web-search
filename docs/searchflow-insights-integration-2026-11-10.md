# 数据分析同站整合验收

正式入口：https://searchflow.20-150-145-162.sslip.io/#insights

已将独立页面改为现有工作台内同一文档视图，继承蓝灰组件、字体、导航及客户会话。没有iframe；旧入口302到正式HTTPS新入口。来源、关键词、请求上限、读取按钮为主要操作；日期、文件导入、历史样本、AI标签和人工复核保留并折叠。默认概览展示记录、网站、请求、摘要、查询词、完成时间和原始记录，可直接导出CSV。

## 实现与口径

新insights_embed.py注入原工作台，DOM命名空间与脚本闭包避免冲突。insights_ui.extend()委托新模块。CustomerDataStore继续限制公开/本客户数据。保存完整原始occurrence、request_id、occurrence_id、响应hash、标题、摘要、查询参数与已知时间。采集完成时间依据request.completed_at，出版日期独立保留；缺失不推断。原文CSV增加source_fields JSON。Python确定性统计无需模型，Google跳转包装域名不作为媒体网站。

默认最新20请求，最大100请求/5000条，截取提示保留，不是全库结论。搜索内容不当作用户评论；新Amazon/Reddit/X抓取器尚未接入。AI仍需显式授权、人工确认和证据校验。

## 验证

- 隔离PostgreSQL相关回归93 passed、5 subtests、0 skipped；最后界面标题和重试名称改进另跑3个嵌入测试及JS语法检查通过。
- 正式四来源只读测试Web8、News193、Trends132、Images90；共6个中间测试快照已按版本删除，未删除原始数据。
- 保留最新20请求快照242条，18个有内容请求贡献记录、另2个无文本；128已知网站、153含摘要。242/242原始source_record、查询及完成时间与鉴权原始接口逐条深比较一致。完成时间范围2026-11-09T06:30:51.763843Z至07:56:07.468855Z。
- 同文档导航、旧入口跳转、桌面/390px/320px和真实CSV字段验证通过。证据在state/insights-integration-20261110/，包括original-data-match.json、source-matrix.json、regression.xml、截图及清理回执。
- 本轮0新搜索、0模型调用。前后原始请求39962、pages332768、分析投影39934、Whale队列9一致；持续采集/正文/上传/领取关闭、启用监测0。

## 发布与回退

Web机仅替换static/index.html及gateway旧入口重定向，nginx校验并reload；采集机仅更新独立insights模块、重启该服务，健康正常。未覆盖web.py/serp_page.py、未重建原服务、未运行原库迁移。

两机各项目state/insights-integration-20261110/backup/保存前版本。回退仅恢复对应文件、重启独立insights/reload gateway，不回滚业务数据库。模型凭据仍为私密文件，不进入网页和仓库。未提交Git。

本地预览HTTP18762、只读SSH隧道18099及独立PostgreSQL55487已停止，ego任务空间104已finish并核验不存在。
