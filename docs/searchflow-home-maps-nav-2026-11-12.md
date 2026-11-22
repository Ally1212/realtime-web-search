# 首页 Maps 与导航修复（2026-11-12）

- 首页数据来源增加 Google Maps「已支持」入口，跳转 `/#maps`，说明商家、电话、网站、评分与导出。Maps 当前仍未接 Marsyoo 中台。
- 修复数据分析离开后残留选中状态：路由切换先清理侧栏 `aria-current`，再设置当前项；创建任务仍归属采集任务。
- 仅最小修改应用机 `/home/azureuser/projects/google-workbench/static/index.html`，未重启服务、修改数据库或发起搜索。备份在同项目 `state/home-maps-nav-20261112/backup/index.html`。发布文件 SHA256：`9b376f5cf0d4bd1cb599885354780052f3ffc174b29f678fdb9e2893bee618f4`。
- 验证：相关单元测试 3 passed，Python 编译与页面 JavaScript 语法检查通过；正式站导航切换、刷新后切换、Maps 卡片、创建任务归属均通过；390px 页面宽度与滚动宽度均为390。证据在 `output/home-maps-nav-20261112/verification.json` 及同目录截图。
- ego 任务工作区252已完成清理；原248丢失后经用户明确允许重建。
