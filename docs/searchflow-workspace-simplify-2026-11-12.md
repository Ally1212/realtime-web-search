# SearchFlow 工作台精简

## 结果

- SearchFlow文字品牌16→23px，旧图标与副标题在首帧CSS中隐藏，首次加载骨架后展示；统计保持四格占位，失败有重试。首页移除顶部搜索/新建/快速入口、流程图获取数据按钮、持续采集状态、汇总缓存说明、24小时与旧运营统计模块。
- 快速使用沿用 `#api`：新采集、地图商家、历史、按ID读取共享现有任务与导出能力；Maps不再单独导航，旧hash重定向。`#developer` 面向外部应用：只读展示现有数据OpenAPI的44个方法、请求参数、JSON体、鉴权、curl、响应与错误结构，不提交请求或管理真实密钥。curl含必需查询参数，复制反馈异步验证通过。
- 删除数据能力、平台同步、数据偏好的页面表单和客户端操作代码，保留空的inert路由壳并重定向首页。创建采集任务固定不加入旧Whale上传队列。没有删除历史记录、停用共享中台服务或撤销现有外部API；服务端内部集成与授权保护保持原状。
- 任务详情与全部关键词从表内展开改为一个右侧dialog抽屉，字段分栏、刷新选中任务、Esc关闭与焦点返回。搜索输入和关闭按钮不裁切，第一次Esc直接关闭，切换页面关闭弹层。初次整页读取显示骨架，小操作显示居中spinner；按用户实际触发的disabled状态识别加载，避免将“不可用”误作“加载”。

## 实现与刷新

`frontend/src/main.tsx` 与 `styles.css` 维护呈现；`realtime/workspace_refinement.py` 精确裁剪冻结页中已退役的客户端模块；`assets/workspace-refinement.js` 提供新路由、开发者文档、抽屉和加载行为；`component_ui.extend()` 组合成内联静态产物。

原先每10秒读取设置及运营统计的做法改为只在创建任务时读取默认地区、只读取首页所需数据；非任务页面60秒复用，任务状态仍10秒更新。后续刷新保留当前内容。未做网络或长期性能SLA测量，不声称消除了全部设备/网络上的卡顿。

## 验证

- TypeScript、构建、整页脚本语法检查通过。
- 7个相关回归通过，新增裁剪边界保护，更新旧“数据能力必须存在”的断言为已退役契约；Maps白名单与清空、快速请求、订阅竞态等保持通过。
- 真实公开数据回放：1440/900/390/320px首页、搜索、任务抽屉、开发者文档、Maps均无横向溢出。逐帧观察见骨架，未见旧品牌；抽屉贴右且不在表内展开；44文档方法、Maps单一表单、旧路由重定向、搜索提交筛选、curl导出format参数、复制异步反馈与加载居中通过。
- 正式HTTPS：1440/390/320px上述关键路径通过，无页面脚本错误；读取collector-settings确认continuous_enabled/content_fetch_enabled均false。没有提交新搜索、模型调用或修改采集任务。

## 发布与回退

只原子替换应用机 `/home/azureuser/projects/google-workbench/static/index.html`，发布前校验旧SHA及冻结业务基线，后续小修按SHA逐版CAS。未覆盖生产web.py/serp_page.py，未重建worker、未迁移数据库。

最终1,073,526字节，SHA256 `984f93d9b26ed7d2fd335f267a48582be2346267366817cc15a27e160d290189`。原版备份 `state/workspace-simplify-20261112/backup/index.html`，本轮中间版本在同目录revision-1、revision-2。回退只恢复原版静态页。

证据在 `output/workspace-simplify-20261112/`：local-verification.json、production-verification.json、published-final.json、final-verification.json及对应截图。多宽度生产测试在最后删除空运营面板前完成，最终hash和删除状态另行验证。浏览器任务281结束、保留正式结果页，清理两个本地预览服务和本任务SSH master。
