# SearchFlow 工作台迁移（2026-10-22）

## 当前状态

网页已部署到 `azureuser@20.150.145.162`（主机 dev-server）。目标公网入口 `http://20.150.145.162:8093`，按用户明确要求不增加登录保护。

公网 TCP 8093 已验证可达：从原服务器直连 HTTP 200，Playwright 直接打开公网地址并完成真实手动任务、结果查看和 CSV 下载。先前连接超时的问题已消失；未通过当前服务器身份修改 Azure 网络规则。

旧网页已清理：停止并移除原 web 容器，旧 8093 无监听；从原 base Compose、override、current-compose.json 和 systemd 启动项移除 web。原五个服务保持原容器 ID，采集与数据部署保留。

## 部署边界

- 迁移网页服务及其运行依赖，不移动 Google worker、PostgreSQL、Valkey、OpenSERP 或私有代理出口。
- 新项目目录 `/home/azureuser/projects/google-workbench`，Compose 项目 `google-workbench`，配置 `compose.json`。
- `web` 使用原生产镜像经 docker save/load 导入，绑定 127.0.0.1:18093；冻结代码复制自原服务器 `state/field-expansion-20261016/revisions/r3/code/web/realtime`，挂载新目录 code/realtime。
- `gateway` 为 Nginx，监听公网 8093，转发到本机 18093。没有改动新服务器已有的 80/443 或其他项目。
- 新服务器 `private/web.env` 保存必要数据库和平台配置，权限 600；private 目录 700。未复制工作树历史正文/hash 迁移，也未启动新采集 worker。

## 数据连接

新服务器 systemd `google-workbench-tunnel.service` 使用专用 Ed25519 密钥建立 SSH 本地转发，连接原服务器 20.106.103.139。专用公钥在原服务器 authorized_keys 中限制为仅转发三个回环端口、禁止 shell、PTY、agent 等用途，不使用操作员私钥部署。

原服务器新增独立 Compose `google-workbench-data-relay`，配置位于原项目 `state/workbench-data-relay-compose.json`。relay 使用原镜像内 Python，通过原 Docker 网络按服务名连接目标，只向宿主 127.0.0.1 发布端口；数据库和 Valkey 未新增公网暴露。

| 新服务器回环端口 | 加密转发后的目标 |
| --- | --- |
| 15432 | 原 Docker 网络 postgres:5432 |
| 16379 | 原 Docker 网络 valkey:6379 |
| 17001 | 原 Docker 网络 openserp:7000 |

relay 对新连接重新解析服务名，不依赖数据库容器当前 IP。worker 仍连接原本的数据库与队列；新网页写入同一份任务和设置，避免双库和数据复制。

## 开机管理

新服务器：

```bash
sudo systemctl status google-workbench.service google-workbench-tunnel.service
cd /home/azureuser/projects/google-workbench
docker compose -p google-workbench -f compose.json ps
```

两项服务已 enabled。网页与网关容器 restart=unless-stopped；隧道 Restart=always、15 秒保活，断开自动重连。原数据 relay 容器也设为 unless-stopped。

## 已完成的验证

- 新网站内部首页 HTTP 200，web 容器 healthy；隧道访问原 OpenSERP /ready HTTP 200。
- 新网页 GET API collector settings、Whale 状态与迁移前快照完全一致。
- 原有全历史新增 URL 424,102，最近 24 小时 1 次成功请求、3 个新增 URL；迁移期间未增加搜索。
- Playwright 打开新服务器网页（临时 SSH 转发 localhost:18094），首页实际加载已有标题、摘要、链接和来源。
- 浏览器保存任务 `42f69070-1c20-4602-897e-fb578a4f9b03`，关键词 `workbench migration verification 20261022`，初始 paused/started=false；随后通过浏览器停止，requests=0。测试未发起采集或上传。
- 仅导出本日既有测试 campaign `7a310535-5bac-46c2-8d1f-1eed92fbab58`，避免全历史导出；核对用户 CSV 44 列、10 条结果。
- 代码文件哈希、设置和导出证据保存在本地忽略目录 `state/workbench-move-20261022`；浏览器截图 `output/playwright/workbench-new-host-tasks-20261022.png`。
- 切换前复核：47 个网站文件哈希完全一致；原五个服务容器 ID 未变，设置和统计保持迁移前快照。证据 `final-pending-public-check.json`。
- 公网实际任务：浏览器创建 `86784589-c680-48ce-ac38-6fa857d332fd`，关键词 `Singapore renewable energy research October 2026`，google_web / 美国 / 自动语言 / 1 页 / once，不加入上传队列。UTC 08:13:13 创建、08:13:26 自动 completed，stop_reason=scope_completed；1 次成功非缓存搜索，9 条自然结果，8 个历史去重新 URL。
- 从任务行进入结果，确认 9 条实际标题、摘要、链接和排名；浏览器下载 CSV，核对 44 列、9 行。Console Errors=0、Warnings=0，平台同步和设置页面可打开。截图 `output/playwright/workbench-public-results-20261022.png`，结果 CSV 和快照存于本地证据目录。
- 清理后公网 API 再验通过：采集和 Whale 设置与基线一致，仅增加本次 1 次搜索与 8 个新链接；原五个容器 ID 未变。证据 `final-public-check.json`、`public-worker-task.json`。自动采集、正文、领取和上传始终关闭，本次仅运行手动一次性任务。

## 已完成的切换与清理

1. 外网 HTTP、浏览器任务与 API 验证通过。
2. 原 web 已停止并移除，启动项已移除；两种有效 Compose 合并配置均校验通过。回退文件位于原项目 `state/workbench-old-web-backup-20261022`，旧冻结包保留归档，未删除 worker 项目。
3. 已验证原五个服务容器 ID 未变，旧 8093 无监听，采集、正文、领取和上传仍为 false。
4. 新网站不独立保存另一份数据库：页面把任务写入原 PostgreSQL、把任务 ID 放入原 Valkey 队列；原 worker 从队列取 ID、读取数据库参数，经 OpenSERP 和代理执行 Google 搜索，再把结果写回同一数据库。新网页读取这些结果展示。新服务器与原服务之间使用专用 SSH 加密转发。
