# SearchFlow 收藏组件 UI 改造（2026-11-12）

正式地址：https://searchflow.20-150-145-162.sslip.io/

## 实际交付

- 新增锁版本的 `frontend/` React 19 工程。实际使用 BoardUI 的 NavItem/Collapsible、Button/ButtonLink、Chip、StatCards、React Aria Table；Solid Glass 的 Turn Status、Status Glyph、Tool Steps；AffectUI 公开 MIT 的 Segmented Control Clean、Progress Tracker Clean。源码、许可及上游 commit 见 `frontend/README.md`。没有使用付费 Pro 源码，没有运行时 CDN。
- 新增 `realtime/component_ui.py` 与编译产物，替代被用户否决的 ObsidianUI 叠加层。首页流动示意、分组侧栏与收起、真实指标卡、来源卡片、API 模式选择/可展开真实进度、数据集和 Maps 数据表为 React 组件；其他模块统一表面、字号、表单、表格、提示与响应式样式，保留业务实现。
- 原表单/唯一 ID/事件仍由原业务 JS 管理；新表格的详情按钮调用原详情，Maps 表格可切原完整卡片查看营业时间；本次商家筛选只影响预览。Maps 桥接只复制白名单公开结果字段，不带任务凭据；新任务清除旧桥接结果。流程进度只读，禁止人工勾选或演示自动完成。
- 修复移动端旧遮罩 z-index 95 盖住新导航的冲突；四个请求选项在手机上缩写但保留完整可访问名称，均可见；附近商家模式隐藏无用关键词字段。保留系统减少动效、原搜索快捷键、焦点与移动导航行为。

## 验证范围

- TypeScript 检查和生产构建通过；相关 6 项回归通过，包含 Maps 城市/坐标恢复、批量请求失败/继续/幂等、组合页面结构/全部脚本语法、订阅乱序保护、Maps 桥接白名单及清除旧结果。生成器重复组合不重复注入；`git diff --check` 通过。
- 正式桌面 12 主模块逐一导航通过；最终正式版本 320/390 CSS px 各 12 模块（24 组）均唯一选中、无页面横向溢出、关闭菜单后 main 不残留 inert，错误监听无脚本异常。数据分析→搜索分析切换再次检查。键盘可切换请求模式；侧栏收起/恢复及快捷搜索通过。
- 实际历史查询 `sodium ion battery research` / google_news 返回 2 个请求；CSV 实际下载 56 数据行/74列，其中包含信封和55结果，不能称56篇文章。没有发起新采集。
- 刷新恢复已存伦敦 nearby/dentist 任务17家，预设城市恢复后自定义坐标为空；站内 Maps 与 API Maps 共用唯一表单和结果。表格空筛选、CSV 17行实际下载通过；商家缺失字段保持未知。
- 图片来源入口正确筛选，沿用显式加载图片，不自动请求外部图片。

证据：`output/boardui-20261112/`。浏览器站点原有110%缩放，使用 mobile:false 并记录实际 innerWidth；截图用 CDP 完整 clip，未改变用户全局缩放。首次移动遮罩问题已修复，最终验收没有浏览器内临时补丁。

## 发布与回退

只原子替换应用机 `/home/azureuser/projects/google-workbench/static/index.html`，保留 root:root/644；没有重启后端、worker、运行数据库迁移或开启采集/正文/旧Whale。CAS 校验并证明移除新旧呈现层后，原业务 HTML 字节相同。

- 发布前：`57afc5309616b588a2289f88460dccd8d1fa21493a39107acc2d81e054909fe8`
- 最终：`bb7abdd7057a3ac044523fea1632370970197fb24134b874599b54ab3d5ad06f`
- 原版备份：应用机 `state/boardui-20261112/backup/index.html`；首版候选：同目录 `index-r1.html`。回退原子恢复原版静态即可，不需要数据库操作。
- 本机 `product_ui.extend()` 已切换新模块；服务器后端生成器本轮未同步。以后重新生成正式静态页需携带本次 `component_ui.py` 及编译资产，不能从旧后端生成器覆盖新静态页。

Maps 中台同步仍待同事补 `google_maps` 及位置契约；本轮没有扩大后端能力声明。统计、额度、租户与导出契约保持原有边界。浏览器/预览/SSH资源收尾见任务完成记录。

收尾：ego Space 266 已 finish，保留正式首页 p2 交用户审核（ownership=user），本地预览页已关闭；4312 无监听，本任务 SSH masters 已退出，专属服务器临时文件已清理。资源收藏中三条原链接已追加技能规定的使用备注，未自行评分或更改“未验证过”。未提交 Git，既有工作树改动保留。
