# SearchFlow 黑白 AI 工作台

2026-11-12 用户否决 BoardUI + Solid Glass + AffectUI 组合的视觉效果，要求流行 AI native 开源体系及统一黑白风格。正式站：https://searchflow.20-150-145-162.sslip.io/ 。

## 实现和来源

- 主体系改为 shadcn/ui neutral：实际导入 Button、Badge、Card、Tabs、Collapsible；Vercel AI Elements 实际导入 Task，用于真实请求状态、步骤与逐项结果。Lucide 负责 React 图标。不是新增聊天、模型或虚构推理。
- shadcn/ui MIT，commit `dd41d19e2f80ff6891951f7ced74c2c9a650b065`；AI Elements Apache-2.0，commit `ca8be600bfd01b3131f9bf6134b51602a71f4374`。源码、许可及修改说明见 `frontend/src/vendor` 和设计目录 `github.md`。完整第三方 notices 保留在静态 HTML。
- 原三库与 RemixIcon 已无运行时引用，构建输入清单可复核；旧源码保留作历史参考。React Aria 仅保留表格的可访问性与集合行为。
- 导航、汇总、来源入口、模式切换、任务流程、数据集和 Maps 结果使用 React 组件；其他业务表单继续使用原 DOM，统一黑白灰 tokens、线框、按钮、圆角、焦点和图表灰阶。未声称全部业务重写 React。
- 请求、权限、幂等、额度、下载仍由原业务处理；Maps 展示桥只传公开白名单字段，保留 null/0、新提交清理旧结果。

## 发布

只原子替换应用机 `/home/azureuser/projects/google-workbench/static/index.html`。
最终 SHA256：`c279274cb74e4b8fbb4dbbd46979ae33b9b46378a448cde3a0b2d0f1ed6ffa5b`，1,099,733 字节。

原版备份：`state/ai-native-mono-20261112/backup/index.html`；首次黑白候选备份：`backup/index-r1.html`。两次发布均检查前一版本 SHA，剥离呈现层后原业务 HTML 字节一致。未覆盖服务器 web.py/serp_page.py、未重启服务、未迁移数据库、未开启持续采集/正文/Whale。需要回退时检查现版哈希后，按原 owner/mode 原子恢复备份。

本地 `realtime/component_ui.py` 和 `realtime/assets/component-ui*` 是后续生成所需新层；服务器后端生成器本轮未同步，后续重生成静态页需携带此层，不能仅用旧生成器覆盖正式页。

## 验证

- TypeScript、生产构建、git diff --check 通过；Maps/快速请求/能力目录/导航竞态/展示桥 6 项回归通过。
- 正式 12 模块在 1584、429、352px 各 12 组检查通过；再按实际 CSS 宽 390、320px 各 12 组检查通过。均无横向溢出、单一选中项、手机抽屉能关闭。最终补丁仅统一遗留分析页配色。
- 真实历史 `sodium ion battery research` / Google News 读取2请求；CSV 56数据行、74列，与上一版已核验导出逐字段一致（含信封，不是56篇文章）。AI Elements Task 展开/收起及真实步骤可用；键盘方向键切换模式已验证。
- Maps 恢复伦敦附近 dentist 已存任务17家，默认经纬度空值保持，名称本地筛选到1家后可清除；CSV17行与上一版已核验导出逐字段一致。390px显示17张结果卡，无新搜索。
- 最终正式页面无捕获到的 JS error/unhandledrejection，首页4项汇总来自原DOM。汇总首次读取超过10秒后完成，不用虚构数字填补等待。
- 截图/导航/导出证据位于 `output/ai-native-20261112/`。 Maps 中台同步仍待中台增量更新，本轮UI不改变该边界。

旧资源用户反馈仅在项目设计元信息记为 changes-requested；尝试按原收藏条目追加反馈时，资源收藏.md 已不在原库根路径，未新建或覆盖笔记。
