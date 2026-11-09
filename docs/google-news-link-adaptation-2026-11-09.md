# News 真链接全链路：开发与部署前验收

范围：Google News 包装地址解析、保存、去重、读取与导出。本文不把文章正文、作者、出版者 canonical 或数值 Trends 当作本轮完成项。

**当前阶段：本地开发与测试，暂停后续部署。** 用户最新要求先完成 News 全链路的全部开发测试，最后再统一部署。此前已上线的核心能力保持现状；没有因此回滚、再次发布或新增生产采集测试。已执行过的安装脚本不应直接重跑。

## 已实现的链路

1. RSS 保留原始 Google 包装链接、标题、摘要、媒体、日期及顺序。
2. 能离线解析的旧格式按 protobuf 声明长度解码；现代格式读取 Google article 页面参数，再调用 Google 的链接解析端点；Google HTTP 跳转也可直接取得目标地址。
3. 只请求固定 Google 端点，禁止自动跟随到出版社；目标 URL 做协议、主机、凭据和敏感参数检查。解析失败保留原新闻，不以媒体首页代替文章地址。
4. 入库前补 `link`、`publisher_link`、`is_resolved`、`resolution_method`、解析版本/时点/响应摘要，同时保留 `original_link`、`google_news_link`、`redirect_link`。不把 Google 返回的目标地址冒充已独立核验的出版者 canonical。
5. 在同一保存事务内登记已确认的包装链接与目标链接关系。历史包装记录、原始请求及出现次数保留；历史已见的包装链接补成实际地址，不再作为第二个新增 URL。并发记录按统一顺序加锁。
6. 标准读取与请求 JSONL/CSV、occurrence CSV 可携带这些字段，旧默认合同和旧固定上报快照不改写。

## 保护与边界

- 解析缓存分公开/私有 campaign 隔离，singleflight 获取锁后再次检查缓存，发布缓存后释放锁。
- 共享解析速率上限 2 RPS；每批最多 100 个链接、150 秒，单次网络等待最多 8 秒。超限明确 deferred，不表示全部解析完成。
- 成功缓存 7 天；普通失败缓存 5 分钟；403/429/验证码遵守 30 分钟保护。尚未解析完整的 RSS 缓存缩短至最多 5 分钟，便于后续有限任务重新补充。
- HTML 和 RPC 响应大小分别限制 2 MiB、128 KiB；反劫持 JSON/长度分帧、RPC 名称、文章 ID 与 URL 类型均有检查。
- RSS 接收时点与后续链接解析耗时分开保存，避免把两分钟的补链耗时冒充 RSS HTTP 接收时间。
- 最新本地补丁把未知 Google 路径明确标为未完成，不能因为没有识别出可解析 token 而默认为完整。这项增强尚未部署。
- 未开展全部历史 News 的批量回填；仅在新采集遇到已知包装链接时登记对应关系。旧请求保持原始事实，不会批量覆盖冻结数据。

## 本地验证

- `predeployment-functional-regression/pytest.log`：相关功能 **95 passed、11 subtests**。
- `predeployment-full-regression/pytest.log`：现有工作树全量 **1,087 passed、333 subtests、0 failed、0 skipped**。
- 冻结候选验证 **64 passed、4 subtests、1 deselected**，登记于 `predeployment-frozen-regression/`。其中读取/导出的完整组合由工作树及此前实机读取函数、导出校验覆盖，不把 worker 包中没有的 API 模块伪装成已测。
- 覆盖正常解析、离线旧格式、HTTP 跳转、RPC 分帧、错误 ID/URL、未知格式、大小上限、超时/停止、403/429、缓存竞争与隔离、存储失败释放锁、并发历史去重、失败保留 RSS、读取/导出一致性。

中途一次全量复跑出现两项本机 HTTP 联调超时：Ads 快照分页及 CSV 导出；1069 项通过、2 项超时。两项独立重跑通过，之后上述最新全量 1087 项全部通过。没有为通过测试放宽产品规则或修改生产数据库配置。

## 此前已完成的真实验证（发生在部署暂停指令之前）

- Google 公共端点三个样本均返回实际文章地址；未请求出版社正文。
- 一个有边界的 News 单次任务：1 次 RSS 保存请求，55 条新闻 **55/55 解析成功**；110 次 Google 解析 HTTP 调用均成功。RSS 读取约 **1.226 秒**，补链约 **131.072 秒**。
- 历史去重后仅 **1 个新增 URL**，没有把 55 个补齐字段都算新增新闻。
- 重复任务复用既有快照，解析调用总数仍 110，没有重复发起补链请求。两个任务的最后已取得状态均为 completed，不是无限持续任务。
- 生产已部署的标准读取函数返回 55 个 publisher_article_url；其记录与生产 JSONL 逐字段相同。生产导出校验 1 请求、55 occurrence、0 未解析包装链接。
- SSH 后续断开，直接文件复制和最终状态快照没有完成。本地从已捕获 SQL 记录重建三文件，其字节 SHA-256 均与实机已验证清单相同；重建性质见 `delivered/reconstruction-proof.json`，不冒称 SSH 复制成功。
- 本轮不把实机读取函数验证说成额外的公网 HTTP 鉴权或发布后网页点击验收；浏览器空间 35 随后已不存在，未另建替代空间。

示例目标地址：

- Battery Technology：`https://www.batterytechonline.com/materials/sodium-ion-batteries-gain-ground-in-australia-s-energy-storage-market`
- IDTechEx：`https://www.idtechex.com/en/research-article/sodium-ion-batteries-from-technical-promise-to-commercial-reality/35483`
- Bioengineer.org：`https://bioengineer.org/solvothermal-method-enhances-manganese-prussian-white-performance-for-sodium-batteries/`

这些是 Google 返回的媒体文章目标地址；没有据此宣称出版社可访问性、正文真实性或 canonical 已独立验证。

## 候选与最终部署顺序

证据和候选目录：`state/news-link-adaptation-20261109/`；`predeployment-manifest.json` 明确 `deployment_hold=true`。

候选以实际冻结 worker 为基线，仅合并 `google_news_links.py`、`google_feeds.py`、`serp_store.py`、`serp_worker.py`。工作树中原有的私有 RSS 原始字节捕获等其他变更未夹带。最终部署前需重新读取当前生产哈希、确认无活动任务、重新备份并生成最小补丁；不直接覆盖整个工作树，不运行历史正文/hash 迁移，不自行开启持续采集、正文或上传。

后续统一部署与部署后的必要小样本复验属于最后阶段，当前不执行。

最终四文件候选包：`state/news-link-adaptation-20261109/predeployment-news-candidate.tar`；SHA-256 与各文件哈希记录于 `predeployment-manifest.json`。本地隔离 PostgreSQL 已停止并清理临时数据目录，测试日志保留。候选包仅用于最终部署前复核，当前未执行部署。
