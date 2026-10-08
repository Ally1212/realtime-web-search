# 纯 Google 网页搜索：烹饪、食材与家庭厨房

- 服务器实验：`google-kitchen-20261006`，2026-10-06 22:28（新加坡时间）启动。
- 截止：**2026-10-07 09:00 新加坡时间 / 01:00 UTC**。
- 沿用用户此前指定的 WML，仅 `google_web`；未收到通道选择的新答复时继续该设置。
- 新方向：烹饪、食材与家庭厨房，中英文。服务器实际保存 360,000 个不重复基础关键词，首批队列 11,264 个；自动补充已完成或已尝试的旧批次，无正文数量上限。
- 原园艺服务已禁用并停止，园艺 campaign 全部停止，历史数据保留。新实验重新建立页面基线并独立统计。
- 采集服务 `realtime-google-kitchen.service`，失败自动恢复；到截止时间后不会再次启动采集。
- 定时停止保障：`realtime-google-kitchen-finish.timer`，固定 UTC 01:00 触发，持久定时器、1 秒精度。先停止补词守护，再停止 worker 并保存最终报告；停 worker 最多给予 10 秒退出时间。
- 报告在 `state/google-kitchen-20261006/report.json`，最终报告 `final-report.json`，关键词 `keywords.jsonl`。正文只计全局唯一、至少 500 字符、实验开始后新增且在截止前入库的页面。
- 计时停止路径已用模拟验证：到点停止 worker、保存报告，不会再次启动 worker；服务器定时器显示下一次触发为 2026-10-07 01:00 UTC。
- 必要风险：上一轮 WML 持续约 10 小时仍被 Google 拒绝，入库 0；换方向和增加关键词无法保证绕过现有封锁。新实验保留失败率、验证码率、错误分类和零产出告警。

查看：

```sh
sudo systemctl status realtime-google-kitchen.service
sudo systemctl list-timers realtime-google-kitchen-finish.timer
sudo journalctl -u realtime-google-kitchen.service -n 5 --no-pager
cat state/google-kitchen-20261006/report.json
```
