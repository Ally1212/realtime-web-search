# SearchFlow 实机架构检查与部署修正

核验日期：2026-11-09；主要检查、修复与验收窗口 UTC 01:38–02:30（新加坡 09:38–10:30）。以 state/architecture-audit-20261109 中各快照时间为准。

## 结果

页面与登录保留应用机，业务后台已部署到采集机；应用机通过内网请求后台。Whale Writer、Consumer 均从采集机使用固定内网解析和 HTTPS；网络与 Writer 校验通过，独立 Consumer 因 402 credit_insufficient 无法完成本轮读回。

| 验收项 | 本轮结果 | 证明边界 |
| --- | --- | --- |
| 页面、登录、退出、后台 | ego(lite) 实测通过；五个只读接口 200；客户 legacy-data 200，无客户 Bearer 401 | 非全功能写入回归；未创建搜索任务 |
| 应用机到采集机 | 实际 socket 源 10.0.0.7 → 10.0.0.11:18097，HTTP 200 | 非公网 SSH 隧道；旧公网数据隧道 disabled |
| Whale 运行环境解析与 TLS | 后台与重建后的 worker 解析仅 10.0.0.10；对端 10.0.0.10:443，TLSv1.3，系统 CA / 域名校验通过 | 没有 verify=False、公网备用 IP 或代理绕行；未中断共享网络做故障注入 |
| Writer 身份与授权 | /v1/collectors/me 200；已有同条样本 validate 200、dry_run=true、valid=true | dry-run 不预留额度、不写入，不保证未来提交成功 |
| 正式持久化与终态 | 对历史同条修正版本重新查询 receipts：accepted，带 persisted_at、固定 document/version ID | 本轮新提交 0；历史提交时间 2026-11-06，不冒充本轮新增提交验收 |
| 独立身份读回 | Consumer 与 Writer 凭据不同；GET 固定文档返回 402 credit_insufficient | 未取得文档/版本正文，来源 ID、关键字段及固定版本一致性本轮未通过；未充值 |

本轮重新查询的历史记录：document `doc_9fb5017c5add01bc17809c40dade7d60`，version `ver_4a29e4654356820e56abd6407398d5b0`；source_record_key `google_search:serp-run:05677d68-57e4-4cd0-9e7c-cbeb585a016b:google-serp-snapshot-text-1`。这是既有公开搜索结果快照，不是新采集正文。

## 实际部署与资源边界

- 应用机 dev-server：20.150.145.162 / 10.0.0.7。项目 `/home/azureuser/projects/google-workbench`；共享 web-nginx-1 的 HTTPS 配置与容器不变。专属 google-workbench-gateway-1 提供静态页面和反代；172.18.0.1:18094 仅接共享 HTTPS 网段，8093 保留 HTTPS 跳转。访问门 google-workbench-access-1 仍在 127.0.0.1:18095，原凭据不变。
- 静态页面从线上当前代码生成结果导出，存于应用机项目 `static/`，只读挂载 `/srv/searchflow`。根页面及原 stats/articles 页面在应用机返回；所有业务 API 由采集机处理。以后页面代码更新时需重新导出静态页面，不会随后台代码自动更新。
- 原应用机 google-workbench-web-1、google-workbench-prd-1 已停止并设 restart=no，保留作回滚；应用机生效 Compose 已移除 web 服务，google-workbench.service 不再依赖数据隧道。google-workbench-tunnel.service 已 disable/stop。
- 采集机 spider：20.106.103.139 / 10.0.0.11。独立项目 `/home/azureuser/projects/google-workbench-backend`、Compose `google-workbench-backend`、systemd `google-workbench-backend.service` active/enabled。新增 web、prd、ingress 三个专属容器。代码来自应用机实际运行包，不部署本地未提交工作树；镜像使用本项目已有 worker 镜像，导入检查通过。
- 采集机私有入口 10.0.0.11:18097，仅 allow 10.0.0.7，其余 deny all；采集机自身访问该入口实测 403。Web 127.0.0.1:18093，PRD 127.0.0.1:18096；内部 Nginx 用 loopback 请求后台，保留原 HTTPS/Bearer 信任规则。未开放公网业务端口。
- 原 PostgreSQL、Valkey、OpenSERP、proxy-relay 容器 ID/启动时间未改变。保留独立 `realtime-web-search-openserp_postgres-data` / `realtime-web-search-openserp_valkey-data` 卷；数据库与缓存未迁移。后台使用采集机本地 relay 15432/16379；OpenSERP 本机 7001。回环 data-relay 17001 保留但应用机隧道已停。
- 队列为本项目独立 Valkey 中 realtime:* 命名空间，未清空或移动。代理为本项目 proxy-relay、state/proxies 与既有 Pekpik 配置；未使用其他项目凭据。本轮不发搜索请求，外部真实采集产能/成功率不在验收范围。
- Worker 仅为持久化 HTTPS/extra_hosts 配置单独重建；已先比较最终 Compose 与原容器，环境差异仅 WHALE_BASE_URL；代码、镜像、挂载和命令保持原冻结包。实际生效及开机读取的两个 Compose 均加入 www.marsyoo.com/marsyoo.com → 10.0.0.10；没有公网映射备用。
- 所有其他项目未执行覆盖、重启、删除或配置操作。应用机其他容器对比无变化；采集机 170 个其他容器中，reddit-v7-rss-backup 在核验窗口内被另行替换，不能声称所有其他容器状态绝对未变。本轮未对该容器执行操作，未据此回滚或干预它。

## 数据、代码与检查

修复前后：serp_requests 39,840；pages 332,768；公开分析投影 39,830；旧 outbox 9。continuous/body/uploads/claims 保持 false，enabled monitors=0。无新搜索、无 bulk、无充值、无历史正文/hash 迁移。

本地 `realtime/config.py`、`compose.yaml` 与 `.env.example` 的四处旧 HTTP 公网默认值改为 HTTPS 域名；生产使用专属 Compose 域名映射。可复查的无凭据 Nginx 模板在 `deploy/searchflow-application.nginx.conf`、`deploy/searchflow-private-backend.nginx.conf`。

本地相关测试 22 passed、23 skipped、21 subtests passed；跳过项需要隔离 PostgreSQL，不能当作通过。线上 Nginx 配置、真实数据库只读基线、客户权限、页面/登录和私网链路另行验收，详见证据。

匿名浏览器进入工作台会返回访问验证页；匿名 page.fetch 报 Failed to fetch，本轮不把它单独当作公网 HTTP 401 证据。上表无客户 Bearer 401 是已有站点访问会话下的客户 API 鉴权结果，两者不同。

## 备份与回滚

修改前，两机各自原项目下 `state/architecture-20261109/backup/` 保存原部署配置；包含凭据的原 Docker 配置仅留远端受限目录，未打印或复制到本地证据。回滚脚本分别位于两机原项目 `state/architecture-20261109/rollback.sh`，权限 700。

回滚顺序：先在采集机停止新增 backend、恢复 worker 冻结 Compose 并单独恢复 worker；再在应用机恢复专属配置、旧 web/prd 与原隧道。全程不执行 compose down、remove-orphans、卷删除或共享 HTTPS 重启。原镜像、代码、静态页面及暂停状态均保留。

后续需要补足平台可用额度，并明确下一次单条样本/预算授权，才能完成新的真实提交和独立 Consumer 固定版本逐字段读回；本轮未擅自扩大旧单次授权。

## 证据

目录 `state/architecture-audit-20261109/`：两机 inventory/after、other-project-comparison、final-project-containers、private-peer、whale-readback、whale-writer、consumer-credit、final-data-and-worker、browser-after、customer-route、final-browser、架构预览与截图。API 原始敏感身份字段和完整凭据均未写入这些文件。

桌面 `SearchFlow-实际部署架构-20261109.svg` 已通过 ego(lite) HTTP 预览及截图检查；实线区分网络已验证，虚线明确 Consumer 业务读回被阻塞。浏览器任务和本地预览服务完成后清理。
