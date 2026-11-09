# SearchFlow 前置访问验证 · 2026-10-27

已按最新要求，将原本公开的网站改为**整个站点先输入访问 Key**。此要求覆盖此前“无需登录保护”的历史部署说明。

访问地址：<https://searchflow.20-150-145-162.sslip.io/>

- 未认证浏览器跳转 `/_access/login`；未认证数据/API 请求返回 401，不返回工作台内容或数据。登录页本身公开。
- 使用新生成的 256 位随机访问 Key，与现有管理密钥、客户 API 密钥分开。完整 Key 只保存在操作人本机 `~/.config/searchflow/site-access-key` 与 `~/.config/searchflow/网站访问说明.md`，权限 600；代码、日志和本报告不包含明文。
- 登录 Cookie 有效期 12 小时，使用 HMAC 签名，设置 `Secure / HttpOnly / SameSite=Strict / Path=/`，名称使用 `__Host-` 前缀。过期后重新验证；左侧“退出访问”清除当前浏览器 Cookie。
- 同一 IP 五分钟内连续 10 次错误输入后限制重试；成功登录清除该 IP 的失败记录。服务器只保存高熵访问 Key 的 SHA-256 摘要及独立 Cookie 签名秘密，配置文件权限 600。
- 登录提交校验 Origin；不支持将 Key 放入 URL 参数。受保护内容和登录响应均禁止缓存。

**API 接入变化**

所有对外请求需要额外携带 `X-SearchFlow-Access-Key`。原客户鉴权仍使用 `Authorization: Bearer ...`；前置 Key 不会代替客户身份和管理权限。示例只引用环境变量，不包含真实凭据：

```bash
curl 'https://searchflow.20-150-145-162.sslip.io/v1/usage' \
  -H "X-SearchFlow-Access-Key: $SEARCHFLOW_SITE_ACCESS_KEY" \
  -H "Authorization: Bearer $SEARCHFLOW_API_KEY"
```

原先只带客户 API 密钥的外部调用现在会被前置层拒绝，需要补充上述请求头。浏览器登录后由 Cookie 自动验证，无需每页再输入。

**部署范围**

- 新增独立 `realtime/access_gate.py`，仅依赖 Python 标准库，部署为 `google-workbench-access-1`，绑定 `127.0.0.1:18095`。
- 工作台专属 gateway 使用 Nginx `auth_request` 对全部应用路径进行前置验证；验证服务不可用时拒绝访问，不降级为公开。模板在 `deploy/access-gateway.nginx.conf`。
- 新增服务使用现有 Python 镜像、非 root 用户、只读文件系统、去除 capabilities、限制内存/PID，并自动重启；配置与签名秘密持久保存。
- Compose 增加 access 服务；专属 gateway 仅 reload；Web 因增加“退出访问”入口重启。共享 HTTPS Nginx、采集 worker、数据库及其他站点未修改。
- `web.py`、`serp_page.py` 未修改或覆盖；没有新增采集请求，没有恢复持续采集、正文、平台上传、领取或监测。
- 备份目录：服务器 `state/site-access-20261027`。不要直接恢复旧的无验证 gateway 对外开放；回退时也应保留访问限制。

**本轮验证结果**

| 验证 | 结果 |
|---|---|
| 新增鉴权单测 | 4 项通过：错误/缺失 Key、签名篡改/过期/轮换、分 IP 限制、真实 HTTP 登录/Cookie/Origin/退出 |
| 线上 HTTP 验证 | 32 项通过：公开路径/API/导出/历史页面拦截、正确 Key、内部鉴权地址不可访问、Bearer 与前置 Key 同时工作、管理权限保留、HTTP 跳转 |
| 真实浏览器 | 5 项通过：错误 Key、正确登录、刷新、退出再次拦截、320/390 手机布局 |
| 内部端口旁路 | 公网地址 18093 / 18094 / 18095 均不可直接访问 |
| 结束状态 | 持续采集、正文、Whale 上传与领取仍关闭；访问服务、Web、gateway 正常 |

本轮没有重新宣称上一轮 452 项全量测试已再次执行；此次只运行上述与前置验证相关的新测试及线上验证。没有启动本机 Docker。本地一次性浏览器凭据传递服务已退出。

持有这个共享访问 Key 的人都可通过第一道验证；原有客户隔离和管理员权限仍由各自密钥控制。轮换时应同时更新访问 Key 摘要与 Cookie 签名秘密，并重启 access 服务，使旧 Key 和旧 Cookie 一起失效。

证据：

- [线上 32 项验证](../output/playwright/access-gate-20261027/verification.json)
- [浏览器验证](../output/playwright/access-gate-20261027/browser.json)
- [公网端口检查](../output/playwright/access-gate-20261027/ports.json)
- [登录页桌面截图](../output/playwright/access-gate-20261027/login-desktop.png) · [手机截图](../output/playwright/access-gate-20261027/login-mobile.png)
