# OpenSERP 适配说明

新任务只通过自托管 `karust/openserp` 的 `/google/search` 发现 URL。OpenSERP 运行固定镜像
`v0.8.12@sha256:9f5c5736fc7434862fa23dd0955e53d4003ffe42f151a5e1297085decf3100b7`，
使用官方主路径的浏览器模式；不调用 OpenSERP Cloud、mega、多引擎或 extract。

项目仍负责查询调度、分页、全局速率、代理选择、缓存、冷却和熔断。每次请求把选定的
HTTP/HTTPS 代理放入 `X-Proxy-URL`，把代理 key 的 SHA-256 放入
`X-Proxy-Session-ID`。OpenSERP 的缓存、重试和端点回退全部关闭，因此项目中的一次
attempt 对应一次 OpenSERP/Google 尝试。

请求固定使用 `region=US`，让中英文查询都走 `google.com`，避免中文语言参数自动切到
`google.cn`。只有结构完整、明确由 Google 返回且没有 fallback 的 v2 envelope 才能进入正文队列。
只接收公开 HTTP/HTTPS 的 organic 结果。HTTP 200 空页必须带有效引擎状态和 pagination；
无效 JSON、引擎不符和解析错误均不得缓存为空结果。

CAPTCHA、Google 403/429 和明确的代理网络错误会隔离对应出口；OpenSERP 服务不可用、
schema 错误或 parser failure 只冷却 OpenSERP provider，不污染代理健康度。审计保存请求 ID、
版本、内部尝试次数、缓存状态、网络字节数和响应体哈希，绝不保存代理凭据。

OpenSERP 浏览器模式不接受带认证的 SOCKS 代理，因此私有池只调度 HTTP 端点；同一主机的
HTTP/SOCKS 别名仍合并为一个出口。pipeline 的所有搜索线程共享同一轮换与本地冷却状态，
确保先覆盖可用出口再复用，跨进程继续由 PostgreSQL 串行化。OpenSERP 的代理健康记录使用
独立命名空间，旧 WML/轻量页面的历史成功不会掩盖标准 Google 的 429/CAPTCHA。没有兼容
出口时，实验按最早恢复时间进入搜索冷却，不再把未发出网络请求的调度循环当成持续尝试。

当前暂停的 `million-yield-v5-20260915` 保留原账本，不迁移 provider，也不会自动恢复。
新实验必须使用新目录，并把历史 URL/正文指纹作为基线；不继承旧任务 pending URL 或计数。

OpenSERP 使用 MIT License：<https://github.com/karust/openserp>。
