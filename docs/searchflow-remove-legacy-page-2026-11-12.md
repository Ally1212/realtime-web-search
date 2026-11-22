# 刷新旧页面清理（2026-11-12）

已上线 https://searchflow.20-150-145-162.sslip.io/ 。

## 原因与修改

确认此前正式首页仍包含旧彩色流程图、旧品牌、采集状态和24小时面板，只在末尾脚本执行后隐藏/删除。不能仅靠“缓存”解释用户截图；本轮没有证据证明客户端缓存是原因。另外 `/stats`、`/stats/legacy`、`/system-flow`、`/articles` 仍提供独立旧页面。

`workspace_refinement.remove_legacy_presentation()` 在生成阶段删除旧可视DOM，首页图和来源改为明确的React挂载节点；来源通过安全转义JSON传递，统计保留hidden/inert数据桥。品牌原生HTML只有SearchFlow，原生html已带sf-booting状态，脚本尚未执行或被禁用时直接显示骨架。业务API、历史数据和其他业务视图保留。

应用机static目录现在仅有index.html。旧5个独立HTML移出服务目录，旧地址及.html别名统一302至当前工作台对应hash。跳转显式使用正式HTTPS域名，避免反向代理内部协议/端口进入Location。保留原访问门；未改外部数据API或上游路由。

## 验证

- TypeScript检查、前端构建、8项相关Python回归、整页JavaScript语法检查通过。
- 本地和正式站禁用脚本时只显示SearchFlow与骨架，旧图及旧面板不存在。
- 正式响应SHA与本地一致、Cache-Control:no-store；3次刷新逐帧观察均无旧DOM、无页面脚本错误。
- 10个旧入口浏览器请求均返回当前工作台；正式静态目录仅index.html，gateway nginx -t通过。
- 1440/390/320布局、Maps单一表单、44个API文档方法、任务详情抽屉验证。没有提交新搜索，不启动自动采集/正文/Whale，不改数据库或worker。

## 发布与回退

静态SHA256：`317570007093b0cd0a671cc3260edf1d6858478c49f3d9dfa6fe562878e2c826`，1,066,162字节。

应用机备份：`/home/azureuser/projects/google-workbench/state/remove-legacy-20261112/backup/`，包含前版index.html、nginx.conf与退役5文件。恢复旧文件及配置后测试并reload gateway即可回退；未覆盖生产web.py/serp_page.py。

证据：`output/remove-legacy-20261112/` 中发布记录、production.json及本地/正式截图。

本轮ego任务空间284已finish（不保留测试页），本地4313预览与SSH master已清理。最终只读确认continuous_enabled/content_fetch_enabled均false。
