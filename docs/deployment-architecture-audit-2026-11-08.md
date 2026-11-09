# SearchFlow 部署架构核查（2026-11-08）

核验时间：2026-11-08 11:49–11:55 UTC（新加坡 19:49–19:55）。本次只读检查，未修改生产、未重启服务、未执行数据库迁移或新搜索/平台上传，未输出明文凭据。

## 结论

公网 HTTPS 登录、首页和五个只读后台接口正常。两台服务器 SSH 均在收到服务端版本及密钥认证之前断开，实际容器、端口、内网通信和 Whale 当前身份/额度无法验证。不能宣布架构已符合要求，也不能根据历史记录确定后台当前在哪台机器。

当前目标以用户本轮要求为准：应用机 20.150.145.162 / 10.0.0.7 负责页面、登录、反向代理；采集机 20.106.103.139 / 10.0.0.11 负责业务后台和采集；Whale 10.0.0.10，双向 HTTPS 内网连接，无公网回退。三个内网 IP 本轮均未通过服务器网卡或实际连接核验。

## 本次实测

| 项目 | 本次结果 | 证明边界 |
| --- | --- | --- |
| HTTPS 登录页面 | 可打开，浏览器正常加载 | 本机经过 127.0.0.1:7890 代理；浏览器 response securityState=secure，未关闭证书校验；不能证明服务器间链路 |
| 使用现有本站访问 Key 登录 | 成功进入工作台 | Key 从本机专属文件读取，未打印；无新增凭据 |
| 首页汇总 /api/product/analytics/summary | 200，39,830 公开投影请求、522,676 窗口去重 URL | 页面汇总，不冒充原始请求总量 |
| /api/collector-settings | 200，continuous_enabled=false，content_fetch_enabled=false | 当前网站接口返回值 |
| /api/whale/status | 200 | 仅本地业务状态接口，不能替代 Whale 真实身份验证 |
| /api/serp/tasks?limit=1 | 200 | 只读任务查询 |
| /api/serp/results?limit=1&count_total=false | 200 | 只读结果查询 |
| 退出访问 | 返回访问验证页面 | 未改变其他浏览器任务的会话 |
| 匿名 API | 导航最终进入登录页；fetch 失败 | 本轮未取得可独立证实的 HTTP 401，不能沿用历史验证值 |
| 两机 SSH 22 | TCP 建立后 kex_exchange_identification: Connection closed | 尚未进入用户认证，无法归因于 Key；可能为本机代理路径、中间网络或服务端规则 |

本机 route 显示两目标均经 utun1500 / 198.18.0.1；系统 HTTP/HTTPS/SOCKS 代理端口为 7890。额外对应用机使用单连接 SOCKS ProxyCommand 诊断仍在 SSH 握手前关闭；没有修改系统路由、代理、VPN 或服务器安全规则。

## 资源盘点：仅本地源码和历史记录，尚非现网盘点

| 资源 | 已知配置/历史位置 | 尚需核查 |
| --- | --- | --- |
| 应用项目 | /home/azureuser/projects/google-workbench；Compose google-workbench | 当前容器 ID、挂载、服务归属、实际运行包 |
| HTTPS / gateway / 登录 | 公网 443；历史 8093 跳转、172.18.0.1:18094、127.0.0.1:18095 | 当前监听与共享 HTTPS 的本项目虚拟主机边界 |
| 业务 Web | 本地 deploy/access-gateway.nginx.conf 转发至 127.0.0.1:18093；历史部署在应用机 | 与本轮后台放采集机要求存在差异，须实机核实后再修复，不能直接覆盖模板 |
| 采集项目 | /home/azureuser/projects/realtime-web-search-openserp；Compose realtime-web-search-openserp | 与 Instagram 等同机项目的容器、网络、卷完全区分 |
| 采集服务 | 历史 openserp / proxy-relay / local-worker / postgres / valkey | 当前容器标签、端口、重启时间、配置及数据目录 |
| 数据库/缓存 | PostgreSQL :5432、Valkey :6379；本地命名卷 postgres-data / valkey-data（Compose 前缀作用域） | 实际卷名、DB、共享情况与客户端来源 |
| 队列 | 源码 realtime:campaigns:ready / delayed，realtime:campaign:queued:* | 当前独立 Valkey 实例及队列内容；不迁移/清空队列 |
| 数据隧道 | 历史 google-workbench-tunnel.service，经 15432/16379/17001 转发数据库、缓存、OpenSERP | 实际目标公网/内网、活跃 socket；不能据历史隧道宣称符合内网要求 |
| 代理 | 历史采集机出口及 Pekpik proxy-relay；缓存 state/proxies | 仅核验本项目配置，不能复用其他项目凭据或从本机连接私有代理 |
| Whale | 历史显式 https://www.marsyoo.com 覆盖文件；源码 Config/Compose 仍有旧 http://20.169.21.11 默认值 | 当前生效值、容器 DNS、代理环境、TLS SNI、对端 10.0.0.10 与断网无公网回退 |

源码读回客户端要求 HTTPS，默认保留 Requests 证书验证且拒绝重定向；单次平台适配器区分 Writer/Consumer，并关闭环境代理继承。旧 Collector 配置分支仍可落到历史 HTTP 默认值；本轮无法确认生产是否触发，未修改这批有既有改动的源码或未核验的部署配置。

## 六项验收状态

1. 网站页面、登录、只读后台请求：本次通过上述范围；不等于所有业务功能回归。
2. 应用机 → 采集机内网后台：阻塞，服务位置及端口未核验。
3. 实际运行环境 Whale DNS/连接目标 10.0.0.10：阻塞，未从本机绕公网探测 Whale。
4. Whale 当前身份、权限、额度：阻塞，未调用；2026-11-06 曾有 402/credit_insufficient，仅历史情况，不能当作当前结果。
5. validate、真实提交、正式入库及终态回执：未执行；内网、身份、额度与具体单次任务预算未核定，不新增写入或采集。
6. 独立 Consumer 固定版本读回：未执行；不拿工作台证明缓存或历史 accepted 替代当前读回。

## 解除阻塞后的实施与回滚边界

先恢复本项目 SSH 可达路径（核对当前来源放行或已有堡垒机/端口，不发送密钥）。登录后按 Compose 标签、systemd 单元与监听 PID 盘点两机所有服务名，读取配置时仅输出白名单端点及凭据是否存在，不输出原始 env 或带密码 URL。

任何部署前保存本项目生效 Compose、专属 Nginx 配置、运行代码哈希、容器 ID/启动时间和数据库只读基线；记录恢复命令。不对整台机器执行 compose down、remove-orphans 或覆盖共享配置。若需将本项目 gateway 上游指向采集机，仅在确认真实后台和空闲端口后操作本项目配置；后台保留在采集机。变更先配置校验、逐服务健康检查，再对本项目单服务 reload/recreate。回滚恢复本次备份，保留访问门，不能恢复无鉴权旧配置。

Whale 使用证书对应域名在本项目运行环境解析到 10.0.0.10，验证实际 TCP 对端、SNI 和证书链，隔离绕行代理并验证无公网回退；不全局修改其他项目 DNS、代理或凭据。分别验证网络、身份授权、validate、正式 accepted 与独立固定版本一致性，每阶段独立留证。真实提交需绑定明确单次样本和现有预算；额度不足停止，不充值。

## 证据与交付

- state/architecture-audit-20261108/browser-authenticated.json：本次五接口状态及受限字段。
- state/architecture-audit-20261108/browser-anonymous.json：匿名 fetch 失败的真实结果。
- state/architecture-audit-20261108/ssh-app.log、ssh-collector.log、ssh-app-socks.log：SSH 连接诊断。
- state/architecture-audit-20261108/home.png、architecture-preview.png：浏览器截图。
- 桌面 SearchFlow-部署架构核查-20261108.svg：地址、端口、服务角色、历史差异及待验证链路；未核验端口明确写待盘点，无凭据。

图不是现网已完成拓扑证明。生产修复尚未开始，因此本轮没有生产回滚操作或已完成部署的声明。
