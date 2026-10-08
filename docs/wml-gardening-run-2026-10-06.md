# WML 园艺长期采集

- 服务器目录：`/home/azureuser/projects/realtime-web-search-openserp`。
- 实验标识：`wml-gardening-20261006`。
- 启动时间：2026-10-06 03:48:58 UTC / 新加坡时间 11:48:58。
- 新话题：园艺、阳台种植与植物养护，中英文。
- 仅 `google_web` 来源、仅 `wml` provider；不会回退到 OpenSERP、News、Trends 或 RSS。
- 无终止时间、无正文配额。按用户扩大规模的要求，首批队列为 **11,264 个关键词**，保留 1,408 个活跃批次，每批 8 个关键词。完成的批次替换；已开始但未完成的旧批次每 4 小时轮换，尚未开始的批次不会因等待超过 4 小时被丢弃。关键词游标和任务关联保存在 PostgreSQL，重启后继续。
- 已实际生成并保存 **223,200 个不重复基础关键词**到 `state/wml-gardening-20261006/keywords.jsonl`，词库信息在同目录 `catalog.json`。耗尽后按真实日期提供新的 `after:` 搜索组合，长期持续补充。若提前耗尽某日组合，会等待次日，不生成未来日期。
- local-worker 为 4 个槽位、正文最短 500 字符；WML 初始 RPS 1、上限 2、单代理最小间隔 90 秒、失败冷却 900 秒，实际速率由健康状态调整。
- 旧强制提升 delayed 队列的 `~/realtime-monitor-20261004/promote.sh` 已停止，重试遵守正常冷却。

## 守护和报告

systemd：`realtime-wml-gardening.service`，已启用开机启动、失败后自动重启；每分钟补充任务并更新报告。如果 local-worker 退出，守护会恢复它；Google 403 不通过反复重启 worker 处理。

```sh
sudo systemctl status realtime-wml-gardening.service
sudo journalctl -u realtime-wml-gardening.service -n 10 --no-pager
cat state/wml-gardening-20261006/report.json
cat state/wml-gardening-20261006/alerts.json
```

告警写入报告、alerts.json 和 systemd 日志，不发送外部消息。至少 20 次尝试且成功率低于 5%立即记录告警；运行一小时后检查无搜索和无合格新正文。

正文只计实验开始后新建、属于本实验、长度至少 500 字符的全局唯一页面，按首次关联小时归属；每个页面整个实验只计一次。报告包括累计产量、最近一小时增量、小时产量、搜索成功率、验证码率和错误分类。

## 首轮验证

- 验证生产 worker 配置为 `providers=('wml',)`、正文长度 500、4 个槽位。
- 连续控制 tick 成功；初始小批运行后，按用户要求扩至 1,408 个活跃批次、11,264 个排队关键词。
- 扩容后本地验证 223,200 个基础关键词没有重复，既有关键词索引保持一致。
- 启动后前 77 次搜索全部来自 WML，均为 `google_http_403`，合格新正文 0，已写低成功率告警。此时任务和守护运行正常，但搜索被 Google 拒绝，尚未恢复历史产量。

配置备份：服务器 `.env.before-wml-gardening-20261006T034740Z`。旧报告和历史数据保留。

## 停止方式

先停止守护，再停止 worker，避免守护自动恢复 worker：

```sh
sudo systemctl disable --now realtime-wml-gardening.service
docker compose stop local-worker
```

这会暂停运行；任务和游标保留，重新启用服务即可继续。
