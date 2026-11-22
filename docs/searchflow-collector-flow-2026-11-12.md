# 首页采集闭环动画

用户要求按“请求→采集器→平台采集→汇总到数据中台→返回结果”表现数据流，保留黑白风格及 Google 来源标识。

实现位于 `frontend/src/main.tsx` 与 `frontend/src/styles.css`：12段原生SVG曲线，6.6秒循环顺序显示数据点，采集器向五来源分流，再向中台汇总并返回。SVG坐标随容器重算，圆点不随窄屏变形；手机纵向布局，减少动态效果偏好关闭动画。图内标注目标流程及实际接入范围：Web经中台，其余来源当前独立返回。此次仅改变呈现，不新增中台后端对接。

验证：TypeScript与构建通过；Maps/快速请求/能力/订阅竞态/桥接6项回归通过。1440/900/390/320px无溢出；五标识加载；12段动画逐段可见，圆点保持圆形；减少动态效果与CTA通过。生产1440/390/320px与真实移动验证通过，持续采集仍暂停，未发起新搜索。

只原子发布应用机 `static/index.html`，发布前CAS校验旧版SHA，剥离三个呈现标签后业务HTML字节相等。备份 `/home/azureuser/projects/google-workbench/state/fan-flow-20261112/backup/index.html`。生产与候选SHA均为 `8c1f4b43bf40e4444cc16d206068faf7baf716ae9b99968b6041af7467b4dfea`，1,112,338字节。未重建任何worker、未修改数据库、未开启采集/正文/Whale。

证据：`output/fan-flow-20261112/published.json`、`production-verification.json`及截图。中间 `local-verification.json` 属前一分流版本，最终版本以 `chain-*.png` 和 `production-*.png` 为准。本任务ego空间278已finish，保留正式结果页；SSH master关闭，本地预览服务停止。
