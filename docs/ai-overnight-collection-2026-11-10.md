# AI 内容持续采集至次日09:00（2026-11-10）

用户明确授权新建AI相关持续采集，截止明天上午09:00；按用户时区Asia/Singapore，固定截止2026-11-11 09:00 +08:00（UTC01:00）。仅授权本任务，不恢复全站自动供给、正文、Whale上传/领取或旧实验。

- campaign_id：`a0d0ec51-9624-4b47-b73d-e57c02a05d9f`，主查询“AI 人工智能最新进展”，20条中英文关键词，涵盖模型、研究、智能体、编程、芯片、安全和主要模型厂商；完整payload在state/ai-overnight-20261110/task.json。
- 模式scheduled，Google Web+Google News，Web一页/US/auto，News US:en；默认现有调度与缓存，Web查询周期7200秒、News基础间隔3600秒，不宣称每轮均新采或完整覆盖所有AI内容。保存SERP标题/摘要/链接/时间和历史去重结果，不采正文、不上传。
- 通过正式HTTPS双层认证的管理API幂等创建暂停任务，先安装验证停止定时器再start；UTC15:41启动，网页显示运行中。启动快照4次请求、3成功、0缓存复用、8个历史去重新URL，含1次google_http_403待既有退避重试；不是最终成绩或长期成功率。
- 自动停止：采集机`searchflow-ai-deadline-20261111.timer` active/enabled，OnCalendar固定UTC01:00，Persistent=true/AccuracySec=1s，service失败30秒后重试；只修改本CID状态与停止原因，不修改其他任务、数据或总开关。停止脚本位于后台项目state/ai-overnight-20261110/stop-task.py，--check验证本人任务身份且不写入；真正执行拒绝提前运行。停止阻止新工作，已发出的网络请求可能稍后排空。
- 本轮无产品源码修改、旧迁移、容器重建或采集全局开关修改。全站continuous_enabled=false，正文false；手动scheduled任务独立运行，所以顶部全站“持续采集已暂停”不代表这个任务暂停。
- 浏览器ego空间135仅用于本任务，已筛选并展开关键词供用户查看；finish保留p1结果页，空间交还用户。其他Instagram AI任务/定时器未操作。

用户可在网站采集任务搜索“AI 人工智能最新进展”，查看进度或手动暂停/停止；结果按钮限定此campaign。定时器安装证据state/ai-overnight-20261110/deadline-verification.txt。到期后的实际停止结果尚未发生，不能宣称已验证次日停止。
