# AGENTS.md

## 生产服务器

- 地址：`azureuser@20.106.103.139`（Azure 美国 VPS，主机名 `vm-ubuntu-4c8g-us`）
- 登录：`ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes azureuser@20.106.103.139`
- 唯一保留的部署：`/home/azureuser/projects/realtime-web-search-openserp`（compose 项目名 `realtime-web-search-openserp`，6 容器：openserp / proxy-relay / local-worker / web / postgres / valkey）
- 网页入口：`http://20.106.103.139:8091`（或本机 `ssh -L 8091:127.0.0.1:8091 azureuser@20.106.103.139`）
- 私有代理白名单登记的是这台服务器的出口 IP（`20.106.103.139`）；不要从本机直连私有代理，必被防火墙拒绝。
- 已清理：`/opt/projects/realtime-web-search`（旧版）、`openserp-pipeline`（重复栈）、`/opt/projects/realtime-web-search-main`（代码副本）已于 2026-09-30 删除，删除前备份在服务器 `~/realtime-web-search-OLD-backup-20260930.sql.gz` 和 `~/openserp-pipeline-backup-20260930.sql.gz`。

## 总目标

**在服务器上持续运行采集系统，通过实验找到并固定"单日入库正文量最大"的采集配置，使系统长期以最大产能稳定运行。**

### 子目标与方法

1. **最大化日产量**：以"每天入库的合格新正文篇数"为唯一核心指标，持续优化直至达到系统上限。
2. **对照实验**：尝试不同来源组合并量化对比，至少覆盖两个极端配置：
   - "全部打开"：google_web + google_news + google_trends + rss 全部来源同时启用；
   - "纯正谷歌搜索"：仅启用 google_web（OpenSERP 标准 Google 网页搜索）。
   - 每组实验独立运行、记录尝试数/成功率/CAPTCHA 率/入库量，用数据决定最终配置。
3. **持续运行**：确定最优配置后，让系统 7×24 不间断采集，监控成功率与代理健康，异常时告警排查。

### 当前状态（2026-09-30）

- 4 个采集任务已停止（google_web 通道当时闲置）；历史产能参考：google_web 活跃期约 700 次搜索/小时，正文入库约 3.6 篇/小时（仅 News/Trends/RSS 来源时）。
- 私有代理池：固定 80 个云厂商 IP（Pekpik），经 proxy-relay 中转供 OpenSERP 使用。
